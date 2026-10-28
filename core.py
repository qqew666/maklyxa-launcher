#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MaklyxaLauncher — ядро лаунчера (логика без интерфейса).

Что здесь:
  * конфиг приложения (+ миграция со старого имени RobloxLauncher);
  * три темы оформления (тёмная, светлая, стекло);
  * поиск и статус Roblox, список запущенных процессов;
  * FastFlags — запись слиянием в ClientAppSettings.json;
  * настройки Froststrap/Bloxstrap;
  * данные игры (название, автор, онлайн, иконка) из публичного API;
  * галерея скриншотов Roblox: сессии, группировка по плейсам, кэш;
  * одноэкземплярность (mutex) и хелперы безрамочного окна.

Лаунчер не внедряет код в игру и не обходит защиту — только стандартные
ссылки roblox://, запись конфигов, запуск официальных файлов и локальная
замена PNG-курсоров клиента.
"""

import base64
import ctypes
import hashlib
import json
import os
import random
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    import winreg
except ImportError:  # не Windows
    winreg = None

APP_NAME = "MaklyxaLauncher"
WINDOW_TITLE = APP_NAME
VERSION = "1.9.6"

INSTALLER_URL = "https://www.roblox.com/download/client"
DISCOVER_URL = "https://www.roblox.com/discover"
CREATE_NO_WINDOW = 0x08000000

GITHUB_REPO = "qqew666/maklyxa-launcher"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
RELEASES_API = (f"https://api.github.com/repos/{GITHUB_REPO}"
                "/releases?per_page=15")

CONFIG_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"
OLD_CONFIG_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / "RobloxLauncher"
OLD_CONFIG_PATH = OLD_CONFIG_DIR / "config.json"

CURSOR_SETS_DIR = CONFIG_DIR / "CursorSets"
CURSOR_STATE_PATH = CONFIG_DIR / "cursor_state.json"
CURSOR_BACKUP_DIR = CONFIG_DIR / "cursor_backup"

THEME_SETS_DIR = CONFIG_DIR / "ThemeSets"
ACCOUNTS_PATH = CONFIG_DIR / "accounts.json"
AVATARS_DIR = CONFIG_DIR / "avatars"
BROWSER_PROFILE_DIR = CONFIG_DIR / "browser_profile"
LAUNCH_DIR = CONFIG_DIR / "Launch"
LAUNCH_ICON_PATH = LAUNCH_DIR / "icon.ico"
LAUNCH_ICON_PREVIEW = LAUNCH_DIR / "icon_preview.png"
LAUNCH_SHORTCUT_BACKUP_DIR = LAUNCH_DIR / "shortcuts_backup"
LAUNCH_SHORTCUT_BACKUP_PATH = LAUNCH_DIR / "shortcuts.json"
LAUNCH_SOUND_MAX = 12 * 1024 * 1024
CUSTOMIZE_DIR = CONFIG_DIR / "Customize"
UPDATE_DIR = CONFIG_DIR / "Updates"
UI_FONT_DIR = CUSTOMIZE_DIR / "Fonts"
UI_FONT_MAX = 20 * 1024 * 1024
UI_FONT_EXTS = (".ttf", ".otf")
SCREENSHOTS_DIR = Path.home() / "Pictures" / "Roblox"
GALLERY_DIR = CONFIG_DIR / "Gallery"
GALLERY_CACHE_PATH = GALLERY_DIR / "cache.json"
GALLERY_THUMBS_DIR = GALLERY_DIR / "Thumbnails"
SCREENSHOT_EXTS = (".png", ".jpg", ".jpeg", ".webp")
CURSOR_SLOTS = (
    {"key": "shiftlock", "file": "MouseLockedCursor.png",
     "label": "ShiftLock",
     "set_rel": Path("MouseLockedCursor.png"),
     "target_rel": Path("content") / "textures" / "MouseLockedCursor.png"},
    {"key": "arrow", "file": "ArrowCursor.png",
     "label": "Обычная стрелка",
     "set_rel": Path("Cursors") / "KeyboardMouse" / "ArrowCursor.png",
     "target_rel": (Path("content") / "textures" / "Cursors"
                    / "KeyboardMouse" / "ArrowCursor.png")},
    {"key": "arrow_far", "file": "ArrowFarCursor.png",
     "label": "Стрелка в игре",
     "set_rel": Path("Cursors") / "KeyboardMouse" / "ArrowFarCursor.png",
     "target_rel": (Path("content") / "textures" / "Cursors"
                    / "KeyboardMouse" / "ArrowFarCursor.png")},
    {"key": "ibeam", "file": "IBeamCursor.png",
     "label": "Ввод текста",
     "set_rel": Path("Cursors") / "KeyboardMouse" / "IBeamCursor.png",
     "target_rel": (Path("content") / "textures" / "Cursors"
                    / "KeyboardMouse" / "IBeamCursor.png")},
)
CURSOR_SLOT_BY_KEY = {slot["key"]: slot for slot in CURSOR_SLOTS}
CURSOR_SLOT_BY_FILE = {slot["file"].lower(): slot for slot in CURSOR_SLOTS}

if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", "."))
else:
    BUNDLE_DIR = Path(__file__).resolve().parent

# ---- темы ------------------------------------------------------------------
# bg        — фон страницы
# panel     — сайдбар и шапка окна
# surface   — фон карточек
# surface2  — вторичные кнопки, поля
# hover     — подсветка при наведении
# entry     — фон поля ввода
# list      — фон списков и журнала
# border    — контур карточек и полей
# text/muted— основной и приглушённый текст
# on_accent — текст на акцентной заливке
# alpha     — прозрачность окна (стекло)
THEMES = {
    "dark": {
        "label": "Тёмная",
        "bg": "#0f1218",
        "panel": "#151a22",
        "surface": "#1a202a",
        "surface2": "#222a35",
        "hover": "#2a3441",
        "entry": "#1d242e",
        "list": "#12171e",
        "border": "#2a323d",
        "text": "#edf1f7",
        "muted": "#8d98a9",
        "on_accent": "#071510",
        "titlebar": "#10151c",
        "alpha": 1.0,
    },
    "light": {
        "label": "Светлая",
        "bg": "#f4f6fa",
        "panel": "#ffffff",
        "surface": "#ffffff",
        "surface2": "#eef2f8",
        "hover": "#e3e9f2",
        "entry": "#f7f9fc",
        "list": "#f0f3f8",
        "border": "#d9e0ea",
        "text": "#17202b",
        "muted": "#657184",
        "on_accent": "#071510",
        "titlebar": "#ffffff",
        "alpha": 1.0,
    },
    "glass": {
        "label": "Стекло",
        "bg": "#0b1118",
        "panel": "#0f1722",
        "surface": "#152131",
        "surface2": "#1b2a3e",
        "hover": "#243750",
        "entry": "#12202f",
        "list": "#0e1925",
        "border": "#2b4059",
        "text": "#e9f1fb",
        "muted": "#8ea3ba",
        "on_accent": "#071510",
        "titlebar": "#0a1018",
        "alpha": 0.92,
    },
}
THEME_ORDER = ["dark", "light", "glass"]
DEFAULT_THEME = "dark"

ACCENT_PRESETS = [
    ("#00c48c", "Зелёный"),
    ("#3b9eff", "Синий"),
    ("#a06bff", "Фиолетовый"),
    ("#ff9f43", "Оранжевый"),
    ("#ff5f9e", "Розовый"),
]

SPLASH_STYLES = ("off", "minimal", "card")

DEFAULT_CONFIG = {
    "auto_close": False,
    "save_history": True,
    "remember_last": True,
    "default_place_id": "",
    "last_place_id": "",
    "roblox_version": "",
    "favorites": [],
    "history": [],
    "fps_limit": 0,
    "graphics_mode": "",
    "quality_level": 0,
    "apply_flags_on_launch": True,
    "use_custom_cursors": True,
    "cursor_set": "",
    "accent": "#00c48c",
    "theme": DEFAULT_THEME,
    "custom_theme": "",
    "window_geometry": "",
    "window_buttons": "mac",
    "discord_rpc": True,
    "discord_app_id": "",
    "launch_sound": True,
    "launch_sound_name": "",
    "launch_splash": "card",
    "error_sound": True,
    "error_sound_name": "",
    "ui_font_name": "",
    "ui_font_family": "",
    "ingame_overlay": True,
    "ingame_island": False,
    "ingame_overlay_start": False,
    "ingame_overlay_corner": "tr",
    "ingame_hotkey": "shift+f1",
    "ingame_clock": False,
    "ingame_clock_hotkey": "shift+f3",
    "ingame_auto": True,
    "ingame_afk": False,
    "ingame_afk_mode": "mouse",
    "ingame_afk_key": "space",
    "ingame_afk_minutes": 5,
}

# ---- FastFlags и бутстраперы -----------------------------------------------
FPS_CHOICES = ["Как в игре", "60", "120", "144", "240", "360"]
GRAPHICS_MODES = ["Direct3D 11", "Direct3D 12", "Vulkan", "OpenGL"]
FLAG_FPS = "DFIntTaskSchedulerTargetFps"
FLAG_QUALITY = "DFIntDebugFRMQualityLevelOverride"
FLAG_API = {
    "Direct3D 11": "FFlagDebugGraphicsPreferD3D11",
    "Direct3D 12": "FFlagDebugGraphicsPreferD3D12",
    "Vulkan": "FFlagDebugGraphicsPreferVulkan",
    "OpenGL": "FFlagDebugGraphicsPreferOpenGL",
}
MANAGED_FLAGS = {FLAG_FPS, FLAG_QUALITY} | set(FLAG_API.values())

BOOT_BOOLS_SHARED = [
    ("UseDiscordRichPresence", "Discord Rich Presence"),
    ("HideRPCButtons", "Скрыть кнопки в Discord-статусе"),
    ("ShowAccountOnRichPresence", "Показывать аккаунт в Discord-статусе"),
    ("ShowServerDetails", "Показывать детали сервера"),
    ("ConfirmLaunches", "Подтверждать запуск игры"),
    ("EnableActivityTracking", "Отслеживать активность"),
]
BOOT_BOOLS_FROST = [
    ("EnableBetterMatchmaking", "Улучшенный матчмейкинг"),
    ("ShowServerUptime", "Показывать аптайм сервера"),
    ("PlaytimeCounter", "Счётчик времени в игре"),
    ("AutoChangeTitle", "Менять заголовок окна Roblox"),
    ("AutoChangeTitleWithPlayerCount", "…с числом игроков в заголовке"),
    ("AutoChangeIcon", "Менять иконку окна Roblox"),
    ("ShowGameHistoryMenu", "Меню истории игр"),
]


# ---- вспомогательные функции -------------------------------------------------


def shade(hex_color, factor):
    """Осветлить/затемнить цвет (hover/pressed состояния)."""
    hex_color = str(hex_color).lstrip("#")
    if len(hex_color) != 6:
        return hex_color
    try:
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return hex_color
    clamp = lambda v: max(0, min(255, int(v * factor)))  # noqa: E731
    return f"#{clamp(r):02x}{clamp(g):02x}{clamp(b):02x}"


def fmt_num(value):
    return f"{int(value):,}".replace(",", " ")


def trunc(text, limit=46):
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def mb(byte_count):
    return f"{byte_count / 1048576:.1f}"


def valid_geometry(text):
    """Строка вида 900x650+100+100 (Tk пишет отрицательные сдвиги как +-32000)."""
    return bool(re.fullmatch(
        r"\d{3,5}x\d{3,5}[+-]-?\d{1,7}[+-]-?\d{1,7}",
        str(text or "")))


# ---- конфигурация -----------------------------------------------------------


def migrate_old_config(new_path=None, old_path=None):
    """Переносим config.json из старой папки RobloxLauncher (один раз)."""
    new_path = Path(new_path or CONFIG_PATH)
    old_path = Path(old_path or OLD_CONFIG_PATH)
    try:
        if new_path.exists() or not old_path.exists():
            return False
        data = json.loads(old_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        return True
    except (OSError, ValueError):
        return False


def load_config(path=None):
    cfg = dict(DEFAULT_CONFIG)
    config_path = Path(path or CONFIG_PATH)
    if path is None:
        migrate_old_config()
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in DEFAULT_CONFIG:
                    if key in data:
                        cfg[key] = data[key]
    except (OSError, ValueError):
        pass

    for key in ("default_place_id", "last_place_id"):
        value = str(cfg.get(key) or "")
        cfg[key] = value if value.isdigit() else ""
    cfg["roblox_version"] = str(cfg.get("roblox_version") or "").strip()[:260]
    cfg["cursor_set"] = str(cfg.get("cursor_set") or "")

    for key in ("favorites", "history"):
        clean = []
        for item in cfg.get(key) or []:
            if isinstance(item, dict):
                pid = str(item.get("place_id") or "")
                if pid.isdigit():
                    clean.append({"place_id": pid,
                                  "name": str(item.get("name") or ""),
                                  "ts": int(item.get("ts") or 0)})
        cfg[key] = clean[:40]

    for flag in ("auto_close", "save_history", "remember_last",
                 "apply_flags_on_launch", "use_custom_cursors",
                 "discord_rpc", "launch_sound", "error_sound",
                 "ingame_overlay", "ingame_island",
                 "ingame_overlay_start", "ingame_clock",
                 "ingame_auto", "ingame_afk"):
        cfg[flag] = bool(cfg.get(flag))
    corner = str(cfg.get("ingame_overlay_corner") or "").lower()
    cfg["ingame_overlay_corner"] = (
        corner if corner in ("tl", "tr", "bl", "br") else "tr")
    hotkey = re.sub(r"\s+", "", str(cfg.get("ingame_hotkey") or "").lower())
    cfg["ingame_hotkey"] = (
        hotkey if re.fullmatch(r"[a-z0-9+]{3,48}", hotkey) else "shift+f1")
    clock_hotkey = re.sub(
        r"\s+", "", str(cfg.get("ingame_clock_hotkey") or "").lower())
    cfg["ingame_clock_hotkey"] = (
        clock_hotkey if re.fullmatch(r"[a-z0-9+]{3,48}", clock_hotkey)
        else "shift+f3")
    if cfg["ingame_clock_hotkey"] == cfg["ingame_hotkey"]:
        cfg["ingame_clock_hotkey"] = "shift+f3"
    afk_mode = str(cfg.get("ingame_afk_mode") or "").lower()
    cfg["ingame_afk_mode"] = (
        afk_mode if afk_mode in ("mouse", "key") else "mouse")
    cfg["ingame_afk_key"] = (
        re.sub(r"[^a-z0-9_]", "",
               str(cfg.get("ingame_afk_key") or "").lower())[:16] or "space")
    try:
        cfg["ingame_afk_minutes"] = max(
            1, min(15, int(cfg.get("ingame_afk_minutes") or 5)))
    except (TypeError, ValueError):
        cfg["ingame_afk_minutes"] = 5
    cfg["discord_app_id"] = valid_discord_app_id(cfg.get("discord_app_id"))
    cfg["launch_sound_name"] = str(cfg.get("launch_sound_name") or "").strip()[:80]
    cfg["error_sound_name"] = str(cfg.get("error_sound_name") or "").strip()[:80]
    cfg["ui_font_name"] = str(cfg.get("ui_font_name") or "").strip()[:80]
    cfg["ui_font_family"] = str(cfg.get("ui_font_family") or "").strip()[:80]
    splash = str(cfg.get("launch_splash") or "").lower()
    cfg["launch_splash"] = splash if splash in SPLASH_STYLES else "card"
    try:
        cfg["fps_limit"] = max(0, min(1000, int(cfg.get("fps_limit") or 0)))
    except (TypeError, ValueError):
        cfg["fps_limit"] = 0
    try:
        cfg["quality_level"] = max(
            0, min(10, int(cfg.get("quality_level") or 0)))
    except (TypeError, ValueError):
        cfg["quality_level"] = 0
    mode = str(cfg.get("graphics_mode") or "")
    cfg["graphics_mode"] = mode if mode in FLAG_API else ""
    accent = str(cfg.get("accent") or "")
    cfg["accent"] = (accent.lower()
                     if re.fullmatch(r"#[0-9a-fA-F]{6}", accent)
                     else DEFAULT_CONFIG["accent"])
    cfg["custom_theme"] = str(cfg.get("custom_theme") or "").strip()[:48]
    theme = str(cfg.get("theme") or "").lower()
    if theme == "custom" and cfg["custom_theme"]:
        cfg["theme"] = "custom"
    else:
        cfg["theme"] = theme if theme in THEMES else DEFAULT_THEME
    cfg["window_geometry"] = (str(cfg.get("window_geometry") or "")
                              if valid_geometry(cfg.get("window_geometry"))
                              else "")
    buttons = str(cfg.get("window_buttons") or "").lower()
    cfg["window_buttons"] = buttons if buttons in ("mac", "win") else "mac"
    return cfg


def save_config(cfg, path=None):
    config_path = Path(path or CONFIG_PATH)
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, config_path)
        return True
    except OSError:
        return False


# ---- поиск Place ID ----------------------------------------------------------


def parse_place_id(text):
    """Достаёт Place ID из любых ссылок Roblox или из чистого числа."""
    if not text:
        return None
    text = str(text).strip().strip('"').strip("'")
    m = re.search(r"placeid=(\d+)", text, re.I)
    if m:
        return m.group(1)
    m = re.search(r"roblox\.com/games/(\d+)", text, re.I)
    if m:
        return m.group(1)
    if re.fullmatch(r"\d{4,20}", text):
        return text
    return None


# ---- обнаружение Roblox -------------------------------------------------------


def probe_protocol():
    """(зарегистрирован ли roblox://, полный путь к обработчику)."""
    if winreg is None:
        return False, ""
    for hive, sub in (
        (winreg.HKEY_CURRENT_USER,
         r"Software\Classes\roblox\shell\open\command"),
        (winreg.HKEY_CLASSES_ROOT, r"roblox\shell\open\command"),
    ):
        try:
            with winreg.OpenKey(hive, sub) as key:
                cmd = str(winreg.QueryValueEx(key, "")[0])
        except OSError:
            continue
        m = re.search(r'"([^"]+)"', cmd) or re.search(r"(\S+?\.exe)", cmd, re.I)
        return True, (m.group(1) if m else "")
    return False, ""


def find_player_exe():
    """Самый свежий RobloxPlayerBeta.exe (официальный или бутстраперный)."""
    local = os.getenv("LOCALAPPDATA", "")
    roots = []
    if local:
        for name in ("Roblox", "Froststrap", "Bloxstrap"):
            roots.append(Path(local) / name / "Versions")
    roots.append(Path(r"C:\Program Files\Froststrap"))
    roots.append(Path(r"C:\Program Files (x86)\Froststrap"))

    best, best_mtime = None, -1.0
    for root in roots:
        try:
            if not root.is_dir():
                continue
            for exe in root.glob("**/RobloxPlayerBeta.exe"):
                mtime = exe.stat().st_mtime
                if mtime > best_mtime:
                    best, best_mtime = exe, mtime
        except OSError:
            continue
    return best


def roblox_status():
    """('installed'|'bootstrapper'|'broken'|'missing', деталь)."""
    exe = find_player_exe()
    if exe:
        return "installed", str(exe)
    registered, handler = probe_protocol()
    if registered and handler and Path(handler).exists():
        return "bootstrapper", handler
    if registered:
        return "broken", handler
    return "missing", ""


def player_pids():
    """PID всех процессов RobloxPlayerBeta."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq RobloxPlayerBeta.exe",
             "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
            creationflags=CREATE_NO_WINDOW, errors="replace")
        pids = set()
        for line in out.stdout.splitlines():
            if "RobloxPlayerBeta" in line:
                parts = line.split(",")
                if len(parts) > 1 and parts[1].strip('"').isdigit():
                    pids.add(int(parts[1].strip('"')))
        return pids
    except Exception:
        return set()


def wait_players_gone(seconds=12.0, poll=0.4):
    """True — процессы Roblox завершились до истечения срока."""
    deadline = time.time() + max(0.0, seconds)
    while True:
        if not player_pids():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(poll)


def processes_named(*names):
    """Множество имён запущенных процессов (по tasklist)."""
    wanted = {name.lower() for name in names}
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
            text=True, timeout=10, creationflags=CREATE_NO_WINDOW,
            errors="replace")
        found = set()
        for line in out.stdout.splitlines():
            if not line:
                continue
            first = line.split(",")[0].strip().strip('"').lower()
            if first in wanted:
                found.add(first)
        return found
    except Exception:
        return set()


def bootstrapper_running():
    return bool(processes_named("Froststrap.exe", "Bloxstrap.exe"))


def _enum_windows(callback):
    """Перечисление окон; callback(hwnd) -> bool (False — остановить)."""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                   wintypes.LPARAM)
    user32.EnumWindows(enum_proc(callback), 0)


def focus_roblox_window(pids):
    """Выводит окно Roblox на передний план. True — если окно найдено."""
    if not pids:
        return False
    user32 = ctypes.windll.user32
    found = []

    def _cb(hwnd, _lparam):
        from ctypes import wintypes
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value:
                found.append(hwnd)
        return True

    _enum_windows(_cb)
    if not found:
        return False
    hwnd = found[0]
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


def focus_window_by_title(title):
    """Найти окно по заголовку и вывести вперёд."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, str(title))
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, 9)      # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def roblox_window_pids(pids=None):
    """PID клиента Roblox с видимым окном.

    Новый клиент после закрытия окна остаётся жить в трее без окон —
    такие процессы сюда не попадают, их нужно перезапускать.
    """
    try:
        wanted = set(pids) if pids is not None else player_pids()
    except TypeError:
        wanted = set()
    if not wanted:
        return set()
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        found = set()

        def _cb(hwnd, _lparam):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if (pid.value in wanted and user32.IsWindowVisible(hwnd)
                    and user32.GetWindowTextLengthW(hwnd) > 0):
                found.add(pid.value)
            return True

        _enum_windows(_cb)
        return found
    except Exception:
        return set()


def wait_for_roblox_window(seconds=8, poll=0.5):
    """True — у клиента Roblox появилось видимое окно."""
    deadline = time.time() + max(0.0, seconds)
    while True:
        if roblox_window_pids():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(poll)


# ---- одноэкземплярность --------------------------------------------------------

_MUTEX_NAME = r"Local\MaklyxaLauncher_SingleInstance"
ERROR_ALREADY_EXISTS = 183


def acquire_single_instance():
    """Mutex от второго запуска.

    Возвращает хэндл (первый экземпляр), False (уже запущен — окно
    выведено вперёд, вызывающий должен выйти) или объект-заглушку,
    если API недоступно.
    """
    if sys.platform != "win32":
        return object()
    try:
        k32 = ctypes.windll.kernel32
        k32.SetLastError(0)
        handle = k32.CreateMutexW(None, False, _MUTEX_NAME)
        if k32.GetLastError() == ERROR_ALREADY_EXISTS:
            if handle:
                k32.CloseHandle(handle)
            focus_window_by_title(WINDOW_TITLE)
            return False
        return handle
    except Exception:
        return object()


# ---- уборка брошенных распаковок ----------------------------------------------

BUNDLE_STATE = CONFIG_DIR / "bundles.json"


def remember_bundle_dir():
    """Запомнить свою папку _MEI на случай, если её не удастся убрать."""
    if not getattr(sys, "frozen", False):
        return
    try:
        path = str(Path(sys._MEIPASS).resolve())
    except (AttributeError, OSError):
        return
    try:
        data = json.loads(BUNDLE_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = []
    if not isinstance(data, list):
        data = []
    if path not in data:
        data.append(path)
    try:
        BUNDLE_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = BUNDLE_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data[-20:]), encoding="utf-8")
        os.replace(tmp, BUNDLE_STATE)
    except OSError:
        pass


def _is_our_bundle(path):
    # после неудачной уборки в папке остаются только Tk-файлы и иконка
    if not (path / "_tkinter.pyd").is_file():
        return False
    return ((path / "icon.png").is_file()
            or (path / "tcl9tk90.dll").is_file())


def _drop_bundle(path):
    dll = next(path.glob("python3*.dll"), None)
    if dll is None:
        return False
    try:
        dll.unlink()
    except OSError:
        return False
    shutil.rmtree(path, ignore_errors=True)
    return not path.exists()


def cleanup_stale_bundle_dirs(min_age=600):
    """Удалить свои брошенные папки _MEI от PyInstaller onefile.

    Своя распаковка узнаётся по записи в bundles.json или по Tk-файлам
    и иконке лаунчера. Занятая папка не трогается: python3*.dll удаляется
    первым, и отказ в доступе отменяет уборку всей папки.
    """
    if not getattr(sys, "frozen", False):
        return 0
    current = Path(getattr(sys, "_MEIPASS", "")).resolve()
    now = time.time()
    try:
        recorded = json.loads(BUNDLE_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        recorded = []
    if not isinstance(recorded, list):
        recorded = []
    kept = []
    removed = 0
    for item in recorded:
        try:
            path = Path(item)
            if path.resolve() == current:
                kept.append(item)
                continue
            if not path.is_dir():
                continue
            if now - path.stat().st_mtime < min_age:
                kept.append(item)
                continue
            if _drop_bundle(path):
                removed += 1
            else:
                kept.append(item)
        except OSError:
            continue
    try:
        candidates = list(Path(tempfile.gettempdir()).glob("_MEI*"))
    except OSError:
        candidates = []
    for path in candidates:
        try:
            if not path.is_dir() or path.resolve() == current:
                continue
            if now - path.stat().st_mtime < min_age:
                continue
            if not _is_our_bundle(path):
                continue
            if _drop_bundle(path):
                removed += 1
        except OSError:
            continue
    try:
        tmp = BUNDLE_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(kept[-20:]), encoding="utf-8")
        os.replace(tmp, BUNDLE_STATE)
    except OSError:
        pass
    return removed


# ---- данные игры (публичный API) ------------------------------------------------

HTTP_HEADERS = {"User-Agent": f"Mozilla/5.0 ({APP_NAME}/{VERSION})"}


def _get_json(url, timeout=8):
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def version_tuple(text):
    """'v1.7.0' → (1, 7, 0); None, если цифр в строке нет."""
    nums = re.findall(r"\d+", str(text or ""))
    return tuple(int(n) for n in nums[:4]) or None


def check_launcher_update(timeout=8):
    """Последний релиз на GitHub и признак, что он новее этой сборки."""
    try:
        releases = _get_json(RELEASES_API, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — сеть, лимит API, битый ответ
        return {"error": str(exc) or type(exc).__name__}
    if not isinstance(releases, list):
        return {"error": "GitHub вернул неожиданный ответ"}
    release = next(
        (r for r in releases
         if isinstance(r, dict) and not r.get("draft")), None)
    if release is None:
        return {"error": "На GitHub нет опубликованных релизов"}
    tag = str(release.get("tag_name") or "")
    latest = version_tuple(tag)
    current = version_tuple(VERSION) or ()
    page = str(release.get("html_url") or RELEASES_URL)
    assets = {}
    for item in release.get("assets") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").lower()
        info = release_asset(item)
        if info is None:
            continue
        if name.endswith(".exe") and "exe" not in assets:
            assets["exe"] = info
        elif name.endswith(".msi") and "msi" not in assets:
            assets["msi"] = info
    return {
        "current": VERSION,
        "tag": tag,
        "latest": ".".join(str(n) for n in latest) if latest else tag,
        "name": str(release.get("name") or tag),
        "url": page,
        "download": (assets.get("exe") or assets.get("msi") or
                     {}).get("url") or page,
        "assets": assets,
        "prerelease": bool(release.get("prerelease")),
        "is_newer": bool(latest and latest > current),
    }


def release_asset(item):
    """Файл релиза: имя, ссылка, размер и sha256 (если GitHub отдал digest)."""
    url = str(item.get("browser_download_url") or "")
    if not url:
        return None
    digest = str(item.get("digest") or "")
    if digest.lower().startswith("sha256:"):
        digest = digest.split(":", 1)[1]
    digest = digest.lower() if re.fullmatch(r"[0-9a-fA-F]{64}", digest) else ""
    try:
        size = max(0, int(item.get("size") or 0))
    except (TypeError, ValueError):
        size = 0
    return {
        "name": str(item.get("name") or ""),
        "url": url,
        "size": size,
        "sha256": digest,
    }


def sha256_file(path, chunk=1048576):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def update_file_name(asset):
    """Безопасное имя файла обновления из имени ассета."""
    name = Path(str((asset or {}).get("name") or "")).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name or "MaklyxaLauncher-update.exe"


def update_file_valid(path, asset):
    """Файл уже скачан и совпадает с ассетом по размеру/хешу."""
    path = Path(path)
    asset = asset or {}
    try:
        size = int(asset.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    sha = str(asset.get("sha256") or "")
    if not size and not sha:
        return False
    try:
        if not path.is_file():
            return False
        if size and path.stat().st_size != size:
            return False
        if sha and sha256_file(path) != sha:
            return False
    except OSError:
        return False
    return True


class UpdateCancelled(Exception):
    """Скачивание обновления прервано пользователем."""


def _unlink_quiet(path):
    try:
        Path(path).unlink()
    except OSError:
        pass


def _download_to_part(url, part, size, progress, cancel, timeout, chunk):
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        try:
            total = int(resp.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            total = 0
        total = total or int(size or 0)
        done = 0
        with open(part, "wb") as fh:
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateCancelled()
                block = resp.read(chunk)
                if not block:
                    break
                fh.write(block)
                done += len(block)
                if progress is not None:
                    progress(done, total)
    if total and done != total:
        raise OSError(f"скачано {done} из {total} байт")


def download_update(url, target, size=0, sha256="", progress=None,
                    cancel=None, timeout=60, chunk=131072, attempts=2):
    """Скачивает файл обновления во временный .part и проверяет его."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    last = None
    for attempt in range(max(1, attempts)):
        if cancel is not None and cancel.is_set():
            raise UpdateCancelled()
        try:
            _download_to_part(url, part, size, progress, cancel, timeout, chunk)
            if sha256 and sha256_file(part) != str(sha256).lower():
                raise ValueError("контрольная сумма файла не совпала")
            os.replace(part, target)
            return True
        except UpdateCancelled:
            _unlink_quiet(part)
            raise
        except ValueError:
            _unlink_quiet(part)
            raise
        except OSError as exc:
            _unlink_quiet(part)
            last = exc
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.8)
    raise last or OSError("не удалось скачать файл")


def fetch_game_info(place_id, timeout=8):
    """Название, автор, сколько в игре + universe_id для иконки."""
    try:
        uni = _get_json(
            "https://apis.roblox.com/universes/v1/places/"
            f"{place_id}/universe", timeout=timeout)
        universe_id = uni.get("universeId")
        if not universe_id:
            return None
        data = _get_json(
            "https://games.roblox.com/v1/games?"
            f"universeIds={universe_id}", timeout=timeout)
        rows = data.get("data") or []
        if not rows:
            return None
        game = rows[0]
        return {
            "place_id": place_id,
            "name": str(game.get("name") or ""),
            "creator": str((game.get("creator") or {}).get("name") or ""),
            "playing": int(game.get("playing") or 0),
            "universe_id": str(universe_id),
        }
    except Exception:
        return None


def fetch_game_icon(universe_id, size="250x250"):
    """PNG-иконка игры (bytes) или None."""
    if not universe_id:
        return None
    try:
        data = _get_json(
            "https://thumbnails.roblox.com/v1/games/icons"
            f"?universeIds={universe_id}&size={size}&format=Png"
            "&isCircular=false")
        rows = data.get("data") or []
        if not rows or not rows[0].get("imageUrl"):
            return None
        req = urllib.request.Request(rows[0]["imageUrl"], headers=HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read()
    except Exception:
        return None


# ---- галерея скриншотов ---------------------------------------------------------

SCREENSHOT_RE = re.compile(
    r"robloxscreenshot(\d{8})_(\d{6})(\d{3})", re.IGNORECASE)
JOIN_LOG_RE = re.compile(r"Joining Game \((\d+)/")
GALLERY_CACHE_VERSION = 1
_RU_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def screenshots_dir():
    """Папка снимков Roblox; учитывает перенос «Изображений» в OneDrive."""
    for folder in (SCREENSHOTS_DIR,
                   Path.home() / "OneDrive" / "Pictures" / "Roblox"):
        try:
            if folder.is_dir():
                return folder
        except OSError:
            continue
    return SCREENSHOTS_DIR


def parse_screenshot_time(name, fallback=None):
    """Время снимка из RobloxScreenShotYYYYMMDD_HHMMSSmmm; иначе fallback."""
    match = SCREENSHOT_RE.search(Path(str(name)).name)
    if match:
        try:
            moment = datetime.strptime(
                match.group(1) + match.group(2), "%Y%m%d%H%M%S")
            moment = moment.replace(microsecond=int(match.group(3)) * 1000)
            return moment.timestamp()
        except ValueError:
            pass
    try:
        return float(fallback) if fallback is not None else None
    except (TypeError, ValueError):
        return None


def scan_screenshots(folder=None):
    """Все снимки в папке Roblox, новые сверху."""
    target = Path(folder) if folder is not None else screenshots_dir()
    shots = []
    try:
        if not target.is_dir():
            return shots
        candidates = list(target.rglob("*"))
    except OSError:
        return shots
    for path in candidates:
        if len(shots) >= 5000:
            break
        try:
            if (not path.is_file()
                    or path.suffix.lower() not in SCREENSHOT_EXTS):
                continue
            stat = path.stat()
        except OSError:
            continue
        shots.append({
            "path": str(path),
            "name": path.name,
            "ts": parse_screenshot_time(path.name, stat.st_mtime),
            "mtime": stat.st_mtime,
            "size": stat.st_size,
        })
    shots.sort(key=lambda item: item["ts"], reverse=True)
    return shots


def _iso_epoch(value):
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _epoch_value(value):
    if isinstance(value, (int, float)):
        return float(value)
    return _iso_epoch(value)


def _make_session(start, end, place_id, source, name=""):
    if start is None:
        return None
    if end is None or end <= start:
        end = start + 1800
    return {
        "start": float(start),
        "end": float(end),
        "place_id": str(place_id),
        "source": str(source),
        "name": str(name or ""),
    }


def _merge_sessions(sessions, gap=180):
    """Склеить соседние сессии одного плейса с коротким разрывом."""
    sessions = sorted(sessions, key=lambda item: (item["place_id"],
                                                  item["start"]))
    merged = []
    for session in sessions:
        last = merged[-1] if merged else None
        if (last is not None
                and last["place_id"] == session["place_id"]
                and session["start"] <= last["end"] + gap):
            last["end"] = max(last["end"], session["end"])
            if not last["name"] and session.get("name"):
                last["name"] = session["name"]
            continue
        merged.append(dict(session))
    merged.sort(key=lambda item: item["start"])
    return merged


def _sessions_from_history_file(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        data = (data.get("history") or data.get("games")
                or data.get("data") or [])
    if not isinstance(data, list):
        return []
    label = Path(path).parent.name
    if label.lower() == "cache":
        label = Path(path).parent.parent.name
    label = label or "История"
    sessions = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        place_id = str(entry.get("placeId")
                       or entry.get("place_id") or "").strip()
        if not place_id.isdigit():
            continue
        servers = entry.get("servers")
        if not isinstance(servers, list) or not servers:
            servers = [entry]
        for server in servers:
            if not isinstance(server, dict):
                continue
            start = _epoch_value(server.get("joinedAt")
                                 or server.get("start")
                                 or server.get("ts"))
            end = _epoch_value(server.get("timeLeft")
                               or server.get("leftAt")
                               or server.get("end"))
            session = _make_session(start, end, place_id, label)
            if session:
                sessions.append(session)
    return sessions


def _sessions_from_bloxstrap_log(path):
    try:
        stamp = Path(path).stat().st_mtime
        lines = Path(path).read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    joins = []
    for line in lines:
        match = JOIN_LOG_RE.search(line)
        if not match:
            continue
        head = line.strip().split(" ", 1)[0]
        moment = _iso_epoch(head)
        if moment is None:
            moment = stamp
        joins.append((moment, match.group(1)))
    sessions = []
    for index, (moment, place_id) in enumerate(joins):
        if index + 1 < len(joins):
            end = joins[index + 1][0]
        else:
            end = min(stamp, moment + 4 * 3600)
        session = _make_session(moment, end, place_id, "Bloxstrap")
        if session:
            sessions.append(session)
    return sessions


def collect_play_sessions(config_history=None, history_files=None,
                          log_files=None):
    """Сессии из Froststrap/Bloxstrap и истории запусков лаунчера."""
    local = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData"
                                               / "Local")))
    if history_files is None:
        history_files = [
            local / "Froststrap" / "Cache" / "GameHistory.json",
            local / "Bloxstrap" / "Cache" / "GameHistory.json",
            local / "Bloxstrap" / "GameHistory.json",
        ]
    if log_files is None:
        try:
            log_files = sorted(
                (local / "Bloxstrap" / "Logs").glob("Bloxstrap_*.log"),
                key=lambda item: item.stat().st_mtime, reverse=True)[:30]
        except OSError:
            log_files = []
    sessions = []
    for path in history_files or []:
        try:
            if Path(path).is_file():
                sessions.extend(_sessions_from_history_file(path))
        except OSError:
            continue
    for path in log_files or []:
        sessions.extend(_sessions_from_bloxstrap_log(path))
    entries = []
    for entry in config_history or []:
        if not isinstance(entry, dict):
            continue
        place_id = parse_place_id(
            entry.get("place_id") or entry.get("placeId") or "")
        try:
            moment = float(entry.get("ts") or 0)
        except (TypeError, ValueError):
            moment = 0
        if not place_id or moment <= 0:
            continue
        name = str(entry.get("name") or "").strip()
        if name.upper().startswith("[TITLE") or name.lower() == "unknown":
            name = ""
        entries.append((moment, place_id, name))
    entries.sort()
    for index, (moment, place_id, name) in enumerate(entries):
        if index + 1 < len(entries):
            end = min(entries[index + 1][0], moment + 3 * 3600)
        else:
            end = moment + 2 * 3600
        session = _make_session(moment, end, place_id, "Лаунчер", name)
        if session:
            sessions.append(session)
    return _merge_sessions(sessions)


def find_session(ts, sessions, grace=180):
    """Сессия, в которую попал снимок; при пересечении — более поздняя."""
    best = None
    try:
        moment = float(ts)
    except (TypeError, ValueError):
        return None
    for session in sessions or []:
        if (session["start"] - grace <= moment
                <= session["end"] + grace):
            if best is None or session["start"] > best["start"]:
                best = session
    return best


def gallery_date_label(ts):
    try:
        moment = datetime.fromtimestamp(float(ts))
    except (TypeError, ValueError, OSError):
        return "Без даты"
    return f"{moment.day} {_RU_MONTHS[moment.month - 1]} {moment.year}"


def gallery_time_label(ts):
    try:
        moment = datetime.fromtimestamp(float(ts))
    except (TypeError, ValueError, OSError):
        return ""
    return moment.strftime("%d.%m.%Y · %H:%M")


def gallery_cache_load(path=None):
    empty = {"version": GALLERY_CACHE_VERSION,
             "places": {}, "assignments": {}}
    target = Path(path) if path is not None else GALLERY_CACHE_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    raw_places = data.get("places")
    raw_assign = data.get("assignments")
    places = {}
    if isinstance(raw_places, dict):
        for place_id, info in raw_places.items():
            place_id = str(place_id)
            if not place_id.isdigit() or not isinstance(info, dict):
                continue
            try:
                checked = float(info.get("checked") or 0)
            except (TypeError, ValueError):
                checked = 0
            places[place_id] = {
                "name": str(info.get("name") or "").strip(),
                "creator": str(info.get("creator") or "").strip(),
                "checked": checked,
            }
    assignments = {}
    if isinstance(raw_assign, dict):
        for name, value in raw_assign.items():
            if isinstance(value, dict):
                value = value.get("place_id") or value.get("placeId")
            place_id = str(value or "").strip()
            if name and place_id.isdigit():
                assignments[str(name)] = place_id
    return {"version": GALLERY_CACHE_VERSION,
            "places": places, "assignments": assignments}


def gallery_cache_save(cache, path=None):
    target = Path(path) if path is not None else GALLERY_CACHE_PATH
    payload = {
        "version": GALLERY_CACHE_VERSION,
        "places": cache.get("places") or {},
        "assignments": cache.get("assignments") or {},
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        temp.replace(target)
        return True
    except OSError:
        return False


def gallery_seed_places(cache, sessions):
    """Названия из истории лаунчера — без запросов к API."""
    places = cache.setdefault("places", {})
    for session in sessions or []:
        place_id = str(session.get("place_id") or "")
        name = str(session.get("name") or "").strip()
        if not place_id.isdigit() or not name:
            continue
        info = places.setdefault(
            place_id, {"name": "", "creator": "", "checked": 0})
        if not info.get("name"):
            info["name"] = name


def resolve_gallery_places(cache, place_ids, timeout=6, limit=40):
    """Дотянуть названия плейсов через публичный API и обновить кэш."""
    places = cache.setdefault("places", {})
    now = time.time()
    attempted = resolved = 0
    unique = []
    for place_id in place_ids or []:
        place_id = str(place_id)
        if place_id.isdigit() and place_id not in unique:
            unique.append(place_id)
    for place_id in unique:
        if attempted >= limit:
            break
        info = places.setdefault(
            place_id, {"name": "", "creator": "", "checked": 0})
        if info.get("name"):
            continue
        try:
            checked = float(info.get("checked") or 0)
        except (TypeError, ValueError):
            checked = 0
        if checked and now - checked < 6 * 3600:
            continue
        attempted += 1
        game = fetch_game_info(place_id, timeout=timeout)
        info["checked"] = time.time()
        if game and game.get("name"):
            info["name"] = str(game["name"])
            info["creator"] = str(game.get("creator") or "")
            resolved += 1
    return {"attempted": attempted, "resolved": resolved}


def gallery_assign(cache, names, place_id):
    """Вручную привязать снимки к плейсу; возвращает число записей."""
    place_id = parse_place_id(place_id)
    if not place_id:
        return 0
    assignments = cache.setdefault("assignments", {})
    count = 0
    for name in names or []:
        if name:
            assignments[str(name)] = place_id
            count += 1
    return count


def build_gallery_groups(screenshots, sessions, cache):
    """Сгруппировать снимки по названию плейса, неизвестные — по дате."""
    places = cache.get("places") or {}
    assignments = cache.get("assignments") or {}
    groups = {}
    for shot in screenshots or []:
        try:
            moment = float(shot.get("ts"))
        except (TypeError, ValueError):
            moment = 0
        name = str(shot.get("name") or Path(str(shot.get("path"))).name)
        place_id = assignments.get(name)
        if not place_id:
            session = find_session(moment, sessions)
            if session:
                place_id = session["place_id"]
        item = dict(shot)
        item["name"] = name
        item["time_label"] = gallery_time_label(moment)
        item["date_label"] = gallery_date_label(moment)
        if place_id:
            place_id = str(place_id)
            key = f"place:{place_id}"
            info = places.get(place_id) or {}
            title = str(info.get("name") or "").strip()
            if not title:
                session = find_session(moment, sessions)
                title = str((session or {}).get("name") or "").strip()
            if not title:
                title = f"Place {place_id}"
            creator = str(info.get("creator") or "").strip()
            subtitle = f"от {creator}" if creator else (
                "Place ID " + place_id)
            group = groups.get(key)
            if group is None:
                group = {
                    "key": key, "kind": "place", "place_id": place_id,
                    "title": title, "subtitle": subtitle, "shots": [],
                }
                groups[key] = group
        else:
            key = "date:" + datetime.fromtimestamp(moment).strftime("%Y-%m-%d")
            group = groups.get(key)
            if group is None:
                group = {
                    "key": key, "kind": "date", "place_id": "",
                    "title": gallery_date_label(moment),
                    "subtitle": "Плейс не определён", "shots": [],
                }
                groups[key] = group
        group["shots"].append(item)
    place_groups = sorted(
        (group for group in groups.values() if group["kind"] == "place"),
        key=lambda group: (group["title"].casefold(), group["title"]))
    date_groups = sorted(
        (group for group in groups.values() if group["kind"] == "date"),
        key=lambda group: group["shots"][0]["ts"], reverse=True)
    result = place_groups + date_groups
    for group in result:
        group["shots"].sort(key=lambda shot: shot["ts"], reverse=True)
        group["latest"] = group["shots"][0]["ts"] if group["shots"] else 0
    return result


def gallery_stats(groups):
    total = sum(len(group["shots"]) for group in groups or [])
    known = sum(len(group["shots"]) for group in groups or []
                if group["kind"] == "place")
    return {"total": total, "known": known, "unknown": total - known,
            "groups": len(groups or [])}


def gallery_thumb_path(shot, width=400, height=225):
    source = Path(str(shot.get("path") or shot.get("name") or ""))
    stamp = shot.get("mtime") or 0
    key = f"{source}|{stamp}|{int(width)}x{int(height)}"
    digest = hashlib.sha1(
        key.encode("utf-8", "replace")).hexdigest()[:20]
    return GALLERY_THUMBS_DIR / f"{digest}.jpg"


# ---- FastFlags ------------------------------------------------------------------


def fastflag_values(cfg):
    """Наши флаги (flag -> строковое значение) по текущим настройкам."""
    values = {}
    try:
        fps = int(cfg.get("fps_limit") or 0)
    except (TypeError, ValueError):
        fps = 0
    if 0 < fps <= 1000:
        values[FLAG_FPS] = str(fps)
    mode = str(cfg.get("graphics_mode") or "")
    if mode in FLAG_API:
        for label, key in FLAG_API.items():
            values[key] = "True" if label == mode else "False"
    try:
        quality = int(cfg.get("quality_level") or 0)
    except (TypeError, ValueError):
        quality = 0
    if 1 <= quality <= 10:
        values[FLAG_QUALITY] = str(quality)
    return values


def merge_flags_into(path, values):
    """Слить наши флаги в ClientAppSettings.json, не трогая чужие ключи.

    Возвращает False, если файл есть, но его не удалось прочитать
    (не затираем чужой конфиг).
    """
    data = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(raw, dict):
            return False
        data = raw
    for key in MANAGED_FLAGS:
        data.pop(key, None)
    data.update(values)
    ok, _err = write_json_dict(path, data)
    return ok


def client_settings_paths():
    """Все ClientAppSettings.json, куда имеют смысл наши флаги."""
    paths = []
    local_env = os.getenv("LOCALAPPDATA")
    if local_env:
        local = Path(local_env)
        # стандартный путь официального клиента
        if (local / "Roblox").is_dir():
            paths.append(local / "Roblox" / "ClientSettings"
                         / "ClientAppSettings.json")
        # папки модификаций бутстраперов
        for name in ("Froststrap", "Bloxstrap"):
            if (local / name).is_dir():
                paths.append(local / name / "Modifications" / "ClientSettings"
                             / "ClientAppSettings.json")
    exe = find_player_exe()
    if exe:
        paths.append(exe.parent / "ClientSettings" / "ClientAppSettings.json")
    seen, unique = set(), []
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def apply_fastflags(cfg, values=None):
    """Записать наши флаги во все найденные файлы. Возвращает число файлов."""
    if values is None:
        values = fastflag_values(cfg)
    written = 0
    for path in client_settings_paths():
        if merge_flags_into(path, values):
            written += 1
    return written


# ---- конфиги бутстраперов ----------------------------------------------------------


def read_json_dict(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json_dict(path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
        return True, ""
    except OSError as exc:
        return False, str(exc)


# ---- свои курсоры ------------------------------------------------------------

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CURSOR_README = """\
MaklyxaLauncher — наборы курсоров Roblox
========================================

Каждый набор — отдельная папка внутри CursorSets.
Создавай, переименовывай и удаляй наборы во вкладке «Курсоры».

В наборе четыре слота (структура совместима с Froststrap):
  MouseLockedCursor.png                   ShiftLock
  Cursors\\KeyboardMouse\\ArrowCursor.png      обычная стрелка
  Cursors\\KeyboardMouse\\ArrowFarCursor.png   стрелка в игре
  Cursors\\KeyboardMouse\\IBeamCursor.png      ввод текста

PNG с прозрачным фоном, рекомендуемый размер — 64x64.
Набор можно импортировать и экспортировать ZIP-архивом.

Файлы копируются в установленный клиент Roblox и в Modifications
Froststrap/Bloxstrap. Исходники сохраняются в cursor_backup,
кнопка «Вернуть исходные» откатывает замену.
"""


def ensure_cursor_sets_dir(folder=None):
    """Создаёт папку наборов и памятку. None — если папка недоступна."""
    folder = Path(folder or CURSOR_SETS_DIR)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    readme = folder / "README.txt"
    if not readme.exists():
        try:
            readme.write_text(CURSOR_README, encoding="utf-8-sig")
        except OSError:
            pass
    return folder


def png_info_bytes(data):
    """(ширина, высота, возможно ли прозрачное изображение)."""
    if len(data) < 26 or data[:8] != PNG_SIGNATURE:
        raise ValueError("это не PNG или файл повреждён")
    if data[12:16] != b"IHDR":
        raise ValueError("нет заголовка PNG")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        raise ValueError("некорректный размер")
    color_type = data[25]
    return width, height, color_type in (3, 4, 6)


def png_info(path):
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    return png_info_bytes(data)


def valid_cursor_set_name(name):
    name = str(name or "").strip()
    if not name or name in (".", "..") or len(name) > 48:
        return False
    if name.endswith((" ", ".")):
        return False
    if any(ch in '<>:"/\\|?*' or ord(ch) < 32 for ch in name):
        return False
    return True


def _cursor_sets_base(sets_dir=None):
    return Path(sets_dir) if sets_dir is not None else CURSOR_SETS_DIR


def list_cursor_sets(sets_dir=None):
    base = ensure_cursor_sets_dir(sets_dir)
    if base is None:
        return []
    try:
        return sorted((p.name for p in base.iterdir() if p.is_dir()),
                      key=str.lower)
    except OSError:
        return []


def _unique_cursor_set_name(base, wanted):
    wanted = str(wanted or "").strip()
    if not valid_cursor_set_name(wanted):
        wanted = "Набор"
    name = wanted
    index = 2
    while (Path(base) / name).exists():
        name = f"{wanted} ({index})"
        index += 1
    return name


def create_cursor_set(name=None, sets_dir=None):
    base = ensure_cursor_sets_dir(sets_dir)
    if base is None:
        return {"ok": False, "name": "", "error": "папка наборов недоступна"}
    if name:
        if not valid_cursor_set_name(name):
            return {"ok": False, "name": "", "error": "недопустимое имя набора"}
        if (base / name).exists():
            return {"ok": False, "name": "", "error": "набор уже существует"}
        final_name = str(name).strip()
    else:
        index = 1
        while (base / f"Набор {index}").exists():
            index += 1
        final_name = f"Набор {index}"
    try:
        (base / final_name).mkdir()
    except OSError as exc:
        return {"ok": False, "name": "", "error": str(exc)}
    return {"ok": True, "name": final_name, "error": ""}


def rename_cursor_set(old, new, sets_dir=None):
    base = _cursor_sets_base(sets_dir)
    if not valid_cursor_set_name(old):
        return {"ok": False, "name": "", "error": "набор не найден"}
    old_path = base / str(old)
    new = str(new or "").strip()
    if not old_path.is_dir():
        return {"ok": False, "name": "", "error": "набор не найден"}
    if not valid_cursor_set_name(new):
        return {"ok": False, "name": "", "error": "недопустимое имя набора"}
    if new == old:
        return {"ok": True, "name": new, "error": ""}
    new_path = base / new
    if new_path.exists():
        return {"ok": False, "name": "",
                "error": "набор с таким именем уже есть"}
    try:
        old_path.rename(new_path)
    except OSError as exc:
        return {"ok": False, "name": "", "error": str(exc)}
    _rename_cursor_state_set(str(old), new)
    return {"ok": True, "name": new, "error": ""}


def delete_cursor_set(name, sets_dir=None):
    base = _cursor_sets_base(sets_dir)
    if not valid_cursor_set_name(name):
        return {"ok": False, "error": "набор не найден"}
    target = base / str(name)
    if not target.is_dir():
        return {"ok": False, "error": "набор не найден"}
    try:
        shutil.rmtree(target)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "error": ""}


def cursor_slot_path(set_name, key, sets_dir=None):
    slot = CURSOR_SLOT_BY_KEY.get(key)
    if slot is None or not valid_cursor_set_name(set_name):
        return None
    return _cursor_sets_base(sets_dir) / str(set_name) / slot["set_rel"]


def cursor_set_files(set_name, sets_dir=None):
    """Существующие файлы набора: {ключ слота: путь}."""
    result = {}
    for slot in CURSOR_SLOTS:
        path = cursor_slot_path(set_name, slot["key"], sets_dir)
        if path is not None and path.is_file():
            result[slot["key"]] = path
    return result


def set_cursor_slot(set_name, key, source, sets_dir=None):
    slot = CURSOR_SLOT_BY_KEY.get(key)
    if slot is None:
        return {"ok": False, "error": "неизвестный слот курсора"}
    source = Path(source)
    if not source.is_file():
        return {"ok": False, "error": "файл не найден"}
    try:
        width, height, alpha = png_info(source)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    target = cursor_slot_path(set_name, key, sets_dir)
    if target is None:
        return {"ok": False, "error": "набор не найден"}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "error": "", "path": target,
            "width": width, "height": height, "alpha": alpha}


def remove_cursor_slot(set_name, key, sets_dir=None):
    path = cursor_slot_path(set_name, key, sets_dir)
    if path is None or not path.exists():
        return {"ok": False, "error": "файл не найден"}
    try:
        path.unlink()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "error": ""}


def cursor_set_warnings(set_name, sets_dir=None):
    warnings = []
    for path in cursor_set_files(set_name, sets_dir).values():
        try:
            width, height, alpha = png_info(path)
        except ValueError as exc:
            warnings.append(f"{path.name}: {exc}")
            continue
        if width > 64 or height > 64:
            warnings.append(
                f"{path.name}: {width}×{height}, рекомендуется 64×64")
        if not alpha:
            warnings.append(f"{path.name}: без прозрачности")
    return warnings


def find_player_dirs():
    """Папки версий, где лежит именно игровой RobloxPlayerBeta.exe."""
    roots = []
    local = os.getenv("LOCALAPPDATA", "")
    if local:
        for name in ("Roblox", "Froststrap", "Bloxstrap"):
            roots.append(Path(local) / name / "Versions")
    roots.append(Path(r"C:\Program Files\Froststrap"))
    roots.append(Path(r"C:\Program Files (x86)\Froststrap"))

    found = {}
    for root in roots:
        try:
            if not root.is_dir():
                continue
            for exe in root.glob("**/RobloxPlayerBeta.exe"):
                if exe.is_file():
                    found[str(exe.parent).lower()] = exe.parent
        except OSError:
            continue
    return sorted(found.values(), key=lambda p: str(p).lower())


def file_version_text(path):
    """Версия exe из ресурса Windows («0.745.0.7451137») или ''."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        version = ctypes.WinDLL("version")
        version.GetFileVersionInfoSizeW.argtypes = [
            wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        version.GetFileVersionInfoW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        version.GetFileVersionInfoW.restype = wintypes.BOOL
        version.VerQueryValueW.argtypes = [
            ctypes.c_void_p, wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
        version.VerQueryValueW.restype = wintypes.BOOL
        target = str(path)
        size = version.GetFileVersionInfoSizeW(target, None)
        if not size:
            return ""
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(target, 0, size, buffer):
            return ""
        pointer = ctypes.c_void_p()
        length = wintypes.UINT()
        # строка версии из таблицы строк — как в свойствах файла Windows
        translation = ctypes.c_void_p()
        trans_len = wintypes.UINT()
        if version.VerQueryValueW(buffer, "\\VarFileInfo\\Translation",
                                  ctypes.byref(translation),
                                  ctypes.byref(trans_len)):
            if trans_len.value >= 4:
                pair = ctypes.cast(translation,
                                   ctypes.POINTER(ctypes.c_uint16))
                lang, codepage = pair[0], pair[1]
                for key in ("ProductVersion", "FileVersion"):
                    sub = (f"\\StringFileInfo\\{lang:04x}{codepage:04x}"
                           f"\\{key}")
                    text_ptr = ctypes.c_void_p()
                    text_len = wintypes.UINT()
                    if not version.VerQueryValueW(
                            buffer, sub, ctypes.byref(text_ptr),
                            ctypes.byref(text_len)):
                        continue
                    if not text_len.value:
                        continue
                    value = ctypes.wstring_at(text_ptr, text_len.value)
                    value = value.split("\x00", 1)[0].strip()
                    value = re.sub(r"[,\s]+", ".", value).strip(".")
                    if re.fullmatch(r"\d+(?:\.\d+){2,3}", value):
                        return value
        if not version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer),
                                     ctypes.byref(length)):
            return ""
        if length.value < 16:
            return ""
        numbers = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))
        high, low = numbers[2], numbers[3]
        if not high and not low:
            return ""
        return f"{high >> 16}.{high & 0xffff}.{low >> 16}.{low & 0xffff}"
    except Exception:  # noqa: BLE001 — версия не критична для запуска
        return ""


def roblox_versions_dir():
    """Папка установленных версий официального клиента."""
    local = os.getenv("LOCALAPPDATA")
    if not local:
        return None
    return Path(local) / "Roblox" / "Versions"


def roblox_version_entries():
    """Установленные версии игрового клиента, свежие сверху.

    Одна запись на RobloxPlayerBeta.exe: путь, папка version-…, версия из
    ресурса Windows, дата изменения и размер.
    """
    roots = []
    local = os.getenv("LOCALAPPDATA", "")
    if local:
        for name in ("Roblox", "Froststrap", "Bloxstrap"):
            roots.append(Path(local) / name / "Versions")
    roots.append(Path(r"C:\Program Files\Froststrap"))
    roots.append(Path(r"C:\Program Files (x86)\Froststrap"))

    seen, entries = set(), []
    for root in roots:
        try:
            if not root.is_dir():
                continue
            for exe in root.glob("**/RobloxPlayerBeta.exe"):
                try:
                    if not exe.is_file():
                        continue
                    key = str(exe.resolve()).lower()
                    stat = exe.stat()
                except OSError:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                entries.append({
                    "exe": exe,
                    "folder": exe.parent,
                    "name": exe.parent.name,
                    "version": file_version_text(exe) or exe.parent.name,
                    "mtime": stat.st_mtime,
                    "size": stat.st_size,
                })
        except OSError:
            continue
    entries.sort(key=lambda item: item["mtime"], reverse=True)
    # одна и та же сборка бывает и у Roblox, и у бутстрапера — оставляем
    # свежую копию (она выше в списке после сортировки)
    unique, names = [], set()
    for item in entries:
        key = item["name"].lower()
        if key in names:
            continue
        names.add(key)
        unique.append(item)
    return unique


def selected_roblox_exe(cfg=None):
    """Клиент, который запускает лаунчер: выбранная версия или самая новая."""
    value = str((cfg or {}).get("roblox_version") or "").strip()
    if value:
        try:
            target = Path(value)
            if target.is_file():
                return target
        except OSError:
            pass
    return find_player_exe()


def configured_roblox_exe(cfg=None):
    """Выбранная версия, если она отличается от самой новой, иначе None."""
    chosen = selected_roblox_exe(cfg)
    if chosen is None:
        return None
    newest = find_player_exe()
    try:
        if newest is not None and (
                Path(chosen).resolve() == Path(newest).resolve()
                or Path(chosen).parent.name.lower()
                == Path(newest).parent.name.lower()):
            return None
    except OSError:
        pass
    return Path(chosen)


def cursor_install_roots(prefer_mods=False):
    """Корни клиента и модов: версии игры и Modifications бутстраперов."""
    version_roots = list(find_player_dirs())
    mod_roots = []
    local = os.getenv("LOCALAPPDATA", "")
    if local:
        for name in ("Froststrap", "Bloxstrap"):
            base = Path(local) / name
            if base.is_dir():
                mod_roots.append(base / "Modifications")

    roots = mod_roots + version_roots if prefer_mods \
        else version_roots + mod_roots
    seen, unique = set(), []
    for path in roots:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _cursor_backup_path(target, backup_dir):
    digest = hashlib.sha1(str(target).lower().encode("utf-8")).hexdigest()[:20]
    return Path(backup_dir) / f"{digest}_{target.name}"


def _load_cursor_state(path):
    data = read_json_dict(Path(path))
    items = data.get("files")
    if not isinstance(items, list):
        return []
    clean = []
    for item in items:
        if not isinstance(item, dict) or not item.get("target"):
            continue
        backup = str(item.get("backup") or "")
        clean.append({"target": str(item["target"]), "backup": backup,
                      "source": str(item.get("source") or "")})
    return clean


def _save_cursor_state(path, items, set_name=""):
    write_json_dict(Path(path), {"files": items,
                                 "set": str(set_name or "")})


def _rename_cursor_state_set(old, new):
    data = read_json_dict(CURSOR_STATE_PATH)
    if str(data.get("set") or "") != str(old):
        return
    data["set"] = str(new)
    write_json_dict(CURSOR_STATE_PATH, data)


def cursor_state_info(state_path=None):
    data = read_json_dict(Path(state_path or CURSOR_STATE_PATH))
    items = data.get("files")
    return {"set": str(data.get("set") or ""),
            "files": len(items) if isinstance(items, list) else 0}


def restore_custom_cursors(state_path=None, backup_dir=None):
    """Вернуть файлы, которые были до замены, и очистить состояние."""
    state_path = Path(state_path or CURSOR_STATE_PATH)
    backup_dir = Path(backup_dir or CURSOR_BACKUP_DIR)
    items = _load_cursor_state(state_path)
    remaining = []
    errors = []
    restored = 0

    for item in items:
        try:
            target = Path(item["target"])
            backup = Path(item["backup"]) if item["backup"] else None
            if not target.parent.exists():
                if backup and backup.exists():
                    backup.unlink()
                restored += 1
                continue
            if backup is not None:
                if not backup.exists():
                    if target.exists():
                        raise OSError("резервная копия не найдена")
                    restored += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(backup, target)
                try:
                    backup.unlink()
                except OSError:
                    pass
            elif target.exists():
                target.unlink()
            restored += 1
        except (OSError, ValueError) as exc:
            remaining.append(item)
            errors.append(f"{Path(item.get('target', '')).name}: {exc}")

    _save_cursor_state(state_path, remaining)
    return {"ok": not errors, "restored": restored, "errors": errors}


def _cursor_error(message, set_name=""):
    return {"ok": False, "files": 0, "installs": 0, "copied": 0,
            "errors": [message], "error": message,
            "set": str(set_name or "")}


def apply_cursor_set(name, roots=None, state_path=None, backup_dir=None,
                     sets_dir=None):
    """Скопировать набор в клиент, сохранив исходные файлы для отката."""
    base = ensure_cursor_sets_dir(sets_dir)
    if base is None:
        return _cursor_error("папка наборов недоступна", name)
    name = str(name or "")
    if not valid_cursor_set_name(name):
        return _cursor_error("набор не найден", name)
    set_dir = base / name
    if not set_dir.is_dir():
        return _cursor_error("набор не найден", name)

    files = []
    for slot in CURSOR_SLOTS:
        source = set_dir / slot["set_rel"]
        if not source.is_file():
            continue
        try:
            png_info(source)
        except ValueError as exc:
            return _cursor_error(f"{slot['file']}: {exc}", name)
        files.append((slot, source))
    if not files:
        return _cursor_error("в наборе нет курсоров", name)

    raw_roots = (list(roots) if roots is not None
                 else cursor_install_roots())
    clean_roots, seen = [], set()
    for root in raw_roots:
        root = Path(root)
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            clean_roots.append(root)
    if not clean_roots:
        return _cursor_error("папки Roblox не найдены", name)

    state_path = Path(state_path or CURSOR_STATE_PATH)
    backup_dir = Path(backup_dir or CURSOR_BACKUP_DIR)
    old = restore_custom_cursors(state_path, backup_dir)
    if not old["ok"]:
        return _cursor_error(
            "не удалось вернуть исходные файлы: "
            + "; ".join(old["errors"]), name)

    applied, errors = [], []
    copied = installs = 0
    for root in clean_roots:
        wrote_here = False
        for slot, source in files:
            target = root / slot["target_rel"]
            backup = None
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    backup = _cursor_backup_path(target, backup_dir)
                    if not backup.exists():
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(target, backup)
                applied.append({"target": str(target),
                                "backup": str(backup) if backup else "",
                                "source": str(source)})
                shutil.copyfile(source, target)
                copied += 1
                wrote_here = True
            except OSError as exc:
                errors.append(f"{target}: {exc}")
        if wrote_here:
            installs += 1

    _save_cursor_state(state_path, applied, name)
    return {"ok": not errors, "files": len(files),
            "installs": installs, "copied": copied,
            "errors": errors, "error": errors[0] if errors else "",
            "set": name}


def copy_current_cursor_set(name, roots=None, sets_dir=None):
    """Скопировать текущие файлы курсоров клиента в выбранный набор."""
    base = ensure_cursor_sets_dir(sets_dir)
    if (base is None or not valid_cursor_set_name(name)
            or not (base / str(name)).is_dir()):
        return {"ok": False, "copied": 0, "missing": [],
                "errors": ["набор не найден"], "error": "набор не найден"}
    raw_roots = (cursor_install_roots(prefer_mods=True)
                 if roots is None else list(roots))
    copied, missing, errors = 0, [], []
    for slot in CURSOR_SLOTS:
        source = None
        for root in raw_roots:
            candidate = Path(root) / slot["target_rel"]
            if candidate.is_file():
                source = candidate
                break
        dest = cursor_slot_path(name, slot["key"], sets_dir)
        if source is None:
            missing.append(slot["label"])
            try:
                if dest.exists():
                    dest.unlink()
            except OSError as exc:
                errors.append(f"{slot['label']}: {exc}")
            continue
        try:
            png_info(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            copied += 1
        except (OSError, ValueError) as exc:
            errors.append(f"{slot['label']}: {exc}")
    if copied == 0 and not errors:
        errors.append("в клиенте не найдены файлы курсоров")
    return {"ok": not errors, "copied": copied, "missing": missing,
            "errors": errors, "error": errors[0] if errors else ""}


def export_cursor_set(name, zip_path, sets_dir=None):
    files = cursor_set_files(name, sets_dir)
    if not files:
        return {"ok": False, "files": 0, "error": "в наборе нет курсоров"}
    zip_path = Path(zip_path)
    try:
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for slot in CURSOR_SLOTS:
                path = files.get(slot["key"])
                if path is not None:
                    zf.write(path, slot["set_rel"].as_posix())
    except (OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "files": 0, "error": str(exc)}
    return {"ok": True, "files": len(files), "path": zip_path, "error": ""}


def import_cursor_set(zip_path, name=None, sets_dir=None):
    """Импортировать ZIP (в том числе набор Froststrap) в новый набор."""
    base = ensure_cursor_sets_dir(sets_dir)
    if base is None:
        return {"ok": False, "name": "", "files": 0, "skipped": 0,
                "error": "папка наборов недоступна"}
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        return {"ok": False, "name": "", "files": 0, "skipped": 0,
                "error": "архив не найден"}

    found, skipped = {}, 0
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                base_name = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
                slot = CURSOR_SLOT_BY_FILE.get(base_name.lower())
                if slot is None:
                    continue
                if slot["key"] in found or info.file_size > 8 * 1024 * 1024:
                    skipped += 1
                    continue
                try:
                    data = zf.read(info)
                    png_info_bytes(data)
                except (OSError, ValueError):
                    skipped += 1
                    continue
                found[slot["key"]] = data
    except (OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "name": "", "files": 0, "skipped": skipped,
                "error": str(exc)}

    if not found:
        return {"ok": False, "name": "", "files": 0, "skipped": skipped,
                "error": "в архиве нет PNG с именами курсоров"}

    wanted = name or zip_path.stem
    if not valid_cursor_set_name(wanted):
        wanted = "Импорт"
    final_name = _unique_cursor_set_name(base, wanted)
    created = create_cursor_set(final_name, sets_dir)
    if not created["ok"]:
        return {"ok": False, "name": "", "files": 0, "skipped": skipped,
                "error": created["error"]}

    try:
        for key, data in found.items():
            dest = cursor_slot_path(final_name, key, sets_dir)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    except OSError as exc:
        shutil.rmtree(base / final_name, ignore_errors=True)
        return {"ok": False, "name": "", "files": 0, "skipped": skipped,
                "error": str(exc)}
    return {"ok": True, "name": final_name, "files": len(found),
            "skipped": skipped, "error": ""}


# ---- свои темы оформления -----------------------------------------------------

THEME_TINTS = [
    ("#00c48c", "Зелёная"),
    ("#3b9eff", "Синяя"),
    ("#a06bff", "Фиолетовая"),
    ("#ff9f43", "Оранжевая"),
    ("#ff5f9e", "Розовая"),
    ("#e5484d", "Красная"),
    ("#16c6c6", "Бирюзовая"),
    ("#e8b93b", "Золотая"),
]
THEME_PALETTE_KEYS = ("bg", "panel", "surface", "surface2", "hover", "entry",
                      "list", "border", "text", "muted", "on_accent",
                      "titlebar")
THEME_IMAGE_MAX = 20 * 1024 * 1024
# Нейтральное «стекло» для режима «только картинка»: цвета темы отключены,
# остаются тёмные поверхности и светлый акцент.
IMAGE_ONLY_PALETTE = {
    "bg": "#0b0f0d",
    "panel": "#101512",
    "surface": "#151b18",
    "surface2": "#1d2521",
    "hover": "#27312c",
    "entry": "#131917",
    "list": "#0e1311",
    "border": "#2b3630",
    "text": "#f1f5f3",
    "muted": "#97a39d",
    "on_accent": "#0b100e",
    "titlebar": "#090d0b",
}
IMAGE_ONLY_ACCENT = "#dfe8e3"
THEME_README = """\
MaklyxaLauncher — свои темы
===========================

Каждая тема — папка внутри ThemeSets:
  theme.json     цвета и настройки
  background.*   картинка-фон (PNG, JPG, WEBP, BMP или GIF)

Тему можно создать в лаунчере (раздел «Темы»), поменять цвета,
поставить свою картинку и экспортировать ZIP-архивом.

Цвета задаются в theme.json в формате #RRGGBB. Картинка
показывается фоном рабочей области лаунчера. Режим
"image_only": true убирает цвета темы и оставляет только картинку.
"""


def _hex_ok(value):
    return bool(re.fullmatch(r"#[0-9a-fA-F]{6}", str(value or "")))


def mix_hex(first, second, ratio):
    """Смешать два цвета; ratio — доля second."""
    try:
        a = [int(str(first).lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
        b = [int(str(second).lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    except (ValueError, TypeError):
        return first if _hex_ok(first) else "#000000"
    ratio = max(0.0, min(1.0, float(ratio)))
    return "#%02x%02x%02x" % tuple(
        int(round(a[i] + (b[i] - a[i]) * ratio)) for i in range(3))


def _luma(hex_color):
    try:
        r, g, b = (int(str(hex_color).lstrip("#")[i:i + 2], 16)
                   for i in (0, 2, 4))
    except (ValueError, TypeError):
        return 0.5
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def build_theme_palette(base="dark", tint="#00c48c", accent="#00c48c",
                        image_only=False):
    """Палитра своей темы: базовая тёмная/светлая + оттенок и акцент.

    image_only=True — цвета темы отключены, интерфейс нейтральный,
    картинка остаётся единственным оформлением.
    """
    base_key = "light" if str(base).lower() == "light" else "dark"
    tint = str(tint).lower() if _hex_ok(tint) else "#00c48c"
    accent = str(accent).lower() if _hex_ok(accent) else "#00c48c"
    if image_only:
        palette = dict(IMAGE_ONLY_PALETTE)
        palette.update({
            "alpha": 1.0,
            "label": "",
            "base": "dark",
            "tint": IMAGE_ONLY_ACCENT,
            "accent": IMAGE_ONLY_ACCENT,
            "image_only": True,
        })
        return palette
    source = THEMES[base_key]
    ratios = ({
        "bg": 0.06, "panel": 0.10, "surface": 0.14, "surface2": 0.20,
        "hover": 0.26, "entry": 0.16, "list": 0.10, "border": 0.34,
        "titlebar": 0.10,
    } if base_key == "dark" else {
        "bg": 0.035, "panel": 0.045, "surface": 0.035, "surface2": 0.075,
        "hover": 0.12, "entry": 0.055, "list": 0.05, "border": 0.16,
        "titlebar": 0.03,
    })
    palette = {key: mix_hex(source[key], tint, ratio)
               for key, ratio in ratios.items()}
    palette.update({
        "text": source["text"],
        "muted": source["muted"],
        "on_accent": "#071510" if _luma(accent) > 0.5 else "#ffffff",
        "alpha": 1.0,
        "label": "",
        "base": base_key,
        "tint": tint,
        "accent": accent,
        "image_only": False,
    })
    return palette


def valid_theme_set_name(name):
    return valid_cursor_set_name(name)


def ensure_theme_sets_dir(folder=None):
    folder = Path(folder or THEME_SETS_DIR)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    readme = folder / "README.txt"
    if not readme.exists():
        try:
            readme.write_text(THEME_README, encoding="utf-8-sig")
        except OSError:
            pass
    return folder


def _theme_sets_base(sets_dir=None):
    return Path(sets_dir) if sets_dir is not None else THEME_SETS_DIR


def list_theme_sets(sets_dir=None):
    base = ensure_theme_sets_dir(sets_dir)
    if base is None:
        return []
    try:
        return sorted(
            (p.name for p in base.iterdir()
             if p.is_dir() and (p / "theme.json").is_file()),
            key=str.lower)
    except OSError:
        return []


def _unique_theme_set_name(base, wanted):
    wanted = str(wanted or "").strip()
    if not valid_theme_set_name(wanted):
        wanted = "Моя тема"
    name = wanted
    index = 2
    while (Path(base) / name).exists():
        name = f"{wanted} ({index})"
        index += 1
    return name


def theme_image_kind(path):
    """Расширение по сигнатуре картинки или ''."""
    try:
        data = Path(path).read_bytes()[:32]
    except OSError:
        return ""
    if data.startswith(PNG_SIGNATURE):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    if (len(data) >= 12 and data[:4] == b"RIFF"
            and data[8:12] == b"WEBP"):
        return ".webp"
    return ""


def theme_image_kind_bytes(data):
    if data.startswith(PNG_SIGNATURE):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def theme_image_file(name, sets_dir=None):
    """Путь к картинке темы внутри папки набора (или None)."""
    if not valid_theme_set_name(name):
        return None
    base = _theme_sets_base(sets_dir)
    set_dir = base / str(name)
    raw = read_json_dict(set_dir / "theme.json")
    image = str(raw.get("image") or "")
    if not image:
        return None
    candidate = set_dir / Path(image).name
    try:
        if candidate.is_file() and set_dir in candidate.resolve().parents:
            return candidate
    except OSError:
        pass
    return None


def load_theme_palette(name, sets_dir=None):
    """Палитра своей темы для интерфейса; None — темы нет."""
    if not valid_theme_set_name(name):
        return None
    base = _theme_sets_base(sets_dir)
    set_dir = base / str(name)
    raw = read_json_dict(set_dir / "theme.json")
    if not raw:
        return None
    base_key = "light" if str(raw.get("base")).lower() == "light" else "dark"
    tint = str(raw.get("tint") or "").lower()
    accent = str(raw.get("accent") or "").lower()
    tint = tint if _hex_ok(tint) else "#00c48c"
    accent = accent if _hex_ok(accent) else tint
    image_only = bool(raw.get("image_only"))
    palette = raw.get("palette")
    valid = (isinstance(palette, dict)
             and all(_hex_ok(palette.get(key)) for key in THEME_PALETTE_KEYS))
    if image_only:
        palette = build_theme_palette(base_key, tint, accent,
                                      image_only=True)
    elif not valid:
        palette = build_theme_palette(base_key, tint, accent)
    else:
        palette = dict(palette)
    palette.update({
        "label": str(name),
        "base": palette.get("base") or base_key,
        "tint": palette.get("tint") or tint,
        "accent": palette.get("accent") or accent,
        "stored_base": base_key,
        "stored_tint": tint,
        "stored_accent": accent,
        "alpha": 1.0,
        "custom": True,
        "image": None,
        "image_only": image_only,
    })
    image = theme_image_file(name, sets_dir)
    if image is not None:
        palette["image"] = str(image)
    return palette


def theme_set_info(name, sets_dir=None):
    palette = load_theme_palette(name, sets_dir)
    if palette is None:
        return {"ok": False, "error": "тема не найдена"}
    image = palette.get("image")
    width = height = 0
    if image:
        try:
            width, height, _alpha = png_info(image)
        except ValueError:
            width = height = 0
    return {"ok": True, "name": str(name),
            "base": palette.get("stored_base") or palette["base"],
            "tint": palette.get("stored_tint") or palette["tint"],
            "accent": palette.get("stored_accent") or palette["accent"],
            "image_only": bool(palette.get("image_only")),
            "image": Path(image) if image else None,
            "width": width, "height": height, "error": ""}


def _write_theme_json(set_dir, name, base, tint, accent, palette, image="",
                      image_only=False):
    data = {
        "version": 1,
        "name": str(name),
        "base": base,
        "tint": tint,
        "accent": accent,
        "image": image,
        "image_only": bool(image_only),
        "palette": palette,
    }
    return write_json_dict(Path(set_dir) / "theme.json", data)


def set_theme_image(name, source, sets_dir=None):
    if not valid_theme_set_name(name):
        return {"ok": False, "error": "тема не найдена", "image": ""}
    base = ensure_theme_sets_dir(sets_dir)
    if base is None:
        return {"ok": False, "error": "папка тем недоступна", "image": ""}
    set_dir = base / str(name)
    if not (set_dir / "theme.json").is_file():
        return {"ok": False, "error": "тема не найдена", "image": ""}
    source = Path(source)
    if not source.is_file():
        return {"ok": False, "error": "файл не найден", "image": ""}
    try:
        if source.stat().st_size > THEME_IMAGE_MAX:
            return {"ok": False, "error": "картинка больше 20 МБ",
                    "image": ""}
    except OSError as exc:
        return {"ok": False, "error": str(exc), "image": ""}
    kind = theme_image_kind(source)
    if not kind:
        return {"ok": False, "image": "",
                "error": "нужна картинка PNG, JPG, WEBP, BMP или GIF"}
    for old in set_dir.glob("background.*"):
        try:
            old.unlink()
        except OSError:
            pass
    filename = f"background{kind}"
    try:
        shutil.copyfile(source, set_dir / filename)
    except OSError as exc:
        return {"ok": False, "error": str(exc), "image": ""}
    raw = read_json_dict(set_dir / "theme.json")
    raw["image"] = filename
    ok, err = write_json_dict(set_dir / "theme.json", raw)
    if not ok:
        return {"ok": False, "error": err, "image": ""}
    return {"ok": True, "error": "", "image": filename,
            "path": set_dir / filename}


def remove_theme_image(name, sets_dir=None):
    if not valid_theme_set_name(name):
        return {"ok": False, "error": "тема не найдена"}
    base = _theme_sets_base(sets_dir)
    set_dir = base / str(name)
    if not (set_dir / "theme.json").is_file():
        return {"ok": False, "error": "тема не найдена"}
    for old in set_dir.glob("background.*"):
        try:
            old.unlink()
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
    raw = read_json_dict(set_dir / "theme.json")
    raw["image"] = ""
    write_json_dict(set_dir / "theme.json", raw)
    return {"ok": True, "error": ""}


def create_theme_set(name=None, base="dark", tint="#00c48c",
                     accent="#00c48c", image=None, image_only=False,
                     sets_dir=None):
    base_dir = ensure_theme_sets_dir(sets_dir)
    if base_dir is None:
        return {"ok": False, "name": "", "error": "папка тем недоступна"}
    if name:
        if not valid_theme_set_name(name):
            return {"ok": False, "name": "", "error": "недопустимое имя темы"}
        if (base_dir / str(name)).exists():
            return {"ok": False, "name": "", "error": "тема уже существует"}
        final_name = str(name).strip()
    else:
        final_name = _unique_theme_set_name(base_dir, "Моя тема")
    base_key = "light" if str(base).lower() == "light" else "dark"
    tint = str(tint).lower() if _hex_ok(tint) else "#00c48c"
    accent = str(accent).lower() if _hex_ok(accent) else tint
    set_dir = base_dir / final_name
    try:
        set_dir.mkdir()
    except OSError as exc:
        return {"ok": False, "name": "", "error": str(exc)}
    palette = build_theme_palette(base_key, tint, accent,
                                  image_only=bool(image_only))
    ok, err = _write_theme_json(set_dir, final_name, base_key, tint, accent,
                                palette, image_only=image_only)
    if not ok:
        shutil.rmtree(set_dir, ignore_errors=True)
        return {"ok": False, "name": "", "error": err}
    if image:
        added = set_theme_image(final_name, image, sets_dir)
        if not added["ok"]:
            shutil.rmtree(set_dir, ignore_errors=True)
            return {"ok": False, "name": "", "error": added["error"]}
    return {"ok": True, "name": final_name, "error": ""}


def update_theme_set(name, base=None, tint=None, accent=None, image=None,
                     remove_image=False, image_only=None, sets_dir=None):
    palette = load_theme_palette(name, sets_dir)
    if palette is None:
        return {"ok": False, "error": "тема не найдена"}
    base_key = (str(base).lower() if base
                else palette.get("stored_base") or palette["base"])
    base_key = "light" if base_key == "light" else "dark"
    tint = (str(tint).lower() if tint and _hex_ok(tint)
            else palette.get("stored_tint") or palette["tint"])
    accent = (str(accent).lower() if accent and _hex_ok(accent)
              else palette.get("stored_accent") or palette["accent"])
    set_dir = _theme_sets_base(sets_dir) / str(name)
    raw = read_json_dict(set_dir / "theme.json")
    flag = (bool(raw.get("image_only")) if image_only is None
            else bool(image_only))
    new_palette = build_theme_palette(base_key, tint, accent,
                                      image_only=flag)
    image_name = str(raw.get("image") or "")
    if remove_image:
        removed = remove_theme_image(name, sets_dir)
        if not removed["ok"]:
            return removed
        image_name = ""
    elif image:
        added = set_theme_image(name, image, sets_dir)
        if not added["ok"]:
            return added
        image_name = added["image"]
    ok, err = _write_theme_json(set_dir, name, base_key, tint, accent,
                                new_palette, image_name,
                                image_only=flag)
    return {"ok": ok, "name": str(name), "error": err,
            "image_only": flag}


def rename_theme_set(old, new, sets_dir=None):
    base = _theme_sets_base(sets_dir)
    if not valid_theme_set_name(old):
        return {"ok": False, "name": "", "error": "тема не найдена"}
    old_path = base / str(old)
    new = str(new or "").strip()
    if not old_path.is_dir():
        return {"ok": False, "name": "", "error": "тема не найдена"}
    if not valid_theme_set_name(new):
        return {"ok": False, "name": "", "error": "недопустимое имя темы"}
    if new == old:
        return {"ok": True, "name": new, "error": ""}
    if (base / new).exists():
        return {"ok": False, "name": "", "error": "тема с таким именем уже есть"}
    try:
        old_path.rename(base / new)
    except OSError as exc:
        return {"ok": False, "name": "", "error": str(exc)}
    raw = read_json_dict(base / new / "theme.json")
    raw["name"] = new
    write_json_dict(base / new / "theme.json", raw)
    return {"ok": True, "name": new, "error": ""}


def delete_theme_set(name, sets_dir=None):
    base = _theme_sets_base(sets_dir)
    if not valid_theme_set_name(name):
        return {"ok": False, "error": "тема не найдена"}
    target = base / str(name)
    if not target.is_dir():
        return {"ok": False, "error": "тема не найдена"}
    try:
        shutil.rmtree(target)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "error": ""}


def export_theme_set(name, zip_path, sets_dir=None):
    palette = load_theme_palette(name, sets_dir)
    if palette is None:
        return {"ok": False, "error": "тема не найдена", "files": 0}
    set_dir = _theme_sets_base(sets_dir) / str(name)
    zip_path = Path(zip_path)
    files = 1
    try:
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(set_dir / "theme.json", "theme.json")
            image = theme_image_file(name, sets_dir)
            if image is not None:
                zf.write(image, image.name)
                files += 1
    except (OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "error": str(exc), "files": 0}
    return {"ok": True, "error": "", "files": files, "path": zip_path}


def import_theme_set(zip_path, name=None, sets_dir=None):
    base = ensure_theme_sets_dir(sets_dir)
    if base is None:
        return {"ok": False, "name": "", "error": "папка тем недоступна"}
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        return {"ok": False, "name": "", "error": "архив не найден"}
    raw, image_data, image_ext = None, None, ""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = [m for m in zf.infolist()
                       if not m.is_dir() and m.file_size <= THEME_IMAGE_MAX]
            theme_members = [m for m in members
                             if m.filename.replace("\\", "/").rsplit("/", 1)[-1]
                             == "theme.json"
                             and m.filename.count("/") <= 1]
            if not theme_members:
                return {"ok": False, "name": "",
                        "error": "в архиве нет theme.json"}
            raw = json.loads(zf.read(theme_members[0]).decode("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("theme.json повреждён")
            image_name = str(raw.get("image") or "").rsplit("/", 1)[-1]
            candidates = [m for m in members
                          if m.filename.replace("\\", "/").rsplit("/", 1)[-1]
                          == image_name and image_name]
            if not candidates:
                candidates = [m for m in members
                              if m.filename.replace("\\", "/")
                              .rsplit("/", 1)[-1].startswith("background.")]
            if candidates:
                image_data = zf.read(candidates[0])
                image_ext = theme_image_kind_bytes(image_data)
                if not image_ext:
                    image_data = None
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return {"ok": False, "name": "", "error": str(exc)}

    wanted = str(name or raw.get("name") or zip_path.stem).strip()
    if not valid_theme_set_name(wanted):
        wanted = "Импорт"
    final_name = _unique_theme_set_name(base, wanted)
    base_key = "light" if str(raw.get("base")).lower() == "light" else "dark"
    tint = str(raw.get("tint") or "").lower()
    accent = str(raw.get("accent") or "").lower()
    tint = tint if _hex_ok(tint) else "#00c48c"
    accent = accent if _hex_ok(accent) else tint
    image_only = bool(raw.get("image_only"))
    if image_only:
        palette = build_theme_palette(base_key, tint, accent,
                                      image_only=True)
    else:
        palette = raw.get("palette")
        if not (isinstance(palette, dict)
                and all(_hex_ok(palette.get(key))
                        for key in THEME_PALETTE_KEYS)):
            palette = build_theme_palette(base_key, tint, accent)
    set_dir = base / final_name
    try:
        set_dir.mkdir()
    except OSError as exc:
        return {"ok": False, "name": "", "error": str(exc)}
    image_name = f"background{image_ext}" if image_data else ""
    try:
        if image_data:
            (set_dir / image_name).write_bytes(image_data)
    except OSError as exc:
        shutil.rmtree(set_dir, ignore_errors=True)
        return {"ok": False, "name": "", "error": str(exc)}
    ok, err = _write_theme_json(set_dir, final_name, base_key, tint, accent,
                                palette, image_name,
                                image_only=image_only)
    if not ok:
        shutil.rmtree(set_dir, ignore_errors=True)
        return {"ok": False, "name": "", "error": err}
    return {"ok": True, "name": final_name, "error": "",
            "image": bool(image_data), "image_only": image_only}


def find_bootstrapper_settings():
    """('froststrap'|'bloxstrap', путь к Settings.json) или (None, None)."""
    local_env = os.getenv("LOCALAPPDATA")
    if not local_env:
        return None, None
    local = Path(local_env)
    for kind, path in (("froststrap", local / "Froststrap" / "Settings.json"),
                       ("bloxstrap", local / "Bloxstrap" / "Settings.json")):
        if path.exists():
            return kind, path
    return None, None


def datacenter_regions():
    """Регионы серверов из кэша Froststrap (['Auto', 'Amsterdam, NL', ...])."""
    local = os.getenv("LOCALAPPDATA", "")
    if not local:
        return ["Auto"]
    try:
        cache = Path(local) / "Froststrap" / "Cache" / "DataCentersCache.json"
        data = read_json_dict(cache)
        regions = data.get("regions")
        if isinstance(regions, dict) and regions:
            return ["Auto"] + sorted(regions)
    except Exception:
        pass
    return ["Auto"]


# ---- стиль запуска: иконка, звук, ярлыки --------------------------------------

LAUNCH_ICON_MAX = 8 * 1024 * 1024
LAUNCH_ICON_SIZES = ((256, 256), (128, 128), (64, 64),
                     (48, 48), (32, 32), (16, 16))
LAUNCH_SOUND_EXTS = (".wav", ".mp3")
LAUNCH_SOUND_ROLES = {"launch": "sound", "error": "error"}
MCI_ALIAS = "maklyxa_launch_sound"
_mci_lock = threading.Lock()


def _launch_error(message):
    return {"ok": False, "error": str(message), "path": None}


def import_launch_icon(source, icon_path=None, preview_path=None):
    """Сделать из картинки многоразмерный ICO и превью для интерфейса."""
    source = Path(source)
    icon_path = Path(icon_path or LAUNCH_ICON_PATH)
    preview_path = Path(preview_path or LAUNCH_ICON_PREVIEW)
    if not source.is_file():
        return _launch_error("файл не найден")
    try:
        if source.stat().st_size > LAUNCH_ICON_MAX:
            return _launch_error("иконка больше 8 МБ")
    except OSError as exc:
        return _launch_error(exc)
    try:
        from PIL import Image
    except ImportError:
        return _launch_error("нужен модуль Pillow")
    try:
        with Image.open(source) as raw:
            image = raw.convert("RGBA")
    except Exception:  # noqa: BLE001 — Pillow бросает разные типы
        return _launch_error("не удалось прочитать картинку")
    side = min(image.size)
    if side <= 0:
        return _launch_error("пустая картинка")
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side))
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
    image = image.resize((256, 256), resample)
    try:
        icon_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(icon_path, format="ICO", sizes=list(LAUNCH_ICON_SIZES))
        image.resize((112, 112), resample).save(preview_path, format="PNG")
    except (OSError, ValueError) as exc:
        return _launch_error(exc)
    return {"ok": True, "error": "", "path": icon_path,
            "preview": preview_path, "name": source.name}


def remove_launch_icon(icon_path=None, preview_path=None):
    removed = 0
    errors = []
    paths = (Path(icon_path or LAUNCH_ICON_PATH),
             Path(preview_path or LAUNCH_ICON_PREVIEW))
    for path in paths:
        try:
            if path.exists():
                path.unlink()
                removed += 1
        except OSError as exc:
            errors.append(str(exc))
    return {"ok": not errors, "removed": removed,
            "error": errors[0] if errors else ""}


def launch_icon_state(icon_path=None, preview_path=None):
    icon = Path(icon_path or LAUNCH_ICON_PATH)
    preview = Path(preview_path or LAUNCH_ICON_PREVIEW)
    custom = icon.is_file()
    return {"custom": custom,
            "icon": icon if custom else None,
            "preview": preview if preview.is_file() else None}


def _sound_signature_ok(path, ext):
    try:
        with Path(path).open("rb") as fh:
            head = fh.read(12)
    except OSError:
        return False
    if ext == ".wav":
        return (len(head) >= 12 and head[:4] == b"RIFF"
                and head[8:12] == b"WAVE")
    if ext == ".mp3":
        if head[:3] == b"ID3":
            return True
        return (len(head) >= 2 and head[0] == 0xFF
                and (head[1] & 0xE0) == 0xE0)
    return False


def _sound_stem(role):
    return LAUNCH_SOUND_ROLES.get(str(role or "launch"), "sound")


def launch_sound_path(sound_dir=None, role="launch"):
    base = Path(sound_dir or LAUNCH_DIR)
    stem = _sound_stem(role)
    for ext in LAUNCH_SOUND_EXTS:
        path = base / f"{stem}{ext}"
        if path.is_file():
            return path
    return None


def import_launch_sound(source, sound_dir=None, role="launch"):
    source = Path(source)
    base = Path(sound_dir or LAUNCH_DIR)
    stem = _sound_stem(role)
    ext = source.suffix.lower()
    if not source.is_file():
        return _launch_error("файл не найден")
    if ext not in LAUNCH_SOUND_EXTS:
        return _launch_error("нужен звук WAV или MP3")
    try:
        if source.stat().st_size > LAUNCH_SOUND_MAX:
            return _launch_error("звук больше 12 МБ")
    except OSError as exc:
        return _launch_error(exc)
    if not _sound_signature_ok(source, ext):
        return _launch_error("файл не похож на WAV или MP3")
    dest = base / f"{stem}{ext}"
    try:
        base.mkdir(parents=True, exist_ok=True)
        stop_launch_sound()
        if source.resolve() != dest.resolve():
            shutil.copyfile(source, dest)
    except OSError as exc:
        return _launch_error(exc)
    for other in LAUNCH_SOUND_EXTS:
        old = base / f"{stem}{other}"
        if old != dest:
            try:
                old.unlink()
            except OSError:
                pass
    return {"ok": True, "error": "", "path": dest, "name": source.name}


def remove_launch_sound(sound_dir=None, role="launch"):
    stop_launch_sound()
    base = Path(sound_dir or LAUNCH_DIR)
    stem = _sound_stem(role)
    removed = 0
    errors = []
    for ext in LAUNCH_SOUND_EXTS:
        path = base / f"{stem}{ext}"
        try:
            if path.exists():
                path.unlink()
                removed += 1
        except OSError as exc:
            errors.append(str(exc))
    return {"ok": not errors, "removed": removed,
            "error": errors[0] if errors else ""}


def _mci_send(command):
    if os.name != "nt":
        return 1
    try:
        winmm = ctypes.windll.winmm
        winmm.mciSendStringW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
            ctypes.c_void_p]
        winmm.mciSendStringW.restype = ctypes.c_uint
        return winmm.mciSendStringW(command, None, 0, None)
    except (AttributeError, OSError):
        return 1


def _mci_close():
    _mci_send(f"stop {MCI_ALIAS}")
    _mci_send(f"close {MCI_ALIAS}")


def play_launch_sound(path=None):
    """Проиграть WAV/MP3 асинхронно через Windows MCI."""
    target = Path(path) if path else launch_sound_path()
    if target is None or not target.is_file():
        return False
    kind = "mpegvideo" if target.suffix.lower() == ".mp3" else "waveaudio"
    with _mci_lock:
        _mci_close()
        if _mci_send(f'open "{target}" type {kind} alias {MCI_ALIAS}'):
            return False
        if _mci_send(f"play {MCI_ALIAS} from 0"):
            _mci_send(f"close {MCI_ALIAS}")
            return False
    return True


def stop_launch_sound():
    with _mci_lock:
        _mci_close()


# ---- кастомизация лаунчера: свой шрифт интерфейса ------------------------------

UI_FONT_PRIVATE = 0x10


def _ui_font_error(message):
    return {"ok": False, "error": str(message), "path": None,
            "name": "", "family": "", "families": []}


def _font_name_table(data):
    """Имена семейства из таблицы name (SFNT/TTF/OTF)."""
    if len(data) < 6:
        return []
    try:
        fmt, count, string_offset = struct.unpack(">HHH", data[:6])
    except struct.error:
        return []
    if fmt not in (0, 1):
        return []
    ranked = {}
    pos = 6
    for _ in range(min(count, 512)):
        if pos + 12 > len(data):
            break
        try:
            (platform, _encoding, language, name_id, length,
             offset) = struct.unpack(">HHHHHH", data[pos:pos + 12])
        except struct.error:
            break
        pos += 12
        if name_id not in (1, 16):
            continue
        start = string_offset + offset
        raw = data[start:start + length]
        if not raw:
            continue
        if platform in (0, 3):
            try:
                text = raw.decode("utf-16-be")
            except UnicodeDecodeError:
                continue
        elif platform == 1:
            text = raw.decode("mac_roman", "ignore")
        else:
            continue
        text = text.replace("\x00", "").strip()
        if not text:
            continue
        rank = (
            0 if platform == 3 and language in (0, 0x409)
            else 1 if platform == 3
            else 2 if platform == 1 else 3,
            0 if name_id == 1 else 1,
        )
        known = {name.casefold() for name in ranked}
        if text.casefold() not in known:
            ranked[text] = rank
    return [name for name, _rank in sorted(ranked.items(),
                                           key=lambda item: item[1])]


def read_font_families(path):
    """Семейства шрифта (сначала основное), [] если файл не TTF/OTF."""
    path = Path(path)
    try:
        if path.stat().st_size > UI_FONT_MAX:
            return []
        with path.open("rb") as fh:
            head = fh.read(12)
            if (len(head) < 12 or head[:4] == b"ttcf"
                    or head[:4] not in (b"\x00\x01\x00\x00", b"OTTO",
                                        b"true", b"typ1")):
                return []
            num_tables = struct.unpack(">H", head[4:6])[0]
            if not (1 <= num_tables <= 100):
                return []
            name_tables = []
            for _ in range(num_tables):
                record = fh.read(16)
                if len(record) < 16:
                    break
                tag, _checksum, offset, length = struct.unpack(
                    ">4sIII", record)
                if tag == b"name":
                    name_tables.append((offset, length))
            for offset, length in name_tables:
                fh.seek(offset)
                data = fh.read(min(length, 1000000))
                names = _font_name_table(data)
                if names:
                    return names
    except (OSError, struct.error):
        return []
    return []


def inspect_ui_font(source):
    source = Path(source)
    ext = source.suffix.lower()
    if not source.is_file():
        return _ui_font_error("файл не найден")
    if ext not in UI_FONT_EXTS:
        return _ui_font_error("нужен шрифт TTF или OTF")
    try:
        size = source.stat().st_size
    except OSError as exc:
        return _ui_font_error(exc)
    if size <= 0:
        return _ui_font_error("файл пустой")
    if size > UI_FONT_MAX:
        return _ui_font_error("шрифт больше 20 МБ")
    families = read_font_families(source)
    if not families:
        return _ui_font_error("не удалось прочитать TTF/OTF")
    return {"ok": True, "error": "", "path": source,
            "name": source.name[:80], "family": families[0],
            "families": families}


def ui_font_path(font_dir=None):
    base = Path(font_dir or UI_FONT_DIR)
    for ext in UI_FONT_EXTS:
        path = base / f"font{ext}"
        if path.is_file():
            return path
    return None


def ui_font_state(font_dir=None):
    path = ui_font_path(font_dir)
    families = read_font_families(path) if path is not None else []
    return {"custom": path is not None, "path": path,
            "family": families[0] if families else "",
            "families": families}


def import_ui_font(source, font_dir=None):
    """Скопировать TTF/OTF в папку лаунчера (атомарно, с заменой)."""
    info = inspect_ui_font(source)
    if not info["ok"]:
        return info
    source = Path(source)
    base = Path(font_dir or UI_FONT_DIR)
    ext = source.suffix.lower()
    dest = base / f"font{ext}"
    temp = base / f"font{ext}.tmp"
    try:
        base.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, temp)
        os.replace(temp, dest)
    except OSError as exc:
        try:
            temp.unlink()
        except OSError:
            pass
        return _ui_font_error(exc)
    for other in UI_FONT_EXTS:
        old = base / f"font{other}"
        if old != dest:
            try:
                old.unlink()
            except OSError:
                pass
    info["path"] = dest
    return info


def remove_ui_font(font_dir=None):
    base = Path(font_dir or UI_FONT_DIR)
    removed = 0
    errors = []
    for ext in UI_FONT_EXTS:
        path = base / f"font{ext}"
        try:
            if path.exists():
                path.unlink()
                removed += 1
        except OSError as exc:
            errors.append(str(exc))
    return {"ok": not errors, "removed": removed,
            "error": errors[0] if errors else ""}


def load_private_font(path):
    """Подключить шрифт только к текущему процессу, без установки."""
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.gdi32.AddFontResourceExW(
            str(Path(path)), UI_FONT_PRIVATE, 0))
    except (AttributeError, OSError):
        return False


def unload_private_font(path):
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.gdi32.RemoveFontResourceExW(
            str(Path(path)), UI_FONT_PRIVATE, 0))
    except (AttributeError, OSError):
        return False


def refresh_shell_icons():
    if os.name != "nt":
        return False
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
        return True
    except (AttributeError, OSError):
        return False


def _run_powershell(script, timeout=30):
    if os.name != "nt":
        return {"ok": False, "returncode": -1, "output": "",
                "error": "только Windows"}
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            capture_output=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "returncode": -1, "output": "",
                "error": str(exc)}
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "output": proc.stdout.decode("utf-8", "replace").strip(),
            "error": proc.stderr.decode("utf-8", "replace").strip()}


def _ps_payload(data):
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _shortcut_roots():
    roots = []

    def add(path):
        if not path:
            return
        try:
            value = Path(os.path.expandvars(str(path)))
        except (OSError, ValueError):
            return
        if value not in roots:
            roots.append(value)

    if winreg is not None:
        try:
            with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion"
                    r"\Explorer\User Shell Folders") as key:
                for name in ("Desktop", "Programs", "Start Menu"):
                    try:
                        value, _kind = winreg.QueryValueEx(key, name)
                    except OSError:
                        continue
                    add(value)
        except OSError:
            pass
    home = Path.home()
    add(home / "Desktop")
    add(home / "OneDrive" / "Desktop")
    public = os.getenv("PUBLIC")
    if public:
        add(Path(public) / "Desktop")
    appdata = os.getenv("APPDATA")
    if appdata:
        add(Path(appdata) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs")
        add(Path(appdata) / "Microsoft" / "Internet Explorer" / "Quick Launch"
            / "User Pinned" / "TaskBar")
    programdata = os.getenv("PROGRAMDATA")
    if programdata:
        add(Path(programdata) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs")
    return roots


_SHORTCUT_FIND_SCRIPT = """\
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$roots = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}')) | ConvertFrom-Json
$found = @()
foreach ($root in @($roots)) {
  if (-not (Test-Path -LiteralPath $root)) { continue }
  Get-ChildItem -LiteralPath $root -Filter *.lnk -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
    $name = $_.BaseName
    if ($name -notmatch '(?i)roblox') { return }
    if ($name -match '(?i)studio|uninstall|deinstall') { return }
    $found += [pscustomobject]@{ path = $_.FullName; name = $name }
  }
}
if ($found.Count -eq 0) { Write-Output '[]' } else { Write-Output (ConvertTo-Json -InputObject $found -Compress -Depth 2) }
"""

_SHORTCUT_ICON_SCRIPT = """\
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$items = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}')) | ConvertFrom-Json
$changed = 0
$errors = @()
$shell = New-Object -ComObject WScript.Shell
foreach ($item in @($items)) {
  try {
    $lnk = $shell.CreateShortcut([string]$item.path)
    $lnk.IconLocation = [string]$item.icon + ',0'
    $lnk.Save()
    $changed++
  } catch {
    $errors += [string]$item.path
  }
}
$shell = $null
[pscustomobject]@{ changed = $changed; errors = @($errors) } | ConvertTo-Json -Compress
"""


def find_roblox_shortcuts(roots=None, timeout=30):
    """Найти ярлыки Roblox на рабочем столе, в «Пуске» и на панели задач."""
    if os.name != "nt":
        return {"ok": False, "items": [], "error": "только Windows"}
    candidates = _shortcut_roots() if roots is None else list(roots)
    existing = [str(Path(path)) for path in candidates
                if Path(path).is_dir()]
    if not existing:
        return {"ok": True, "items": [], "error": ""}
    script = _SHORTCUT_FIND_SCRIPT.replace("{payload}",
                                           _ps_payload(existing))
    result = _run_powershell(script, timeout=timeout)
    if not result["ok"]:
        return {"ok": False, "items": [],
                "error": result["error"] or "PowerShell не сработал"}
    try:
        data = json.loads(result["output"] or "[]")
    except ValueError:
        return {"ok": False, "items": [],
                "error": "не удалось разобрать ответ PowerShell"}
    if isinstance(data, dict):
        data = [data]
    items = []
    seen = set()
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "")
        if not path.lower().endswith(".lnk"):
            continue
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        items.append({"path": path,
                      "name": str(entry.get("name")
                                  or Path(path).stem)})
    return {"ok": True, "items": items, "error": ""}


def _set_shortcuts_icon(entries, timeout=30):
    if not entries:
        return {"ok": True, "changed": 0, "errors": []}
    script = _SHORTCUT_ICON_SCRIPT.replace("{payload}",
                                           _ps_payload(entries))
    result = _run_powershell(script, timeout=timeout)
    if not result["ok"]:
        return {"ok": False, "changed": 0,
                "errors": [result["error"] or "PowerShell не сработал"]}
    try:
        data = json.loads(result["output"] or "{}")
    except ValueError:
        return {"ok": False, "changed": 0,
                "errors": ["не удалось разобрать ответ PowerShell"]}
    if not isinstance(data, dict):
        data = {}
    errors = data.get("errors") or []
    if isinstance(errors, str):
        errors = [errors]
    try:
        changed = int(data.get("changed") or 0)
    except (TypeError, ValueError):
        changed = 0
    return {"ok": not errors, "changed": changed,
            "errors": [str(item) for item in errors]}


def _load_launch_shortcut_state(state_path=None):
    data = read_json_dict(Path(state_path or LAUNCH_SHORTCUT_BACKUP_PATH))
    raw = data.get("items")
    if not isinstance(raw, list):
        return []
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "")
        backup = str(entry.get("backup") or "")
        if not path or not backup:
            continue
        items.append({"path": path, "backup": backup,
                      "name": str(entry.get("name") or Path(path).stem)})
    return items


def _save_launch_shortcut_state(items, state_path=None):
    return write_json_dict(
        Path(state_path or LAUNCH_SHORTCUT_BACKUP_PATH), {"items": items})


def shortcut_backup_count(state_path=None):
    return len(_load_launch_shortcut_state(state_path))


def _shortcut_backup_file(target, backup_dir):
    digest = hashlib.sha1(
        os.path.normcase(str(target)).encode("utf-8")).hexdigest()[:20]
    return Path(backup_dir) / f"{digest}_{Path(target).name}"


def apply_launch_icon_to_shortcuts(roots=None, icon_path=None,
                                   state_path=None, backup_dir=None,
                                   timeout=30):
    """Поставить свою иконку на ярлыки Roblox, сохранив копии оригиналов."""
    icon = Path(icon_path or LAUNCH_ICON_PATH)
    if not icon.is_file():
        return {"ok": False, "found": 0, "changed": 0, "errors": [],
                "error": "сначала добавь свою иконку"}
    found = find_roblox_shortcuts(roots, timeout=timeout)
    if not found["ok"]:
        return {"ok": False, "found": 0, "changed": 0, "errors": [],
                "error": found["error"]}
    items = found["items"]
    if not items:
        return {"ok": False, "found": 0, "changed": 0, "errors": [],
                "error": "ярлыки Roblox не найдены"}
    state_path = Path(state_path or LAUNCH_SHORTCUT_BACKUP_PATH)
    backup_dir = Path(backup_dir or LAUNCH_SHORTCUT_BACKUP_DIR)
    state = _load_launch_shortcut_state(state_path)
    known = {os.path.normcase(item["path"]) for item in state}
    errors = []
    added = 0
    for entry in items:
        key = os.path.normcase(entry["path"])
        if key in known:
            continue
        backup = _shortcut_backup_file(entry["path"], backup_dir)
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry["path"], backup)
        except OSError as exc:
            errors.append(f"{entry['name']}: {exc}")
            continue
        state.append({"path": entry["path"], "backup": str(backup),
                      "name": entry["name"]})
        known.add(key)
        added += 1
    if added:
        ok, err = _save_launch_shortcut_state(state, state_path)
        if not ok:
            errors.append(f"резервная копия: {err}")
    result = _set_shortcuts_icon(
        [{"path": entry["path"], "icon": str(icon)} for entry in items],
        timeout=timeout)
    errors.extend(result["errors"])
    if result["changed"]:
        refresh_shell_icons()
    return {"ok": not errors and result["changed"] > 0,
            "found": len(items), "changed": result["changed"],
            "errors": errors,
            "error": errors[0] if errors else ""}


def restore_launch_icon_shortcuts(state_path=None, backup_dir=None):
    """Вернуть ярлыки из резервных копий и очистить состояние."""
    state_path = Path(state_path or LAUNCH_SHORTCUT_BACKUP_PATH)
    backup_dir = Path(backup_dir or LAUNCH_SHORTCUT_BACKUP_DIR)
    items = _load_launch_shortcut_state(state_path)
    if not items:
        return {"ok": True, "restored": 0, "errors": [], "error": ""}
    errors = []
    restored = 0
    for item in items:
        target = Path(item["path"])
        backup = Path(item["backup"])
        try:
            if not target.parent.is_dir():
                raise OSError("папка ярлыка недоступна")
            if not backup.is_file():
                raise OSError("резервная копия не найдена")
            shutil.copy2(backup, target)
            try:
                backup.unlink()
            except OSError:
                pass
            restored += 1
        except OSError as exc:
            errors.append(f"{target.name}: {exc}")
    if restored:
        refresh_shell_icons()
    if not errors:
        try:
            state_path.unlink()
        except OSError:
            _save_launch_shortcut_state([], state_path)
        try:
            if backup_dir.is_dir() and not any(backup_dir.iterdir()):
                backup_dir.rmdir()
        except OSError:
            pass
    else:
        _save_launch_shortcut_state(
            [item for item in items if Path(item["backup"]).exists()],
            state_path)
    return {"ok": not errors, "restored": restored, "errors": errors,
            "error": errors[0] if errors else ""}


# ---- аккаунты Roblox ---------------------------------------------------------

ROBLOX_COOKIE_NAME = ".ROBLOSECURITY"
ROBLOX_AUTHENTICATED_URL = "https://users.roblox.com/v1/users/authenticated"
ROBLOX_TICKET_URL = "https://auth.roblox.com/v1/authentication-ticket"
ROBLOX_AVATAR_URL = ("https://thumbnails.roblox.com/v1/users/"
                     "avatar-headshot")

_AUTH_BASE_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": f"Mozilla/5.0 ({APP_NAME}/{VERSION})",
    "Referer": "https://www.roblox.com/",
    "Origin": "https://www.roblox.com",
}


def _http_request(url, method="GET", headers=None, body=None, cookie="",
                  timeout=15):
    """(status, headers, body). Сетевые ошибки — status 0."""
    hdrs = dict(HTTP_HEADERS)
    hdrs.update(headers or {})
    if cookie:
        hdrs["Cookie"] = f"{ROBLOX_COOKIE_NAME}={cookie}"
    request = urllib.request.Request(url, data=body, headers=hdrs,
                                     method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.headers, exc.read()
        except Exception:
            return exc.code, exc.headers, b""
    except Exception:
        return 0, {}, b""


def _json_body(body):
    try:
        data = json.loads(body.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else {}
    except (ValueError, AttributeError):
        return {}


def verify_roblox_cookie(cookie, timeout=15):
    """Проверить сессию и получить id/имя аккаунта."""
    if not cookie:
        return {"ok": False, "error": "пустая сессия"}
    status, _headers, body = _http_request(
        ROBLOX_AUTHENTICATED_URL, headers={"Accept": "application/json"},
        cookie=cookie, timeout=timeout)
    if status != 200:
        return {"ok": False,
                "error": "Сессия Roblox недействительна — войди заново."}
    data = _json_body(body)
    user_id = str(data.get("id") or "")
    name = str(data.get("name") or "")
    if not user_id or not name:
        return {"ok": False, "error": "Roblox не вернул данные аккаунта."}
    return {"ok": True, "id": user_id, "name": name,
            "display": str(data.get("displayName") or name)}


def _post_with_csrf(url, payload, cookie="", timeout=20):
    headers = dict(_AUTH_BASE_HEADERS)
    status, resp_headers, body = _http_request(
        url, "POST", headers, payload, cookie, timeout)
    if status in (401, 403):
        try:
            token = resp_headers.get("x-csrf-token")
        except AttributeError:
            token = ""
        if token:
            headers["X-CSRF-TOKEN"] = token
            status, resp_headers, body = _http_request(
                url, "POST", headers, payload, cookie, timeout)
    return status, resp_headers, body


def roblox_auth_ticket(cookie, timeout=20):
    """Одноразовый тикет для запуска игры под аккаунтом."""
    if not cookie:
        return {"ok": False, "error": "Нет сессии аккаунта."}
    status, headers, body = _post_with_csrf(
        ROBLOX_TICKET_URL, b"{}", cookie, timeout)
    try:
        ticket = headers.get("rbx-authentication-ticket") or ""
    except AttributeError:
        ticket = ""
    if status in (200, 204) and ticket:
        return {"ok": True, "ticket": ticket, "error": ""}
    if status in (401, 403):
        return {"ok": False,
                "error": "Сессия аккаунта истекла — войди заново."}
    data = _json_body(body)
    detail = ""
    for err in data.get("errors") or []:
        if isinstance(err, dict):
            detail = str(err.get("userFacingMessage")
                         or err.get("message") or "")
            if detail:
                break
    return {"ok": False,
            "error": f"Roblox не выдал тикет входа. {detail}".strip()}


def game_launch_url(place_id, ticket):
    """Ссылка roblox-player с тикетом аккаунта (старый клиент)."""
    tracker = random.randint(1000000000, 9999999999)
    launcher = ("https://assetgame.roblox.com/game/PlaceLauncher.ashx"
                f"?request=RequestGame&browserTrackerId={tracker}"
                f"&placeId={place_id}&isPlayTogetherGame=false")
    return ("roblox-player:1+launchmode:play+gameinfo:" + str(ticket)
            + "+launchtime:" + str(int(time.time() * 1000))
            + "+placelauncherurl:" + urllib.parse.quote(launcher, safe="")
            + "+browsertrackerid:" + str(tracker)
            + "+robloxLocale:ru_ru+gameLocale:ru_ru"
            + "+channel:+LaunchExp:InApp")


def roblox_deep_link(place_id):
    """Ссылка, которую понимает и новый клиент Roblox (Unified/WebView2)."""
    return f"roblox://experiences/start?placeId={place_id}"


def roblox_cookie_store_path():
    """Файл сессии, из которого новый клиент берёт .ROBLOSECURITY."""
    local = os.getenv("LOCALAPPDATA")
    if not local:
        return None
    return Path(local) / "Roblox" / "LocalStorage" / "RobloxCookies.dat"


def _cookie_entry_name(entry):
    fields = entry.split("\t")
    if len(fields) >= 7:
        return fields[5].strip()
    if "=" in entry and "\t" not in entry:
        return entry.split("=", 1)[0].strip()
    return ""


def _cookie_entry_value(entry):
    fields = entry.split("\t")
    if len(fields) >= 7:
        return fields[6].strip()
    if "=" in entry and "\t" not in entry:
        return entry.split("=", 1)[1].strip()
    return ""


def read_roblox_cookie_store(path=None):
    """Разобрать хранилище сессии клиента. None — файла нет или он битый."""
    target = path if path is not None else roblox_cookie_store_path()
    if target is None:
        return None
    target = Path(target)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    encoded = raw.get("CookiesData")
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        blob = base64.b64decode(encoded)
    except ValueError:
        return None
    plain = _dpapi_call(blob, False)
    if not plain:
        return None
    try:
        text = plain.decode("utf-8")
    except UnicodeDecodeError:
        return None
    entries = [part for part in text.split("; ") if part.strip()]
    if not entries:
        return None
    cookie = ""
    for entry in entries:
        if _cookie_entry_name(entry) == ROBLOX_COOKIE_NAME:
            cookie = _cookie_entry_value(entry)
            break
    return {"path": target, "entries": entries, "cookie": cookie, "raw": raw}


def set_roblox_session(cookie, path=None):
    """Записать сессию аккаунта в хранилище нового клиента Roblox.

    Клиент 0.744+ при запуске читает RobloxCookies.dat, поэтому подмена
    .ROBLOSECURITY + обычная ссылка roblox:// запускают игру под нужным
    аккаунтом без устаревшего roblox-player с PlaceLauncher.
    """
    cookie = str(cookie or "").strip()
    if not cookie:
        return {"ok": False, "changed": 0, "error": "пустая сессия"}
    store = read_roblox_cookie_store(path)
    if store is None:
        return {"ok": False, "changed": 0,
                "error": "хранилище сессии Roblox не найдено"}
    target = store["path"]
    if store["cookie"] == cookie:
        return {"ok": True, "changed": 0, "error": ""}
    entries = []
    replaced = False
    for entry in store["entries"]:
        if _cookie_entry_name(entry) == ROBLOX_COOKIE_NAME:
            fields = entry.split("\t")
            if len(fields) >= 7:
                fields[6] = cookie
                entry = "\t".join(fields)
            else:
                entry = f"{ROBLOX_COOKIE_NAME}={cookie}"
            replaced = True
        entries.append(entry)
    if not replaced:
        entries.insert(0, "#HttpOnly_.roblox.com\tTRUE\t/\tTRUE\t0\t"
                          f"{ROBLOX_COOKIE_NAME}\t{cookie}")
    blob = _dpapi_call("; ".join(entries).encode("utf-8"), True)
    if not blob:
        return {"ok": False, "changed": 0,
                "error": "Windows не зашифровал сессию"}
    data = dict(store["raw"])
    data["CookiesData"] = base64.b64encode(blob).decode("ascii")
    data.setdefault("CookiesVersion", "1")
    payload = json.dumps(data, separators=(",", ":"))
    for _ in range(2):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = target.with_name(target.name + ".maklyxa.bak")
            if not backup.exists():
                shutil.copy2(target, backup)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, target)
        except OSError as exc:
            return {"ok": False, "changed": 0, "error": str(exc)}
        written = read_roblox_cookie_store(target)
        if written is not None and written["cookie"] == cookie:
            return {"ok": True, "changed": 1, "error": ""}
        time.sleep(0.25)
    return {"ok": False, "changed": 0,
            "error": "Клиент Roblox перезаписал сессию — попробуй ещё раз"}


ROBLOX_CLIENT_START_WAIT = 20
ROBLOX_JOIN_WAIT = 45
ROBLOX_LAUNCH_TRIES = 3


def wait_for_new_players(before, seconds, settle=1.5):
    """True — после before появился и живёт процесс игрового клиента."""
    before = set(before or ())
    seen = set()
    deadline = time.time() + max(0.0, seconds)
    while time.time() < deadline:
        time.sleep(0.5)
        current = player_pids()
        seen |= current - before
        if seen and current & seen:
            if settle:
                time.sleep(settle)
            if player_pids() & seen:
                return True
    return False


def roblox_store_ready(path=None, quiet=0.8, timeout=10):
    """Дождаться, пока клиент допишет хранилище сессии."""
    target = path if path is not None else roblox_cookie_store_path()
    if target is None:
        return True
    target = Path(target)
    stamp = None
    deadline = time.time() + timeout
    while True:
        try:
            current = target.stat().st_mtime_ns
        except OSError:
            return True
        if stamp == current:
            return True
        stamp = current
        if time.time() >= deadline:
            return False
        time.sleep(quiet)


def roblox_log_dir():
    """Папка журналов игрового клиента."""
    local = os.getenv("LOCALAPPDATA")
    if not local:
        return None
    folder = Path(local) / "Roblox" / "logs"
    return folder if folder.is_dir() else None


ROBLOX_APP_THEMES = {"dark": "Тёмная", "light": "Светлая"}


def roblox_app_storage_path():
    """appStorage.json клиента Roblox — там лежит тема приложения."""
    local = os.getenv("LOCALAPPDATA")
    if not local:
        return None
    return Path(local) / "Roblox" / "LocalStorage" / "appStorage.json"


def _read_app_storage(path):
    """(dict, исходный текст) или (None, '')."""
    try:
        text = Path(path).read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, ValueError):
        return None, ""
    return (data if isinstance(data, dict) else None), text


def _device_themes(data):
    """Разбор DeviceLevelTheme: {user_id: 'dark'|'light'}."""
    raw = data.get("DeviceLevelTheme")
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value).lower() for key, value in parsed.items()}


def roblox_theme_state(path=None):
    """Текущая тема клиента Roblox (тёмная/светлая)."""
    target = Path(path) if path is not None else roblox_app_storage_path()
    if target is None:
        return {"ok": False, "error": "не найдена папка Roblox",
                "theme": "", "device": {}, "path": None}
    data, _text = _read_app_storage(target)
    if data is None:
        return {"ok": False, "error": "appStorage.json ещё нет — "
                                      "запусти Roblox один раз",
                "theme": "", "device": {}, "path": target}
    device = _device_themes(data)
    auth = str(data.get("AuthenticatedTheme") or "").lower()
    theme = auth if auth in ROBLOX_APP_THEMES else ""
    if not theme and device:
        counts = {}
        for value in device.values():
            counts[value] = counts.get(value, 0) + 1
        theme = max(counts, key=counts.get)
    return {"ok": True, "theme": theme, "auth": auth, "device": device,
            "path": target, "error": ""}


def set_roblox_theme(theme, path=None):
    """Сменить тему клиента Roblox (только при закрытом клиенте).

    Пишет AuthenticatedTheme и DeviceLevelTheme в appStorage.json —
    официальный файл настроек приложения Roblox. Перед первой записью
    рядом остаётся резервная копия *.maklyxa.bak.
    """
    theme = str(theme or "").lower()
    if theme not in ROBLOX_APP_THEMES:
        return {"ok": False, "error": "неизвестная тема"}
    if player_pids():
        return {"ok": False,
                "error": "Roblox запущен — закрой клиент и повтори"}
    target = Path(path) if path is not None else roblox_app_storage_path()
    if target is None:
        return {"ok": False, "error": "не найдена папка Roblox"}
    data, text = _read_app_storage(target)
    if data is None:
        return {"ok": False, "error": "appStorage.json ещё нет — "
                                      "запусти Roblox один раз"}
    device = _device_themes(data)
    same = (str(data.get("AuthenticatedTheme") or "").lower() == theme
            and (not device or all(v == theme for v in device.values())))
    backup = target.with_name(target.name + ".maklyxa.bak")
    if same:
        return {"ok": True, "theme": theme, "changed": False,
                "backup": backup if backup.is_file() else None, "error": ""}
    try:
        if not backup.is_file():
            shutil.copy2(target, backup)
        data["AuthenticatedTheme"] = theme
        if device:
            data["DeviceLevelTheme"] = json.dumps(
                {key: theme for key in device},
                ensure_ascii=False, separators=(",", ":"))
        data["DeviceLevelThemeSnapshotTimestamp"] = str(int(time.time()))
        compact = "\n" not in text
        out = json.dumps(data, ensure_ascii=False,
                         separators=(",", ":") if compact else None,
                         indent=None if compact else 2)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(out, encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "theme": theme, "changed": True,
            "backup": backup, "error": ""}


def _log_line_epoch(line):
    """Время строки журнала Roblox (UTC) в epoch; None — не строка журнала."""
    if len(line) < 24 or line[4] != "-" or line[10] != "T":
        return None
    try:
        stamp = datetime.strptime(
            line[:24], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return stamp.timestamp()


def _join_outcome(mark):
    """'joined' | 'failed' | None — по свежим журналам клиента."""
    folder = roblox_log_dir()
    if folder is None:
        return None
    floor = mark - 3.0
    joined = failed = False
    try:
        files = [item for item in folder.glob("*.log")
                 if item.stat().st_mtime >= floor - 60]
    except OSError:
        return None
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if ("game_join_loadtime" not in line
                    and "join-game" not in line):
                continue
            stamp = _log_line_epoch(line)
            if stamp is None or stamp < floor:
                continue
            if "game_join_loadtime" in line:
                joined = True
            elif "HttpError" in line or "Failed to connect" in line:
                failed = True
    if joined:
        return "joined"
    return "failed" if failed else None


def wait_for_join(mark, seconds=ROBLOX_JOIN_WAIT, poll=1.5):
    """'joined' | 'failed' | 'timeout' | 'unknown'."""
    if roblox_log_dir() is None:
        return "unknown"
    deadline = time.time() + max(0.0, seconds)
    while True:
        outcome = _join_outcome(mark)
        if outcome:
            return outcome
        if time.time() >= deadline:
            return "timeout"
        time.sleep(poll)


def _drive_launch(url, tries=ROBLOX_LAUNCH_TRIES,
                  wait_client=ROBLOX_CLIENT_START_WAIT,
                  wait_join=ROBLOX_JOIN_WAIT):
    """Отправить клиенту ссылку и дождаться входа в игру.

    Клиент нового поколения после закрытия окна остаётся в трее и держит
    одиночный запуск, но ссылку на игру из трея не принимает. Такой процесс
    закрывается перед запуском — иначе игра не откроется без диспетчера
    задач. Ссылка повторяется и когда клиент не успел подключиться
    к gamejoin.
    """
    notes = []
    stale = player_pids()
    if stale and roblox_window_pids(stale):
        # клиент открыт на экране — ссылку примет его окно
        try:
            os.startfile(url)
        except OSError as exc:
            return {"ok": False, "attempts": 1, "notes": notes,
                    "error": f"Не удалось открыть ссылку Roblox: {exc}"}
        return {"ok": True, "attempts": 1, "notes": notes, "error": ""}
    if stale:
        notes.append("Клиент Roblox висел в трее — закрываю и запускаю заново")
        close_roblox_players()
        wait_players_gone(10)
        time.sleep(1.0)
    for attempt in range(1, max(1, tries) + 1):
        before = player_pids()
        mark = time.time()
        try:
            os.startfile(url)
        except OSError as exc:
            return {"ok": False, "attempts": attempt, "notes": notes,
                    "error": f"Не удалось открыть ссылку Roblox: {exc}"}
        if not before and not wait_for_new_players(before, wait_client):
            notes.append(f"Попытка {attempt}: клиент не запустился")
        else:
            outcome = wait_for_join(mark, wait_join)
            if outcome in ("joined", "unknown"):
                return {"ok": True, "attempts": attempt, "notes": notes,
                        "error": ""}
            notes.append(f"Попытка {attempt}: вход в игру не удался")
        if attempt < tries:
            time.sleep(1.0)
    return {"ok": False, "attempts": max(1, tries), "notes": notes,
            "error": "Roblox не открыл игру. Нажми «Играть» ещё раз — "
                     "зависший клиент лаунчер закроет сам."}


def _drive_exe_launch(exe, url, tries=ROBLOX_LAUNCH_TRIES,
                      wait_client=ROBLOX_CLIENT_START_WAIT,
                      wait_join=ROBLOX_JOIN_WAIT):
    """Запустить выбранную версию клиента напрямую и дождаться входа.

    Ссылка передаётся exe аргументом — так же, как бутстраперы передают
    её обычному клиенту. Открытый на экране клиент не закрываем: ссылка
    уходит ему, а выбранная версия применится после перезапуска.
    """
    notes = []
    target = Path(exe)
    if not target.is_file():
        return {"ok": False, "attempts": 0, "notes": notes,
                "error": "Выбранная версия Roblox не найдена на диске."}
    stale = player_pids()
    if stale and roblox_window_pids(stale):
        notes.append("Roblox уже открыт — ссылка передана ему, выбранная "
                     "версия применится после закрытия клиента")
        try:
            os.startfile(url)
        except OSError as exc:
            return {"ok": False, "attempts": 1, "notes": notes,
                    "error": f"Не удалось открыть ссылку Roblox: {exc}"}
        return {"ok": True, "attempts": 1, "notes": notes, "error": ""}
    if stale:
        notes.append("Клиент Roblox висел в трее — закрываю и запускаю "
                     "выбранную версию")
        close_roblox_players()
        wait_players_gone(10)
        time.sleep(1.0)
    for attempt in range(1, max(1, tries) + 1):
        before = player_pids()
        mark = time.time()
        try:
            subprocess.Popen([str(target), url], cwd=str(target.parent))
        except OSError as exc:
            return {"ok": False, "attempts": attempt, "notes": notes,
                    "error": f"Не удалось запустить выбранную версию: {exc}"}
        if not before and not wait_for_new_players(before, wait_client):
            notes.append(f"Попытка {attempt}: клиент не запустился")
        else:
            outcome = wait_for_join(mark, wait_join)
            if outcome in ("joined", "unknown"):
                return {"ok": True, "attempts": attempt, "notes": notes,
                        "error": ""}
            notes.append(f"Попытка {attempt}: вход в игру не удался")
        if attempt < tries:
            time.sleep(1.0)
    return {"ok": False, "attempts": max(1, tries), "notes": notes,
            "error": "Выбранная версия Roblox не открыла игру. Верни «Авто» "
                     "в строке версии или нажми «Играть» ещё раз."}


def launch_game(place_id, player_exe=None):
    """Открыть игру ссылкой клиента (или выбранной версией напрямую)."""
    url = roblox_deep_link(place_id)
    if player_exe is not None:
        return _drive_exe_launch(player_exe, url)
    return _drive_launch(url)


def fetch_roblox_avatar(user_id, timeout=12):
    """PNG-аватар аккаунта (bytes) или None."""
    if not str(user_id or "").isdigit():
        return None
    status, _headers, body = _http_request(
        ROBLOX_AVATAR_URL + f"?userIds={user_id}&size=150x150"
        "&format=Png&isCircular=true",
        headers={"Accept": "application/json"}, timeout=timeout)
    if status != 200:
        return None
    data = _json_body(body)
    rows = data.get("data") or []
    if not rows or not rows[0].get("imageUrl"):
        return None
    status, _headers, image = _http_request(
        rows[0]["imageUrl"], headers={"Accept": "image/png"},
        timeout=timeout)
    if status == 200 and image.startswith(PNG_SIGNATURE):
        return image
    return None


def _dpapi_call(data, protect):
    if sys.platform != "win32":
        return None
    try:
        from ctypes import wintypes

        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        function = (crypt32.CryptProtectData if protect
                    else crypt32.CryptUnprotectData)
        function.argtypes = [
            ctypes.POINTER(DataBlob), wintypes.LPCWSTR,
            ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(DataBlob)]
        function.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        buffer = ctypes.create_string_buffer(data, len(data))
        blob_in = DataBlob(len(data),
                           ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = DataBlob()
        if not function(ctypes.byref(blob_in), None, None, None, None, 0,
                        ctypes.byref(blob_out)):
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def protect_secret(text):
    """DPAPI-шифрование секрета. (строка, ошибка) — ошибка пустая при успехе."""
    raw = str(text or "").encode("utf-8")
    if not raw:
        return "", "пустой секрет"
    blob = _dpapi_call(raw, True)
    if blob:
        return "dpapi:" + base64.b64encode(blob).decode("ascii"), ""
    if sys.platform == "win32":
        return "", "Не удалось зашифровать сессию Windows."
    return "b64:" + base64.b64encode(raw).decode("ascii"), ""


def unprotect_secret(value):
    value = str(value or "")
    try:
        if value.startswith("dpapi:"):
            blob = base64.b64decode(value[6:])
            raw = _dpapi_call(blob, False)
            return raw.decode("utf-8") if raw else ""
        if value.startswith("b64:"):
            return base64.b64decode(value[4:]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        pass
    return ""


def _empty_accounts():
    return {"active": "", "accounts": []}


def _normalize_accounts(raw):
    data = _empty_accounts()
    if not isinstance(raw, dict):
        return data
    accounts, seen = [], set()
    for item in raw.get("accounts") or []:
        if not isinstance(item, dict):
            continue
        account_id = str(item.get("id") or "")[:64]
        user_id = str(item.get("user_id") or "")
        session = str(item.get("session") or "")
        if not account_id or account_id in seen or not user_id.isdigit():
            continue
        if not session:
            continue
        seen.add(account_id)
        try:
            added = int(item.get("added") or 0)
            used = int(item.get("last_used") or 0)
        except (TypeError, ValueError):
            added = used = 0
        accounts.append({
            "id": account_id,
            "user_id": user_id,
            "name": str(item.get("name") or "")[:64],
            "display": str(item.get("display") or "")[:64],
            "session": session,
            "added": added,
            "last_used": used,
        })
    data["accounts"] = accounts
    active = str(raw.get("active") or "")
    if active not in {item["id"] for item in accounts}:
        active = accounts[0]["id"] if accounts else ""
    data["active"] = active
    return data


def load_accounts(path=None):
    raw = read_json_dict(Path(path or ACCOUNTS_PATH))
    return _normalize_accounts(raw)


def save_accounts(data, path=None):
    return write_json_dict(Path(path or ACCOUNTS_PATH),
                           _normalize_accounts(data))[0]


def _account_meta(account):
    return {"id": account["id"], "user_id": account["user_id"],
            "name": account["name"], "display": account["display"],
            "added": account["added"], "last_used": account["last_used"]}


def accounts_state(path=None):
    data = load_accounts(path)
    return {"active": data["active"],
            "accounts": [_account_meta(item) for item in data["accounts"]]}


def account_avatar_path(user_id):
    try:
        path = Path(AVATARS_DIR) / f"{int(user_id)}.png"
    except (TypeError, ValueError):
        return None
    return path if path.is_file() else None


def _save_account_avatar(user_id, avatar_bytes):
    if not avatar_bytes or not avatar_bytes.startswith(PNG_SIGNATURE):
        return None
    try:
        Path(AVATARS_DIR).mkdir(parents=True, exist_ok=True)
        path = Path(AVATARS_DIR) / f"{user_id}.png"
        path.write_bytes(avatar_bytes)
        return path
    except OSError:
        return None


def add_account(cookie, profile=None, path=None, fetch_avatar=True):
    cookie = str(cookie or "").strip().strip('"')
    cookie = re.sub(r"^\.?ROBLOSECURITY=", "", cookie, flags=re.I).strip()
    if not cookie:
        return {"ok": False, "error": "Нет сессии Roblox."}
    if profile is None:
        profile = verify_roblox_cookie(cookie)
    if not profile.get("ok"):
        return {"ok": False, "error": profile.get("error")
                or "Не удалось проверить аккаунт."}
    session, error = protect_secret(cookie)
    if error:
        return {"ok": False, "error": error}
    data = load_accounts(path)
    now = int(time.time())
    existing = next((item for item in data["accounts"]
                     if item["user_id"] == str(profile["id"])), None)
    if existing is not None:
        existing["session"] = session
        existing["name"] = str(profile.get("name") or existing["name"])[:64]
        existing["display"] = str(profile.get("display")
                                   or existing["display"])[:64]
        existing["last_used"] = now
        account_id = existing["id"]
    else:
        account_id = uuid.uuid4().hex
        data["accounts"].append({
            "id": account_id,
            "user_id": str(profile["id"]),
            "name": str(profile.get("name") or "")[:64],
            "display": str(profile.get("display")
                           or profile.get("name") or "")[:64],
            "session": session,
            "added": now,
            "last_used": now,
        })
    if not data["active"]:
        data["active"] = account_id
    if not save_accounts(data, path):
        return {"ok": False, "error": "Не удалось сохранить аккаунт."}
    if fetch_avatar:
        avatar = fetch_roblox_avatar(profile["id"])
        if avatar:
            _save_account_avatar(profile["id"], avatar)
    account = next(item for item in data["accounts"]
                   if item["id"] == account_id)
    return {"ok": True, "error": "", "account": _account_meta(account),
            "active": data["active"]}


def set_active_account(account_id, path=None):
    data = load_accounts(path)
    if account_id not in {item["id"] for item in data["accounts"]}:
        return {"ok": False, "error": "Аккаунт не найден."}
    data["active"] = str(account_id)
    if not save_accounts(data, path):
        return {"ok": False, "error": "Не удалось сохранить выбор."}
    return {"ok": True, "error": ""}


def remove_account(account_id, path=None):
    data = load_accounts(path)
    account = next((item for item in data["accounts"]
                    if item["id"] == account_id), None)
    if account is None:
        return {"ok": False, "error": "Аккаунт не найден."}
    data["accounts"] = [item for item in data["accounts"]
                        if item["id"] != account_id]
    if data["active"] == account_id:
        data["active"] = (data["accounts"][0]["id"]
                          if data["accounts"] else "")
    if not save_accounts(data, path):
        return {"ok": False, "error": "Не удалось удалить аккаунт."}
    if not any(item["user_id"] == account["user_id"]
               for item in data["accounts"]):
        try:
            avatar = account_avatar_path(account["user_id"])
            if avatar is not None:
                avatar.unlink()
        except OSError:
            pass
    return {"ok": True, "error": ""}


def account_session(account_id, path=None):
    data = load_accounts(path)
    account = next((item for item in data["accounts"]
                    if item["id"] == account_id), None)
    return unprotect_secret(account["session"]) if account else ""


def _touch_account(account_id, path=None):
    data = load_accounts(path)
    account = next((item for item in data["accounts"]
                    if item["id"] == account_id), None)
    if account is None:
        return
    account["last_used"] = int(time.time())
    save_accounts(data, path)


def launch_game_as(place_id, account_id=None, accounts_path=None,
                   player_exe=None):
    """Запустить игру под выбранным аккаунтом.

    Новый клиент Roblox (0.744+, WebView2) сам выполняет вход в игру и не
    принимает старую ссылку roblox-player с PlaceLauncher. Для него сессия
    аккаунта кладётся в RobloxCookies.dat, после чего открывается обычная
    ссылка roblox://. Старый клиент запускается прежним способом.
    Запуск повторяется, пока клиент не появится и не войдёт в игру.
    player_exe — выбранная в лаунчере версия: она запускается напрямую.
    """
    data = load_accounts(accounts_path)
    account = None
    if account_id:
        account = next((item for item in data["accounts"]
                        if item["id"] == account_id), None)
    if account is None:
        account = next((item for item in data["accounts"]
                        if item["id"] == data["active"]), None)
    if account is None and data["accounts"]:
        account = data["accounts"][0]
    if account is None:
        return {"ok": False, "error": "Нет добавленных аккаунтов."}
    cookie = unprotect_secret(account["session"])
    if not cookie:
        return {"ok": False, "account": _account_meta(account),
                "error": "Сессия аккаунта повреждена — войди заново."}
    check = verify_roblox_cookie(cookie)
    if not check["ok"]:
        return {"ok": False, "account": _account_meta(account),
                "error": check["error"]}
    roblox_store_ready()
    if player_pids():
        # клиент в трее держит сессию в памяти — без перезапуска
        # подмена .ROBLOSECURITY не даст нужный аккаунт
        close_roblox_players()
        wait_players_gone(12)
        time.sleep(1.0)
    session = set_roblox_session(cookie)
    if session["ok"]:
        if player_exe is not None:
            driven = _drive_exe_launch(player_exe,
                                       roblox_deep_link(place_id))
        else:
            driven = _drive_launch(roblox_deep_link(place_id))
        if driven["ok"]:
            _touch_account(account["id"], accounts_path)
            return {"ok": True, "error": "",
                    "mode": "exe" if player_exe is not None else "app",
                    "attempts": driven["attempts"],
                    "notes": driven["notes"],
                    "account": _account_meta(account)}
        return {"ok": False, "account": _account_meta(account),
                "notes": driven["notes"], "error": driven["error"]}
    ticket = roblox_auth_ticket(cookie)
    if not ticket["ok"]:
        return {"ok": False, "account": _account_meta(account),
                "error": ticket["error"]}
    url = game_launch_url(place_id, ticket["ticket"])
    if player_exe is not None:
        driven = _drive_exe_launch(player_exe, url)
        if driven["ok"]:
            _touch_account(account["id"], accounts_path)
            return {"ok": True, "error": "", "mode": "exe",
                    "attempts": driven["attempts"],
                    "notes": driven["notes"],
                    "account": _account_meta(account)}
        return {"ok": False, "account": _account_meta(account),
                "notes": driven["notes"], "error": driven["error"]}
    try:
        os.startfile(url)
    except OSError as exc:
        return {"ok": False, "account": _account_meta(account),
                "error": f"Не удалось открыть ссылку Roblox: {exc}"}
    _touch_account(account["id"], accounts_path)
    return {"ok": True, "error": "", "mode": "legacy", "attempts": 1,
            "notes": [], "account": _account_meta(account)}


def close_roblox_players():
    """Закрыть игровые процессы Roblox (включая зависший трей).

    Вызывать только после согласия: игра может быть открыта.
    """
    try:
        subprocess.run(["taskkill", "/IM", "RobloxPlayerBeta.exe",
                        "/F", "/T"],
                       capture_output=True, timeout=15,
                       creationflags=CREATE_NO_WINDOW)
        return True
    except Exception:
        return False


# ---- вход через браузер (Edge/Chrome + CDP) ----------------------------------

def find_browser_exe():
    local = os.getenv("LOCALAPPDATA", "")
    roots = []
    for env in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432"):
        base = os.getenv(env)
        if base:
            roots.append(Path(base) / "Microsoft" / "Edge" / "Application")
    if local:
        roots.append(Path(local) / "Microsoft" / "Edge" / "Application")
    for env in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432"):
        base = os.getenv(env)
        if base:
            roots.append(Path(base) / "Google" / "Chrome" / "Application")
    if local:
        roots.append(Path(local) / "Google" / "Chrome" / "Application")
    for root in roots:
        for name in ("msedge.exe", "chrome.exe"):
            candidate = root / name
            if candidate.is_file():
                return candidate
    return None


def _free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def _cdp_json(url, timeout=1.5):
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


class _WebSocket:
    """Минимальный WebSocket-клиент для локального DevTools (ws://)."""

    def __init__(self, url, timeout=5.0):
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("DevTools закрыл соединение")
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise OSError("DevTools не принял WebSocket")
        self._id = 0
        self._buffer = response.split(b"\r\n\r\n", 1)[1]

    def _read_exact(self, size):
        data = self._buffer
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise OSError("DevTools закрыл соединение")
            data += chunk
        self._buffer = data[size:]
        return data[:size]

    def _send_frame(self, opcode, payload):
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[index % 4]
                       for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _recv_message(self):
        while True:
            first, second = self._read_exact(2)
            opcode = first & 0x0F
            fin = bool(first & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            if second & 0x80:
                mask = self._read_exact(4)
                payload = bytes(byte ^ mask[index % 4]
                                for index, byte in enumerate(
                                    self._read_exact(length)))
            else:
                payload = self._read_exact(length)
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0x8:
                raise OSError("DevTools закрыл соединение")
            if opcode in (0x0, 0x1):
                if not fin:
                    parts = [payload]
                    while True:
                        first2, second2 = self._read_exact(2)
                        length2 = second2 & 0x7F
                        if length2 == 126:
                            length2 = struct.unpack(
                                ">H", self._read_exact(2))[0]
                        elif length2 == 127:
                            length2 = struct.unpack(
                                ">Q", self._read_exact(8))[0]
                        part = self._read_exact(length2)
                        parts.append(part)
                        if first2 & 0x80:
                            payload = b"".join(parts)
                            break
                try:
                    return json.loads(payload.decode("utf-8", "replace"))
                except ValueError:
                    continue

    def call(self, method, params=None, timeout=8.0):
        self._id += 1
        call_id = self._id
        self._send_frame(0x1, json.dumps({
            "id": call_id, "method": method, "params": params or {},
        }).encode("utf-8"))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.sock.settimeout(max(0.2, deadline - time.time()))
            message = self._recv_message()
            if message and message.get("id") == call_id:
                return message
        return {}

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def _browser_cookies(ws):
    response = ws.call("Network.getAllCookies", timeout=6.0)
    result = response.get("result") or {}
    cookies = result.get("cookies") or []
    for item in cookies:
        if not isinstance(item, dict):
            continue
        if str(item.get("name")) != ROBLOX_COOKIE_NAME:
            continue
        domain = str(item.get("domain") or "").lower()
        if "roblox.com" in domain and item.get("value"):
            return str(item["value"])
    return ""


def _stop_browser(proc, browser_ws_url=""):
    if browser_ws_url:
        try:
            ws = _WebSocket(browser_ws_url, timeout=3.0)
            try:
                ws.call("Browser.close", timeout=3.0)
            finally:
                ws.close()
        except Exception:
            pass
    try:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            time.sleep(0.5)
        if proc is not None and proc.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
                creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass


def _cleanup_browser_profile(profile_dir):
    path = Path(profile_dir)
    for _ in range(6):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.5)


def browser_login(timeout=300, cancel_event=None, status_cb=None,
                  profile_dir=None):
    """Вход в Roblox в отдельном окне Edge/Chrome.

    Лаунчер запускает браузер с временным профилем и читает cookie
    через локальный DevTools. Основной профиль браузера не затрагивается.
    """
    def emit(text):
        if status_cb is not None:
            try:
                status_cb(text)
            except Exception:
                pass

    exe = find_browser_exe()
    if exe is None:
        return {"ok": False, "cancelled": False,
                "error": "Edge или Chrome не найден — установи браузер, "
                         "чтобы войти через сайт Roblox."}
    profile = Path(profile_dir or BROWSER_PROFILE_DIR)
    _cleanup_browser_profile(profile)
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "cancelled": False, "error": str(exc)}

    port = _free_port()
    args = [
        str(exe),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-sync", "--disable-extensions",
        "--remote-allow-origins=*",
        "--app=https://www.roblox.com/login",
    ]
    proc = None
    ws = None
    browser_ws_url = ""
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        emit("Открыл окно браузера — войди в Roblox.")
        deadline = time.time() + timeout
        ready_deadline = time.time() + 25
        page_ws_url = ""
        while time.time() < ready_deadline:
            if cancel_event is not None and cancel_event.is_set():
                return {"ok": False, "cancelled": True, "error": "Отменено."}
            try:
                version = _cdp_json(f"http://127.0.0.1:{port}/json/version")
                browser_ws_url = str(version.get("webSocketDebuggerUrl") or "")
                break
            except Exception:
                time.sleep(0.5)
        if not browser_ws_url:
            return {"ok": False, "cancelled": False,
                    "error": "Браузер не ответил. Попробуй открыть вход "
                             "через сайт ещё раз."}

        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                return {"ok": False, "cancelled": True, "error": "Отменено."}
            if ws is None:
                try:
                    targets = _cdp_json(
                        f"http://127.0.0.1:{port}/json/list", timeout=2.0)
                except Exception:
                    targets = []
                pages = [item for item in targets
                         if item.get("type") == "page"
                         and item.get("webSocketDebuggerUrl")]
                pages.sort(key=lambda item: "roblox.com" not in str(
                    item.get("url") or ""))
                if pages:
                    page_ws_url = str(pages[0]["webSocketDebuggerUrl"])
                    try:
                        ws = _WebSocket(page_ws_url)
                        ws.call("Network.enable", timeout=4.0)
                    except Exception:
                        try:
                            if ws is not None:
                                ws.close()
                        except Exception:
                            pass
                        ws = None
                if ws is None:
                    time.sleep(1.0)
                    continue
            try:
                cookie = _browser_cookies(ws)
            except Exception:
                try:
                    ws.close()
                except Exception:
                    pass
                ws = None
                time.sleep(0.5)
                continue
            if not cookie:
                time.sleep(1.2)
                continue
            emit("Проверяю аккаунт…")
            profile_info = verify_roblox_cookie(cookie)
            if profile_info.get("ok"):
                return {"ok": True, "cancelled": False, "cookie": cookie,
                        "profile": profile_info, "error": ""}
            time.sleep(1.0)
        return {"ok": False, "cancelled": False,
                "error": "Время ожидания входа вышло."}
    except Exception as exc:
        return {"ok": False, "cancelled": False,
                "error": f"Браузерный вход не сработал: {exc}"}
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        _stop_browser(proc, browser_ws_url)
        _cleanup_browser_profile(profile)


# ---- Discord Rich Presence ---------------------------------------------------

DISCORD_PORTAL_URL = "https://discord.com/developers/applications"

_OP_HANDSHAKE = 0
_OP_FRAME = 1
_OP_CLOSE = 2
_OP_PING = 3
_OP_PONG = 4


def valid_discord_app_id(value):
    """Application ID приложения Discord — 15–25 цифр, иначе пусто."""
    value = str(value or "").strip()
    return value if re.fullmatch(r"\d{15,25}", value) else ""


def discord_activity(details=None, state=None, start_ms=None, buttons=None):
    """Собирает объект activity для SET_ACTIVITY в рамках лимитов Discord."""
    activity = {"instance": True}
    for key, value in (("details", details), ("state", state)):
        text = str(value or "").strip()
        if len(text) > 128:
            text = text[:127] + "…"
        if len(text) >= 2:
            activity[key] = text
    if start_ms:
        activity["timestamps"] = {"start": int(start_ms)}
    clean = []
    for item in buttons or ():
        label = str(item.get("label") or "").strip()[:32]
        url = str(item.get("url") or "").strip()
        if label and url.startswith(("https://", "http://")):
            clean.append({"label": label, "url": url[:512]})
    if clean:
        activity["buttons"] = clean[:2]
    return activity


def discord_frame(op, payload):
    """Кадр Discord IPC: little-endian op + длина + JSON."""
    data = json.dumps(payload, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
    return struct.pack("<II", op, len(data)) + data


class DiscordRPC:
    """Минимальный клиент Discord IPC через named pipe (без библиотек)."""

    def __init__(self, app_id):
        self.app_id = valid_discord_app_id(app_id)
        self._pipe = None
        self._write_lock = threading.Lock()
        self._ready = threading.Event()
        self._closed = threading.Event()
        self.last_error = ""

    @property
    def connected(self):
        return self._pipe is not None and self._ready.is_set()

    @staticmethod
    def _pipe_names():
        if sys.platform == "win32":
            return [rf"\\?\pipe\discord-ipc-{i}" for i in range(10)]
        base = os.getenv("XDG_RUNTIME_DIR") or os.getenv("TMPDIR") or "/tmp"
        return [str(Path(base) / f"discord-ipc-{i}") for i in range(10)]

    @staticmethod
    def _read_exact(pipe, size):
        data = bytearray()
        while len(data) < size:
            chunk = pipe.read(size - len(data))
            if not chunk:
                return b""
            data.extend(chunk)
        return bytes(data)

    def connect(self, timeout=3.0):
        if not self.app_id:
            return False
        pipe = None
        for name in self._pipe_names():
            try:
                pipe = open(name, "r+b", buffering=0)
                break
            except OSError:
                continue
        if pipe is None:
            self.last_error = "no_pipe"
            return False
        self._pipe = pipe
        self._closed.clear()
        self.last_error = ""
        try:
            self._send(_OP_HANDSHAKE, {"v": 1, "client_id": self.app_id})
        except OSError:
            self.close()
            return False
        threading.Thread(target=self._read_loop, daemon=True,
                         name="discord-rpc-reader").start()
        if not self._ready.wait(timeout):
            if self.last_error == "closed":
                # Discord закрыл пайп до READY — обычно это чужой client_id
                self.last_error = "rejected"
            elif not self.last_error:
                self.last_error = "no_response"
            self.close()
            return False
        return True

    def _send(self, op, payload):
        pipe = self._pipe
        if pipe is None:
            raise OSError("Discord IPC закрыт")
        frame = discord_frame(op, payload)
        with self._write_lock:
            offset = 0
            while offset < len(frame):
                written = pipe.write(frame[offset:])
                if not written:
                    raise OSError("Discord IPC: запись не удалась")
                offset += written

    def _read_loop(self):
        pipe = self._pipe
        try:
            while pipe is not None:
                header = self._read_exact(pipe, 8)
                if not header:
                    break
                op, length = struct.unpack("<II", header)
                body = self._read_exact(pipe, length) if length else b""
                if length and not body:
                    break
                payload = {}
                if body:
                    try:
                        payload = json.loads(body.decode("utf-8", "replace"))
                    except ValueError:
                        payload = {}
                if op == _OP_PING:
                    try:
                        self._send(_OP_PONG, payload)
                    except OSError:
                        break
                elif op == _OP_CLOSE:
                    if not self.last_error:
                        self.last_error = "closed"
                    break
                elif payload.get("evt") == "READY":
                    self._ready.set()
                elif payload.get("evt") == "ERROR":
                    self.last_error = "rejected"
                    break
        except (OSError, ValueError):
            pass
        finally:
            self._ready.clear()
            if not self.last_error:
                self.last_error = "closed"

    def set_activity(self, activity):
        self._send(_OP_FRAME, {
            "cmd": "SET_ACTIVITY",
            "args": {"pid": os.getpid(), "activity": activity},
            "nonce": uuid.uuid4().hex,
        })

    def clear_activity(self):
        self._send(_OP_FRAME, {
            "cmd": "SET_ACTIVITY",
            "args": {"pid": os.getpid(), "activity": None},
            "nonce": uuid.uuid4().hex,
        })

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        self._ready.clear()
        pipe, self._pipe = self._pipe, None
        if pipe is not None:
            try:
                pipe.close()
            except (OSError, ValueError):
                pass


class DiscordPresence:
    """Фоновый статус: подключение с ретраями и обновление активности."""

    RETRY_DELAY = 15

    def __init__(self, app_id, start_ts=None):
        self.app_id = valid_discord_app_id(app_id)
        self.start_ms = int((start_ts or time.time()) * 1000)
        self._activity = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self.connected = False
        self.last_error = ""

    def start(self):
        if not self.app_id or self._thread is not None:
            return False
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="discord-presence")
        self._thread.start()
        return True

    def set_activity(self, details=None, state=None, buttons=None):
        activity = discord_activity(details, state, self.start_ms, buttons)
        with self._lock:
            self._activity = activity
        self._wake.set()

    def clear_activity(self):
        with self._lock:
            self._activity = None
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def _run(self):
        rpc = None
        while not self._stop.is_set():
            if rpc is None:
                rpc = DiscordRPC(self.app_id)
                if not rpc.connect():
                    self.last_error = rpc.last_error or "no_pipe"
                    rpc = None
                    self.connected = False
                    self._stop.wait(self.RETRY_DELAY)
                    continue
                self.connected = True
                self.last_error = ""
            with self._lock:
                activity = self._activity
            try:
                if activity is not None:
                    rpc.set_activity(activity)
                else:
                    rpc.clear_activity()
            except OSError:
                self.connected = False
                self.last_error = rpc.last_error or "closed"
                rpc.close()
                rpc = None
                continue
            self._wake.wait(self.RETRY_DELAY)
            self._wake.clear()
            if self._stop.is_set():
                break
            if not rpc.connected:
                self.connected = False
                self.last_error = rpc.last_error or "closed"
                rpc.close()
                rpc = None
                continue
            with self._lock:
                activity = self._activity
            try:
                if activity is None:
                    rpc.clear_activity()
                else:
                    rpc.set_activity(activity)
            except OSError:
                self.connected = False
                self.last_error = rpc.last_error or "closed"
                rpc.close()
                rpc = None
        self.connected = False
        if rpc is not None:
            try:
                rpc.clear_activity()
            except OSError:
                pass
            rpc.close()


# ---- геометрия безрамочного окна --------------------------------------------------


def work_area():
    """Рабочая область экрана (без панели задач): (x, y, w, h)."""
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    rect = RECT()
    SPI_GETWORKAREA = 0x0030
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    if not ok:
        return 0, 0, 1280, 720
    return (rect.left, rect.top,
            rect.right - rect.left, rect.bottom - rect.top)


def hit_test(x, y, w, h, border=6, maximized=False):
    """Зона захвата для ресайза: '', 'n','s','e','w','ne','nw','se','sw'."""
    if maximized or border <= 0 or w <= border * 2 or h <= border * 2:
        return ""
    zone = ""
    if y < border:
        zone += "n"
    elif y >= h - border:
        zone += "s"
    if x < border:
        zone += "w"
    elif x >= w - border:
        zone += "e"
    return zone


def resize_geometry(x, y, w, h, zone, dx, dy, min_w=640, min_h=440):
    """Новая геометрия после сдвига мыши на (dx, dy) в зоне zone."""
    nx, ny, nw, nh = x, y, w, h
    if "e" in zone:
        nw = w + dx
    if "s" in zone:
        nh = h + dy
    if "w" in zone:
        nw = w - dx
        nx = x + dx
    if "n" in zone:
        nh = h - dy
        ny = y + dy
    if nw < min_w:
        if "w" in zone:
            nx = x + w - min_w
        nw = min_w
    if nh < min_h:
        if "n" in zone:
            ny = y + h - min_h
        nh = min_h
    return nx, ny, nw, nh
