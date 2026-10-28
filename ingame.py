#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""In-game helper: safe click-through overlay and anti-AFK for Roblox."""

import ctypes
import io
import json
import os
import queue
import random
import subprocess
import sys
import threading
import time
from collections import deque
from ctypes import wintypes
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont

try:
    import core
except ImportError:
    core = None

try:
    from PIL import Image, ImageDraw, ImageTk
except Exception:  # Pillow не обязателен для остального помощника
    Image = ImageDraw = ImageTk = None

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MEDIA_MANAGER,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PLAYBACK,
    )
    from winrt.windows.storage.streams import (
        Buffer as WINRT_BUFFER,
        DataReader as WINRT_READER,
        InputStreamOptions as WINRT_INPUT,
    )
    MEDIA_PLAYING = int(getattr(PLAYBACK, "PLAYING", 4))
except Exception:  # старые Windows или нет pywinrt — островок без трека
    MEDIA_MANAGER = None
    MEDIA_PLAYING = 4

IS_WINDOWS = os.name == "nt"

STOP_EVENT = r"Local\MaklyxaLauncherInGameStop_v1"
TOGGLE_EVENT = r"Local\MaklyxaLauncherInGameToggle_v1"
HOTKEY_ID = 0xB10
HOTKEY_ID_CLOCK = 0xB11
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
ERROR_ALREADY_EXISTS = 183
TH32CS_SNAPPROCESS = 0x00000002
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
ROBLOX_EXE = "robloxplayerbeta.exe"
FPS_NOTE = "кадры игры"
FPS_MAX = 2000
FPS_INTERVAL = 0.5
FPS_WINDOW = 0.9
WDDM_SOURCE_LIMIT = 8
PROCESS_QUERY_ACCESS = 0x0400 | 0x1000
D3DKMT_QUERYSTATISTICS_ADAPTER = 0
D3DKMT_QUERYSTATISTICS_PROCESS_VIDPNSOURCE = 8

ISLAND_KEY = "#010203"
ISLAND_BG = (14, 19, 27, 255)
ISLAND_BORDER = (44, 58, 76, 255)
ISLAND_TEXT = "#eef4fb"
ISLAND_MUTED = "#93a6ba"
ISLAND_H = 44
ISLAND_TOP_MARGIN = 10
ISLAND_COVER = 26
MEDIA_POLL = 2.0

CORNER_LABELS = {
    "tl": "Слева сверху",
    "tr": "Справа сверху",
    "bl": "Слева снизу",
    "br": "Справа снизу",
}
CORNER_BY_LABEL = {label: key for key, label in CORNER_LABELS.items()}

AFK_KEYS = (
    ("space", "Пробел", 0x20),
    ("shift", "Shift", 0x10),
    ("z", "Z", 0x5A),
    ("x", "X", 0x58),
    ("c", "C", 0x43),
    ("v", "V", 0x56),
    ("b", "B", 0x42),
)
AFK_KEY_LABELS = {key: label for key, label, _vk in AFK_KEYS}
AFK_KEY_LABEL_BY_KEY = AFK_KEY_LABELS
AFK_KEY_BY_LABEL = {label: key for key, label, _vk in AFK_KEYS}
AFK_VK = {key: vk for key, _label, vk in AFK_KEYS}
AFK_MODE_LABELS = {
    "mouse": "Мышь — микро-движение",
    "key": "Клавиша",
}
AFK_MODE_BY_LABEL = {label: key for key, label in AFK_MODE_LABELS.items()}

DEFAULT_HOTKEY = "shift+f1"
DEFAULT_CLOCK_HOTKEY = "shift+f3"
HOTKEY_MOD_ORDER = ("ctrl", "alt", "shift", "win")
HOTKEY_MODS = {
    "ctrl": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}
HOTKEY_MOD_LABELS = {
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "win": "Win",
}
HOTKEY_VK = {
    "space": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "esc": 0x1B,
    "backspace": 0x08,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "minus": 0xBD,
    "equals": 0xBB,
    "comma": 0xBC,
    "period": 0xBE,
    "slash": 0xBF,
    "semicolon": 0xBA,
    "quote": 0xDE,
    "backquote": 0xC0,
    "bracketleft": 0xDB,
    "bracketright": 0xDD,
    "backslash": 0xDC,
}
for _letter in "abcdefghijklmnopqrstuvwxyz":
    HOTKEY_VK[_letter] = ord(_letter.upper())
for _digit in "0123456789":
    HOTKEY_VK[_digit] = ord(_digit)
for _number in range(1, 25):
    HOTKEY_VK[f"f{_number}"] = 0x6F + _number
HOTKEY_KEY_LABELS = {
    "space": "Space",
    "tab": "Tab",
    "enter": "Enter",
    "esc": "Esc",
    "backspace": "Backspace",
    "insert": "Insert",
    "delete": "Delete",
    "home": "Home",
    "end": "End",
    "pageup": "PgUp",
    "pagedown": "PgDn",
    "left": "Left",
    "up": "Up",
    "right": "Right",
    "down": "Down",
    "minus": "-",
    "equals": "=",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "semicolon": ";",
    "quote": "'",
    "backquote": "`",
    "bracketleft": "[",
    "bracketright": "]",
    "backslash": "\\",
}
HOTKEY_KEY_ALIASES = {
    "return": "enter",
    "escape": "esc",
    "prior": "pageup",
    "next": "pagedown",
    "plus": "equals",
    "underscore": "minus",
    "colon": "semicolon",
    "quotedbl": "quote",
    "less": "comma",
    "greater": "period",
    "question": "slash",
    "bar": "backslash",
    "asciitilde": "backquote",
    "braceleft": "bracketleft",
    "braceright": "bracketright",
}
HOTKEY_MOD_KEYSYMS = {
    "control_l", "control_r", "shift_l", "shift_r", "alt_l", "alt_r",
    "win_l", "win_r", "super_l", "super_r", "meta_l", "meta_r",
    "caps_lock", "num_lock", "scroll_lock", "menu",
}


def normalize_hotkey(spec):
    """'Ctrl + Shift + H' -> 'ctrl+shift+h'; None для негодной записи."""
    text = str(spec or "").strip().lower().replace(" ", "+")
    parts = [part for part in text.split("+") if part]
    mods = set()
    key = None
    for part in parts:
        if part in ("ctrl", "control"):
            mods.add("ctrl")
        elif part in ("alt", "menu"):
            mods.add("alt")
        elif part == "shift":
            mods.add("shift")
        elif part in ("win", "windows", "super", "meta"):
            mods.add("win")
        elif key is None and part in HOTKEY_VK:
            key = part
        else:
            return None
    if key is None:
        return None
    ordered = [name for name in HOTKEY_MOD_ORDER if name in mods]
    return "+".join(ordered + [key])


def hotkey_parts(spec):
    """(модификаторы WinAPI, виртуальный код клавиши) или None."""
    normalized = normalize_hotkey(spec)
    if not normalized:
        return None
    parts = normalized.split("+")
    mods = 0
    for part in parts[:-1]:
        mods |= HOTKEY_MODS[part]
    return mods, HOTKEY_VK[parts[-1]]


def hotkey_label(spec):
    normalized = normalize_hotkey(spec) or DEFAULT_HOTKEY
    parts = normalized.split("+")
    labels = [HOTKEY_MOD_LABELS[part] for part in parts[:-1]]
    key = parts[-1]
    labels.append(HOTKEY_KEY_LABELS.get(key, key.upper()))
    return "+".join(labels)


def key_from_keysym(keysym):
    """Имя клавиши Tk -> каноническое имя для normalize_hotkey."""
    name = str(keysym or "").strip().lower()
    if not name or name in HOTKEY_MOD_KEYSYMS:
        return None
    name = HOTKEY_KEY_ALIASES.get(name, name)
    return name if name in HOTKEY_VK else None


def pressed_hotkey_modifiers():
    """Модификаторы, зажатые прямо сейчас (надёжнее event.state)."""
    if not IS_WINDOWS:
        return []
    checks = (("ctrl", 0x11), ("alt", 0x12), ("shift", 0x10),
              ("win", 0x5B), ("win", 0x5C))
    mods = []
    for name, vk in checks:
        try:
            down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
        except (OSError, AttributeError):
            return []
        if down and name not in mods:
            mods.append(name)
    return [name for name in HOTKEY_MOD_ORDER if name in mods]


user32 = ctypes.WinDLL("user32", use_last_error=True) if IS_WINDOWS else None
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if IS_WINDOWS else None

if IS_WINDOWS:
    kernel32.CreateEventW.argtypes = [
        wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = [
        wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.SetEvent.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD,
                                                  wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.GetTickCount.restype = wintypes.DWORD
    kernel32.GlobalMemoryStatusEx.argtypes = [wintypes.LPVOID]
    kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = [wintypes.LPVOID, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                      ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClientRect.argtypes = [wintypes.HWND, wintypes.LPVOID]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, wintypes.LPVOID]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                      wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.SendInput.argtypes = [wintypes.UINT, wintypes.LPVOID, ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.GetLastInputInfo.argtypes = [wintypes.LPVOID]
    user32.GetLastInputInfo.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE


def _data_dir():
    base = Path(core.CONFIG_PATH).parent if core is not None else Path(
        os.getenv("APPDATA", str(Path.home()))) / "MaklyxaLauncher"
    return base / "InGame"


def status_path(path=None):
    return Path(path) if path else _data_dir() / "status.json"


def helper_log(text):
    try:
        folder = _data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "helper.log"
        if path.exists() and path.stat().st_size > 128 * 1024:
            path.unlink()
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
    except OSError:
        pass


def write_status(data, path=None):
    target = status_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(data)
        payload["updated"] = time.time()
        payload.setdefault("pid", os.getpid())
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
        return True
    except (OSError, TypeError, ValueError):
        return False


def read_status(path=None, max_age=20):
    try:
        data = json.loads(status_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        age = time.time() - float(data.get("updated") or 0)
    except (TypeError, ValueError):
        return None
    if max_age is not None and age > max_age:
        return None
    return data


def helper_running():
    if not IS_WINDOWS:
        return False
    handle = kernel32.OpenEventW(SYNCHRONIZE, False, STOP_EVENT)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def stop_helper():
    if not IS_WINDOWS:
        return False
    handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, STOP_EVENT)
    if not handle:
        return False
    ok = bool(kernel32.SetEvent(handle))
    kernel32.CloseHandle(handle)
    return ok


def toggle_overlay():
    if not IS_WINDOWS:
        return False
    handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, TOGGLE_EVENT)
    if not handle:
        return False
    ok = bool(kernel32.SetEvent(handle))
    kernel32.CloseHandle(handle)
    return ok


def _launcher_command():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--ingame"]
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.is_file() else exe
    script = Path(__file__).resolve().with_name("launcher.py")
    return [str(runner), str(script), "--ingame"]


PYI_ENV_VARS = ("_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE",
                "_PYI_PARENT_PROCESS_LEVEL", "_PYI_SPLASH_IPC")


def helper_env():
    """Окружение помощника без унаследованной папки _MEI лаунчера."""
    env = os.environ.copy()
    for key in PYI_ENV_VARS:
        env.pop(key, None)
    if getattr(sys, "frozen", False):
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def helper_cwd():
    # папка _MEI не должна быть текущей: Windows не удаляет занятый каталог
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent)
    return str(Path(__file__).resolve().parent)


def start_helper(wait=5.0):
    if not IS_WINDOWS:
        return False, "только Windows"
    if helper_running():
        return True, "уже работает"
    try:
        subprocess.Popen(
            _launcher_command(),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            cwd=helper_cwd(), env=helper_env())
    except OSError as exc:
        return False, f"не запустился: {exc}"
    deadline = time.monotonic() + max(0.2, float(wait))
    while time.monotonic() < deadline:
        if helper_running():
            return True, "запущен"
        time.sleep(0.05)
    return False, f"не ответил за {wait:g} с — смотри helper.log"


def _enable_dpi_awareness():
    if not IS_WINDOWS:
        return
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def memory_snapshot():
    if not IS_WINDOWS:
        return None
    state = MEMORYSTATUSEX()
    state.dwLength = ctypes.sizeof(state)
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
        return None
    total = int(state.ullTotalPhys)
    free = int(state.ullAvailPhys)
    used = max(0, total - free)
    return {"total": total, "used": used,
            "percent": int(round(used * 100 / total)) if total else 0}


class _NvmlUtilization(ctypes.Structure):
    _fields_ = [("gpu", wintypes.UINT), ("memory", wintypes.UINT)]


class _NvmlMemory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                ("used", ctypes.c_ulonglong)]


class Nvml:
    def __init__(self):
        self._dll = None
        self._handle = None
        self._available = None
        self._next_try = 0.0
        self._lock = threading.Lock()

    def _load(self):
        if self._available:
            return True
        now = time.monotonic()
        if self._available is False and now < self._next_try:
            return False
        with self._lock:
            if self._available:
                return True
            if self._available is False and now < self._next_try:
                return False
            try:
                dll = ctypes.WinDLL("nvml.dll")
                init = getattr(dll, "nvmlInit_v2", None) or dll.nvmlInit
                init.restype = ctypes.c_int
                getter = (getattr(dll, "nvmlDeviceGetHandleByIndex_v2", None)
                          or dll.nvmlDeviceGetHandleByIndex)
                getter.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
                getter.restype = ctypes.c_int
                if init() != 0:
                    raise OSError("nvmlInit failed")
                handle = ctypes.c_void_p()
                if getter(0, ctypes.byref(handle)) != 0 or not handle.value:
                    raise OSError("nvml device not found")
                dll.nvmlDeviceGetTemperature.argtypes = [
                    ctypes.c_void_p, ctypes.c_uint,
                    ctypes.POINTER(ctypes.c_uint)]
                dll.nvmlDeviceGetTemperature.restype = ctypes.c_int
                dll.nvmlDeviceGetUtilizationRates.argtypes = [
                    ctypes.c_void_p, ctypes.POINTER(_NvmlUtilization)]
                dll.nvmlDeviceGetUtilizationRates.restype = ctypes.c_int
                dll.nvmlDeviceGetMemoryInfo.argtypes = [
                    ctypes.c_void_p, ctypes.POINTER(_NvmlMemory)]
                dll.nvmlDeviceGetMemoryInfo.restype = ctypes.c_int
                self._dll = dll
                self._handle = handle
                self._available = True
                return True
            except (OSError, AttributeError, ValueError):
                self._available = False
                self._handle = None
                self._dll = None
                self._next_try = now + 60
                return False

    def read(self):
        if not IS_WINDOWS or not self._load():
            return None
        try:
            temp = ctypes.c_uint()
            temp_ok = (self._dll.nvmlDeviceGetTemperature(
                self._handle, 0, ctypes.byref(temp)) == 0)
            util = _NvmlUtilization()
            util_ok = (self._dll.nvmlDeviceGetUtilizationRates(
                self._handle, ctypes.byref(util)) == 0)
            mem = _NvmlMemory()
            mem_ok = (self._dll.nvmlDeviceGetMemoryInfo(
                self._handle, ctypes.byref(mem)) == 0)
            return {
                "temp": int(temp.value) if temp_ok else None,
                "util": int(util.gpu) if util_ok else None,
                "mem_used": int(mem.used) if mem_ok else None,
                "mem_total": int(mem.total) if mem_ok else None,
            }
        except (OSError, AttributeError):
            return None


_NVML = Nvml()


def gpu_snapshot():
    return _NVML.read()


class _Luid(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _OpenAdapterFromHdc(ctypes.Structure):
    _fields_ = [
        ("hDc", wintypes.HDC),
        ("hAdapter", wintypes.UINT),
        ("AdapterLuid", _Luid),
        ("VidPnSourceId", wintypes.UINT),
    ]


class _CloseAdapter(ctypes.Structure):
    _fields_ = [("hAdapter", wintypes.UINT)]


class _D3dkmtQueryStatistics(ctypes.Structure):
    _fields_ = [
        ("Type", ctypes.c_uint),
        ("AdapterLuid", _Luid),
        ("hProcess", wintypes.HANDLE),
        ("Result", ctypes.c_ubyte * 776),
        ("VidPnSourceId", ctypes.c_uint),
    ]


# Пассивный счётчик кадров: WDDM отдаёт число презентаций процесса на
# VidPnSource. Только чтение статистики драйвера — без внедрения в игру.
gdi32 = None
_FPS_NATIVE = False
if IS_WINDOWS and ctypes.sizeof(ctypes.c_void_p) == 8:
    try:
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        gdi32.D3DKMTOpenAdapterFromHdc.argtypes = [
            ctypes.POINTER(_OpenAdapterFromHdc)]
        gdi32.D3DKMTOpenAdapterFromHdc.restype = wintypes.LONG
        gdi32.D3DKMTQueryStatistics.argtypes = [
            ctypes.POINTER(_D3dkmtQueryStatistics)]
        gdi32.D3DKMTQueryStatistics.restype = wintypes.LONG
        gdi32.D3DKMTCloseAdapter.argtypes = [ctypes.POINTER(_CloseAdapter)]
        gdi32.D3DKMTCloseAdapter.restype = wintypes.LONG
        _FPS_NATIVE = ctypes.sizeof(_D3dkmtQueryStatistics) == 0x328
    except (AttributeError, OSError, ValueError):
        gdi32 = None
        _FPS_NATIVE = False


def fps_from_samples(samples, min_span=0.5, max_fps=FPS_MAX):
    """Кадры в секунду по замерам (время, суммарный счётчик кадров)."""
    if samples is None or len(samples) < 2:
        return None
    first, last = samples[0], samples[-1]
    span = float(last[0]) - float(first[0])
    if span <= 0 or span < float(min_span):
        return None
    delta = int(last[1]) - int(first[1])
    if delta < 0:
        return None
    value = delta / span
    if value > float(max_fps):
        return None
    return int(round(value))


class FpsMeter:
    FRAME_RESULT_BYTES = 8
    SOURCE_COUNT_OFFSET = 8
    FAIL_LIMIT = 4

    def __init__(self):
        self.value = None
        self.available = bool(_FPS_NATIVE)
        self._pid = 0
        self._process = None
        self._hwnd = 0
        self._adapter = 0
        self._luid = None
        self._sources = 0
        self._samples = deque(maxlen=4)
        self._fails = 0
        self._next_adapter = 0.0

    def _close_process(self):
        if self._process:
            try:
                kernel32.CloseHandle(self._process)
            except (OSError, AttributeError):
                pass
        self._process = None

    def _close_adapter(self):
        if self._adapter and gdi32 is not None:
            try:
                gdi32.D3DKMTCloseAdapter(
                    ctypes.byref(_CloseAdapter(self._adapter)))
            except (OSError, AttributeError, ValueError):
                pass
        self._adapter = 0
        self._luid = None
        self._sources = 0

    def _ensure_process(self, pid):
        if self._process and self._pid == pid:
            return True
        self._close_process()
        self._samples.clear()
        self.value = None
        try:
            handle = kernel32.OpenProcess(PROCESS_QUERY_ACCESS, False, pid)
        except (OSError, AttributeError):
            handle = None
        if not handle:
            return False
        self._process = handle
        self._pid = pid
        return True

    def _count_sources(self):
        query = _D3dkmtQueryStatistics()
        query.Type = D3DKMT_QUERYSTATISTICS_ADAPTER
        query.AdapterLuid = self._luid
        if gdi32.D3DKMTQueryStatistics(ctypes.byref(query)) != 0:
            return 0
        raw = ctypes.string_at(ctypes.addressof(query.Result), 12)
        count = int.from_bytes(raw[self.SOURCE_COUNT_OFFSET:12], "little")
        return max(0, min(count, WDDM_SOURCE_LIMIT))

    def _open_adapter(self, hwnd):
        hwnd = _hwnd_value(hwnd)
        hdc = user32.GetDC(hwnd) if hwnd else 0
        owned = bool(hdc)
        if not hdc:
            hdc = user32.GetDC(None)
        if not hdc:
            return False
        try:
            params = _OpenAdapterFromHdc()
            params.hDc = hdc
            status = gdi32.D3DKMTOpenAdapterFromHdc(ctypes.byref(params))
        finally:
            user32.ReleaseDC(hwnd if owned else None, hdc)
        if status != 0 or not params.hAdapter:
            return False
        self._adapter = int(params.hAdapter)
        self._luid = params.AdapterLuid
        self._sources = self._count_sources()
        if self._sources <= 0:
            self._close_adapter()
            return False
        self._hwnd = hwnd
        return True

    def _read_frames(self):
        total = 0
        for source in range(self._sources):
            query = _D3dkmtQueryStatistics()
            query.Type = D3DKMT_QUERYSTATISTICS_PROCESS_VIDPNSOURCE
            query.AdapterLuid = self._luid
            query.hProcess = self._process
            query.VidPnSourceId = source
            if gdi32.D3DKMTQueryStatistics(ctypes.byref(query)) != 0:
                if source == 0:
                    return None
                continue
            raw = ctypes.string_at(
                ctypes.addressof(query.Result), self.FRAME_RESULT_BYTES)
            total += int.from_bytes(raw, "little")
        return total

    def sample(self, pid, hwnd, now=None):
        if not self.available:
            return None
        now = time.monotonic() if now is None else float(now)
        pid = int(pid or 0)
        if not pid:
            self.reset()
            return None
        if not self._ensure_process(pid):
            self.value = None
            return None
        if self._adapter and _hwnd_value(hwnd) != self._hwnd:
            self._close_adapter()
            self._samples.clear()
        if not self._adapter:
            if now < self._next_adapter:
                return None
            if not self._open_adapter(hwnd):
                self._next_adapter = now + 5.0
                self._samples.clear()
                return None
        frames = self._read_frames()
        if frames is None:
            self._fails += 1
            self._close_adapter()
            self._samples.clear()
            self._next_adapter = now + 1.5
            if self._fails >= self.FAIL_LIMIT:
                self.available = False
                self.value = None
            return None
        self._fails = 0
        self._samples.append((now, frames))
        value = fps_from_samples(self._samples, min_span=FPS_WINDOW)
        if value is None:
            return None
        self.value = value
        return value

    def reset(self):
        """Сбросить историю, сохранив последнее значение для повторного показа."""
        self._samples.clear()
        self._fails = 0
        self._next_adapter = 0.0
        self._close_process()
        self._close_adapter()
        self._hwnd = 0
        self._pid = 0

    def close(self):
        self.reset()
        self.value = None


def roblox_pids():
    if not IS_WINDOWS:
        return set()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot:
        return set()
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        found = set()
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if str(entry.szExeFile).lower() == ROBLOX_EXE:
                found.add(int(entry.th32ProcessID))
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        return found
    finally:
        kernel32.CloseHandle(snapshot)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _window_title(hwnd):
    length = int(user32.GetWindowTextLengthW(hwnd))
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _client_rect(hwnd):
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    left = wintypes.POINT(0, 0)
    right = wintypes.POINT(rect.right, rect.bottom)
    if not user32.ClientToScreen(hwnd, ctypes.byref(left)):
        return None
    if not user32.ClientToScreen(hwnd, ctypes.byref(right)):
        return None
    if right.x <= left.x or right.y <= left.y:
        return None
    return (left.x, left.y, right.x, right.y)


def foreground_pid():
    if not IS_WINDOWS:
        return 0
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def roblox_foreground():
    pid = foreground_pid()
    return bool(pid) and pid in roblox_pids()


def _hwnd_value(hwnd):
    if hwnd is None:
        return 0
    if isinstance(hwnd, int):
        return hwnd
    return int(getattr(hwnd, "value", 0) or 0)


def find_roblox_window():
    if not IS_WINDOWS:
        return None
    pids = roblox_pids()
    if not pids:
        return None
    candidates = []
    enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) not in pids:
            return True
        title = _window_title(hwnd)
        rect = _client_rect(hwnd)
        if not title or rect is None:
            return True
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        if width < 240 or height < 180:
            return True
        candidates.append({
            "hwnd": _hwnd_value(hwnd), "pid": int(pid.value), "title": title,
            "rect": rect, "width": width, "height": height,
            "offset": 0,
        })
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    if not candidates:
        return None
    best = max(candidates, key=lambda item: item["width"] * item["height"])
    best["foreground"] = bool(
        _hwnd_value(user32.GetForegroundWindow()) == best["hwnd"]
        or foreground_pid() in pids)
    return best


def overlay_origin(rect, size, corner="tr", margin=16):
    left, top, right, bottom = rect
    width, height = size
    if corner in ("tl", "bl"):
        x = left + margin
    else:
        x = right - width - margin
    if corner in ("tl", "tr"):
        y = top + margin
    else:
        y = bottom - height - margin
    x = max(left + 4, min(x, max(left + 4, right - width - 4)))
    y = max(top + 4, min(y, max(top + 4, bottom - height - 4)))
    return int(x), int(y)


def afk_vk(key):
    return AFK_VK.get(str(key or "").lower(), AFK_VK["space"])


def afk_interval_seconds(minutes, jitter=True):
    try:
        base = max(1, min(15, int(minutes or 5))) * 60
    except (TypeError, ValueError):
        base = 300
    if not jitter:
        return base
    return max(30.0, base + random.uniform(-0.08, 0.08) * base)


def format_countdown(seconds):
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError):
        total = 0
    minutes, secs = divmod(total, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def user_idle_seconds():
    if not IS_WINDOWS:
        return None
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    tick = kernel32.GetTickCount()
    return ((tick - info.dwTime) & 0xFFFFFFFF) / 1000.0


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


def _send_mouse_move(dx, dy):
    item = INPUT(type=0)
    item.union.mi = MOUSEINPUT(dx, dy, 0, 0x0001, 0, 0)
    return user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT)) == 1


def _send_key(vk, up=False):
    item = INPUT(type=1)
    item.union.ki = KEYBDINPUT(vk, 0, 0x0002 if up else 0, 0, 0)
    return user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT)) == 1


def send_micro_action(mode="mouse", key="space"):
    if not IS_WINDOWS or not roblox_foreground():
        return False
    try:
        if str(mode).lower() == "key":
            vk = afk_vk(key)
            ok = _send_key(vk, False)
            time.sleep(0.04)
            ok = _send_key(vk, True) and ok
            return bool(ok)
        ok = _send_mouse_move(1, 0)
        time.sleep(0.035)
        ok = _send_mouse_move(-1, 0) and ok
        return bool(ok)
    except (OSError, AttributeError):
        return False


class HotkeyThread:
    def __init__(self, events, spec=DEFAULT_HOTKEY, hotkey_id=HOTKEY_ID,
                 event="toggle"):
        self.events = events
        self.spec = normalize_hotkey(spec) or DEFAULT_HOTKEY
        self.hotkey_id = int(hotkey_id)
        self.event = str(event)
        self.ok = False
        self.error = ""
        self._thread = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._tid = 0
        self._registered = False

    @property
    def label(self):
        return hotkey_label(self.spec)

    def start(self):
        if not IS_WINDOWS:
            self.error = "только Windows"
            return False
        if not hotkey_parts(self.spec):
            self.error = "неверная комбинация"
            return False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(1.2)
        return self.ok

    def _register(self):
        parsed = hotkey_parts(self.spec)
        if parsed is None:
            return False
        mods, key = parsed
        try:
            return bool(user32.RegisterHotKey(
                None, self.hotkey_id, mods | MOD_NOREPEAT, key))
        except (OSError, AttributeError):
            return False

    def _run(self):
        self._tid = int(kernel32.GetCurrentThreadId())
        while not self._stop.is_set():
            if self._register():
                break
            self._ready.set()
            if not hotkey_parts(self.spec):
                self.error = "неверная комбинация"
                return
            self.error = f"{self.label} занят другой программой"
            self._stop.wait(3.0)
        if self._stop.is_set():
            self._ready.set()
            return
        self._registered = True
        self.ok = True
        self.error = ""
        self._ready.set()
        msg = wintypes.MSG()
        while not self._stop.is_set():
            result = int(user32.GetMessageW(ctypes.byref(msg), None, 0, 0))
            if result <= 0:
                break
            if msg.message == WM_HOTKEY and int(msg.wParam) == self.hotkey_id:
                self.events.put(self.event)
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        try:
            user32.UnregisterHotKey(None, self.hotkey_id)
        except (OSError, AttributeError):
            pass
        self._registered = False

    def stop(self):
        if not IS_WINDOWS or self._thread is None:
            return
        self._stop.set()
        try:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        except (OSError, AttributeError):
            pass
        self._thread.join(timeout=3.6)


def overlay_font(root, cfg):
    """Подключить сохранённый TTF/OTF только к процессу оверлея."""
    if core is None:
        return "Segoe UI"
    path = None
    try:
        state = core.ui_font_state()
        path = state.get("path")
        if path is None or not core.load_private_font(path):
            return "Segoe UI"
        available = {str(name).casefold(): str(name)
                     for name in tkfont.families(root)}
        candidates = list(state.get("families") or [])
        candidates.append((cfg or {}).get("ui_font_family") or "")
        for candidate in candidates:
            key = str(candidate or "").strip().casefold()
            if key and key in available:
                return available[key]
        core.unload_private_font(path)
    except Exception:  # noqa: BLE001 — шрифт не должен ломать оверлей
        if path is not None:
            try:
                core.unload_private_font(path)
            except Exception:
                pass
    return "Segoe UI"


class Overlay:
    BG = "#0b0f14"
    BORDER = "#243040"
    TEXT = "#e7eef7"
    MUTED = "#8fa3b8"
    FONT = "Segoe UI"

    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.FONT = overlay_font(root, cfg)
        self.top = tk.Toplevel(root)
        self.top.withdraw()
        self.top.overrideredirect(True)
        try:
            self.top.attributes("-topmost", True)
            self.top.attributes("-alpha", 0.93)
            self.top.attributes("-toolwindow", True)
        except tk.TclError:
            pass
        self.top.configure(bg=self.BG)
        self._visible = False
        self._hwnd = None
        self._root_hwnd = None
        self._accent = self._accent_color(cfg)
        self._hotkey_label = hotkey_label((cfg or {}).get("ingame_hotkey"))
        self._hotkey_ok = True
        self._build()
        self.top.update_idletasks()
        self._hwnd = int(self.top.winfo_id())
        self._root_hwnd = int(
            user32.GetAncestor(self._hwnd, 2) or self._hwnd) if IS_WINDOWS \
            else self._hwnd
        self._apply_style()

    @staticmethod
    def _accent_color(cfg):
        value = str((cfg or {}).get("accent") or "#00c48c").lower()
        if len(value) == 7 and value.startswith("#"):
            try:
                r, g, b = (int(value[i:i + 2], 16) for i in (1, 3, 5))
                if 0.299 * r + 0.587 * g + 0.114 * b >= 75:
                    return value
            except ValueError:
                pass
        return "#00c48c"

    def _build(self):
        self.fps_var = tk.StringVar(value="—")
        self.fps_sub_var = tk.StringVar(value=FPS_NOTE)
        self.gpu_var = tk.StringVar(value="—")
        self.gpu_sub_var = tk.StringVar(value="нет датчика")
        self.ram_var = tk.StringVar(value="—")
        self.ram_sub_var = tk.StringVar(value="")
        self.afk_var = tk.StringVar(value="—")
        self.afk_sub_var = tk.StringVar(value="выкл.")
        self.time_var = tk.StringVar(value=time.strftime("%H:%M:%S"))
        self.hint_var = tk.StringVar(
            value=f"{self._hotkey_label} скрыть")
        self.live_var = tk.StringVar(value="● LIVE")

        body = tk.Frame(self.top, bg=self.BG,
                        highlightbackground=self.BORDER,
                        highlightthickness=1)
        body.pack(fill="both", expand=True)
        self.rail = tk.Frame(body, bg=self._accent, width=4)
        self.rail.pack(side="left", fill="y")
        content = tk.Frame(body, bg=self.BG)
        content.pack(side="left", fill="both", expand=True,
                     padx=(11, 14), pady=(9, 8))

        head = tk.Frame(content, bg=self.BG)
        head.pack(fill="x")
        self.brand_label = tk.Label(
            head, text="MAKLYXA", bg=self.BG, fg=self._accent,
            font=(self.FONT, 8, "bold"))
        self.brand_label.pack(side="left")
        tk.Label(head, text="  HUD", bg=self.BG, fg=self.MUTED,
                 font=(self.FONT, 8, "bold")).pack(side="left")
        tk.Label(head, textvariable=self.time_var, bg=self.BG,
                 fg=self.TEXT, font=(self.FONT, 16, "bold")).pack(
                     side="right")

        tk.Frame(content, bg=self.BORDER, height=1).pack(
            fill="x", pady=(6, 8))

        grid = tk.Frame(content, bg=self.BG)
        grid.pack(fill="x")
        for column in range(2):
            grid.grid_columnconfigure(column, weight=1, uniform="metric")
        self._metric(grid, 0, 0, "FPS", self.fps_var, self.fps_sub_var)
        self._metric(grid, 0, 1, "GPU", self.gpu_var, self.gpu_sub_var)
        self._metric(grid, 1, 0, "ПАМЯТЬ", self.ram_var, self.ram_sub_var)
        self._metric(grid, 1, 1, "AFK", self.afk_var, self.afk_sub_var)

        tk.Frame(content, bg=self.BORDER, height=1).pack(
            fill="x", pady=(2, 0))
        foot = tk.Frame(content, bg=self.BG)
        foot.pack(fill="x", pady=(5, 0))
        tk.Label(foot, textvariable=self.hint_var, bg=self.BG,
                 fg=self.MUTED, font=(self.FONT, 7)).pack(side="left")
        self.live_label = tk.Label(
            foot, textvariable=self.live_var, bg=self.BG, fg=self._accent,
            font=(self.FONT, 7, "bold"))
        self.live_label.pack(side="right")

    def _metric(self, parent, row, column, label, value_var, sub_var):
        cell = tk.Frame(parent, bg=self.BG)
        cell.grid(row=row, column=column, sticky="w",
                  padx=(0, 14), pady=(0, 6))
        tk.Label(cell, text=label, bg=self.BG, fg=self.MUTED,
                 font=(self.FONT, 7, "bold")).pack(anchor="w")
        self._value_labels = getattr(self, "_value_labels", [])
        value = tk.Label(cell, textvariable=value_var, bg=self.BG,
                         fg=self._accent, font=(self.FONT, 14, "bold"),
                         anchor="w")
        value.pack(anchor="w")
        self._value_labels.append(value)
        tk.Label(cell, textvariable=sub_var, bg=self.BG, fg=self.MUTED,
                 font=(self.FONT, 7), anchor="w").pack(anchor="w")

    def _apply_style(self):
        if not IS_WINDOWS or not self._hwnd:
            return
        try:
            if ctypes.sizeof(ctypes.c_void_p) == 8:
                get_long = user32.GetWindowLongPtrW
                set_long = user32.SetWindowLongPtrW
            else:
                get_long = user32.GetWindowLongW
                set_long = user32.SetWindowLongW
            get_long.argtypes = [wintypes.HWND, ctypes.c_int]
            get_long.restype = ctypes.c_ssize_t
            set_long.argtypes = [wintypes.HWND, ctypes.c_int,
                                 ctypes.c_ssize_t]
            set_long.restype = ctypes.c_ssize_t
            for hwnd in {self._hwnd, self._root_hwnd}:
                if not hwnd:
                    continue
                style = int(get_long(hwnd, -20))
                # click-through, no activation, no taskbar button; alpha is
                # left entirely to Tk so the window keeps rendering
                style |= 0x00000020 | 0x08000000 | 0x00000080
                set_long(hwnd, -20, style)
            user32.SetWindowPos(
                self._root_hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0010 | 0x0020)
        except (OSError, AttributeError):
            pass

    def set_hotkey_ok(self, ok, label=None):
        self._hotkey_ok = bool(ok)
        if label:
            self._hotkey_label = str(label)
        self.hint_var.set(f"{self._hotkey_label} скрыть" if ok
                          else f"{self._hotkey_label} занят")

    def apply_config(self, cfg):
        self.cfg = cfg
        accent = self._accent_color(cfg)
        if accent != self._accent:
            self._accent = accent
            self.brand_label.configure(fg=accent)
            self.rail.configure(bg=accent)
            self.live_label.configure(fg=accent)
            for label in getattr(self, "_value_labels", []):
                label.configure(fg=accent)
        self._hotkey_label = hotkey_label((cfg or {}).get("ingame_hotkey"))
        self.hint_var.set(f"{self._hotkey_label} скрыть"
                          if self._hotkey_ok
                          else f"{self._hotkey_label} занят")

    @property
    def visible(self):
        return self._visible

    def show(self, rect):
        if not rect:
            return
        self.top.update_idletasks()
        width = max(1, self.top.winfo_reqwidth())
        height = max(1, self.top.winfo_reqheight())
        corner = str(self.cfg.get("ingame_overlay_corner") or "tr")
        x, y = overlay_origin(rect, (width, height), corner)
        previous = (user32.GetForegroundWindow() if IS_WINDOWS else None)
        if not self._visible:
            self.top.deiconify()
            self.top.attributes("-topmost", True)
            self._apply_style()
            self._visible = True
        else:
            self.top.lift()
        # lift() откатывает ещё не применённую геометрию — ставим её после
        self.top.geometry(f"{x:+d}{y:+d}")
        if (IS_WINDOWS and previous
                and user32.GetForegroundWindow() != previous):
            user32.SetForegroundWindow(previous)

    def hide(self):
        if self._visible:
            self.top.withdraw()
            self._visible = False

    def update_stats(self, mem, gpu, afk_text):
        self.time_var.set(time.strftime("%H:%M:%S"))
        if mem:
            used = mem["used"] / (1024 ** 3)
            total = mem["total"] / (1024 ** 3)
            self.ram_var.set(f"{mem['percent']}%")
            self.ram_sub_var.set(f"{used:.1f} / {total:.1f} ГБ")
        else:
            self.ram_var.set("—")
            self.ram_sub_var.set("нет данных")
        if gpu:
            temp = gpu.get("temp")
            util = gpu.get("util")
            self.gpu_var.set(f"{temp}°C" if temp is not None else "—")
            bits = []
            if util is not None:
                bits.append(f"{util}%")
            if gpu.get("mem_used") and gpu.get("mem_total"):
                bits.append(f"{gpu['mem_used'] / 1024 ** 3:.1f} ГБ")
            self.gpu_sub_var.set(" · ".join(bits) if bits else "нет датчика")
        else:
            self.gpu_var.set("—")
            self.gpu_sub_var.set("нет датчика")
        raw = str(afk_text or "").strip()
        if raw.startswith("AFK:"):
            self.afk_var.set("—")
            self.afk_sub_var.set(raw[4:].strip() or "пауза")
        elif raw.startswith("AFK ") and raw != "AFK выкл.":
            self.afk_var.set(raw[4:].strip())
            self.afk_sub_var.set("до действия")
        else:
            self.afk_var.set("—")
            self.afk_sub_var.set(
                "выкл." if raw in ("", "AFK выкл.") else raw)

    def set_fps(self, value):
        if not self._fps_ok():
            self.fps_var.set("—")
            self.fps_sub_var.set("нет датчика")
        elif value is None:
            self.fps_var.set("—")
            self.fps_sub_var.set(FPS_NOTE)
        else:
            self.fps_var.set(str(int(value)))
            self.fps_sub_var.set(FPS_NOTE)

    @staticmethod
    def _fps_ok():
        return bool(_FPS_NATIVE)

    def destroy(self):
        try:
            self.top.destroy()
        except tk.TclError:
            pass


def _pil_lanczos():
    if Image is None:
        return 1
    return getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)


class MediaWatcher(threading.Thread):
    """Текущий трек из системных медиасессий Windows (SMTC)."""

    def __init__(self):
        super().__init__(daemon=True, name="maklyxa-media")
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._state = {"ok": False, "playing": False, "title": "",
                       "artist": "", "cover": b"", "key": ""}
        self._cover_key = ""
        self._cover = b""
        self.error = ""

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    def stop(self):
        self._stop_event.set()

    def run(self):
        if MEDIA_MANAGER is None:
            self.error = "pywinrt недоступен"
            helper_log("музыка: pywinrt не установлен — островок без трека")
            return
        init_apartment = None
        uninit_apartment = None
        apartment = None
        try:
            from winrt.runtime import (
                ApartmentType as apartment, init_apartment, uninit_apartment)
        except Exception:
            pass
        try:
            if init_apartment is not None:
                try:
                    init_apartment(apartment.MULTI_THREADED)
                except (TypeError, AttributeError):
                    init_apartment()
            import asyncio
            asyncio.run(self._loop())
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            helper_log(f"музыка: {self.error}")
        finally:
            if uninit_apartment is not None:
                try:
                    uninit_apartment()
                except Exception:
                    pass

    async def _loop(self):
        import asyncio
        manager = None
        while not self._stop_event.is_set():
            try:
                if manager is None:
                    manager = await MEDIA_MANAGER.request_async()
                state = await self._collect(manager)
                with self._lock:
                    self._state = state
            except Exception as exc:  # noqa: BLE001
                manager = None
                if not self.error:
                    self.error = f"{type(exc).__name__}: {exc}"
                    helper_log(f"музыка: {self.error}")
            await asyncio.sleep(MEDIA_POLL)

    async def _collect(self, manager):
        state = {"ok": True, "playing": False, "title": "", "artist": "",
                 "cover": b"", "key": ""}
        session = manager.get_current_session()
        if session is None:
            return state
        info = session.get_playback_info()
        if info is None or int(info.playback_status) != MEDIA_PLAYING:
            return state
        props = await session.try_get_media_properties_async()
        if props is None:
            return state
        title = str(props.title or "").strip()
        artist = str(props.artist or "").strip()
        if not title and not artist:
            return state
        key = f"{session.source_app_user_model_id}|{title}|{artist}"
        if key != self._cover_key and props.thumbnail is not None:
            cover = await self._thumbnail(props.thumbnail)
            if cover:
                self._cover_key = key
                self._cover = cover
        cover = self._cover if key == self._cover_key else b""
        state.update(playing=True, title=title, artist=artist,
                     cover=cover, key=key)
        return state

    async def _thumbnail(self, reference):
        stream = await reference.open_read_async()
        try:
            size = int(stream.size)
            if size <= 0 or size > 8 * 1024 * 1024:
                return b""
            buffer = WINRT_BUFFER(size)
            await stream.read_async(buffer, size, WINRT_INPUT.NONE)
            reader = WINRT_READER.from_buffer(buffer)
            data = bytearray(size)
            reader.read_bytes(data)
            return bytes(data)
        finally:
            try:
                stream.close()
            except Exception:
                pass


class Island:
    """Пилюля сверху экрана: часы и текущий трек с обложкой."""

    BG = "#0e131b"
    BORDER = "#2c3a4c"
    TEXT = ISLAND_TEXT
    MUTED = ISLAND_MUTED

    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.FONT = overlay_font(root, cfg)
        self.clock_font = tkfont.Font(root=root, family=self.FONT,
                                      size=13, weight="bold")
        self.title_font = tkfont.Font(root=root, family=self.FONT,
                                      size=10, weight="bold")
        self.artist_font = tkfont.Font(root=root, family=self.FONT, size=8)
        self.top = tk.Toplevel(root)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self._key_ok = False
        try:
            self.top.attributes("-topmost", True)
            self.top.attributes("-alpha", 0.95)
            self.top.attributes("-toolwindow", True)
            self.top.attributes("-transparentcolor", ISLAND_KEY)
            self._key_ok = True
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(
            self.top, bg=ISLAND_KEY if self._key_ok else self.BG,
            highlightthickness=0, bd=0)
        self.canvas.pack()
        self._visible = False
        self._hwnd = None
        self._root_hwnd = None
        self._bg_photo = None
        self._cover_photo = None
        self._size = (0, 0)
        self._rect = None
        self._clock = ""
        self._music = False
        self._title = ""
        self._artist = ""
        self._cover = b""
        self._key = ""
        self._paint()
        self.top.update_idletasks()
        self._hwnd = int(self.top.winfo_id())
        self._root_hwnd = int(
            user32.GetAncestor(self._hwnd, 2) or self._hwnd) if IS_WINDOWS \
            else self._hwnd
        self._apply_style()

    # -- отрисовка -----------------------------------------------------------

    def _measure(self):
        clock_w = self.clock_font.measure(self._clock or "00:00")
        if not self._music:
            return max(88, int(clock_w) + 40), ISLAND_H
        text_w = max(self.title_font.measure(self._title),
                     self.artist_font.measure(self._artist))
        text_w = min(max(text_w, 64), 230)
        width = 16 + clock_w + 13 + 1 + 13 + ISLAND_COVER + 10 + text_w + 18
        return int(width), ISLAND_H

    def _rounded_bg(self, width, height):
        if Image is None or ImageTk is None:
            return None
        scale = 4
        try:
            image = Image.new("RGBA", (width * scale, height * scale),
                              (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle(
                (scale, scale, width * scale - scale - 1,
                 height * scale - scale - 1),
                radius=height * scale / 2, fill=ISLAND_BG,
                outline=ISLAND_BORDER, width=scale)
            base = Image.new("RGB", image.size,
                             ISLAND_KEY if self._key_ok else self.BG)
            base.paste(image, (0, 0), image)
            base = base.resize((width, height), _pil_lanczos())
            return ImageTk.PhotoImage(base)
        except Exception:
            return None

    def _cover_image(self):
        if not self._cover or Image is None or ImageTk is None:
            return None
        try:
            image = Image.open(io.BytesIO(self._cover)).convert("RGBA")
            width, height = image.size
            side = min(width, height)
            image = image.crop(((width - side) // 2, (height - side) // 2,
                                (width + side) // 2, (height + side) // 2))
            image = image.resize((ISLAND_COVER, ISLAND_COVER),
                                 _pil_lanczos())
            mask = Image.new("L", (ISLAND_COVER * 4, ISLAND_COVER * 4), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, ISLAND_COVER * 4 - 1, ISLAND_COVER * 4 - 1),
                radius=ISLAND_COVER * 4, fill=255)
            mask = mask.resize((ISLAND_COVER, ISLAND_COVER), _pil_lanczos())
            image.putalpha(mask)
            return ImageTk.PhotoImage(image)
        except Exception:
            return None

    @staticmethod
    def _fit(text, font, width):
        text = str(text or "")
        if font.measure(text) <= width:
            return text
        ellipsis = "…"
        while text and font.measure(text + ellipsis) > width:
            text = text[:-1]
        return (text + ellipsis) if text else ellipsis

    def _paint(self):
        self._clock = time.strftime("%H:%M")
        width, height = self._measure()
        self._size = (width, height)
        self.canvas.configure(width=width, height=height)
        self.canvas.delete("all")
        self._bg_photo = self._rounded_bg(width, height)
        if self._bg_photo is not None:
            self.canvas.create_image(0, 0, anchor="nw",
                                     image=self._bg_photo)
        else:
            self.canvas.create_rectangle(
                1, 1, width - 1, height - 1, fill=self.BG,
                outline=self.BORDER)
        pad = 16
        self.canvas.create_text(
            pad, height / 2, text=self._clock, anchor="w",
            fill=self.TEXT, font=self.clock_font)
        if not self._music:
            return
        clock_w = self.clock_font.measure(self._clock)
        separator = pad + clock_w + 13
        self.canvas.create_line(separator, 12, separator, height - 12,
                                fill=self.BORDER)
        cover_x = separator + 13
        cover_y = (height - ISLAND_COVER) / 2
        self._cover_photo = self._cover_image()
        if self._cover_photo is not None:
            self.canvas.create_image(cover_x, cover_y, anchor="nw",
                                     image=self._cover_photo)
        else:
            self.canvas.create_oval(
                cover_x, cover_y, cover_x + ISLAND_COVER,
                cover_y + ISLAND_COVER, fill="#182231",
                outline=self.BORDER)
        text_x = cover_x + ISLAND_COVER + 10
        text_w = max(24, width - text_x - 18)
        title = self._fit(self._title, self.title_font, text_w)
        artist = self._fit(self._artist, self.artist_font, text_w)
        if artist:
            self.canvas.create_text(
                text_x, 14, text=title, anchor="w", fill=self.TEXT,
                font=self.title_font)
            self.canvas.create_text(
                text_x, 30, text=artist, anchor="w", fill=self.MUTED,
                font=self.artist_font)
        else:
            self.canvas.create_text(
                text_x, height / 2, text=title, anchor="w",
                fill=self.TEXT, font=self.title_font)
        if self._visible:
            self._place()

    def _place(self):
        if not self._rect:
            return
        width, height = self._size
        left, top, right = self._rect
        x = int(left) + max(0, (int(right) - int(left) - width) // 2)
        y = int(top) + ISLAND_TOP_MARGIN
        self.top.geometry(f"{width}x{height}+{x}+{y}")

    # -- данные --------------------------------------------------------------

    def set_media(self, state):
        state = state or {}
        playing = bool(state.get("playing")) and bool(
            state.get("title") or state.get("artist"))
        title = str(state.get("title") or "") if playing else ""
        artist = str(state.get("artist") or "") if playing else ""
        key = str(state.get("key") or "") if playing else ""
        cover = state.get("cover") or b""
        if not cover and playing and key == self._key:
            cover = self._cover
        if (playing, title, artist, key, cover) == (
                self._music, self._title, self._artist, self._key,
                self._cover):
            return
        self._music = playing
        self._title = title
        self._artist = artist
        self._key = key
        self._cover = cover
        self._paint()

    def set_clock(self):
        if time.strftime("%H:%M") == self._clock:
            return
        self._paint()

    # -- окно ----------------------------------------------------------------

    def _apply_style(self):
        if not IS_WINDOWS or not self._hwnd:
            return
        try:
            if ctypes.sizeof(ctypes.c_void_p) == 8:
                get_long = user32.GetWindowLongPtrW
                set_long = user32.SetWindowLongPtrW
            else:
                get_long = user32.GetWindowLongW
                set_long = user32.SetWindowLongW
            get_long.argtypes = [wintypes.HWND, ctypes.c_int]
            get_long.restype = ctypes.c_ssize_t
            set_long.argtypes = [wintypes.HWND, ctypes.c_int,
                                 ctypes.c_ssize_t]
            set_long.restype = ctypes.c_ssize_t
            for hwnd in {self._hwnd, self._root_hwnd}:
                if not hwnd:
                    continue
                style = int(get_long(hwnd, -20))
                # прозрачен для кликов, не активируется, без кнопки в панели
                style |= 0x00000020 | 0x08000000 | 0x00000080
                set_long(hwnd, -20, style)
            user32.SetWindowPos(
                self._root_hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0010 | 0x0020)
        except (OSError, AttributeError):
            pass

    @property
    def visible(self):
        return self._visible

    def show(self, rect):
        if not rect:
            return
        if isinstance(rect, dict):
            left, top = rect.get("left", 0), rect.get("top", 0)
            right = rect.get("right", left)
        else:
            left, top, right = rect[0], rect[1], rect[2]
        self._rect = (left, top, right)
        previous = user32.GetForegroundWindow() if IS_WINDOWS else None
        if not self._visible:
            self.top.deiconify()
            self.top.attributes("-topmost", True)
            self._apply_style()
            self._visible = True
        else:
            self.top.lift()
        # lift() откатывает ещё не применённую геометрию — ставим её после
        self._place()
        if (IS_WINDOWS and previous
                and user32.GetForegroundWindow() != previous):
            user32.SetForegroundWindow(previous)

    def hide(self):
        if self._visible:
            self.top.withdraw()
            self._visible = False

    def destroy(self):
        try:
            self.top.destroy()
        except tk.TclError:
            pass


class Clock:
    """Отдельные часы: только время, своё окно и свой бинд."""

    BG = "#0e131b"
    BORDER = "#2c3a4c"
    TEXT = ISLAND_TEXT

    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.FONT = overlay_font(root, cfg)
        self.clock_font = tkfont.Font(root=root, family=self.FONT,
                                      size=15, weight="bold")
        self.top = tk.Toplevel(root)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self._key_ok = False
        try:
            self.top.attributes("-topmost", True)
            self.top.attributes("-alpha", 0.95)
            self.top.attributes("-toolwindow", True)
            self.top.attributes("-transparentcolor", ISLAND_KEY)
            self._key_ok = True
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(
            self.top, bg=ISLAND_KEY if self._key_ok else self.BG,
            highlightthickness=0, bd=0)
        self.canvas.pack()
        self._visible = False
        self._hwnd = None
        self._root_hwnd = None
        self._bg_photo = None
        self._size = (0, 0)
        self._rect = None
        self._offset = 0
        self._clock = ""
        self._accent = Overlay._accent_color(cfg)
        self._paint()
        self.top.update_idletasks()
        self._hwnd = int(self.top.winfo_id())
        self._root_hwnd = int(
            user32.GetAncestor(self._hwnd, 2) or self._hwnd) if IS_WINDOWS \
            else self._hwnd
        self._apply_style()

    # -- отрисовка -----------------------------------------------------------

    def _measure(self):
        clock_w = self.clock_font.measure(self._clock or "00:00:00")
        return int(clock_w) + 50, ISLAND_H

    def _rounded_bg(self, width, height):
        if Image is None or ImageTk is None:
            return None
        scale = 4
        try:
            image = Image.new("RGBA", (width * scale, height * scale),
                              (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle(
                (scale, scale, width * scale - scale - 1,
                 height * scale - scale - 1),
                radius=height * scale / 2, fill=ISLAND_BG,
                outline=ISLAND_BORDER, width=scale)
            base = Image.new("RGB", image.size,
                             ISLAND_KEY if self._key_ok else self.BG)
            base.paste(image, (0, 0), image)
            base = base.resize((width, height), _pil_lanczos())
            return ImageTk.PhotoImage(base)
        except Exception:
            return None

    def _paint(self):
        self._clock = time.strftime("%H:%M:%S")
        width, height = self._measure()
        self._size = (width, height)
        self.canvas.configure(width=width, height=height)
        self.canvas.delete("all")
        self._bg_photo = self._rounded_bg(width, height)
        if self._bg_photo is not None:
            self.canvas.create_image(0, 0, anchor="nw",
                                     image=self._bg_photo)
        else:
            self.canvas.create_rectangle(
                1, 1, width - 1, height - 1, fill=self.BG,
                outline=self.BORDER)
        centre = height / 2
        self.canvas.create_oval(16, centre - 3, 22, centre + 3,
                                fill=self._accent, outline="")
        self.canvas.create_text(32, centre, text=self._clock, anchor="w",
                                fill=self.TEXT, font=self.clock_font)
        if self._visible:
            self._place()

    def _place(self):
        if not self._rect:
            return
        width, height = self._size
        left, top, right = self._rect
        x = int(left) + max(0, (int(right) - int(left) - width) // 2)
        y = int(top) + ISLAND_TOP_MARGIN + int(self._offset)
        self.top.geometry(f"{width}x{height}+{x}+{y}")

    # -- данные --------------------------------------------------------------

    def set_clock(self):
        if time.strftime("%H:%M:%S") == self._clock:
            return
        self._paint()

    def apply_config(self, cfg):
        self.cfg = cfg
        accent = Overlay._accent_color(cfg)
        if accent != self._accent:
            self._accent = accent
            self._paint()

    # -- окно ----------------------------------------------------------------

    def _apply_style(self):
        if not IS_WINDOWS or not self._hwnd:
            return
        try:
            if ctypes.sizeof(ctypes.c_void_p) == 8:
                get_long = user32.GetWindowLongPtrW
                set_long = user32.SetWindowLongPtrW
            else:
                get_long = user32.GetWindowLongW
                set_long = user32.SetWindowLongW
            get_long.argtypes = [wintypes.HWND, ctypes.c_int]
            get_long.restype = ctypes.c_ssize_t
            set_long.argtypes = [wintypes.HWND, ctypes.c_int,
                                 ctypes.c_ssize_t]
            set_long.restype = ctypes.c_ssize_t
            for hwnd in {self._hwnd, self._root_hwnd}:
                if not hwnd:
                    continue
                style = int(get_long(hwnd, -20))
                # прозрачен для кликов, не активируется, без кнопки в панели
                style |= 0x00000020 | 0x08000000 | 0x00000080
                set_long(hwnd, -20, style)
            user32.SetWindowPos(
                self._root_hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0010 | 0x0020)
        except (OSError, AttributeError):
            pass

    @property
    def visible(self):
        return self._visible

    def show(self, rect, offset=0):
        if not rect:
            return
        if isinstance(rect, dict):
            left, top = rect.get("left", 0), rect.get("top", 0)
            right = rect.get("right", left)
        else:
            left, top, right = rect[0], rect[1], rect[2]
        self._rect = (left, top, right)
        self._offset = int(offset)
        previous = user32.GetForegroundWindow() if IS_WINDOWS else None
        if not self._visible:
            self.top.deiconify()
            self.top.attributes("-topmost", True)
            self._apply_style()
            self._visible = True
        else:
            self.top.lift()
        # lift() откатывает ещё не применённую геометрию — ставим её после
        self._place()
        if (IS_WINDOWS and previous
                and user32.GetForegroundWindow() != previous):
            user32.SetForegroundWindow(previous)

    def hide(self):
        if self._visible:
            self.top.withdraw()
            self._visible = False

    def destroy(self):
        try:
            self.top.destroy()
        except tk.TclError:
            pass


class InGameHelper:
    def __init__(self, root, stop_handle, toggle_handle):
        self.root = root
        self.stop_handle = stop_handle
        self.toggle_handle = toggle_handle
        self.cfg = core.load_config() if core is not None else {}
        self.overlay = Overlay(root, self.cfg)
        self.island = Island(root, self.cfg)
        self.clock = Clock(root, self.cfg)
        self.media = None
        self.events = queue.Queue()
        self.hotkey = HotkeyThread(self.events,
                                   self.cfg.get("ingame_hotkey"))
        self.clock_hotkey = HotkeyThread(
            self.events, self.cfg.get("ingame_clock_hotkey"),
            hotkey_id=HOTKEY_ID_CLOCK, event="clock")
        self._desired = bool(self.cfg.get("ingame_overlay_start"))
        self._clock_desired = bool(self.cfg.get("ingame_clock"))
        self._clock_hotkey_on = False
        self._hotkey_seen = False
        self._clock_hotkey_seen = False
        self._last_seen = 0.0
        self._seen = False
        self._win = None
        self._next_poll = 0.0
        self._next_stats = 0.0
        self._next_config = 0.0
        self._next_status = 0.0
        self._gpu = None
        self._next_gpu = 0.0
        self.fps = FpsMeter()
        self._next_fps = 0.0
        self._fps_active = False
        self._fps_available_seen = bool(self.fps.available)
        self._afk_next = None
        self._afk_minutes = int(self.cfg.get("ingame_afk_minutes") or 5)
        self._afk_last = 0.0
        self._closing = False
        self._tick_job = None
        self._initialized = False

    def start(self):
        hotkey_ok = self.hotkey.start()
        self._hotkey_seen = hotkey_ok
        self.overlay.set_hotkey_ok(hotkey_ok, self.hotkey.label)
        if not hotkey_ok:
            helper_log(f"hotkey: {self.hotkey.error}")
        if self.cfg.get("ingame_clock"):
            clock_ok = self.clock_hotkey.start()
            self._clock_hotkey_on = True
            self._clock_hotkey_seen = clock_ok
            if not clock_ok:
                helper_log(f"hotkey часов: {self.clock_hotkey.error}")
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)
        self._initialized = True
        if (not self.cfg.get("ingame_overlay")
                and not self.cfg.get("ingame_island")
                and not self.cfg.get("ingame_clock")
                and not self.cfg.get("ingame_afk")):
            helper_log("нет включённых функций — выход")
            self._write_status()
            self.root.after(50, self.shutdown)
            return
        if self.cfg.get("ingame_island") or self.cfg.get("ingame_overlay"):
            self._ensure_media()
        if self.cfg.get("ingame_afk"):
            self._afk_next = time.monotonic() + afk_interval_seconds(
                self._afk_minutes)
        self._write_status()
        self._tick_job = self.root.after(120, self._tick)

    def _write_status(self):
        now = time.monotonic()
        win = self._win
        afk_next_in = None
        if (self.cfg.get("ingame_afk") and self._afk_next is not None
                and win and win.get("foreground")):
            afk_next_in = max(0, int(self._afk_next - now))
        write_status({
            "running": not self._closing,
            "roblox": bool(win),
            "foreground": bool(win and win.get("foreground")),
            "overlay": bool(self.overlay.visible),
            "overlay_desired": bool(self._desired),
            "island": bool(self.island.visible),
            "island_desired": bool(self._desired),
            "clock": bool(self.clock.visible),
            "clock_desired": bool(self._clock_desired),
            "clock_hotkey": bool(self.clock_hotkey.ok),
            "clock_hotkey_spec": self.clock_hotkey.spec,
            "clock_hotkey_label": self.clock_hotkey.label,
            "clock_hotkey_error": "" if self.clock_hotkey.ok
            else self.clock_hotkey.error,
            "hotkey": bool(self.hotkey.ok),
            "hotkey_spec": self.hotkey.spec,
            "hotkey_label": self.hotkey.label,
            "hotkey_error": "" if self.hotkey.ok else self.hotkey.error,
            "afk": bool(self.cfg.get("ingame_afk")),
            "afk_mode": self.cfg.get("ingame_afk_mode") or "mouse",
            "afk_key": self.cfg.get("ingame_afk_key") or "space",
            "afk_next_in": afk_next_in,
            "afk_last": self._afk_last,
            "fps": (self.fps.value
                    if self.fps.available and self.overlay.visible else None),
        })

    def _tick(self):
        if self._closing:
            return
        try:
            if kernel32.WaitForSingleObject(self.stop_handle, 0) == 0:
                self.shutdown()
                return
            if kernel32.WaitForSingleObject(self.toggle_handle, 0) == 0:
                self._toggle()
            while True:
                try:
                    event = self.events.get_nowait()
                except queue.Empty:
                    break
                if event == "toggle":
                    self._toggle()
                elif event == "clock":
                    self._toggle_clock()
            if self.hotkey.ok != self._hotkey_seen:
                self._hotkey_seen = bool(self.hotkey.ok)
                self.overlay.set_hotkey_ok(self._hotkey_seen,
                                           self.hotkey.label)
                helper_log(
                    f"hotkey: {self.hotkey.label} свободен"
                    if self._hotkey_seen else f"hotkey: {self.hotkey.error}")
            if self.clock_hotkey.ok != self._clock_hotkey_seen:
                self._clock_hotkey_seen = bool(self.clock_hotkey.ok)
                helper_log(
                    f"hotkey часов: {self.clock_hotkey.label} свободен"
                    if self._clock_hotkey_seen
                    else f"hotkey часов: {self.clock_hotkey.error}")
            now = time.monotonic()
            if now >= self._next_poll:
                self._next_poll = now + 0.5
                self._win = find_roblox_window()
                if self._win or roblox_pids():
                    self._seen = True
                    self._last_seen = now
                elif self._seen and now - self._last_seen > 90:
                    helper_log("Roblox закрыт — выход")
                    self.shutdown()
                    return
                self._apply_overlay()
            if now >= self._next_config:
                self._next_config = now + 4
                self._reload_config()
                if self._closing:
                    return
            self._check_afk(now)
            if now >= self._next_gpu:
                self._next_gpu = now + 2
                self._gpu = gpu_snapshot()
            if now >= self._next_stats:
                self._next_stats = now + 1
                if self.overlay.visible:
                    afk_text = self._afk_text(now)
                    self.overlay.update_stats(memory_snapshot(), self._gpu,
                                              afk_text)
                if self.clock.visible:
                    self.clock.set_clock()
            if self.overlay.visible and self._win:
                if now >= self._next_fps:
                    self._next_fps = now + FPS_INTERVAL
                    self._fps_active = True
                    value = self.fps.sample(self._win.get("pid"),
                                            self._win.get("hwnd"), now)
                    if value is not None:
                        self.overlay.set_fps(value)
            elif self._fps_active:
                self._fps_active = False
                self._next_fps = 0.0
                self.fps.reset()
            if self.fps.available != self._fps_available_seen:
                self._fps_available_seen = bool(self.fps.available)
                if not self._fps_available_seen:
                    self.overlay.set_fps(None)
                    helper_log("FPS: датчик WDDM недоступен")
            if now >= self._next_status:
                self._next_status = now + 2
                self._write_status()
            self._tick_job = self.root.after(120, self._tick)
        except Exception as exc:  # noqa: BLE001
            helper_log(f"tick: {type(exc).__name__}: {exc}")
            try:
                self._tick_job = self.root.after(500, self._tick)
            except tk.TclError:
                self.shutdown()

    def _apply_overlay(self):
        enabled = bool(self.cfg.get("ingame_overlay"))
        show = (enabled and self._desired and self._win
                and self._win.get("foreground"))
        if show:
            self.overlay.update_stats(
                memory_snapshot(), self._gpu, self._afk_text(time.monotonic()))
            self.overlay.set_fps(self.fps.value)
            self.overlay.show(self._win.get("rect"))
        else:
            self.overlay.hide()
        self._apply_island()
        self._apply_clock()

    def _apply_island(self):
        enabled = bool(self.cfg.get("ingame_island"))
        auto = bool(self.cfg.get("ingame_overlay"))
        state = self.media.snapshot() if self.media is not None else None
        playing = bool(state and state.get("playing"))
        show = (self._desired and self._win and self._win.get("foreground")
                and (enabled or (auto and playing)))
        if show:
            if self.media is None:
                self._ensure_media()
            self.island.set_media(state)
            self.island.set_clock()
            self.island.show(self._win.get("rect"))
        else:
            self.island.hide()

    def _apply_clock(self):
        enabled = bool(self.cfg.get("ingame_clock"))
        show = (enabled and self._clock_desired and self._win
                and self._win.get("foreground"))
        if show:
            self.clock.set_clock()
            offset = (ISLAND_H + 8) if self.island.visible else 0
            self.clock.show(self._win.get("rect"), offset)
        else:
            self.clock.hide()

    def _ensure_media(self):
        if self.media is not None or not IS_WINDOWS:
            return
        self.media = MediaWatcher()
        self.media.start()

    def _drop_media(self):
        if self.media is None:
            return
        self.media.stop()
        self.media = None

    def _toggle(self):
        if not (self.cfg.get("ingame_overlay")
                or self.cfg.get("ingame_island")):
            return
        self._desired = not self._desired
        self._apply_overlay()
        self._write_status()
        helper_log(f"HUD: {'показан' if self._desired else 'скрыт'}")

    def _toggle_clock(self):
        if not self.cfg.get("ingame_clock"):
            return
        self._clock_desired = not self._clock_desired
        self._apply_clock()
        self._write_status()
        helper_log(
            f"Часы: {'показаны' if self._clock_desired else 'скрыты'}")

    def _reload_config(self):
        if core is None:
            return
        fresh = core.load_config()
        if not isinstance(fresh, dict):
            return
        changed = any(fresh.get(key) != self.cfg.get(key) for key in (
            "ingame_overlay", "ingame_island", "ingame_clock", "ingame_afk",
            "ingame_afk_mode", "ingame_afk_key", "ingame_afk_minutes",
            "ingame_overlay_corner", "ingame_hotkey",
            "ingame_clock_hotkey", "accent"))
        if (fresh.get("ingame_overlay_start")
                and not self.cfg.get("ingame_overlay_start")):
            self._desired = True
        if fresh.get("ingame_clock") and not self.cfg.get("ingame_clock"):
            self._clock_desired = True
        self.cfg = fresh
        self.overlay.apply_config(fresh)
        self.clock.apply_config(fresh)
        self._reload_hotkey()
        if fresh.get("ingame_island") or fresh.get("ingame_overlay"):
            self._ensure_media()
        else:
            self._drop_media()
        if fresh.get("ingame_afk"):
            minutes = int(fresh.get("ingame_afk_minutes") or 5)
            if minutes != self._afk_minutes:
                self._afk_minutes = minutes
                self._afk_next = time.monotonic() + afk_interval_seconds(
                    minutes)
        else:
            self._afk_next = None
        if changed:
            helper_log("настройки обновлены")
        if (not fresh.get("ingame_overlay") and not fresh.get("ingame_island")
                and not fresh.get("ingame_clock")
                and not fresh.get("ingame_afk")):
            helper_log("функции выключены — выход")
            self.shutdown()

    def _reload_hotkey(self):
        spec = normalize_hotkey(self.cfg.get("ingame_hotkey")) or DEFAULT_HOTKEY
        if spec == self.hotkey.spec:
            self.overlay.set_hotkey_ok(self.hotkey.ok, self.hotkey.label)
        else:
            self.hotkey.stop()
            self.hotkey = HotkeyThread(self.events, spec)
            ok = self.hotkey.start()
            self._hotkey_seen = bool(ok)
            self.overlay.set_hotkey_ok(ok, self.hotkey.label)
            helper_log(f"hotkey: {self.hotkey.label} "
                       + ("свободен" if ok else self.hotkey.error))
        clock_spec = (normalize_hotkey(self.cfg.get("ingame_clock_hotkey"))
                      or DEFAULT_CLOCK_HOTKEY)
        clock_wanted = bool(self.cfg.get("ingame_clock"))
        if not clock_wanted:
            if self._clock_hotkey_on:
                self.clock_hotkey.stop()
                self.clock_hotkey = HotkeyThread(
                    self.events, clock_spec, hotkey_id=HOTKEY_ID_CLOCK,
                    event="clock")
                self._clock_hotkey_on = False
                self._clock_hotkey_seen = False
        elif not self._clock_hotkey_on:
            self.clock_hotkey = HotkeyThread(
                self.events, clock_spec, hotkey_id=HOTKEY_ID_CLOCK,
                event="clock")
            ok = self.clock_hotkey.start()
            self._clock_hotkey_on = True
            self._clock_hotkey_seen = bool(ok)
            helper_log(f"hotkey часов: {self.clock_hotkey.label} "
                       + ("свободен" if ok else self.clock_hotkey.error))
        elif clock_spec != self.clock_hotkey.spec:
            self.clock_hotkey.stop()
            self.clock_hotkey = HotkeyThread(
                self.events, clock_spec, hotkey_id=HOTKEY_ID_CLOCK,
                event="clock")
            ok = self.clock_hotkey.start()
            self._clock_hotkey_seen = bool(ok)
            helper_log(f"hotkey часов: {self.clock_hotkey.label} "
                       + ("свободен" if ok else self.clock_hotkey.error))

    def _afk_text(self, now):
        if not self.cfg.get("ingame_afk"):
            return "AFK выкл."
        if not self._win:
            return "AFK: ждёт Roblox"
        if not self._win.get("foreground"):
            return "AFK: пауза"
        if self._afk_next is None:
            return "AFK: готов"
        return f"AFK {format_countdown(self._afk_next - now)}"

    def _check_afk(self, now):
        if not self.cfg.get("ingame_afk"):
            self._afk_next = None
            return
        if self._afk_next is None:
            self._afk_next = now + afk_interval_seconds(self._afk_minutes)
            return
        if not self._win or not self._win.get("foreground"):
            return
        if now < self._afk_next:
            return
        idle = user_idle_seconds()
        if idle is not None and idle < 60:
            self._afk_next = now + 60
            return
        mode = self.cfg.get("ingame_afk_mode") or "mouse"
        key = self.cfg.get("ingame_afk_key") or "space"
        if send_micro_action(mode, key):
            self._afk_last = time.time()
            self._afk_next = now + afk_interval_seconds(self._afk_minutes)
            helper_log(f"AFK: микро-действие ({mode})")
        else:
            self._afk_next = now + 30

    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        if self._tick_job is not None:
            try:
                self.root.after_cancel(self._tick_job)
            except tk.TclError:
                pass
            self._tick_job = None
        try:
            self._write_status()
        except Exception:  # noqa: BLE001
            pass
        self.hotkey.stop()
        self.clock_hotkey.stop()
        self.fps.close()
        self._drop_media()
        self.overlay.destroy()
        try:
            self.island.destroy()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.clock.destroy()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.root.quit()
        except tk.TclError:
            pass
        self.overlay = None

    def run(self):
        self.overlay = Overlay(self.root, self.cfg)
        self.root.withdraw()
        self.start()
        self.root.mainloop()


def _create_events():
    if not IS_WINDOWS:
        return None, None, "только Windows"
    ctypes.set_last_error(0)
    stop_handle = kernel32.CreateEventW(None, True, False, STOP_EVENT)
    if not stop_handle:
        return None, None, "не удалось создать событие остановки"
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(stop_handle)
        return None, None, "уже работает"
    ctypes.set_last_error(0)
    toggle_handle = kernel32.CreateEventW(None, False, False, TOGGLE_EVENT)
    if not toggle_handle:
        kernel32.CloseHandle(stop_handle)
        return None, None, "не удалось создать событие переключения"
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(toggle_handle)
        kernel32.CloseHandle(stop_handle)
        return None, None, "уже работает"
    return stop_handle, toggle_handle, ""


def main():
    if not IS_WINDOWS:
        return 1
    if core is not None:
        core.remember_bundle_dir()
    stop_handle, toggle_handle, error = _create_events()
    if error:
        helper_log(error)
        return 0
    _enable_dpi_awareness()
    root = tk.Tk()
    helper = None
    try:
        helper = InGameHelper(root, stop_handle, toggle_handle)
        root.withdraw()
        helper.start()
        root.mainloop()
    except Exception as exc:  # noqa: BLE001
        helper_log(f"fatal: {type(exc).__name__}: {exc}")
        try:
            if helper is not None:
                helper.shutdown()
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            if helper is not None:
                helper.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            write_status({"running": False})
        except Exception:  # noqa: BLE001
            pass
        try:
            root.destroy()
        except tk.TclError:
            pass
        kernel32.CloseHandle(toggle_handle)
        kernel32.CloseHandle(stop_handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
