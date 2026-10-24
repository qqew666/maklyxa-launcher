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
  * одноэкземплярность (mutex) и хелперы безрамочного окна.

Лаунчер не внедряет код в игру и не обходит защиту — только стандартные
ссылки roblox://, запись конфигов, запуск официальных файлов и локальная
замена PNG-курсоров клиента.
"""

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

try:
    import winreg
except ImportError:  # не Windows
    winreg = None

APP_NAME = "MaklyxaLauncher"
WINDOW_TITLE = APP_NAME
VERSION = "1.6.0"

INSTALLER_URL = "https://www.roblox.com/download/client"
DISCOVER_URL = "https://www.roblox.com/discover"
CREATE_NO_WINDOW = 0x08000000

CONFIG_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"
OLD_CONFIG_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / "RobloxLauncher"
OLD_CONFIG_PATH = OLD_CONFIG_DIR / "config.json"

CURSOR_SETS_DIR = CONFIG_DIR / "CursorSets"
CURSOR_STATE_PATH = CONFIG_DIR / "cursor_state.json"
CURSOR_BACKUP_DIR = CONFIG_DIR / "cursor_backup"
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

DEFAULT_CONFIG = {
    "auto_close": False,
    "save_history": True,
    "remember_last": True,
    "default_place_id": "",
    "last_place_id": "",
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
    "window_geometry": "",
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
                 "apply_flags_on_launch", "use_custom_cursors"):
        cfg[flag] = bool(cfg.get(flag))
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
    theme = str(cfg.get("theme") or "").lower()
    cfg["theme"] = theme if theme in THEMES else DEFAULT_THEME
    cfg["window_geometry"] = (str(cfg.get("window_geometry") or "")
                              if valid_geometry(cfg.get("window_geometry"))
                              else "")
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


# ---- данные игры (публичный API) ------------------------------------------------

HTTP_HEADERS = {"User-Agent": f"Mozilla/5.0 ({APP_NAME}/{VERSION})"}


def _get_json(url, timeout=8):
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_game_info(place_id):
    """Название, автор, сколько в игре + universe_id для иконки."""
    try:
        uni = _get_json(
            f"https://apis.roblox.com/universes/v1/places/{place_id}/universe")
        universe_id = uni.get("universeId")
        if not universe_id:
            return None
        data = _get_json(
            f"https://games.roblox.com/v1/games?universeIds={universe_id}")
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
