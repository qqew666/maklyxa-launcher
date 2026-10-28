#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MaklyxaLauncher — интерфейс лаунчера (Tkinter).

UI v3 (полный редизайн):
  * своё окно без рамки: перетаскивание за шапку, ресайз за края,
    максимизация двойным кликом, кнопки свернуть/развернуть/закрыть;
  * боковое меню с группами разделов, акцентной подсветкой активного
    пункта и статусом Roblox внизу;
  * новая нейтральная палитра трёх тем, верхняя панель с брендом,
    версией и быстрым переключением темы;
  * заголовки страниц-«героев», карточки с акцентной чертой,
    кнопки с плавной анимацией цвета при наведении;
  * компактная статусная строка и переработанный экран запуска игры.

Ядро (логика) — в core.py.
Запуск: python launcher.py [--page game|favorites|gallery|ingame|flags|
                                   cursors|themes|launchstyle|settings|log]
                            [--theme dark|light|glass]
                            [--ingame]
        --ingame — служебный режим внутриигрового помощника (оверлей и AFK).
"""

import base64
import ctypes
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser, filedialog, messagebox, ttk

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageTk
    HAVE_PIL = True
except Exception:  # Pillow не обязателен для запуска
    HAVE_PIL = False

import ingame as ingame_mod

import core

from core import (
    ACCENT_PRESETS, APP_NAME, BOOT_BOOLS_FROST, BOOT_BOOLS_SHARED,
    BUNDLE_DIR, CONFIG_DIR, CONFIG_PATH, CREATE_NO_WINDOW,
    CURSOR_SLOTS, CUSTOMIZE_DIR,
    DEFAULT_CONFIG, DEFAULT_THEME,
    DISCORD_PORTAL_URL, DISCOVER_URL, FPS_CHOICES, FLAG_API, FLAG_FPS, FLAG_QUALITY,
    GRAPHICS_MODES, HTTP_HEADERS, INSTALLER_URL,
    IMAGE_ONLY_ACCENT, IMAGE_ONLY_PALETTE, LAUNCH_DIR, RELEASES_URL,
    THEMES, THEME_ORDER, UPDATE_DIR, VERSION, WINDOW_TITLE,
    ROBLOX_APP_THEMES,
    THEME_SETS_DIR, THEME_TINTS,
    account_avatar_path, account_session, accounts_state, add_account,
    acquire_single_instance, apply_cursor_set, apply_fastflags,
    apply_launch_icon_to_shortcuts, bootstrapper_running, browser_login,
    build_gallery_groups, build_theme_palette, check_launcher_update,
    client_settings_paths, close_roblox_players, collect_play_sessions,
    configured_roblox_exe,
    copy_current_cursor_set,
    create_cursor_set, create_theme_set, cursor_install_roots,
    cursor_set_files, cursor_set_warnings, cursor_slot_path,
    cursor_state_info, datacenter_regions, delete_cursor_set,
    delete_theme_set, ensure_cursor_sets_dir, ensure_theme_sets_dir,
    export_cursor_set, export_theme_set, fetch_game_icon, fetch_game_info,
    fastflag_values, file_version_text, find_bootstrapper_settings,
    find_player_exe,
    focus_roblox_window, fmt_num, gallery_assign, gallery_cache_load,
    gallery_cache_save, gallery_seed_places, gallery_stats,
    gallery_thumb_path, game_launch_url, hit_test, import_cursor_set,
    import_launch_icon, import_launch_sound, import_theme_set,
    import_ui_font, inspect_ui_font, launch_game, launch_game_as,
    launch_icon_state, launch_sound_path, list_cursor_sets, list_theme_sets,
    load_config, load_private_font, load_theme_palette, mb, merge_flags_into,
    migrate_old_config, parse_place_id, png_info, player_pids,
    play_launch_sound, probe_protocol, protect_secret, read_json_dict,
    read_roblox_cookie_store, remove_account, remove_cursor_slot,
    remove_launch_icon, remove_launch_sound, remove_theme_image,
    remove_ui_font, rename_cursor_set, rename_theme_set, resize_geometry,
    resolve_gallery_places, restore_custom_cursors,
    restore_launch_icon_shortcuts, roblox_deep_link, roblox_status,
    roblox_theme_state, roblox_version_entries, roblox_versions_dir,
    roblox_window_pids,
    scan_screenshots,
    screenshots_dir, save_config, selected_roblox_exe, set_active_account,
    set_cursor_slot,
    set_roblox_session, set_roblox_theme, set_theme_image, shade,
    shortcut_backup_count,
    stop_launch_sound, theme_set_info, trunc,
    ui_font_path, ui_font_state, unload_private_font, unprotect_secret,
    update_theme_set, valid_cursor_set_name, valid_discord_app_id,
    valid_geometry, valid_theme_set_name, version_tuple,
    wait_for_roblox_window, wait_players_gone,
    work_area,
    write_json_dict, DiscordPresence, discord_activity, discord_frame,
    download_update, update_file_name, update_file_valid, UpdateCancelled,
)

ICON_PNG = BUNDLE_DIR / "icon.png"

TITLEBAR_H = 48
SIDEBAR_W = 216
MIN_W, MIN_H = 960, 640
BORDER_PX = 6
DEFAULT_SIZE = (1080, 760)
HEADER_H = 126
HEADER_H_COMPACT = 76
THEME_PREVIEW_H = 148
GALLERY_TILE_W = 224
GALLERY_TILE_H = 126

UI_FONT = "Segoe UI"


def _resolve_tk_family(root, candidates):
    """Найти в Tk первое из имён семейств (без учёта регистра)."""
    try:
        available = {str(name).casefold(): str(name)
                     for name in tkfont.families(root)}
    except tk.TclError:
        return ""
    for candidate in candidates:
        name = str(candidate or "").strip().casefold()
        if name and name in available:
            return available[name]
    return ""


def _prepare_preview_font(root, cfg):
    """Подключить сохранённый шрифт для превью; UI лаунчера — Segoe UI."""
    path = ui_font_path()
    if path is None:
        return None, ""
    if not load_private_font(path):
        return None, ""
    state = ui_font_state()
    family = _resolve_tk_family(
        root, list(state["families"]) + [cfg.get("ui_font_family") or ""])
    if not family:
        unload_private_font(path)
        return None, ""
    return path, family


LAUNCH_STYLE_LABELS = {"off": "Выкл", "minimal": "Минимальный",
                       "card": "Карточка"}
LAUNCH_STYLE_NOTES = {
    "off": "Экран запуска не показывается — только звук, если включён.",
    "minimal": "Узкая полоса с иконкой и названием игры.",
    "card": "Карточка с иконкой, названием и статусом запуска.",
}

CURSORS = {
    "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
    "e": "sb_h_double_arrow", "w": "sb_h_double_arrow",
    "ne": "sizing", "nw": "sizing", "se": "sizing", "sw": "sizing",
}

NAV_ITEMS = [
    ("game", "▶", "Игра"),
    ("accounts", "◉", "Аккаунты"),
    ("favorites", "★", "Избранное"),
    ("gallery", "▦", "Галерея"),
    ("ingame", "◳", "В игре"),
    ("flags", "⚑", "Флаги"),
    ("cursors", "◈", "Курсоры"),
    ("themes", "◐", "Темы"),
    ("launchstyle", "✎", "Кастомизация"),
    ("settings", "⚙", "Настройки"),
    ("log", "≡", "Журнал"),
]

NAV_GROUPS = [
    ("ЗАПУСК", ("game", "accounts", "favorites")),
    ("МЕДИА", ("gallery",)),
    ("В ИГРЕ", ("ingame",)),
    ("ПРЕДУСТАНОВЛЕННЫЕ\nМОДИФИКАЦИИ",
     ("flags", "cursors", "themes", "launchstyle")),
    ("СИСТЕМА", ("settings", "log")),
]

DETACHED_PROCESS = 0x00000008

UPDATE_SCRIPT = r"""$ErrorActionPreference = 'Stop'
$WaitPid = @@PID@@
$Target = @@TARGET@@
$NewFile = @@NEWFILE@@
$Msi = @@MSI@@
$deadline = (Get-Date).AddSeconds(120)
while ($WaitPid -gt 0 -and (Get-Date) -lt $deadline) {
    if (-not (Get-Process -Id $WaitPid -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 400
}
Start-Sleep -Milliseconds 500
if ($Msi -ne '') {
    $ok = $false
    try {
        $p = Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/i', ('"' + $Msi + '"'), '/passive', '/norestart') -Verb RunAs -Wait -PassThru
        if ($p.ExitCode -eq 0 -or $p.ExitCode -eq 3010) { $ok = $true }
    } catch { $ok = $false }
    if ($ok) {
        Remove-Item -LiteralPath $Msi -Force -ErrorAction SilentlyContinue
        if ($Target -ne '') {
            Start-Process -FilePath $Target -WorkingDirectory (Split-Path -Parent $Target)
        }
    }
    exit 0
}
$copied = $false
while ((Get-Date) -lt $deadline) {
    try {
        Copy-Item -LiteralPath $NewFile -Destination $Target -Force -ErrorAction Stop
        $copied = $true
        break
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $copied) {
    try {
        $cmd = "Copy-Item -LiteralPath '" + ($NewFile -replace "'", "''") + "' -Destination '" + ($Target -replace "'", "''") + "' -Force"
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-Command', $cmd) -Wait
        $copied = $true
    } catch {
        $copied = $false
    }
}
if ($copied) {
    Remove-Item -LiteralPath $NewFile -Force -ErrorAction SilentlyContinue
    Start-Process -FilePath $Target -WorkingDirectory (Split-Path -Parent $Target)
}
exit 0
"""


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def build_update_script(wait_pid, target="", new_file="", msi=""):
    """PowerShell-скрипт: дождаться выхода лаунчера, заменить файл, запустить."""
    return (UPDATE_SCRIPT
            .replace("@@PID@@", str(int(wait_pid)))
            .replace("@@TARGET@@", _ps_quote(target))
            .replace("@@NEWFILE@@", _ps_quote(new_file))
            .replace("@@MSI@@", _ps_quote(msi)))


def round_window_corners(hwnd, radius=12):
    """Скруглить окно без рамки через регион Windows."""
    if os.name != "nt" or not hwnd:
        return False
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        rect = wintypes.RECT()
        if not user32.GetWindowRect(ctypes.c_void_p(hwnd),
                                    ctypes.byref(rect)):
            return False
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return False
        region = gdi32.CreateRoundRectRgn(
            0, 0, width + 1, height + 1, radius, radius)
        if not region:
            return False
        # регион переходит во владение окна — удалять его нельзя
        user32.SetWindowRgn(ctypes.c_void_p(hwnd), region, True)
        return True
    except (AttributeError, OSError):
        return False


def square_window_corners(hwnd):
    """Вернуть окну прямоугольную форму (максимизация)."""
    if os.name != "nt" or not hwnd:
        return False
    try:
        ctypes.windll.user32.SetWindowRgn(
            ctypes.c_void_p(hwnd), None, True)
        return True
    except (AttributeError, OSError):
        return False


# ---- безрамочное окно ----------------------------------------------------------


class FramelessWindow:
    """Своё окно: перетаскивание, ресайз за края, максимизация, кнопки."""

    def __init__(self, root, min_size=(MIN_W, MIN_H)):
        self.root = root
        self.min_w, self.min_h = min_size
        self.border = BORDER_PX
        self.title_h = TITLEBAR_H
        self.maximized = False
        self.excluded = set()          # виджеты, где drag/resize не работает
        self.on_max_change = None      # колбэк смены состояния максимизации
        self.on_alt_tab = None         # колбэк ухода в другое окно через Alt+Tab
        self._mode = None              # 'move' | 'resize'
        self._zone = ""
        self._start = None
        self._prev_geom = None
        self._cursor = ""
        self._styled = False
        self._alt_seen = 0.0
        self._was_foreground = True
        self._region_job = None

        root.overrideredirect(True)
        root.bind("<Map>", self._on_map, add="+")
        root.bind("<Configure>", self._on_configure, add="+")
        root.bind("<Button-1>", self._on_press, add="+")
        root.bind("<B1-Motion>", self._on_drag, add="+")
        root.bind("<ButtonRelease-1>", self._on_release, add="+")
        root.bind("<Motion>", self._on_hover, add="+")
        root.bind("<Double-Button-1>", self._on_double, add="+")
        self._init_switch_watch()
        root.after(70, self._watch_switch)

    # -- уход в другое окно через Alt+Tab -----------------------------------

    def _init_switch_watch(self):
        if os.name != "nt":
            return
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
            user32.GetAsyncKeyState.restype = ctypes.c_short
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        except (AttributeError, OSError):
            pass

    def _foreground_elsewhere(self):
        if os.name != "nt":
            return False
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return pid.value != os.getpid()
        except (AttributeError, OSError):
            return False

    def _watch_switch(self):
        """Закрывает лаунчер, если он потерял фокус из-за Alt+Tab."""
        if self.root is None:
            return
        try:
            self.root.after(70, self._watch_switch)
        except tk.TclError:
            return
        now = time.monotonic()
        try:
            if ctypes.windll.user32.GetAsyncKeyState(0x12) & 0x8000:
                self._alt_seen = now
        except (AttributeError, OSError):
            return
        if not self._foreground_elsewhere():
            self._was_foreground = True
            return
        if not self._was_foreground:
            return
        self._was_foreground = False
        if now - self._alt_seen > 1.2:
            return
        if callable(self.on_alt_tab):
            try:
                self.on_alt_tab()
            except tk.TclError:
                pass

    # -- окно в панели задач -------------------------------------------------

    def _hwnd(self):
        try:
            return ctypes.windll.user32.GetParent(self.root.winfo_id())
        except Exception:
            return None

    def _on_map(self, _event=None):
        """Окно без рамки не попадает в панель задач — добавляем стиль.

        Вызывается при каждом Map: attributes('-alpha') у Tk сбрасывает
        exstyle (остаётся только WS_EX_LAYERED), а пересборка UI при смене
        темы выставляет alpha заново.
        """
        hwnd = self._hwnd()
        if not hwnd:
            return
        GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x80, 0x40000
        try:
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(ctypes.c_void_p(hwnd),
                                          ctypes.c_int(GWL_EXSTYLE))
            style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
            user32.SetWindowLongW(ctypes.c_void_p(hwnd),
                                  ctypes.c_int(GWL_EXSTYLE),
                                  ctypes.c_int(style))
        except Exception:
            pass
        self._apply_region()

    def _apply_region(self):
        """Скруглить углы окна; в максимизации — прямые."""
        self._region_job = None
        hwnd = self._hwnd()
        if not hwnd:
            return
        if self.maximized:
            square_window_corners(hwnd)
        else:
            round_window_corners(hwnd, 12)

    def _on_configure(self, event):
        if self.root is None or event.widget is not self.root:
            return
        if self._region_job is not None:
            try:
                self.root.after_cancel(self._region_job)
            except tk.TclError:
                pass
        try:
            self._region_job = self.root.after(40, self._apply_region)
        except tk.TclError:
            self._region_job = None

    def _frame(self):
        """(x, y, w, h) окна в экранных координатах."""
        return (self.root.winfo_rootx(), self.root.winfo_rooty(),
                self.root.winfo_width(), self.root.winfo_height())

    def _zone_at(self, event):
        x, y, w, h = self._frame()
        return hit_test(event.x_root - x, event.y_root - y, w, h,
                        self.border, self.maximized)

    def _in_titlebar(self, event):
        _x, y, _w, _h = self._frame()
        return (event.y_root - y) < self.title_h

    # -- события -------------------------------------------------------------

    def _on_press(self, event):
        if event.widget in self.excluded:
            return
        zone = self._zone_at(event)
        if zone:
            self._mode, self._zone = "resize", zone
            self._start = (event.x_root, event.y_root) + self._frame()
            return
        if self._in_titlebar(event) and not self.maximized:
            self._mode, self._zone = "move", ""
            self._start = (event.x_root, event.y_root) + self._frame()

    def _on_drag(self, event):
        if self._mode is None or self._start is None:
            return
        sx, sy, wx, wy, w, h = self._start
        dx, dy = event.x_root - sx, event.y_root - sy
        if self._mode == "move":
            self._move_by(wx, wy, w, h, dx, dy)
        else:
            nx, ny, nw, nh = resize_geometry(
                wx, wy, w, h, self._zone, dx, dy, self.min_w, self.min_h)
            self.root.geometry(f"{nw}x{nh}+{nx}+{ny}")

    def _move_by(self, wx, wy, w, h, dx, dy):
        """Перемещение с удержанием шапки в пределах рабочей области."""
        ax, ay, aw, ah = work_area()
        nx = wx + dx
        ny = wy + dy
        nx = max(ax - w + 120, min(nx, ax + aw - 120))
        ny = max(ay, min(ny, ay + ah - self.title_h))
        self.root.geometry(f"+{nx}+{ny}")

    def _on_release(self, _event):
        self._mode = None
        self._zone = ""
        self._start = None

    def _on_hover(self, event):
        if self._mode is not None:
            return
        zone = self._zone_at(event)
        if not zone and self._in_titlebar(event) \
                and event.widget not in self.excluded and not self.maximized:
            cursor = "fleur"
        else:
            cursor = CURSORS.get(zone, "")
        if cursor != self._cursor:
            self._cursor = cursor
            try:
                self.root.configure(cursor=cursor)
            except tk.TclError:
                pass

    def _on_double(self, event):
        if event.widget in self.excluded:
            return
        if self._in_titlebar(event):
            self.toggle_maximize()

    # -- операции окна ---------------------------------------------------------

    def minimize(self):
        """Сворачивание: Tk не умеет iconify у overrideredirect — через WinAPI."""
        try:
            hwnd = self._hwnd()
            if hwnd:
                ctypes.windll.user32.ShowWindow(
                    ctypes.c_void_p(hwnd), ctypes.c_int(6))  # SW_MINIMIZE
            else:
                self.root.iconify()
        except tk.TclError:
            pass

    def maximize(self):
        if self.maximized:
            return
        self._prev_geom = self.root.geometry()
        x, y, w, h = work_area()
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.maximized = True
        self.root.after_idle(self._apply_region)
        self._notify_max()

    def restore(self):
        if not self.maximized:
            return
        geom = self._prev_geom or ""
        if not valid_geometry(geom):
            x, y, w, h = work_area()
            geom = f"{DEFAULT_SIZE[0]}x{DEFAULT_SIZE[1]}+{x + 40}+{y + 40}"
        self.root.geometry(geom)
        self.maximized = False
        self.root.after_idle(self._apply_region)
        self._notify_max()

    def toggle_maximize(self):
        if self.maximized:
            self.restore()
        else:
            self.maximize()

    def _notify_max(self):
        if callable(self.on_max_change):
            try:
                self.on_max_change(self.maximized)
            except tk.TclError:
                pass


class SidebarItem(tk.Frame):
    """Пункт бокового меню: полоса, иконка, подпись и подсветка."""

    def __init__(self, app, parent, key, icon, text):
        self.app = app
        self.key = key
        self._active = False
        self._hover = False
        c = app.c
        super().__init__(parent, bg=c["panel"], cursor="hand2")
        self.bar = tk.Frame(self, width=3, bg=c["panel"])
        self.bar.pack(side="left", fill="y")
        self.icon = tk.Label(
            self, text=icon, bg=c["panel"], fg=c["muted"], width=2,
            font=(UI_FONT, 11), cursor="hand2", anchor="center")
        self.icon.pack(side="left", padx=(11, 6), pady=5)
        self.label = tk.Label(
            self, text=text, bg=c["panel"], fg=c["muted"], anchor="w",
            font=(UI_FONT, 10), cursor="hand2")
        self.label.pack(side="left", fill="x", expand=True, pady=5)
        for widget in (self, self.bar, self.icon, self.label):
            widget.bind("<Button-1>", self._click)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _click(self, _event=None):
        self.app.show_page(self.key)

    def _enter(self, _event=None):
        if self.app._closing:
            return
        self._hover = True
        self._paint()

    def _leave(self, _event=None):
        if self.app._closing:
            return
        self.after_idle(self._check_pointer)

    def _check_pointer(self):
        if self.app._closing:
            return
        try:
            x, y = self.winfo_pointerxy()
            widget = self.winfo_containing(x, y)
        except tk.TclError:
            widget = None
        inside = False
        while widget is not None:
            if widget is self:
                inside = True
                break
            widget = getattr(widget, "master", None)
        self._hover = inside
        self._paint()

    def _paint(self):
        c = self.app.c
        if self._active:
            bg = self.app._lerp_hex(c["panel"], self.app.accent, 0.16)
            self.bar.configure(bg=self.app.accent)
            self.icon.configure(bg=bg, fg=self.app.accent)
            self.label.configure(bg=bg, fg=c["text"],
                                 font=(UI_FONT, 10, "bold"))
            self.configure(bg=bg)
        else:
            bg = c["hover"] if self._hover else c["panel"]
            fg = c["text"] if self._hover else c["muted"]
            self.bar.configure(bg=c["panel"])
            self.icon.configure(bg=bg, fg=fg)
            self.label.configure(bg=bg, fg=fg, font=(UI_FONT, 10))
            self.configure(bg=bg)

    def set_active(self, active):
        self._active = bool(active)
        self._hover = False
        self._paint()

    def apply_accent(self, _color):
        if self._active:
            self._paint()


def _round_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    r = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    points = (
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    )
    return canvas.create_polygon(points, smooth=True, splinesteps=10, **kwargs)


class ThemedCheck(tk.Frame):
    """Чекбокс в стиле лаунчера: скруглённый квадрат вместо ttk."""

    def __init__(self, app, parent, text, variable, command=None, bg=None):
        c = app.c
        self.app = app
        self.var = variable
        self.command = command
        self.bg = bg or c["surface"]
        self._hover = False
        super().__init__(parent, bg=self.bg, cursor="hand2")
        self.box = tk.Canvas(self, width=19, height=19, bg=self.bg, bd=0,
                             highlightthickness=0, cursor="hand2",
                             takefocus=True)
        self.box.pack(side="left", pady=1)
        self.label = tk.Label(self, text=text, bg=self.bg, fg=c["text"],
                              font=(UI_FONT, 10), anchor="w",
                              cursor="hand2")
        self.label.pack(side="left", padx=(9, 0))
        for widget in (self, self.box, self.label):
            widget.bind("<Button-1>", self._click)
        self.box.bind("<Enter>", self._enter)
        self.box.bind("<Leave>", self._leave)
        self.box.bind("<space>", self._click)
        self.box.bind("<Return>", self._click)
        self.box.bind("<FocusIn>", lambda _e: self._draw())
        self.box.bind("<FocusOut>", lambda _e: self._draw())
        self.var.trace_add("write", lambda *_a: self._draw())
        app._checks.append(self)
        self._draw()

    def _click(self, _event=None):
        self.var.set(not bool(self.var.get()))
        if self.command is not None:
            self.command()
        return "break"

    def _enter(self, _event=None):
        self._hover = True
        self._draw()

    def _leave(self, _event=None):
        self._hover = False
        self._draw()

    def _draw(self):
        app, c = self.app, self.app.c
        checked = bool(self.var.get())
        try:
            self.box.delete("all")
        except tk.TclError:
            return
        if checked:
            fill = app.accent
            border = app.accent if not self._hover else c["on_accent"]
        else:
            fill = c["surface2"] if self.bg == c["surface"] else self.bg
            border = c["border"] if not self._hover else app.accent
        _round_rect(self.box, 1.5, 1.5, 17.5, 17.5, 4,
                    fill=fill, outline=border, width=1)
        if checked:
            self.box.create_line(5, 9.5, 8, 12.8, 14.5, 5.5,
                                 fill=c["on_accent"], width=2,
                                 capstyle="round", joinstyle="round")

    def redraw(self):
        self._draw()


class ThinScrollbar(tk.Canvas):
    """Тонкий скроллбар, полностью окрашенный под тему."""

    def __init__(self, parent, command, trough, thumb, hover, width=8):
        super().__init__(parent, width=width, height=1, bg=trough, bd=0,
                         highlightthickness=0, cursor="arrow")
        self._command = command
        self._trough = trough
        self._thumb = thumb
        self._hover_color = hover
        self._first, self._last = 0.0, 1.0
        self._hover = False
        self._drag_off = None
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)

    def set(self, first, last):
        try:
            self._first, self._last = float(first), float(last)
        except (TypeError, ValueError):
            return
        self._draw()

    def _enter(self, _event=None):
        self._hover = True
        self._draw()

    def _leave(self, _event=None):
        self._hover = False
        self._draw()

    def _draw(self):
        try:
            self.delete("all")
            height = self.winfo_height()
            width = self.winfo_width()
        except tk.TclError:
            return
        if height <= 2 or self._last - self._first >= 0.999:
            return
        top = int(self._first * height)
        bottom = int(self._last * height)
        if bottom - top < 26:
            middle = (top + bottom) // 2
            top = max(2, min(middle - 13, height - 28))
            bottom = top + 26
        color = (self._hover_color if self._hover or self._drag_off is not None
                 else self._thumb)
        _round_rect(self, 1.5, top + 1.5, width - 1.5, bottom - 1.5, 3,
                    fill=color, outline="")

    def _press(self, event):
        height = max(1, self.winfo_height())
        top = self._first * height
        bottom = self._last * height
        if top <= event.y <= bottom:
            self._drag_off = event.y - top
        else:
            half = max(4.0, (bottom - top) / 2)
            self._drag_off = half
            self._move_to((event.y - half) / height)
        self._draw()

    def _motion(self, event):
        if self._drag_off is None:
            return
        self._move_to((event.y - self._drag_off) / max(1, self.winfo_height()))

    def _move_to(self, fraction):
        fraction = max(0.0, min(1.0, fraction))
        self._command("moveto", str(fraction))

    def _release(self, _event=None):
        self._drag_off = None
        self._draw()


# ---- приложение ------------------------------------------------------------------


class LaunchSplash:
    """Экран запуска поверх окон: карточка или узкая полоса."""

    def __init__(self, app, title, subtitle="Запускаю Roblox…", style="card"):
        self.app = app
        self.style = "minimal" if style == "minimal" else "card"
        self.subtitle = subtitle or "Запускаю Roblox…"
        self.win = None
        self._photo = None
        self._job = None
        self._closed = False
        c = app.c
        card = self.style == "card"
        width = 430 if card else 350
        height = 172 if card else 86
        win = tk.Toplevel(app.root)
        self.win = win
        win.withdraw()
        win.overrideredirect(True)
        win.configure(bg=c["border"])
        try:
            win.attributes("-topmost", True)
        except tk.TclError:
            pass
        body = tk.Frame(win, bg=c["surface"])
        body.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Frame(body, bg=app.accent, height=3).pack(fill="x", side="top")
        content = tk.Frame(body, bg=c["surface"],
                           padx=18 if card else 12,
                           pady=14 if card else 8)
        content.pack(fill="both", expand=True)
        size = 58 if card else 36
        icon_box = tk.Frame(content, bg=c["surface2"], width=size,
                            height=size, highlightthickness=1,
                            highlightbackground=c["border"])
        icon_box.pack(side="left", anchor="center")
        icon_box.pack_propagate(False)
        icon_lbl = tk.Label(icon_box, text="▶", bg=c["surface2"],
                            fg=app.accent,
                            font=(UI_FONT, 18 if card else 12, "bold"))
        icon_lbl.pack(fill="both", expand=True)
        photo = app._splash_icon_photo(size - 8)
        if photo is not None:
            icon_lbl.configure(image=photo, text="")
            self._photo = photo
        text = tk.Frame(content, bg=c["surface"])
        text.pack(side="left", fill="both", expand=True,
                  padx=(14 if card else 10, 0))
        brand = tk.Label(text, text="MAKLYXA LAUNCHER", bg=c["surface"],
                         fg=app.accent,
                         font=(UI_FONT, 7, "bold"))
        if card:
            brand.pack(anchor="w")
        title_lbl = tk.Label(
            text, text=trunc(title or "Roblox", 52 if card else 40),
            bg=c["surface"], fg=c["text"],
            font=(UI_FONT, 14 if card else 11, "bold"),
            anchor="w", justify="left",
            wraplength=310 if card else 250)
        title_lbl.pack(anchor="w", pady=(2, 0))
        self.status_lbl = tk.Label(
            text, text=self.subtitle, bg=c["surface"], fg=c["muted"],
            font=(UI_FONT, 9), anchor="w", justify="left",
            wraplength=310 if card else 250)
        self.status_lbl.pack(anchor="w", pady=(4 if card else 1, 0))
        close_lbl = tk.Label(text, text="клик — скрыть", bg=c["surface"],
                             fg=c["muted"], font=(UI_FONT, 7),
                             anchor="w")
        if card:
            close_lbl.pack(anchor="w", pady=(5, 0))
        for widget in (win, body, content, icon_box, icon_lbl, text, brand,
                       title_lbl, self.status_lbl, close_lbl):
            widget.bind("<Button-1>", lambda _e: self.close())
        win.update_idletasks()
        try:
            screen_w = win.winfo_screenwidth()
            screen_h = win.winfo_screenheight()
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 3)
            win.geometry(f"{width}x{height}+{x}+{y}")
            win.deiconify()
        except tk.TclError:
            self.close()
            return
        self._animate()

    def _animate(self):
        if self.win is None or self._closed:
            return
        dots = "." * (1 + (int(time.monotonic() * 2.5) % 3))
        try:
            self.status_lbl.configure(text=f"{self.subtitle}{dots}")
            self._job = self.win.after(360, self._animate)
        except tk.TclError:
            self._job = None

    def set_status(self, text):
        if text:
            self.subtitle = text
        if self.win is None:
            return
        try:
            self.status_lbl.configure(text=self.subtitle)
        except tk.TclError:
            pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._job is not None:
            try:
                self.win.after_cancel(self._job)
            except (tk.TclError, AttributeError):
                pass
            self._job = None
        win, self.win = self.win, None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass


class App:
    def __init__(self, root, interactive=True, start_page=None):
        self.root = root
        self.interactive = interactive
        self.cfg = load_config()
        self.theme_name = self.cfg["theme"]
        self.custom_theme_name = ""
        self.c = THEMES.get(self.theme_name, THEMES[DEFAULT_THEME])
        if self.theme_name == "custom":
            palette = load_theme_palette(self.cfg.get("custom_theme") or "")
            if palette is not None:
                self.c = palette
                self.custom_theme_name = self.cfg["custom_theme"]
            else:
                self.theme_name = DEFAULT_THEME
                self.c = THEMES[DEFAULT_THEME]
                self.cfg["theme"] = DEFAULT_THEME
        self.accent = (self.c.get("accent") if self.theme_name == "custom"
                       else self.cfg["accent"])
        self._loaded_font_path = None
        self._ui_font_family = ""
        self._loaded_font_path, self._ui_font_family = _prepare_preview_font(
            self.root, self.cfg)

        self.q = queue.Queue()
        self.info_cache = {}
        self.icon_cache = {}
        self.log_lines = []
        self.nav_buttons = {}
        self.pages = {}
        self.scroll_canvases = {}
        self.primary_buttons = []
        self.accent_bgs = []          # виджеты с фоном акцентного цвета
        self.accent_fgs = []          # виджеты с текстом акцентного цвета
        self._checks = []             # кастомные чекбоксы
        self._btn_anim_jobs = {}      # анимации наведения кнопок
        self.current_page = start_page or "game"
        self._rpc = None
        self._rpc_start_ts = time.time()
        self.discord_status_var = tk.StringVar(value="")
        self.update_status_var = tk.StringVar(
            value="Проверка ещё не выполнялась")
        self._update_busy = False
        self._upd_asset = None
        self._upd_file = None
        self._upd_version = ""
        self._upd_busy = False
        self._upd_cancel = threading.Event()
        self._upd_install_busy = False
        self._upd_helper_stop_sent = False
        self.account_name_var = tk.StringVar(value="Не выбран")
        self.account_user_var = tk.StringVar(value="Войти в аккаунт")
        self.game_account_var = tk.StringVar(value="Аккаунт не выбран")
        self.active_display_var = tk.StringVar(value="Аккаунт не выбран")
        self.active_login_var = tk.StringVar(value="Добавь аккаунт, чтобы играть под ним")
        self.active_meta_var = tk.StringVar(value="")
        self._accounts = {"active": "", "accounts": []}
        self._avatar_photos = {}
        self._page_views = {}
        self._page_layout_jobs = {}
        self._header_canvases = []
        self._backdrop_pil = None
        self._backdrop_master = None
        self._backdrop_key = None
        self._backdrop_job = None
        self._play_busy = False
        self._theme_preview_photo = None
        self._theme_preview_cache = {}
        self._theme_active_set = None
        self._theme_base = "dark"
        self._theme_tint = "#00c48c"
        self._theme_accent = "#00c48c"
        self._theme_image_path = None
        self._theme_image_only = False
        self._login_cancel = None
        self._login_dialog = None
        self._login_busy = False
        self.launch_icon_status_var = tk.StringVar(value="")
        self.launch_sound_status_var = tk.StringVar(value="")
        self.error_sound_status_var = tk.StringVar(value="")
        self.ui_font_status_var = tk.StringVar(value="")
        self.launch_shortcut_status_var = tk.StringVar(value="")
        self.launch_style_note_var = tk.StringVar(value="")
        self._launch_icon_photo_ref = None
        self._shortcut_busy = False
        self._launch_splash = None
        self._launch_splash_job = None
        self._launch_effect_ts = 0.0
        self._splash_icon_cache = {}
        self._gallery_cache = None
        self._gallery_groups = []
        self._gallery_shots = []
        self._gallery_group_key = ""
        self._gallery_pending_key = ""
        self._gallery_page = 0
        self._gallery_page_size = 6
        self._gallery_busy = False
        self._gallery_refresh_token = 0
        self._gallery_last_scan = 0.0
        self._gallery_photo_cache = {}
        self._gallery_thumb_jobs = set()
        self._gallery_visible = []
        self._gallery_tiles = []
        self._gallery_viewer = None
        self._gallery_viewer_group = None
        self._gallery_viewer_index = 0
        self._gallery_assign_dialog = None
        self._gallery_clean_dialog = None
        self.ingame_helper_var = tk.StringVar(value="Помощник не запущен")
        self.ingame_roblox_var = tk.StringVar(value="Roblox: не запущен")
        self.ingame_overlay_state_var = tk.StringVar(value="Оверлей: выключен")
        self.ingame_fps_var = tk.StringVar(value="FPS: —")
        self.ingame_hotkey_var = tk.StringVar(
            value=f"{ingame_mod.hotkey_label(self.cfg.get('ingame_hotkey'))}: "
                  "не проверен")
        self.ingame_afk_state_var = tk.StringVar(value="AFK: выключен")
        self.ingame_clock_state_var = tk.StringVar(value="Часы: выключены")
        self.ingame_message_var = tk.StringVar(value="")
        self._ingame_busy = False
        self._ingame_last = None
        self._ingame_poll_job = None
        self._ingame_user_stopped = False
        self._ingame_next_auto = 0.0

        self._debounce = None
        self._dl_active = False
        self._dl_cancel = threading.Event()
        self._client_busy = False
        self._closing = False
        self._startup_started = False
        self._wheel_bound = False
        self._boot_layout_done = False
        self._icon_ref = None
        self.roblox_kind = "missing"
        self.boot_kind = None
        self.boot_config_path = None

        self.frameless = FramelessWindow(root, (MIN_W, MIN_H))
        self.frameless.on_alt_tab = self._close_on_alt_tab
        self._configure_window()
        self._build(keep_page=self.current_page)

        if not self._wheel_bound:
            self.root.bind_all("<MouseWheel>", self._on_wheel, add="+")
            self._wheel_bound = True
        self.root.after(100, self._poll_queue)
        self._startup_checks()
        self.on_entry_changed()
        self._start_presence()
        self.discord_status_var.set(self._discord_status_text())
        if self.interactive:
            self._poll_discord_status()
            self._ingame_poll_job = self.root.after(
                1600, self._poll_ingame_status)

    # -- сборка / пересборка ----------------------------------------------------

    def _build(self, keep_page=None):
        self._build_styles()
        self._build_shell()
        self._build_page_game()
        self._build_page_accounts()
        self._build_page_favorites()
        self._build_page_gallery()
        self._build_page_ingame()
        self._build_page_flags()
        self._build_page_cursors()
        self._build_page_themes()
        self._build_page_launchstyle()
        self._build_page_settings()
        self._build_page_log()
        self._load_into_ui()
        self._refresh_lists()
        self._refresh_account_ui()
        page = keep_page if keep_page in self.pages else "game"
        self.show_page(page)
        self.apply_accent(self.accent)
        self._bind_page_children()
        for key in list(self._page_views):
            self._schedule_page_layout(key, delay=60)
        self._schedule_backdrops(80)

    def _rebuild(self):
        """Полная пересборка UI (смена темы)."""
        keep = self.current_page
        for job in list(self._page_layout_jobs.values()):
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._page_layout_jobs.clear()
        if self._backdrop_job is not None:
            try:
                self.root.after_cancel(self._backdrop_job)
            except tk.TclError:
                pass
            self._backdrop_job = None
        self._page_views.clear()
        self._header_canvases.clear()
        self._backdrop_key = None
        self._backdrop_master = None
        self._backdrop_pil = None
        for child in self.root.winfo_children():
            child.destroy()
        self.nav_buttons.clear()
        self.pages.clear()
        self.scroll_canvases.clear()
        self.primary_buttons.clear()
        self.accent_bgs.clear()
        self.accent_fgs.clear()
        self._checks.clear()
        for job in self._btn_anim_jobs.values():
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._btn_anim_jobs.clear()
        self._debounce = None
        self._boot_layout_done = False
        self.frameless.excluded = set()
        self.frameless._cursor = ""
        self._gallery_viewer = None
        self._gallery_assign_dialog = None
        self._gallery_clean_dialog = None
        self._gallery_tiles = []
        self._gallery_photo_cache.clear()
        self._build(keep_page=keep)

    def _configure_window(self):
        root = self.root
        root.title(WINDOW_TITLE)
        geom = self.cfg.get("window_geometry") or ""
        if valid_geometry(geom):
            root.geometry(geom)
        else:
            ax, ay, aw, ah = work_area()
            gw = min(DEFAULT_SIZE[0], aw - 40)
            gh = min(DEFAULT_SIZE[1], ah - 80)
            root.geometry(
                f"{gw}x{gh}+{ax + (aw - gw) // 2}+{ay + (ah - gh) // 2}")
        root.minsize(MIN_W, MIN_H)
        root.protocol("WM_DELETE_WINDOW", self.close)
        if ICON_PNG.exists():
            try:
                icon = tk.PhotoImage(file=str(ICON_PNG))
                root.iconphoto(True, icon)
                self._icon_ref = icon
            except tk.TclError:
                pass

    # -- стили -----------------------------------------------------------------

    def _build_styles(self):
        c = self.c
        self.root.configure(bg=c["bg"])
        style = ttk.Style(self.root)
        self.style = style
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.root.option_add("*TCombobox*Listbox.background", c["list"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", self.accent)
        self.root.option_add("*TCombobox*Listbox.selectForeground",
                             c["on_accent"])

        style.configure(".", background=c["bg"], foreground=c["text"],
                        fieldbackground=c["entry"], troughcolor=c["bg"],
                        bordercolor=c["border"], lightcolor=c["border"],
                        darkcolor=c["border"], font=(UI_FONT, 10))
        style.configure("TFrame", background=c["bg"])
        style.configure("TLabel", background=c["bg"], foreground=c["text"])
        style.configure("TCombobox", fieldbackground=c["entry"],
                        background=c["surface2"], foreground=c["text"],
                        arrowcolor=c["text"], borderwidth=1)
        style.map("TCombobox",
                  fieldbackground=[("readonly", c["entry"]),
                                   ("focus", c["entry"])],
                  foreground=[("readonly", c["text"])],
                  bordercolor=[("focus", self.accent)])
        style.configure("TEntry", fieldbackground=c["entry"],
                        foreground=c["text"], insertcolor=c["text"],
                        borderwidth=1, padding=(8, 8))
        style.map("TEntry",
                  bordercolor=[("focus", self.accent)],
                  lightcolor=[("focus", self.accent)],
                  darkcolor=[("focus", self.accent)])
        style.configure("Card.TLabel", background=c["surface"],
                        foreground=c["text"])
        style.configure("CardMuted.TLabel", background=c["surface"],
                        foreground=c["muted"], font=(UI_FONT, 9))
        style.configure("TButton", background=c["surface2"],
                        foreground=c["text"], borderwidth=1,
                        focusthickness=1, padding=(10, 6))
        style.map("TButton",
                  background=[("active", c["hover"]),
                              ("pressed", shade(self.accent, 0.85))],
                  foreground=[("active", c["text"])])
        style.configure("Primary.TButton", background=self.accent,
                        foreground=c["on_accent"], borderwidth=0,
                        focusthickness=0, padding=(16, 8),
                        font=(UI_FONT, 10, "bold"))
        style.map("Primary.TButton",
                  background=[("active", shade(self.accent, 1.12)),
                              ("pressed", shade(self.accent, 0.85))])
        style.configure("green.Horizontal.TProgressbar",
                        background=self.accent, troughcolor=c["surface2"],
                        bordercolor=c["border"], lightcolor=self.accent,
                        darkcolor=self.accent)

    # -- каркас: шапка, панель вкладок, страницы, статус -------------------------

    def _build_shell(self):
        root, c = self.root, self.c
        try:
            root.attributes("-alpha", c["alpha"])
        except tk.TclError:
            pass
        # alpha сбрасывает exstyle — возвращаем пометку панели задач
        self.root.after_idle(self.frameless._on_map)

        # верхняя панель: бренд, версия, текущий раздел, тема и кнопки окна
        titlebar = tk.Frame(root, bg=c["titlebar"], height=TITLEBAR_H)
        titlebar.pack(fill="x")
        titlebar.pack_propagate(False)

        win_controls = self.cfg.get("window_buttons") == "win"
        controls = tk.Frame(titlebar, bg=c["titlebar"])
        if win_controls:
            controls.pack(side="right", fill="y")
        else:
            controls.pack(side="left", padx=(14, 4))
        if ICON_PNG.exists():
            try:
                img = tk.PhotoImage(file=str(ICON_PNG))
                img = img.subsample(12, 12)      # 256 → 21 px
                self._title_icon = img
                tk.Label(titlebar, image=img, bg=c["titlebar"],
                         bd=0).pack(side="left",
                                     padx=(14, 8) if win_controls else (8, 8))
            except tk.TclError:
                pass
        tk.Label(titlebar, text=APP_NAME, bg=c["titlebar"], fg=c["text"],
                 font=(UI_FONT, 10, "bold")).pack(side="left")
        tk.Label(titlebar, text=f"v{VERSION}", bg=c["surface2"],
                 fg=c["muted"], font=(UI_FONT, 8, "bold"), padx=7,
                 pady=2).pack(side="left", padx=(9, 0))
        self.title_crumb = tk.Label(
            titlebar, text="", bg=c["titlebar"], fg=c["muted"],
            font=(UI_FONT, 9))
        self.title_crumb.pack(side="left", padx=(14, 0))

        if win_controls:
            min_btn = self._window_button(
                controls, "–", self.frameless.minimize)
            max_btn = self._window_button(
                controls, "□", self.frameless.toggle_maximize)
            close_btn = self._window_button(
                controls, "✕", self.close, danger=True)
        else:
            close_btn = self._traffic_light(
                controls, "#ff5f57", "#5c0b06", "✕", self.close)
            min_btn = self._traffic_light(
                controls, "#febc2e", "#5c3d00", "–",
                self.frameless.minimize)
            max_btn = self._traffic_light(
                controls, "#28c840", "#0b4a12", "＋",
                self.frameless.toggle_maximize)
        self.max_button = max_btn
        self.frameless.on_max_change = self._on_max_change

        theme_lbl = self.c["label"]
        self.theme_cycle_btn = tk.Button(
            titlebar, text=f"◐  {theme_lbl}", relief="flat", bd=0,
            font=(UI_FONT, 9), cursor="hand2", takefocus=False,
            bg=c["surface2"], fg=c["text"], activebackground=c["hover"],
            activeforeground=c["text"], highlightthickness=0, padx=12,
            command=self._cycle_theme)
        self.theme_cycle_btn.pack(side="right", padx=(0, 12))
        self._bind_hover(self.theme_cycle_btn, "ghost")

        self.account_switch_btn = tk.Menubutton(
            titlebar, text="◉  Войти", relief="flat", bd=0,
            font=(UI_FONT, 9), cursor="hand2", takefocus=False,
            bg=c["surface2"], fg=c["text"], activebackground=c["hover"],
            activeforeground=c["text"], highlightthickness=0, padx=12)
        self.account_menu = tk.Menu(
            self.account_switch_btn, tearoff=0, bg=c["surface"],
            fg=c["text"], activebackground=self.accent,
            activeforeground=c["on_accent"], bd=0, font=(UI_FONT, 9))
        self.account_switch_btn.configure(menu=self.account_menu)
        self.account_switch_btn.pack(side="right", padx=(0, 8))
        self.frameless.excluded = {min_btn, max_btn, close_btn,
                                   self.theme_cycle_btn,
                                   self.account_switch_btn}

        body = tk.Frame(root, bg=c["bg"])
        body.pack(fill="both", expand=True)

        sidebar = tk.Frame(body, bg=c["panel"], width=SIDEBAR_W)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Frame(body, bg=c["border"], width=1).pack(side="left", fill="y")

        main = tk.Frame(body, bg=c["bg"])
        main.pack(side="left", fill="both", expand=True)

        self._build_sidebar(sidebar)

        self.proto_var = tk.StringVar(value="Проверка Roblox…")
        self.status_var = tk.StringVar(value="Готово")

        # нижняя статусная строка — внутри рабочей области
        statusbar = tk.Frame(main, bg=c["panel"], height=38)
        statusbar.pack(fill="x", side="bottom")
        statusbar.pack_propagate(False)
        self.status_dot = tk.Label(statusbar, text="●", bg=c["panel"],
                                   fg=self.accent, font=(UI_FONT, 7))
        self.status_dot.pack(side="left", padx=(18, 0))
        tk.Label(statusbar, textvariable=self.proto_var, bg=c["panel"],
                 fg=c["muted"], font=(UI_FONT, 9)).pack(
                     side="left", padx=(7, 0))
        self.btn_save_all = self._button(
            statusbar, "Сохранить всё", self.save_everything, kind="primary",
            side="right", padx=(0, 14))
        tk.Label(statusbar, textvariable=self.status_var, bg=c["panel"],
                 fg=c["text"], font=(UI_FONT, 9)).pack(
                     side="right", padx=(0, 14))
        tk.Frame(main, bg=c["border"], height=1).pack(
            fill="x", side="bottom")

        content = tk.Frame(main, bg=c["bg"])
        content.pack(fill="both", expand=True)
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)
        self.content = content
        if not getattr(self, "_root_configure_bound", False):
            root.bind("<Configure>", self._on_root_configure, add="+")
            self._root_configure_bound = True

    def _build_sidebar(self, sidebar):
        c = self.c
        nav = tk.Frame(sidebar, bg=c["panel"])
        nav.pack(fill="x", padx=12, pady=(18, 0))
        items = {key: (icon, text) for key, icon, text in NAV_ITEMS}
        for title, keys in NAV_GROUPS:
            tk.Label(nav, text=title, bg=c["panel"], fg=c["muted"],
                     font=(UI_FONT, 8, "bold"), anchor="w",
                     justify="left").pack(
                         fill="x", padx=9, pady=(8 if title == "ЗАПУСК"
                                               else 7, 2))
            for key in keys:
                icon, text = items[key]
                item = SidebarItem(self, nav, key, icon, text)
                item.pack(fill="x")
                self.nav_buttons[key] = item

        tk.Frame(sidebar, bg=c["panel"]).pack(fill="both", expand=True)

        # активный аккаунт — быстрый переход к переключателю
        acc_card = tk.Frame(sidebar, bg=c["border"], cursor="hand2")
        acc_card.pack(fill="x", padx=12, pady=(10, 0))
        acc_inner = tk.Frame(acc_card, bg=c["surface"], padx=12, pady=8,
                             cursor="hand2")
        acc_inner.pack(fill="x", padx=1, pady=1)
        acc_head = tk.Frame(acc_inner, bg=c["surface"], cursor="hand2")
        acc_head.pack(fill="x")
        acc_dot = tk.Label(acc_head, text="●", bg=c["surface"],
                           fg=self.accent, font=(UI_FONT, 7),
                           cursor="hand2")
        acc_dot.pack(side="left")
        tk.Label(acc_head, text="АККАУНТ", bg=c["surface"], fg=c["muted"],
                 font=(UI_FONT, 8, "bold"), cursor="hand2").pack(
                     side="left", padx=(6, 0))
        self.sidebar_account_dot = acc_dot
        acc_name = tk.Label(acc_inner, textvariable=self.account_name_var,
                            bg=c["surface"], fg=c["text"],
                            font=(UI_FONT, 9, "bold"), anchor="w",
                            cursor="hand2")
        acc_name.pack(fill="x", pady=(4, 0))
        acc_user = tk.Label(acc_inner, textvariable=self.account_user_var,
                            bg=c["surface"], fg=c["muted"],
                            font=(UI_FONT, 8), anchor="w",
                            cursor="hand2")
        acc_user.pack(fill="x")
        for widget in (acc_card, acc_inner, acc_head, acc_dot, acc_name,
                       acc_user):
            widget.bind("<Button-1>",
                        lambda _e: self.show_page("accounts"))

        # статус Roblox внизу сайдбара
        card = tk.Frame(sidebar, bg=c["border"])
        card.pack(fill="x", padx=12, pady=(10, 0))
        inner = tk.Frame(card, bg=c["surface"], padx=12, pady=10)
        inner.pack(fill="x", padx=1, pady=1)
        head = tk.Frame(inner, bg=c["surface"])
        head.pack(fill="x")
        self.sidebar_status_dot = tk.Label(
            head, text="●", bg=c["surface"], fg=self.accent,
            font=(UI_FONT, 7))
        self.sidebar_status_dot.pack(side="left")
        tk.Label(head, text="ROBLOX", bg=c["surface"], fg=c["muted"],
                 font=(UI_FONT, 8, "bold")).pack(side="left", padx=(6, 0))
        self.sidebar_roblox_var = tk.StringVar()
        tk.Label(inner, textvariable=self.sidebar_roblox_var,
                 bg=c["surface"], fg=c["text"],
                 font=(UI_FONT, 9, "bold"), anchor="w").pack(
                     fill="x", pady=(4, 0))
        self._refresh_sidebar_status()

    def _refresh_sidebar_status(self):
        var = getattr(self, "sidebar_roblox_var", None)
        if var is None:
            return
        kind = getattr(self, "roblox_kind", "missing")
        text = {
            "installed": "установлен",
            "bootstrapper": "через бутстрапер",
            "broken": "файл потерян",
        }.get(kind, "не найден")
        var.set(text)
        dot = getattr(self, "sidebar_status_dot", None)
        if dot is not None:
            if kind in ("installed", "bootstrapper"):
                color = self.accent
            elif kind == "broken":
                color = self._warn_fg()
            else:
                color = self.c["muted"]
            dot.configure(fg=color)

    def _cycle_theme(self):
        """Быстрая смена темы в верхней панели."""
        order = list(THEME_ORDER)
        custom_name = self._custom_theme_available()
        if custom_name:
            order.append("custom")
        idx = order.index(self.theme_name) if self.theme_name in order else 0
        target = order[(idx + 1) % len(order)]
        if target == "custom":
            self.set_theme("custom", custom_name=custom_name)
        else:
            self.set_theme(target)

    def _custom_theme_available(self):
        name = str(self.cfg.get("custom_theme") or "")
        try:
            return name if name and name in list_theme_sets() else ""
        except Exception:
            return ""

    # -- фон из картинки своей темы ------------------------------------------------

    def _theme_image_source(self):
        source = self.c.get("image") or ""
        try:
            if source and Path(source).is_file():
                return str(source)
        except OSError:
            pass
        return ""

    def _on_root_configure(self, event=None):
        if getattr(self, "_closing", False):
            return
        if event is not None and event.widget is not self.root:
            return
        self._schedule_backdrops()

    def _schedule_backdrops(self, delay=120):
        if getattr(self, "_closing", False):
            return
        if self._backdrop_job is not None:
            try:
                self.root.after_cancel(self._backdrop_job)
            except tk.TclError:
                pass
        try:
            self._backdrop_job = self.root.after(delay, self._refresh_backdrops)
        except tk.TclError:
            self._backdrop_job = None

    def _refresh_backdrops(self):
        self._backdrop_job = None
        if getattr(self, "_closing", False):
            return
        self._backdrop_key = None
        self._backdrop_master = None
        self._backdrop_pil = None
        for key in list(self._page_views):
            self._sync_page_layout(key)
        for canvas in list(self._header_canvases):
            self._render_header(canvas)

    def _ensure_backdrop_master(self):
        """Фото на всё окно: затемнённое, с лёгким блюром."""
        source = self._theme_image_source()
        if not source or not HAVE_PIL:
            return None
        try:
            width = max(1, self.root.winfo_width())
            height = max(1, self.root.winfo_height())
            stamp = Path(source).stat().st_mtime_ns
        except (OSError, tk.TclError):
            return None
        if width < 40 or height < 40:
            return None
        key = (source, stamp, width, height, self.theme_name)
        if key == self._backdrop_key and self._backdrop_master is not None:
            return self._backdrop_master
        try:
            image = Image.open(source).convert("RGB")
            scale = max(width / image.width, height / image.height)
            size = (max(1, int(image.width * scale + 0.5)),
                    max(1, int(image.height * scale + 0.5)))
            image = image.resize(size, Image.LANCZOS)
            left = max(0, (image.width - width) // 2)
            top = max(0, (image.height - height) // 2)
            image = image.crop((left, top, left + width, top + height))
            image = image.filter(ImageFilter.GaussianBlur(1.1))
            try:
                raw = self.c["bg"].lstrip("#")
                base_rgb = tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))
            except (ValueError, TypeError, KeyError):
                base_rgb = (10, 12, 10)
            base_rgb = tuple(int(value * 0.5) for value in base_rgb)
            overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            dim = 130 if self.c.get("image_only") else 112
            draw.rectangle([0, 0, width, height], fill=base_rgb + (dim,))
            fade = min(int(height * 0.30), 240)
            for index in range(fade):
                alpha = int(72 * (1 - index / max(1, fade - 1)) ** 2)
                if alpha:
                    draw.line([(0, index), (width, index)],
                              fill=(0, 0, 0, alpha))
            fade = min(int(height * 0.22), 180)
            for index in range(fade):
                alpha = int(80 * (index / max(1, fade - 1)) ** 2)
                if alpha:
                    y = height - fade + index
                    draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))
            pil = Image.alpha_composite(image.convert("RGBA"),
                                        overlay).convert("RGB")
            photo = ImageTk.PhotoImage(pil)
        except Exception:
            return None
        self._backdrop_pil = pil
        self._backdrop_master = photo
        self._backdrop_key = key
        return photo

    def _apply_page_photo(self, key, photo=None):
        data = self._page_views.get(key)
        if data is None:
            return
        if photo is None:
            photo = self._ensure_backdrop_master()
        view, pad = data["view"], data["pad"]
        data["photo"] = photo
        try:
            view.itemconfigure(
                data["view_photo"], image=photo if photo is not None else "")
            pad.itemconfigure(
                data["pad_photo"], image=photo if photo is not None else "")
        except tk.TclError:
            return
        if photo is not None:
            self._position_page_photo(key)

    def _position_page_photo(self, key):
        data = self._page_views.get(key)
        if data is None or data.get("photo") is None:
            return
        view, pad = data["view"], data["pad"]
        try:
            view_x = view.winfo_rootx() - self.root.winfo_rootx()
            view_y = view.winfo_rooty() - self.root.winfo_rooty()
            offset = view.canvasy(0)
            view.coords(data["view_photo"], -view_x, offset - view_y)
            pad.coords(data["pad_photo"],
                       -view_x - data["padx"],
                       offset - view_y - data["pady"])
        except tk.TclError:
            pass

    def _on_page_scroll(self, key, first, last):
        data = self._page_views.get(key)
        if data is None:
            return
        try:
            data["bar"].set(first, last)
        except tk.TclError:
            pass
        self._position_page_photo(key)
        for canvas in self._header_canvases:
            if getattr(canvas, "_header_key", "") == key:
                try:
                    self._schedule_header(canvas, delay=40)
                except tk.TclError:
                    pass
                break

    def _schedule_page_layout(self, key, delay=50):
        if getattr(self, "_closing", False):
            return
        job = self._page_layout_jobs.pop(key, None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        try:
            self._page_layout_jobs[key] = self.root.after(
                delay, lambda k=key: self._sync_page_layout(k))
        except tk.TclError:
            pass

    def _sync_page_layout(self, key):
        self._page_layout_jobs.pop(key, None)
        if getattr(self, "_closing", False):
            return
        data = self._page_views.get(key)
        if data is None:
            return
        view, pad = data["view"], data["pad"]
        try:
            view_w = view.winfo_width()
            view_h = view.winfo_height()
        except tk.TclError:
            return
        if view_w <= 1 or view_h <= 1:
            return
        pad_w = max(160, view_w - 2 * data["padx"])
        try:
            current_w = int(float(view.itemcget(data["window"], "width") or 1))
        except (tk.TclError, ValueError):
            current_w = 0
        if abs(current_w - pad_w) > 1:
            try:
                view.itemconfigure(data["window"], width=pad_w)
                pad.update_idletasks()
            except tk.TclError:
                return
        try:
            content_h = max(1, pad.winfo_reqheight())
            view.itemconfigure(data["window"], height=content_h)
            view.configure(
                scrollregion=(0, 0, view_w,
                              content_h + 2 * data["pady"]))
        except tk.TclError:
            return
        data["height"] = content_h
        self._apply_page_photo(key)
        self._position_page_photo(key)

    def _bind_page_children(self):
        for key, data in self._page_views.items():
            try:
                children = data["pad"].winfo_children()
            except tk.TclError:
                continue
            for child in children:
                if getattr(child, "_maklyxa_sync", False):
                    continue
                child._maklyxa_sync = True
                child.bind(
                    "<Configure>",
                    lambda _e, k=key: self._schedule_page_layout(k),
                    add="+")

    def _render_header(self, canvas):
        canvas._header_job = None
        if getattr(self, "_closing", False):
            return
        spec = getattr(canvas, "_header_spec", None)
        if not spec:
            return
        try:
            width = canvas.winfo_width()
            height = canvas.winfo_height()
        except tk.TclError:
            return
        if width <= 2 or height <= 2:
            return
        glyph, title, subtitle = spec
        c = self.c
        accent = self.accent
        compact = height < 100
        canvas.delete("all")
        canvas._header_photo = None
        photo = self._ensure_backdrop_master()
        if photo is not None and self._backdrop_pil is not None:
            try:
                x = canvas.winfo_rootx() - self.root.winfo_rootx()
                y = canvas.winfo_rooty() - self.root.winfo_rooty()
            except tk.TclError:
                x = y = 0
            try:
                xs = min(max(0, x),
                         max(0, self._backdrop_pil.width - width))
                ys = min(max(0, y),
                         max(0, self._backdrop_pil.height - height))
                crop = self._backdrop_pil.crop(
                    (xs, ys, xs + width, ys + height)).convert("RGBA")
                overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                for index in range(height):
                    alpha = int(150 * (1 - index / max(1, height - 1)) ** 1.3)
                    if alpha:
                        draw.line([(0, index), (width, index)],
                                  fill=(5, 8, 7, alpha))
                header_photo = ImageTk.PhotoImage(
                    Image.alpha_composite(crop, overlay).convert("RGB"))
                canvas._header_photo = header_photo
                canvas.create_image(0, 0, image=header_photo, anchor="nw")
            except Exception:
                canvas.configure(bg=c["bg"])
        else:
            canvas.configure(bg=c["bg"])
        if compact:
            canvas.create_rectangle(0, 10, 16, 12, fill=accent, outline="")
            canvas.create_text(24, 4, text="M A K L Y X A", anchor="nw",
                               fill=accent, font=(UI_FONT, 8, "bold"))
            canvas.create_text(0, 24, text=title, anchor="nw", fill=c["text"],
                               font=(UI_FONT, 19, "bold"))
            badge = 44
        else:
            canvas.create_rectangle(0, 18, 18, 20, fill=accent, outline="")
            canvas.create_text(26, 11, text="M A K L Y X A", anchor="nw",
                               fill=accent, font=(UI_FONT, 8, "bold"))
            canvas.create_text(0, 30, text=title, anchor="nw", fill=c["text"],
                               font=(UI_FONT, 22, "bold"))
            badge = 56
            if subtitle:
                canvas.create_text(
                    0, 72, text=subtitle, anchor="nw", fill=c["muted"],
                    font=(UI_FONT, 10), width=max(220, width - 130),
                    justify="left")
        by1 = 8
        bx2 = width - 8
        _round_rect(canvas, bx2 - badge, by1, bx2, by1 + badge, 12,
                    fill=c["surface2"], outline=c["border"], width=1)
        canvas.create_text(bx2 - badge // 2, by1 + badge // 2, text=glyph,
                           fill=accent,
                           font=(UI_FONT, 16 if compact else 20, "bold"))

    def _traffic_light(self, parent, color, glyph_color, glyph, command):
        """Кнопка окна как в macOS: цветной кружок со знаком при наведении."""
        size = 14
        canvas = tk.Canvas(
            parent, width=size, height=size, bg=self.c["titlebar"],
            highlightthickness=0, bd=0, cursor="hand2", takefocus=0)
        dot = canvas.create_oval(2, 2, size - 2, size - 2,
                                 fill=color, outline="")
        mark = canvas.create_text(size / 2, size / 2, text="",
                                  fill=glyph_color,
                                  font=(UI_FONT, 7, "bold"))
        canvas.hover_glyph = glyph
        canvas.pack(side="left", padx=(0, 8))

        def on_enter(_e):
            canvas.itemconfigure(dot, fill=shade(color, 1.14))
            canvas.itemconfigure(
                mark, text=getattr(canvas, "hover_glyph", glyph))

        def on_leave(_e):
            canvas.itemconfigure(dot, fill=color)
            canvas.itemconfigure(mark, text="")

        canvas.bind("<Enter>", on_enter)
        canvas.bind("<Leave>", on_leave)
        canvas.bind("<Button-1>", lambda _e: command())
        return canvas

    def _window_button(self, parent, glyph, command, danger=False):
        """Кнопка окна как в Windows: плоская, во всю высоту шапки."""
        c = self.c
        hot_bg = "#e81123" if danger else c["hover"]
        hot_fg = "#ffffff" if danger else c["text"]
        btn = tk.Button(
            parent, text=glyph, command=command, relief="flat", bd=0,
            font=(UI_FONT, 10), cursor="hand2", takefocus=False,
            bg=c["titlebar"], fg=c["text"], padx=0, pady=0, width=5,
            activebackground=hot_bg, activeforeground=hot_fg,
            highlightthickness=0)
        btn.pack(side="left", fill="y")
        btn.bind("<Enter>", lambda _e: btn.configure(bg=hot_bg, fg=hot_fg))
        btn.bind("<Leave>",
                 lambda _e: btn.configure(bg=c["titlebar"], fg=c["text"]))
        return btn

    def _on_max_change(self, maximized):
        try:
            if isinstance(self.max_button, tk.Canvas):
                self.max_button.hover_glyph = "❐" if maximized else "＋"
            else:
                self.max_button.configure(text="❐" if maximized else "□")
        except (AttributeError, tk.TclError):
            pass

    # -- страницы -----------------------------------------------------------------

    def _scroll_page(self, key, padx=28, pady=18):
        """Страница со скроллом; карточки лежат на фоне-картинке."""
        c = self.c
        holder = tk.Frame(self.content, bg=c["bg"])
        holder.grid(row=0, column=0, sticky="nsew")
        self.pages[key] = holder

        view = tk.Canvas(holder, bg=c["bg"], highlightthickness=0, bd=0)
        bar = self._vscroll(holder, view.yview, c["bg"], width=8)
        view.configure(
            yscrollcommand=lambda f, l, k=key: self._on_page_scroll(k, f, l))
        view.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

        view_photo = view.create_image(0, 0, anchor="nw")
        pad = tk.Canvas(view, bg=c["bg"], highlightthickness=0, bd=0)
        window_id = view.create_window((padx, pady), window=pad,
                                       anchor="nw", width=1, height=1)
        pad_photo = pad.create_image(0, 0, anchor="nw")
        self._page_views[key] = {
            "view": view, "bar": bar, "pad": pad, "window": window_id,
            "padx": padx, "pady": pady, "view_photo": view_photo,
            "pad_photo": pad_photo, "photo": None, "height": 0,
        }
        self.scroll_canvases[key] = view
        view.bind("<Configure>",
                  lambda _e, k=key: self._schedule_page_layout(k))
        self.root.after_idle(lambda k=key: self._sync_page_layout(k))
        return pad

    def show_page(self, key):
        page = self.pages.get(key)
        if page is None:
            return
        if key != "ingame" and getattr(self, "_hotkey_capture_id", None):
            self._ingame_hotkey_cancel()
        page.tkraise()
        self.current_page = key
        canvas = self.scroll_canvases.get(key)
        if canvas is not None:
            try:
                canvas.yview_moveto(0)
            except tk.TclError:
                pass
        # breadcrumb в шапке окна
        crumb = next((text for k, _icon, text in NAV_ITEMS if k == key), "")
        if getattr(self, "title_crumb", None) is not None:
            try:
                self.title_crumb.configure(text=f"›  {crumb}")
            except tk.TclError:
                pass
        for name, item in self.nav_buttons.items():
            item.set_active(name == key)
        if (key == "gallery" and self.interactive
                and not getattr(self, "_closing", False)):
            try:
                self.root.after(120, self._gallery_maybe_refresh)
            except tk.TclError:
                pass

    def _on_wheel(self, event):
        """Скролл страницы колесом; спискам и журналу даём скроллиться самим."""
        if self._closing:
            return
        try:
            cls = event.widget.winfo_class()
        except Exception:
            cls = ""
        if cls in ("Listbox", "Text", "Treeview", "TComboboxListbox"):
            return
        canvas = self.scroll_canvases.get(self.current_page)
        if canvas is None:
            return
        delta = event.delta
        steps = delta // 120 if abs(delta) >= 120 else (1 if delta > 0 else -1)
        if steps:
            canvas.yview_scroll(-steps, "units")

    # -- хелперы виджетов -----------------------------------------------------------

    def _page_header(self, pad, glyph, title, subtitle=None):
        """Шапка страницы поверх фото: бренд, заголовок и иконка."""
        c = self.c
        height = HEADER_H if subtitle else HEADER_H_COMPACT
        canvas = tk.Canvas(pad, bg=c["bg"], height=height,
                           highlightthickness=0, bd=0)
        canvas.pack(fill="x", pady=(0, 14))
        canvas._header_spec = (glyph, title, subtitle)
        canvas._header_job = None
        canvas._header_key = next(
            (k for k, d in self._page_views.items() if d["pad"] is pad), "")
        self._header_canvases.append(canvas)
        canvas.bind("<Configure>",
                    lambda _e, cv=canvas: self._schedule_header(cv))
        self.root.after_idle(lambda cv=canvas: self._render_header(cv))
        return canvas

    def _schedule_header(self, canvas, delay=30):
        if getattr(self, "_closing", False):
            return
        job = getattr(canvas, "_header_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        try:
            canvas._header_job = self.root.after(
                delay, lambda cv=canvas: self._render_header(cv))
        except tk.TclError:
            canvas._header_job = None

    def _card(self, parent, title=None, subtitle=None, expand=False,
              glyph=None):
        """Карточка: акцентная черта заголовка, подпись и разделитель."""
        c = self.c
        outer = tk.Frame(parent, bg=c["border"])
        outer.pack(fill="both" if expand else "x", expand=expand,
                   pady=(0, 16))
        body = tk.Frame(outer, bg=c["surface"])
        body.pack(fill="both", expand=True, padx=1, pady=1)
        inner = tk.Frame(body, bg=c["surface"], padx=20, pady=16)
        inner.pack(fill="both", expand=True)
        if title:
            head = tk.Frame(inner, bg=c["surface"])
            head.pack(fill="x")
            bar = tk.Frame(head, bg=self.accent, width=3, height=16)
            bar.pack(side="left", pady=2)
            self.accent_bgs.append(bar)
            offset = 10
            if glyph:
                glyph_lbl = tk.Label(
                    head, text=glyph, bg=c["surface"], fg=self.accent,
                    font=(UI_FONT, 11, "bold"))
                glyph_lbl.pack(side="left", padx=(10, 7))
                self.accent_fgs.append(glyph_lbl)
                offset = 0
            tk.Label(head, text=title, bg=c["surface"], fg=c["text"],
                     font=(UI_FONT, 11, "bold"),
                     anchor="w").pack(side="left", padx=(offset, 0))
            if subtitle:
                tk.Label(inner, text=subtitle, bg=c["surface"],
                         fg=c["muted"], font=(UI_FONT, 9), anchor="w",
                         justify="left", wraplength=520).pack(
                             fill="x", pady=(5, 0))
            tk.Frame(inner, bg=c["border"], height=1).pack(
                fill="x", pady=(12, 0))
        return inner

    def _btn_colors(self, kind):
        if kind == "primary":
            base = self.accent
            fg = self.c["on_accent"]
        elif kind == "slot":
            base = self.c["entry"]
            fg = self.c["text"]
        else:
            base = self.c["surface2"]
            fg = self.c["text"]
        factor = 0.95 if self.c.get("base") == "light" else 1.14
        return base, fg, shade(base, factor)

    def _bind_hover(self, btn, kind):
        """Плавный переход цвета кнопки при наведении (~100 мс)."""
        btn.bind("<Enter>",
                 lambda e: self._animate_btn(btn, kind, hover=True))
        btn.bind("<Leave>",
                 lambda e: self._animate_btn(btn, kind, hover=False))

    @staticmethod
    def _lerp_hex(c1, c2, t):
        """Линейная интерполяция двух hex-цветов."""
        try:
            a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
            b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
        except (ValueError, TypeError):
            return c2
        return "#%02x%02x%02x" % tuple(
            int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))

    def _animate_btn(self, btn, kind, hover):
        """Анимация цвета кнопки; hover=True — на подсветку, иначе назад."""
        key = id(btn)
        job = self._btn_anim_jobs.pop(key, None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        base, fg, hot = self._btn_colors(kind)
        target = hot if hover else base
        try:
            current = btn.cget("bg")
        except tk.TclError:
            return
        if current == target:
            return
        steps, delay = 5, 20

        def tick(i=1):
            try:
                if i >= steps:
                    btn.configure(bg=target, activebackground=hot,
                                  activeforeground=fg, fg=fg)
                    self._btn_anim_jobs.pop(key, None)
                    return
                btn.configure(bg=self._lerp_hex(current, target, i / steps))
                self._btn_anim_jobs[key] = self.root.after(
                    delay, lambda: tick(i + 1))
            except (tk.TclError, RuntimeError):
                self._btn_anim_jobs.pop(key, None)

        self._btn_anim_jobs[key] = self.root.after(delay, tick)

    def _button(self, parent, text, command=None, kind="ghost",
                side=None, padx=0, **kw):
        """Своя кнопка с анимацией наведения. kind: primary | ghost."""
        base, fg, hot = self._btn_colors(kind)
        btn = tk.Button(
            parent, text=text, command=command, relief="flat", bd=0,
            font=(UI_FONT, 10, "bold" if kind == "primary" else "normal"),
            padx=14, pady=7, cursor="hand2", bg=base, fg=fg,
            activebackground=hot, activeforeground=fg, takefocus=True,
            highlightthickness=0, **kw)
        if side:
            btn.pack(side=side, padx=padx)
        else:
            btn.pack()
        self._bind_hover(btn, kind)
        if kind == "primary":
            self.primary_buttons.append(btn)
        return btn

    def _hint(self, parent, text, bg=None, wrap=500):
        bg = bg or self.c["surface"]
        lbl = tk.Label(parent, text=text, bg=bg, fg=self.c["muted"],
                       font=(UI_FONT, 9), anchor="w", justify="left",
                       wraplength=wrap)
        lbl.pack(fill="x", pady=(6, 0))
        return lbl

    def _checkbox(self, parent, text, variable, command=None):
        return ThemedCheck(self, parent, text, variable, command=command)

    def _warn_fg(self):
        light = self.c.get("base") == "light"
        return "#b45309" if light else "#ff9f43"

    def _vscroll(self, parent, command, trough, width=8):
        c = self.c
        light = c.get("base") == "light"
        thumb = self._lerp_hex(trough, c["text"], 0.13 if light else 0.20)
        hover = self._lerp_hex(trough, c["text"], 0.24 if light else 0.36)
        return ThinScrollbar(parent, command, trough, thumb, hover, width)

    def _list_selection(self):
        c = self.c
        return (self._lerp_hex(c["list"], self.accent, 0.30), c["text"])

    def _make_listbox(self, parent, height=7):
        c = self.c
        holder = tk.Frame(parent, bg=c["surface"])
        holder.pack(fill="both", expand=True)
        select_bg, select_fg = self._list_selection()
        box = tk.Listbox(
            holder, bg=c["list"], fg=c["text"], selectbackground=select_bg,
            selectforeground=select_fg, activestyle="none", bd=0,
            highlightthickness=1, highlightbackground=c["border"],
            highlightcolor=self.accent, font=(UI_FONT, 10),
            exportselection=False, height=height)
        scroll = self._vscroll(holder, box.yview, c["list"], width=7)
        box.configure(yscrollcommand=scroll.set)
        box.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return box, scroll

    # -- страница «Игра» -----------------------------------------------------------

    def _build_page_game(self):
        c = self.c
        pad = self._scroll_page("game")

        self._page_header(
            pad, "▶", "Запуск",
            "Просто открой клиент Roblox или запусти игру по ссылке "
            "или Place ID.")

        # быстрый запуск клиента и недавние
        top = tk.Frame(pad, bg=c["bg"])
        top.pack(fill="x", pady=(0, 16))

        left = self._side_card(top, "Клиент Roblox", "left", glyph="◉")
        tk.Label(left, text="Открыть клиент без игры — игру можно выбрать "
                            "позже.",
                 bg=c["surface"], fg=c["muted"], font=(UI_FONT, 9),
                 anchor="w", justify="left", wraplength=320).pack(
                     fill="x", pady=(0, 10))
        self.btn_client = tk.Button(
            left, text="▶   ПРОСТО В ROBLOX", relief="flat", bd=0,
            cursor="hand2", font=(UI_FONT, 12, "bold"), pady=12,
            takefocus=True, command=self.launch_client,
            highlightthickness=0)
        self.btn_client.pack(fill="x")
        self.primary_buttons.append(self.btn_client)
        base, fg, hot = self._btn_colors("primary")
        self.btn_client.configure(bg=base, fg=fg, activebackground=hot,
                                  activeforeground=fg)
        self._bind_hover(self.btn_client, "primary")
        self._hint(left, "Если клиент открыт, лаунчер выведет его окно "
                         "вперёд.", wrap=320)

        right = self._side_card(top, "Недавние", "right", glyph="◷")
        self.recent_box, _ = self._make_listbox(right, height=4)
        self.recent_box.bind("<Double-Button-1>", self._recent_activate)
        self._hint(right, "Enter или двойной клик — запустить игру.",
                   wrap=270)

        # выбор установленной версии клиента
        version_card = self._card(
            pad, title="Версия Roblox", glyph="◈",
            subtitle="Если новая версия что-то сломала, запусти другую "
                     "установленную сборку. «Авто» — самая новая.")
        ver_row = tk.Frame(version_card, bg=c["surface"])
        ver_row.pack(fill="x", pady=(10, 0))
        self.roblox_version_var = tk.StringVar()
        self.roblox_version_box = ttk.Combobox(
            ver_row, textvariable=self.roblox_version_var,
            state="readonly", width=54)
        self.roblox_version_box.pack(side="left", fill="x", expand=True)
        self.roblox_version_box.bind(
            "<<ComboboxSelected>>", self._roblox_version_pick)
        self._button(ver_row, "Обновить", self._refresh_roblox_versions,
                     side="left", padx=(8, 0))
        self._button(ver_row, "Папка", self._open_roblox_versions_dir,
                     side="left")
        self.roblox_version_note_var = tk.StringVar(value="")
        ver_note = tk.Label(
            version_card, textvariable=self.roblox_version_note_var,
            bg=c["surface"], fg=self.accent, font=(UI_FONT, 9, "bold"),
            anchor="w", justify="left", wraplength=560)
        ver_note.pack(fill="x", pady=(8, 0))
        self.accent_fgs.append(ver_note)
        self._hint(
            version_card,
            "Выбранная сборка запускается напрямую — без бутстрапера и "
            "автообновления. Вход в аккаунт, FastFlags и оверлей работают "
            "как обычно. Если папку версии удалят, лаунчер сам вернётся "
            "к самой новой.", wrap=560)
        self._refresh_roblox_versions()

        # запуск игры по ссылке или Place ID
        inner = self._card(pad)
        tk.Label(inner, text="ВЫБРАННАЯ ИГРА", bg=c["surface"],
                 fg=c["muted"], font=(UI_FONT, 8, "bold"),
                 anchor="w").pack(fill="x")

        head = tk.Frame(inner, bg=c["surface"])
        head.pack(fill="x", pady=(9, 0))

        self.icon_box = tk.Frame(
            head, bg=c["surface2"], width=88, height=88,
            highlightthickness=1, highlightbackground=c["border"])
        self.icon_box.pack(side="left")
        self.icon_box.pack_propagate(False)
        self.icon_lbl = tk.Label(self.icon_box, text="▶", bg=c["surface2"],
                                 fg=c["muted"], font=(UI_FONT, 24))
        self.icon_lbl.pack(fill="both", expand=True)
        self.icon_img = None

        col = tk.Frame(head, bg=c["surface"])
        col.pack(side="left", fill="both", expand=True, padx=(16, 0))
        self.game_name_var = tk.StringVar(value="Игра не выбрана")
        self.info_var = tk.StringVar(
            value="Вставь ссылку на игру или введи Place ID")
        self.pid_var = tk.StringVar(value="")
        tk.Label(col, textvariable=self.game_name_var, bg=c["surface"],
                 fg=c["text"], font=(UI_FONT, 16, "bold"), anchor="w",
                 justify="left", wraplength=420).pack(fill="x")
        tk.Label(col, textvariable=self.info_var, bg=c["surface"],
                 fg=c["muted"], font=(UI_FONT, 9), anchor="w",
                 wraplength=420, justify="left").pack(fill="x", pady=(4, 4))
        pid_lbl = tk.Label(col, textvariable=self.pid_var, bg=c["surface"],
                           fg=self.accent, font=(UI_FONT, 9, "bold"),
                           anchor="w")
        pid_lbl.pack(fill="x")
        self.accent_fgs.append(pid_lbl)

        row = tk.Frame(inner, bg=c["surface"])
        row.pack(fill="x", pady=(14, 0))
        self.entry_var = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.entry_var)
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<KeyRelease>", self.on_entry_changed)
        entry.bind("<Return>", lambda _e: self.play())
        self._button(row, "Вставить", self.paste_clipboard,
                     side="left", padx=(8, 0))

        acc_strip = tk.Frame(inner, bg=c["surface2"], padx=11, pady=8)
        acc_strip.pack(fill="x", pady=(12, 0))
        acc_glyph = tk.Label(acc_strip, text="◉", bg=c["surface2"],
                             fg=self.accent, font=(UI_FONT, 12, "bold"))
        acc_glyph.pack(side="left")
        self.accent_fgs.append(acc_glyph)
        acc_text = tk.Frame(acc_strip, bg=c["surface2"])
        acc_text.pack(side="left", fill="x", expand=True, padx=(10, 8))
        tk.Label(acc_text, text="АККАУНТ ДЛЯ ЗАПУСКА", bg=c["surface2"],
                 fg=c["muted"], font=(UI_FONT, 8, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(acc_text, textvariable=self.game_account_var,
                 bg=c["surface2"], fg=c["text"], font=(UI_FONT, 9),
                 anchor="w", justify="left", wraplength=420).pack(fill="x")
        self._button(acc_strip, "Сменить",
                     lambda: self.show_page("accounts"), side="right")

        self.btn_play = tk.Button(
            inner, text="▶   ИГРАТЬ ПО ID", relief="flat", bd=0,
            cursor="hand2", font=(UI_FONT, 13, "bold"), pady=12,
            takefocus=True, command=self.play, highlightthickness=0)
        self.btn_play.pack(fill="x", pady=(12, 0))
        self.primary_buttons.append(self.btn_play)
        base, fg, hot = self._btn_colors("primary")
        self.btn_play.configure(bg=base, fg=fg, activebackground=hot,
                                activeforeground=fg)
        self._bind_hover(self.btn_play, "primary")

        actions = tk.Frame(pad, bg=c["bg"])
        actions.pack(fill="x")
        self._button(actions, "Открыть в браузере", self.open_in_browser,
                     side="left")
        self._button(actions, "В избранное", self.add_favorite,
                     side="left", padx=8)
        self._button(actions, "Копировать ID", self.copy_id,
                     side="left")

    # -- страница «Аккаунты» ---------------------------------------------------------

    def _build_page_accounts(self):
        c = self.c
        pad = self._scroll_page("accounts")

        self._page_header(
            pad, "◉", "Аккаунты",
            "Быстрый переключатель: выбери аккаунт — и следующая игра "
            "запустится под ним. Вход — только через сайт Roblox в "
            "браузере.")

        row = tk.Frame(pad, bg=c["bg"])
        row.pack(fill="x")

        left = self._side_card(row, "Мои аккаунты", "left", glyph="◉")
        self.accounts_box, _ = self._make_listbox(left, height=9)
        self.accounts_box.bind("<Double-Button-1>",
                               lambda _e: self._activate_selected_account())
        btns = tk.Frame(left, bg=c["surface"])
        btns.pack(fill="x", pady=(10, 0))
        self._button(btns, "＋ Добавить", self._open_add_account_dialog,
                     kind="primary", side="left")
        self._button(btns, "Удалить", self._account_remove, side="left",
                     padx=8)
        self._hint(left, "Двойной клик — сделать аккаунт активным. "
                         "Вход только через сайт Roblox: пароль лаунчеру "
                         "не нужен и нигде не хранится.", wrap=320)

        right = self._side_card(row, "Активный аккаунт", "right", glyph="✓")
        head = tk.Frame(right, bg=c["surface"])
        head.pack(fill="x")
        avatar = tk.Frame(head, bg=c["surface2"], width=84, height=84,
                          highlightthickness=1,
                          highlightbackground=c["border"])
        avatar.pack(side="left")
        avatar.pack_propagate(False)
        self.account_avatar_label = tk.Label(
            avatar, text="◉", bg=c["surface2"], fg=self.accent,
            font=(UI_FONT, 26, "bold"))
        self.account_avatar_label.pack(fill="both", expand=True)
        self.accent_fgs.append(self.account_avatar_label)

        col = tk.Frame(head, bg=c["surface"])
        col.pack(side="left", fill="both", expand=True, padx=(16, 0))
        tk.Label(col, textvariable=self.active_display_var, bg=c["surface"],
                 fg=c["text"], font=(UI_FONT, 16, "bold"), anchor="w",
                 justify="left", wraplength=280).pack(fill="x")
        tk.Label(col, textvariable=self.active_login_var, bg=c["surface"],
                 fg=self.accent, font=(UI_FONT, 10, "bold"),
                 anchor="w").pack(fill="x", pady=(3, 3))
        tk.Label(col, textvariable=self.active_meta_var, bg=c["surface"],
                 fg=c["muted"], font=(UI_FONT, 8), anchor="w",
                 justify="left", wraplength=280).pack(fill="x")

        actions = tk.Frame(right, bg=c["surface"])
        actions.pack(fill="x", pady=(14, 0))
        self._button(actions, "Добавить аккаунт",
                     self._open_add_account_dialog, kind="primary",
                     side="left")
        self._button(actions, "Обновить", self._refresh_account_ui,
                     side="left", padx=8)

        self.account_status_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.account_status_var,
                 bg=c["surface"], fg=c["muted"], font=(UI_FONT, 9),
                 anchor="w", justify="left", wraplength=330).pack(
                     fill="x", pady=(12, 0))

        info = self._card(pad, title="Как это работает", glyph="ⓘ")
        for text in (
            "Активный аккаунт применяется при запуске игры: лаунчер "
            "получает одноразовый тикет Roblox и открывает клиент уже "
            "под нужным аккаунтом.",
            "Если Roblox уже запущен, лаунчер спросит, закрыть ли его "
            "перед сменой аккаунта.",
            "Вход открывает сайт Roblox в отдельном окне Edge/Chrome "
            "с временным профилем — основной браузер не затрагивается.",
            "Пароль лаунчер не запрашивает и не сохраняет. Сессия "
            "Roblox лежит в accounts.json и зашифрована средствами "
            "Windows (DPAPI).",
        ):
            tk.Label(info, text="•  " + text, bg=c["surface"],
                     fg=c["muted"], font=(UI_FONT, 9), anchor="w",
                     justify="left", wraplength=560).pack(
                         fill="x", pady=(3, 0))

    def _active_account(self):
        active = self._accounts.get("active") or ""
        return next((item for item in self._accounts.get("accounts") or []
                     if item["id"] == active), None)

    @staticmethod
    def _account_label(account):
        display = account.get("display") or account.get("name") or "аккаунт"
        login = account.get("name") or ""
        if login and login.lower() != display.lower():
            return f"{display}  @{login}"
        return display

    def _refresh_account_ui(self):
        try:
            self._accounts = accounts_state()
        except Exception:
            self._accounts = {"active": "", "accounts": []}
        accounts = self._accounts.get("accounts") or []
        active = self._active_account()
        if active is not None:
            display = (active.get("display") or active.get("name")
                       or "Аккаунт")
            login = f"@{active['name']}" if active.get("name") else ""
            self.account_name_var.set(trunc(display, 20))
            self.account_user_var.set(login or "Roblox")
            self.game_account_var.set(
                f"{display}  {login}".strip()
                + "  ·  игра запустится под ним")
            self.active_display_var.set(display)
            self.active_login_var.set(login or "—")
            meta = f"Roblox ID {active.get('user_id') or '—'}"
            if active.get("last_used"):
                meta += "  ·  последний запуск " + time.strftime(
                    "%d.%m.%Y", time.localtime(active["last_used"]))
            self.active_meta_var.set(meta)
        else:
            self.account_name_var.set("Не выбран")
            self.account_user_var.set("Нажми, чтобы войти")
            self.game_account_var.set(
                "Аккаунт не выбран — игра запустится как обычно")
            self.active_display_var.set("Аккаунт не выбран")
            self.active_login_var.set("Добавь аккаунт, чтобы играть под ним")
            self.active_meta_var.set("")

        dot = getattr(self, "sidebar_account_dot", None)
        if dot is not None:
            try:
                dot.configure(fg=self.accent if active else self.c["muted"])
            except tk.TclError:
                pass

        box = getattr(self, "accounts_box", None)
        if box is not None:
            try:
                box.delete(0, "end")
                for account in accounts:
                    mark = ("●  " if account["id"] == self._accounts["active"]
                            else "     ")
                    box.insert("end", mark + self._account_label(account))
                if not accounts:
                    box.insert("end", "   аккаунтов пока нет")
                elif active is not None:
                    index = [a["id"] for a in accounts].index(active["id"])
                    box.selection_clear(0, "end")
                    box.selection_set(index)
                    box.see(index)
            except (tk.TclError, ValueError):
                pass

        status = getattr(self, "account_status_var", None)
        if status is not None:
            try:
                if active is not None:
                    status.set(f"Аккаунтов: {len(accounts)}  ·  выбран "
                               f"«{active.get('display') or active['name']}»")
                elif accounts:
                    status.set(f"Аккаунтов: {len(accounts)}  ·  активный "
                               "не выбран")
                else:
                    status.set("Аккаунтов пока нет — добавь первый.")
            except tk.TclError:
                pass

        self._set_account_avatar(active)
        self._refresh_account_menu()

    def _set_account_avatar(self, account):
        label = getattr(self, "account_avatar_label", None)
        if label is None:
            return
        photo = None
        if account is not None:
            photo = self._avatar_photo(account.get("user_id"), 84)
        try:
            if photo is not None:
                self._account_avatar_photo = photo
                label.configure(image=photo, text="")
            else:
                self._account_avatar_photo = None
                name = ((account or {}).get("display")
                        or (account or {}).get("name") or "?")
                label.configure(image="", text=str(name)[:1].upper(),
                                fg=self.accent)
        except tk.TclError:
            pass

    def _avatar_photo(self, user_id, size):
        path = account_avatar_path(user_id) if user_id else None
        if path is None:
            return None
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            stamp = 0
        key = (str(user_id), int(size), stamp)
        cached = self._avatar_photos.get(key)
        if cached is not None:
            return cached
        photo = None
        try:
            if HAVE_PIL:
                image = Image.open(path).convert("RGBA").resize(
                    (size, size), Image.LANCZOS)
                photo = ImageTk.PhotoImage(image)
            else:
                image = tk.PhotoImage(file=str(path))
                factor = max(1, -(-max(image.width(), image.height())
                                  // max(1, size)))
                if factor > 1:
                    image = image.subsample(factor, factor)
                photo = image
        except Exception:
            photo = None
        if photo is not None:
            self._avatar_photos[key] = photo
            if len(self._avatar_photos) > 24:
                self._avatar_photos.pop(next(iter(self._avatar_photos)))
        return photo

    def _refresh_account_menu(self):
        menu = getattr(self, "account_menu", None)
        button = getattr(self, "account_switch_btn", None)
        if menu is None or button is None:
            return
        accounts = self._accounts.get("accounts") or []
        active = self._active_account()
        try:
            menu.delete(0, "end")
            if accounts:
                label = (active.get("display") or active.get("name")
                         if active is not None else "Войти")
                button.configure(text=f"◉  {trunc(label, 18)}")
                for account in accounts:
                    mark = "✓  " if account.get("id") == self._accounts.get(
                        "active") else "\u2003  "
                    menu.add_command(
                        label=mark + self._account_label(account),
                        command=lambda a=account["id"]:
                        self._choose_account(a))
                menu.add_separator()
                menu.add_command(label="Добавить аккаунт…",
                                 command=self._open_add_account_dialog)
                menu.add_command(label="Управление аккаунтами…",
                                 command=lambda: self.show_page("accounts"))
            else:
                button.configure(text="◉  Войти")
                menu.add_command(label="Войти в аккаунт…",
                                 command=self._open_add_account_dialog)
                menu.add_command(label="Подробнее об аккаунтах…",
                                 command=lambda: self.show_page("accounts"))
        except tk.TclError:
            pass

    def _selected_account(self):
        box = getattr(self, "accounts_box", None)
        accounts = self._accounts.get("accounts") or []
        if box is None or not accounts:
            return None
        try:
            selection = box.curselection()
        except tk.TclError:
            return None
        if not selection or selection[0] >= len(accounts):
            self.status_var.set("Выбери аккаунт в списке")
            return None
        return accounts[selection[0]]

    def _activate_selected_account(self):
        account = self._selected_account()
        if account is not None:
            self._choose_account(account["id"])

    def _choose_account(self, account_id):
        result = set_active_account(account_id)
        if not result["ok"]:
            self.status_var.set(result["error"] or "Не удалось выбрать аккаунт")
            return
        self._refresh_account_ui()
        active = self._active_account()
        if active is None:
            return
        who = active.get("display") or active.get("name") or "аккаунт"
        note = " — применится к следующему запуску" if player_pids() else ""
        self.status_var.set(f"Аккаунт: {who}{note}")
        self.log(f"Активный аккаунт: @{active.get('name')}")

    def _account_remove(self):
        account = self._selected_account() or self._active_account()
        if account is None:
            if self.interactive:
                messagebox.showinfo(APP_NAME, "В лаунчере нет аккаунтов.",
                                    parent=self.root)
            return
        if not self.interactive:
            return
        if not messagebox.askyesno(
                APP_NAME,
                f"Удалить аккаунт «{self._account_label(account)}»?\n"
                "Сохранённая сессия будет удалена — для игры под ним "
                "придётся войти заново.",
                parent=self.root):
            return
        result = remove_account(account["id"])
        if not result["ok"]:
            messagebox.showerror(APP_NAME, result["error"],
                                 parent=self.root)
            return
        self._refresh_account_ui()
        self.status_var.set("Аккаунт удалён")
        self.log(f"Аккаунт @{account.get('name')} удалён")

    def _login_window_alive(self):
        win = getattr(self, "_login_dialog", None)
        if win is None:
            return False
        try:
            return bool(win.winfo_exists())
        except tk.TclError:
            return False

    def _set_login_status(self, text, error=False):
        if not self._login_window_alive():
            return
        try:
            self._login_status_var.set(text)
            self._login_status_lbl.configure(
                fg=self._warn_fg() if error else self.c["muted"])
        except tk.TclError:
            pass

    def _set_login_busy(self, busy, text=None):
        self._login_busy = busy
        widget = getattr(self, "_login_browser_btn", None)
        if widget is not None:
            try:
                self._btn_state(widget, "primary", not busy)
            except tk.TclError:
                pass
        if text is not None:
            self._set_login_status(text)

    def _open_add_account_dialog(self):
        if self._login_busy:
            self.status_var.set("Дождись завершения входа")
            return
        if self._login_window_alive():
            try:
                self._login_dialog.lift()
                self._login_dialog.focus_force()
            except tk.TclError:
                pass
            return
        c = self.c
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title("Добавить аккаунт")
        win.configure(bg=c["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        self._login_dialog = win
        self._login_cancel = None

        body = tk.Frame(win, bg=c["bg"], padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Добавить аккаунт", bg=c["bg"], fg=c["text"],
                 font=(UI_FONT, 16, "bold"), anchor="w").pack(fill="x")
        tk.Label(body, text="Вход только через сайт Roblox в браузере. "
                            "Пароль лаунчер не запрашивает и не сохраняет — "
                            "только зашифрованную сессию для запуска игр.",
                 bg=c["bg"], fg=c["muted"], font=(UI_FONT, 9),
                 anchor="w", justify="left", wraplength=430).pack(
                     fill="x", pady=(4, 14))

        outer = tk.Frame(body, bg=c["border"])
        outer.pack(fill="x", pady=(0, 12))
        br = tk.Frame(outer, bg=c["surface"], padx=14, pady=12)
        br.pack(fill="x", padx=1, pady=1)
        tk.Label(br, text="ВХОД ЧЕРЕЗ САЙТ", bg=c["surface"],
                 fg=c["muted"], font=(UI_FONT, 8, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(br, text="Откроется отдельное окно Edge или Chrome с "
                          "временным профилем. Войди на сайте Roblox — "
                          "лаунчер заберёт сессию и закроет окно. Капча "
                          "и 2FA вводятся прямо на сайте.",
                 bg=c["surface"], fg=c["muted"], font=(UI_FONT, 9),
                 anchor="w", justify="left", wraplength=430).pack(
                     fill="x", pady=(8, 10))
        self._login_browser_btn = self._button(
            br, "Войти через сайт Roblox", self._account_login_browser,
            kind="primary")
        self._login_browser_btn.pack_configure(fill="x")
        tk.Label(br, text="Основной браузер не затрагивается: профиль "
                          "создаётся во временной папке и удаляется.",
                 bg=c["surface"], fg=c["muted"], font=(UI_FONT, 8),
                 anchor="w", justify="left", wraplength=430).pack(
                     fill="x", pady=(8, 0))

        self._login_status_var = tk.StringVar(value="")
        self._login_status_lbl = tk.Label(
            body, textvariable=self._login_status_var, bg=c["bg"],
            fg=c["muted"], font=(UI_FONT, 9), anchor="w",
            justify="left", wraplength=430)
        self._login_status_lbl.pack(fill="x", pady=(2, 10))

        close_btn = self._button(body, "Закрыть",
                                 self._close_login_dialog)
        close_btn.pack_configure(side="right")

        win.protocol("WM_DELETE_WINDOW", self._close_login_dialog)
        win.update_idletasks()
        width = max(470, win.winfo_reqwidth())
        height = win.winfo_reqheight()
        try:
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
            rw = self.root.winfo_width()
            rh = self.root.winfo_height()
        except tk.TclError:
            rx = ry = rw = rh = 0
        if rw > 1 and rh > 1:
            x = rx + max(0, (rw - width) // 2)
            y = ry + max(0, (rh - height) // 2)
        else:
            x = y = 120
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.deiconify()
        try:
            win.grab_set()
            self._login_browser_btn.focus_set()
        except tk.TclError:
            pass

    def _close_login_dialog(self):
        win = getattr(self, "_login_dialog", None)
        if self._login_cancel is not None:
            try:
                self._login_cancel.set()
            except Exception:
                pass
        self._login_dialog = None
        widget = getattr(self, "_login_browser_btn", None)
        if widget is not None:
            try:
                self.primary_buttons.remove(widget)
            except ValueError:
                pass
        if win is None:
            return
        try:
            win.grab_release()
        except tk.TclError:
            pass
        try:
            win.destroy()
        except tk.TclError:
            pass

    def _finish_account_add(self, added, method):
        account = (added or {}).get("account") or {}
        set_active_account(account.get("id"))
        self._refresh_account_ui()
        who = account.get("display") or account.get("name") or "аккаунт"
        self.status_var.set(f"Аккаунт «{who}» добавлен и выбран")
        self.log(f"Добавлен аккаунт @{account.get('name')} ({method})")
        self._close_login_dialog()

    def _account_login_browser(self):
        if self._login_busy:
            return
        self._login_cancel = threading.Event()
        cancel = self._login_cancel
        self._set_login_busy(True, "Открываю сайт Roblox…")

        def report(text):
            self.q.put(lambda t=text: self._set_login_status(t))

        def worker():
            result = browser_login(cancel_event=cancel, status_cb=report)
            added = None
            if result.get("ok"):
                added = add_account(result["cookie"], result.get("profile"))
            self.q.put(lambda: self._browser_login_done(result, added))

        threading.Thread(target=worker, daemon=True).start()

    def _browser_login_done(self, result, added):
        self._login_busy = False
        self._login_cancel = None
        if self._login_window_alive():
            self._set_login_busy(False)
        if result.get("cancelled"):
            self._set_login_status("Вход через сайт отменён.")
            return
        if not result.get("ok"):
            self._set_login_status(result.get("error")
                                   or "Не удалось войти.", error=True)
            return
        if not added or not added.get("ok"):
            error = (added or {}).get("error") or \
                "Не удалось сохранить аккаунт."
            self._set_login_status(error, error=True)
            return
        self._finish_account_add(added, "через сайт")

    # -- страница «Избранное» --------------------------------------------------------

    def _side_card(self, parent, title, side, glyph=None):
        """Карточка в ряду карточек (side: left/right)."""
        c = self.c
        outer = tk.Frame(parent, bg=c["border"])
        padx = (0, 8) if side == "left" else (8, 0)
        outer.pack(side=side, fill="both", expand=True, padx=padx)
        body = tk.Frame(outer, bg=c["surface"])
        body.pack(fill="both", expand=True, padx=1, pady=1)
        inner = tk.Frame(body, bg=c["surface"], padx=18, pady=14)
        inner.pack(fill="both", expand=True)
        head = tk.Frame(inner, bg=c["surface"])
        head.pack(fill="x", pady=(0, 10))
        bar = tk.Frame(head, bg=self.accent, width=3, height=14)
        bar.pack(side="left", pady=2)
        self.accent_bgs.append(bar)
        offset = 10
        if glyph:
            glyph_lbl = tk.Label(
                head, text=glyph, bg=c["surface"], fg=self.accent,
                font=(UI_FONT, 10, "bold"))
            glyph_lbl.pack(side="left", padx=(10, 6))
            self.accent_fgs.append(glyph_lbl)
            offset = 0
        tk.Label(head, text=title, bg=c["surface"], fg=c["text"],
                 font=(UI_FONT, 11, "bold")).pack(
                     side="left", padx=(offset, 0))
        return inner

    def _build_page_favorites(self):
        c = self.c
        pad = self._scroll_page("favorites")

        self._page_header(
            pad, "★", "Избранное и история",
            "Двойной клик по строке — запустить игру")

        row = tk.Frame(pad, bg=c["bg"])
        row.pack(fill="both", expand=True)

        fav = self._side_card(row, "Избранное", "left", glyph="★")
        self.fav_box, _ = self._make_listbox(fav, height=15)
        self.fav_box.bind("<Double-Button-1>", lambda _e: self._fav_play())
        btns = tk.Frame(fav, bg=c["surface"])
        btns.pack(fill="x", pady=(10, 0))
        self._button(btns, "Запустить", self._fav_play, side="left")
        self._button(btns, "Удалить", self._fav_remove, side="left", padx=8)

        hist = self._side_card(row, "История запусков", "right", glyph="◷")
        self.hist_box, _ = self._make_listbox(hist, height=15)
        self.hist_box.bind("<Double-Button-1>", lambda _e: self._hist_play())
        btns2 = tk.Frame(hist, bg=c["surface"])
        btns2.pack(fill="x", pady=(10, 0))
        self._button(btns2, "Запустить", self._hist_play, side="left")
        self._button(btns2, "Очистить", self._hist_clear, side="left",
                     padx=8)

    # -- страница «Флаги» -----------------------------------------------------------

    def _build_page_flags(self):
        c = self.c
        pad = self._scroll_page("flags")

        self._page_header(
            pad, "⚑", "Флаги и настройки клиента",
            "FastFlags — те же настройки, что в Bloxstrap/Froststrap. "
            "Пишутся в ClientAppSettings.json через слияние: чужие "
            "флаги не затираются.")

        # -- быстрые флаги
        inner = self._card(pad, title="Быстрые флаги", glyph="⚡")
        gridf = tk.Frame(inner, bg=c["surface"])
        gridf.pack(fill="x", pady=(6, 0))
        gridf.grid_columnconfigure(1, weight=1)

        def _row(row, label, widget):
            tk.Label(gridf, text=label, bg=c["surface"], fg=c["text"],
                     font=(UI_FONT, 10)).grid(
                row=row, column=0, sticky="w", pady=4, padx=(0, 14))
            widget.grid(row=row, column=1, sticky="ew", pady=4)

        fps_values = list(FPS_CHOICES)
        if (self.cfg["fps_limit"]
                and str(self.cfg["fps_limit"]) not in fps_values):
            fps_values.append(str(self.cfg["fps_limit"]))
        self.combo_fps = ttk.Combobox(gridf, values=fps_values,
                                      state="normal")
        self.combo_fps.set(
            str(self.cfg["fps_limit"]) if self.cfg["fps_limit"]
            else "Как в игре")
        self.combo_fps.bind("<<ComboboxSelected>>", self._on_fps_change)
        self.combo_fps.bind("<Return>", self._on_fps_change)
        self.combo_fps.bind("<FocusOut>", self._on_fps_change)
        _row(0, "Ограничение FPS", self.combo_fps)

        self.combo_api = ttk.Combobox(
            gridf, values=["Авто"] + GRAPHICS_MODES, state="readonly")
        self.combo_api.set(self.cfg["graphics_mode"] or "Авто")
        self.combo_api.bind("<<ComboboxSelected>>", self._on_api_change)
        _row(1, "Графический API", self.combo_api)

        self.api_hint = tk.Label(
            gridf, text="", bg=c["surface"], fg=self._warn_fg(),
            font=(UI_FONT, 9), anchor="w", justify="left",
            wraplength=480)
        self.api_hint.grid(row=2, column=0, columnspan=2, sticky="w",
                           pady=(0, 4))
        self._update_api_hint()

        self.combo_quality = ttk.Combobox(
            gridf, values=["Как в игре"] + [str(i) for i in range(1, 11)],
            state="readonly")
        self.combo_quality.set(
            str(self.cfg["quality_level"]) if self.cfg["quality_level"]
            else "Как в игре")
        self.combo_quality.bind("<<ComboboxSelected>>",
                                self._on_quality_change)
        _row(3, "Качество графики", self.combo_quality)

        tk.Label(gridf, text="FPS можно ввести вручную: число от 1 до 1000. "
                            "«Как в игре» или пустое поле — без ограничения.",
                 bg=c["surface"], fg=c["muted"], font=(UI_FONT, 8),
                 anchor="w", justify="left", wraplength=520).grid(
                     row=5, column=0, columnspan=2, sticky="w",
                     pady=(8, 0))

        self.var_apply_flags = tk.BooleanVar(
            value=bool(self.cfg["apply_flags_on_launch"]))
        self._checkbox(
            gridf, "Применять флаги перед каждым запуском",
            self.var_apply_flags, command=self._save_apply_flags_var).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        btn_row = tk.Frame(gridf, bg=c["surface"])
        btn_row.grid(row=6, column=0, columnspan=2, sticky="ew",
                     pady=(10, 0))
        self._button(btn_row, "Применить сейчас", self.apply_flags_now,
                     side="left")
        self.flags_status = tk.StringVar(value="")
        tk.Label(btn_row, textvariable=self.flags_status, bg=c["surface"],
                 fg=c["muted"], font=(UI_FONT, 9)).pack(
                     side="left", padx=12)

        # -- панель бутстрапера
        inner2 = self._card(pad, title="Панель бутстрапера",
                            subtitle=("Читает и пишет конфиг твоего "
                                      "бутстрапера — только известные "
                                      "ключи, остальное не трогает."),
                            glyph="⚙")
        self.boot_title = tk.Label(inner2, text="", bg=c["surface"],
                                   fg=c["text"],
                                   font=(UI_FONT, 11, "bold"),
                                   anchor="w")
        self.boot_title.pack(fill="x", pady=(6, 0))
        self.boot_status = tk.StringVar(value="")
        tk.Label(inner2, textvariable=self.boot_status, bg=c["surface"],
                 fg=c["muted"], font=(UI_FONT, 9), anchor="w",
                 wraplength=520, justify="left").pack(fill="x")

        self.boot_body = tk.Frame(inner2, bg=c["surface"])

        # регион (только Froststrap)
        self.region_row = tk.Frame(self.boot_body, bg=c["surface"])
        tk.Label(self.region_row, text="Регион сервера", bg=c["surface"],
                 fg=c["text"], font=(UI_FONT, 10)).pack(
                     side="left", padx=(0, 12))
        self.combo_region = ttk.Combobox(
            self.region_row, values=datacenter_regions(), state="readonly",
            width=24)
        self.combo_region.set("Auto")
        self.combo_region.pack(side="left")
        self.region_row.pack(fill="x", pady=(6, 0))

        # чекбоксы в две колонки: общие слева, только Froststrap справа
        self.boot_cols = tk.Frame(self.boot_body, bg=c["surface"])
        col_left = tk.Frame(self.boot_cols, bg=c["surface"])
        col_left.pack(side="left", fill="both", expand=True)
        self.col_right = tk.Frame(self.boot_cols, bg=c["surface"])
        self.col_right.pack(side="left", fill="both", expand=True,
                            padx=(18, 0))
        self.boot_vars = []
        for key, label in BOOT_BOOLS_SHARED:
            var = tk.BooleanVar(value=False)
            self.boot_vars.append((key, var))
            self._checkbox(col_left, label, var).pack(anchor="w", pady=2)
        for key, label in BOOT_BOOLS_FROST:
            var = tk.BooleanVar(value=False)
            self.boot_vars.append((key, var))
            self._checkbox(self.col_right, label, var).pack(
                anchor="w", pady=2)
        self.boot_cols.pack(fill="x", pady=(8, 0))

        self.boot_path = tk.Label(self.boot_body, text="", bg=c["surface"],
                                  fg=c["muted"], font=(UI_FONT, 9),
                                  anchor="w", wraplength=520, justify="left")
        self.boot_path.pack(fill="x", pady=(10, 0))

        btn_row2 = tk.Frame(self.boot_body, bg=c["surface"])
        btn_row2.pack(fill="x", pady=(8, 0))
        self._button(btn_row2, "Обновить из файла", self.refresh_boot_panel,
                     side="left")
        tk.Label(btn_row2, text="изменения запишет «Сохранить всё» внизу",
                 bg=c["surface"], fg=c["muted"],
                 font=(UI_FONT, 8)).pack(side="left", padx=10)
        self.boot_body.pack(fill="x")

        self.boot_none = tk.Label(
            inner2, bg=c["surface"], fg=c["muted"], font=(UI_FONT, 9),
            justify="left", wraplength=520, anchor="w",
            text=("Ни Froststrap, ни Bloxstrap не найдены — панель "
                  "недоступна. Быстрые флаги выше работают всегда."))
        self._load_boot_panel()

    def _boot_extras(self, show):
        """Регион и Froststrap-колонка — только у Froststrap."""
        for widget in (self.region_row, self.boot_cols):
            widget.pack_forget()
        if show:
            self.region_row.pack(fill="x", pady=(6, 0))
            self.boot_cols.pack(fill="x", pady=(8, 0))
            self.col_right.pack(side="left", fill="both", expand=True,
                                padx=(18, 0))
        else:
            self.col_right.pack_forget()
            self.boot_cols.pack(fill="x", pady=(8, 0))

    def _load_boot_panel(self, note=""):
        kind, path = find_bootstrapper_settings()
        self.boot_kind = kind
        self.boot_config_path = path
        if not kind or not path:
            self.boot_body.pack_forget()
            self.boot_title.configure(text="Бутстрапер не найден")
            self.boot_none.pack(fill="x", pady=(8, 0))
            self.boot_status.set("")
            return
        self.boot_none.pack_forget()
        self.boot_body.pack(fill="x")
        self._boot_extras(kind == "froststrap")
        name = "Froststrap" if kind == "froststrap" else "Bloxstrap"
        self.boot_title.configure(text=f"Конфиг: {name}")
        data = read_json_dict(path)
        if kind == "froststrap":
            regions = datacenter_regions()
            current = str(data.get("SelectedRegion") or "Auto")
            if current not in regions:
                regions = [current] + regions
            self.combo_region.configure(values=regions)
            self.combo_region.set(current)
        for key, var in self.boot_vars:
            var.set(bool(data.get(key, False)))
        self.boot_path.configure(text=str(path))
        if note:
            self.boot_status.set(note)

    def _write_boot_panel(self):
        """Записать панель бутстрапера. Возвращает текст ошибки или \"\"."""
        kind, path = find_bootstrapper_settings()
        if not path:
            return ""
        data = read_json_dict(path)
        if kind == "froststrap":
            data["SelectedRegion"] = self.combo_region.get().strip() or "Auto"
        for key, var in self.boot_vars:
            data[key] = bool(var.get())
        ok, err = write_json_dict(path, data)
        if not ok:
            self.boot_status.set("Ошибка записи")
            self.log(f"Не удалось записать {path}: {err}")
            return "бутстрапер: ошибка записи"
        name = "Froststrap" if kind == "froststrap" else "Bloxstrap"
        note = f"Сохранено в {name}"
        if bootstrapper_running():
            note += " (бутстрапер запущен — при выходе может перезаписать)"
        self.boot_status.set(note)
        self.log(f"Настройки {name} сохранены: {path}")
        return ""

    def refresh_boot_panel(self):
        self._load_boot_panel(note="Обновлено из файла")
        self.log("Конфиг бутстрапера перечитан")

    def _save_apply_flags_var(self):
        self.cfg["apply_flags_on_launch"] = bool(self.var_apply_flags.get())
        save_config(self.cfg)
        self.status_var.set("Настройка сохранена")

    def _on_fps_change(self, _event=None):
        raw = self.combo_fps.get().strip()
        if raw in ("", "Как в игре"):
            value = 0
        else:
            try:
                value = int(raw)
            except ValueError:
                value = -1
            if value != 0 and not 1 <= value <= 1000:
                value = -1
        if value < 0:
            current = int(self.cfg.get("fps_limit") or 0)
            self.combo_fps.set("Как в игре" if not current else str(current))
            self.flags_status.set("FPS: введи число от 1 до 1000")
            self.status_var.set("Некорректное ограничение FPS")
            return
        display = str(value) if value else "Как в игре"
        if self.combo_fps.get() != display:
            self.combo_fps.set(display)
        if int(self.cfg.get("fps_limit") or 0) == value:
            return
        self.cfg["fps_limit"] = value
        self._flags_changed("Ограничение FPS")

    def _on_api_change(self, _event=None):
        raw = self.combo_api.get()
        value = "" if raw == "Авто" else raw if raw in GRAPHICS_MODES else ""
        self._update_api_hint()
        if value == str(self.cfg.get("graphics_mode") or ""):
            return
        self.cfg["graphics_mode"] = value
        self._flags_changed("Графический API")

    def _update_api_hint(self):
        """Предупреждение о багах бета-рендеров Roblox."""
        mode = str(self.cfg.get("graphics_mode") or "")
        text = ""
        if mode == "Vulkan":
            text = ("Vulkan в Roblox — бета: известен баг белого экрана "
                    "при сворачивании/разворачивании игры. Если он "
                    "появился — поставь «Авто» или Direct3D 11.")
        elif mode == "Direct3D 12":
            text = ("Direct3D 12 в Roblox — бета: возможны белый/чёрный "
                    "экран и вылеты на некоторых видеокартах. При проблемах "
                    "поставь «Авто» или Direct3D 11.")
        try:
            self.api_hint.configure(text=text)
        except tk.TclError:
            pass

    def _on_quality_change(self, _event=None):
        raw = self.combo_quality.get()
        try:
            value = 0 if raw == "Как в игре" else int(raw)
        except ValueError:
            value = 0
        if value == int(self.cfg.get("quality_level") or 0):
            return
        self.cfg["quality_level"] = value
        self._flags_changed("Качество графики")

    def _flags_changed(self, what):
        save_config(self.cfg)
        self.status_var.set(f"{what}: сохранено")
        if self.cfg["apply_flags_on_launch"]:
            values = fastflag_values(self.cfg)
            written = apply_fastflags(self.cfg, values)
            if values:
                self.flags_status.set(
                    f"Применено: {len(values)} флаг(ов) → {written} файл(ов)")
            else:
                self.flags_status.set(
                    f"Наши флаги сброшены → {written} файл(ов)")

    def apply_flags_now(self):
        values = fastflag_values(self.cfg)
        written = apply_fastflags(self.cfg, values)
        if values:
            self.flags_status.set(f"Применено: {len(values)} → {written} файл(ов)")
            self.log(f"FastFlags применены: {len(values)} → {written} файл(ов)")
        else:
            self.flags_status.set(f"Наши флаги сброшены → {written} файл(ов)")
            self.log(f"FastFlags сброшены, файлов обновлено: {written}")
        self.status_var.set("FastFlags применены")

    # -- страница «Курсоры» -----------------------------------------------------------

    def _build_page_cursors(self):
        c = self.c
        pad = self._scroll_page("cursors")

        self._page_header(
            pad, "◈", "Наборы курсоров",
            "Готовые наборы PNG для Roblox: ShiftLock, обычная стрелка, "
            "стрелка в игре и курсор ввода текста.")

        inner = self._card(
            pad, title="Мои наборы", glyph="▤",
            subtitle="Создай набор, выбери PNG для каждого слота и нажми "
                     "«Применить набор».")

        body = tk.Frame(inner, bg=c["surface"])
        body.pack(fill="both", expand=True, pady=(12, 0))

        left = tk.Frame(body, bg=c["surface"], width=196)
        left.pack(side="left", fill="y", padx=(0, 16))
        left.pack_propagate(False)
        list_holder = tk.Frame(left, bg=c["surface"])
        list_holder.pack(fill="both", expand=True)
        select_bg, select_fg = self._list_selection()
        self.cursor_sets_box = tk.Listbox(
            list_holder, bg=c["list"], fg=c["text"],
            selectbackground=select_bg, selectforeground=select_fg,
            activestyle="none", bd=0, highlightthickness=1,
            highlightbackground=c["border"], highlightcolor=self.accent,
            font=(UI_FONT, 10), exportselection=False, height=10)
        sets_scroll = self._vscroll(
            list_holder, self.cursor_sets_box.yview, c["list"], width=7)
        self.cursor_sets_box.configure(yscrollcommand=sets_scroll.set)
        self.cursor_sets_box.pack(side="left", fill="both", expand=True)
        sets_scroll.pack(side="right", fill="y")
        self.cursor_sets_box.bind("<<ListboxSelect>>",
                                  self._on_cursor_set_select)
        nav = tk.Frame(left, bg=c["surface"])
        nav.pack(fill="x", pady=(8, 0))
        self._button(nav, "＋ Новое", self._cursor_create_set, side="left")
        self._button(nav, "Удалить", self._cursor_delete_set,
                     side="left", padx=(6, 0))

        right = tk.Frame(body, bg=c["surface"])
        right.pack(side="left", fill="both", expand=True)

        rename_row = tk.Frame(right, bg=c["surface"])
        rename_row.pack(fill="x")
        self.cursor_rename_var = tk.StringVar()
        self.cursor_rename_entry = ttk.Entry(
            rename_row, textvariable=self.cursor_rename_var)
        self.cursor_rename_entry.pack(side="left", fill="x", expand=True,
                                      ipady=4)
        self.cursor_rename_entry.bind(
            "<Return>", lambda _e: self._cursor_rename_set())
        self._button(rename_row, "Переименовать", self._cursor_rename_set,
                     side="left", padx=(8, 0))

        actions = tk.Frame(right, bg=c["surface"])
        actions.pack(fill="x", pady=(10, 0))
        self._button(actions, "Применить набор", self.apply_cursors_now,
                     kind="primary", side="left")
        self._button(actions, "Вернуть исходные", self.restore_cursors_now,
                     side="left", padx=(8, 0))
        self._button(actions, "Открыть папку", self.open_cursors_dir,
                     side="left", padx=(8, 0))

        actions2 = tk.Frame(right, bg=c["surface"])
        actions2.pack(fill="x", pady=(8, 0))
        self._button(actions2, "Получить текущий",
                     self._cursor_get_current_set, side="left")
        self._button(actions2, "Импорт ZIP", self._cursor_import_set,
                     side="left", padx=(8, 0))
        self._button(actions2, "Экспорт ZIP", self._cursor_export_set,
                     side="left", padx=(8, 0))
        self._button(actions2, "Обновить", self.refresh_cursors,
                     side="left", padx=(8, 0))

        slots = tk.Frame(right, bg=c["surface"])
        slots.pack(fill="x", pady=(14, 0))
        self.cursor_slot_widgets = {}
        self._cursor_preview_images = {}
        for index, slot in enumerate(CURSOR_SLOTS):
            slots.columnconfigure(index % 2, weight=1, uniform="cursor")
            self._build_cursor_slot(slots, index, slot)

        self.cursor_auto_var = tk.BooleanVar(
            value=bool(self.cfg.get("use_custom_cursors", True)))
        self._checkbox(
            right, "Применять выбранный набор при запуске игры",
            self.cursor_auto_var, command=self._toggle_cursor_auto).pack(
                anchor="w", pady=(8, 0))

        self.cursor_status_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.cursor_status_var,
                 bg=c["surface"], fg=c["text"],
                 font=(UI_FONT, 10, "bold"), anchor="w").pack(
                     fill="x", pady=(10, 2))
        self.cursor_warn_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.cursor_warn_var, bg=c["surface"],
                 fg=self._warn_fg(), font=(UI_FONT, 9), anchor="w",
                 justify="left", wraplength=400).pack(fill="x")

        self._hint(
            right,
            "PNG с прозрачным фоном, лучше 64×64. Клик по превью заменяет "
            "файл слота. Импорт понимает ZIP-наборы Froststrap: "
            "MouseLockedCursor.png и папку Cursors\\KeyboardMouse. "
            "Перед применением закрой Roblox.", wrap=420)
        self._refresh_cursor_panel(initial=True)

    def _build_cursor_slot(self, parent, index, slot):
        c = self.c
        row, col = index // 2, index % 2
        card = tk.Frame(parent, bg=c["surface2"], padx=8, pady=8)
        card.grid(row=row, column=col, sticky="nsew",
                  padx=(0, 5) if col == 0 else (5, 0),
                  pady=(0, 10) if row == 0 else (0, 0))

        head = tk.Frame(card, bg=c["surface2"])
        head.pack(fill="x")
        tk.Label(head, text=slot["label"], bg=c["surface2"], fg=c["text"],
                 font=(UI_FONT, 9, "bold"), anchor="w",
                 justify="left", wraplength=150).pack(
                     side="left", fill="x", expand=True)
        remove = tk.Label(head, text="✕", bg=c["surface2"], fg=c["muted"],
                          font=(UI_FONT, 9, "bold"), cursor="hand2")
        remove.bind("<Button-1>",
                    lambda _e, k=slot["key"]: self._cursor_clear_slot(k))

        preview = tk.Frame(card, bg=c["entry"], height=68, cursor="hand2")
        preview.pack(fill="x", pady=(6, 4))
        preview.pack_propagate(False)
        image_label = tk.Label(preview, bg=c["entry"], text="+",
                               fg=c["muted"], font=(UI_FONT, 16),
                               cursor="hand2")
        image_label.place(relx=0.5, rely=0.5, anchor="center")
        for widget in (preview, image_label):
            widget.bind("<Button-1>",
                        lambda _e, k=slot["key"]: self._cursor_pick(k))

        meta = tk.Label(card, text="не задан", bg=c["surface2"],
                        fg=c["muted"], font=(UI_FONT, 8), anchor="w")
        meta.pack(fill="x")

        view = self._button(
            card, "Выбрать PNG",
            lambda k=slot["key"]: self._cursor_view_or_pick(k),
            kind="slot")
        view.configure(pady=4)
        view.pack_configure(fill="x", pady=(5, 0))

        self.cursor_slot_widgets[slot["key"]] = {
            "preview": preview, "image": image_label,
            "meta": meta, "view": view, "remove": remove,
        }

    def _refresh_cursor_panel(self, initial=False, select=None):
        box = getattr(self, "cursor_sets_box", None)
        if box is None:
            return
        if ensure_cursor_sets_dir() is None:
            self.cursor_status_var.set("Папка наборов недоступна")
            return
        names = list_cursor_sets()
        if initial and not names:
            created = create_cursor_set()
            if created["ok"]:
                names = list_cursor_sets()
        current = select or getattr(self, "_cursor_active_set", None)
        saved = self.cfg.get("cursor_set")
        if current not in names:
            current = saved if saved in names else (names[0] if names else None)

        self._cursor_refreshing = True
        box.delete(0, "end")
        for name in names:
            box.insert("end", name)
        if current is not None:
            index = names.index(current)
            box.selection_set(index)
            box.see(index)
        self._cursor_refreshing = False
        self._cursor_active_set = current
        self.cursor_rename_var.set(current or "")
        self.cursor_rename_entry.configure(
            state="normal" if current else "disabled")
        self._refresh_cursor_slots(current)
        self._refresh_cursor_status()

    def _refresh_cursor_slots(self, name):
        files = cursor_set_files(name) if name else {}
        for slot in CURSOR_SLOTS:
            widgets = self.cursor_slot_widgets.get(slot["key"])
            if widgets is None:
                continue
            path = files.get(slot["key"])
            if path is None:
                self._cursor_preview_images.pop(slot["key"], None)
                widgets["image"].configure(image="", text="+",
                                           font=(UI_FONT, 16))
                widgets["meta"].configure(text="не задан")
                widgets["view"].configure(text="Выбрать PNG")
                widgets["remove"].pack_forget()
                continue
            try:
                width, height, _alpha = png_info(path)
                meta = f"{width}×{height} px"
            except ValueError:
                meta = "файл повреждён"
            image = self._cursor_preview_image(path, 120, 76)
            if image is not None:
                self._cursor_preview_images[slot["key"]] = image
                widgets["image"].configure(image=image, text="")
            else:
                self._cursor_preview_images.pop(slot["key"], None)
                widgets["image"].configure(image="", text="PNG",
                                           font=(UI_FONT, 10, "bold"))
            widgets["meta"].configure(text=meta)
            widgets["view"].configure(text="Просмотр")
            widgets["remove"].pack(side="right")

    def _refresh_cursor_status(self):
        name = getattr(self, "_cursor_active_set", None)
        files = cursor_set_files(name) if name else {}
        roots = len(cursor_install_roots())
        auto = "вкл" if self.cfg.get("use_custom_cursors") else "выкл"
        applied = cursor_state_info()
        parts = []
        if name:
            parts.append(f"Набор «{name}»: {len(files)}/4 PNG")
        else:
            parts.append("Нет наборов")
        parts.append(f"папок для записи: {roots}")
        parts.append(f"автоприменение: {auto}")
        if applied["files"]:
            note = f"применено файлов: {applied['files']}"
            if applied["set"]:
                note += f" («{applied['set']}»)"
            parts.append(note)
        self.cursor_status_var.set("  ·  ".join(parts))

        warnings = cursor_set_warnings(name) if name else []
        if name and not files:
            warnings.insert(0, "Добавь PNG хотя бы в один слот.")
        if len(warnings) > 3:
            warnings = warnings[:3] + [f"…и ещё {len(warnings) - 3}"]
        self.cursor_warn_var.set("\n".join(warnings))

    def _cursor_preview_image(self, path, box_w, box_h):
        try:
            image = tk.PhotoImage(file=str(path))
        except tk.TclError:
            return None
        width = max(1, image.width())
        height = max(1, image.height())
        scale = min(box_w / width, box_h / height)
        if scale >= 2:
            zoom = min(6, int(scale))
            if zoom > 1:
                image = image.zoom(zoom)
        elif scale < 1:
            factor = max(1, -(-max(width, height) // max(box_w, box_h)))
            image = image.subsample(factor)
        return image

    def _on_cursor_set_select(self, _event=None):
        if getattr(self, "_cursor_refreshing", False):
            return
        selection = self.cursor_sets_box.curselection()
        if not selection:
            return
        name = self.cursor_sets_box.get(selection[0])
        if name == getattr(self, "_cursor_active_set", None):
            return
        self._cursor_active_set = name
        self.cursor_rename_var.set(name)
        self._refresh_cursor_slots(name)
        self._refresh_cursor_status()
        self.cfg["cursor_set"] = name
        save_config(self.cfg)

    def _cursor_create_set(self):
        result = create_cursor_set()
        if not result["ok"]:
            messagebox.showerror(APP_NAME, result["error"], parent=self.root)
            return
        self.cfg["cursor_set"] = result["name"]
        save_config(self.cfg)
        self._refresh_cursor_panel(select=result["name"])
        self.status_var.set(f"Создан набор «{result['name']}»")
        self.log(f"Создан набор курсоров «{result['name']}»")

    def _cursor_delete_set(self):
        name = getattr(self, "_cursor_active_set", None)
        if not name:
            return
        applied = cursor_state_info()
        note = ""
        if applied["files"] and applied["set"] == name:
            note = "\nНабор сейчас применён — лаунчер вернёт исходные курсоры."
        if not messagebox.askyesno(
                APP_NAME,
                f"Удалить набор «{name}» вместе с PNG?{note}",
                parent=self.root):
            return

        if applied["files"] and applied["set"] == name:
            if player_pids():
                messagebox.showwarning(
                    APP_NAME,
                    "Закрой Roblox, затем удали набор: сначала нужно "
                    "вернуть исходные курсоры.",
                    parent=self.root)
                return
            restored = restore_custom_cursors()
            if not restored["ok"]:
                messagebox.showerror(
                    APP_NAME,
                    "Не удалось вернуть исходные курсоры:\n"
                    + "\n".join(restored["errors"][:4]),
                    parent=self.root)
                return
            self.cfg["use_custom_cursors"] = False
            if getattr(self, "cursor_auto_var", None) is not None:
                self.cursor_auto_var.set(False)

        result = delete_cursor_set(name)
        if not result["ok"]:
            messagebox.showerror(APP_NAME, result["error"], parent=self.root)
            return
        remaining = list_cursor_sets()
        next_name = remaining[0] if remaining else ""
        self.cfg["cursor_set"] = next_name
        save_config(self.cfg)
        self._refresh_cursor_panel(select=next_name or None)
        self.status_var.set(f"Набор «{name}» удалён")
        self.log(f"Набор курсоров «{name}» удалён")

    def _cursor_rename_set(self):
        old = getattr(self, "_cursor_active_set", None)
        if not old:
            return
        new = self.cursor_rename_var.get().strip()
        if not valid_cursor_set_name(new):
            messagebox.showerror(
                APP_NAME,
                "Имя набора не должно содержать <>:\"/\\|?* и не может "
                "быть пустым.",
                parent=self.root)
            return
        result = rename_cursor_set(old, new)
        if not result["ok"]:
            messagebox.showerror(APP_NAME, result["error"], parent=self.root)
            return
        if self.cfg.get("cursor_set") == old:
            self.cfg["cursor_set"] = result["name"]
            save_config(self.cfg)
        self._refresh_cursor_panel(select=result["name"])
        self.status_var.set(f"Набор переименован в «{result['name']}»")
        self.log(f"Набор курсоров «{old}» → «{result['name']}»")

    def _cursor_pick(self, key):
        name = getattr(self, "_cursor_active_set", None)
        if not name:
            messagebox.showinfo(
                APP_NAME,
                "Сначала создай набор кнопкой «＋ Новое».",
                parent=self.root)
            return
        slot = next((s for s in CURSOR_SLOTS if s["key"] == key), None)
        if slot is None:
            return
        path = filedialog.askopenfilename(
            parent=self.root, title=f"Курсор: {slot['label']}",
            filetypes=[("PNG-изображения", "*.png")])
        if not path:
            return
        result = set_cursor_slot(name, key, path)
        if not result["ok"]:
            messagebox.showerror(
                APP_NAME,
                f"Не удалось добавить PNG:\n{result['error']}",
                parent=self.root)
            return
        self._refresh_cursor_slots(name)
        self._refresh_cursor_status()
        note = ""
        if result["width"] > 64 or result["height"] > 64:
            note = f" ({result['width']}×{result['height']}, лучше 64×64)"
        self.status_var.set(f"{slot['label']}: PNG добавлен{note}")
        self.log(f"Курсор {slot['file']} добавлен в набор «{name}»")

    def _cursor_clear_slot(self, key):
        name = getattr(self, "_cursor_active_set", None)
        slot = next((s for s in CURSOR_SLOTS if s["key"] == key), None)
        if not name or slot is None:
            return
        if not messagebox.askyesno(
                APP_NAME,
                f"Удалить {slot['label']} из набора «{name}»?",
                parent=self.root):
            return
        result = remove_cursor_slot(name, key)
        if not result["ok"]:
            messagebox.showerror(APP_NAME, result["error"], parent=self.root)
            return
        self._refresh_cursor_slots(name)
        self._refresh_cursor_status()
        self.log(f"Курсор {slot['file']} удалён из набора «{name}»")

    def _cursor_view_or_pick(self, key):
        name = getattr(self, "_cursor_active_set", None)
        path = cursor_slot_path(name, key) if name else None
        if path is not None and path.is_file():
            self._cursor_view_slot(key)
        else:
            self._cursor_pick(key)

    def _cursor_view_slot(self, key):
        name = getattr(self, "_cursor_active_set", None)
        path = cursor_slot_path(name, key) if name else None
        if path is None or not path.is_file():
            messagebox.showinfo(APP_NAME, "В этом слоте нет файла.",
                                parent=self.root)
            return
        try:
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Не удалось открыть файл:\n{exc}",
                                 parent=self.root)

    def _cursor_get_current_set(self):
        name = getattr(self, "_cursor_active_set", None)
        if not name:
            messagebox.showinfo(
                APP_NAME, "Сначала создай и выбери набор.", parent=self.root)
            return
        if not messagebox.askyesno(
                APP_NAME,
                f"Заменить содержимое набора «{name}» текущими курсорами "
                "из установленного Roblox?",
                parent=self.root):
            return
        result = copy_current_cursor_set(name)
        self._refresh_cursor_slots(name)
        self._refresh_cursor_status()
        if not result["ok"]:
            messagebox.showerror(
                APP_NAME,
                "Не удалось получить текущий набор:\n"
                + "; ".join(result["errors"][:4]),
                parent=self.root)
            return
        message = f"Скопировано PNG: {result['copied']}"
        if result["missing"]:
            message += "\nНе найдены: " + ", ".join(result["missing"])
        self.status_var.set(f"Текущие курсоры скопированы: {result['copied']}")
        self.log(f"Текущие курсоры Roblox → набор «{name}»: "
                 f"{result['copied']} PNG")
        messagebox.showinfo(APP_NAME, message, parent=self.root)

    def _cursor_import_set(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Импорт набора курсоров",
            filetypes=[("ZIP-архив", "*.zip")])
        if not path:
            return
        result = import_cursor_set(path)
        if not result["ok"]:
            messagebox.showerror(
                APP_NAME,
                f"Не удалось импортировать набор:\n{result['error']}",
                parent=self.root)
            return
        self.cfg["cursor_set"] = result["name"]
        save_config(self.cfg)
        self._refresh_cursor_panel(select=result["name"])
        message = (f"Импортирован набор «{result['name']}»\n"
                   f"PNG: {result['files']}")
        if result["skipped"]:
            message += f"\nПропущено файлов: {result['skipped']}"
        self.status_var.set(f"Импортирован набор «{result['name']}»")
        self.log(f"Импорт набора курсоров «{result['name']}»: "
                 f"{result['files']} PNG")
        messagebox.showinfo(APP_NAME, message, parent=self.root)

    def _cursor_export_set(self):
        name = getattr(self, "_cursor_active_set", None)
        if not name:
            messagebox.showinfo(
                APP_NAME, "Сначала выбери набор.", parent=self.root)
            return
        if not cursor_set_files(name):
            messagebox.showinfo(
                APP_NAME, "В наборе нет PNG для экспорта.", parent=self.root)
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Экспорт набора курсоров",
            defaultextension=".zip", initialfile=f"{name}.zip",
            filetypes=[("ZIP-архив", "*.zip")])
        if not path:
            return
        result = export_cursor_set(name, path)
        if not result["ok"]:
            messagebox.showerror(
                APP_NAME, f"Не удалось экспортировать набор:\n"
                         f"{result['error']}", parent=self.root)
            return
        self.status_var.set(f"Набор «{name}» экспортирован")
        self.log(f"Экспорт набора «{name}»: {result['files']} PNG → {path}")
        messagebox.showinfo(
            APP_NAME,
            f"Набор сохранён:\n{result['path']}",
            parent=self.root)

    def open_cursors_dir(self):
        folder = ensure_cursor_sets_dir()
        if folder is None:
            messagebox.showerror(APP_NAME,
                                 "Не удалось создать папку наборов.",
                                 parent=self.root)
            return
        try:
            os.startfile(str(folder))
        except OSError as exc:
            messagebox.showerror(
                APP_NAME, f"Не удалось открыть папку:\n{exc}",
                parent=self.root)

    def refresh_cursors(self):
        self._refresh_cursor_panel()
        self.status_var.set("Список наборов обновлён")
        self.log("Список наборов курсоров обновлён")

    def _apply_cursors(self, show_errors=True):
        name = getattr(self, "_cursor_active_set", None)
        if not name:
            self.status_var.set("Набор не выбран — курсоры не применены")
            if show_errors and self.interactive:
                messagebox.showinfo(
                    APP_NAME,
                    "Создай и выбери набор курсоров.",
                    parent=self.root)
            self._refresh_cursor_panel()
            return False
        if player_pids():
            text = ("Закрой Roblox, затем примени набор ещё раз. "
                    "Пока клиент запущен, файлы курсоров могут быть заняты.")
            self.status_var.set("Курсоры не применены — Roblox запущен")
            self.log("Курсоры не применены: Roblox запущен")
            if show_errors and self.interactive:
                messagebox.showwarning(APP_NAME, text, parent=self.root)
            self._refresh_cursor_panel()
            return False

        result = apply_cursor_set(name)
        self.cfg["cursor_set"] = name
        save_config(self.cfg)
        self._refresh_cursor_panel(select=name)
        if result["ok"]:
            message = (f"Набор «{name}»: {result['files']} PNG → "
                       f"{result['installs']} пап(ок)")
            self.status_var.set(message)
            self.log(message)
            return True

        error = result.get("error") or "неизвестная ошибка"
        self.status_var.set("Ошибка применения набора курсоров")
        self.log(f"Ошибка применения набора «{name}»: {error}")
        if show_errors and self.interactive:
            messagebox.showerror(
                APP_NAME,
                "Не удалось применить набор.\n\n"
                f"{error}\n\n"
                "Закрой Roblox и попробуй снова.",
                parent=self.root)
        return False

    def apply_cursors_now(self):
        self._apply_cursors()

    def _toggle_cursor_auto(self):
        enabled = bool(self.cursor_auto_var.get())
        self.cfg["use_custom_cursors"] = enabled
        save_config(self.cfg)
        if enabled:
            self.log("Автоприменение курсоров включено")
            self._apply_cursors()
        else:
            self.log("Автоприменение курсоров выключено")
            self._restore_cursors(confirm=False)

    def _restore_cursors(self, confirm=True):
        if confirm and self.interactive and not messagebox.askyesno(
                APP_NAME,
                "Вернуть файлы курсоров, которые были до замены?\n"
                "PNG в наборах останутся на месте.",
                parent=self.root):
            return False
        if player_pids():
            self.status_var.set("Roblox запущен — курсоры не восстановлены")
            self.log("Восстановление курсоров отменено: Roblox запущен")
            if self.interactive:
                messagebox.showwarning(
                    APP_NAME,
                    "Закрой Roblox, затем верни исходные курсоры.",
                    parent=self.root)
            self._refresh_cursor_panel()
            return False

        result = restore_custom_cursors()
        if result["ok"]:
            self.cfg["use_custom_cursors"] = False
            if getattr(self, "cursor_auto_var", None) is not None:
                self.cursor_auto_var.set(False)
            save_config(self.cfg)
            self.status_var.set("Исходные курсоры восстановлены")
            self.log(f"Исходные курсоры восстановлены: "
                     f"{result['restored']} файл(ов)")
        else:
            self.status_var.set("Не удалось вернуть исходные курсоры")
            self.log("Ошибка восстановления курсоров: "
                     + "; ".join(result["errors"]))
            if self.interactive:
                messagebox.showerror(
                    APP_NAME,
                    "Не удалось восстановить часть файлов:\n"
                    + "\n".join(result["errors"][:4]),
                    parent=self.root)
        self._refresh_cursor_panel()
        return result["ok"]

    def restore_cursors_now(self):
        self._restore_cursors(confirm=True)

    def _apply_cursors_for_launch(self):
        """Применение с UI-потока перед запуском игры."""
        if not self.cfg.get("use_custom_cursors") or player_pids():
            return
        name = (self.cfg.get("cursor_set")
                or getattr(self, "_cursor_active_set", None))
        if not name:
            return
        result = apply_cursor_set(name)
        if result["ok"] and result["files"]:
            self.log(f"Набор курсоров «{name}»: {result['files']} PNG → "
                     f"{result['installs']} пап(ок)")
        elif not result["ok"]:
            self.log(f"Курсоры не применены: {result.get('error')}")
        self._refresh_cursor_panel(select=name)

    def _cursor_launch_done(self, result):
        if self._closing:
            return
        if result["ok"] and result["files"]:
            self.log(f"Набор курсоров «{result.get('set', '')}»: "
                     f"{result['files']} PNG → {result['installs']} пап(ок)")
        elif not result["ok"]:
            self.log(f"Курсоры не применены: {result.get('error')}")
        self._refresh_cursor_panel()

    # -- страница «Темы» --------------------------------------------------------------

    def _build_page_themes(self):
        c = self.c
        pad = self._scroll_page("themes")

        self._page_header(
            pad, "◐", "Свои темы",
            "Собери оформление из базовых цветов, оттенка и своей картинки. "
            "Картинка становится фоном всего окна, а режим «только картинка» "
            "убирает цвета темы и оставляет фото.")

        self.roblox_theme_var = tk.StringVar(value="Проверяю…")
        rob = self._card(
            pad, title="Тема Roblox", glyph="◑",
            subtitle="Оформление самого клиента Roblox: главная страница, "
                     "меню и настройки. Меняется официальный параметр "
                     "appStorage.json — файлы игры остаются нетронутыми.")
        row = tk.Frame(rob, bg=c["surface"])
        row.pack(fill="x", pady=(10, 0))
        self.btn_roblox_dark = self._button(
            row, "Тёмная", lambda: self._set_roblox_theme("dark"),
            side="left")
        self.btn_roblox_light = self._button(
            row, "Светлая", lambda: self._set_roblox_theme("light"),
            side="left", padx=(8, 0))
        tk.Label(row, textvariable=self.roblox_theme_var, bg=c["surface"],
                 fg=c["muted"], font=(UI_FONT, 9), anchor="w").pack(
                     side="left", padx=(14, 0))
        self._hint(
            rob,
            "Тему Roblox читает при запуске, поэтому клиент должен быть "
            "закрыт — работающий файл лаунчер не трогает. Рядом остаётся "
            "копия appStorage.json.maklyxa.bak, чтобы можно было вернуть "
            "исходное оформление.", wrap=560)
        self._refresh_roblox_theme()

        inner = self._card(
            pad, title="Мои темы", glyph="◐",
            subtitle="Выбери тему слева — справа можно переименовать её, "
                     "сменить цвета, поставить картинку и применить.")

        body = tk.Frame(inner, bg=c["surface"])
        body.pack(fill="both", expand=True, pady=(12, 0))

        left = tk.Frame(body, bg=c["surface"], width=200)
        left.pack(side="left", fill="y", padx=(0, 16))
        left.pack_propagate(False)
        list_holder = tk.Frame(left, bg=c["surface"])
        list_holder.pack(fill="both", expand=True)
        select_bg, select_fg = self._list_selection()
        self.theme_sets_box = tk.Listbox(
            list_holder, bg=c["list"], fg=c["text"],
            selectbackground=select_bg, selectforeground=select_fg,
            activestyle="none", bd=0, highlightthickness=1,
            highlightbackground=c["border"], highlightcolor=self.accent,
            font=(UI_FONT, 10), exportselection=False, height=10)
        scroll = self._vscroll(list_holder, self.theme_sets_box.yview,
                               c["list"], width=7)
        self.theme_sets_box.configure(yscrollcommand=scroll.set)
        self.theme_sets_box.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.theme_sets_box.bind("<<ListboxSelect>>",
                                 self._on_theme_set_select)

        nav = tk.Frame(left, bg=c["surface"])
        nav.pack(fill="x", pady=(8, 0))
        self._button(nav, "＋ Создать", self._theme_create_set, side="left")
        self._button(nav, "Удалить", self._theme_delete_set, side="left",
                     padx=(6, 0))
        nav2 = tk.Frame(left, bg=c["surface"])
        nav2.pack(fill="x", pady=(6, 0))
        self._button(nav2, "Импорт ZIP", self._theme_import_set,
                     side="left")
        self._button(nav2, "Экспорт ZIP", self._theme_export_set,
                     side="left", padx=(6, 0))
        self._hint(left, "Новая тема создаётся с текущими цветами. "
                         "Имя можно поменять справа.", wrap=200)

        right = tk.Frame(body, bg=c["surface"])
        right.pack(side="left", fill="both", expand=True)

        rename_row = tk.Frame(right, bg=c["surface"])
        rename_row.pack(fill="x")
        self.theme_rename_var = tk.StringVar()
        self.theme_rename_entry = ttk.Entry(
            rename_row, textvariable=self.theme_rename_var)
        self.theme_rename_entry.pack(side="left", fill="x", expand=True,
                                     ipady=4)
        self.theme_rename_entry.bind(
            "<Return>", lambda _e: self._theme_rename_set())
        self._button(rename_row, "Переименовать", self._theme_rename_set,
                     side="left", padx=(8, 0))

        self.theme_preview_canvas = tk.Canvas(
            right, height=THEME_PREVIEW_H, bg=c["bg"],
            highlightthickness=1, highlightbackground=c["border"], bd=0)
        self.theme_preview_canvas.pack(fill="x", pady=(10, 0))
        self.theme_preview_canvas.bind(
            "<Configure>", lambda _e: self._render_theme_preview())
        self.theme_preview_canvas.bind(
            "<Double-Button-1>", lambda _e: self._theme_apply())

        self.theme_info_var = tk.StringVar(value="")
        self.theme_image_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.theme_info_var, bg=c["surface"],
                 fg=c["muted"], font=(UI_FONT, 9), anchor="w",
                 justify="left", wraplength=460).pack(fill="x", pady=(7, 0))

        opts = tk.Frame(right, bg=c["surface"])
        opts.pack(fill="x", pady=(10, 0))
        opts.grid_columnconfigure(1, weight=1)

        def opt_row(row, label):
            tk.Label(opts, text=label, bg=c["surface"], fg=c["muted"],
                     font=(UI_FONT, 9), anchor="w").grid(
                         row=row, column=0, sticky="w", pady=4,
                         padx=(0, 12))
            holder = tk.Frame(opts, bg=c["surface"])
            holder.grid(row=row, column=1, sticky="w", pady=4)
            return holder

        self.theme_base_row = opt_row(0, "База")
        self.theme_tint_row = opt_row(1, "Оттенок")
        self.theme_accent_row = opt_row(2, "Акцент")
        tk.Label(opts, text="Картинка", bg=c["surface"], fg=c["muted"],
                 font=(UI_FONT, 9), anchor="w").grid(
                     row=3, column=0, sticky="w", pady=4, padx=(0, 12))
        image_row = tk.Frame(opts, bg=c["surface"])
        image_row.grid(row=3, column=1, sticky="w", pady=4)
        self.theme_pick_btn = self._button(
            image_row, "Выбрать", self._theme_pick_image, side="left")
        self.theme_image_remove_btn = self._button(
            image_row, "Убрать", self._theme_remove_image, side="left",
            padx=(6, 0))
        tk.Label(opts, textvariable=self.theme_image_var, bg=c["surface"],
                 fg=c["muted"], font=(UI_FONT, 8), anchor="w",
                 justify="left", wraplength=320).grid(
                     row=4, column=1, sticky="w", pady=(2, 0))

        self.theme_image_only_var = tk.BooleanVar(value=False)
        self.theme_image_only_check = self._checkbox(
            opts, "Оставить только картинку — без цветов темы",
            self.theme_image_only_var,
            command=self._theme_toggle_image_only)
        self.theme_image_only_check.grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))

        actions = tk.Frame(right, bg=c["surface"])
        actions.pack(fill="x", pady=(12, 0))
        self.theme_apply_btn = self._button(
            actions, "Применить тему", self._theme_apply, kind="primary",
            side="left")
        self._button(actions, "Обновить", self._refresh_theme_panel,
                     side="left", padx=8)

        self.theme_status_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.theme_status_var, bg=c["surface"],
                 fg=c["text"], font=(UI_FONT, 9), anchor="w",
                 justify="left", wraplength=460).pack(fill="x", pady=(10, 0))
        self._hint(
            right,
            "Картинка становится фоном рабочей области и кадрируется по "
            "размеру окна. Режим «только картинка» отключает цвета темы — "
            "интерфейс становится нейтральным, а фото остаётся. Импорт и "
            "экспорт — ZIP, как у курсоров.",
            wrap=470)
        self._refresh_theme_panel()
        self.root.after_idle(self._render_theme_preview)

    def _refresh_roblox_theme(self):
        var = getattr(self, "roblox_theme_var", None)
        if var is None:
            return
        state = roblox_theme_state()
        theme = state.get("theme") or ""
        if not state.get("ok"):
            var.set("недоступно: " + str(state.get("error") or ""))
        elif theme:
            var.set("Сейчас: " + ROBLOX_APP_THEMES[theme].lower())
        else:
            var.set("Тема ещё не задана")
        for key, btn in (("dark", getattr(self, "btn_roblox_dark", None)),
                         ("light", getattr(self, "btn_roblox_light", None))):
            if btn is None:
                continue
            label = "Тёмная" if key == "dark" else "Светлая"
            try:
                btn.configure(text=("● " + label) if key == theme else label)
            except tk.TclError:
                pass

    def _set_roblox_theme(self, theme):
        result = set_roblox_theme(theme)
        if not result.get("ok"):
            self.status_var.set("Тема Roblox не изменена")
            self.log("Тема Roblox: " + str(result.get("error") or ""))
            if self.interactive:
                messagebox.showwarning(
                    APP_NAME,
                    "Не удалось сменить тему Roblox:\n"
                    + str(result.get("error") or ""), parent=self.root)
            self._refresh_roblox_theme()
            return False
        label = ROBLOX_APP_THEMES[theme].lower()
        if result.get("changed"):
            self.status_var.set(f"Тема Roblox: {label}")
            self.log(f"Тема Roblox переключена на «{label}» — применится "
                     "при следующем запуске клиента")
        else:
            self.status_var.set(f"Тема Roblox уже {label}")
        self._refresh_roblox_theme()
        return True

    def _refresh_theme_panel(self, select=None):
        box = getattr(self, "theme_sets_box", None)
        if box is None:
            return
        if ensure_theme_sets_dir() is None:
            self.theme_status_var.set("Папка тем недоступна")
            return
        names = list_theme_sets()
        current = select or getattr(self, "_theme_active_set", None)
        saved = str(self.cfg.get("custom_theme") or "")
        if current not in names:
            current = saved if saved in names else (names[0] if names else None)
        self._theme_refreshing = True
        try:
            box.delete(0, "end")
            for name in names:
                box.insert("end", name)
            if current is not None:
                index = names.index(current)
                box.selection_set(index)
                box.see(index)
        except tk.TclError:
            pass
        self._theme_refreshing = False
        self._theme_active_set = current
        self.theme_rename_var.set(current or "")
        try:
            self.theme_rename_entry.configure(
                state="normal" if current else "disabled")
        except tk.TclError:
            pass
        self._load_theme_editor(current)

    def _on_theme_set_select(self, _event=None):
        if getattr(self, "_theme_refreshing", False):
            return
        selection = self.theme_sets_box.curselection()
        if not selection:
            return
        name = self.theme_sets_box.get(selection[0])
        if name == getattr(self, "_theme_active_set", None):
            return
        self._theme_active_set = name
        self.theme_rename_var.set(name)
        try:
            self.theme_rename_entry.configure(state="normal")
        except tk.TclError:
            pass
        self._load_theme_editor(name)

    @staticmethod
    def _clear_frame(frame):
        for child in frame.winfo_children():
            child.destroy()

    def _load_theme_editor(self, name):
        info = theme_set_info(name) if name else {"ok": False}
        palette = load_theme_palette(name) if name else None
        if not info.get("ok") or palette is None:
            self._theme_image_path = None
            self._theme_image_only = False
            if getattr(self, "theme_image_only_var", None) is not None:
                self.theme_image_only_var.set(False)
            self.theme_info_var.set("")
            self.theme_image_var.set("")
            self.theme_status_var.set(
                "Тем пока нет — создай новую кнопкой «＋ Создать»."
                if not list_theme_sets() else "Выбери тему слева.")
            for holder in (getattr(self, "theme_base_row", None),
                           getattr(self, "theme_tint_row", None),
                           getattr(self, "theme_accent_row", None)):
                if holder is not None:
                    self._clear_frame(holder)
            for attr in ("theme_pick_btn", "theme_image_remove_btn",
                         "theme_apply_btn"):
                widget = getattr(self, attr, None)
                if widget is not None:
                    try:
                        widget.configure(state="disabled")
                    except tk.TclError:
                        pass
            self._render_theme_preview()
            return

        self._theme_base = info.get("base") or "dark"
        self._theme_tint = info.get("tint") or "#00c48c"
        self._theme_accent = info.get("accent") or "#00c48c"
        self._theme_image_path = info.get("image")
        self._theme_image_only = bool(info.get("image_only"))
        if getattr(self, "theme_image_only_var", None) is not None:
            self.theme_image_only_var.set(self._theme_image_only)
        if self._theme_image_only:
            self.theme_info_var.set(
                "Режим «только картинка»: цвета темы отключены, интерфейс "
                "нейтральный. Сохранено для возврата — "
                f"{'светлая' if self._theme_base == 'light' else 'тёмная'} "
                f"база, оттенок {self._theme_tint}, "
                f"акцент {self._theme_accent}.")
        else:
            self.theme_info_var.set(
                f"База: "
                f"{'светлая' if self._theme_base == 'light' else 'тёмная'}"
                f"  ·  оттенок {self._theme_tint}"
                f"  ·  акцент {self._theme_accent}")
        self._build_theme_options()
        if self._theme_image_path:
            width = info.get("width") or 0
            height = info.get("height") or 0
            self.theme_image_var.set(
                f"{Path(self._theme_image_path).name}  ·  {width}×{height}")
            remove_state = "normal"
        else:
            self.theme_image_var.set(
                "не выбрана — PNG, JPG, WEBP, BMP или GIF до 20 МБ")
            remove_state = "disabled"
        for attr in ("theme_pick_btn", "theme_apply_btn"):
            widget = getattr(self, attr, None)
            if widget is not None:
                try:
                    widget.configure(state="normal")
                except tk.TclError:
                    pass
        if getattr(self, "theme_image_remove_btn", None) is not None:
            try:
                self.theme_image_remove_btn.configure(state=remove_state)
            except tk.TclError:
                pass
        self._render_theme_preview()

    def _build_theme_options(self):
        for holder in (self.theme_base_row, self.theme_tint_row,
                       self.theme_accent_row):
            self._clear_frame(holder)
        frozen = bool(getattr(self, "_theme_image_only", False))
        for value, label in (("dark", "Тёмная"), ("light", "Светлая")):
            active = self._theme_base == value
            base, fg, hot = self._btn_colors(
                "primary" if active else "ghost")
            btn = tk.Button(
                self.theme_base_row, text=label, relief="flat", bd=0,
                font=(UI_FONT, 9, "bold" if active else "normal"),
                padx=12, pady=4, cursor="hand2", bg=base, fg=fg,
                activebackground=hot, activeforeground=fg,
                highlightthickness=0, takefocus=False,
                state="disabled" if frozen else "normal",
                command=lambda v=value: self._theme_set_colors(base=v))
            btn.pack(side="left", padx=(0, 6))
        tints = list(THEME_TINTS)
        if not any(color.lower() == str(self._theme_tint).lower()
                   for color, _label in tints):
            tints.insert(0, (str(self._theme_tint), "Свой"))
        for color, _label in tints:
            self._theme_swatch(
                self.theme_tint_row, color,
                color.lower() == str(self._theme_tint).lower(),
                lambda c=color: self._theme_set_colors(tint=c),
                disabled=frozen)
        accents = list(ACCENT_PRESETS)
        if not any(color.lower() == str(self._theme_accent).lower()
                   for color, _label in accents):
            accents.insert(0, (str(self._theme_accent), "Свой"))
        for color, _label in accents:
            self._theme_swatch(
                self.theme_accent_row, color,
                color.lower() == str(self._theme_accent).lower(),
                lambda c=color: self._theme_set_colors(accent=c),
                disabled=frozen)

    def _theme_swatch(self, parent, color, selected, command,
                      disabled=False):
        border = self.accent if selected else self.c["border"]
        holder = tk.Frame(parent, bg=border)
        holder.pack(side="left", padx=(0, 6), pady=1)
        btn = tk.Button(
            holder, bg=color, activebackground=shade(color, 1.15),
            width=2, height=1, bd=0, relief="flat", cursor="hand2",
            highlightthickness=0, takefocus=False, command=command,
            state="disabled" if disabled else "normal")
        btn.pack(padx=2, pady=2)

    def _theme_set_colors(self, base=None, tint=None, accent=None):
        name = getattr(self, "_theme_active_set", None)
        if not name:
            return
        if getattr(self, "_theme_image_only", False):
            self.theme_status_var.set(
                "Сначала выключи «Только картинку», чтобы менять цвета.")
            return
        if base is not None:
            self._theme_base = base
        if tint is not None:
            self._theme_tint = tint
        if accent is not None:
            self._theme_accent = accent
        result = update_theme_set(name, base=self._theme_base,
                                  tint=self._theme_tint,
                                  accent=self._theme_accent)
        if not result["ok"]:
            self.theme_status_var.set(result["error"]
                                     or "Не удалось сохранить тему.")
            return
        applied = (self.theme_name == "custom"
                   and self.custom_theme_name == name)
        self._load_theme_editor(name)
        if applied:
            self.theme_status_var.set(
                "Сохранено — нажми «Применить тему», чтобы увидеть "
                "изменения в лаунчере.")
        else:
            self.theme_status_var.set("Изменения сохранены.")

    def _theme_toggle_image_only(self):
        name = getattr(self, "_theme_active_set", None)
        if not name:
            return
        wanted = bool(self.theme_image_only_var.get())
        if wanted == getattr(self, "_theme_image_only", False):
            return
        result = update_theme_set(name, image_only=wanted)
        if not result["ok"]:
            self.theme_image_only_var.set(not wanted)
            self.theme_status_var.set(result["error"]
                                     or "Не удалось сохранить тему.")
            return
        applied = (self.theme_name == "custom"
                   and self.custom_theme_name == name)
        self._load_theme_editor(name)
        if not self._theme_image_path:
            self.theme_status_var.set(
                "Режим включён, но картинки нет — интерфейс будет "
                "нейтральным. Добавь картинку.")
        elif applied:
            self.theme_status_var.set(
                "Сохранено — нажми «Применить тему», чтобы увидеть "
                "изменения в лаунчере.")
        else:
            self.theme_status_var.set("Изменения сохранены.")

    def _theme_pick_image(self):
        name = getattr(self, "_theme_active_set", None)
        if not name:
            return
        path = filedialog.askopenfilename(
            parent=self.root, title="Картинка для темы",
            filetypes=[("Картинки", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),
                       ("Все файлы", "*.*")])
        if not path:
            return
        result = set_theme_image(name, path)
        if not result["ok"]:
            messagebox.showerror(APP_NAME, result["error"],
                                 parent=self.root)
            return
        self._theme_preview_cache.clear()
        self._load_theme_editor(name)
        self.theme_status_var.set(
            "Картинка добавлена — примени тему, чтобы увидеть фон.")
        self.status_var.set("Картинка темы обновлена")
        self.log(f"Тема «{name}»: картинка {Path(path).name}")

    def _theme_remove_image(self):
        name = getattr(self, "_theme_active_set", None)
        if not name or not self._theme_image_path:
            return
        if not messagebox.askyesno(APP_NAME,
                                   "Убрать картинку из темы?",
                                   parent=self.root):
            return
        result = remove_theme_image(name)
        if not result["ok"]:
            messagebox.showerror(APP_NAME, result["error"],
                                 parent=self.root)
            return
        self._theme_preview_cache.clear()
        self._load_theme_editor(name)
        self.theme_status_var.set(
            "Картинка убрана — примени тему, чтобы обновить окно.")
        self.status_var.set("Картинка темы убрана")
        self.log(f"Тема «{name}»: картинка убрана")

    def _theme_apply(self):
        name = getattr(self, "_theme_active_set", None)
        if not name:
            messagebox.showinfo(
                APP_NAME, "Выбери тему или создай новую.", parent=self.root)
            return
        self.set_theme("custom", custom_name=name)

    def _theme_create_set(self):
        base = ("light" if (self.c.get("stored_base") or self.c.get("base"))
                == "light" else "dark")
        tint = str(self.c.get("stored_tint") or self.c.get("tint")
                   or self.cfg.get("accent") or "#00c48c")
        result = create_theme_set(base=base, tint=tint, accent=tint)
        if not result["ok"]:
            messagebox.showerror(APP_NAME, result["error"],
                                 parent=self.root)
            return
        self._theme_preview_cache.clear()
        self._refresh_theme_panel(select=result["name"])
        self.status_var.set(f"Создана тема «{result['name']}»")
        self.log(f"Создана своя тема «{result['name']}»")

    def _theme_delete_set(self):
        name = getattr(self, "_theme_active_set", None)
        if not name:
            return
        if not messagebox.askyesno(
                APP_NAME,
                f"Удалить тему «{name}» вместе с картинкой?",
                parent=self.root):
            return
        applied = (self.theme_name == "custom"
                   and self.custom_theme_name == name)
        if applied:
            self.set_theme(DEFAULT_THEME)
        result = delete_theme_set(name)
        if not result["ok"]:
            messagebox.showerror(APP_NAME, result["error"],
                                 parent=self.root)
            return
        if self.cfg.get("custom_theme") == name:
            self.cfg["custom_theme"] = ""
            save_config(self.cfg)
        remaining = list_theme_sets()
        next_name = remaining[0] if remaining else None
        self._theme_active_set = next_name
        self._theme_preview_cache.clear()
        self._refresh_theme_panel(select=next_name)
        self.status_var.set(f"Тема «{name}» удалена")
        self.log(f"Своя тема «{name}» удалена")

    def _theme_rename_set(self):
        old = getattr(self, "_theme_active_set", None)
        if not old:
            return
        new = self.theme_rename_var.get().strip()
        if not valid_theme_set_name(new):
            messagebox.showerror(
                APP_NAME,
                "Имя темы не должно содержать <>:\"/\\|?* и не может "
                "быть пустым.",
                parent=self.root)
            return
        result = rename_theme_set(old, new)
        if not result["ok"]:
            messagebox.showerror(APP_NAME, result["error"],
                                 parent=self.root)
            return
        applied = (self.theme_name == "custom"
                   and self.custom_theme_name == old)
        if self.cfg.get("custom_theme") == old:
            self.cfg["custom_theme"] = result["name"]
            save_config(self.cfg)
        self._theme_preview_cache.clear()
        if applied:
            self.set_theme("custom", custom_name=result["name"])
        else:
            self._theme_active_set = result["name"]
            self._refresh_theme_panel(select=result["name"])
        self.status_var.set(f"Тема переименована в «{result['name']}»")
        self.log(f"Своя тема «{old}» → «{result['name']}»")

    def _theme_import_set(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Импорт темы",
            filetypes=[("ZIP-архив", "*.zip")])
        if not path:
            return
        result = import_theme_set(path)
        if not result["ok"]:
            messagebox.showerror(
                APP_NAME,
                f"Не удалось импортировать тему:\n{result['error']}",
                parent=self.root)
            return
        self._theme_preview_cache.clear()
        self._theme_active_set = result["name"]
        self._refresh_theme_panel(select=result["name"])
        message = f"Импортирована тема «{result['name']}»"
        if result.get("image"):
            message += "\nКартинка темы тоже импортирована."
        message += "\nНажми «Применить тему», чтобы включить её."
        self.status_var.set(f"Импортирована тема «{result['name']}»")
        self.log(f"Импорт темы «{result['name']}»")
        messagebox.showinfo(APP_NAME, message, parent=self.root)

    def _theme_export_set(self):
        name = getattr(self, "_theme_active_set", None)
        if not name:
            messagebox.showinfo(APP_NAME, "Сначала выбери тему.",
                                parent=self.root)
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Экспорт темы",
            defaultextension=".zip", initialfile=f"{name}.zip",
            filetypes=[("ZIP-архив", "*.zip")])
        if not path:
            return
        result = export_theme_set(name, path)
        if not result["ok"]:
            messagebox.showerror(
                APP_NAME, f"Не удалось экспортировать тему:\n"
                          f"{result['error']}", parent=self.root)
            return
        self.status_var.set(f"Тема «{name}» экспортирована")
        self.log(f"Экспорт темы «{name}» → {path}")
        messagebox.showinfo(APP_NAME, f"Тема сохранена:\n{result['path']}",
                            parent=self.root)

    def _draw_theme_preview_fallback(self, canvas, palette, width, height):
        canvas.create_rectangle(0, 0, width, 18,
                                fill=palette["titlebar"], outline="")
        canvas.create_rectangle(0, 18, 78, height,
                                fill=palette["panel"], outline="")
        canvas.create_rectangle(90, 30, width - 12, height - 14,
                                fill=palette["surface"],
                                outline=palette["border"])
        canvas.create_rectangle(100, 44, width - 26, 51,
                                fill=palette["text"], outline="")
        canvas.create_rectangle(100, 60, width - 90, 65,
                                fill=palette["muted"], outline="")
        canvas.create_rectangle(100, height - 46, 190, height - 24,
                                fill=palette["accent"], outline="")
        canvas.create_rectangle(0, 0, 3, height,
                                fill=palette["accent"], outline="")
        canvas.create_rectangle(0, height - 3, width, height,
                                fill=palette["accent"], outline="")

    def _make_theme_preview(self, palette, width, height):
        if not HAVE_PIL:
            return None

        def rgb(key):
            value = str(palette.get(key) or "#000000").lstrip("#")
            try:
                return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
            except (ValueError, TypeError):
                return (0, 0, 0)

        source = getattr(self, "_theme_image_path", None)
        base_image = None
        if source is not None:
            try:
                base_image = Image.open(source).convert("RGB")
                scale = max(width / base_image.width,
                            height / base_image.height)
                size = (max(1, int(base_image.width * scale + 0.5)),
                        max(1, int(base_image.height * scale + 0.5)))
                base_image = base_image.resize(size, Image.LANCZOS)
                left = max(0, (base_image.width - width) // 2)
                top = max(0, (base_image.height - height) // 2)
                base_image = base_image.crop(
                    (left, top, left + width, top + height)).convert("RGBA")
            except Exception:
                base_image = None
        if base_image is None:
            base_image = Image.new("RGBA", (width, height),
                                   rgb("bg") + (255,))
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rectangle([0, 0, width, 18], fill=rgb("titlebar") + (238,))
        draw.rectangle([0, 18, 78, height], fill=rgb("panel") + (240,))
        draw.rectangle([90, 30, width - 12, height - 14],
                       fill=rgb("surface") + (244,))
        draw.rectangle([100, 44, width - 26, 51],
                       fill=rgb("text") + (95,))
        draw.rectangle([100, 60, max(140, width - 90), 65],
                       fill=rgb("muted") + (120,))
        draw.rectangle([100, height - 46, 190, height - 24],
                       fill=rgb("accent") + (255,))
        draw.rectangle([0, 0, 3, height], fill=rgb("accent") + (255,))
        draw.rectangle([0, height - 3, width, height],
                       fill=rgb("accent") + (255,))
        for row_y in (34, 56, 78, 100):
            if row_y + 8 < height:
                draw.rectangle([12, row_y, 66, row_y + 7],
                               fill=rgb("text") + (80,))
        image = Image.alpha_composite(base_image, overlay).convert("RGB")
        return ImageTk.PhotoImage(image)

    def _render_theme_preview(self, _event=None):
        canvas = getattr(self, "theme_preview_canvas", None)
        if canvas is None:
            return
        try:
            width = max(260, canvas.winfo_width())
        except tk.TclError:
            return
        height = THEME_PREVIEW_H
        name = getattr(self, "_theme_active_set", None)
        palette = load_theme_palette(name) if name else None
        has_theme = palette is not None
        if palette is None:
            palette = build_theme_palette("dark", self.accent, self.accent)
        source = getattr(self, "_theme_image_path", None)
        try:
            stamp = Path(source).stat().st_mtime_ns if source else 0
        except OSError:
            stamp = 0
        key = (name or "", width, height, palette.get("base"),
               palette.get("tint"), palette.get("accent"),
               str(source or ""), stamp)
        photo = self._theme_preview_cache.get(key)
        if photo is None and HAVE_PIL:
            try:
                photo = self._make_theme_preview(palette, width, height)
            except Exception:
                photo = None
            if photo is not None:
                self._theme_preview_cache[key] = photo
                if len(self._theme_preview_cache) > 8:
                    self._theme_preview_cache.pop(
                        next(iter(self._theme_preview_cache)))
        try:
            canvas.delete("all")
            canvas.configure(bg=palette.get("bg") or "#000000")
            if photo is not None:
                self._theme_preview_photo = photo
                canvas.create_image(0, 0, image=photo, anchor="nw")
                return
            self._theme_preview_photo = None
            self._draw_theme_preview_fallback(canvas, palette, width, height)
            if not has_theme:
                canvas.create_text(
                    width // 2, height // 2,
                    text="Выбери тему или создай новую",
                    fill=palette["muted"], font=(UI_FONT, 10))
        except tk.TclError:
            pass

    # -- страница «Кастомизация» ---------------------------------------------------------

    def _build_page_launchstyle(self):
        c = self.c
        pad = self._scroll_page("launchstyle")

        self._page_header(
            pad, "✎", "Кастомизация",
            "Шрифт оверлея, сигналы лаунчера, иконка, звук "
            "и экран запуска. Файлы Roblox не изменяются.")

        inner_font = self._card(
            pad, title="Шрифт оверлея",
            subtitle="TTF или OTF для внутриигрового оверлея",
            glyph="A")
        tk.Label(inner_font, textvariable=self.ui_font_status_var,
                 bg=c["surface"], fg=c["text"],
                 font=(UI_FONT, 11, "bold"), anchor="w",
                 justify="left", wraplength=560).pack(fill="x")
        self.ui_font_sample = tk.Label(
            inner_font,
            text="Пример: 60 FPS · 62 °C · 15:42:07 · RAM 47%",
            bg=c["surface2"], fg=c["text"],
            font=(self._ui_font_family or UI_FONT, 12),
            anchor="w", padx=12, pady=10)
        self.ui_font_sample.pack(fill="x", pady=(10, 0))
        row_font = tk.Frame(inner_font, bg=c["surface"])
        row_font.pack(fill="x", pady=(10, 0))
        self._button(row_font, "Выбрать шрифт", self.choose_ui_font,
                     kind="primary", side="left")
        self.btn_ui_font_reset = self._button(
            row_font, "Сбросить", self.reset_ui_font,
            side="left", padx=8)
        self._hint(
            inner_font,
            "TTF или OTF до 20 МБ. Шрифт подключается только к оверлею "
            "Maklyxa (FPS, время, память) и не меняет интерфейс Roblox; "
            "лаунчер остаётся на Segoe UI. Применится к оверлею при "
            "следующем запуске.", wrap=560)

        inner = self._card(
            pad, title="Иконка",
            subtitle="Превью экрана запуска и иконка для ярлыков Roblox",
            glyph="◈")
        row = tk.Frame(inner, bg=c["surface"])
        row.pack(fill="x", pady=(10, 0))
        self.launch_icon_box = tk.Frame(
            row, bg=c["surface2"], width=96, height=96,
            highlightthickness=1, highlightbackground=c["border"])
        self.launch_icon_box.pack(side="left")
        self.launch_icon_box.pack_propagate(False)
        self.launch_icon_label = tk.Label(
            self.launch_icon_box, text="✦", bg=c["surface2"],
            fg=c["muted"], font=(UI_FONT, 26, "bold"))
        self.launch_icon_label.pack(fill="both", expand=True)
        info = tk.Frame(row, bg=c["surface"])
        info.pack(side="left", fill="both", expand=True, padx=(16, 0))
        tk.Label(info, textvariable=self.launch_icon_status_var,
                 bg=c["surface"], fg=c["text"],
                 font=(UI_FONT, 11, "bold"), anchor="w",
                 justify="left", wraplength=520).pack(fill="x")
        self._hint(
            info, "PNG, JPG, WEBP, BMP или ICO. Лаунчер сам сделает "
                  "многоразмерную иконку 16–256 px.", wrap=520)
        row_btns = tk.Frame(inner, bg=c["surface"])
        row_btns.pack(fill="x", pady=(12, 0))
        self._button(row_btns, "Выбрать иконку", self.choose_launch_icon,
                     kind="primary", side="left")
        self.btn_launch_icon_remove = self._button(
            row_btns, "Убрать", self.remove_launch_icon_ui,
            side="left", padx=8)
        row_short = tk.Frame(inner, bg=c["surface"])
        row_short.pack(fill="x", pady=(8, 0))
        self.btn_shortcut_apply = self._button(
            row_short, "Применить к ярлыкам",
            self.apply_launch_shortcut_icon, side="left")
        self.btn_shortcut_restore = self._button(
            row_short, "Вернуть ярлыки", self.restore_launch_shortcuts,
            side="left", padx=8)
        tk.Label(inner, textvariable=self.launch_shortcut_status_var,
                 bg=c["surface"], fg=c["muted"], font=(UI_FONT, 9),
                 anchor="w", wraplength=560, justify="left").pack(
                     fill="x", pady=(9, 0))
        self._hint(
            inner,
            "Меняются только ярлыки Roblox на рабочем столе, в «Пуске» "
            "и на панели задач. Перед заменой каждый ярлык копируется "
            "в Launch\\shortcuts_backup, «Вернуть ярлыки» откатывает "
            "изменения.", wrap=560)

        inner2 = self._card(
            pad, title="Звук запуска",
            subtitle="Короткий сигнал при старте игры", glyph="♪")
        self.var_launch_sound = tk.BooleanVar(
            value=bool(self.cfg.get("launch_sound", True)))
        self._checkbox(
            inner2, "Играть звук при запуске игры",
            self.var_launch_sound,
            command=self._save_launch_sound_flag).pack(
                anchor="w", pady=(6, 8))
        tk.Label(inner2, textvariable=self.launch_sound_status_var,
                 bg=c["surface"], fg=c["text"],
                 font=(UI_FONT, 10, "bold"), anchor="w",
                 wraplength=560).pack(fill="x")
        row_sound = tk.Frame(inner2, bg=c["surface"])
        row_sound.pack(fill="x", pady=(9, 0))
        self._button(row_sound, "Выбрать звук", self.choose_launch_sound,
                     kind="primary", side="left")
        self._button(row_sound, "Послушать",
                     self.play_launch_sound_preview, side="left", padx=8)
        self._button(row_sound, "Стоп", self.stop_launch_sound_preview,
                     side="left")
        self._button(row_sound, "Убрать", self.remove_launch_sound_ui,
                     side="left", padx=8)
        self._hint(
            inner2, "WAV или MP3 до 12 МБ. Звук проигрывает лаунчер "
                    "через Windows; звуки и файлы Roblox не заменяются.",
            wrap=560)

        inner_err = self._card(
            pad, title="Звук ошибки",
            subtitle="Сигнал, если запуск не удался — например, «Oof»",
            glyph="!")
        self.var_error_sound = tk.BooleanVar(
            value=bool(self.cfg.get("error_sound", True)))
        self._checkbox(
            inner_err, "Играть звук при ошибке запуска",
            self.var_error_sound,
            command=self._save_error_sound_flag).pack(
                anchor="w", pady=(6, 8))
        tk.Label(inner_err, textvariable=self.error_sound_status_var,
                 bg=c["surface"], fg=c["text"],
                 font=(UI_FONT, 10, "bold"), anchor="w",
                 wraplength=560).pack(fill="x")
        row_err = tk.Frame(inner_err, bg=c["surface"])
        row_err.pack(fill="x", pady=(9, 0))
        self._button(row_err, "Выбрать звук", self.choose_error_sound,
                     kind="primary", side="left")
        self._button(row_err, "Послушать",
                     self.play_error_sound_preview, side="left", padx=8)
        self._button(row_err, "Стоп", self.stop_error_sound_preview,
                     side="left")
        self._button(row_err, "Убрать", self.remove_error_sound_ui,
                     side="left", padx=8)
        self._hint(
            inner_err, "WAV или MP3 до 12 МБ. Лаунчер проигрывает его "
                       "только при своей ошибке запуска; звуки Roblox "
                       "не подменяются.", wrap=560)

        inner3 = self._card(
            pad, title="Экран запуска",
            subtitle="Что показывать, пока Roblox открывается",
            glyph="✦")
        row_style = tk.Frame(inner3, bg=c["surface"])
        row_style.pack(fill="x", pady=(9, 0))
        self.launch_style_buttons = {}
        for key in ("off", "minimal", "card"):
            btn = self._button(
                row_style, LAUNCH_STYLE_LABELS[key],
                lambda k=key: self.set_launch_splash(k),
                side="left", padx=(0, 8))
            self.launch_style_buttons[key] = btn
        self._button(row_style, "Показать",
                     self.preview_launch_splash, kind="primary",
                     side="left", padx=(10, 0))
        tk.Label(inner3, textvariable=self.launch_style_note_var,
                 bg=c["surface"], fg=c["muted"], font=(UI_FONT, 9),
                 anchor="w", justify="left", wraplength=560).pack(
                     fill="x", pady=(9, 0))
        self._hint(
            inner3, "Экран — отдельное окно лаунчера поверх игры. "
                    "Он закрывается сам или по клику; клиент Roblox "
                    "не патчится.", wrap=560)

        inner4 = self._card(pad, title="Файлы", glyph="ⓘ")
        tk.Label(inner4, text=str(CUSTOMIZE_DIR), bg=c["surface"],
                 fg=c["muted"], font=(UI_FONT, 9), anchor="w",
                 wraplength=560, justify="left").pack(fill="x")
        tk.Label(inner4, text=str(LAUNCH_DIR), bg=c["surface"],
                 fg=c["muted"], font=(UI_FONT, 9), anchor="w",
                 wraplength=560, justify="left").pack(fill="x")
        row_files = tk.Frame(inner4, bg=c["surface"])
        row_files.pack(fill="x", pady=(10, 0))
        self._button(row_files, "Открыть папку кастомизации",
                     self.open_customize_dir, side="left")
        self._button(row_files, "Открыть папку стиля",
                     self.open_launch_dir, side="left", padx=8)
        self._hint(inner4, "Шрифт оверлея, звуки и резервные копии "
                           "ярлыков хранятся локально.", wrap=560)

        inner5 = self._card(
            pad, title="Почему файлы Roblox не меняются", glyph="ⓘ")
        tk.Label(
            inner5,
            text="Шрифты и звуки внутри клиента Roblox — часть его "
                 "файлов. Их замена считается вмешательством в клиент "
                 "и может привести к блокировке аккаунта. Поэтому "
                 "лаунчер меняет только собственное окно: шрифт оверлея, "
                 "свои сигналы, не трогая установленный Roblox.",
            bg=c["surface"], fg=c["muted"], font=(UI_FONT, 9),
            anchor="w", justify="left", wraplength=560).pack(fill="x")

        self._refresh_launch_style_ui()

    def _refresh_launch_style_ui(self):
        self._refresh_launch_icon()
        self._refresh_launch_sound()
        self._refresh_error_sound()
        self._refresh_ui_font()
        self._refresh_launch_style_buttons()

    def _refresh_launch_style_buttons(self):
        active = self.cfg.get("launch_splash") or "card"
        if active not in LAUNCH_STYLE_LABELS:
            active = "card"
        for key, btn in getattr(self, "launch_style_buttons", {}).items():
            try:
                btn.configure(
                    text=("● " if key == active else "")
                    + LAUNCH_STYLE_LABELS[key],
                    highlightthickness=2 if key == active else 0,
                    highlightbackground=(self.accent if key == active
                                         else self.c["surface2"]))
            except tk.TclError:
                pass
        try:
            self.launch_style_note_var.set(LAUNCH_STYLE_NOTES[active])
        except (AttributeError, tk.TclError):
            pass

    def _splash_icon_photo(self, size=56):
        if not HAVE_PIL:
            return None
        state = launch_icon_state()
        source = state.get("preview") or state.get("icon")
        if source is None and ICON_PNG.exists():
            source = ICON_PNG
        if source is None:
            return None
        try:
            stamp = Path(source).stat().st_mtime_ns
        except OSError:
            stamp = 0
        key = (str(source), stamp, int(size))
        photo = self._splash_icon_cache.get(key)
        if photo is not None:
            return photo
        try:
            with Image.open(source) as raw:
                image = raw.convert("RGBA").resize(
                    (int(size), int(size)),
                    getattr(getattr(Image, "Resampling", Image), "LANCZOS"))
            photo = ImageTk.PhotoImage(image)
        except Exception:  # noqa: BLE001 — битая картинка не должна ломать UI
            return None
        self._splash_icon_cache[key] = photo
        if len(self._splash_icon_cache) > 6:
            self._splash_icon_cache.pop(next(iter(self._splash_icon_cache)))
        return photo

    def _refresh_launch_icon(self):
        state = launch_icon_state()
        if state["custom"]:
            self.launch_icon_status_var.set(
                f"Своя иконка · {Path(state['icon']).name}")
        else:
            self.launch_icon_status_var.set(
                "Стандартная — выбери PNG, JPG или ICO")
        photo = self._splash_icon_photo(84)
        self._launch_icon_photo_ref = photo
        try:
            if photo is not None:
                self.launch_icon_label.configure(image=photo, text="")
            else:
                self.launch_icon_label.configure(image="", text="✦")
        except tk.TclError:
            pass
        count = shortcut_backup_count()
        self.launch_shortcut_status_var.set(
            f"Резервных копий ярлыков: {count}" if count
            else "Ярлыки пока не менялись")

    def _refresh_launch_sound(self):
        path = launch_sound_path()
        if hasattr(self, "var_launch_sound"):
            try:
                self.var_launch_sound.set(
                    bool(self.cfg.get("launch_sound", True)))
            except tk.TclError:
                pass
        if path is None:
            self.launch_sound_status_var.set("Свой звук не выбран")
            return
        try:
            size = mb(path.stat().st_size)
        except OSError:
            size = "?"
        name = str(self.cfg.get("launch_sound_name") or path.name)
        self.launch_sound_status_var.set(f"Звук: {name} · {size} МБ")

    def _refresh_error_sound(self):
        path = launch_sound_path(role="error")
        if hasattr(self, "var_error_sound"):
            try:
                self.var_error_sound.set(
                    bool(self.cfg.get("error_sound", True)))
            except tk.TclError:
                pass
        if path is None:
            self.error_sound_status_var.set("Свой звук ошибки не выбран")
            return
        try:
            size = mb(path.stat().st_size)
        except OSError:
            size = "?"
        name = str(self.cfg.get("error_sound_name") or path.name)
        self.error_sound_status_var.set(
            f"Звук ошибки: {name} · {size} МБ")

    def _refresh_ui_font(self):
        state = ui_font_state()
        family = self._ui_font_family or ""
        if state["custom"] and self._loaded_font_path is not None:
            name = str(self.cfg.get("ui_font_name")
                       or Path(state["path"]).name)
            self.ui_font_status_var.set(
                f"Свой шрифт оверлея: {name} · {family}")
        elif state["custom"]:
            self.ui_font_status_var.set(
                "Шрифт сохранён, но Windows не смог его подключить — "
                "выбери другой файл")
        else:
            self.ui_font_status_var.set(
                "Стандартный шрифт оверлея (Segoe UI)")
        sample = getattr(self, "ui_font_sample", None)
        if sample is not None:
            try:
                sample.configure(font=(family or UI_FONT, 12))
            except tk.TclError:
                pass

    def _launch_action_error(self, message):
        self.status_var.set(message)
        self.log(f"Стиль запуска: {message}")
        if self.interactive:
            messagebox.showwarning(APP_NAME, message, parent=self.root)

    def choose_launch_icon(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Иконка запуска",
            filetypes=[("Иконки и картинки",
                        "*.png *.ico *.jpg *.jpeg *.webp *.bmp"),
                       ("Все файлы", "*.*")])
        if not path:
            return
        result = import_launch_icon(path)
        if not result["ok"]:
            self._launch_action_error(
                f"Не удалось добавить иконку: {result['error']}")
            return
        self._refresh_launch_icon()
        self.status_var.set("Иконка запуска обновлена")
        self.log(f"Иконка запуска: {Path(path).name}")
        if shortcut_backup_count():
            self.apply_launch_shortcut_icon()

    def remove_launch_icon_ui(self):
        had_shortcuts = shortcut_backup_count() > 0
        result = remove_launch_icon()
        if had_shortcuts:
            restored = restore_launch_icon_shortcuts()
        else:
            restored = None
        self._refresh_launch_icon()
        if not result["ok"]:
            self._launch_action_error(
                f"Иконка не удалилась: {result['error']}")
            return
        if restored is not None and not restored["ok"]:
            self._launch_action_error(
                "Иконка убрана, но ярлыки не вернулись: "
                + "; ".join(restored["errors"][:2]))
            return
        if had_shortcuts:
            text = "Своя иконка убрана, ярлыки восстановлены"
        else:
            text = "Своя иконка убрана"
        self.status_var.set(text)
        self.log(text)

    def choose_launch_sound(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Звук запуска",
            filetypes=[("Звуки WAV и MP3", "*.wav *.mp3"),
                       ("Все файлы", "*.*")])
        if not path:
            return
        result = import_launch_sound(path)
        if not result["ok"]:
            self._launch_action_error(
                f"Не удалось добавить звук: {result['error']}")
            return
        self.cfg["launch_sound_name"] = Path(path).name[:80]
        self.cfg["launch_sound"] = True
        save_config(self.cfg)
        self._refresh_launch_sound()
        self.status_var.set("Звук запуска сохранён")
        self.log(f"Звук запуска: {Path(path).name}")

    def remove_launch_sound_ui(self):
        result = remove_launch_sound()
        self.cfg["launch_sound_name"] = ""
        save_config(self.cfg)
        self._refresh_launch_sound()
        if result["ok"]:
            self.status_var.set("Звук запуска убран")
            self.log("Звук запуска удалён")
        else:
            self._launch_action_error(
                f"Звук не удалился: {result['error']}")

    def _save_launch_sound_flag(self):
        self.cfg["launch_sound"] = bool(self.var_launch_sound.get())
        save_config(self.cfg)
        if self.cfg["launch_sound"]:
            self.status_var.set("Звук при запуске включён")
            self.log("Звук при запуске включён")
        else:
            stop_launch_sound()
            self.status_var.set("Звук при запуске выключен")
            self.log("Звук при запуске выключен")

    def play_launch_sound_preview(self):
        path = launch_sound_path()
        if path is None:
            self.launch_sound_status_var.set("Сначала выбери WAV или MP3")
            return
        if not play_launch_sound(path):
            self.launch_sound_status_var.set(
                "Не удалось проиграть — проверь формат файла")
            return
        self.status_var.set("Проигрываю звук запуска")
        self.root.after(3500, self._refresh_launch_sound)

    def stop_launch_sound_preview(self):
        stop_launch_sound()
        self._refresh_launch_sound()
        self.status_var.set("Звук остановлен")

    def choose_ui_font(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Шрифт оверлея",
            filetypes=[("Шрифты TTF и OTF", "*.ttf *.otf"),
                       ("Все файлы", "*.*")])
        if path:
            self.apply_ui_font_file(path)

    def apply_ui_font_file(self, source):
        info = inspect_ui_font(source)
        if not info["ok"]:
            self._launch_action_error(
                f"Не удалось добавить шрифт: {info['error']}")
            return False
        source = Path(info["path"])
        if not load_private_font(source):
            self._launch_action_error(
                "Windows не смог подключить шрифт — файл повреждён "
                "или это не TTF/OTF")
            return False
        family = _resolve_tk_family(self.root, info["families"])
        if not family:
            unload_private_font(source)
            self._launch_action_error(
                "Tk не увидел шрифт — попробуй другой TTF или OTF")
            return False
        old_path = self._loaded_font_path
        result = import_ui_font(source)
        unload_private_font(source)
        if not result["ok"]:
            self._launch_action_error(
                f"Не удалось сохранить шрифт: {result['error']}")
            return False
        dest = Path(result["path"])
        if old_path is not None:
            unload_private_font(old_path)
        if not load_private_font(dest):
            if (old_path is not None and Path(old_path) != dest
                    and Path(old_path).is_file()):
                load_private_font(old_path)
            self._launch_action_error(
                "Windows не смог подключить шрифт из папки лаунчера")
            return False
        self._loaded_font_path = dest
        self._ui_font_family = family
        self.cfg["ui_font_name"] = info["name"]
        self.cfg["ui_font_family"] = family
        save_config(self.cfg)
        self._refresh_ui_font()
        self.status_var.set(f"Шрифт оверлея: {family}")
        self.log(f"Шрифт оверлея: {info['name']} ({family})")
        return True

    def reset_ui_font(self):
        old_path = self._loaded_font_path
        if old_path is not None:
            unload_private_font(old_path)
        result = remove_ui_font()
        self._loaded_font_path = None
        self._ui_font_family = ""
        self.cfg["ui_font_name"] = ""
        self.cfg["ui_font_family"] = ""
        save_config(self.cfg)
        self._refresh_ui_font()
        if not result["ok"]:
            self._launch_action_error(
                f"Шрифт сброшен, но файл не удалился: {result['error']}")
            return
        self.status_var.set("Шрифт оверлея сброшен")
        self.log("Шрифт оверлея: стандартный Segoe UI")

    def choose_error_sound(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Звук ошибки",
            filetypes=[("Звуки WAV и MP3", "*.wav *.mp3"),
                       ("Все файлы", "*.*")])
        if not path:
            return
        result = import_launch_sound(path, role="error")
        if not result["ok"]:
            self._launch_action_error(
                f"Не удалось добавить звук ошибки: {result['error']}")
            return
        self.cfg["error_sound_name"] = Path(path).name[:80]
        self.cfg["error_sound"] = True
        save_config(self.cfg)
        self._refresh_error_sound()
        self.status_var.set("Звук ошибки сохранён")
        self.log(f"Звук ошибки: {Path(path).name}")

    def remove_error_sound_ui(self):
        result = remove_launch_sound(role="error")
        self.cfg["error_sound_name"] = ""
        save_config(self.cfg)
        self._refresh_error_sound()
        if result["ok"]:
            self.status_var.set("Звук ошибки убран")
            self.log("Звук ошибки удалён")
        else:
            self._launch_action_error(
                f"Звук не удалился: {result['error']}")

    def _save_error_sound_flag(self):
        self.cfg["error_sound"] = bool(self.var_error_sound.get())
        save_config(self.cfg)
        if self.cfg["error_sound"]:
            self.status_var.set("Звук ошибки включён")
            self.log("Звук при ошибке запуска включён")
        else:
            stop_launch_sound()
            self.status_var.set("Звук ошибки выключен")
            self.log("Звук при ошибке запуска выключен")

    def play_error_sound_preview(self):
        path = launch_sound_path(role="error")
        if path is None:
            self.error_sound_status_var.set(
                "Сначала выбери WAV или MP3")
            return
        if not play_launch_sound(path):
            self.error_sound_status_var.set(
                "Не удалось проиграть — проверь формат файла")
            return
        self.status_var.set("Проигрываю звук ошибки")
        self.root.after(3500, self._refresh_error_sound)

    def stop_error_sound_preview(self):
        stop_launch_sound()
        self._refresh_error_sound()
        self.status_var.set("Звук остановлен")

    def _play_error_sound(self):
        if not self.interactive or self._closing:
            return
        if not self.cfg.get("error_sound", True):
            return
        path = launch_sound_path(role="error")
        if path is not None:
            play_launch_sound(path)

    def open_customize_dir(self):
        try:
            CUSTOMIZE_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(CUSTOMIZE_DIR))
        except OSError as exc:
            self._launch_action_error(
                f"Не удалось открыть папку: {exc}")

    def set_launch_splash(self, style):
        if style not in LAUNCH_STYLE_LABELS:
            return
        self.cfg["launch_splash"] = style
        save_config(self.cfg)
        self._refresh_launch_style_buttons()
        self.status_var.set(
            f"Экран запуска: {LAUNCH_STYLE_LABELS[style].lower()}")
        self.log(f"Стиль запуска: {LAUNCH_STYLE_LABELS[style]}")

    def preview_launch_splash(self):
        style = self.cfg.get("launch_splash") or "card"
        if style == "off":
            style = "card"
        self._show_launch_splash(
            "Тестовая игра", "Так выглядит экран запуска",
            duration=2800, force_style=style)

    def open_launch_dir(self):
        try:
            LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(LAUNCH_DIR))
        except OSError as exc:
            self._launch_action_error(
                f"Не удалось открыть папку: {exc}")

    # -- ярлыки и эффект запуска -----------------------------------------------------

    def apply_launch_shortcut_icon(self):
        if self._shortcut_busy:
            return
        if not launch_icon_state()["custom"]:
            self._launch_action_error("Сначала выбери свою иконку")
            return
        self._shortcut_busy = True
        self._btn_state(self.btn_shortcut_apply, "ghost", False,
                        text="Применяю…")
        self.launch_shortcut_status_var.set("Ищу ярлыки Roblox…")
        self.status_var.set("Меняю иконки ярлыков Roblox…")

        def worker():
            result = apply_launch_icon_to_shortcuts()
            self.q.put(lambda: self._shortcut_apply_done(result))

        threading.Thread(target=worker, daemon=True).start()

    def _shortcut_apply_done(self, result):
        self._shortcut_busy = False
        if self._closing:
            return
        try:
            self._btn_state(self.btn_shortcut_apply, "ghost", True,
                            text="Применить к ярлыкам")
        except tk.TclError:
            pass
        self._refresh_launch_icon()
        changed = int(result.get("changed") or 0)
        errors = result.get("errors") or []
        if changed:
            text = f"Готово: обновлено ярлыков — {changed}"
            if errors:
                text += f", ошибок — {len(errors)}"
            self.status_var.set(text)
            self.log(f"Иконка ярлыков: {text}")
            self.launch_shortcut_status_var.set(text)
        else:
            error = result.get("error") or "ярлыки не изменены"
            self.status_var.set(error)
            self.log(f"Иконка ярлыков: {error}")
            self.launch_shortcut_status_var.set(error)

    def restore_launch_shortcuts(self):
        if self._shortcut_busy:
            return
        if not shortcut_backup_count():
            self.launch_shortcut_status_var.set(
                "Резервных копий нет — ярлыки не менялись")
            self.status_var.set("Нечего восстанавливать")
            return
        if self.interactive and not messagebox.askyesno(
                APP_NAME, "Вернуть исходные иконки ярлыков Roblox?",
                parent=self.root):
            return
        result = restore_launch_icon_shortcuts()
        self._refresh_launch_icon()
        if result["ok"]:
            text = f"Исходные ярлыки восстановлены: {result['restored']}"
            self.status_var.set(text)
            self.log(text)
            self.launch_shortcut_status_var.set(text)
        else:
            text = ("Часть ярлыков не вернулась: "
                    + "; ".join(result["errors"][:3]))
            self.status_var.set("Не удалось вернуть все ярлыки")
            self.log(f"Ярлыки: {text}")
            self.launch_shortcut_status_var.set(text)

    def _show_launch_splash(self, title, subtitle="Запускаю Roblox…",
                            duration=None, force_style=None):
        style = force_style or self.cfg.get("launch_splash") or "card"
        if style == "off":
            return None
        self._hide_launch_splash()
        try:
            splash = LaunchSplash(self, title, subtitle, style)
        except tk.TclError:
            return None
        if splash.win is None:
            return None
        self._launch_splash = splash
        if duration is not None:
            self._launch_splash_job = self.root.after(
                duration, self._finish_launch_effect)
        return splash

    def _hide_launch_splash(self):
        if self._launch_splash_job is not None:
            try:
                self.root.after_cancel(self._launch_splash_job)
            except tk.TclError:
                pass
            self._launch_splash_job = None
        splash, self._launch_splash = self._launch_splash, None
        if splash is not None:
            splash.close()

    def _finish_launch_effect(self):
        self._launch_splash_job = None
        self._hide_launch_splash()

    def _begin_launch_effect(self, title, subtitle="Запускаю Roblox…"):
        self._launch_effect_ts = time.monotonic()
        self._show_launch_splash(title, subtitle)
        if self.cfg.get("launch_sound") and self.interactive:
            path = launch_sound_path()
            if path is not None:
                play_launch_sound(path)

    def _end_launch_effect(self, status=None):
        if self._launch_splash is None:
            return
        if status:
            self._launch_splash.set_status(status)
        elapsed = time.monotonic() - (self._launch_effect_ts
                                      or time.monotonic())
        delay = max(450, 1700 - int(elapsed * 1000))
        if self._launch_splash_job is not None:
            try:
                self.root.after_cancel(self._launch_splash_job)
            except tk.TclError:
                pass
        self._launch_splash_job = self.root.after(
            delay, self._finish_launch_effect)

    # -- страница «Галерея» -------------------------------------------------------------

    def _build_page_gallery(self):
        c = self.c
        pad = self._scroll_page("gallery", padx=26)

        self._page_header(pad, "▦", "Галерея скриншотов")

        bar = tk.Frame(pad, bg=c["bg"])
        bar.pack(fill="x", pady=(0, 8))
        self.gallery_refresh_btn = self._button(
            bar, "↻  Обновить", self.refresh_gallery, kind="primary",
            side="left")
        self._button(bar, "Открыть папку", self.open_screenshots_folder,
                     side="left", padx=8)
        self._button(bar, "Назначить плейс…",
                     self._open_gallery_assign_dialog, side="left")
        self.gallery_status_var = tk.StringVar(
            value="Галерея ещё не сканировалась")
        tk.Label(pad, textvariable=self.gallery_status_var, bg=c["bg"],
                 fg=c["muted"], font=(UI_FONT, 9), anchor="w",
                 justify="left", wraplength=760).pack(fill="x",
                                                      pady=(0, 10))

        inner = self._card(
            pad, title="Снимки по плейсам",
            subtitle="Новые снимки появляются после «Обновить». "
                     "Старые файлы без записи о запуске попадают в группы "
                     "по дате — плейс можно назначить вручную.",
            glyph="▦")

        head = tk.Frame(inner, bg=c["surface"])
        head.pack(fill="x", pady=(10, 0))
        tk.Label(head, text="Группа", bg=c["surface"], fg=c["muted"],
                 font=(UI_FONT, 9)).pack(side="left")
        self.gallery_group_combo = ttk.Combobox(
            head, state="readonly", width=44, font=(UI_FONT, 9))
        self.gallery_group_combo.pack(side="left", fill="x", expand=True,
                                       padx=(10, 0))
        self.gallery_group_combo.bind(
            "<<ComboboxSelected>>", self._gallery_group_changed)
        self.gallery_group_meta_var = tk.StringVar(value="")
        meta_row = tk.Frame(inner, bg=c["surface"])
        meta_row.pack(fill="x", pady=(5, 0))
        tk.Label(meta_row, textvariable=self.gallery_group_meta_var,
                 bg=c["surface"], fg=c["muted"], font=(UI_FONT, 8),
                 anchor="w").pack(side="left", fill="x", expand=True)
        self.gallery_next_btn = self._button(
            meta_row, "▶", self._gallery_next, side="right")
        self.gallery_page_var = tk.StringVar(value="")
        tk.Label(meta_row, textvariable=self.gallery_page_var,
                 bg=c["surface"], fg=c["muted"], font=(UI_FONT, 8)).pack(
                     side="right", padx=6)
        self.gallery_prev_btn = self._button(
            meta_row, "◀", self._gallery_prev, side="right")

        self.gallery_grid = tk.Frame(inner, bg=c["surface"])
        self.gallery_grid.pack(fill="x", pady=(8, 0))
        for col in range(3):
            self.gallery_grid.grid_columnconfigure(col, weight=1)
        for slot in range(self._gallery_page_size):
            outer = tk.Frame(self.gallery_grid, bg=c["border"])
            tile = tk.Frame(outer, bg=c["surface2"])
            tile.pack(fill="both", expand=True, padx=1, pady=1)
            canvas = tk.Canvas(
                tile, width=GALLERY_TILE_W, height=GALLERY_TILE_H,
                bg=c["list"], highlightthickness=0, bd=0,
                cursor="hand2")
            canvas.pack(padx=6, pady=(6, 4))
            image_item = canvas.create_image(
                GALLERY_TILE_W // 2, GALLERY_TILE_H // 2, anchor="center")
            text_item = canvas.create_text(
                GALLERY_TILE_W // 2, GALLERY_TILE_H // 2, text="",
                fill=c["muted"], font=(UI_FONT, 9),
                width=GALLERY_TILE_W - 14, justify="center")
            caption = tk.Label(
                tile, text="", bg=c["surface2"], fg=c["muted"],
                font=(UI_FONT, 8), anchor="w", cursor="hand2")
            caption.pack(fill="x", padx=8, pady=(0, 7))
            for widget in (outer, tile, canvas, caption):
                widget.bind(
                    "<Button-1>",
                    lambda _e, index=slot: self._gallery_tile_click(index))
            self._gallery_tiles.append({
                "outer": outer, "tile": tile, "canvas": canvas,
                "image": image_item, "text": text_item,
                "caption": caption, "shot": None,
            })

        self.gallery_empty = tk.Label(
            inner, text="Пока нет снимков. Сделай скриншот в Roblox "
                        "и нажми «Обновить».",
            bg=c["surface"], fg=c["muted"], font=(UI_FONT, 10),
            anchor="w", justify="left", wraplength=700)
        self.gallery_empty.pack(fill="x", pady=(18, 14))

        clean = self._card(
            pad, title="Очистить задний план",
            subtitle="Убрать интерфейс Roblox на новых снимках",
            glyph="✦")
        tk.Label(
            clean,
            text="Готовый PNG уже содержит интерфейс — убрать его задним "
                 "числом нельзя. Флаг DFIntCanHideGuiGroupId исключён "
                 "Roblox из локального allowlist, поэтому лаунчер не "
                 "записывает нерабочий FastFlag. Чистые кадры дают "
                 "официальные «Записи» (Captures) Roblox: клиент сам "
                 "скрывает UI во время съёмки.",
            bg=c["surface"], fg=c["muted"], font=(UI_FONT, 9), anchor="w",
            justify="left", wraplength=720).pack(fill="x", pady=(10, 0))
        row_clean = tk.Frame(clean, bg=c["surface"])
        row_clean.pack(fill="x", pady=(10, 0))
        self._button(row_clean, "Очистить задний план",
                     self.open_clean_background_dialog, kind="primary",
                     side="left")
        self._button(row_clean, "Открыть папку снимков",
                     self.open_screenshots_folder, side="left", padx=8)

    def _gallery_maybe_refresh(self):
        if (not self.interactive or self._closing
                or self._gallery_busy):
            return
        if (not self._gallery_groups
                or time.time() - self._gallery_last_scan > 60):
            self.refresh_gallery()

    def refresh_gallery(self, resolve=True):
        if self._gallery_busy or getattr(self, "_closing", False):
            return
        self._gallery_busy = True
        self._gallery_refresh_token += 1
        token = self._gallery_refresh_token
        try:
            self.gallery_refresh_btn.configure(state="disabled")
            self.gallery_status_var.set("Сканирую снимки Roblox…")
        except tk.TclError:
            pass
        history = list(self.cfg.get("history") or [])

        def work():
            try:
                shots = scan_screenshots()
                sessions = collect_play_sessions(history)
                cache = gallery_cache_load()
                gallery_seed_places(cache, sessions)
                groups = build_gallery_groups(shots, sessions, cache)
                place_ids = [group["place_id"] for group in groups
                             if group["kind"] == "place"]
                if resolve and place_ids:
                    self.q.put(lambda: self._gallery_status(
                        "Уточняю названия плейсов…"))
                    resolve_gallery_places(
                        cache, place_ids, timeout=5, limit=30)
                    groups = build_gallery_groups(shots, sessions, cache)
                gallery_cache_save(cache)
                data = {
                    "groups": groups,
                    "shots": shots,
                    "stats": gallery_stats(groups),
                    "cache": cache,
                    "folder": str(screenshots_dir()),
                }
                self.q.put(
                    lambda t=token, d=data: self._gallery_apply(t, d))
            except Exception as exc:  # noqa: BLE001 — сеть и файлы
                self.q.put(lambda t=token, e=exc: self._gallery_fail(t, e))

        threading.Thread(target=work, daemon=True,
                         name="gallery-scan").start()

    def _gallery_status(self, text):
        if getattr(self, "_closing", False):
            return
        try:
            self.gallery_status_var.set(text)
        except tk.TclError:
            pass

    def _gallery_apply(self, token, data):
        if token != self._gallery_refresh_token or self._closing:
            return
        self._gallery_busy = False
        self._gallery_last_scan = time.time()
        try:
            self.gallery_refresh_btn.configure(state="normal")
        except tk.TclError:
            return
        groups = data.get("groups") or []
        self._gallery_groups = groups
        self._gallery_shots = data.get("shots") or []
        self._gallery_cache = data.get("cache")
        stats = data.get("stats") or gallery_stats(groups)
        folder = data.get("folder") or str(screenshots_dir())
        if not groups:
            self._gallery_group_key = ""
            self._gallery_visible = []
            try:
                self.gallery_group_combo.configure(values=[])
                self.gallery_group_combo.set("")
                self.gallery_group_meta_var.set("")
                self.gallery_grid.pack_forget()
                self.gallery_empty.pack(fill="x", pady=(18, 14))
                self.gallery_page_var.set("")
                self.gallery_prev_btn.configure(state="disabled")
                self.gallery_next_btn.configure(state="disabled")
            except tk.TclError:
                return
            self.gallery_status_var.set(
                f"В папке {folder} пока нет снимков Roblox")
            self._schedule_page_layout("gallery")
            return
        labels = [self._gallery_group_label(group) for group in groups]
        keys = [group["key"] for group in groups]
        try:
            self.gallery_empty.pack_forget()
            self.gallery_grid.pack(fill="x", pady=(8, 0))
            self.gallery_group_combo.configure(values=labels)
        except tk.TclError:
            return
        pending = self._gallery_pending_key
        self._gallery_pending_key = ""
        if pending in keys:
            self._gallery_group_key = pending
        elif self._gallery_group_key not in keys:
            self._gallery_group_key = keys[0]
        self._gallery_page = 0
        try:
            self.gallery_group_combo.current(keys.index(
                self._gallery_group_key))
        except (ValueError, tk.TclError):
            pass
        self.gallery_status_var.set(
            f"Найдено {stats['total']} · определено {stats['known']} · "
            f"без плейса {stats['unknown']} · групп {stats['groups']} · "
            f"{folder}")
        self._gallery_render()
        self._schedule_page_layout("gallery")

    def _gallery_fail(self, token, exc):
        if token != self._gallery_refresh_token:
            return
        self._gallery_busy = False
        try:
            self.gallery_refresh_btn.configure(state="normal")
            self.gallery_status_var.set(
                f"Не удалось просканировать галерею: {exc}")
        except tk.TclError:
            pass

    @staticmethod
    def _gallery_group_label(group):
        count = len(group.get("shots") or [])
        if group.get("kind") == "date":
            return f"{group['title']} · без плейса ({count})"
        return f"{group['title']} ({count})"

    def _gallery_selected_group(self):
        for group in self._gallery_groups:
            if group["key"] == self._gallery_group_key:
                return group
        return None

    def _gallery_group_changed(self, _event=None):
        try:
            index = self.gallery_group_combo.current()
        except tk.TclError:
            return
        if 0 <= index < len(self._gallery_groups):
            self._gallery_group_key = self._gallery_groups[index]["key"]
            self._gallery_page = 0
            self._gallery_render()

    def _gallery_render(self):
        group = self._gallery_selected_group()
        if group is None:
            self._gallery_visible = []
            for slot in range(self._gallery_page_size):
                self._gallery_clear_tile(slot)
            try:
                self.gallery_page_var.set("")
                self.gallery_prev_btn.configure(state="disabled")
                self.gallery_next_btn.configure(state="disabled")
            except tk.TclError:
                pass
            return
        shots = group.get("shots") or []
        pages = max(1, (len(shots) + self._gallery_page_size - 1)
                    // self._gallery_page_size)
        self._gallery_page = max(0, min(self._gallery_page, pages - 1))
        start = self._gallery_page * self._gallery_page_size
        visible = shots[start:start + self._gallery_page_size]
        self._gallery_visible = visible
        for slot in range(self._gallery_page_size):
            if slot < len(visible):
                self._gallery_set_tile(slot, visible[slot])
            else:
                self._gallery_clear_tile(slot)
        subtitle = group.get("subtitle") or ""
        keys = [item["key"] for item in self._gallery_groups]
        try:
            if group["key"] in keys:
                self.gallery_group_combo.current(keys.index(group["key"]))
            self.gallery_group_meta_var.set(subtitle)
            self.gallery_page_var.set(
                f"{self._gallery_page + 1} / {pages} · {len(shots)} снимк.")
            self.gallery_prev_btn.configure(
                state="normal" if self._gallery_page > 0 else "disabled")
            self.gallery_next_btn.configure(
                state="normal" if self._gallery_page + 1 < pages
                else "disabled")
        except tk.TclError:
            return
        self._gallery_request_thumbs(visible)
        self._schedule_page_layout("gallery")

    def _gallery_set_tile(self, slot, shot):
        if slot >= len(self._gallery_tiles):
            return
        tile = self._gallery_tiles[slot]
        tile["shot"] = shot
        photo = self._gallery_photo(shot)
        canvas = tile["canvas"]
        try:
            canvas.itemconfigure(
                tile["image"], image=photo if photo is not None else "")
            canvas.itemconfigure(
                tile["text"],
                text="" if photo is not None else
                ("…" if shot["name"] in self._gallery_thumb_jobs
                 else "нет превью"))
            tile["caption"].configure(
                text=trunc(shot.get("time_label") or shot.get("name"), 30))
            tile["outer"].grid(
                row=slot // 3, column=slot % 3, sticky="w",
                padx=5, pady=5)
        except tk.TclError:
            pass

    def _gallery_clear_tile(self, slot):
        if slot >= len(self._gallery_tiles):
            return
        tile = self._gallery_tiles[slot]
        tile["shot"] = None
        try:
            tile["outer"].grid_remove()
            tile["canvas"].itemconfigure(tile["image"], image="")
            tile["canvas"].itemconfigure(tile["text"], text="")
            tile["caption"].configure(text="")
        except tk.TclError:
            pass

    def _gallery_tile_click(self, slot):
        self._gallery_open_viewer(slot)

    def _gallery_thumb_fresh(self, shot):
        target = gallery_thumb_path(shot)
        try:
            if not target.is_file():
                return False
            return target.stat().st_mtime >= Path(shot["path"]).stat().st_mtime
        except OSError:
            return False

    def _gallery_photo(self, shot):
        target = gallery_thumb_path(shot)
        key = f"{target}|{GALLERY_TILE_W}x{GALLERY_TILE_H}"
        photo = self._gallery_photo_cache.get(key)
        if photo is not None:
            return photo
        if not HAVE_PIL or not target.is_file():
            return None
        try:
            with Image.open(target) as raw:
                raw.load()
                image = ImageOps.fit(
                    raw.convert("RGB"),
                    (GALLERY_TILE_W, GALLERY_TILE_H),
                    method=getattr(getattr(Image, "Resampling", Image),
                                   "LANCZOS"))
            photo = ImageTk.PhotoImage(image)
        except Exception:  # noqa: BLE001 — битый кэш не должен ломать UI
            return None
        self._gallery_photo_cache[key] = photo
        while len(self._gallery_photo_cache) > 36:
            self._gallery_photo_cache.pop(
                next(iter(self._gallery_photo_cache)))
        return photo

    def _gallery_request_thumbs(self, shots):
        if not HAVE_PIL or not shots:
            return
        missing = []
        for shot in shots:
            if self._gallery_thumb_fresh(shot):
                continue
            if shot["name"] in self._gallery_thumb_jobs:
                continue
            missing.append(shot)
        if not missing:
            return
        names = {shot["name"] for shot in missing}
        self._gallery_thumb_jobs |= names
        token = self._gallery_refresh_token

        def work():
            made = set()
            for shot in missing:
                try:
                    if self._gallery_make_thumb(shot):
                        made.add(shot["name"])
                except Exception:  # noqa: BLE001
                    continue
            self.q.put(
                lambda t=token, n=made: self._gallery_thumbs_ready(t, n))

        threading.Thread(target=work, daemon=True,
                         name="gallery-thumbs").start()

    @staticmethod
    def _gallery_make_thumb(shot):
        if not HAVE_PIL:
            return False
        source = Path(shot["path"])
        target = gallery_thumb_path(shot)
        try:
            if (target.is_file()
                    and target.stat().st_mtime >= source.stat().st_mtime):
                return True
            target.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as raw:
                raw.load()
                image = raw.convert("RGB")
                image = ImageOps.fit(
                    image, (400, 225),
                    method=getattr(getattr(Image, "Resampling", Image),
                                   "LANCZOS"))
            temp = target.with_name(target.name + ".tmp")
            image.save(temp, "JPEG", quality=84, optimize=True)
            temp.replace(target)
            return True
        except Exception:  # noqa: BLE001
            try:
                target.with_name(target.name + ".tmp").unlink()
            except OSError:
                pass
            return False

    def _gallery_thumbs_ready(self, token, names):
        self._gallery_thumb_jobs -= set(names or ())
        if token != self._gallery_refresh_token or self._closing:
            return
        visible = {shot["name"] for shot in self._gallery_visible}
        if visible & set(names or ()):
            self._gallery_render()

    def _gallery_prev(self):
        if self._gallery_page <= 0:
            return
        self._gallery_page -= 1
        self._gallery_render()

    def _gallery_next(self):
        group = self._gallery_selected_group()
        if group is None:
            return
        pages = max(1, (len(group["shots"]) + self._gallery_page_size - 1)
                    // self._gallery_page_size)
        if self._gallery_page + 1 >= pages:
            return
        self._gallery_page += 1
        self._gallery_render()

    # -- просмотрщик снимков -----------------------------------------------------

    def _gallery_open_viewer(self, slot):
        group = self._gallery_selected_group()
        if group is None:
            return
        index = self._gallery_page * self._gallery_page_size + slot
        if not 0 <= index < len(group["shots"]):
            return
        self._gallery_viewer_group = group
        self._gallery_viewer_index = index
        if (self._gallery_viewer is None
                or not self._gallery_viewer["win"].winfo_exists()):
            self._gallery_build_viewer()
        self._gallery_viewer_show(index)

    def _gallery_build_viewer(self):
        c = self.c
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title("MaklyxaLauncher — просмотр снимка")
        win.configure(bg=c["panel"])
        win.transient(self.root)
        self._gallery_viewer_title_var = tk.StringVar(value="")
        self._gallery_viewer_caption_var = tk.StringVar(value="")
        self._gallery_viewer_counter_var = tk.StringVar(value="")

        top = tk.Frame(win, bg=c["panel"], padx=16, pady=10)
        top.pack(fill="x")
        tk.Label(top, textvariable=self._gallery_viewer_title_var,
                 bg=c["panel"], fg=c["text"], font=(UI_FONT, 11, "bold"),
                 anchor="w").pack(side="left", fill="x", expand=True)
        close_btn = tk.Button(
            top, text="✕", command=self._gallery_viewer_close,
            relief="flat", bd=0, cursor="hand2", bg=c["surface2"],
            fg=c["text"], activebackground=c["hover"],
            activeforeground=c["text"], font=(UI_FONT, 10, "bold"),
            padx=10, pady=3, highlightthickness=0)
        close_btn.pack(side="right")

        canvas = tk.Canvas(win, bg=c["list"], highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        bottom = tk.Frame(win, bg=c["panel"], padx=16, pady=10)
        bottom.pack(fill="x")
        self._button(bottom, "◀  Назад",
                     lambda: self._gallery_viewer_step(-1), side="left")
        self._button(bottom, "Вперёд  ▶",
                     lambda: self._gallery_viewer_step(1), side="left",
                     padx=8)
        tk.Label(bottom, textvariable=self._gallery_viewer_counter_var,
                 bg=c["panel"], fg=c["muted"], font=(UI_FONT, 9)).pack(
                     side="left", padx=10)
        self._button(bottom, "Назначить плейс…",
                     self._gallery_viewer_assign, side="right")
        self._button(bottom, "Папка", self._gallery_viewer_open_folder,
                     side="right", padx=8)
        self._button(bottom, "Открыть файл",
                     self._gallery_viewer_open_file, side="right")

        caption = tk.Label(win, textvariable=self._gallery_viewer_caption_var,
                           bg=c["panel"], fg=c["muted"], font=(UI_FONT, 8),
                           anchor="w", padx=16, pady=4)
        caption.pack(fill="x")

        viewer = {
            "win": win, "canvas": canvas, "master": None, "photo": None,
            "render_job": None,
        }
        self._gallery_viewer = viewer
        canvas.bind("<Configure>", self._gallery_viewer_resize)
        win.bind("<Escape>", lambda _e: self._gallery_viewer_close())
        win.bind("<Left>", lambda _e: self._gallery_viewer_step(-1))
        win.bind("<Right>", lambda _e: self._gallery_viewer_step(1))
        win.protocol("WM_DELETE_WINDOW", self._gallery_viewer_close)

        width = min(1240, max(760, int(win.winfo_screenwidth() * 0.82)))
        height = min(840, max(520, int(win.winfo_screenheight() * 0.82)))
        x = (win.winfo_screenwidth() - width) // 2
        y = max(0, (win.winfo_screenheight() - height) // 2 - 20)
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.deiconify()
        win.lift()
        try:
            win.focus_force()
        except tk.TclError:
            pass

    def _gallery_viewer_shot(self):
        group = self._gallery_viewer_group
        if group is None:
            return None
        shots = group.get("shots") or []
        if not shots:
            return None
        self._gallery_viewer_index %= len(shots)
        return shots[self._gallery_viewer_index]

    def _gallery_viewer_show(self, index):
        viewer = self._gallery_viewer
        group = self._gallery_viewer_group
        if viewer is None or group is None:
            return
        shots = group.get("shots") or []
        if not shots:
            return
        self._gallery_viewer_index = index % len(shots)
        shot = shots[self._gallery_viewer_index]
        master = None
        if HAVE_PIL:
            try:
                with Image.open(shot["path"]) as raw:
                    raw.load()
                    master = raw.convert("RGB")
            except Exception:  # noqa: BLE001
                master = None
        viewer["master"] = master
        try:
            self._gallery_viewer_title_var.set(group["title"])
            self._gallery_viewer_caption_var.set(
                f"{shot.get('time_label') or ''} · {shot['name']}")
            self._gallery_viewer_counter_var.set(
                f"{self._gallery_viewer_index + 1} из {len(shots)}")
        except tk.TclError:
            return
        self._gallery_viewer_render()

    def _gallery_viewer_resize(self, _event=None):
        viewer = self._gallery_viewer
        if viewer is None:
            return
        if viewer.get("render_job") is not None:
            try:
                viewer["win"].after_cancel(viewer["render_job"])
            except tk.TclError:
                pass
        try:
            viewer["render_job"] = viewer["win"].after(
                60, self._gallery_viewer_render)
        except tk.TclError:
            viewer["render_job"] = None

    def _gallery_viewer_render(self):
        viewer = self._gallery_viewer
        if viewer is None or not viewer["win"].winfo_exists():
            return
        viewer["render_job"] = None
        canvas = viewer["canvas"]
        try:
            width = max(80, canvas.winfo_width() - 18)
            height = max(80, canvas.winfo_height() - 18)
        except tk.TclError:
            return
        master = viewer.get("master")
        canvas.delete("all")
        viewer["photo"] = None
        if master is None:
            canvas.create_text(
                width // 2, height // 2,
                text="Не удалось открыть изображение.\n"
                     "Используй «Открыть файл».",
                fill=self.c["muted"], font=(UI_FONT, 11),
                justify="center")
            return
        try:
            image = master.copy()
            image.thumbnail(
                (width, height),
                getattr(getattr(Image, "Resampling", Image), "LANCZOS"))
            photo = ImageTk.PhotoImage(image)
        except Exception:  # noqa: BLE001
            return
        viewer["photo"] = photo
        canvas.create_image(width // 2 + 9, height // 2 + 9,
                            image=photo, anchor="center")

    def _gallery_viewer_step(self, step):
        group = self._gallery_viewer_group
        if group is None or not group.get("shots"):
            return
        index = (self._gallery_viewer_index + step) % len(group["shots"])
        self._gallery_viewer_show(index)

    def _gallery_viewer_close(self):
        viewer = self._gallery_viewer
        self._gallery_viewer = None
        self._gallery_viewer_group = None
        if viewer is None:
            return
        win = viewer.get("win")
        if win is None:
            return
        try:
            if viewer.get("render_job") is not None:
                win.after_cancel(viewer["render_job"])
        except tk.TclError:
            pass
        try:
            win.destroy()
        except tk.TclError:
            pass

    def _gallery_viewer_assign(self):
        shot = self._gallery_viewer_shot()
        if shot is None:
            return
        self._gallery_viewer_close()
        self._open_gallery_assign_dialog(
            names=[shot["name"]],
            label=shot.get("time_label") or shot["name"])

    def _gallery_viewer_open_file(self):
        shot = self._gallery_viewer_shot()
        if shot is None or not self.interactive:
            return
        try:
            os.startfile(shot["path"])
        except OSError as exc:
            self.status_var.set(f"Не удалось открыть файл: {exc}")

    def _gallery_viewer_open_folder(self):
        self.open_screenshots_folder()

    # -- назначение плейса и очистка фона -----------------------------------------

    def _open_gallery_assign_dialog(self, names=None, label=None):
        win = getattr(self, "_gallery_assign_dialog", None)
        if win is not None:
            try:
                win.lift()
                win.focus_force()
                return
            except tk.TclError:
                self._gallery_assign_dialog = None
        group = None
        if names is None:
            group = self._gallery_selected_group()
            if group is None:
                self.status_var.set("Сначала выбери группу снимков")
                return
            names = [shot["name"] for shot in group["shots"]]
            label = label or group["title"]
        names = [str(name) for name in names or [] if name]
        if not names:
            self.status_var.set("В этой группе нет снимков")
            return
        c = self.c
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title("Назначить плейс")
        win.configure(bg=c["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        self._gallery_assign_dialog = win
        self._gallery_assign_names = names

        body = tk.Frame(win, bg=c["bg"], padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Назначить плейс", bg=c["bg"], fg=c["text"],
                 font=(UI_FONT, 16, "bold"), anchor="w").pack(fill="x")
        tk.Label(
            body,
            text=f"Снимков: {len(names)} · {label or ''}\n"
                 "Вставь ссылку на игру Roblox или Place ID. Название "
                 "подтянется из публичного API и сохранится только в "
                 "кэше лаунчера.",
            bg=c["bg"], fg=c["muted"], font=(UI_FONT, 9), anchor="w",
            justify="left", wraplength=460).pack(fill="x", pady=(5, 14))
        self._gallery_assign_var = tk.StringVar(value="")
        entry = ttk.Entry(body, textvariable=self._gallery_assign_var,
                          font=(UI_FONT, 10))
        entry.pack(fill="x")
        entry.bind("<Return>", lambda _e: self._gallery_assign_submit())
        self._gallery_assign_status_var = tk.StringVar(value="")
        tk.Label(body, textvariable=self._gallery_assign_status_var,
                 bg=c["bg"], fg=self._warn_fg(), font=(UI_FONT, 9),
                 anchor="w", justify="left", wraplength=460).pack(
                     fill="x", pady=(8, 12))
        row = tk.Frame(body, bg=c["bg"])
        row.pack(fill="x")
        self._button(row, "Назначить", self._gallery_assign_submit,
                     kind="primary", side="right")
        self._button(row, "Отмена", self._close_gallery_assign_dialog,
                     side="right", padx=8)
        win.protocol("WM_DELETE_WINDOW", self._close_gallery_assign_dialog)
        win.update_idletasks()
        width = max(480, win.winfo_reqwidth())
        height = win.winfo_reqheight()
        try:
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            rw, rh = self.root.winfo_width(), self.root.winfo_height()
        except tk.TclError:
            rx = ry = rw = rh = 0
        x = rx + max(0, (rw - width) // 2) if rw > 1 else 140
        y = ry + max(0, (rh - height) // 2) if rh > 1 else 140
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.deiconify()
        try:
            win.grab_set()
            entry.focus_set()
        except tk.TclError:
            pass

    def _close_gallery_assign_dialog(self):
        win = getattr(self, "_gallery_assign_dialog", None)
        self._gallery_assign_dialog = None
        if win is None:
            return
        try:
            win.grab_release()
        except tk.TclError:
            pass
        try:
            win.destroy()
        except tk.TclError:
            pass

    def _gallery_assign_submit(self):
        raw = self._gallery_assign_var.get().strip()
        place_id = parse_place_id(raw)
        if not place_id:
            self._gallery_assign_status_var.set(
                "Не понял ссылку или Place ID — нужен номер игры Roblox")
            return
        cache = self._gallery_cache
        if not isinstance(cache, dict):
            cache = gallery_cache_load()
        names = list(self._gallery_assign_names or [])
        count = gallery_assign(cache, names, place_id)
        if not count or not gallery_cache_save(cache):
            self._gallery_assign_status_var.set(
                "Не удалось сохранить назначение в Gallery\\cache.json")
            return
        self._gallery_cache = cache
        self._gallery_pending_key = f"place:{place_id}"
        self._close_gallery_assign_dialog()
        self.status_var.set(
            f"Снимков привязано к Place {place_id}: {count}")
        self.log(f"Галерея: {count} снимк. → Place {place_id}")
        self.refresh_gallery()

    def open_screenshots_folder(self):
        if not self.interactive:
            return
        folder = screenshots_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))
        except OSError as exc:
            self.status_var.set(f"Не удалось открыть папку: {exc}")

    def open_clean_background_dialog(self):
        win = getattr(self, "_gallery_clean_dialog", None)
        if win is not None:
            try:
                win.lift()
                win.focus_force()
                return
            except tk.TclError:
                self._gallery_clean_dialog = None
        c = self.c
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title("Очистить задний план")
        win.configure(bg=c["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        self._gallery_clean_dialog = win

        body = tk.Frame(win, bg=c["bg"], padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Очистить задний план", bg=c["bg"],
                 fg=c["text"], font=(UI_FONT, 16, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(
            body,
            text="Уже сохранённый PNG содержит интерфейс как часть "
                 "картинки: отдельных слоёв в нём нет, поэтому убрать UI "
                 "задним числом нельзя.",
            bg=c["bg"], fg=c["text"], font=(UI_FONT, 10), anchor="w",
            justify="left", wraplength=500).pack(fill="x", pady=(8, 12))
        tk.Label(
            body,
            text="Почему не через FastFlags\n"
                 "Флаг DFIntCanHideGuiGroupId исключён Roblox из локального "
                 "allowlist клиента, а сейчас клиент отклоняет и обычные "
                 "локальные флаги. Запись такого флага ничего бы не "
                 "изменила, поэтому лаунчер её не делает.",
            bg=c["surface"], fg=c["muted"], font=(UI_FONT, 9), anchor="w",
            justify="left", wraplength=500, padx=14, pady=12).pack(
                fill="x", pady=(0, 12))
        tk.Label(
            body,
            text="Как получить чистый кадр\n"
                 "• Делай новые снимки через официальные «Записи» "
                 "(Captures) Roblox — клиент сам скрывает интерфейс.\n"
                 "• Если в игре есть фоторежим, используй его.\n"
                 "• Новые файлы из Pictures\\Roblox появятся здесь после "
                 "«Обновить».",
            bg=c["bg"], fg=c["text"], font=(UI_FONT, 9), anchor="w",
            justify="left", wraplength=500).pack(fill="x")
        row = tk.Frame(body, bg=c["bg"])
        row.pack(fill="x", pady=(16, 0))
        self._button(row, "Открыть папку снимков",
                     self.open_screenshots_folder, kind="primary",
                     side="left")
        self._button(row, "Обновить галерею", self._clean_dialog_refresh,
                     side="left", padx=8)
        self._button(row, "Понятно", self._close_clean_background_dialog,
                     side="right")
        win.protocol("WM_DELETE_WINDOW", self._close_clean_background_dialog)
        win.update_idletasks()
        width = max(540, win.winfo_reqwidth())
        height = win.winfo_reqheight()
        try:
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            rw, rh = self.root.winfo_width(), self.root.winfo_height()
        except tk.TclError:
            rx = ry = rw = rh = 0
        x = rx + max(0, (rw - width) // 2) if rw > 1 else 120
        y = ry + max(0, (rh - height) // 2) if rh > 1 else 120
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.deiconify()
        try:
            win.grab_set()
        except tk.TclError:
            pass

    def _close_clean_background_dialog(self):
        win = getattr(self, "_gallery_clean_dialog", None)
        self._gallery_clean_dialog = None
        if win is None:
            return
        try:
            win.grab_release()
        except tk.TclError:
            pass
        try:
            win.destroy()
        except tk.TclError:
            pass

    def _clean_dialog_refresh(self):
        self._close_clean_background_dialog()
        self.refresh_gallery()

    # -- страница «В игре» ---------------------------------------------------------

    def _build_page_ingame(self):
        c = self.c
        pad = self._scroll_page("ingame")

        self._page_header(
            pad, "◳", "В игре",
            "Оверлей, островок с музыкой и отдельные часы поверх Roblox "
            "по своим горячим клавишам, плюс AFK-режим. Помощник — "
            "отдельный процесс: не внедряется в клиент и не меняет его "
            "файлы.")

        self.var_ingame_overlay = tk.BooleanVar(
            value=bool(self.cfg.get("ingame_overlay")))
        self.var_ingame_island = tk.BooleanVar(
            value=bool(self.cfg.get("ingame_island")))
        self.var_ingame_start = tk.BooleanVar(
            value=bool(self.cfg.get("ingame_overlay_start")))
        self.var_ingame_clock = tk.BooleanVar(
            value=bool(self.cfg.get("ingame_clock")))
        self.var_ingame_auto = tk.BooleanVar(
            value=bool(self.cfg.get("ingame_auto")))
        self.var_ingame_afk = tk.BooleanVar(
            value=bool(self.cfg.get("ingame_afk")))
        self.hotkey_button_var = tk.StringVar(
            value=ingame_mod.hotkey_label(self.cfg.get("ingame_hotkey")))
        self.clock_hotkey_button_var = tk.StringVar(
            value=ingame_mod.hotkey_label(
                self.cfg.get("ingame_clock_hotkey")))
        self._hotkey_capture_id = None
        self._hotkey_capture_target = None
        mode_key = self.cfg.get("ingame_afk_mode") or "mouse"
        self.var_ingame_mode = tk.StringVar(
            value=ingame_mod.AFK_MODE_LABELS.get(mode_key,
                                                 ingame_mod.AFK_MODE_LABELS["mouse"]))
        key_key = self.cfg.get("ingame_afk_key") or "space"
        self.var_ingame_key = tk.StringVar(
            value=ingame_mod.AFK_KEY_LABELS.get(key_key,
                                                ingame_mod.AFK_KEY_LABELS["space"]))
        self.var_ingame_minutes = tk.StringVar(
            value=f"{int(self.cfg.get('ingame_afk_minutes') or 5)} мин")

        self.btn_ingame_overlay = None
        self.btn_ingame_start = None
        self.btn_ingame_stop = None

        # -- оверлей
        inner = self._card(
            pad, title="Оверлей",
            subtitle="Неактивируемое окно поверх Roblox: клики проходят "
                     "сквозь него в игру",
            glyph="◳")
        self._checkbox(
            inner, "Показывать оверлей поверх Roblox",
            self.var_ingame_overlay,
            command=self._ingame_feature_toggle).pack(
                anchor="w", pady=(6, 0))
        self._checkbox(
            inner, "Показывать сразу при входе в игру",
            self.var_ingame_start, command=self._save_ingame_cfg).pack(
                anchor="w", pady=(5, 0))

        preview_row = tk.Frame(inner, bg=c["surface"])
        preview_row.pack(fill="x", pady=(12, 0))
        preview = tk.Frame(preview_row, bg="#0b0f14",
                           highlightthickness=1,
                           highlightbackground="#243040")
        preview.pack(side="right", anchor="n")
        tk.Frame(preview, bg=self.accent, width=4).pack(
            side="left", fill="y")
        preview_box = tk.Frame(preview, bg="#0b0f14")
        preview_box.pack(side="left", padx=(10, 13), pady=(9, 8))
        head = tk.Frame(preview_box, bg="#0b0f14")
        head.pack(fill="x")
        tk.Label(head, text="MAKLYXA", bg="#0b0f14", fg=self.accent,
                 font=(UI_FONT, 8, "bold")).pack(side="left")
        tk.Label(head, text="  HUD", bg="#0b0f14", fg="#8fa3b8",
                 font=(UI_FONT, 8, "bold")).pack(side="left")
        tk.Label(head, text="21:40:05", bg="#0b0f14", fg="#e7eef7",
                 font=(UI_FONT, 14, "bold")).pack(side="right")
        tk.Frame(preview_box, bg="#243040", height=1).pack(
            fill="x", pady=(6, 8))
        metrics = tk.Frame(preview_box, bg="#0b0f14")
        metrics.pack(fill="x")
        for column in range(2):
            metrics.grid_columnconfigure(column, weight=1, uniform="prev")
        for index, (title, value, sub) in enumerate((
                ("FPS", "60", "кадры игры"),
                ("GPU", "52°C", "52% · 1.2 ГБ"),
                ("ПАМЯТЬ", "43%", "13.7 / 31.9 ГБ"),
                ("AFK", "4:59", "до действия"))):
            cell = tk.Frame(metrics, bg="#0b0f14")
            cell.grid(row=index // 2, column=index % 2, sticky="w",
                      padx=(0, 16), pady=(0, 6))
            tk.Label(cell, text=title, bg="#0b0f14", fg="#8fa3b8",
                     font=(UI_FONT, 7, "bold"), anchor="w").pack(fill="x")
            tk.Label(cell, text=value, bg="#0b0f14", fg=self.accent,
                     font=(UI_FONT, 13, "bold"), anchor="w").pack(fill="x")
            tk.Label(cell, text=sub, bg="#0b0f14", fg="#8fa3b8",
                     font=(UI_FONT, 7), anchor="w").pack(fill="x")
        tk.Frame(preview_box, bg="#243040", height=1).pack(
            fill="x", pady=(2, 0))
        foot = tk.Frame(preview_box, bg="#0b0f14")
        foot.pack(fill="x", pady=(5, 0))
        tk.Label(foot,
                 text=f"{ingame_mod.hotkey_label(self.cfg.get('ingame_hotkey'))} скрыть",
                 bg="#0b0f14", fg="#8fa3b8",
                 font=(UI_FONT, 7)).pack(side="left")
        tk.Label(foot, text="● LIVE", bg="#0b0f14", fg=self.accent,
                 font=(UI_FONT, 7, "bold")).pack(side="right")
        tk.Label(preview_box, text="пример оформления", bg="#0b0f14",
                 fg="#5d7186", font=(UI_FONT, 7)).pack(anchor="e",
                                                      pady=(4, 0))

        note = tk.Frame(preview_row, bg=c["surface"])
        note.pack(side="left", fill="both", expand=True)
        tk.Label(note, text="ТАК ВЫГЛЯДИТ ОВЕРЛЕЙ", bg=c["surface"],
                 fg=c["muted"], font=(UI_FONT, 8, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(
            note,
            text="Сетка 2×2: FPS, GPU, память и AFK-таймер, сверху крупные "
                 "часы с акцентной полосой. FPS настоящий: помощник читает "
                 "счётчик кадров Windows (WDDM) для процесса Roblox — без "
                 "внедрения, без чтения памяти игры. Значение обновляется "
                 "раз в полсекунды; «—» появится, только если драйвер не "
                 "отдаёт статистику.\n"
                 "Температура GPU — NVIDIA через NVML; RAM, время и "
                 "AFK-таймер тоже настоящие.",
            bg=c["surface"], fg=c["text"], font=(UI_FONT, 9),
            anchor="w", justify="left", wraplength=400).pack(
                fill="x", pady=(6, 0))

        row_pos = tk.Frame(inner, bg=c["surface"])
        row_pos.pack(fill="x", pady=(12, 0))
        tk.Label(row_pos, text="Угол оверлея", bg=c["surface"],
                 fg=c["text"], font=(UI_FONT, 10)).pack(side="left")
        self.combo_ingame_corner = ttk.Combobox(
            row_pos, values=list(ingame_mod.CORNER_LABELS.values()),
            state="readonly", width=18)
        self.combo_ingame_corner.set(ingame_mod.CORNER_LABELS.get(
            self.cfg.get("ingame_overlay_corner") or "tr",
            ingame_mod.CORNER_LABELS["tr"]))
        self.combo_ingame_corner.bind("<<ComboboxSelected>>",
                                      self._save_ingame_cfg)
        self.combo_ingame_corner.pack(side="left", padx=(12, 0))

        row_bind = tk.Frame(inner, bg=c["surface"])
        row_bind.pack(fill="x", pady=(10, 0))
        tk.Label(row_bind, text="Горячая клавиша", bg=c["surface"],
                 fg=c["text"], font=(UI_FONT, 10)).pack(side="left")
        self.btn_ingame_hotkey = self._button(
            row_bind, "", self._ingame_hotkey_capture, side="left", padx=12,
            textvariable=self.hotkey_button_var)
        self._button(row_bind, "Сброс", self._ingame_hotkey_reset,
                     side="left")
        self._hint(
            inner,
            "Нажми «Горячая клавиша», затем комбинацию с Ctrl, Alt, Shift "
            "или Win — например Ctrl+Shift+H. Esc отменяет запись. "
            "Комбинация действует глобально, пока работает помощник: "
            "если её занял другой программой, помощник сообщит об этом.",
            wrap=560)

        row_overlay = tk.Frame(inner, bg=c["surface"])
        row_overlay.pack(fill="x", pady=(10, 0))
        self.btn_ingame_overlay = self._button(
            row_overlay, "Показать оверлей", self._ingame_toggle_overlay,
            kind="primary", side="left")
        self._hint(
            inner,
            "Оверлей виден только когда окно Roblox активно. Помощник "
            "запускается автоматически, как только Roblox открывается; "
            "своя горячая клавиша переключает оверлей глобально. Если "
            "комбинацию занял другой программой — пользуйся кнопкой.",
            wrap=560)

        # -- островок и часы
        island = self._card(
            pad, title="Островок и часы",
            subtitle="Пилюля сверху по центру игры: время, а если играет "
                     "музыка — название трека и обложка. Часы можно "
                     "показывать отдельно и со своим биндом",
            glyph="◒")
        self._checkbox(
            island, "Показывать островок (вместе с оверлеем)",
            self.var_ingame_island,
            command=self._ingame_feature_toggle).pack(
                anchor="w", pady=(6, 0))
        self._checkbox(
            island, "Отдельные часы поверх Roblox",
            self.var_ingame_clock,
            command=self._ingame_feature_toggle).pack(
                anchor="w", pady=(5, 0))

        row_clock = tk.Frame(island, bg=c["surface"])
        row_clock.pack(fill="x", pady=(10, 0))
        tk.Label(row_clock, text="Бинд часов", bg=c["surface"],
                 fg=c["text"], font=(UI_FONT, 10)).pack(side="left")
        self.btn_ingame_clock_hotkey = self._button(
            row_clock, "", lambda: self._ingame_hotkey_capture("clock"),
            side="left", padx=12,
            textvariable=self.clock_hotkey_button_var)
        self._button(row_clock, "Сброс",
                     lambda: self._ingame_hotkey_reset("clock"),
                     side="left")

        preview = tk.Frame(island, bg=c["surface2"], padx=12, pady=12)
        preview.pack(fill="x", pady=(10, 0))
        pill = tk.Frame(preview, bg="#0e131b", padx=14, pady=7,
                        highlightthickness=1, highlightbackground="#2c3a4c")
        pill.pack(anchor="center")
        tk.Label(pill, text="21:40", bg="#0e131b", fg="#eef4fb",
                 font=(UI_FONT, 12, "bold")).pack(side="left")
        tk.Frame(pill, bg="#2c3a4c", width=1, height=24).pack(
            side="left", fill="y", padx=12)
        art = tk.Frame(pill, bg="#182231", width=26, height=26,
                       highlightthickness=1, highlightbackground="#2c3a4c")
        art.pack(side="left")
        art.pack_propagate(False)
        tk.Label(art, text="♪", bg="#182231", fg="#93a6ba",
                 font=(UI_FONT, 9)).pack(expand=True)
        track = tk.Frame(pill, bg="#0e131b")
        track.pack(side="left", padx=(10, 0))
        tk.Label(track, text="Название трека", bg="#0e131b",
                 fg="#eef4fb", font=(UI_FONT, 9, "bold")).pack(anchor="w")
        tk.Label(track, text="Исполнитель", bg="#0e131b", fg="#93a6ba",
                 font=(UI_FONT, 7)).pack(anchor="w")
        tk.Label(preview, text="пример: музыка не играет — остаются часы",
                 bg=c["surface2"], fg=c["muted"],
                 font=(UI_FONT, 8)).pack(pady=(8, 0))
        self._hint(
            island,
            "Трек берётся из системной медиасессии Windows — Spotify, "
            "браузер, проигрыватель. Помощник не подключается к аккаунтам "
            "и не читает файлы музыки: только название, исполнитель и "
            "обложку, которые Windows и так показывает в оверлее громкости. "
            "Трек показывается автоматически, как только музыка играет — "
            "даже если галочка островка снята: без галочки островок "
            "появляется только на время трека и прячется на паузе. "
            "«Отдельные часы» — своя пилюля с секундами: включаются сразу "
            "и переключаются только своим биндом, не трогая оверлей "
            "и островок.",
            wrap=560)

        # -- AFK
        afk = self._card(
            pad, title="AFK-режим",
            subtitle="Микро-действие только когда Roblox в фокусе и ты "
                     "не трогал мышь и клавиатуру",
            glyph="◷")
        self._checkbox(
            afk, "Не давать серверу кикнуть за бездействие",
            self.var_ingame_afk,
            command=self._ingame_feature_toggle).pack(
                anchor="w", pady=(6, 8))
        grid = tk.Frame(afk, bg=c["surface"])
        grid.pack(fill="x")
        grid.grid_columnconfigure(1, weight=1)

        def _row(row, label, widget):
            tk.Label(grid, text=label, bg=c["surface"], fg=c["text"],
                     font=(UI_FONT, 10)).grid(
                         row=row, column=0, sticky="w", pady=4,
                         padx=(0, 14))
            widget.grid(row=row, column=1, sticky="w", pady=4)

        self.combo_ingame_mode = ttk.Combobox(
            grid, values=list(ingame_mod.AFK_MODE_LABELS.values()),
            state="readonly", width=28)
        self.combo_ingame_mode.set(self.var_ingame_mode.get())
        self.combo_ingame_mode.bind("<<ComboboxSelected>>",
                                    self._save_ingame_cfg)
        _row(0, "Действие", self.combo_ingame_mode)

        self.combo_ingame_key = ttk.Combobox(
            grid, values=[label for _key, label, _vk in ingame_mod.AFK_KEYS],
            state="readonly", width=14)
        self.combo_ingame_key.set(self.var_ingame_key.get())
        self.combo_ingame_key.bind("<<ComboboxSelected>>",
                                   self._save_ingame_cfg)
        _row(1, "Клавиша", self.combo_ingame_key)

        self.combo_ingame_minutes = ttk.Combobox(
            grid, values=[f"{n} мин" for n in range(1, 16)],
            state="readonly", width=12)
        self.combo_ingame_minutes.set(self.var_ingame_minutes.get())
        self.combo_ingame_minutes.bind("<<ComboboxSelected>>",
                                       self._save_ingame_cfg)
        _row(2, "Интервал", self.combo_ingame_minutes)

        tk.Label(afk, textvariable=self.ingame_afk_state_var,
                 bg=c["surface"], fg=self.accent,
                 font=(UI_FONT, 10, "bold"), anchor="w").pack(
                     fill="x", pady=(9, 0))
        self._hint(
            afk,
            "Действие отправляется системным вводом Windows (SendInput) — "
            "без DLL, без чтения памяти и без изменения файлов Roblox. "
            "Режим молчит, пока окно игры не в фокусе, и не срабатывает, "
            "если ты только что пользовался компьютером (порог 60 секунд). "
            "На некоторых играх правило 20 минут строже — интервал можно "
            "уменьшить.", wrap=560)

        # -- помощник
        box = self._card(
            pad, title="Помощник",
            subtitle="Отдельный процесс, который живёт и после закрытия "
                     "лаунчера",
            glyph="⚙")
        self._checkbox(
            box, "Запускать помощник автоматически",
            self.var_ingame_auto, command=self._save_ingame_cfg).pack(
                anchor="w", pady=(6, 0))

        state_grid = tk.Frame(box, bg=c["surface2"], padx=12, pady=10)
        state_grid.pack(fill="x", pady=(10, 0))
        state_grid.grid_columnconfigure(0, weight=1)
        state_grid.grid_columnconfigure(1, weight=1)
        for index, var in enumerate((self.ingame_helper_var,
                                     self.ingame_hotkey_var,
                                     self.ingame_roblox_var,
                                     self.ingame_overlay_state_var,
                                     self.ingame_fps_var,
                                     self.ingame_clock_state_var)):
            tk.Label(state_grid, textvariable=var, bg=c["surface2"],
                     fg=c["text"], font=(UI_FONT, 9), anchor="w",
                     justify="left", wraplength=300).grid(
                         row=index // 2, column=index % 2, sticky="w",
                         padx=(0, 12), pady=2)
        tk.Label(state_grid, textvariable=self.ingame_message_var,
                 bg=c["surface2"], fg=self.c["muted"],
                 font=(UI_FONT, 9), anchor="w", justify="left",
                 wraplength=600).grid(row=3, column=0, columnspan=2,
                                      sticky="w", pady=(4, 0))

        row_btns = tk.Frame(box, bg=c["surface"])
        row_btns.pack(fill="x", pady=(10, 0))
        self.btn_ingame_start = self._button(
            row_btns, "Запустить помощник", self._ingame_start_now,
            kind="primary", side="left")
        self.btn_ingame_stop = self._button(
            row_btns, "Остановить", self._ingame_stop, side="left", padx=8)

        row_files = tk.Frame(box, bg=c["surface"])
        row_files.pack(fill="x", pady=(9, 0))
        self._button(row_files, "Открыть папку", self._open_ingame_dir,
                     side="left")
        self._button(row_files, "Журнал", self._open_ingame_log,
                     side="left", padx=8)
        tk.Label(box, text=str(ingame_mod.status_path().parent),
                 bg=c["surface"], fg=c["muted"], font=(UI_FONT, 8),
                 anchor="w", justify="left", wraplength=560).pack(
                     fill="x", pady=(7, 0))
        self._hint(
            box,
            "Помощник завершится сам примерно через 90 секунд после "
            "закрытия Roblox. Свёрнутая игра его не останавливает, а "
            "закрытие лаунчера — тоже; остановить можно кнопкой "
            "«Остановить».", wrap=560)

        # -- безопасность
        safe = self._card(pad, title="Безопасность", glyph="⛨")
        tk.Label(
            safe,
            bg=c["surface"], fg=c["muted"], font=(UI_FONT, 9),
            justify="left", anchor="w", wraplength=600, text=(
                "•  Оверлей — отдельное окно Windows. Оно неактивируемое, "
                "клики проходят сквозь него в игру, а при потере фокуса "
                "Roblox скрывается.\n"
                "•  Помощник не внедряет DLL, не читает память Roblox, "
                "не патчит и не изменяет файлы клиента.\n"
                "•  AFK использует только системный ввод Windows и молчит, "
                "когда игра не в фокусе.\n"
                "•  FPS берётся из статистики WDDM (D3DKMTQueryStatistics): "
                "Windows сама считает презентации кадров процесса, помощник "
                "только читает это число."
            )).pack(fill="x")

        self._refresh_ingame_status()

    def _save_ingame_cfg(self, _event=None, message=None):
        if not hasattr(self, "var_ingame_overlay"):
            return
        self.cfg["ingame_overlay"] = bool(self.var_ingame_overlay.get())
        self.cfg["ingame_island"] = bool(self.var_ingame_island.get())
        self.cfg["ingame_overlay_start"] = bool(self.var_ingame_start.get())
        self.cfg["ingame_clock"] = bool(self.var_ingame_clock.get())
        self.cfg["ingame_auto"] = bool(self.var_ingame_auto.get())
        self.cfg["ingame_afk"] = bool(self.var_ingame_afk.get())
        corner = ingame_mod.CORNER_BY_LABEL.get(
            self.combo_ingame_corner.get(), "tr")
        self.cfg["ingame_overlay_corner"] = corner
        mode = ingame_mod.AFK_MODE_BY_LABEL.get(
            self.combo_ingame_mode.get(), "mouse")
        self.cfg["ingame_afk_mode"] = mode
        key = ingame_mod.AFK_KEY_BY_LABEL.get(
            self.combo_ingame_key.get(), "space")
        self.cfg["ingame_afk_key"] = key
        try:
            minutes = int(str(self.combo_ingame_minutes.get()).split()[0])
        except (IndexError, ValueError):
            minutes = 5
        self.cfg["ingame_afk_minutes"] = max(1, min(15, minutes))
        self.var_ingame_mode.set(self.combo_ingame_mode.get())
        self.var_ingame_key.set(self.combo_ingame_key.get())
        self.var_ingame_minutes.set(self.combo_ingame_minutes.get())
        save_config(self.cfg)
        running = ingame_mod.helper_running()
        if message is None:
            message = ("Сохранено — помощник применит изменения "
                       "в течение 4 секунд" if running else "Сохранено")
        self.ingame_message_var.set(message)
        self.status_var.set("Настройки «В игре» сохранены")
        self._ingame_update_controls()
        self.log("In-game: настройки обновлены")

    def _ingame_hotkey_capture(self, target="main"):
        if getattr(self, "_hotkey_capture_id", None):
            self._ingame_hotkey_cancel()
            self.ingame_message_var.set("Запись комбинации отменена")
            return
        try:
            self._hotkey_capture_id = self.root.bind(
                "<KeyPress>", self._ingame_hotkey_key, add="+")
            self.root.focus_set()
        except tk.TclError:
            self._hotkey_capture_id = None
            return
        self._hotkey_capture_target = (
            "clock" if target == "clock" else "main")
        self._clock_or_main_button_var().set("Нажми комбинацию…")
        self.ingame_message_var.set(
            "Нажми Ctrl, Alt, Shift или Win вместе с клавишей. "
            "Esc — отмена")

    def _clock_or_main_button_var(self):
        if getattr(self, "_hotkey_capture_target", None) == "clock":
            return self.clock_hotkey_button_var
        return self.hotkey_button_var

    def _ingame_hotkey_cancel(self):
        capture_id = getattr(self, "_hotkey_capture_id", None)
        if capture_id:
            try:
                self.root.unbind("<KeyPress>", capture_id)
            except tk.TclError:
                pass
        self._hotkey_capture_id = None
        self._hotkey_capture_target = None
        if hasattr(self, "hotkey_button_var"):
            self.hotkey_button_var.set(
                ingame_mod.hotkey_label(self.cfg.get("ingame_hotkey")))
        if hasattr(self, "clock_hotkey_button_var"):
            self.clock_hotkey_button_var.set(
                ingame_mod.hotkey_label(
                    self.cfg.get("ingame_clock_hotkey")))

    def _ingame_hotkey_key(self, event):
        if not getattr(self, "_hotkey_capture_id", None):
            return None
        if str(event.keysym or "").lower() == "escape":
            self._ingame_hotkey_cancel()
            self.ingame_message_var.set("Запись комбинации отменена")
            return "break"
        key = ingame_mod.key_from_keysym(event.keysym)
        if key is None:
            return "break"
        mods = ingame_mod.pressed_hotkey_modifiers()
        if not mods:
            self.ingame_message_var.set(
                "Нужен хотя бы один модификатор: Ctrl, Alt, Shift или Win")
            return "break"
        spec = ingame_mod.normalize_hotkey("+".join(mods + [key]))
        if spec:
            self._ingame_apply_hotkey(
                spec, self._hotkey_capture_target or "main")
        return "break"

    def _ingame_hotkey_reset(self, target="main"):
        self._ingame_apply_hotkey(
            ingame_mod.DEFAULT_CLOCK_HOTKEY if target == "clock"
            else ingame_mod.DEFAULT_HOTKEY, target)

    def _ingame_apply_hotkey(self, spec, target="main"):
        if target == "clock":
            normalized = (ingame_mod.normalize_hotkey(spec)
                          or ingame_mod.DEFAULT_CLOCK_HOTKEY)
            self._ingame_hotkey_cancel()
            self.cfg["ingame_clock_hotkey"] = normalized
            label = ingame_mod.hotkey_label(normalized)
            self.clock_hotkey_button_var.set(label)
            self._save_ingame_cfg(message=f"Бинд часов: {label}")
            return
        normalized = (ingame_mod.normalize_hotkey(spec)
                      or ingame_mod.DEFAULT_HOTKEY)
        self._ingame_hotkey_cancel()
        self.cfg["ingame_hotkey"] = normalized
        label = ingame_mod.hotkey_label(normalized)
        self.hotkey_button_var.set(label)
        self._save_ingame_cfg(message=f"Горячая клавиша: {label}")

    def _ingame_feature_toggle(self):
        if (self.var_ingame_overlay.get() or self.var_ingame_island.get()
                or self.var_ingame_clock.get()):
            self.var_ingame_start.set(True)
        self._save_ingame_cfg()
        if (self.var_ingame_overlay.get() or self.var_ingame_island.get()
                or self.var_ingame_clock.get()
                or self.var_ingame_afk.get()):
            self._maybe_start_ingame_helper(force=True)

    def _maybe_start_ingame_helper(self, force=False):
        if not self.interactive or self._closing or self._ingame_busy:
            return
        if not (self.cfg.get("ingame_overlay")
                or self.cfg.get("ingame_island")
                or self.cfg.get("ingame_clock")
                or self.cfg.get("ingame_afk")):
            return
        if ingame_mod.helper_running():
            return
        if not force:
            if not self.cfg.get("ingame_auto"):
                return
            if self._ingame_user_stopped:
                return
            if time.monotonic() < self._ingame_next_auto:
                return
        self._ingame_start(show=False)

    def _ingame_update_controls(self):
        if not hasattr(self, "combo_ingame_corner"):
            return
        overlay_on = bool(self.var_ingame_overlay.get())
        island_on = bool(self.var_ingame_island.get())
        afk_on = bool(self.var_ingame_afk.get())
        key_mode = (self.var_ingame_mode.get()
                    == ingame_mod.AFK_MODE_LABELS["key"])
        running = ingame_mod.helper_running()
        self.combo_ingame_corner.configure(
            state="readonly" if overlay_on else "disabled")
        self.combo_ingame_mode.configure(
            state="readonly" if afk_on else "disabled")
        self.combo_ingame_key.configure(
            state="readonly" if afk_on and key_mode else "disabled")
        self.combo_ingame_minutes.configure(
            state="readonly" if afk_on else "disabled")
        desired = bool(self._ingame_last
                       and self._ingame_last.get("overlay_desired"))
        if self.btn_ingame_overlay is not None:
            name = "оверлей" if overlay_on else "островок"
            self._btn_state(
                self.btn_ingame_overlay, "primary", overlay_on or island_on,
                text=("Скрыть " if desired else "Показать ") + name)
        if self.btn_ingame_start is not None:
            self._btn_state(self.btn_ingame_start, "primary",
                            not running and not self._ingame_busy,
                            text="Запускаю…" if self._ingame_busy
                            else "Запустить помощник")
        if self.btn_ingame_stop is not None:
            self._btn_state(self.btn_ingame_stop, "ghost",
                            running or self._ingame_busy,
                            text="Останавливаю…" if self._ingame_busy
                            else "Остановить")

    def _refresh_ingame_status(self):
        if not hasattr(self, "ingame_helper_var"):
            return
        try:
            running = ingame_mod.helper_running()
        except Exception:  # noqa: BLE001
            running = False
        status = ingame_mod.read_status(max_age=6) if running else None
        self._ingame_last = status
        hotkey_name = ingame_mod.hotkey_label(
            (status or {}).get("hotkey_spec")
            or self.cfg.get("ingame_hotkey"))
        clock_name = ingame_mod.hotkey_label(
            (status or {}).get("clock_hotkey_spec")
            or self.cfg.get("ingame_clock_hotkey"))
        if running:
            pid = (status or {}).get("pid")
            self.ingame_helper_var.set(
                f"Помощник: работает (PID {pid})" if pid
                else "Помощник: работает")
        else:
            self.ingame_helper_var.set("Помощник: не запущен")
        if status is None:
            if running:
                self.ingame_roblox_var.set("Roblox: нет ответа")
                self.ingame_overlay_state_var.set("Оверлей: нет данных")
                self.ingame_fps_var.set("FPS: нет данных")
                self.ingame_hotkey_var.set(f"{hotkey_name}: нет данных")
                self.ingame_afk_state_var.set("AFK: нет данных")
                self.ingame_clock_state_var.set("Часы: нет данных")
            else:
                self.ingame_roblox_var.set("Roblox: не запущен")
                self.ingame_overlay_state_var.set(
                    "Оверлей: выключен" if not self.cfg.get("ingame_overlay")
                    else "Оверлей: помощник не запущен")
                self.ingame_fps_var.set("FPS: —")
                self.ingame_hotkey_var.set(
                    f"{hotkey_name}: помощник не запущен")
                self.ingame_afk_state_var.set(
                    "AFK: выключен" if not self.cfg.get("ingame_afk")
                    else "AFK: помощник не запущен")
                self.ingame_clock_state_var.set(
                    "Часы: выключены" if not self.cfg.get("ingame_clock")
                    else "Часы: помощник не запущен")
        else:
            if status.get("foreground"):
                roblox = "Roblox: в фокусе"
            elif status.get("roblox"):
                roblox = "Roblox: окно найдено"
            else:
                roblox = "Roblox: не запущен"
            self.ingame_roblox_var.set(roblox)
            if not self.cfg.get("ingame_overlay"):
                overlay = "Оверлей: выключен"
            elif status.get("overlay"):
                overlay = "Оверлей: показан"
            elif status.get("overlay_desired"):
                overlay = "Оверлей: ждёт Roblox в фокусе"
            else:
                overlay = f"Оверлей: скрыт ({hotkey_name})"
            self.ingame_overlay_state_var.set(overlay)
            fps_value = status.get("fps") if status.get("overlay") else None
            self.ingame_fps_var.set(
                f"FPS: {int(fps_value)}" if isinstance(fps_value, int)
                else "FPS: —")
            self.ingame_hotkey_var.set(
                f"{hotkey_name}: свободен" if status.get("hotkey")
                else f"{hotkey_name}: занят другой программой")
            if not self.cfg.get("ingame_afk"):
                afk = "AFK: выключен"
            elif not status.get("roblox"):
                afk = "AFK: ждёт Roblox"
            elif not status.get("foreground"):
                afk = "AFK: пауза — Roblox не в фокусе"
            elif status.get("afk_next_in") is None:
                afk = "AFK: пауза"
            else:
                afk = ("AFK: действие через "
                       + ingame_mod.format_countdown(status["afk_next_in"]))
            self.ingame_afk_state_var.set(afk)
            if not self.cfg.get("ingame_clock"):
                clock = "Часы: выключены"
            elif status.get("clock"):
                clock = f"Часы: показаны ({clock_name})"
            elif status.get("clock_desired"):
                clock = "Часы: ждут Roblox в фокусе"
            else:
                clock = f"Часы: скрыты ({clock_name})"
            self.ingame_clock_state_var.set(clock)
        self._ingame_update_controls()

    def _poll_ingame_status(self):
        if self._closing:
            return
        self._refresh_ingame_status()
        if (self.interactive and not self._ingame_busy
                and not self._ingame_user_stopped
                and (self.cfg.get("ingame_overlay")
                     or self.cfg.get("ingame_island")
                     or self.cfg.get("ingame_clock")
                     or self.cfg.get("ingame_afk"))
                and self.cfg.get("ingame_auto")
                and not ingame_mod.helper_running()
                and ingame_mod.roblox_pids()):
            self._maybe_start_ingame_helper()
        self._ingame_poll_job = self.root.after(1200,
                                                self._poll_ingame_status)

    def _ingame_start(self, show=False):
        if self._ingame_busy or self._closing:
            return
        if not (self.cfg.get("ingame_overlay")
                or self.cfg.get("ingame_island")
                or self.cfg.get("ingame_clock")
                or self.cfg.get("ingame_afk")):
            self.ingame_message_var.set(
                "Сначала включи оверлей, островок, часы или AFK-режим")
            return
        self._ingame_user_stopped = False
        if ingame_mod.helper_running():
            if show and self.cfg.get("ingame_overlay"):
                status = ingame_mod.read_status(max_age=10)
                if status and not status.get("overlay_desired"):
                    ingame_mod.toggle_overlay()
                    self.ingame_message_var.set(
                        "Оверлей включён — появится, когда Roblox "
                        "будет в фокусе")
                elif status:
                    self.ingame_message_var.set("Оверлей уже включён")
                else:
                    self.ingame_message_var.set(
                        "Помощник уже работает, но ещё не отчитался")
            return
        self._ingame_busy = True
        self._ingame_update_controls()
        self.ingame_message_var.set("Запускаю помощник…")

        def worker():
            ok, message = ingame_mod.start_helper(wait=4.0)
            if ok and show and self.cfg.get("ingame_overlay"):
                for _ in range(20):
                    status = ingame_mod.read_status(max_age=5)
                    if status and status.get("running"):
                        if not status.get("overlay_desired"):
                            ingame_mod.toggle_overlay()
                        break
                    time.sleep(0.05)
            self.q.put(lambda: self._ingame_start_done(ok, message))

        threading.Thread(target=worker, daemon=True).start()

    def _ingame_start_now(self):
        self._ingame_start(show=False)

    def _ingame_start_done(self, ok, message):
        self._ingame_busy = False
        if self._closing:
            return
        if ok:
            self._ingame_next_auto = 0.0
            self.ingame_message_var.set("Помощник запущен")
            self.log("In-game: помощник запущен")
        else:
            self._ingame_next_auto = time.monotonic() + 30
            self.ingame_message_var.set(
                f"Не удалось запустить: {message}")
            self.log(f"In-game: {message}")
        self._ingame_update_controls()
        self.root.after(600, self._refresh_ingame_status)

    def _ingame_stop(self):
        if self._ingame_busy:
            return
        self._ingame_user_stopped = True
        if ingame_mod.stop_helper():
            self.ingame_message_var.set("Останавливаю помощник…")
            self.root.after(900, self._ingame_stopped)
        else:
            self.ingame_message_var.set("Помощник уже не запущен")
            self._refresh_ingame_status()
        self._ingame_update_controls()

    def _ingame_stopped(self):
        if self._closing:
            return
        self._refresh_ingame_status()
        if ingame_mod.helper_running():
            self.ingame_message_var.set("Помощник ещё завершается…")
        else:
            self.ingame_message_var.set("Помощник остановлен")
            self.log("In-game: помощник остановлен")

    def _ingame_toggle_overlay(self):
        overlay_on = bool(self.cfg.get("ingame_overlay"))
        if not (overlay_on or self.cfg.get("ingame_island")):
            self.ingame_message_var.set(
                "Показ выключен — включи оверлей или островок выше")
            return
        if not ingame_mod.helper_running():
            self._ingame_start(show=True)
            return
        status = ingame_mod.read_status(max_age=10)
        if status is None:
            self.ingame_message_var.set(
                "Помощник ещё не готов — повтори через секунду")
            return
        want_show = not bool(status.get("overlay_desired"))
        if not ingame_mod.toggle_overlay():
            self.ingame_message_var.set("Не удалось переключить оверлей")
            return
        name = "Оверлей" if overlay_on else "Островок"
        if want_show:
            self.ingame_message_var.set(
                f"{name} включён — появится поверх Roblox")
        else:
            self.ingame_message_var.set(f"{name} скрыт")
        self.root.after(500, self._refresh_ingame_status)

    def _open_ingame_dir(self):
        folder = ingame_mod.status_path().parent
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))
        except OSError as exc:
            self.ingame_message_var.set(f"Не удалось открыть папку: {exc}")

    def _open_ingame_log(self):
        path = ingame_mod.status_path().parent / "helper.log"
        try:
            if not path.exists():
                self.ingame_message_var.set(
                    "Журнал пока пуст — помощник ещё не писал событий")
                return
            os.startfile(str(path))
        except OSError as exc:
            self.ingame_message_var.set(f"Не удалось открыть журнал: {exc}")

    def _start_ingame_helper_after_launch(self):
        self._maybe_start_ingame_helper()

    # -- страница «Настройки» ----------------------------------------------------------

    def _build_page_settings(self):
        c = self.c
        pad = self._scroll_page("settings")

        self._page_header(
            pad, "⚙", "Настройки",
            "Поведение лаунчера, оформление и управление Roblox на этом ПК")

        # поведение
        inner = self._card(pad, title="Поведение", glyph="☑")
        self.var_auto_close = tk.BooleanVar(value=self.cfg["auto_close"])
        self.var_save_history = tk.BooleanVar(value=self.cfg["save_history"])
        self.var_remember = tk.BooleanVar(value=self.cfg["remember_last"])
        for text, var in (
            ("Закрывать лаунчер через 1.5 с после запуска игры",
             self.var_auto_close),
            ("Запоминать запущенные игры (история)", self.var_save_history),
            ("Открывать лаунчер с последней запущенной игрой",
             self.var_remember),
        ):
            self._checkbox(inner, text, var,
                           command=self._save_settings).pack(
                anchor="w", pady=(4, 4))

        # игра по умолчанию
        inner2 = self._card(pad, title="Игра по умолчанию",
                            subtitle="Place ID, который подставится при "
                                     "запуске",
                            glyph="▶")
        row = tk.Frame(inner2, bg=c["surface"])
        row.pack(fill="x", pady=(8, 0))
        self.var_default = tk.StringVar(value=self.cfg["default_place_id"])
        ttk.Entry(row, textvariable=self.var_default).pack(
            side="left", fill="x", expand=True, ipady=3)
        tk.Label(row, text="сохранит «Сохранить всё» внизу",
                 bg=c["surface"], fg=c["muted"],
                 font=(UI_FONT, 8)).pack(side="left", padx=10)

        # Discord Rich Presence
        inner_d = self._card(
            pad, title="Discord",
            subtitle="Статус MaklyxaLauncher в профиле Discord",
            glyph="◍")
        self.var_discord_rpc = tk.BooleanVar(
            value=bool(self.cfg.get("discord_rpc", True)))
        self.var_discord_id = tk.StringVar(
            value=self.cfg.get("discord_app_id") or "")
        self._checkbox(
            inner_d, "Показывать статус лаунчера в Discord",
            self.var_discord_rpc).pack(anchor="w", pady=(4, 8))
        tk.Label(inner_d, text="Application ID", bg=c["surface"],
                 fg=c["text"], font=(UI_FONT, 10)).pack(anchor="w")
        row_d = tk.Frame(inner_d, bg=c["surface"])
        row_d.pack(fill="x", pady=(4, 0))
        ttk.Entry(row_d, textvariable=self.var_discord_id).pack(
            side="left", fill="x", expand=True, ipady=3)
        tk.Label(inner_d, text="ID применит «Сохранить всё» внизу",
                 bg=c["surface"], fg=c["muted"],
                 font=(UI_FONT, 8), anchor="w").pack(fill="x", pady=(4, 0))
        row_d2 = tk.Frame(inner_d, bg=c["surface"])
        row_d2.pack(fill="x", pady=(8, 0))
        self._button(row_d2, "Создать приложение",
                     self.open_discord_portal, side="left")
        tk.Label(inner_d, textvariable=self.discord_status_var,
                 bg=c["surface"], fg=c["muted"], font=(UI_FONT, 9),
                 anchor="w").pack(fill="x", pady=(8, 0))
        self._hint(
            inner_d,
            "Application ID берётся у приложения с именем MaklyxaLauncher "
            "на discord.com/developers/applications. Для статуса нужен "
            "запущенный Discord для ПК.", wrap=540)

        # Roblox на этом ПК
        inner3 = self._card(pad, title="Roblox на этом ПК", glyph="◉")
        tk.Label(inner3, textvariable=self.proto_var, bg=c["surface"],
                 fg=c["text"], font=(UI_FONT, 11), anchor="w",
                 wraplength=520, justify="left").pack(fill="x", pady=(6, 0))
        row3 = tk.Frame(inner3, bg=c["surface"])
        row3.pack(fill="x", pady=(10, 0))
        self._button(row3, "Проверить снова", self.recheck, side="left")
        self.btn_dl = self._button(row3, "Скачать Roblox", self.start_download,
                                   kind="primary", side="left", padx=8)
        self.btn_dl_cancel = self._button(row3, "Отмена",
                                          self.cancel_download, side="left")
        self.dl_bar = ttk.Progressbar(inner3, maximum=100,
                                       style="green.Horizontal.TProgressbar")
        self.dl_label = tk.Label(inner3, text="", bg=c["surface"],
                                 fg=c["muted"], font=(UI_FONT, 9),
                                 anchor="w")
        self.btn_dl_cancel.pack_forget()
        self.dl_bar.pack_forget()
        self.dl_label.pack_forget()

        # оформление
        inner4 = self._card(pad, title="Оформление",
                            subtitle="Тема и акцентный цвет интерфейса",
                            glyph="◐")
        theme_row = tk.Frame(inner4, bg=c["surface"])
        theme_row.pack(fill="x", pady=(8, 0))
        self.theme_buttons = {}
        for name in THEME_ORDER:
            kind = "primary" if name == self.theme_name else "ghost"
            btn = self._button(theme_row, THEMES[name]["label"],
                               lambda n=name: self.set_theme(n), kind=kind,
                               side="left", padx=(0, 8))
            self.theme_buttons[name] = btn
        custom = self._custom_theme_available()
        if custom:
            kind = "primary" if self.theme_name == "custom" else "ghost"
            self.theme_buttons["custom"] = self._button(
                theme_row, f"◐ {trunc(custom, 14)}",
                lambda n=custom: self.set_theme("custom", custom_name=n),
                kind=kind, side="left", padx=(0, 8))
        themes_row = tk.Frame(inner4, bg=c["surface"])
        themes_row.pack(fill="x", pady=(8, 0))
        self._button(themes_row, "Свои темы и картинка",
                     lambda: self.show_page("themes"), side="left")
        tk.Label(themes_row, text="цвета и фон в разделе «Темы»",
                 bg=c["surface"], fg=c["muted"],
                 font=(UI_FONT, 8)).pack(side="left", padx=8)

        tk.Label(inner4, text="Акцентный цвет", bg=c["surface"],
                 fg=c["text"], font=(UI_FONT, 10)).pack(
                     anchor="w", pady=(14, 4))
        color_row = tk.Frame(inner4, bg=c["surface"])
        color_row.pack(fill="x")
        for hex_color, preset_name in ACCENT_PRESETS:
            swatch = tk.Button(
                color_row, width=4, height=1, bd=0, relief="flat",
                bg=hex_color, activebackground=shade(hex_color, 1.15),
                highlightthickness=1, highlightbackground=c["border"],
                cursor="hand2", takefocus=False,
                command=lambda col=hex_color: self.set_accent(col))
            swatch.pack(side="left", padx=(0, 6), pady=2)
        self.accent_name_label = tk.Label(color_row, text="", bg=c["surface"],
                                          fg=c["muted"],
                                          font=(UI_FONT, 9))
        self.accent_name_label.pack(side="left", padx=8)

        buttons_row = tk.Frame(inner4, bg=c["surface"])
        buttons_row.pack(fill="x", pady=(14, 0))
        tk.Label(buttons_row, text="Кнопки окна", bg=c["surface"],
                 fg=c["text"], font=(UI_FONT, 10)).pack(side="left")
        style_btns = tk.Frame(buttons_row, bg=c["surface"])
        style_btns.pack(side="right")
        self.window_style_buttons = {}
        current_style = self.cfg.get("window_buttons", "mac")
        for key, label in (("mac", "macOS"), ("win", "Windows")):
            kind = "primary" if current_style == key else "ghost"
            self.window_style_buttons[key] = self._button(
                style_btns, label,
                lambda v=key: self.set_window_buttons(v),
                kind=kind, side="left",
                padx=(8, 0) if key == "win" else 0)
        self._hint(inner4, "macOS — цветные кружки слева, Windows — "
                    "кнопки свернуть, развернуть и закрыть справа.", wrap=540)

        # обновления
        inner_upd = self._card(
            pad, title="Обновления",
            subtitle="Проверка и скачивание новых версий с GitHub",
            glyph="⟳")
        upd_row = tk.Frame(inner_upd, bg=c["surface"])
        upd_row.pack(fill="x", pady=(8, 0))
        tk.Label(upd_row, text=f"Установлена версия {VERSION}",
                 bg=c["surface"], fg=c["text"],
                 font=(UI_FONT, 10, "bold")).pack(side="left")
        upd_btns = tk.Frame(upd_row, bg=c["surface"])
        upd_btns.pack(side="right")
        self.btn_update = self._button(
            upd_btns, "Проверить обновления", self.check_updates,
            side="left")
        self.btn_update_get = self._button(
            upd_btns, "Скачать обновление", self.start_update_download,
            kind="primary", side="left", padx=(8, 0))
        self.btn_update_install = self._button(
            upd_btns, "Установить и перезапустить", self.install_update,
            kind="primary", side="left", padx=(8, 0))
        self.btn_update_cancel = self._button(
            upd_btns, "Отмена", self.cancel_update_download,
            side="left", padx=(8, 0))
        self.btn_update_get.pack_forget()
        self.btn_update_install.pack_forget()
        self.btn_update_cancel.pack_forget()
        tk.Label(inner_upd, textvariable=self.update_status_var,
                 bg=c["surface"], fg=c["muted"],
                 font=(UI_FONT, 9), anchor="w").pack(
                     fill="x", pady=(9, 0))
        self.upd_progress = tk.Frame(inner_upd, bg=c["surface"])
        self.upd_progress.pack(fill="x")
        self.upd_bar = ttk.Progressbar(self.upd_progress, maximum=100,
                                       style="green.Horizontal.TProgressbar")
        self.upd_label = tk.Label(self.upd_progress, text="",
                                  bg=c["surface"], fg=c["muted"],
                                  font=(UI_FONT, 9), anchor="w")
        self.upd_bar.pack_forget()
        self.upd_label.pack_forget()
        self._hint(
            inner_upd,
            "Лаунчер сам скачает файл обновления с GitHub и проверит размер "
            "и контрольную сумму. «Установить» заменит файл лаунчера и "
            "запустит его заново — настройки и аккаунты сохранятся.", wrap=540)
        if self._upd_file is not None and Path(self._upd_file).is_file():
            self._show_update_install()

        # о программе
        inner5 = self._card(pad, title="О программе", glyph="ⓘ")
        tk.Label(
            inner5, bg=c["surface"], fg=c["muted"], font=(UI_FONT, 9),
            justify="left", anchor="w", wraplength=540, text=(
                "Лаунчер передаёт системе ссылку roblox:// — игру открывает "
                "официальный клиент Roblox.\n"
                "Код игры не изменяется: лаунчер только пишет конфиги и, "
                "если включены наборы курсоров, заменяет PNG-файлы "
                "курсоров.\n"
                "Название, версия, тема и списки хранятся локально."
            )).pack(fill="x")
        tk.Label(inner5, text=str(CONFIG_PATH), bg=c["surface"],
                 fg=c["muted"], font=(UI_FONT, 9), anchor="w",
                 wraplength=540, justify="left").pack(fill="x", pady=(6, 0))
        row4 = tk.Frame(inner5, bg=c["surface"])
        row4.pack(fill="x", pady=(10, 0))
        self._button(row4, "Открыть папку настроек", self.open_config_dir,
                     side="left")
        self._button(row4, "Сбросить настройки", self.reset_settings,
                     side="left", padx=8)

    # -- страница «Журнал» ---------------------------------------------------------------

    def _build_page_log(self):
        c = self.c
        pad = self._scroll_page("log")

        self._page_header(
            pad, "≡", "Журнал",
            "Все действия лаунчера за текущий сеанс")

        inner = self._card(pad, expand=True)

        head = tk.Frame(inner, bg=c["surface"])
        head.pack(fill="x")
        tk.Label(head, text="События", bg=c["surface"], fg=c["text"],
                 font=(UI_FONT, 11, "bold")).pack(side="left")
        self._button(head, "Очистить", self.clear_log, side="right")

        holder = tk.Frame(inner, bg=c["surface"])
        holder.pack(fill="both", expand=True, pady=(10, 0))
        self.log_text = tk.Text(
            holder, bg=c["list"], fg=c["text"], insertbackground=c["text"],
            bd=0, highlightthickness=1, highlightbackground=c["border"],
            highlightcolor=self.accent, font=("Consolas", 10),
            state="disabled", wrap="word", height=20, spacing1=2, spacing3=2,
            selectbackground=self.accent, selectforeground=c["on_accent"])
        scroll = self._vscroll(holder, self.log_text.yview, c["list"])
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        for line in self.log_lines:
            self.log_text.insert("end", line + "\n")
        if self.log_lines:
            self.log_text.see("end")

    # -- настройки: загрузка/сохранение ------------------------------------------------

    def _load_into_ui(self):
        self.var_auto_close.set(self.cfg["auto_close"])
        self.var_save_history.set(self.cfg["save_history"])
        self.var_remember.set(self.cfg["remember_last"])
        self.var_default.set(self.cfg["default_place_id"])
        self.var_discord_rpc.set(bool(self.cfg.get("discord_rpc", True)))
        self.var_discord_id.set(self.cfg.get("discord_app_id") or "")
        if hasattr(self, "var_launch_sound"):
            self.var_launch_sound.set(bool(self.cfg.get("launch_sound", True)))
        if hasattr(self, "var_error_sound"):
            self.var_error_sound.set(bool(self.cfg.get("error_sound", True)))

        start = ""
        if self.cfg["remember_last"] and self.cfg["last_place_id"]:
            start = self.cfg["last_place_id"]
        elif self.cfg["default_place_id"]:
            start = self.cfg["default_place_id"]
        if start:
            self.entry_var.set(start)
        self.on_entry_changed()

    def _save_settings(self):
        self.cfg["auto_close"] = bool(self.var_auto_close.get())
        self.cfg["save_history"] = bool(self.var_save_history.get())
        self.cfg["remember_last"] = bool(self.var_remember.get())
        save_config(self.cfg)
        self.status_var.set("Настройки сохранены")

    def save_everything(self):
        """Единая кнопка внизу: сохранить настройки всех разделов."""
        problems = []

        if hasattr(self, "var_apply_flags"):
            self.cfg["apply_flags_on_launch"] = bool(
                self.var_apply_flags.get())
        if hasattr(self, "combo_fps"):
            self._on_fps_change()
            self._on_api_change()
            self._on_quality_change()

        if hasattr(self, "var_auto_close"):
            self.cfg["auto_close"] = bool(self.var_auto_close.get())
            self.cfg["save_history"] = bool(self.var_save_history.get())
            self.cfg["remember_last"] = bool(self.var_remember.get())
            raw = self.var_default.get().strip()
            if raw:
                pid = parse_place_id(raw)
                if pid:
                    self.var_default.set(pid)
                    self.cfg["default_place_id"] = pid
                else:
                    problems.append("игра по умолчанию: некорректный ID")
            else:
                self.cfg["default_place_id"] = ""

        if hasattr(self, "var_discord_id"):
            if not self.apply_discord_settings(quiet=True):
                problems.append("Discord: Application ID — только цифры")

        if hasattr(self, "var_ingame_overlay"):
            self._save_ingame_cfg(message="Сохранено")

        if hasattr(self, "var_launch_sound"):
            self.cfg["launch_sound"] = bool(self.var_launch_sound.get())
        if hasattr(self, "var_error_sound"):
            self.cfg["error_sound"] = bool(self.var_error_sound.get())

        if hasattr(self, "boot_vars"):
            boot_error = self._write_boot_panel()
            if boot_error:
                problems.append(boot_error)

        if not save_config(self.cfg):
            problems.append("config.json: ошибка записи")
        if problems:
            text = "Сохранено с замечаниями — " + "; ".join(problems)
            self.status_var.set(text)
            self.log(text)
        else:
            self.status_var.set("Все настройки сохранены")
            self.log("Все настройки сохранены")

    # -- Discord Rich Presence ---------------------------------------------------

    def _start_presence(self):
        if self._rpc is not None or not self.cfg.get("discord_rpc"):
            return
        app_id = valid_discord_app_id(self.cfg.get("discord_app_id"))
        if not app_id:
            return
        self._rpc = DiscordPresence(app_id, self._rpc_start_ts)
        self._rpc.start()
        self._rpc_set("В меню", "Игра не выбрана")

    def _stop_presence(self):
        rpc, self._rpc = self._rpc, None
        if rpc is not None:
            rpc.stop()

    def _rpc_set(self, details, state):
        if self._rpc is not None:
            self._rpc.set_activity(details, state)

    def _discord_status_text(self):
        if not self.cfg.get("discord_rpc"):
            return "Статус в Discord выключен"
        if not valid_discord_app_id(self.cfg.get("discord_app_id")):
            return "Не подключён — укажи Application ID"
        if self._rpc is None:
            return "Ожидание запуска…"
        if self._rpc.connected:
            return "Подключён к Discord ✓"
        error = getattr(self._rpc, "last_error", "")
        if error == "rejected":
            return "Discord не принял Application ID"
        if error == "no_pipe":
            return "Discord не запущен — жду подключения"
        if error == "closed":
            return "Соединение с Discord потеряно — переподключаюсь"
        if error == "no_response":
            return "Discord не отвечает — переподключаюсь"
        return "Подключаюсь к Discord…"

    def _poll_discord_status(self):
        if self._closing:
            return
        self.discord_status_var.set(self._discord_status_text())
        self.root.after(2000, self._poll_discord_status)

    def apply_discord_settings(self, quiet=False):
        raw = self.var_discord_id.get().strip()
        app_id = valid_discord_app_id(raw)
        if raw and not app_id:
            self.status_var.set("Application ID — только цифры")
            if not quiet and self.interactive:
                messagebox.showwarning(
                    APP_NAME, "Application ID приложения Discord "
                    "состоит только из цифр.", parent=self.root)
            return False
        previous = (bool(self.cfg.get("discord_rpc", True)),
                    self.cfg.get("discord_app_id") or "")
        self.cfg["discord_rpc"] = bool(self.var_discord_rpc.get())
        self.cfg["discord_app_id"] = app_id
        self.var_discord_id.set(app_id)
        save_config(self.cfg)
        if previous != (self.cfg["discord_rpc"], app_id):
            self._stop_presence()
            self._start_presence()
            self.log("Discord Rich Presence: "
                     f"{'вкл' if self.cfg['discord_rpc'] else 'выкл'}"
                     + (f", ID {app_id}" if app_id else ""))
        if not app_id:
            self.status_var.set("Discord: укажи Application ID")
        elif self.cfg["discord_rpc"]:
            self.status_var.set("Discord: статус включён")
        else:
            self.status_var.set("Discord: статус выключен")
        return True

    def open_discord_portal(self):
        webbrowser.open(DISCORD_PORTAL_URL)

    def reset_settings(self):
        if self.interactive and not messagebox.askyesno(
                APP_NAME,
                "Сбросить настройки поведения и игру по умолчанию?\n"
                "Избранное и история не пострадают.",
                parent=self.root):
            return
        for key in ("auto_close", "save_history", "remember_last"):
            self.cfg[key] = DEFAULT_CONFIG[key]
        self.cfg["default_place_id"] = ""
        self.cfg["last_place_id"] = ""
        save_config(self.cfg)
        self._load_into_ui()
        self.status_var.set("Настройки сброшены")
        self.log("Настройки поведения сброшены")

    def open_config_dir(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(CONFIG_DIR))
        except OSError as exc:
            messagebox.showerror(
                APP_NAME, f"Не удалось открыть папку:\n{exc}",
                parent=self.root)

    # -- проверка обновлений ---------------------------------------------------------

    def check_updates(self):
        if self._update_busy:
            return
        self._update_busy = True
        self.update_status_var.set("Спрашиваю GitHub о последнем релизе…")
        self._btn_state(self.btn_update, "ghost", False, text="Проверяю…")
        self.status_var.set("Проверяю обновления…")

        def worker():
            result = check_launcher_update()
            self.q.put(lambda: self._on_update_checked(result))

        threading.Thread(target=worker, daemon=True).start()

    def _update_widgets_alive(self):
        try:
            return bool(self.btn_update.winfo_exists()
                        and self.btn_update_get.winfo_exists()
                        and self.btn_update_install.winfo_exists()
                        and self.btn_update_cancel.winfo_exists())
        except (AttributeError, tk.TclError):
            return False

    def _on_update_checked(self, result):
        self._update_busy = False
        if self._closing or not self._update_widgets_alive():
            return
        self._btn_state(self.btn_update, "ghost", True,
                        text="Проверить обновления")
        error = result.get("error")
        if error:
            self.update_status_var.set(
                "Не удалось связаться с GitHub — проверь подключение")
            self._hide_update_install()
            self.btn_update_get.pack_forget()
            self.log(f"Проверка обновлений: {error}")
            self.status_var.set("Не удалось проверить обновления")
            return
        latest = result.get("latest") or "?"
        if result.get("is_newer"):
            asset = self._pick_update_asset(result)
            self._upd_asset = asset
            self._upd_version = latest
            label = f"Доступна версия {latest}"
            if result.get("prerelease"):
                label += " (бета)"
            if asset is not None and asset.get("size"):
                label += f" · {mb(asset['size'])} МБ"
            self.update_status_var.set(label)
            self._hide_update_install()
            self._btn_state(self.btn_update_get, "primary", True,
                            text="Скачать обновление" if asset
                            else "Открыть загрузку")
            if not self.btn_update_get.winfo_manager():
                self.btn_update_get.pack(side="left", padx=(8, 0))
            if asset is not None:
                target = self._update_target_path(asset)
                if update_file_valid(target, asset):
                    self._upd_file = target
                    self._show_update_install()
            self.log(f"Проверка обновлений: доступна версия {latest}")
            self.status_var.set(f"Доступна версия {latest}")
            return
        self._upd_asset = None
        self._upd_file = None
        self._upd_version = ""
        self._hide_update_install()
        self.update_status_var.set(
            f"Установлена последняя версия — {VERSION}")
        self.btn_update_get.pack_forget()
        self.log(f"Проверка обновлений: установлена последняя версия "
                 f"{VERSION}")
        self.status_var.set("Обновлений нет")

    def _hide_update_install(self):
        self.btn_update_install.pack_forget()
        self.btn_update_cancel.pack_forget()

    def _show_update_install(self):
        version = f" {self._upd_version}" if self._upd_version else ""
        self.update_status_var.set(
            f"Обновление{version} скачано — нажми «Установить и перезапустить»")
        self.btn_update_get.pack_forget()
        if not self.btn_update_install.winfo_manager():
            self.btn_update_install.pack(side="left", padx=(8, 0))

    def _pick_update_asset(self, result):
        """Для установленной копии — MSI, для портативной — exe."""
        assets = result.get("assets") or {}
        order = ("msi", "exe") if self._installed_build() else ("exe", "msi")
        for kind in order:
            asset = assets.get(kind)
            if isinstance(asset, dict) and asset.get("url"):
                return dict(asset, kind=kind)
        return None

    @staticmethod
    def _launcher_exe():
        if not getattr(sys, "frozen", False):
            return None
        try:
            return Path(sys.executable).resolve()
        except OSError:
            return Path(sys.executable)

    def _installed_build(self):
        exe = self._launcher_exe()
        if exe is None:
            return False
        path = os.path.normcase(str(exe))
        for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            root = os.environ.get(var)
            if not root:
                continue
            root = os.path.normcase(os.path.abspath(root)).rstrip("\\")
            if path == root or path.startswith(root + "\\"):
                return True
        return False

    @staticmethod
    def _update_target_path(asset):
        return UPDATE_DIR / update_file_name(asset)

    def start_update_download(self):
        if self._upd_busy or self._upd_install_busy:
            return
        asset = self._upd_asset
        if not isinstance(asset, dict) or not asset.get("url"):
            self.log("Ассет обновления не найден — открываю страницу релиза")
            self.status_var.set("Открываю страницу релиза")
            webbrowser.open(RELEASES_URL)
            return
        target = self._update_target_path(asset)
        if update_file_valid(target, asset):
            self._upd_finished(target)
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._upd_error(exc)
            return
        self._upd_busy = True
        self._upd_cancel.clear()
        self._btn_state(self.btn_update, "ghost", False)
        self.btn_update_get.pack_forget()
        if not self.btn_update_cancel.winfo_manager():
            self.btn_update_cancel.pack(side="left", padx=(8, 0))
        self.upd_bar["value"] = 0
        self.upd_bar.pack(fill="x", pady=(8, 0))
        self.upd_label.pack(fill="x")
        self.upd_label.configure(text="Подключение…")
        self.update_status_var.set(
            f"Скачиваю обновление {self._upd_version}…")
        self.status_var.set("Скачиваю обновление…")
        self.log(f"Скачивание обновления: {asset.get('url')}")

        def worker():
            try:
                download_update(
                    asset["url"], target,
                    size=asset.get("size") or 0,
                    sha256=asset.get("sha256") or "",
                    progress=lambda done, total: self.q.put(
                        lambda d=done, t=total: self._upd_progress(d, t)),
                    cancel=self._upd_cancel)
                self.q.put(lambda: self._upd_finished(target))
            except UpdateCancelled:
                self.q.put(self._upd_cancelled_update)
            except Exception as exc:  # noqa: BLE001
                self.q.put(lambda e=exc: self._upd_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def cancel_update_download(self):
        if self._upd_busy:
            self._upd_cancel.set()
            self.upd_label.configure(text="Отмена…")

    def _upd_progress(self, done, total):
        if self._closing:
            return
        try:
            if total > 0:
                pct = min(100.0, done * 100.0 / total)
                self.upd_bar["value"] = pct
                self.upd_label.configure(
                    text=f"{mb(done)} из {mb(total)} МБ · {pct:.0f}%")
            else:
                self.upd_label.configure(text=f"{mb(done)} МБ")
        except tk.TclError:
            pass

    def _upd_reset_progress(self):
        self._upd_busy = False
        self.upd_bar.pack_forget()
        self.upd_label.pack_forget()
        self.btn_update_cancel.pack_forget()
        self._btn_state(self.btn_update, "ghost", True,
                        text="Проверить обновления")
        self._btn_state(self.btn_update_get, "primary", True,
                        text="Скачать обновление")

    def _upd_finished(self, target):
        self._upd_reset_progress()
        if self._closing:
            return
        self._upd_file = Path(target)
        try:
            size = self._upd_file.stat().st_size
        except OSError:
            size = 0
        self._show_update_install()
        self.log(f"Обновление скачано: {self._upd_file} ({mb(size)} МБ)")
        self.status_var.set("Обновление скачано")

    def _upd_cancelled_update(self):
        self._upd_reset_progress()
        if self._closing:
            return
        if not self.btn_update_get.winfo_manager():
            self.btn_update_get.pack(side="left", padx=(8, 0))
        self.update_status_var.set("Скачивание обновления отменено")
        self.status_var.set("Скачивание отменено")
        self.log("Скачивание обновления отменено")

    def _upd_error(self, exc):
        self._upd_reset_progress()
        if self._closing:
            return
        if not self.btn_update_get.winfo_manager():
            self.btn_update_get.pack(side="left", padx=(8, 0))
        self.update_status_var.set(
            "Не удалось скачать обновление — попробуй ещё раз")
        self.status_var.set("Ошибка скачивания обновления")
        self.log(f"Ошибка скачивания обновления: {exc}")
        if self.interactive:
            messagebox.showerror(
                APP_NAME,
                f"Не удалось скачать обновление:\n{exc}\n\n"
                "Проверь интернет и попробуй снова.",
                parent=self.root)

    def install_update(self):
        if self._upd_busy or self._upd_install_busy:
            return
        path = self._upd_file
        if path is None or not Path(path).is_file():
            self._upd_file = None
            self.update_status_var.set(
                "Файл обновления не найден — скачай его заново")
            return
        path = Path(path)
        exe = self._launcher_exe()
        kind = (self._upd_asset or {}).get("kind") or (
            "msi" if path.suffix.lower() == ".msi" else "exe")
        if exe is None:
            if self.interactive:
                messagebox.showinfo(
                    APP_NAME,
                    "Лаунчер запущен из исходников — автоматическая "
                    f"установка недоступна.\nФайл обновления: {path}",
                    parent=self.root)
            try:
                if kind == "msi":
                    os.startfile(str(path))
                    self.log("Открываю установщик обновления")
                else:
                    os.startfile(str(path.parent))
            except OSError as exc:
                self.log(f"Не удалось открыть обновление: {exc}")
            return
        if self.interactive:
            note = ("Установщик запросит права администратора. "
                    if kind == "msi" else "")
            if not messagebox.askyesno(
                    APP_NAME,
                    f"{note}Лаунчер закроется, обновление установится и "
                    "запустится снова.\nПродолжить?",
                    parent=self.root):
                return
        self._upd_install_busy = True
        self.btn_update_install.pack_forget()
        self.update_status_var.set("Останавливаю помощник перед обновлением…")
        self.status_var.set("Устанавливаю обновление…")
        self._install_after_helper(time.monotonic() + 6.0)

    def _install_after_helper(self, deadline):
        if self._closing:
            return
        if ingame_mod.helper_running():
            if time.monotonic() < deadline:
                if not self._upd_helper_stop_sent:
                    ingame_mod.stop_helper()
                    self._upd_helper_stop_sent = True
                    self.log("In-game: останавливаю помощник перед "
                             "обновлением")
                self.root.after(
                    300, lambda: self._install_after_helper(deadline))
                return
            self.log("In-game: помощник не завершился — запускаю обновление")
        self._upd_helper_stop_sent = False
        self._launch_updater()

    def _launch_updater(self):
        path = Path(self._upd_file)
        exe = self._launcher_exe()
        kind = (self._upd_asset or {}).get("kind") or (
            "msi" if path.suffix.lower() == ".msi" else "exe")
        if kind == "msi":
            args = {"target": str(exe or ""), "new_file": "", "msi": str(path)}
        else:
            args = {"target": str(exe or ""), "new_file": str(path), "msi": ""}
        try:
            fd, name = tempfile.mkstemp(prefix="maklyxa-update-",
                                        suffix=".ps1")
            os.close(fd)
            script = Path(name)
            script.write_text(build_update_script(os.getpid(), **args),
                              encoding="utf-8-sig")
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-WindowStyle", "Hidden", "-File", str(script)],
                creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                close_fds=True)
        except OSError as exc:
            self._upd_install_busy = False
            self._upd_helper_stop_sent = False
            self._upd_reset_progress()
            self._show_update_install()
            self.update_status_var.set(
                "Не удалось запустить установку обновления")
            self.status_var.set("Ошибка установки обновления")
            self.log(f"Ошибка установки обновления: {exc}")
            if self.interactive:
                messagebox.showerror(
                    APP_NAME, f"Ошибка установки:\n{exc}", parent=self.root)
            return
        self.log(f"Установка обновления: {path}")
        self.status_var.set("Лаунчер перезапустится с новой версией")
        self.root.after(150, self.close)

    def _close_on_alt_tab(self):
        """Alt+Tab в другое окно закрывает лаунчер, а не оставляет его висеть."""
        if self._closing or not self.interactive:
            return
        if self._login_busy or self._login_window_alive():
            return
        if (self._client_busy or self._play_busy or self._dl_active
                or self._upd_busy or self._upd_install_busy):
            return
        self.close()

    def close(self):
        """Закрытие: запоминаем геометрию окна и выходим."""
        if self._closing:
            return
        self._closing = True
        self._stop_presence()
        self._hide_launch_splash()
        stop_launch_sound()
        if self._loaded_font_path is not None:
            unload_private_font(self._loaded_font_path)
        try:
            self.cfg["window_geometry"] = self.root.geometry()
        except tk.TclError:
            pass
        save_config(self.cfg)
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # -- журнал --------------------------------------------------------------------------

    def log(self, message):
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}]  {message}"
        self.log_lines.append(line)
        if len(self.log_lines) > 400:
            del self.log_lines[:-400]
        box = getattr(self, "log_text", None)
        if box is None:
            return
        try:
            box.configure(state="normal")
            box.insert("end", line + "\n")
            box.see("end")
            box.configure(state="disabled")
        except tk.TclError:
            pass

    def clear_log(self):
        self.log_lines.clear()
        try:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
        except tk.TclError:
            pass
        self.status_var.set("Журнал очищен")

    # -- списки ----------------------------------------------------------------------------

    def _name_for(self, pid):
        if pid in self.info_cache and self.info_cache[pid].get("name"):
            return self.info_cache[pid]["name"]
        for item in self.cfg["favorites"] + self.cfg["history"]:
            if item["place_id"] == pid and item.get("name"):
                return item["name"]
        return ""

    def _fill_box(self, box, items, empty_text, limit=None):
        box.delete(0, "end")
        pids = []
        if limit is not None:
            items = items[:limit]
        if not items:
            box.insert("end", f"   {empty_text}")
            return pids
        for item in items:
            pid = item["place_id"]
            name = item.get("name") or ""
            label = f"{trunc(name)}  ·  {pid}" if name else f"   {pid}"
            box.insert("end", label)
            pids.append(pid)
        return pids

    def _refresh_lists(self):
        self.fav_pids = self._fill_box(
            self.fav_box, self.cfg["favorites"],
            "пока пусто — добавь кнопкой «В избранное»")
        self.hist_pids = self._fill_box(self.hist_box, self.cfg["history"],
                                        "история пока пуста")
        self.recent_pids = self._fill_box(self.recent_box, self.cfg["history"],
                                          "запущенных игр пока не было",
                                          limit=8)

    def _selected_pid(self, box, pids):
        sel = box.curselection()
        if not sel:
            self.status_var.set("Выбери строку в списке")
            return None
        idx = sel[0]
        if idx >= len(pids):
            return None
        return pids[idx]

    def _fav_play(self):
        pid = self._selected_pid(self.fav_box, self.fav_pids)
        if pid:
            self.play(pid)

    def _hist_play(self):
        pid = self._selected_pid(self.hist_box, self.hist_pids)
        if pid:
            self.play(pid)

    def _recent_activate(self, _event):
        pid = self._selected_pid(self.recent_box, self.recent_pids)
        if pid:
            self.play(pid)

    def _fav_remove(self):
        pid = self._selected_pid(self.fav_box, self.fav_pids)
        if not pid:
            return
        self.cfg["favorites"] = [f for f in self.cfg["favorites"]
                                 if f["place_id"] != pid]
        save_config(self.cfg)
        self._refresh_lists()
        self.status_var.set("Удалено из избранного")
        self.log(f"Из избранного удалена игра {pid}")

    def _hist_clear(self):
        self.cfg["history"] = []
        save_config(self.cfg)
        self._refresh_lists()
        self.status_var.set("История очищена")
        self.log("История запусков очищена")

    def add_favorite(self):
        pid = parse_place_id(self.entry_var.get())
        if not pid:
            self.status_var.set("Сначала введи ссылку или ID игры")
            return
        if any(f["place_id"] == pid for f in self.cfg["favorites"]):
            self.status_var.set("Эта игра уже в избранном")
            return
        self.cfg["favorites"].insert(
            0, {"place_id": pid, "name": self._name_for(pid),
                "ts": int(time.time())})
        save_config(self.cfg)
        self._refresh_lists()
        self.status_var.set("Добавлено в избранное")
        self.log(f"В избранное добавлена игра {self._name_for(pid) or pid}")

    def copy_id(self):
        pid = parse_place_id(self.entry_var.get())
        if not pid:
            self.status_var.set("Нечего копировать")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(pid)
        self.status_var.set(f"Place ID {pid} скопирован")

    def paste_clipboard(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            self.status_var.set("Буфер обмена пуст")
            return
        self.entry_var.set(text)
        self.on_entry_changed()

    def open_in_browser(self):
        pid = parse_place_id(self.entry_var.get())
        if not pid:
            self.status_var.set("Сначала введи ссылку или ID игры")
            return
        webbrowser.open(f"https://www.roblox.com/games/{pid}")

    # -- карточка игры: данные и иконка --------------------------------------------------------

    def on_entry_changed(self, _event=None):
        if self._debounce:
            try:
                self.root.after_cancel(self._debounce)
            except tk.TclError:
                pass
            self._debounce = None
        pid = parse_place_id(self.entry_var.get())
        if not pid:
            self._reset_game_card()
            return
        self._debounce = self.root.after(450, lambda: self._fetch_info(pid))

    def _reset_game_card(self):
        self.game_name_var.set("Игра не выбрана")
        self.info_var.set("Вставь ссылку на игру или введи Place ID")
        self.pid_var.set("")
        self.icon_lbl.configure(image="", text="▶")
        self.icon_img = None

    def _fetch_info(self, pid):
        if pid in self.info_cache:
            self._show_info(pid, self.info_cache[pid])
            return
        self.game_name_var.set(f"Place ID {pid}")
        self.info_var.set("Загружаю данные…")

        def worker():
            info = fetch_game_info(pid)
            self.q.put(lambda: self._on_info(pid, info))

        threading.Thread(target=worker, daemon=True).start()

    def _on_info(self, pid, info):
        if self._closing:
            return
        if info:
            self.info_cache[pid] = info
            changed = False
            for key in ("favorites", "history"):
                for item in self.cfg[key]:
                    if item["place_id"] == pid and not item.get("name"):
                        item["name"] = info["name"]
                        changed = True
            if changed:
                save_config(self.cfg)
                self._refresh_lists()
        if parse_place_id(self.entry_var.get()) == pid:
            self._show_info(pid, info or {})

    def _show_info(self, pid, info):
        info = info or {}
        name = info.get("name") or ""
        self.game_name_var.set(name or f"Place ID {pid}")
        parts = []
        if info.get("creator"):
            parts.append(info["creator"])
        if info.get("playing"):
            parts.append(f"сейчас в игре: {fmt_num(info['playing'])}")
        self.info_var.set("  ·  ".join(parts))
        self.pid_var.set(f"Place ID {pid}")
        self._load_icon(pid, info.get("universe_id"))

    def _load_icon(self, pid, universe_id):
        if not hasattr(self, "_icon_pending"):
            self._icon_pending = set()
        cached = self.icon_cache.get(pid)
        if cached:
            self._set_icon(cached)
            return
        if not universe_id:
            return
        if pid in self._icon_pending:
            return
        self._icon_pending.add(pid)

        def worker():
            data = fetch_game_icon(universe_id)
            self.q.put(lambda: self._on_icon(pid, data))

        threading.Thread(target=worker, daemon=True).start()

    def _on_icon(self, pid, data):
        self._icon_pending.discard(pid)
        if self._closing or not data:
            return
        self.icon_cache[pid] = data
        if parse_place_id(self.entry_var.get()) == pid:
            self._set_icon(data)

    def _set_icon(self, data):
        try:
            img = tk.PhotoImage(data=base64.b64encode(data).decode("ascii"))
        except tk.TclError:
            return
        factor = max(1, round(img.width() / 86))
        if factor > 1:
            img = img.subsample(factor, factor)
        self.icon_img = img
        self.icon_lbl.configure(image=img, text="")

    # -- обнаружение Roblox и статус --------------------------------------------------------

    def _apply_status(self, kind, detail):
        self.roblox_kind = kind
        if kind == "installed":
            chosen = selected_roblox_exe(getattr(self, "cfg", None))
            target = str(chosen or detail)
            version = file_version_text(target)
            if version:
                version = ".".join(version.split(".")[:2])
            else:
                version = Path(target).parent.name or Path(target).name
            text = f"Roblox: установлен ({version})"
        elif kind == "bootstrapper":
            text = (f"Roblox: найден бутстрапер {Path(detail).name} — "
                    "клиент доустановится при первом запуске")
        elif kind == "broken":
            text = (f"Roblox: протокол есть, но файл потерян "
                    f"({Path(detail).name})")
        else:
            text = "Roblox: не найден"
        self.proto_var.set(text)
        self._refresh_sidebar_status()

    def _startup_checks(self):
        if self._startup_started:
            return
        self._startup_started = True
        self.log(f"{APP_NAME} v{VERSION} запущен")
        self.log(f"Файл настроек: {CONFIG_PATH}")
        self.proto_var.set("Проверка Roblox…")
        threading.Thread(target=self._startup_worker, daemon=True).start()

    def _startup_worker(self):
        kind, detail = roblox_status()
        boot_kind, boot_path = find_bootstrapper_settings()
        flags_msg = ""
        if self.cfg["apply_flags_on_launch"]:
            values = fastflag_values(self.cfg)
            if values:
                written = apply_fastflags(self.cfg, values)
                flags_msg = f"FastFlags: {len(values)} → {written} файл(ов)"
        self.q.put(lambda: self._startup_done(
            kind, detail, boot_kind, boot_path, flags_msg))

    def _startup_done(self, kind, detail, boot_kind, boot_path, flags_msg):
        if self._closing:
            return
        if boot_kind:
            self.log(f"Конфиг бутстрапера: {boot_kind} — {boot_path}")
        if flags_msg:
            self.log(flags_msg)
        self._apply_status(kind, detail)
        if kind == "installed":
            self.log(f"Клиент найден: {detail}")
        elif kind == "bootstrapper":
            self.log(f"Бутстрапер найден: {detail}")
        elif kind == "broken":
            self.log(f"Обработчик roblox:// не найден на диске: {detail}")
        else:
            self.log("Roblox не найден на этом ПК.")
        if kind in ("missing", "broken") and self.interactive:
            if messagebox.askyesno(
                    APP_NAME,
                    "Roblox на этом ПК не найден.\n"
                    "Скачать официальный установщик сейчас?",
                    parent=self.root):
                self.show_page("settings")
                self.start_download()
            else:
                self.log("Скачивание отложено — кнопка «Скачать Roblox» "
                         "в настройках")

    def recheck(self):
        self.status_var.set("Проверяю…")
        self.root.update_idletasks()

        def worker():
            kind, detail = roblox_status()
            self.q.put(lambda: self._recheck_done(kind, detail))

        threading.Thread(target=worker, daemon=True).start()

    def _recheck_done(self, kind, detail):
        if self._closing:
            return
        self._apply_status(kind, detail)
        self.log(f"Повторная проверка: {self.proto_var.get()}")
        self.status_var.set("Проверка завершена")

    # -- скачивание установщика ----------------------------------------------------------------

    def _btn_state(self, btn, kind, enabled, text=None):
        """Включение/выключение своих кнопок с корректным цветом."""
        base, fg, hot = self._btn_colors(kind)
        cfg = {}
        if text is not None:
            cfg["text"] = text
        if enabled:
            cfg.update(state="normal", bg=base, fg=fg,
                       activebackground=hot, activeforeground=fg,
                       disabledforeground=fg)
        else:
            dim = shade(base, 0.78)
            cfg.update(state="disabled", bg=dim,
                       disabledforeground=self.c["muted"])
        btn.configure(**cfg)

    def start_download(self):
        if self._dl_active:
            return
        kind, _detail = roblox_status()
        if kind == "installed":
            self.status_var.set("Roblox уже установлен")
            self.log("Скачивание не требуется — клиент уже найден")
            return
        self._dl_active = True
        self._dl_cancel = threading.Event()
        self._btn_state(self.btn_dl, "primary", False, text="Скачивание…")
        self.btn_dl_cancel.pack(side="left", padx=(4, 0))
        self.dl_bar.pack(fill="x", pady=(8, 0))
        self.dl_label.pack(fill="x")
        self.dl_bar["value"] = 0
        self.dl_label.configure(text="Подключение…")
        self.status_var.set("Скачиваю Roblox…")
        self.log(f"Скачивание установщика: {INSTALLER_URL}")

        target = Path(tempfile.gettempdir()) / "RobloxPlayerInstaller.exe"

        def worker():
            try:
                req = urllib.request.Request(INSTALLER_URL,
                                             headers=HTTP_HEADERS)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    total = int(resp.headers.get("Content-Length") or 0)
                    done = 0
                    with open(target, "wb") as fh:
                        while True:
                            if self._dl_cancel.is_set():
                                fh.close()
                                try:
                                    target.unlink()
                                except OSError:
                                    pass
                                self.q.put(self._dl_cancelled)
                                return
                            chunk = resp.read(131072)
                            if not chunk:
                                break
                            fh.write(chunk)
                            done += len(chunk)
                            self.q.put(
                                lambda d=done, t=total: self._dl_progress(d, t))
                self.q.put(lambda: self._dl_finished(target))
            except Exception as exc:  # noqa: BLE001
                self.q.put(lambda e=exc: self._dl_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def cancel_download(self):
        if self._dl_active:
            self._dl_cancel.set()
            self.dl_label.configure(text="Отмена…")

    def _dl_reset_buttons(self):
        self._dl_active = False
        self._btn_state(self.btn_dl, "primary", True, text="Скачать Roblox")
        self.btn_dl_cancel.pack_forget()
        self.dl_bar.pack_forget()
        self.dl_label.pack_forget()

    def _dl_progress(self, done, total):
        if total > 0:
            pct = min(100.0, done * 100.0 / total)
            self.dl_bar["value"] = pct
            self.dl_label.configure(
                text=f"{mb(done)} из {mb(total)} МБ · {pct:.0f}%")
        else:
            self.dl_label.configure(text=f"{mb(done)} МБ")

    def _dl_finished(self, target):
        if self._closing:
            return
        self._dl_reset_buttons()
        size = target.stat().st_size if target.exists() else 0
        self.status_var.set("Установщик скачан")
        self.log(f"Скачано: {target} ({mb(size)} МБ)")
        if self.interactive and messagebox.askyesno(
                APP_NAME,
                f"Установщик Roblox скачан ({mb(size)} МБ).\n"
                "Запустить установку?",
                parent=self.root):
            try:
                os.startfile(str(target))
                self.log("Установщик запущен — дождись окончания установки, "
                         "затем нажми «Проверить снова»")
                self.status_var.set("Установщик запущен")
            except OSError as exc:
                self.log(f"Не удалось запустить установщик: {exc}")
                messagebox.showerror(APP_NAME, f"Ошибка запуска:\n{exc}",
                                     parent=self.root)
        else:
            self.log("Установщик сохранён, запуск отменён")

    def _dl_cancelled(self):
        if self._closing:
            return
        self._dl_reset_buttons()
        self.status_var.set("Скачивание отменено")
        self.log("Скачивание отменено пользователем")

    def _dl_error(self, exc):
        if self._closing:
            return
        self._dl_reset_buttons()
        self.status_var.set("Ошибка скачивания")
        self.log(f"Ошибка скачивания: {exc}")
        if self.interactive:
            messagebox.showerror(
                APP_NAME,
                f"Не удалось скачать установщик:\n{exc}\n\n"
                "Проверь интернет и попробуй снова.",
                parent=self.root)

    # -- запуск клиента без игры -----------------------------------------------------------------

    def launch_client(self):
        if self._client_busy:
            return
        self._client_busy = True
        self._btn_state(self.btn_client, "primary", False,
                        text="Ищу Roblox…")
        self.status_var.set("Ищу Roblox…")
        self._rpc_set("Клиент Roblox", "Открываю клиент…")
        self._begin_launch_effect("Roblox", "Ищу клиент Roblox…")
        threading.Thread(target=self._client_worker, daemon=True).start()

    def _client_worker(self):
        kind, detail = roblox_status()
        if kind in ("missing", "broken"):
            self.q.put(self._client_missing)
            return

        if self.cfg["apply_flags_on_launch"]:
            values = fastflag_values(self.cfg)
            if values:
                apply_fastflags(self.cfg, values)

        before = player_pids()
        if before:
            focused = False
            try:
                focused = focus_roblox_window(before)
            except Exception:
                focused = False
            if focused:
                self.q.put(lambda: self._client_result("already", True))
                return
            # окна нет — клиент остался в трее и ссылок не принимает:
            # перезапускаем, иначе «Roblox уже запущен» без окна
            close_roblox_players()
            wait_players_gone(10)
            before = player_pids()

        cursor_name = str(self.cfg.get("cursor_set") or "")
        if self.cfg.get("use_custom_cursors") and not before and cursor_name:
            cursor_result = apply_cursor_set(cursor_name)
            self.q.put(lambda r=cursor_result: self._cursor_launch_done(r))

        chosen = configured_roblox_exe(self.cfg)
        if chosen is not None:
            self.log(f"Версия Roblox: прямой запуск {chosen.parent.name}")

        # 1) ссылка приложения Roblox — открывает главную страницу клиента
        if chosen is None:
            try:
                os.startfile("roblox://navigation/home")
                if wait_for_roblox_window(12):
                    self.q.put(lambda: self._client_result("protocol", True))
                    return
            except OSError:
                pass

        # 2) прямой запуск клиента: выбранная версия или самая новая
        exe = chosen
        if exe is None and kind == "installed":
            exe = find_player_exe()
        if exe:
            try:
                subprocess.Popen([str(exe)], cwd=str(exe.parent))
                if wait_for_roblox_window(12):
                    self.q.put(lambda: self._client_result("exe", True))
                    return
            except OSError:
                pass

        self.q.put(lambda: self._client_result("fail", False))

    def _client_result(self, outcome, focused):
        if self._closing:
            return
        self._client_busy = False
        self._btn_state(self.btn_client, "primary", True,
                        text="▶   ПРОСТО В ROBLOX")
        if outcome == "already":
            self._rpc_set("Клиент Roblox", "Клиент уже открыт")
            if focused:
                self.status_var.set("Roblox уже запущен — окно впереди")
                self.log("Roblox уже запущен, окно выведено на передний план")
            else:
                self.status_var.set("Roblox уже запущен")
                self.log("Roblox уже запущен (окно вывести не удалось)")
        elif outcome in ("exe", "protocol"):
            self._rpc_set("Клиент Roblox", "Открыт без игры")
            how = ("прямым запуском клиента" if outcome == "exe"
                   else "через roblox://")
            self.status_var.set("Roblox запущен ✓")
            self.log(f"Клиент Roblox запущен {how}")
        else:
            self._rpc_set("В меню", "Открыт сайт Roblox")
            self.status_var.set("Не удалось — открыл Roblox в браузере")
            self.log("Прямой запуск не сработал, открыт сайт Roblox")
            webbrowser.open(DISCOVER_URL)
            self._play_error_sound()
        if outcome in ("already", "exe", "protocol"):
            self._maybe_start_ingame_helper()
        self._end_launch_effect(
            "Roblox уже запущен" if outcome == "already"
            else "Roblox запущен" if outcome in ("exe", "protocol")
            else "Открываю сайт Roblox…")

    def _client_missing(self):
        if self._closing:
            return
        self._client_busy = False
        self._btn_state(self.btn_client, "primary", True,
                        text="▶   ПРОСТО В ROBLOX")
        self._rpc_set("В меню", "Roblox не установлен")
        self.log("Клиент не найден — предлагаю скачать")
        self._end_launch_effect("Roblox не установлен")
        self._play_error_sound()
        if self.interactive and messagebox.askyesno(
                APP_NAME, "Roblox не установлен.\n"
                "Скачать официальный установщик?",
                parent=self.root):
            self.show_page("settings")
            self.start_download()
        else:
            self.status_var.set("Roblox не установлен — см. Настройки")

    # -- версия клиента -----------------------------------------------------------

    @staticmethod
    def _version_key(path):
        try:
            return str(Path(path).resolve()).lower()
        except OSError:
            return str(path).lower()

    @staticmethod
    def _roblox_version_text(exe):
        version = file_version_text(str(exe))
        if version:
            return version
        try:
            return Path(exe).parent.name or Path(exe).name
        except (TypeError, ValueError):
            return str(exe)

    def _refresh_roblox_versions(self):
        box = getattr(self, "roblox_version_box", None)
        if box is None:
            return
        entries = roblox_version_entries()
        newest = selected_roblox_exe({"roblox_version": ""})
        newest_key = self._version_key(newest) if newest is not None else ""
        choices = [("", "Авто — самая новая версия")]
        for entry in entries:
            date = time.strftime("%d.%m.%Y", time.localtime(entry["mtime"]))
            mark = (" · используется"
                    if self._version_key(entry["exe"]) == newest_key else "")
            choices.append((
                str(entry["exe"]),
                f"{entry['version']} · {entry['name']} · {date}{mark}"))
        self._roblox_version_choices = choices
        box.configure(values=[label for _path, label in choices])
        saved = str(self.cfg.get("roblox_version") or "")
        index = 0
        if saved:
            saved_key = self._version_key(saved)
            saved_folder = Path(saved).parent.name.lower()
            for position, (path, _label) in enumerate(choices):
                if not path:
                    continue
                if (self._version_key(path) == saved_key
                        or Path(path).parent.name.lower() == saved_folder):
                    index = position
                    break
        box.current(index)
        note_var = getattr(self, "roblox_version_note_var", None)
        if note_var is None:
            return
        if not entries:
            note_var.set("Установленные версии не найдены — скачай Roblox "
                         "в настройках")
        elif index:
            label = choices[index][1].split(" · ")[0]
            note_var.set(f"Запускается {label} — напрямую, без обновления")
        elif saved:
            note_var.set("Сохранённая версия не найдена — используется "
                         "самая новая")
        elif newest is not None:
            note_var.set("Авто: " + self._roblox_version_text(newest))
        else:
            note_var.set("Roblox не найден")

    def _roblox_version_pick(self, _event=None):
        choices = getattr(self, "_roblox_version_choices", [])
        index = self.roblox_version_box.current()
        if not (0 <= index < len(choices)):
            return
        path = str(choices[index][0] or "")
        self.cfg["roblox_version"] = path
        save_config(self.cfg)
        if path:
            self.log(f"Версия Roblox: выбрана {Path(path).parent.name} — "
                     "запуск напрямую")
            self.status_var.set("Запускается выбранная версия Roblox")
        else:
            self.log("Версия Roblox: автоматически (самая новая)")
            self.status_var.set("Версия Roblox: авто")
        self._refresh_roblox_versions()
        self._refresh_sidebar_status()

    def _open_roblox_versions_dir(self):
        folder = roblox_versions_dir()
        if folder is None or not folder.is_dir():
            self.status_var.set("Папка версий Roblox не найдена")
            self.log("Папка версий Roblox не найдена")
            return
        try:
            os.startfile(str(folder))
        except OSError as exc:
            self.log(f"Не удалось открыть папку версий: {exc}")

    # -- запуск игры ------------------------------------------------------------------------------

    def play(self, place_id=None):
        pid = place_id or parse_place_id(self.entry_var.get())
        if not pid:
            self.status_var.set("Не удалось определить Place ID")
            self.log("Ошибка: не удалось определить Place ID")
            if self.interactive:
                messagebox.showwarning(
                    APP_NAME,
                    "Введи ссылку вида https://www.roblox.com/games/123456789/...\n"
                    "или просто Place ID (набор цифр).",
                    parent=self.root)
            return
        if not (pid.isdigit() and 4 <= len(pid) <= 20):
            self.status_var.set("Некорректный Place ID")
            self.log(f"Ошибка: некорректный Place ID «{pid}»")
            return

        kind, _detail = roblox_status()
        if kind in ("missing", "broken"):
            self.log("Roblox не найден — предлагаю скачать установщик")
            if self.interactive and messagebox.askyesno(
                    APP_NAME,
                    "Roblox не установлен, поэтому игру не запустить.\n"
                    "Скачать официальный установщик?",
                    parent=self.root):
                self.show_page("settings")
                self.start_download()
            else:
                self.status_var.set("Roblox не установлен — см. Настройки")
            return

        self.show_page("game")
        if self.cfg["apply_flags_on_launch"]:
            values = fastflag_values(self.cfg)
            if values:
                written = apply_fastflags(self.cfg, values)
                self.log(f"FastFlags: {len(values)} → {written} файл(ов)")
        self._apply_cursors_for_launch()
        name = self._name_for(pid)
        game = name or f"Place {pid}"
        self.log(f"Запуск: {name or 'игра'} (place {pid})")
        active = self._active_account()
        if active is not None:
            who = active.get("display") or active.get("name") or "аккаунт"
            running = player_pids()
            if running and roblox_window_pids(running):
                # игра/клиент открыты на экране — спрашиваем перед закрытием
                close_it = True
                if self.interactive:
                    close_it = messagebox.askyesno(
                        APP_NAME,
                        f"Roblox уже запущен.\nЗакрыть клиент и запустить "
                        f"игру под аккаунтом «{who}»?",
                        parent=self.root)
                if close_it:
                    self._start_account_launch(pid, name, game, active)
                    return
                self.log("Аккаунт не применён: Roblox уже запущен")
                self.status_var.set("Запуск без смены аккаунта")
            else:
                if running:
                    self.log("Клиент Roblox висит в трее — перезапускаю "
                             "под аккаунтом")
                self._start_account_launch(pid, name, game, active)
                return
        self._launch_plain(pid, name, game)

    def _launch_plain(self, pid, name, game):
        """Обычный запуск ссылкой клиента — без входа под аккаунтом."""
        if self._play_busy:
            return
        self._play_busy = True
        self._btn_state(self.btn_play, "primary", False,
                        text="Запускаю игру…")
        self._rpc_set(game, "Запускаю игру…")
        self.status_var.set(f"Запускаю {name or pid}…")
        self._begin_launch_effect(game or name or "Roblox",
                                  "Передаю ссылку Roblox…")
        player_exe = configured_roblox_exe(self.cfg)
        if player_exe is not None:
            self.log(f"Версия Roblox: запуск {Path(player_exe).parent.name}")
        threading.Thread(target=self._plain_worker,
                         args=(pid, name, game, player_exe),
                         daemon=True).start()

    def _plain_worker(self, pid, name, game, player_exe=None):
        try:
            result = launch_game(pid, player_exe=player_exe)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "attempts": 0, "notes": [],
                      "error": f"Не удалось запустить игру: {exc}"}
        self.q.put(lambda: self._plain_done(pid, name, game, result))

    def _plain_done(self, pid, name, game, result):
        if self._closing:
            return
        self._play_busy = False
        self._btn_state(self.btn_play, "primary", True,
                        text="▶   ИГРАТЬ ПО ID")
        for note in result.get("notes") or []:
            self.log(note)
        if result.get("ok"):
            self.entry_var.set(pid)
            self.log(f"Ссылка передана клиенту "
                     f"(попыток: {result.get('attempts') or 1})")
            self.status_var.set(f"Запускаю {name or pid}…")
            self._end_launch_effect(f"Запускаю {name or pid}…")
            self.root.after(
                10000, lambda g=game: self._rpc_set(g, "Играет в Roblox"))
            self._after_game_launch(pid, name)
            return
        error = result.get("error") or "Не удалось запустить игру."
        self.log(f"Запуск: {error}")
        self.status_var.set("Не удалось запустить игру")
        self._rpc_set("В меню", "Ошибка запуска")
        self._end_launch_effect("Ошибка запуска")
        self._play_error_sound()
        if self.interactive:
            messagebox.showerror(APP_NAME, error, parent=self.root)

    def _start_account_launch(self, pid, name, game, account):
        if self._play_busy:
            return
        self._play_busy = True
        who = account.get("display") or account.get("name") or "аккаунт"
        self._btn_state(self.btn_play, "primary", False,
                        text="Вхожу в аккаунт…")
        self._rpc_set(game, "Подключаю аккаунт…")
        self.status_var.set(f"Вхожу под «{who}»…")
        self.log(f"Запуск под аккаунтом @{account.get('name')} "
                 f"(place {pid})")
        self._begin_launch_effect(game or name or "Roblox",
                                  "Подключаю аккаунт…")
        player_exe = configured_roblox_exe(self.cfg)
        if player_exe is not None:
            self.log(f"Версия Roblox: запуск {Path(player_exe).parent.name} "
                     "под аккаунтом")
        threading.Thread(
            target=self._account_launch_worker,
            args=(pid, name, game, account, player_exe),
            daemon=True).start()

    def _account_launch_worker(self, pid, name, game, account,
                               player_exe=None):
        if player_pids():
            close_roblox_players()
            wait_players_gone(12)
            # клиент отпускает одиночный экземпляр и дописывает сессию
            time.sleep(1.5)
        result = launch_game_as(pid, account["id"], player_exe=player_exe)
        self.q.put(lambda: self._account_launch_done(
            pid, name, game, result))

    def _account_launch_done(self, pid, name, game, result):
        if self._closing:
            return
        self._play_busy = False
        self._btn_state(self.btn_play, "primary", True,
                        text="▶   ИГРАТЬ ПО ID")
        for note in result.get("notes") or []:
            self.log(note)
        account = result.get("account") or {}
        if result.get("ok"):
            who = account.get("name") or "аккаунт"
            self.entry_var.set(pid)
            self.status_var.set(f"Запускаю {name or pid} под @{who}")
            how = {"app": "новый клиент", "legacy": "старый клиент",
                   "exe": "выбранная версия"}.get(
                       result.get("mode"), "клиент")
            self.log(f"Игра запущена под аккаунтом @{who} "
                     f"(place {pid}, {how}, "
                     f"попыток: {result.get('attempts') or 1})")
            self._rpc_set(game, "Играет в Roblox")
            self.root.after(
                10000, lambda g=game: self._rpc_set(g, "Играет в Roblox"))
            self._end_launch_effect(f"Запускаю {name or pid}…")
            self._after_game_launch(pid, name)
            return
        error = result.get("error") or "Не удалось войти под аккаунтом."
        self.log(f"Аккаунт: {error}")
        self.status_var.set("Не удалось войти под аккаунтом")
        self._rpc_set("В меню", "Ошибка аккаунта")
        self._end_launch_effect("Не удалось войти")
        self._play_error_sound()
        if self.interactive and messagebox.askyesno(
                APP_NAME,
                f"{error}\n\nЗапустить игру без аккаунта, как обычно?",
                parent=self.root):
            self._launch_plain(pid, name, game)

    def _after_game_launch(self, pid, name):
        if self.cfg["save_history"]:
            history = [h for h in self.cfg["history"]
                       if h["place_id"] != pid]
            history.insert(0, {"place_id": pid, "name": name,
                               "ts": int(time.time())})
            self.cfg["history"] = history[:30]
        if self.cfg["remember_last"]:
            self.cfg["last_place_id"] = pid
        save_config(self.cfg)
        self._refresh_lists()
        self._start_ingame_helper_after_launch()
        if self.cfg["auto_close"]:
            self.log("Автозакрытие через 1.5 с…")
            self.root.after(1500, self.close)

    # -- оформление ------------------------------------------------------------------------------

    def set_theme(self, name, save=True, custom_name=None):
        if name == "custom":
            target = custom_name or self.cfg.get("custom_theme") or ""
            palette = load_theme_palette(target)
            if palette is None:
                return
            self.theme_name = "custom"
            self.custom_theme_name = target
            self.c = palette
            self.accent = palette.get("accent") or self.accent
            if save:
                self.cfg["theme"] = "custom"
                self.cfg["custom_theme"] = target
                save_config(self.cfg)
            self._rebuild()
            self.status_var.set(f"Тема: {target}")
            if save:
                self.log(f"Своя тема оформления: {target}")
            return
        if name not in THEMES:
            return
        label = THEMES[name]["label"]
        self.theme_name = name
        self.custom_theme_name = ""
        self.c = THEMES[name]
        self.accent = self.cfg["accent"]
        if save:
            self.cfg["theme"] = name
            save_config(self.cfg)
        self._rebuild()
        self.status_var.set(f"Тема: {label}")
        if save:
            self.log(f"Тема оформления: {label}")

    def set_window_buttons(self, style):
        """Стиль кнопок окна: mac (слева) или win (справа)."""
        style = style if style in ("mac", "win") else "mac"
        if self.cfg.get("window_buttons") == style:
            return
        self.cfg["window_buttons"] = style
        save_config(self.cfg)
        self._rebuild()
        label = "macOS" if style == "mac" else "Windows"
        self.status_var.set(f"Кнопки окна: {label}")
        self.log(f"Кнопки окна: {label}")

    def set_accent(self, color):
        color = color.lower()
        if self.theme_name == "custom" and self.c.get("image_only"):
            self.status_var.set(
                "В режиме «только картинка» цвета темы отключены.")
            return
        self.accent = color
        self.cfg["accent"] = color
        if self.theme_name == "custom" and self.custom_theme_name:
            result = update_theme_set(self.custom_theme_name, accent=color)
            if result["ok"]:
                self.c["accent"] = color
                self._theme_preview_cache.clear()
        save_config(self.cfg)
        self.apply_accent(color)
        name = next((n for c, n in ACCENT_PRESETS if c == color), color)
        self.status_var.set(f"Акцент: {name}")
        self.log(f"Акцент интерфейса: {name} ({color})")

    def apply_accent(self, color):
        self.accent = color
        c = self.c
        style = self.style
        style.configure("Primary.TButton", background=color)
        style.map("Primary.TButton",
                  background=[("active", shade(color, 1.12)),
                              ("pressed", shade(color, 0.85))])
        style.map("TEntry",
                  bordercolor=[("focus", color)],
                  lightcolor=[("focus", color)],
                  darkcolor=[("focus", color)])
        style.map("TCombobox", bordercolor=[("focus", color)])
        style.configure("green.Horizontal.TProgressbar",
                        background=color, lightcolor=color, darkcolor=color)

        select_bg, select_fg = self._list_selection()
        for box in (getattr(self, "recent_box", None),
                    getattr(self, "fav_box", None),
                    getattr(self, "hist_box", None),
                    getattr(self, "cursor_sets_box", None),
                    getattr(self, "theme_sets_box", None),
                    getattr(self, "accounts_box", None)):
            if box is None:
                continue
            try:
                box.configure(selectbackground=select_bg,
                              selectforeground=select_fg,
                              highlightcolor=color)
            except tk.TclError:
                pass
        log_text = getattr(self, "log_text", None)
        if log_text is not None:
            try:
                log_text.configure(selectbackground=color,
                                   selectforeground=c["on_accent"],
                                   highlightcolor=color)
            except tk.TclError:
                pass
        for check in getattr(self, "_checks", ()):
            check.redraw()

        base, fg, hot = self._btn_colors("primary")
        for btn in self.primary_buttons:
            try:
                btn.configure(bg=base, fg=fg, activebackground=hot,
                              activeforeground=fg, disabledforeground=fg)
            except tk.TclError:
                pass

        for item in self.nav_buttons.values():
            item.apply_accent(color)

        for w in self.accent_bgs:
            try:
                w.configure(bg=color)
            except tk.TclError:
                pass
        for w in self.accent_fgs:
            try:
                w.configure(fg=color)
            except tk.TclError:
                pass
        for canvas in list(self._header_canvases):
            try:
                self._schedule_header(canvas, delay=10)
            except tk.TclError:
                pass

        dot = getattr(self, "status_dot", None)
        if dot is not None:
            try:
                dot.configure(fg=color)
            except tk.TclError:
                pass
        self._refresh_sidebar_status()
        name_lbl = getattr(self, "accent_name_label", None)
        if name_lbl is not None:
            name = next((n for col, n in ACCENT_PRESETS if col == color),
                        color)
            name_lbl.configure(text=name)

    # -- цикл очереди ------------------------------------------------------------------------------

    def _poll_queue(self):
        if self._closing:
            return
        try:
            while True:
                callback = self.q.get_nowait()
                try:
                    callback()
                except tk.TclError:
                    pass
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.root.after(100, self._poll_queue)
        except tk.TclError:
            self._closing = True

# -- самотест --------------------------------------------------------------------------------------


def run_selftest():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    failures = []

    # 1) разбор ссылок
    cases = [
        ("6900305353", "6900305353"),
        ("  920587237 ", "920587237"),
        ("https://www.roblox.com/games/920587237/Adopt-Me", "920587237"),
        ("https://www.roblox.com/games/start?placeId=920587237", "920587237"),
        ("roblox://experiences/start?placeId=6900305353", "6900305353"),
        ("roblox://placeId=6900305353&launchData=x", "6900305353"),
        ("https://example.com", None),
        ("abc", None),
        ("", None),
    ]
    for raw, want in cases:
        got = parse_place_id(raw)
        if got != want:
            failures.append(
                f"parse_place_id({raw!r}) = {got!r}, ожидалось {want!r}")

    # 2) конфиг: запись, чтение, миграция со старого имени
    try:
        with tempfile.TemporaryDirectory() as td:
            new_path = Path(td) / "config.json"
            old_path = Path(td) / "old.json"
            old_path.write_text(
                json.dumps({"fps_limit": 144, "theme": "glass"}),
                encoding="utf-8")
            if not migrate_old_config(new_path, old_path):
                failures.append("миграция: старый конфиг не перенесён")
            else:
                moved = json.loads(new_path.read_text(encoding="utf-8"))
                if moved.get("fps_limit") != 144:
                    failures.append("миграция: данные потерялись")
                if not old_path.exists():
                    failures.append("миграция: старый конфиг удалился — "
                                    "он должен остаться на месте")
            cfg = load_config(path=new_path)
            if cfg["theme"] not in THEMES:
                failures.append(f"config: тема {cfg['theme']!r} неизвестна")
            if cfg["fps_limit"] != 144:
                failures.append(f"config: fps_limit={cfg['fps_limit']}")
            cfg["fps_limit"] = 0
            if not save_config(cfg, path=new_path):
                failures.append("save_config: не записал временный конфиг")
            if load_config(path=new_path)["fps_limit"] != 0:
                failures.append("config: значение не перечиталось")
            if migrate_old_config(new_path, old_path):
                failures.append("миграция: сработала повторно")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"config test: {exc}")

    # 2b) конфиг: валидация ключей внутриигрового помощника
    try:
        with tempfile.TemporaryDirectory() as td:
            ingame_path = Path(td) / "config.json"
            ingame_path.write_text(json.dumps({
                "ingame_overlay_corner": "xx",
                "ingame_afk_mode": "teleport",
                "ingame_afk_key": "Space!!!",
                "ingame_afk_minutes": 99,
                "ingame_clock_hotkey": "Shift + F4",
            }), encoding="utf-8")
            ingame_cfg = load_config(path=ingame_path)
            expected_ingame = {
                "ingame_overlay_corner": "tr",
                "ingame_afk_mode": "mouse",
                "ingame_afk_key": "space",
                "ingame_afk_minutes": 15,
                "ingame_overlay": True,
                "ingame_afk": False,
                "ingame_clock": False,
                "ingame_clock_hotkey": "shift+f4",
            }
            for key, want in expected_ingame.items():
                if ingame_cfg.get(key) != want:
                    failures.append(
                        f"config in-game: {key}={ingame_cfg.get(key)!r} "
                        f"!= {want!r}")
            ingame_path.write_text(json.dumps({
                "ingame_overlay": 0, "ingame_overlay_start": 1,
                "ingame_auto": None, "ingame_afk": "да",
                "ingame_afk_minutes": "abc",
                "ingame_overlay_corner": "BL",
                "ingame_clock": 1,
                "ingame_clock_hotkey": "!!",
            }), encoding="utf-8")
            ingame_cfg = load_config(path=ingame_path)
            if ingame_cfg["ingame_overlay"] or not ingame_cfg[
                    "ingame_overlay_start"]:
                failures.append(
                    "config in-game: булевы флаги разобраны неверно "
                    f"{ingame_cfg['ingame_overlay']} / "
                    f"{ingame_cfg['ingame_overlay_start']}")
            if not ingame_cfg["ingame_clock"]:
                failures.append("config in-game: флаг часов потерялся")
            if ingame_cfg["ingame_clock_hotkey"] != "shift+f3":
                failures.append(
                    "config in-game: битый бинд часов "
                    f"{ingame_cfg['ingame_clock_hotkey']!r}")
            if ingame_cfg["ingame_afk_minutes"] != 5:
                failures.append(
                    "config in-game: битые минуты "
                    f"{ingame_cfg['ingame_afk_minutes']}")
            if ingame_cfg["ingame_overlay_corner"] != "bl":
                failures.append(
                    "config in-game: регистр угла "
                    f"{ingame_cfg['ingame_overlay_corner']}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"config in-game test: {exc}")

    # 2c) конфиг: стиль кнопок окна
    try:
        with tempfile.TemporaryDirectory() as td:
            wb_path = Path(td) / "config.json"
            wb_path.write_text(json.dumps({"window_buttons": "WIN"}),
                               encoding="utf-8")
            if load_config(path=wb_path)["window_buttons"] != "win":
                failures.append("config: window_buttons=WIN не нормализован")
            wb_path.write_text(json.dumps({"window_buttons": "linux"}),
                               encoding="utf-8")
            if load_config(path=wb_path)["window_buttons"] != "mac":
                failures.append("config: неизвестный window_buttons не сброшен")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"config window buttons test: {exc}")

    cfg_real = load_config()
    if not save_config(cfg_real):
        failures.append("не удалось записать реальный config.json")
    if not CONFIG_PATH.exists():
        failures.append("реальный config.json не создан")

    # 3) темы: полнота ключей
    needed = {"label", "bg", "panel", "surface", "surface2", "hover",
              "entry", "list", "border", "text", "muted", "on_accent",
              "titlebar", "alpha"}
    if THEME_ORDER != sorted(THEME_ORDER, key=THEME_ORDER.index):
        failures.append("порядок тем нарушен")
    for name in THEME_ORDER:
        if name not in THEMES:
            failures.append(f"тема {name} не определена")
            continue
        missing = needed - set(THEMES[name])
        if missing:
            failures.append(f"тема {name}: нет ключей {sorted(missing)}")

    # 4) геометрия безрамочного окна
    checks = [
        (hit_test(500, 300, 900, 600, 6), "", "центр"),
        (hit_test(300, 3, 900, 600, 6), "n", "верх"),
        (hit_test(300, 597, 900, 600, 6), "s", "низ"),
        (hit_test(2, 300, 900, 600, 6), "w", "лево"),
        (hit_test(897, 300, 900, 600, 6), "e", "право"),
        (hit_test(2, 3, 900, 600, 6), "nw", "угол nw"),
        (hit_test(897, 597, 900, 600, 6), "se", "угол se"),
        (hit_test(2, 3, 900, 600, 6, maximized=True), "", "максимизировано"),
    ]
    for got, want, label in checks:
        if got != want:
            failures.append(f"hit_test({label}): {got!r} != {want!r}")

    got = resize_geometry(100, 100, 900, 600, "e", 50, 0, 640, 440)
    if got != (100, 100, 950, 600):
        failures.append(f"resize east: {got}")
    got = resize_geometry(100, 100, 700, 500, "w", 500, 0, 640, 440)
    if got != (160, 100, 640, 500):
        failures.append(f"resize clamp west: {got}")
    got = resize_geometry(0, 0, 900, 500, "n", 0, 100, 640, 440)
    if got != (0, 60, 900, 440):
        failures.append(f"resize clamp north: {got}")
    if not valid_geometry("900x640+10+10") or valid_geometry("bananas"):
        failures.append("valid_geometry: неверные ответы")
    ax, ay, aw, ah = work_area()
    if aw <= 0 or ah <= 0:
        failures.append(f"work_area: {aw}x{ah}")

    # 4b) Discord Rich Presence: ID, activity и кадр IPC
    try:
        good_id = "123456789012345678"
        if valid_discord_app_id(good_id) != good_id:
            failures.append("discord: валидный Application ID отклонён")
        for bad in ("", "abc", "123", good_id + "x"):
            if valid_discord_app_id(bad):
                failures.append(f"discord: принят неверный ID {bad!r}")
        activity = discord_activity("В меню", "Игра не выбрана",
                                    1700000000000)
        if activity.get("details") != "В меню":
            failures.append(f"discord: details {activity}")
        if activity.get("state") != "Игра не выбрана":
            failures.append(f"discord: state {activity}")
        if activity.get("timestamps", {}).get("start") != 1700000000000:
            failures.append("discord: timestamp не попал в activity")
        if len(discord_activity("x" * 300, "y")["details"]) > 128:
            failures.append("discord: details не обрезаны до 128")
        frame = discord_frame(1, {"cmd": "PING"})
        if int.from_bytes(frame[:4], "little") != 1:
            failures.append("discord: op кадра IPC неверный")
        if int.from_bytes(frame[4:8], "little") != len(frame) - 8:
            failures.append("discord: длина кадра IPC неверна")
        if json.loads(frame[8:].decode("utf-8")) != {"cmd": "PING"}:
            failures.append("discord: JSON кадра IPC повреждён")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"discord test: {exc}")

    # 4c) проверка обновлений: версии и ответ GitHub (без сети)
    try:
        if version_tuple("v1.7.0") != (1, 7, 0):
            failures.append("version_tuple: не разобрал v1.7.0")
        if version_tuple("1.10.2") != (1, 10, 2):
            failures.append("version_tuple: не разобрал 1.10.2")
        if version_tuple("") is not None:
            failures.append("version_tuple: пустая строка не None")

        import core as core_module

        original_get = core_module._get_json
        try:
            core_module._get_json = lambda url, timeout=8: [{
                "tag_name": "v99.0.0", "name": "Тест", "html_url": "u",
                "prerelease": False, "draft": False,
                "assets": [
                    {"name": "MaklyxaLauncher.exe",
                     "browser_download_url": "https://x/app.exe",
                     "size": 10, "digest": "sha256:" + "a" * 64},
                    {"name": "MaklyxaLauncher-99.0.0-x64.msi",
                     "browser_download_url": "https://x/app.msi",
                     "size": 20}]}]
            upd = check_launcher_update()
            if upd.get("error") or not upd.get("is_newer"):
                failures.append(f"check_launcher_update: {upd}")
            if upd.get("download") != "https://x/app.exe":
                failures.append(
                    f"check_launcher_update: ассет {upd.get('download')!r}")
            assets = upd.get("assets") or {}
            if (assets.get("exe") or {}).get("sha256") != "a" * 64:
                failures.append("check_launcher_update: sha256 не разобран")
            if (assets.get("msi") or {}).get("size") != 20:
                failures.append("check_launcher_update: размер msi не разобран")

            core_module._get_json = lambda url, timeout=8: [{
                "tag_name": f"v{VERSION}", "html_url": "u", "assets": []}]
            if check_launcher_update().get("is_newer"):
                failures.append("check_launcher_update: текущая версия "
                                "считается новее себя")

            def offline(url, timeout=8):
                raise OSError("offline")

            core_module._get_json = offline
            if not check_launcher_update().get("error"):
                failures.append(
                    "check_launcher_update: ошибка сети не поймана")
        finally:
            core_module._get_json = original_get

        # скачивание обновления с прогрессом и проверкой хеша (file://)
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                src = Path(tmp_dir) / "asset.exe"
                src.write_bytes(b"maklyxa" * 5000)
                dst = Path(tmp_dir) / "out" / "asset.exe"
                digest = core_module.sha256_file(src)
                seen = []
                ok = download_update(
                    src.as_uri(), dst, size=src.stat().st_size, sha256=digest,
                    progress=lambda done, total: seen.append(done))
                if not ok or dst.read_bytes() != src.read_bytes():
                    failures.append("download_update: файл скачан неверно")
                if not seen or seen[-1] != src.stat().st_size:
                    failures.append("download_update: прогресс не дошёл")
                if not update_file_valid(dst, {"size": src.stat().st_size,
                                               "sha256": digest}):
                    failures.append("update_file_valid: свой файл не принят")
                if update_file_valid(dst, {"sha256": "0" * 64}):
                    failures.append("update_file_valid: чужой хеш принят")
                if update_file_name({"name": "../a b.exe"}) != "a_b.exe":
                    failures.append(
                        "update_file_name: имя не очищено")
                bad = Path(tmp_dir) / "bad.exe"
                try:
                    download_update(src.as_uri(), bad, sha256="0" * 64)
                except ValueError:
                    pass
                else:
                    failures.append("download_update: битый хеш не отклонён")
                if bad.exists() or Path(str(bad) + ".part").exists():
                    failures.append("download_update: мусор после ошибки")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"download update test: {exc}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"update check test: {exc}")

    # 5) обнаружение Roblox
    try:
        exe = find_player_exe()
        if exe is not None and not isinstance(exe, Path):
            failures.append(f"find_player_exe: неожиданный тип {type(exe)}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"find_player_exe: {exc}")
    try:
        kind, detail = roblox_status()
        if kind not in ("installed", "bootstrapper", "broken", "missing"):
            failures.append(f"roblox_status: неожиданный статус {kind!r}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"roblox_status: {exc}")
    try:
        pids = player_pids()
        if not isinstance(pids, set):
            failures.append(f"player_pids: неожиданный тип {type(pids)}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"player_pids: {exc}")

    try:
        with tempfile.TemporaryDirectory() as td:
            ver_path = Path(td) / "config.json"
            ver_path.write_text(json.dumps(
                {"roblox_version": "  " + sys.executable + "  "}),
                encoding="utf-8")
            loaded = load_config(path=ver_path)
            if loaded.get("roblox_version") != sys.executable:
                failures.append(
                    "config: roblox_version "
                    f"{loaded.get('roblox_version')!r}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"roblox_version config: {exc}")

    try:
        entries = roblox_version_entries()
        if not isinstance(entries, list):
            failures.append("roblox_version_entries: не список")
        for item in entries:
            if not Path(item["exe"]).is_file():
                failures.append("roblox_version_entries: битый путь")
                break
            if not str(item.get("version") or ""):
                failures.append("roblox_version_entries: пустая версия")
                break
    except Exception as exc:  # noqa: BLE001
        failures.append(f"roblox_version_entries: {exc}")
    try:
        probe = {"roblox_version": sys.executable}
        if selected_roblox_exe(probe) != Path(sys.executable):
            failures.append("selected_roblox_exe: выбор не учтён")
        if configured_roblox_exe({"roblox_version": ""}) is not None:
            failures.append(
                "configured_roblox_exe: авто даёт переопределение")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"selected_roblox_exe: {exc}")

    # 6) FastFlags: слияние во временном файле
    try:
        with tempfile.TemporaryDirectory() as td:
            probe = Path(td) / "ClientAppSettings.json"
            probe.write_text(
                '{"Foreign": "1", "DFIntTaskSchedulerTargetFps": "60"}',
                encoding="utf-8")
            merge_flags_into(probe, {"FFlagDebugGraphicsPreferVulkan": "True"})
            merged = json.loads(probe.read_text(encoding="utf-8"))
            if merged.get("Foreign") != "1":
                failures.append("merge: чужой флаг пропал")
            if merged.get("FFlagDebugGraphicsPreferVulkan") != "True":
                failures.append("merge: наш флаг не записался")
            if "DFIntTaskSchedulerTargetFps" in merged:
                failures.append("merge: старый наш флаг не удалился")
            merge_flags_into(probe, {})
            cleaned = json.loads(probe.read_text(encoding="utf-8"))
            if "FFlagDebugGraphicsPreferVulkan" in cleaned:
                failures.append("merge: сброс не удалил наш флаг")
            if cleaned.get("Foreign") != "1":
                failures.append("merge: сброс удалил чужой флаг")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"merge test: {exc}")

    try:
        values = fastflag_values({"fps_limit": 144,
                                  "graphics_mode": "Vulkan",
                                  "quality_level": 5})
        if values.get(FLAG_FPS) != "144":
            failures.append(f"fastflag_values fps: {values}")
        if values.get("FFlagDebugGraphicsPreferVulkan") != "True":
            failures.append(f"fastflag_values api: {values}")
        if values.get(FLAG_QUALITY) != "5":
            failures.append(f"fastflag_values quality: {values}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"fastflag_values: {exc}")

    # 6b) наборы курсоров: слоты, ZIP, применение с резервной копией, откат
    try:
        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
            "2mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sets_dir = root / "CursorSets"
            created = create_cursor_set(sets_dir=sets_dir)
            if not created["ok"]:
                failures.append(f"курсоры: набор не создан {created}")
            set_name = created["name"]
            if set_name not in list_cursor_sets(sets_dir):
                failures.append("курсоры: набор не появился в списке")

            slot_keys = {slot["key"] for slot in CURSOR_SLOTS}
            for slot in CURSOR_SLOTS:
                source = root / "sources" / slot["file"]
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(tiny_png)
                added = set_cursor_slot(set_name, slot["key"], source,
                                        sets_dir=sets_dir)
                if not added["ok"]:
                    failures.append(
                        f"курсоры: слот {slot['key']} — {added['error']}")
            files = cursor_set_files(set_name, sets_dir)
            if set(files) != slot_keys:
                failures.append(f"курсоры: слоты набора {set(files)}")

            archive = root / "set.zip"
            exported = export_cursor_set(set_name, archive, sets_dir)
            if not exported["ok"] or not archive.exists():
                failures.append(f"курсоры: экспорт {exported}")
            imported = import_cursor_set(archive, "Импорт", sets_dir)
            if not imported["ok"] or imported["files"] != 4:
                failures.append(f"курсоры: импорт {imported}")
            elif set(cursor_set_files(imported["name"], sets_dir)) != slot_keys:
                failures.append("курсоры: импорт потерял слоты")
            else:
                renamed = rename_cursor_set(imported["name"], "Импорт 2",
                                            sets_dir)
                if not renamed["ok"] or "Импорт 2" not in list_cursor_sets(
                        sets_dir):
                    failures.append(f"курсоры: переименование {renamed}")
            if rename_cursor_set("Импорт 2", "bad/name", sets_dir)["ok"]:
                failures.append("курсоры: недопустимое имя принято")

            target_root = root / "game"
            defaults = {}
            for slot in CURSOR_SLOTS:
                target = target_root / slot["target_rel"]
                target.parent.mkdir(parents=True, exist_ok=True)
                defaults[slot["key"]] = b"default-" + slot["file"].encode()
                target.write_bytes(defaults[slot["key"]])
            state = root / "state.json"
            backups = root / "backup"

            result = apply_cursor_set(set_name, [target_root], state,
                                      backups, sets_dir=sets_dir)
            if not result["ok"] or result["copied"] != 4:
                failures.append(f"курсоры: применение набора {result}")
            if cursor_state_info(state) != {"set": set_name, "files": 4}:
                failures.append(f"курсоры: состояние {cursor_state_info(state)}")
            for slot in CURSOR_SLOTS:
                target = target_root / slot["target_rel"]
                if target.read_bytes() != tiny_png:
                    failures.append(f"курсоры: {slot['file']} не заменён")

            copy_name = "Текущие"
            create_cursor_set(copy_name, sets_dir)
            copied_current = copy_current_cursor_set(
                copy_name, [target_root], sets_dir)
            if (not copied_current["ok"]
                    or copied_current["copied"] != 4):
                failures.append(
                    f"курсоры: получить текущий {copied_current}")

            restored = restore_custom_cursors(state, backups)
            if not restored["ok"] or restored["restored"] != 4:
                failures.append(f"курсоры: восстановление {restored}")
            for slot in CURSOR_SLOTS:
                target = target_root / slot["target_rel"]
                if target.read_bytes() != defaults[slot["key"]]:
                    failures.append(
                        f"курсоры: {slot['file']} не восстановлен")
            if not delete_cursor_set("Импорт 2", sets_dir)["ok"]:
                failures.append("курсоры: набор не удалился")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"cursor sets test: {exc}")

    # 6c) свои темы: палитра, картинка, ZIP, переименование, удаление
    try:
        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
            "2mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sets_dir = root / "ThemeSets"
            created = create_theme_set("Зелёная тема", base="dark",
                                       tint="#00c48c", accent="#00c48c",
                                       sets_dir=sets_dir)
            if not created["ok"] or created["name"] != "Зелёная тема":
                failures.append(f"темы: набор не создан {created}")
            name = created["name"]
            if name not in list_theme_sets(sets_dir):
                failures.append("темы: набор не появился в списке")
            if create_theme_set(name, sets_dir=sets_dir)["ok"]:
                failures.append("темы: создан дубликат набора")
            if create_theme_set("bad/name", sets_dir=sets_dir)["ok"]:
                failures.append("темы: недопустимое имя принято")

            palette = load_theme_palette(name, sets_dir)
            if palette is None or palette.get("base") != "dark":
                failures.append(f"темы: палитра {palette}")
            elif not all(isinstance(palette.get(key), str)
                         and palette[key].startswith("#")
                         for key in ("bg", "panel", "surface", "text",
                                     "muted", "accent", "titlebar")):
                failures.append("темы: палитра неполная")

            image = root / "bg.png"
            image.write_bytes(tiny_png)
            added = set_theme_image(name, image, sets_dir)
            if not added["ok"]:
                failures.append(f"темы: картинка не добавилась {added}")
            info = theme_set_info(name, sets_dir)
            if not info["ok"] or info["image"] is None:
                failures.append(f"темы: картинка не видна в теме {info}")
            elif (info["width"], info["height"]) != (1, 1):
                failures.append(f"темы: размер картинки "
                                f"{info['width']}x{info['height']}")
            if (load_theme_palette(name, sets_dir) or {}).get("image") is None:
                failures.append("темы: палитра не отдала картинку")

            frozen = update_theme_set(name, image_only=True,
                                      sets_dir=sets_dir)
            neutral = load_theme_palette(name, sets_dir) or {}
            if not frozen["ok"] or not neutral.get("image_only"):
                failures.append(f"темы: режим «только картинка» {frozen}")
            if neutral.get("accent") != IMAGE_ONLY_ACCENT:
                failures.append(
                    f"темы: нейтральный акцент {neutral.get('accent')}")
            if neutral.get("bg") != IMAGE_ONLY_PALETTE["bg"]:
                failures.append(
                    f"темы: нейтральный фон {neutral.get('bg')}")
            if (neutral.get("stored_base") != "dark"
                    or neutral.get("stored_tint") != "#00c48c"):
                failures.append(
                    "темы: сохранённые цвета потерялись "
                    f"{neutral.get('stored_base')} {neutral.get('stored_tint')}")
            if neutral.get("image") is None:
                failures.append("темы: режим «только картинка» потерял фото")
            update_theme_set(name, base="light", tint="#ff9f43",
                             sets_dir=sets_dir)
            neutral = load_theme_palette(name, sets_dir) or {}
            if (neutral.get("stored_base") != "light"
                    or neutral.get("stored_tint") != "#ff9f43"
                    or neutral.get("accent") != IMAGE_ONLY_ACCENT):
                failures.append(f"темы: цвета в режиме фото {neutral}")
            restored = update_theme_set(name, image_only=False,
                                        sets_dir=sets_dir)
            palette = load_theme_palette(name, sets_dir) or {}
            if not restored["ok"] or palette.get("image_only"):
                failures.append(f"темы: выход из режима фото {restored}")
            if (palette.get("base") != "light"
                    or palette.get("tint") != "#ff9f43"):
                failures.append(
                    f"темы: цвета не вернулись {palette.get('base')} "
                    f"{palette.get('tint')}")

            bad = root / "bad.txt"
            bad.write_text("not an image", encoding="utf-8")
            if set_theme_image(name, bad, sets_dir)["ok"]:
                failures.append("темы: текстовый файл принят за картинку")

            updated = update_theme_set(name, base="light", tint="#ff9f43",
                                       sets_dir=sets_dir)
            if not updated["ok"]:
                failures.append(f"темы: обновление {updated}")
            palette = load_theme_palette(name, sets_dir) or {}
            if palette.get("base") != "light":
                failures.append(f"темы: база не сменилась {palette.get('base')}")
            if palette.get("tint") != "#ff9f43":
                failures.append(f"темы: оттенок не сменился {palette.get('tint')}")
            if palette.get("accent") != "#00c48c":
                failures.append(f"темы: акцент потерялся {palette.get('accent')}")
            if palette.get("image") is None:
                failures.append("темы: обновление потеряло картинку")

            if not remove_theme_image(name, sets_dir)["ok"]:
                failures.append("темы: картинка не удалилась")
            if theme_set_info(name, sets_dir)["image"] is not None:
                failures.append("темы: удалённая картинка осталась в теме")

            archive = root / "theme.zip"
            exported = export_theme_set(name, archive, sets_dir)
            if not exported["ok"] or not archive.exists():
                failures.append(f"темы: экспорт {exported}")
            imported = import_theme_set(archive, "Импорт темы", sets_dir)
            if not imported["ok"] or "Импорт темы" not in list_theme_sets(
                    sets_dir):
                failures.append(f"темы: импорт {imported}")
            else:
                imported_palette = load_theme_palette(
                    imported["name"], sets_dir) or {}
                if imported_palette.get("tint") != "#ff9f43":
                    failures.append("темы: импорт потерял цвета")
                renamed = rename_theme_set(imported["name"], "Импорт 2",
                                           sets_dir)
                if not renamed["ok"]:
                    failures.append(f"темы: переименование {renamed}")
                elif load_theme_palette("Импорт 2", sets_dir) is None:
                    failures.append("темы: после переименования тема пропала")
                if rename_theme_set("Импорт 2", "bad/name", sets_dir)["ok"]:
                    failures.append("темы: недопустимое имя при переименовании")
                if not delete_theme_set("Импорт 2", sets_dir)["ok"]:
                    failures.append("темы: импорт не удалился")
            if not delete_theme_set(name, sets_dir)["ok"]:
                failures.append("темы: набор не удалился")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"theme sets test: {exc}")

    # 6d) аккаунты: DPAPI, активный аккаунт, ссылка запуска
    try:
        secret, error = protect_secret("COOKIE-1")
        if error or not secret.startswith("dpapi:"):
            failures.append(f"аккаунты: сессия не зашифрована ({error})")
        elif unprotect_secret(secret) != "COOKIE-1":
            failures.append("аккаунты: DPAPI не расшифровал сессию")
        if unprotect_secret("broken") != "":
            failures.append("аккаунты: мусор расшифровался в строку")

        with tempfile.TemporaryDirectory() as td:
            acc_path = Path(td) / "accounts.json"
            first = add_account(
                "COOKIE-1",
                profile={"ok": True, "id": 111, "name": "first_login",
                         "display": "First"},
                path=acc_path, fetch_avatar=False)
            if not first["ok"]:
                failures.append(f"аккаунты: первый не добавлен {first}")
            state = accounts_state(acc_path)
            if len(state["accounts"]) != 1:
                failures.append(f"аккаунты: список {state}")
            first_id = first.get("account", {}).get("id", "")
            if state["active"] != first_id:
                failures.append("аккаунты: первый не стал активным")

            second = add_account(
                "COOKIE-2",
                profile={"ok": True, "id": 222, "name": "second_login",
                         "display": "Second"},
                path=acc_path, fetch_avatar=False)
            second_id = second.get("account", {}).get("id", "")
            state = accounts_state(acc_path)
            if len(state["accounts"]) != 2:
                failures.append(f"аккаунты: второй не добавлен {state}")
            if state["active"] != first_id:
                failures.append("аккаунты: активный сменился сам")
            if not set_active_account(second_id, acc_path)["ok"]:
                failures.append("аккаунты: активный не переключился")
            if accounts_state(acc_path)["active"] != second_id:
                failures.append("аккаунты: активный не сохранился")
            if account_session(second_id, acc_path) != "COOKIE-2":
                failures.append("аккаунты: сессия второго не читается")
            if "COOKIE-2" in acc_path.read_text(encoding="utf-8"):
                failures.append("аккаунты: сессия лежит открытым текстом")

            again = add_account(
                "COOKIE-1B",
                profile={"ok": True, "id": 111, "name": "first_login",
                         "display": "First"},
                path=acc_path, fetch_avatar=False)
            if (not again["ok"]
                    or len(accounts_state(acc_path)["accounts"]) != 2):
                failures.append("аккаунты: повторный вход создал дубликат")
            elif account_session(first_id, acc_path) != "COOKIE-1B":
                failures.append("аккаунты: повторный вход не обновил сессию")

            raw = json.loads(acc_path.read_text(encoding="utf-8"))
            for item in raw["accounts"]:
                if item["user_id"] == "222":
                    item["session"] = "broken"
            acc_path.write_text(json.dumps(raw), encoding="utf-8")
            broken = launch_game_as("12345", second_id,
                                    accounts_path=acc_path)
            if broken["ok"] or "повреждена" not in broken["error"]:
                failures.append(f"аккаунты: битая сессия {broken}")

            empty_path = Path(td) / "empty.json"
            missing = launch_game_as("12345", None,
                                     accounts_path=empty_path)
            if missing["ok"] or "Нет добавленных аккаунтов" not in missing[
                    "error"]:
                failures.append(f"аккаунты: пустой запуск {missing}")

            garbage = Path(td) / "garbage.json"
            garbage.write_text(json.dumps({
                "active": "missing",
                "accounts": [
                    {"id": "a1", "user_id": "333", "name": "ok",
                     "display": "Ok", "session": "dpapi:x"},
                    {"id": "a2", "user_id": "not-a-number", "session": "x"},
                    {"id": "a1", "user_id": "444", "session": "x"},
                    {"id": "a3", "user_id": "555"},
                ],
            }), encoding="utf-8")
            cleaned = accounts_state(garbage)
            if (len(cleaned["accounts"]) != 1
                    or cleaned["active"] != "a1"):
                failures.append(f"аккаунты: мусор не вычищен {cleaned}")

            removed = remove_account(second_id, acc_path)
            state = accounts_state(acc_path)
            if (not removed["ok"] or len(state["accounts"]) != 1
                    or state["active"] != first_id):
                failures.append(f"аккаунты: удаление {removed} {state}")

            url = game_launch_url("12345", "TICKET-ABC")
            if not url.startswith(
                    "roblox-player:1+launchmode:play+gameinfo:TICKET-ABC"):
                failures.append(f"аккаунты: ссылка запуска {url[:70]}")
            if "12345" not in url:
                failures.append("аккаунты: place id не попал в ссылку")

            link = roblox_deep_link("12345")
            if link != "roblox://experiences/start?placeId=12345":
                failures.append(f"аккаунты: ссылка клиента {link}")

            if os.name == "nt":
                import core as core_module

                store = Path(td) / "RobloxCookies.dat"
                plain = (
                    "#HttpOnly_.roblox.com\tTRUE\t/\tTRUE\t0\t"
                    ".ROBLOSECURITY\tOLD; #HttpOnly_.roblox.com\tTRUE\t/\t"
                    "FALSE\t0\tGuestData\tUserID=1")
                blob = core_module._dpapi_call(plain.encode("utf-8"), True)
                if not blob:
                    failures.append("сессия клиента: DPAPI недоступен")
                else:
                    store.write_text(json.dumps({
                        "CookiesVersion": "1",
                        "CookiesData": base64.b64encode(blob).decode(
                            "ascii")}), encoding="utf-8")
                    before = read_roblox_cookie_store(store)
                    if before is None or before["cookie"] != "OLD":
                        failures.append(f"сессия клиента: чтение {before}")
                    wrote = set_roblox_session("NEW", store)
                    after = read_roblox_cookie_store(store)
                    if not wrote["ok"] or wrote["changed"] != 1:
                        failures.append(f"сессия клиента: запись {wrote}")
                    if after is None or after["cookie"] != "NEW":
                        failures.append("сессия клиента: cookie не заменился")
                    names = {core_module._cookie_entry_name(item)
                             for item in (after or {}).get("entries", [])}
                    if "GuestData" not in names:
                        failures.append(
                            "сессия клиента: чужие cookie потерялись")
                    same = set_roblox_session("NEW", store)
                    if not same["ok"] or same["changed"] != 0:
                        failures.append(f"сессия клиента: лишняя запись {same}")
                    broken_store = Path(td) / "broken.dat"
                    broken_store.write_text("{oops", encoding="utf-8")
                    if set_roblox_session("X", broken_store)["ok"]:
                        failures.append(
                            "сессия клиента: битый файл перезаписан")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"accounts test: {exc}")

    # 6d2) контроль запуска: журнал клиента и повтор ссылки
    try:
        import core as core_module

        parsed = core_module._log_line_epoch(
            "2026-10-27T14:36:26.633Z,8.633,3e00,6,text")
        want = core_module.datetime(
            2026, 10, 27, 14, 36, 26, 633000,
            tzinfo=core_module.timezone.utc).timestamp()
        if parsed != want:
            failures.append(f"журнал клиента: время строки {parsed}")
        if core_module._log_line_epoch("garbage") is not None:
            failures.append("журнал клиента: мусор принят за время")

        with tempfile.TemporaryDirectory() as td:
            logs = Path(td)
            original_dir = core_module.roblox_log_dir
            core_module.roblox_log_dir = lambda: logs
            try:
                mark = core_module.datetime.now(
                    core_module.timezone.utc).timestamp()

                def _stamp(offset):
                    return core_module.datetime.fromtimestamp(
                        mark + offset,
                        core_module.timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

                log = logs / "player.log"
                log.write_text(
                    f"{_stamp(-600)},1.0,1,6 [FLog::GameJoinLoadTime] "
                    "Report game_join_loadtime: placeid:1, userid:1, \n",
                    encoding="utf-8")
                if core_module.wait_for_join(mark, 0.1) != "timeout":
                    failures.append("журнал клиента: старая строка засчитана")
                log.write_text(
                    f"{_stamp(0)},2.0,1,6 [DFLog::HttpTraceError] "
                    "HttpResponse error:5 message:HttpError: Timedout url:{ "
                    "\"https://gamejoin.roblox.com/v1/join-game\" }\n",
                    encoding="utf-8")
                if core_module.wait_for_join(mark, 0.1) != "failed":
                    failures.append("журнал клиента: сбой входа не замечен")
                with log.open("a", encoding="utf-8") as fh:
                    fh.write(
                        f"{_stamp(0)},3.0,1,6 [FLog::GameJoinLoadTime] "
                        "Report game_join_loadtime: placeid:1, userid:1, \n")
                if core_module.wait_for_join(mark, 0.1) != "joined":
                    failures.append("журнал клиента: вход не замечен")

                sent = []
                closed = []
                original_start = core_module.os.startfile
                original_pids = core_module.player_pids
                original_wait = core_module.wait_for_new_players
                original_join = core_module.wait_for_join
                original_windows = core_module.roblox_window_pids
                original_close = core_module.close_roblox_players
                original_gone = core_module.wait_players_gone
                core_module.os.startfile = sent.append
                core_module.player_pids = lambda: set()
                core_module.wait_for_new_players = (
                    lambda before, seconds, settle=0: False)
                core_module.wait_for_join = lambda mark, seconds: "failed"
                try:
                    failed = core_module._drive_launch("roblox://x", tries=2)
                finally:
                    core_module.os.startfile = original_start
                    core_module.player_pids = original_pids
                    core_module.wait_for_new_players = original_wait
                    core_module.wait_for_join = original_join
                if failed["ok"] or failed["attempts"] != 2:
                    failures.append(f"повтор запуска: {failed}")
                if len(sent) != 2:
                    failures.append(f"повтор запуска: ссылок {len(sent)}")
                if len(failed["notes"]) != 2:
                    failures.append(
                        f"повтор запуска: заметки {failed['notes']}")

                # клиент открыт на экране — ссылка отдаётся сразу
                core_module.os.startfile = sent.append
                core_module.player_pids = lambda: {777}
                core_module.roblox_window_pids = lambda *a: {777}
                core_module.wait_for_join = lambda mark, seconds: "failed"
                try:
                    opened = core_module._drive_launch("roblox://x", tries=2)
                finally:
                    core_module.os.startfile = original_start
                    core_module.player_pids = original_pids
                    core_module.roblox_window_pids = original_windows
                    core_module.wait_for_join = original_join
                if not opened["ok"] or opened["attempts"] != 1:
                    failures.append(f"повтор запуска: открытый клиент {opened}")

                # клиент в трее: ссылок не принимает — закрывается до запуска
                sent.clear()
                core_module.os.startfile = sent.append
                core_module.player_pids = lambda: {777}
                core_module.roblox_window_pids = lambda *a: set()
                core_module.wait_for_join = lambda mark, seconds: "joined"
                core_module.close_roblox_players = (
                    lambda: closed.append(True))
                core_module.wait_players_gone = lambda *a, **k: True
                try:
                    tray = core_module._drive_launch("roblox://x", tries=2)
                finally:
                    core_module.os.startfile = original_start
                    core_module.player_pids = original_pids
                    core_module.roblox_window_pids = original_windows
                    core_module.wait_for_join = original_join
                    core_module.close_roblox_players = original_close
                    core_module.wait_players_gone = original_gone
                if not tray["ok"] or tray["attempts"] != 1:
                    failures.append(f"повтор запуска: трей {tray}")
                if len(sent) != 1 or len(closed) != 1:
                    failures.append(
                        f"повтор запуска: трей ссылок {len(sent)}, "
                        f"закрытий {len(closed)}")
                if len(tray["notes"]) != 1:
                    failures.append(
                        f"повтор запуска: трей заметки {tray['notes']}")
            finally:
                core_module.roblox_log_dir = original_dir
    except Exception as exc:  # noqa: BLE001
        failures.append(f"проверка запуска: {exc}")

    # 6e) стиль запуска: иконка, звук, ярлыки с резервной копией
    try:
        import core as core_module
        import wave

        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
            "2mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "icon.png"
            if ICON_PNG.exists():
                source.write_bytes(ICON_PNG.read_bytes())
            else:
                source.write_bytes(tiny_png)
            icon = root / "Launch" / "icon.ico"
            preview = root / "Launch" / "icon_preview.png"
            added = import_launch_icon(source, icon, preview)
            if not added["ok"] or not icon.exists() or not preview.exists():
                failures.append(f"стиль: иконка не импортирована {added}")
            elif icon.stat().st_size <= 0:
                failures.append("стиль: ICO записан пустым")
            if not launch_icon_state(icon, preview)["custom"]:
                failures.append("стиль: состояние иконки неверное")
            bad_icon = root / "bad.png"
            bad_icon.write_text("not an image", encoding="utf-8")
            if import_launch_icon(bad_icon, icon, preview)["ok"]:
                failures.append("стиль: текст принят за иконку")
            removed_icon = remove_launch_icon(icon, preview)
            if not removed_icon["ok"] or removed_icon["removed"] != 2:
                failures.append(f"стиль: иконка не удалилась {removed_icon}")

            sound_dir = root / "Launch"
            wav = root / "sound.wav"
            with wave.open(str(wav), "wb") as fh:
                fh.setnchannels(1)
                fh.setsampwidth(2)
                fh.setframerate(8000)
                fh.writeframes(b"\x00\x00" * 800)
            added_sound = import_launch_sound(wav, sound_dir)
            if (not added_sound["ok"]
                    or launch_sound_path(sound_dir) is None):
                failures.append(f"стиль: звук не импортирован {added_sound}")
            else:
                played = play_launch_sound(added_sound["path"])
                stop_launch_sound()
                if not played and os.name == "nt":
                    print("  note: MCI не проиграл тестовый WAV — пропуск")
            bad_sound = root / "bad.mp3"
            bad_sound.write_bytes(b"not a sound")
            if import_launch_sound(bad_sound, sound_dir)["ok"]:
                failures.append("стиль: мусор принят за звук")
            removed_sound = remove_launch_sound(sound_dir)
            if not removed_sound["ok"] or removed_sound["removed"] < 1:
                failures.append(f"стиль: звук не удалился {removed_sound}")

            error_sound = import_launch_sound(wav, sound_dir, role="error")
            if (not error_sound["ok"]
                    or launch_sound_path(sound_dir, role="error") is None):
                failures.append(
                    f"стиль: звук ошибки не импортирован {error_sound}")
            elif launch_sound_path(sound_dir) is not None:
                failures.append("стиль: звук ошибки попал в роль запуска")
            removed_error = remove_launch_sound(sound_dir, role="error")
            if not removed_error["ok"] or removed_error["removed"] < 1:
                failures.append(
                    f"стиль: звук ошибки не удалился {removed_error}")

            if os.name == "nt":
                lnk = root / "Roblox Test.lnk"
                made = core_module._run_powershell(
                    "[Console]::OutputEncoding=[Text.Encoding]::UTF8\n"
                    "$s=New-Object -ComObject WScript.Shell\n"
                    f"$l=$s.CreateShortcut('{lnk}')\n"
                    "$l.TargetPath='C:\\Windows\\notepad.exe'\n"
                    "$l.IconLocation='C:\\Windows\\notepad.exe,0'\n"
                    "$l.Save()\n")
                if not made["ok"] or not lnk.exists():
                    print("  note: тестовый ярлык не создан — "
                          "проверка ярлыков пропущена")
                else:
                    found = core_module.find_roblox_shortcuts([root])
                    if not found["ok"] or not found["items"]:
                        failures.append(f"стиль: ярлык не найден {found}")
                    else:
                        original = lnk.read_bytes()
                        shortcut_icon = root / "Launch" / "shortcut.ico"
                        import_launch_icon(source, shortcut_icon, preview)
                        state = root / "state.json"
                        backups = root / "backup"
                        applied = core_module.apply_launch_icon_to_shortcuts(
                            [root], shortcut_icon, state, backups)
                        if not applied["ok"] or applied["changed"] != 1:
                            failures.append(
                                f"стиль: иконка ярлыка не применена {applied}")
                        elif core_module.shortcut_backup_count(state) != 1:
                            failures.append(
                                "стиль: резервная копия ярлыка не создана")
                        check = core_module._run_powershell(
                            "[Console]::OutputEncoding="
                            "[Text.Encoding]::UTF8\n"
                            "$s=New-Object -ComObject WScript.Shell\n"
                            f"$l=$s.CreateShortcut('{lnk}')\n"
                            "Write-Output $l.IconLocation\n")
                        if str(shortcut_icon).lower() not in \
                                check["output"].lower():
                            failures.append(
                                "стиль: иконка не попала в ярлык "
                                f"{check['output']!r}")
                        restored = (
                            core_module.restore_launch_icon_shortcuts(
                                state, backups))
                        if (not restored["ok"]
                                or restored["restored"] != 1):
                            failures.append(
                                f"стиль: ярлык не восстановлен {restored}")
                        elif lnk.read_bytes() != original:
                            failures.append(
                                "стиль: ярлык отличается от исходного")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"launch style test: {exc}")

    # 6f) свой шрифт лаунчера: разбор TTF, импорт, подключение, откат
    try:
        import core as core_module

        fonts_root = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        test_font = next(
            (fonts_root / name for name in
             ("arial.ttf", "segoeui.ttf", "calibri.ttf", "comic.ttf")
             if (fonts_root / name).is_file()), None)
        if test_font is None:
            print("  note: системный TTF не найден — тест шрифта пропущен")
        else:
            with tempfile.TemporaryDirectory() as td:
                base = Path(td) / "Fonts"
                info = inspect_ui_font(test_font)
                if not info["ok"] or not info["families"]:
                    failures.append(f"шрифт: не разобран {info}")
                else:
                    if not info["family"]:
                        failures.append("шрифт: пустое имя семейства")
                    imported = import_ui_font(test_font, base)
                    path = imported.get("path")
                    if (not imported["ok"] or path is None
                            or not Path(path).is_file()):
                        failures.append(f"шрифт: не импортирован {imported}")
                    else:
                        state = ui_font_state(base)
                        if not state["custom"] or state["path"] != Path(path):
                            failures.append(f"шрифт: состояние {state}")
                        if os.name == "nt":
                            if not load_private_font(path):
                                failures.append(
                                    "шрифт: Windows не подключил TTF")
                            else:
                                unload_private_font(path)
                        removed = remove_ui_font(base)
                        if not removed["ok"] or removed["removed"] != 1:
                            failures.append(f"шрифт: не удалился {removed}")
                        if ui_font_state(base)["custom"]:
                            failures.append(
                                "шрифт: файл остался после удаления")
                bad_font = Path(td) / "bad.ttf"
                bad_font.write_text("not a font", encoding="utf-8")
                if inspect_ui_font(bad_font)["ok"]:
                    failures.append("шрифт: мусор принят за TTF")
                huge = Path(td) / "huge.ttf"
                with huge.open("wb") as fh:
                    fh.truncate(21 * 1024 * 1024)
                if inspect_ui_font(huge)["ok"]:
                    failures.append("шрифт: файл больше 20 МБ принят")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"ui font test: {exc}")

    # 6g) галерея: разбор снимков, сессии, группировка, кэш, миниатюры
    try:
        import core as core_module
        from datetime import datetime as _dt

        moment = core_module.parse_screenshot_time(
            "RobloxScreenShot20240102_030405678.png")
        expected = _dt(2024, 1, 2, 3, 4, 5, 678000).timestamp()
        if moment is None or abs(moment - expected) > 0.5:
            failures.append(f"галерея: время из имени {moment} != {expected}")
        if core_module.parse_screenshot_time(
                "RobloxScreenShot20241340_030405000.png", 123.0) != 123.0:
            failures.append("галерея: битая дата не дала fallback")
        if core_module.parse_screenshot_time("случайный.png") is not None:
            failures.append("галерея: имя без даты дало время")

        with tempfile.TemporaryDirectory(prefix="maklyxa-gallery-") as td:
            base = Path(td)
            shots_dir = base / "Pictures" / "Roblox"
            shots_dir.mkdir(parents=True)
            first = shots_dir / "RobloxScreenShot20240102_030405678.png"
            second = shots_dir / "RobloxScreenShot20240103_030405678.png"
            legacy = shots_dir / "старый.png"
            ignored = shots_dir / "note.txt"
            for path in (first, second, legacy, ignored):
                path.write_bytes(b"png")
            os.utime(legacy, (1_000_000_000, 1_000_000_000))
            found = core_module.scan_screenshots(shots_dir)
            if [item["name"] for item in found] != [
                    second.name, first.name, legacy.name]:
                failures.append(
                    "галерея: порядок/фильтр снимков "
                    f"{[item['name'] for item in found]}")
            elif abs(found[-1]["ts"] - 1_000_000_000) > 3:
                failures.append(f"галерея: fallback mtime {found[-1]['ts']}")
            if core_module.scan_screenshots(base / "нет-папки"):
                failures.append("галерея: несуществующая папка дала снимки")
            history = base / "Froststrap" / "Cache" / "GameHistory.json"
            history.parent.mkdir(parents=True)
            history.write_text(json.dumps([{
                "placeId": 920587237,
                "servers": [{
                    "joinedAt": "2026-10-21T10:00:00Z",
                    "timeLeft": "2026-10-21T10:30:00Z",
                }],
            }]), encoding="utf-8")
            history_sessions = core_module._sessions_from_history_file(history)
            started = _dt.fromisoformat(
                "2026-10-21T10:00:00+00:00").timestamp()
            if (len(history_sessions) != 1
                    or history_sessions[0]["place_id"] != "920587237"
                    or abs(history_sessions[0]["start"] - started) > 0.5
                    or history_sessions[0]["source"] != "Froststrap"):
                failures.append(
                    f"галерея: история Froststrap {history_sessions}")

            log = base / "Bloxstrap_20261020.log"
            log.write_text(
                "2026-10-20T10:00:00.000Z  Joining Game (111/abc)\n"
                "2026-10-20T10:15:00.000Z  Joining Game (222/def)\n",
                encoding="utf-8")
            log_sessions = core_module._sessions_from_bloxstrap_log(log)
            if ([item["place_id"] for item in log_sessions] != ["111", "222"]
                    or abs(log_sessions[0]["end"]
                           - log_sessions[1]["start"]) > 0.5):
                failures.append(f"галерея: лог Bloxstrap {log_sessions}")

            merged = core_module._merge_sessions([
                {"start": 1000, "end": 1100, "place_id": "1",
                 "source": "a", "name": ""},
                {"start": 1200, "end": 1300, "place_id": "1",
                 "source": "b", "name": "Склеенный"},
                {"start": 2000, "end": 2100, "place_id": "1",
                 "source": "a", "name": ""},
            ])
            if (len(merged) != 2 or merged[0]["end"] != 1300
                    or merged[0]["name"] != "Склеенный"):
                failures.append(f"галерея: склейка сессий {merged}")

            launcher_sessions = core_module.collect_play_sessions(
                [{"place_id": "333333", "name": "[TITLE] Служебное",
                  "ts": 1_700_000_000},
                 {"place_id": "444444", "name": "Моя игра",
                  "ts": 1_700_010_000}],
                history_files=[], log_files=[])
            if len(launcher_sessions) != 2:
                failures.append(
                    f"галерея: история лаунчера {launcher_sessions}")
            else:
                first_launch = core_module.find_session(
                    1_700_000_000, launcher_sessions)
                second_launch = core_module.find_session(
                    1_700_010_000, launcher_sessions)
                if (not first_launch or first_launch["name"] != ""
                        or first_launch["source"] != "Лаунчер"):
                    failures.append(
                        f"галерея: сессия лаунчера {first_launch}")
                if (not second_launch
                        or second_launch["name"] != "Моя игра"):
                    failures.append(
                        f"галерея: имя из истории {second_launch}")

            boundary = [{"start": 1000.0, "end": 2000.0, "place_id": "7",
                         "source": "t", "name": ""}]
            if core_module.find_session(820.0, boundary) is None:
                failures.append("галерея: grace до начала сессии не работает")
            if core_module.find_session(819.0, boundary) is not None:
                failures.append("галерея: снимок вне grace привязан")
            if core_module.find_session(2180.0, boundary) is None:
                failures.append("галерея: grace после сессии не работает")
            if core_module.find_session(2181.0, boundary) is not None:
                failures.append("галерея: снимок после grace привязан")
            cache_path = base / "Gallery" / "cache.json"
            cache = {"version": 1,
                     "places": {"111": {"name": "Тестовый плейс",
                                        "creator": "Автор",
                                        "checked": 42}},
                     "assignments": {"shot.png": "111"}}
            if not core_module.gallery_cache_save(cache, cache_path):
                failures.append("галерея: кэш не сохранился")
            loaded = core_module.gallery_cache_load(cache_path)
            if (loaded["places"].get("111", {}).get("name")
                    != "Тестовый плейс"
                    or loaded["assignments"].get("shot.png") != "111"):
                failures.append(f"галерея: кэш прочитан неверно {loaded}")
            cache_path.write_text("не json", encoding="utf-8")
            broken = core_module.gallery_cache_load(cache_path)
            if broken["places"] or broken["assignments"]:
                failures.append(f"галерея: битый кэш не обнулён {broken}")

            thumb_a = core_module.gallery_thumb_path(
                {"path": "C:/shots/a.png", "mtime": 10})
            thumb_b = core_module.gallery_thumb_path(
                {"path": "C:/shots/a.png", "mtime": 11})
            if (thumb_a.suffix != ".jpg" or thumb_a == thumb_b
                    or thumb_a != core_module.gallery_thumb_path(
                        {"path": "C:/shots/a.png", "mtime": 10})):
                failures.append("галерея: путь миниатюры нестабилен")

            seed = {"version": 1, "places": {}, "assignments": {}}
            core_module.gallery_seed_places(seed, [
                {"place_id": "555", "name": "Из истории", "source": "x"}])
            if seed["places"].get("555", {}).get("name") != "Из истории":
                failures.append(f"галерея: сид плейсов {seed['places']}")
            if core_module.gallery_assign(seed, ["x.png"], "abc"):
                failures.append("галерея: назначен нечисловой Place ID")
            if core_module.gallery_assign(
                    seed, ["x.png", "y.png"], "920587237") != 2:
                failures.append("галерея: назначение снимков не сработало")
            if seed["assignments"].get("y.png") != "920587237":
                failures.append(
                    f"галерея: назначения {seed['assignments']}")

            sessions = [
                {"start": 1_700_000_000, "end": 1_700_003_600,
                 "place_id": "111", "source": "t", "name": ""},
                {"start": 1_700_100_000, "end": 1_700_103_600,
                 "place_id": "222", "source": "t", "name": ""},
                {"start": 1_700_200_000, "end": 1_700_203_600,
                 "place_id": "333", "source": "t", "name": ""},
            ]
            group_cache = {
                "version": 1,
                "places": {
                    "111": {"name": "Zeta", "creator": "", "checked": 0},
                    "222": {"name": "Alpha", "creator": "Автор",
                            "checked": 0},
                },
                "assignments": {"assigned.png": "222"},
            }
            group_shots = [
                {"path": "a1.png", "name": "a1.png", "ts": 1_700_000_100,
                 "mtime": 1},
                {"path": "a2.png", "name": "a2.png", "ts": 1_700_000_200,
                 "mtime": 2},
                {"path": "b1.png", "name": "b1.png", "ts": 1_700_100_100,
                 "mtime": 3},
                {"path": "c1.png", "name": "c1.png", "ts": 1_700_200_100,
                 "mtime": 4},
                {"path": "assigned.png", "name": "assigned.png",
                 "ts": 1_700_000_300, "mtime": 5},
                {"path": "old.png", "name": "old.png", "ts": 1_600_000_000,
                 "mtime": 6},
            ]
            groups = core_module.build_gallery_groups(
                group_shots, sessions, group_cache)
            titles = [group["title"] for group in groups]
            if titles[:3] != ["Alpha", "Place 333", "Zeta"]:
                failures.append(f"галерея: порядок групп {titles}")
            if len(groups) == 4 and groups[-1]["kind"] == "date":
                alpha = next(
                    group for group in groups if group["place_id"] == "222")
                zeta = next(
                    group for group in groups if group["place_id"] == "111")
                if [shot["name"] for shot in alpha["shots"]] != [
                        "b1.png", "assigned.png"]:
                    failures.append(
                        "галерея: ручное назначение не сработало "
                        f"{[shot['name'] for shot in alpha['shots']]}")
                if [shot["name"] for shot in zeta["shots"]] != [
                        "a2.png", "a1.png"]:
                    failures.append(
                        "галерея: сортировка снимков "
                        f"{[shot['name'] for shot in zeta['shots']]}")
                if (alpha["subtitle"] != "от Автор"
                        or zeta["subtitle"] != "Place ID 111"):
                    failures.append("галерея: подписи плейсов неверные")
                stats = core_module.gallery_stats(groups)
                if stats != {"total": 6, "known": 5, "unknown": 1,
                             "groups": 4}:
                    failures.append(f"галерея: статистика {stats}")

            resolve_cache = {
                "version": 1,
                "places": {"777": {"name": "", "creator": "",
                                   "checked": 0}},
                "assignments": {},
            }
            calls = []

            def fake_game_info(place_id, timeout=8):
                calls.append((str(place_id), timeout))
                if str(place_id) == "777":
                    return {"name": "Из API", "creator": "Автор API"}
                return None

            original_fetch = core_module.fetch_game_info
            try:
                core_module.fetch_game_info = fake_game_info
                result = core_module.resolve_gallery_places(
                    resolve_cache, ["777", "888", "777"], timeout=1)
                second_resolve = core_module.resolve_gallery_places(
                    resolve_cache, ["777", "888"], timeout=1)
            finally:
                core_module.fetch_game_info = original_fetch
            if (result != {"attempted": 2, "resolved": 1}
                    or calls != [("777", 1), ("888", 1)]):
                failures.append(
                    f"галерея: API-названия {result} / {calls}")
            if second_resolve["attempted"] != 0:
                failures.append(
                    f"галерея: повторный запрос к API {second_resolve}")
            if resolve_cache["places"]["777"]["name"] != "Из API":
                failures.append("галерея: название из API не сохранено")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"gallery test: {type(exc).__name__}: {exc}")

    # 6h) внутриигровой помощник: геометрия, AFK, статус
    try:
        import ingame as ingame_module

        geometry_cases = [
            ((100, 40, 1700, 940), (320, 160), "tl", (116, 56)),
            ((100, 40, 1700, 940), (320, 160), "tr", (1364, 56)),
            ((100, 40, 1700, 940), (320, 160), "bl", (116, 764)),
            ((100, 40, 1700, 940), (320, 160), "br", (1364, 764)),
            ((0, 0, 100, 80), (320, 160), "br", (4, 4)),
            ((0, 0, 100, 80), (320, 160), "tl", (4, 4)),
            ((0, 0, 1280, 720), (320, 160), "nope", (944, 544)),
        ]
        for rect, size, corner, want in geometry_cases:
            got = ingame_module.overlay_origin(rect, size, corner)
            if got != want:
                failures.append(
                    f"overlay_origin({corner}): {got} != {want}")

        countdown_cases = [
            (65, "1:05"), (5, "0:05"), (0, "0:00"), (-8, "0:00"),
            (3661, "1:01:01"), (None, "0:00"),
        ]
        for seconds, want in countdown_cases:
            got = ingame_module.format_countdown(seconds)
            if got != want:
                failures.append(
                    f"format_countdown({seconds!r}): {got!r} != {want!r}")

        hotkey_cases = [
            ("Shift+F1", "shift+f1"),
            ("Ctrl + Shift + H", "ctrl+shift+h"),
            ("alt+ctrl+o", "ctrl+alt+o"),
            ("win+space", "win+space"),
            ("F12", "f12"),
            ("shift", None),
            ("ctrl+shift", None),
            ("ctrl+nope", None),
            ("", None),
        ]
        for raw, want in hotkey_cases:
            got = ingame_module.normalize_hotkey(raw)
            if got != want:
                failures.append(
                    f"normalize_hotkey({raw!r}): {got!r} != {want!r}")
        if ingame_module.hotkey_label("ctrl+shift+h") != "Ctrl+Shift+H":
            failures.append("hotkey_label: Ctrl+Shift+H")
        if ingame_module.hotkey_parts("shift+f1") != (
                ingame_module.MOD_SHIFT, 0x70):
            failures.append("hotkey_parts: shift+f1")
        if ingame_module.key_from_keysym("Prior") != "pageup":
            failures.append("key_from_keysym: Prior")
        if ingame_module.key_from_keysym("Control_L") is not None:
            failures.append("key_from_keysym: модификатор не отброшен")
        if not isinstance(ingame_module.pressed_hotkey_modifiers(), list):
            failures.append("pressed_hotkey_modifiers: не список")

        if ingame_module.afk_interval_seconds(5, jitter=False) != 300:
            failures.append("afk_interval: 5 минут != 300 с")
        if ingame_module.afk_interval_seconds(1, jitter=False) != 60:
            failures.append("afk_interval: 1 минута != 60 с")
        if ingame_module.afk_interval_seconds(99, jitter=False) != 900:
            failures.append("afk_interval: 99 минут не ограничены 15")
        for _ in range(40):
            value = ingame_module.afk_interval_seconds(1)
            if not (55.2 <= value <= 64.8):
                failures.append(f"afk_interval: джиттер {value}")
                break
        for key, want in (("space", 0x20), ("Z", 0x5A), ("x", 0x58),
                          ("unknown", 0x20), (None, 0x20)):
            got = ingame_module.afk_vk(key)
            if got != want:
                failures.append(f"afk_vk({key!r}): {got} != {want}")
        for key in ("mouse", "key"):
            if ingame_module.AFK_MODE_BY_LABEL[
                    ingame_module.AFK_MODE_LABELS[key]] != key:
                failures.append(f"AFK_MODE_BY_LABEL: {key}")
        for key in ("space", "shift", "z", "x", "c", "v", "b"):
            if ingame_module.AFK_KEY_BY_LABEL[
                    ingame_module.AFK_KEY_LABELS[key]] != key:
                failures.append(f"AFK_KEY_BY_LABEL: {key}")
        for key in ("tl", "tr", "bl", "br"):
            if ingame_module.CORNER_BY_LABEL[
                    ingame_module.CORNER_LABELS[key]] != key:
                failures.append(f"CORNER_BY_LABEL: {key}")
        if ingame_module.FPS_NOTE != "кадры игры":
            failures.append(f"FPS_NOTE: {ingame_module.FPS_NOTE!r}")
        fps_cases = [
            ([], 0.5, None),
            ([(0.0, 100)], 0.5, None),
            ([(0.0, 100), (0.4, 140)], 0.5, None),
            ([(0.0, 100), (1.0, 160)], 0.5, 60),
            ([(0.0, 160), (1.0, 100)], 0.5, None),
            ([(0.0, 0), (1.0, 5000)], 0.5, None),
            ([(0.0, 0), (1.0, 0)], 0.5, 0),
            ([(0.0, 10), (0.45, 40), (0.95, 70)], 0.9, 63),
        ]
        for samples, span, want in fps_cases:
            got = ingame_module.fps_from_samples(samples, min_span=span)
            if got != want:
                failures.append(
                    f"fps_from_samples({samples!r}): {got!r} != {want!r}")
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            struct_size = ctypes.sizeof(
                ingame_module._D3dkmtQueryStatistics)
            if struct_size != 0x328:
                failures.append(
                    f"WDDM: размер структуры {struct_size} != 0x328")
            if os.name == "nt" and not ingame_module._FPS_NATIVE:
                failures.append("WDDM: счётчик FPS не инициализировался")

        with tempfile.TemporaryDirectory(prefix="maklyxa-ingame-") as td:
            status_file = Path(td) / "status.json"
            if not ingame_module.write_status(
                    {"running": True, "overlay": True,
                     "hotkey": True}, path=status_file):
                failures.append("статус: не записался")
            loaded = ingame_module.read_status(
                path=status_file, max_age=60)
            if (not loaded or loaded.get("running") is not True
                    or loaded.get("overlay") is not True
                    or "updated" not in loaded):
                failures.append(f"статус: прочитан неверно {loaded}")
            if ingame_module.read_status(
                    path=status_file, max_age=-1) is not None:
                failures.append("статус: устаревшие данные не отсечены")
            status_file.write_text(json.dumps(
                {"updated": time.time() - 300, "running": True}),
                encoding="utf-8")
            if ingame_module.read_status(
                    path=status_file, max_age=20) is not None:
                failures.append("статус: старый файл не отсечён по возрасту")
            status_file.write_text("не json", encoding="utf-8")
            if ingame_module.read_status(path=status_file) is not None:
                failures.append("статус: битый JSON не проигнорирован")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"ingame test: {type(exc).__name__}: {exc}")

    # 7) пути записи флагов (регрессия на неверный путь Roblox/…)
    try:
        for path in client_settings_paths():
            if path.name != "ClientAppSettings.json":
                failures.append(f"flags path: странное имя {path}")
            if len(path.parts) >= 2 and path.parts[-2] == "Roblox":
                failures.append(f"flags path: нет подпапки ClientSettings — {path}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"client_settings_paths: {exc}")

    # 8) бутстраперы
    try:
        bkind, bpath = find_bootstrapper_settings()
        if bkind is not None and not (
                isinstance(bpath, Path) and bpath.exists()):
            failures.append(f"bootstrapper config: {bkind} {bpath}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"find_bootstrapper_settings: {exc}")
    try:
        regions = datacenter_regions()
        if not regions or regions[0] != "Auto":
            failures.append(f"datacenter_regions: {regions[:3]}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"datacenter_regions: {exc}")

    # 9) одноэкземплярность (если экземпляр уже жив — просто пропускаем)
    try:
        mutex = acquire_single_instance()
        if mutex is False:
            print("  note: экземпляр уже запущен — mutex-тест пропущен")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"acquire_single_instance: {exc}")

    # 10) UI: построение, все темы (пересборка), безрамочное окно
    try:
        import core as core_module

        ui_tmp = tempfile.TemporaryDirectory(prefix="maklyxa-ui-")
        ui_data = Path(ui_tmp.name)
        core_module.THEME_SETS_DIR = ui_data / "ThemeSets"
        core_module.ACCOUNTS_PATH = ui_data / "accounts.json"
        core_module.AVATARS_DIR = ui_data / "avatars"
        core_module.CONFIG_PATH = ui_data / "config.json"
        core_module.UI_FONT_DIR = ui_data / "Customize" / "Fonts"
        ui_launch = ui_data / "Launch"
        core_module.LAUNCH_DIR = ui_launch
        core_module.LAUNCH_ICON_PATH = ui_launch / "icon.ico"
        core_module.LAUNCH_ICON_PREVIEW = ui_launch / "icon_preview.png"
        core_module.LAUNCH_SHORTCUT_BACKUP_DIR = ui_launch / "shortcuts_backup"
        core_module.LAUNCH_SHORTCUT_BACKUP_PATH = ui_launch / "shortcuts.json"
        core_module.GALLERY_DIR = ui_data / "Gallery"
        core_module.GALLERY_CACHE_PATH = (
            core_module.GALLERY_DIR / "cache.json")
        core_module.GALLERY_THUMBS_DIR = (
            core_module.GALLERY_DIR / "Thumbnails")
        core_module.SCREENSHOTS_DIR = ui_data / "Pictures" / "Roblox"
        core_module.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        root = tk.Tk()
        root.withdraw()
        app = App(root, interactive=False)
        root.update_idletasks()
        root.update()

        for page_key in ("game", "accounts", "favorites", "gallery",
                         "ingame", "flags", "cursors", "themes",
                         "launchstyle", "settings", "log"):
            app.show_page(page_key)
            root.update()
            if app.current_page != page_key:
                failures.append(f"show_page({page_key}) не сработал")
        if not hasattr(app, "var_discord_id"):
            failures.append("UI: нет поля Discord Application ID")
        if not hasattr(app, "accounts_box"):
            failures.append("UI: нет списка аккаунтов")
        if not hasattr(app, "theme_sets_box"):
            failures.append("UI: нет списка своих тем")
        if not hasattr(app, "roblox_version_box"):
            failures.append("UI: нет выбора версии Roblox")
        else:
            values = list(app.roblox_version_box["values"])
            if not values or not str(values[0]).startswith("Авто"):
                failures.append(f"UI: список версий {values[:2]}")
            if app.roblox_version_box.current() != 0:
                failures.append("UI: авто-версия не выбрана по умолчанию")
            app._roblox_version_pick()
            if app.cfg.get("roblox_version"):
                failures.append("UI: авто-версия записала путь")
        if not hasattr(app, "launch_icon_status_var"):
            failures.append("UI: нет статуса иконки запуска")
        if not hasattr(app, "var_launch_sound"):
            failures.append("UI: нет настройки звука запуска")
        if not hasattr(app, "launch_style_buttons"):
            failures.append("UI: нет выбора экрана запуска")
        if not hasattr(app, "ui_font_status_var"):
            failures.append("UI: нет статуса своего шрифта")
        if (not hasattr(app, "error_sound_status_var")
                or not hasattr(app, "var_error_sound")):
            failures.append("UI: нет настройки звука ошибки")

        # единая кнопка сохранения внизу и отсутствие других «Сохранить»
        if not hasattr(app, "btn_save_all"):
            failures.append("UI: нет единой кнопки сохранения")
        else:
            save_buttons = []

            def _collect_save_buttons(widget):
                for child in widget.winfo_children():
                    try:
                        text = str(child.cget("text")).strip().lower()
                    except tk.TclError:
                        text = ""
                    if isinstance(child, tk.Button) and text.startswith(
                            "сохранить"):
                        save_buttons.append(child)
                    _collect_save_buttons(child)

            _collect_save_buttons(root)
            if len(save_buttons) != 1:
                failures.append(
                    f"UI: кнопок «Сохранить» {len(save_buttons)}, нужна одна")
            elif save_buttons[0] is not app.btn_save_all:
                failures.append("UI: единая кнопка сохранения не внизу")

        # единая кнопка записывает поля всех разделов
        before_save = {key: app.cfg.get(key) for key in
                       ("auto_close", "default_place_id", "discord_rpc")}
        original_apply_flags = app.cfg.get("apply_flags_on_launch")
        original_boot_find = globals()["find_bootstrapper_settings"]
        app.cfg["apply_flags_on_launch"] = False
        globals()["find_bootstrapper_settings"] = lambda: (None, None)
        try:
            app.var_auto_close.set(not bool(before_save["auto_close"]))
            app.var_default.set("920587237")
            app.var_discord_rpc.set(False)
            app.var_discord_id.set("")
            app.save_everything()
            root.update()
            saved_all = load_config()
            if saved_all.get("default_place_id") != "920587237":
                failures.append(
                    "UI: единое сохранение не записало игру по умолчанию")
            if bool(saved_all.get("auto_close")) != bool(
                    app.var_auto_close.get()):
                failures.append("UI: единое сохранение не записало поведение")
            if saved_all.get("discord_rpc") is not False:
                failures.append("UI: единое сохранение не записало Discord")
            if "Все настройки сохранены" not in app.status_var.get():
                failures.append(
                    f"UI: статус единого сохранения "
                    f"{app.status_var.get()!r}")
        finally:
            globals()["find_bootstrapper_settings"] = original_boot_find
            app.cfg["apply_flags_on_launch"] = original_apply_flags
            for key, value in before_save.items():
                app.cfg[key] = value
            app.var_auto_close.set(bool(before_save["auto_close"]))
            app.var_default.set(before_save["default_place_id"] or "")
            app.var_discord_rpc.set(bool(before_save["discord_rpc"]))

        for attr in ("var_ingame_overlay", "var_ingame_island",
                     "var_ingame_start", "var_ingame_clock",
                     "var_ingame_afk", "var_ingame_auto",
                     "combo_ingame_corner", "combo_ingame_mode",
                     "combo_ingame_key", "combo_ingame_minutes",
                     "ingame_helper_var", "ingame_fps_var",
                     "ingame_clock_state_var",
                     "btn_ingame_start", "btn_ingame_stop",
                     "btn_ingame_overlay", "btn_ingame_hotkey",
                     "btn_ingame_clock_hotkey",
                     "hotkey_button_var", "clock_hotkey_button_var"):
            if not hasattr(app, attr):
                failures.append(f"UI: нет поля «В игре» {attr}")
        if not app.ingame_fps_var.get().startswith("FPS:"):
            failures.append(
                f"UI «В игре»: статус FPS {app.ingame_fps_var.get()!r}")
        app.show_page("ingame")
        root.update()
        app._refresh_ingame_status()
        root.update()
        if not app.ingame_helper_var.get():
            failures.append("UI: пустой статус помощника")
        original_hotkey = app.cfg.get("ingame_hotkey") or "shift+f1"
        original_clock_hotkey = (app.cfg.get("ingame_clock_hotkey")
                                 or "shift+f2")
        original_mods = ingame_mod.pressed_hotkey_modifiers
        try:
            ingame_mod.pressed_hotkey_modifiers = lambda: ["ctrl", "shift"]

            class _FakeKey:
                keysym = "h"

            app._ingame_hotkey_capture()
            app._ingame_hotkey_key(_FakeKey())
            if app.cfg.get("ingame_hotkey") != "ctrl+shift+h":
                failures.append(
                    f"UI «В игре»: бинд {app.cfg.get('ingame_hotkey')!r}")
            if app.hotkey_button_var.get() != "Ctrl+Shift+H":
                failures.append(
                    f"UI «В игре»: кнопка бинда "
                    f"{app.hotkey_button_var.get()!r}")
            if app._hotkey_capture_id:
                failures.append("UI «В игре»: запись бинда не завершилась")
            app._ingame_hotkey_capture("clock")
            app._ingame_hotkey_key(_FakeKey())
            if app.cfg.get("ingame_clock_hotkey") != "ctrl+shift+h":
                failures.append(
                    "UI «В игре»: бинд часов "
                    f"{app.cfg.get('ingame_clock_hotkey')!r}")
            if app.clock_hotkey_button_var.get() != "Ctrl+Shift+H":
                failures.append(
                    "UI «В игре»: кнопка бинда часов "
                    f"{app.clock_hotkey_button_var.get()!r}")
            if app.cfg.get("ingame_hotkey") != "ctrl+shift+h":
                failures.append(
                    "UI «В игре»: бинд часов испортил бинд оверлея")
        finally:
            ingame_mod.pressed_hotkey_modifiers = original_mods
            app._ingame_hotkey_cancel()
            app.cfg["ingame_hotkey"] = original_hotkey
            app.cfg["ingame_clock_hotkey"] = original_clock_hotkey
            app.hotkey_button_var.set(
                ingame_mod.hotkey_label(original_hotkey))
            app.clock_hotkey_button_var.set(
                ingame_mod.hotkey_label(original_clock_hotkey))
            save_config(app.cfg)
        for combo, label in ((app.combo_ingame_mode, "режим"),
                             (app.combo_ingame_key, "клавиша"),
                             (app.combo_ingame_minutes, "интервал")):
            if str(combo.cget("state")) != "disabled":
                failures.append(
                    f"UI «В игре»: {label} AFK активен при выключенном AFK")
        if str(app.combo_ingame_corner.cget("state")) != "readonly":
            failures.append(
                "UI «В игре»: угол оверлея недоступен при включённом оверлее")
        app.var_ingame_overlay.set(True)
        app.var_ingame_island.set(True)
        app.var_ingame_start.set(True)
        app.var_ingame_clock.set(True)
        app.var_ingame_afk.set(True)
        app.var_ingame_auto.set(True)
        app.combo_ingame_corner.set("Слева снизу")
        app.combo_ingame_mode.set("Клавиша")
        app.combo_ingame_key.set("X")
        app.combo_ingame_minutes.set("7 мин")
        app._save_ingame_cfg(message="тест")
        root.update()
        saved_ingame = load_config()
        expected_saved = {
            "ingame_overlay": True, "ingame_island": True,
            "ingame_overlay_start": True, "ingame_clock": True,
            "ingame_afk": True, "ingame_auto": True,
            "ingame_overlay_corner": "bl", "ingame_afk_mode": "key",
            "ingame_afk_key": "x", "ingame_afk_minutes": 7,
        }
        for key, want in expected_saved.items():
            if saved_ingame.get(key) != want:
                failures.append(
                    f"UI «В игре»: {key}={saved_ingame.get(key)!r} "
                    f"!= {want!r}")
        if app.var_ingame_mode.get() != "Клавиша":
            failures.append("UI «В игре»: режим AFK не синхронизирован")
        for combo, label in ((app.combo_ingame_corner, "угол"),
                             (app.combo_ingame_mode, "режим"),
                             (app.combo_ingame_key, "клавиша"),
                             (app.combo_ingame_minutes, "интервал")):
            if str(combo.cget("state")) != "readonly":
                failures.append(
                    f"UI «В игре»: {label} остался disabled после включения")
        app.var_ingame_mode.set("Мышь — микро-движение")
        app._ingame_update_controls()
        if str(app.combo_ingame_key.cget("state")) != "disabled":
            failures.append(
                "UI «В игре»: клавиша AFK активна в режиме мыши")
        app.var_ingame_mode.set("Клавиша")
        app._ingame_update_controls()
        start_calls = []
        original_ingame_start = app._ingame_start
        original_interactive = app.interactive
        original_helper_running = ingame_mod.helper_running
        original_read_status = ingame_mod.read_status
        original_user_stopped = app._ingame_user_stopped
        original_next_auto = app._ingame_next_auto
        try:
            app.interactive = True
            ingame_mod.helper_running = lambda: False
            ingame_mod.read_status = lambda *a, **k: None
            app._ingame_start = lambda show=False: start_calls.append(show)
            app._ingame_user_stopped = True
            app._maybe_start_ingame_helper()
            if start_calls:
                failures.append(
                    "UI: помощник стартовал после ручной остановки")
            app._ingame_user_stopped = False
            app._maybe_start_ingame_helper()
            if start_calls != [False]:
                failures.append(
                    f"UI: автозапуск помощника {start_calls}")
            start_calls.clear()
            app.cfg["ingame_auto"] = False
            app._maybe_start_ingame_helper()
            if start_calls:
                failures.append(
                    "UI: помощник стартовал при выключенном автозапуске")
            app.cfg["ingame_auto"] = True
            app._maybe_start_ingame_helper(force=True)
            if start_calls != [False]:
                failures.append("UI: принудительный запуск помощника")
            app._refresh_ingame_status()
            if "помощник не запущен" not in app.ingame_hotkey_var.get():
                failures.append(
                    f"UI: статус Shift+F1 {app.ingame_hotkey_var.get()!r}")
        finally:
            app._ingame_start = original_ingame_start
            app.interactive = original_interactive
            ingame_mod.helper_running = original_helper_running
            ingame_mod.read_status = original_read_status
            app._ingame_user_stopped = original_user_stopped
            app._ingame_next_auto = original_next_auto
        saved_env = {key: os.environ.get(key)
                     for key in ingame_mod.PYI_ENV_VARS}
        try:
            for key in ingame_mod.PYI_ENV_VARS:
                os.environ[key] = "selftest"
            helper_env = ingame_mod.helper_env()
            for key in ingame_mod.PYI_ENV_VARS:
                if key in helper_env:
                    failures.append(f"помощник: {key} унаследован")
        finally:
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        if app._rpc is not None:
            failures.append("UI: presence запустился без интерактива")
        if app.discord_status_var.get() == "":
            failures.append("UI: пустой статус Discord")

        fonts_root = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        ui_font_source = next(
            (fonts_root / name for name in
             ("arial.ttf", "calibri.ttf", "comic.ttf", "segoeui.ttf")
             if (fonts_root / name).is_file()), None)
        if ui_font_source is None:
            print("  note: системный TTF не найден — UI-тест шрифта пропущен")
        elif not app.apply_ui_font_file(ui_font_source):
            failures.append("UI: свой шрифт не применился")
        else:
            root.update()
            if UI_FONT != "Segoe UI":
                failures.append(
                    f"UI: лаунчер сменил шрифт на {UI_FONT!r} вместо Segoe UI")
            if not app._ui_font_family:
                failures.append("UI: семейство шрифта оверлея не определилось")
            if "Свой шрифт" not in app.ui_font_status_var.get():
                failures.append(
                    f"UI: статус шрифта {app.ui_font_status_var.get()!r}")
            if ui_font_path() is None:
                failures.append("UI: файл шрифта не сохранился")
            if len(app.pages) != len(NAV_ITEMS):
                failures.append("UI: после смены шрифта страницы неполные")
            app.reset_ui_font()
            root.update()
            if UI_FONT != "Segoe UI":
                failures.append(f"UI: шрифт лаунчера не Segoe UI ({UI_FONT})")
            if app._ui_font_family:
                failures.append(
                    f"UI: семейство шрифта не сброшено "
                    f"({app._ui_font_family})")
            if ui_font_path() is not None:
                failures.append("UI: файл шрифта остался после сброса")
            if "Стандартный" not in app.ui_font_status_var.get():
                failures.append(
                    f"UI: статус сброса {app.ui_font_status_var.get()!r}")

        app.var_error_sound.set(False)
        app._save_error_sound_flag()
        if app.cfg.get("error_sound"):
            failures.append("UI: выключение звука ошибки не сохранилось")
        app.var_error_sound.set(True)
        app._save_error_sound_flag()
        if not app.cfg.get("error_sound"):
            failures.append("UI: включение звука ошибки не сохранилось")

        original_theme = app.theme_name
        for name in THEME_ORDER:
            app.set_theme(name, save=False)
            root.update()
            if app.theme_name != name or app.c is not THEMES[name]:
                failures.append(f"set_theme({name}): UI не пересобрался")
            if len(app.pages) != len(NAV_ITEMS):
                failures.append(f"set_theme({name}): страницы неполные")
            if len(app.nav_buttons) != len(NAV_ITEMS):
                failures.append(f"set_theme({name}): сайдбар неполный")

        ui_theme = create_theme_set("Тестовая тема", base="light",
                                    tint="#3b9eff", accent="#3b9eff")
        if not ui_theme["ok"]:
            failures.append(f"UI: тестовая тема не создана {ui_theme}")
        else:
            app.set_theme("custom", save=False,
                          custom_name=ui_theme["name"])
            root.update()
            if app.theme_name != "custom" or not app.c.get("custom"):
                failures.append("UI: своя тема не применилась")
            if app.c.get("base") != "light":
                failures.append("UI: своя тема потеряла светлую базу")
            if len(app.pages) != len(NAV_ITEMS):
                failures.append("UI: страницы своей темы неполные")
            if len(app.nav_buttons) != len(NAV_ITEMS):
                failures.append("UI: сайдбар своей темы неполный")
            app._refresh_theme_panel(select=ui_theme["name"])
            root.update()
            if ui_theme["name"] not in app.theme_sets_box.get(0, "end"):
                failures.append("UI: тестовая тема не попала в список")

        if original_theme != app.theme_name:
            app.set_theme(original_theme, save=False)
            root.update()
        if app.cfg["theme"] != load_config()["theme"]:
            failures.append("set_theme(save=False) записал тему в конфиг")
        if ui_theme["ok"]:
            delete_theme_set(ui_theme["name"])

        # кнопки окна: macOS / Windows
        if not hasattr(app, "window_style_buttons"):
            failures.append("UI: нет выбора кнопок окна")
        else:
            for style in ("win", "mac"):
                app.set_window_buttons(style)
                root.update()
                if app.cfg.get("window_buttons") != style:
                    failures.append(
                        f"window_buttons: {style} не сохранился в конфиг")
                if load_config().get("window_buttons") != style:
                    failures.append(
                        f"window_buttons: {style} не записан на диск")
                button = getattr(app, "max_button", None)
                if button is None or not button.winfo_exists():
                    failures.append(
                        f"window_buttons: кнопки окна не построены ({style})")
                elif style == "win" and isinstance(button, tk.Canvas):
                    failures.append(
                        "window_buttons: стиль Windows, а кнопки macOS")
                elif style == "mac" and not isinstance(button, tk.Canvas):
                    failures.append(
                        "window_buttons: стиль macOS, а кнопки Windows")
                if style == "win" and button is not None:
                    app._on_max_change(True)
                    if button.cget("text") != "❐":
                        failures.append(
                            "window_buttons: значок разворота не сменился")
                    app._on_max_change(False)
                    if button.cget("text") != "□":
                        failures.append(
                            "window_buttons: значок разворота не вернулся")
            if not app.window_style_buttons["win"].cget("text") == "Windows":
                failures.append("UI: нет кнопки Windows в оформлении")

        # безрамочное окно: панель задач и сворачивание
        test_geom = f"{MIN_W}x{MIN_H}+-32000+-32000"
        root.geometry(test_geom)
        root.deiconify()
        root.update()
        hwnd = app.frameless._hwnd()
        user32 = ctypes.windll.user32
        user32.IsIconic.argtypes = [ctypes.c_void_p]
        user32.IsIconic.restype = ctypes.c_int
        style = user32.GetWindowLongW(ctypes.c_void_p(hwnd),
                                      ctypes.c_int(-20))
        if not (style & 0x40000):
            failures.append("окно без рамки не помечено для панели задач")
        app.frameless.minimize()
        root.update()
        if not user32.IsIconic(ctypes.c_void_p(hwnd)):
            failures.append("сворачивание окна не работает")
        root.deiconify()
        root.update()
        if user32.IsIconic(ctypes.c_void_p(hwnd)):
            failures.append("разворачивание окна не работает")

        # восстановление из «максимизированного»
        app.frameless.maximized = True
        app.frameless._prev_geom = test_geom
        app.frameless.restore()
        root.update()
        if app.frameless.maximized:
            failures.append("restore: окно осталось максимизированным")
        if f"{MIN_W}x{MIN_H}" not in root.geometry():
            failures.append(f"restore: геометрия {root.geometry()}")

        # Alt+Tab: уход из окна распознаётся, в автотесте не закрывает
        if not callable(app.frameless.on_alt_tab):
            failures.append("Alt+Tab: обработчик закрытия не подключён")
        original_foreground = app.frameless._foreground_elsewhere
        original_tab = app.frameless.on_alt_tab
        hits = []
        app.frameless._foreground_elsewhere = lambda: True
        app.frameless.on_alt_tab = lambda: hits.append(1)
        app.frameless._was_foreground = True
        app.frameless._alt_seen = time.monotonic()
        app.frameless._watch_switch()
        app.frameless._foreground_elsewhere = original_foreground
        app.frameless.on_alt_tab = original_tab
        root.update()
        if app.frameless._was_foreground:
            failures.append("Alt+Tab: уход из окна не распознан")
        if len(hits) != 1:
            failures.append("Alt+Tab: закрытие не сработало")
        app._close_on_alt_tab()
        root.update()
        if app._closing:
            failures.append("Alt+Tab: автотест закрыл лаунчер")

        # проверка обновлений: состояния карточки без сети
        if not hasattr(app, "btn_update"):
            failures.append("UI: нет кнопки проверки обновлений")
        else:
            app._on_update_checked({
                "current": VERSION, "latest": "99.0.0", "tag": "v99.0.0",
                "url": RELEASES_URL, "download": "https://x/app.exe",
                "assets": {"exe": {"name": "MaklyxaLauncher.exe",
                                   "url": "https://x/app.exe",
                                   "size": 22000000, "sha256": ""}},
                "prerelease": False, "is_newer": True})
            root.update()
            if "99.0.0" not in app.update_status_var.get():
                failures.append("UI: доступное обновление не показано")
            if not app.btn_update_get.winfo_manager():
                failures.append("UI: кнопка загрузки скрыта при обновлении")
            if (app._upd_asset or {}).get("url") != "https://x/app.exe":
                failures.append("UI: ассет обновления не сохранён")
            if app.btn_update_install.winfo_manager():
                failures.append("UI: кнопка установки видна до скачивания")
            tmp_upd = Path(tempfile.gettempdir()) / "maklyxa_selftest_upd.exe"
            tmp_upd.write_bytes(b"update")
            app._upd_version = "99.0.0"
            app._upd_finished(tmp_upd)
            root.update()
            if not app.btn_update_install.winfo_manager():
                failures.append("UI: кнопка установки скрыта после скачивания")
            if app.btn_update_get.winfo_manager():
                failures.append("UI: кнопка скачивания осталась после загрузки")
            try:
                tmp_upd.unlink()
            except OSError:
                pass
            app._on_update_checked({"current": VERSION, "latest": VERSION,
                                    "is_newer": False})
            root.update()
            if app.btn_update_get.winfo_manager():
                failures.append("UI: кнопка загрузки осталась без обновления")
            if app.btn_update_install.winfo_manager():
                failures.append("UI: кнопка установки осталась без обновления")
            if app._upd_asset is not None:
                failures.append("UI: ассет остался без обновления")
            if "последняя" not in app.update_status_var.get():
                failures.append("UI: нет статуса последней версии")

        # стиль запуска: переключение экрана и заставка
        app.set_launch_splash("minimal")
        root.update()
        if app.cfg.get("launch_splash") != "minimal":
            failures.append("UI: стиль запуска не сохранился")
        if "●" not in app.launch_style_buttons["minimal"].cget("text"):
            failures.append("UI: активный стиль запуска не отмечен")
        splash = app._show_launch_splash(
            "Тестовая игра", "Проверка экрана", force_style="card",
            duration=500)
        root.update()
        if splash is None or splash.win is None:
            failures.append("UI: экран запуска не открылся")
        else:
            app._hide_launch_splash()
            root.update()
            if splash.win is not None:
                failures.append("UI: экран запуска не закрылся")
        app.set_launch_splash("card")
        root.update()

        # фото-фон, режим «только картинка» и вход через сайт
        photo_theme = create_theme_set(
            "Фото-тема", base="dark", tint="#00c48c", accent="#00c48c",
            image=str(ICON_PNG) if ICON_PNG.exists() else None)
        if not photo_theme["ok"]:
            failures.append(f"UI: фото-тема не создана {photo_theme}")
        else:
            app.set_theme("custom", save=False,
                          custom_name=photo_theme["name"])
            app.show_page("themes")
            app._refresh_backdrops()
            root.update()
            if app._backdrop_master is None:
                failures.append("UI: фон-картинка не построена")
            elif (app._backdrop_master.width(),
                  app._backdrop_master.height()) != (
                      root.winfo_width(), root.winfo_height()):
                failures.append("UI: фон-картинка не на всё окно")
            if len(app._header_canvases) != len(NAV_ITEMS):
                failures.append(
                    f"UI: шапок {len(app._header_canvases)}, "
                    f"а разделов {len(NAV_ITEMS)}")
            for canvas in app._header_canvases:
                if "image" not in {canvas.type(item)
                                   for item in canvas.find_all()}:
                    failures.append("UI: шапка без фото-фона")
            for key, _icon, _title in NAV_ITEMS:
                data = app._page_views.get(key)
                if data is None:
                    failures.append(f"UI: нет вью для {key}")
                    continue
                if not data["pad"].itemcget(data["pad_photo"], "image"):
                    failures.append(f"UI: фото не попало в {key}")
            view = app._page_views["themes"]["view"]
            before = view.coords(
                app._page_views["themes"]["view_photo"])
            view.yview_moveto(0.35)
            root.update()
            after = view.coords(app._page_views["themes"]["view_photo"])
            if before == after:
                failures.append("UI: фон не двигается за скроллом")

            app._refresh_theme_panel(select=photo_theme["name"])
            root.update()
            app.theme_image_only_var.set(True)
            app._theme_toggle_image_only()
            root.update()
            saved = theme_set_info(photo_theme["name"])
            if not saved.get("image_only"):
                failures.append("UI: «только картинка» не сохранилась")
            app.set_theme("custom", save=False,
                          custom_name=photo_theme["name"])
            root.update()
            if not app.c.get("image_only"):
                failures.append("UI: «только картинка» не применилась")
            if app.accent != IMAGE_ONLY_ACCENT:
                failures.append(f"UI: акцент режима фото {app.accent}")

            app._open_add_account_dialog()
            root.update()
            dialog = app._login_dialog
            if dialog is None:
                failures.append("UI: диалог входа не открылся")
            else:
                entries, buttons = [], []
                stack = [dialog]
                while stack:
                    widget = stack.pop()
                    stack.extend(widget.winfo_children())
                    if widget.winfo_class() in ("TEntry", "Entry"):
                        entries.append(widget)
                    if widget.winfo_class() == "Button":
                        buttons.append(str(widget.cget("text")))
                if entries:
                    failures.append("UI: в диалоге входа остались поля пароля")
                if not any("сайт" in text.lower() for text in buttons):
                    failures.append(f"UI: нет кнопки входа через сайт {buttons}")
                app._close_login_dialog()
            app.set_theme(original_theme, save=False)
            root.update()
            delete_theme_set(photo_theme["name"])

        # галерея: группировка, страницы, просмотрщик, назначение, фон
        try:
            shots_root = core_module.SCREENSHOTS_DIR
            base_ts = 1_780_000_000.0
            ui_shots = []
            for index in range(7):
                name = f"RobloxScreenShot20261021_120000{index:03d}.png"
                path = shots_root / name
                if HAVE_PIL:
                    Image.new("RGB", (96, 54),
                              (30 + index * 20, 70, 130)).save(path)
                else:
                    path.write_bytes(b"png")
                st = path.stat()
                ui_shots.append({
                    "path": str(path), "name": name,
                    "ts": base_ts + index * 10,
                    "mtime": st.st_mtime, "size": st.st_size})
            extra = shots_root / "RobloxScreenShot20260901_090000000.png"
            if HAVE_PIL:
                Image.new("RGB", (96, 54), (120, 40, 40)).save(extra)
            else:
                extra.write_bytes(b"png")
            st = extra.stat()
            ui_shots.append({
                "path": str(extra), "name": extra.name,
                "ts": base_ts - 10_000, "mtime": st.st_mtime,
                "size": st.st_size})
            ui_sessions = [{
                "start": base_ts - 5, "end": base_ts + 700,
                "place_id": "920587237", "source": "selftest",
                "name": "UI Game"}]
            ui_cache = {
                "version": 1,
                "places": {"920587237": {
                    "name": "UI Place", "creator": "", "checked": 0}},
                "assignments": {}}
            ui_groups = build_gallery_groups(
                ui_shots, ui_sessions, ui_cache)
            if HAVE_PIL:
                for shot in ui_shots:
                    app._gallery_make_thumb(shot)
            app.show_page("gallery")
            root.update()
            app._gallery_refresh_token += 1
            app._gallery_apply(app._gallery_refresh_token, {
                "groups": ui_groups, "shots": ui_shots,
                "stats": gallery_stats(ui_groups), "cache": ui_cache,
                "folder": str(shots_root)})
            root.update()
            if len(app._gallery_groups) != 2:
                failures.append(
                    f"UI галерея: групп {len(app._gallery_groups)}")
            if app.gallery_group_combo.current() != 0:
                failures.append(
                    "UI галерея: комбобокс не выбран на первой группе")
            if (app._gallery_page_size != 6
                    or len(app._gallery_visible) != 6):
                failures.append(
                    f"UI галерея: видимых {len(app._gallery_visible)}")
            if "1 / 2" not in app.gallery_page_var.get():
                failures.append(
                    f"UI галерея: страница {app.gallery_page_var.get()!r}")
            if str(app.gallery_next_btn.cget("state")) != "normal":
                failures.append("UI галерея: «вперёд» недоступна")
            app._gallery_next()
            root.update()
            if (app._gallery_page != 1
                    or len(app._gallery_visible) != 1):
                failures.append(
                    f"UI галерея: вторая страница {app._gallery_page} / "
                    f"{len(app._gallery_visible)}")
            if "2 / 2" not in app.gallery_page_var.get():
                failures.append(
                    f"UI галерея: страница 2 {app.gallery_page_var.get()!r}")
            if str(app.gallery_next_btn.cget("state")) != "disabled":
                failures.append("UI галерея: «вперёд» активна в конце")
            if str(app.gallery_prev_btn.cget("state")) != "normal":
                failures.append("UI галерея: «назад» недоступна")
            app._gallery_prev()
            root.update()
            if HAVE_PIL:
                thumb = gallery_thumb_path(ui_shots[0])
                if not thumb.is_file():
                    failures.append("UI галерея: миниатюра не создана")
                elif app._gallery_photo(ui_shots[0]) is None:
                    failures.append("UI галерея: превью не загрузилось")
            app._gallery_open_viewer(0)
            root.update()
            viewer = app._gallery_viewer
            if viewer is None or not viewer["win"].winfo_exists():
                failures.append("UI галерея: просмотрщик не открылся")
            else:
                if app._gallery_viewer_counter_var.get() != "1 из 7":
                    failures.append(
                        "UI галерея: счётчик "
                        f"{app._gallery_viewer_counter_var.get()!r}")
                if app._gallery_viewer_title_var.get() != "UI Place":
                    failures.append(
                        "UI галерея: заголовок "
                        f"{app._gallery_viewer_title_var.get()!r}")
                app._gallery_viewer_step(1)
                root.update()
                if app._gallery_viewer_index != 1:
                    failures.append(
                        f"UI галерея: шаг {app._gallery_viewer_index}")
                app._gallery_viewer_close()
                root.update()
                if app._gallery_viewer is not None:
                    failures.append("UI галерея: просмотрщик не закрылся")

            assigned_name = ui_shots[0]["name"]
            app._open_gallery_assign_dialog(
                names=[assigned_name], label="тест")
            root.update()
            if app._gallery_assign_dialog is None:
                failures.append(
                    "UI галерея: диалог назначения не открылся")
            else:
                app._gallery_assign_var.set("abc")
                app._gallery_assign_submit()
                if "Place ID" not in app._gallery_assign_status_var.get():
                    failures.append("UI галерея: нет ошибки Place ID")
                if app._gallery_assign_dialog is None:
                    failures.append(
                        "UI галерея: диалог закрылся при ошибке")
                original_refresh = app.refresh_gallery
                app.refresh_gallery = lambda *a, **k: None
                try:
                    app._gallery_assign_var.set(
                        "https://www.roblox.com/games/920587237/Test")
                    app._gallery_assign_submit()
                finally:
                    app.refresh_gallery = original_refresh
                root.update()
                if app._gallery_assign_dialog is not None:
                    failures.append("UI галерея: диалог не закрылся")
                if (app._gallery_cache or {}).get(
                        "assignments", {}).get(assigned_name) != "920587237":
                    failures.append(
                        "UI галерея: назначение не сохранилось")
                if gallery_cache_load()["assignments"].get(
                        assigned_name) != "920587237":
                    failures.append("UI галерея: назначения нет в кэше")
            app.open_clean_background_dialog()
            root.update()
            clean = app._gallery_clean_dialog
            texts = []
            if clean is not None:
                stack = [clean]
                while stack:
                    widget = stack.pop()
                    stack.extend(widget.winfo_children())
                    try:
                        texts.append(str(widget.cget("text")))
                    except tk.TclError:
                        pass
            if clean is None:
                failures.append(
                    "UI галерея: диалог очистки фона не открылся")
            elif not any("DFIntCanHideGuiGroupId" in text for text in texts):
                failures.append("UI галерея: нет объяснения про FastFlag")
            else:
                app._close_clean_background_dialog()
                root.update()
                if app._gallery_clean_dialog is not None:
                    failures.append(
                        "UI галерея: диалог очистки не закрылся")

            app._gallery_refresh_token += 1
            app._gallery_apply(app._gallery_refresh_token, {
                "groups": [], "shots": [], "stats": gallery_stats([]),
                "cache": ui_cache, "folder": str(shots_root)})
            root.update()
            if app.gallery_empty.winfo_manager() != "pack":
                failures.append(
                    "UI галерея: пустое состояние не показано")
            if app.gallery_grid.winfo_manager() == "pack":
                failures.append("UI галерея: сетка осталась при пустоте")
        except Exception as exc:  # noqa: BLE001
            failures.append(
                f"UI gallery test: {type(exc).__name__}: {exc}")

        root.withdraw()
        app._closing = True        # не пишем тестовую геометрию в конфиг
        root.destroy()
        ui_tmp.cleanup()
    except Exception as exc:  # noqa: BLE001
        failures.append(f"UI smoke test: {type(exc).__name__}: {exc}")

    if failures:
        print("SELFTEST FAILED")
        for item in failures:
            print("  -", item)
        return 1
    print("SELFTEST OK: core, themes, accounts, frameless window, flags, "
          "cursors, launch style, custom font, error sound, updates, "
          "UI rebuild — all good")
    return 0


_INSTANCE = None


def main():
    global _INSTANCE
    if "--ingame" in sys.argv:
        sys.exit(ingame_mod.main())
    if "--selftest" in sys.argv:
        sys.exit(run_selftest())

    _INSTANCE = acquire_single_instance()
    if _INSTANCE is False:
        # второй экземпляр: его окно уже выведено вперёд
        return
    core.remember_bundle_dir()
    if getattr(sys, "frozen", False) and not ingame_mod.helper_running():
        threading.Thread(target=core.cleanup_stale_bundle_dirs,
                         daemon=True).start()

    root = tk.Tk()
    start_page = None
    if "--page" in sys.argv:
        idx = sys.argv.index("--page")
        if idx + 1 < len(sys.argv):
            start_page = sys.argv[idx + 1]
    app = App(root, start_page=start_page)
    if "--theme" in sys.argv:
        idx = sys.argv.index("--theme")
        if idx + 1 < len(sys.argv):
            app.set_theme(sys.argv[idx + 1], save=False)
    root.mainloop()


if __name__ == "__main__":
    main()
