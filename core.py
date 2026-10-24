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
ссылки roblox://, запись конфигов и запуск официальных файлов.
"""

import ctypes
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

try:
    import winreg
except ImportError:  # не Windows
    winreg = None

APP_NAME = "MaklyxaLauncher"
WINDOW_TITLE = APP_NAME
VERSION = "1.3.2"

INSTALLER_URL = "https://www.roblox.com/download/client"
DISCOVER_URL = "https://www.roblox.com/discover"
CREATE_NO_WINDOW = 0x08000000

CONFIG_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"
OLD_CONFIG_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / "RobloxLauncher"
OLD_CONFIG_PATH = OLD_CONFIG_DIR / "config.json"

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
        "bg": "#12122a",
        "panel": "#191936",
        "surface": "#1d1d3e",
        "surface2": "#26264c",
        "hover": "#303059",
        "entry": "#22224a",
        "list": "#15152f",
        "border": "#34345f",
        "text": "#eef0f8",
        "muted": "#9296b8",
        "on_accent": "#0d1b16",
        "titlebar": "#0e0e22",
        "alpha": 1.0,
    },
    "light": {
        "label": "Светлая",
        "bg": "#eef1f7",
        "panel": "#e4e9f3",
        "surface": "#ffffff",
        "surface2": "#f1f4fa",
        "hover": "#e6ebf5",
        "entry": "#ffffff",
        "list": "#f7f9fd",
        "border": "#d5dbe8",
        "text": "#1a1d2b",
        "muted": "#5f6779",
        "on_accent": "#0d1b16",
        "titlebar": "#dde3ef",
        "alpha": 1.0,
    },
    "glass": {
        "label": "Стекло",
        "bg": "#0d1524",
        "panel": "#121b2c",
        "surface": "#1a2740",
        "surface2": "#22314e",
        "hover": "#2b3d5e",
        "entry": "#16223a",
        "list": "#111a2c",
        "border": "#3f5878",
        "text": "#eaf2ff",
        "muted": "#94a8c4",
        "on_accent": "#0d1b16",
        "titlebar": "#0b1220",
        "alpha": 0.9,
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
                 "apply_flags_on_launch"):
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
