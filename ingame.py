#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""In-game helper: safe click-through overlay and anti-AFK for Roblox."""

import ctypes
import json
import os
import queue
import random
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont

try:
    import core
except ImportError:
    core = None

IS_WINDOWS = os.name == "nt"

STOP_EVENT = r"Local\MaklyxaLauncherInGameStop_v1"
TOGGLE_EVENT = r"Local\MaklyxaLauncherInGameToggle_v1"
HOTKEY_ID = 0xB10
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
VK_F1 = 0x70
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
ERROR_ALREADY_EXISTS = 183
TH32CS_SNAPPROCESS = 0x00000002
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
ROBLOX_EXE = "robloxplayerbeta.exe"
FPS_NOTE = "Roblox: Shift+F5"

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


def start_helper(wait=2.5):
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
            cwd=str(Path(__file__).resolve().parent))
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
    def __init__(self, events):
        self.events = events
        self.ok = False
        self.error = ""
        self._thread = None
        self._ready = threading.Event()
        self._tid = 0

    def start(self):
        if not IS_WINDOWS:
            self.error = "только Windows"
            return False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(1.2)
        return self.ok

    def _run(self):
        self._tid = int(kernel32.GetCurrentThreadId())
        try:
            self.ok = bool(user32.RegisterHotKey(
                None, HOTKEY_ID, MOD_SHIFT | MOD_NOREPEAT, VK_F1))
        except (OSError, AttributeError):
            self.ok = False
        if not self.ok:
            self.error = "Shift+F1 занят другой программой"
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        while True:
            result = int(user32.GetMessageW(ctypes.byref(msg), None, 0, 0))
            if result <= 0:
                break
            if msg.message == WM_HOTKEY and int(msg.wParam) == HOTKEY_ID:
                self.events.put("toggle")
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        try:
            user32.UnregisterHotKey(None, HOTKEY_ID)
        except (OSError, AttributeError):
            pass

    def stop(self):
        if not IS_WINDOWS or self._thread is None:
            return
        try:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        except (OSError, AttributeError):
            pass
        self._thread.join(timeout=0.6)


class Overlay:
    BG = "#0b0f14"
    BORDER = "#243040"
    TEXT = "#e7eef7"
    MUTED = "#8fa3b8"
    FONT = "Segoe UI"

    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.FONT = self._load_overlay_font(cfg)
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
        self._build()
        self.top.update_idletasks()
        self._hwnd = int(self.top.winfo_id())
        self._root_hwnd = int(
            user32.GetAncestor(self._hwnd, 2) or self._hwnd) if IS_WINDOWS \
            else self._hwnd
        self._apply_style()
        self._hotkey_ok = True

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

    def _load_overlay_font(self, cfg):
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
                         for name in tkfont.families(self.root)}
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

    def _build(self):
        self.fps_var = tk.StringVar(value="—")
        self.fps_sub_var = tk.StringVar(value=FPS_NOTE)
        self.gpu_var = tk.StringVar(value="—")
        self.gpu_sub_var = tk.StringVar(value="нет датчика")
        self.ram_var = tk.StringVar(value="—")
        self.ram_sub_var = tk.StringVar(value="")
        self.time_var = tk.StringVar(value=time.strftime("%H:%M:%S"))
        self.hint_var = tk.StringVar(value="Shift+F1 скрыть")
        self.afk_var = tk.StringVar(value="")

        body = tk.Frame(self.top, bg=self.BG,
                        highlightbackground=self.BORDER,
                        highlightthickness=1)
        body.pack(fill="both", expand=True)

        head = tk.Frame(body, bg=self.BG)
        head.pack(fill="x", padx=12, pady=(9, 0))
        self.brand_label = tk.Label(
            head, text="MAKLYXA", bg=self.BG, fg=self._accent,
            font=(self.FONT, 8, "bold"))
        self.brand_label.pack(side="left")
        tk.Label(head, text="  IN-GAME", bg=self.BG, fg=self.MUTED,
                 font=(self.FONT, 8, "bold")).pack(side="left")
        tk.Label(head, textvariable=self.time_var, bg=self.BG,
                 fg=self.TEXT, font=(self.FONT, 11, "bold")).pack(
                     side="right")

        tk.Frame(body, bg=self.BORDER, height=1).pack(
            fill="x", padx=12, pady=(7, 0))

        grid = tk.Frame(body, bg=self.BG)
        grid.pack(fill="x", padx=12, pady=(6, 0))
        for index in range(3):
            grid.grid_columnconfigure(index, weight=1)
        self._metric(grid, 0, "FPS", self.fps_var, self.fps_sub_var)
        self._metric(grid, 1, "GPU", self.gpu_var, self.gpu_sub_var)
        self._metric(grid, 2, "ПАМЯТЬ", self.ram_var, self.ram_sub_var)

        foot = tk.Frame(body, bg=self.BG)
        foot.pack(fill="x", padx=12, pady=(5, 8))
        tk.Label(foot, textvariable=self.hint_var, bg=self.BG,
                 fg=self.MUTED, font=(self.FONT, 7)).pack(side="left")
        tk.Label(foot, textvariable=self.afk_var, bg=self.BG,
                 fg=self.MUTED, font=(self.FONT, 7)).pack(side="right")

    def _metric(self, parent, column, label, value_var, sub_var):
        cell = tk.Frame(parent, bg=self.BG)
        cell.grid(row=0, column=column, sticky="w", padx=(0, 4))
        tk.Label(cell, text=label, bg=self.BG, fg=self.MUTED,
                 font=(self.FONT, 7, "bold")).pack(anchor="w")
        self._value_labels = getattr(self, "_value_labels", [])
        value = tk.Label(cell, textvariable=value_var, bg=self.BG,
                         fg=self._accent, font=(self.FONT, 15, "bold"),
                         width=7, anchor="w")
        value.pack(anchor="w")
        self._value_labels.append(value)
        tk.Label(cell, textvariable=sub_var, bg=self.BG, fg=self.MUTED,
                 font=(self.FONT, 7), width=16, anchor="w").pack(anchor="w")

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

    def set_hotkey_ok(self, ok):
        self._hotkey_ok = bool(ok)
        self.hint_var.set("Shift+F1 скрыть" if ok
                          else "Shift+F1 занят")

    def apply_config(self, cfg):
        self.cfg = cfg
        accent = self._accent_color(cfg)
        if accent != self._accent:
            self._accent = accent
            self.brand_label.configure(fg=accent)
            for label in getattr(self, "_value_labels", []):
                label.configure(fg=accent)

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
        self.top.geometry(f"{x:+d}{y:+d}")
        previous = (user32.GetForegroundWindow() if IS_WINDOWS else None)
        if not self._visible:
            self.top.deiconify()
            self.top.attributes("-topmost", True)
            self._apply_style()
            self._visible = True
        else:
            self.top.lift()
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
        self.afk_var.set(afk_text or "")

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
        self.events = queue.Queue()
        self.hotkey = HotkeyThread(self.events)
        self._desired = bool(self.cfg.get("ingame_overlay_start"))
        self._started = time.monotonic()
        self._last_seen = 0.0
        self._seen = False
        self._win = None
        self._next_poll = 0.0
        self._next_stats = 0.0
        self._next_config = 0.0
        self._next_status = 0.0
        self._gpu = None
        self._next_gpu = 0.0
        self._afk_next = None
        self._afk_minutes = int(self.cfg.get("ingame_afk_minutes") or 5)
        self._afk_last = 0.0
        self._closing = False
        self._tick_job = None
        self._initialized = False

    def start(self):
        hotkey_ok = self.hotkey.start()
        self.overlay.set_hotkey_ok(hotkey_ok)
        if not hotkey_ok:
            helper_log(f"hotkey: {self.hotkey.error}")
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)
        self._initialized = True
        if (not self.cfg.get("ingame_overlay")
                and not self.cfg.get("ingame_afk")):
            helper_log("нет включённых функций — выход")
            self._write_status()
            self.root.after(50, self.shutdown)
            return
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
            "hotkey": bool(self.hotkey.ok),
            "hotkey_error": "" if self.hotkey.ok else self.hotkey.error,
            "afk": bool(self.cfg.get("ingame_afk")),
            "afk_mode": self.cfg.get("ingame_afk_mode") or "mouse",
            "afk_key": self.cfg.get("ingame_afk_key") or "space",
            "afk_next_in": afk_next_in,
            "afk_last": self._afk_last,
        })

    def _tick(self):
        if self._closing:
            return
        try:
            if kernel32.WaitForSingleObject(self.stop_handle, 0) == 0:
                self.shutdown()
                return
            while True:
                try:
                    event = self.events.get_nowait()
                except queue.Empty:
                    break
                if event == "toggle":
                    self._toggle()
            now = time.monotonic()
            if now >= self._next_poll:
                self._next_poll = now + 0.5
                self._win = find_roblox_window()
                if self._win:
                    self._seen = True
                    self._last_seen = now
                elif self._seen and now - self._last_seen > 90:
                    helper_log("Roblox закрыт — выход")
                    self.shutdown()
                    return
                elif not self._seen and now - self._started > 240:
                    helper_log("Roblox не появился — выход")
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
            self.overlay.show(self._win.get("rect"))
        else:
            self.overlay.hide()

    def _toggle(self):
        if not self.cfg.get("ingame_overlay"):
            return
        self._desired = not self._desired
        self._apply_overlay()
        self._write_status()
        helper_log(f"оверлей: {'показан' if self._desired else 'скрыт'}")

    def _reload_config(self):
        if core is None:
            return
        fresh = core.load_config()
        if not isinstance(fresh, dict):
            return
        changed = any(fresh.get(key) != self.cfg.get(key) for key in (
            "ingame_overlay", "ingame_afk", "ingame_afk_mode",
            "ingame_afk_key", "ingame_afk_minutes",
            "ingame_overlay_corner", "accent"))
        if (fresh.get("ingame_overlay_start")
                and not self.cfg.get("ingame_overlay_start")):
            self._desired = True
        self.cfg = fresh
        self.overlay.apply_config(fresh)
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
        if not fresh.get("ingame_overlay") and not fresh.get("ingame_afk"):
            helper_log("функции выключены — выход")
            self.shutdown()

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
        self.overlay.destroy()
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
