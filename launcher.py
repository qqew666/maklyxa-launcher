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
Запуск: python launcher.py [--page game|favorites|flags|cursors|settings|log]
                            [--theme dark|light|glass]
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
from tkinter import filedialog, messagebox, ttk

from core import (
    ACCENT_PRESETS, APP_NAME, BOOT_BOOLS_FROST, BOOT_BOOLS_SHARED,
    BUNDLE_DIR, CONFIG_DIR, CONFIG_PATH, CURSOR_SLOTS,
    DEFAULT_CONFIG, DEFAULT_THEME,
    DISCOVER_URL, FPS_CHOICES, FLAG_API, FLAG_FPS, FLAG_QUALITY,
    GRAPHICS_MODES, HTTP_HEADERS, INSTALLER_URL,
    THEMES, THEME_ORDER, VERSION, WINDOW_TITLE,
    acquire_single_instance, apply_cursor_set, apply_fastflags,
    bootstrapper_running, client_settings_paths, copy_current_cursor_set,
    create_cursor_set, cursor_install_roots, cursor_set_files,
    cursor_set_warnings, cursor_slot_path, cursor_state_info,
    datacenter_regions, delete_cursor_set, ensure_cursor_sets_dir,
    export_cursor_set, fetch_game_icon, fetch_game_info, fastflag_values,
    find_bootstrapper_settings, find_player_exe, focus_roblox_window,
    fmt_num, hit_test, import_cursor_set, list_cursor_sets, load_config,
    mb, merge_flags_into, migrate_old_config, parse_place_id, png_info,
    player_pids, probe_protocol, read_json_dict, remove_cursor_slot,
    rename_cursor_set, resize_geometry, restore_custom_cursors,
    roblox_status, save_config, set_cursor_slot, shade, trunc,
    valid_cursor_set_name, valid_geometry, work_area, write_json_dict,
)

ICON_PNG = BUNDLE_DIR / "icon.png"

TITLEBAR_H = 48
SIDEBAR_W = 216
MIN_W, MIN_H = 960, 640
BORDER_PX = 6
DEFAULT_SIZE = (1080, 760)

CURSORS = {
    "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
    "e": "sb_h_double_arrow", "w": "sb_h_double_arrow",
    "ne": "sizing", "nw": "sizing", "se": "sizing", "sw": "sizing",
}

NAV_ITEMS = [
    ("game", "▶", "Игра"),
    ("favorites", "★", "Избранное"),
    ("flags", "⚑", "Флаги"),
    ("cursors", "◈", "Курсоры"),
    ("settings", "⚙", "Настройки"),
    ("log", "≡", "Журнал"),
]

NAV_GROUPS = [
    ("ЗАПУСК", ("game", "favorites")),
    ("ПРЕДУСТАНОВЛЕННЫЕ\nМОДИФИКАЦИИ", ("flags", "cursors")),
    ("СИСТЕМА", ("settings", "log")),
]


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
        self._mode = None              # 'move' | 'resize'
        self._zone = ""
        self._start = None
        self._prev_geom = None
        self._cursor = ""
        self._styled = False

        root.overrideredirect(True)
        root.bind("<Map>", self._on_map, add="+")
        root.bind("<Button-1>", self._on_press, add="+")
        root.bind("<B1-Motion>", self._on_drag, add="+")
        root.bind("<ButtonRelease-1>", self._on_release, add="+")
        root.bind("<Motion>", self._on_hover, add="+")
        root.bind("<Double-Button-1>", self._on_double, add="+")

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

    # -- геометрия ------------------------------------------------------------

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
            font=("Segoe UI", 11), cursor="hand2", anchor="center")
        self.icon.pack(side="left", padx=(11, 6), pady=10)
        self.label = tk.Label(
            self, text=text, bg=c["panel"], fg=c["muted"], anchor="w",
            font=("Segoe UI", 10), cursor="hand2")
        self.label.pack(side="left", fill="x", expand=True, pady=10)
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
                                 font=("Segoe UI", 10, "bold"))
            self.configure(bg=bg)
        else:
            bg = c["hover"] if self._hover else c["panel"]
            fg = c["text"] if self._hover else c["muted"]
            self.bar.configure(bg=c["panel"])
            self.icon.configure(bg=bg, fg=fg)
            self.label.configure(bg=bg, fg=fg, font=("Segoe UI", 10))
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
                              font=("Segoe UI", 10), anchor="w",
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


class App:
    def __init__(self, root, interactive=True, start_page=None):
        self.root = root
        self.interactive = interactive
        self.cfg = load_config()
        self.theme_name = self.cfg["theme"]
        self.c = THEMES[self.theme_name]
        self.accent = self.cfg["accent"]

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
        self._configure_window()
        self._build(keep_page=self.current_page)

        if not self._wheel_bound:
            self.root.bind_all("<MouseWheel>", self._on_wheel, add="+")
            self._wheel_bound = True
        self.root.after(100, self._poll_queue)
        self._startup_checks()
        self.on_entry_changed()

    # -- сборка / пересборка ----------------------------------------------------

    def _build(self, keep_page=None):
        self._build_styles()
        self._build_shell()
        self._build_page_game()
        self._build_page_favorites()
        self._build_page_flags()
        self._build_page_cursors()
        self._build_page_settings()
        self._build_page_log()
        self._load_into_ui()
        self._refresh_lists()
        page = keep_page if keep_page in self.pages else "game"
        self.show_page(page)
        self.apply_accent(self.accent)

    def _rebuild(self):
        """Полная пересборка UI (смена темы)."""
        keep = self.current_page
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
                        darkcolor=c["border"], font=("Segoe UI", 10))
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
                        foreground=c["muted"], font=("Segoe UI", 9))
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
                        font=("Segoe UI", 10, "bold"))
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

        if ICON_PNG.exists():
            try:
                img = tk.PhotoImage(file=str(ICON_PNG))
                img = img.subsample(12, 12)      # 256 → 21 px
                self._title_icon = img
                tk.Label(titlebar, image=img, bg=c["titlebar"],
                         bd=0).pack(side="left", padx=(16, 8))
            except tk.TclError:
                pass
        tk.Label(titlebar, text=APP_NAME, bg=c["titlebar"], fg=c["text"],
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        tk.Label(titlebar, text=f"v{VERSION}", bg=c["surface2"],
                 fg=c["muted"], font=("Segoe UI", 8, "bold"), padx=7,
                 pady=2).pack(side="left", padx=(9, 0))
        self.title_crumb = tk.Label(
            titlebar, text="", bg=c["titlebar"], fg=c["muted"],
            font=("Segoe UI", 9))
        self.title_crumb.pack(side="left", padx=(14, 0))

        close_btn = self._window_button(
            titlebar, "✕", self.close, danger=True)
        max_btn = self._window_button(
            titlebar, "□", self.frameless.toggle_maximize)
        min_btn = self._window_button(
            titlebar, "─", self.frameless.minimize)
        self.max_button = max_btn
        self.frameless.on_max_change = self._on_max_change

        theme_lbl = THEMES[self.theme_name]["label"]
        self.theme_cycle_btn = tk.Button(
            titlebar, text=f"◐  {theme_lbl}", relief="flat", bd=0,
            font=("Segoe UI", 9), cursor="hand2", takefocus=False,
            bg=c["surface2"], fg=c["text"], activebackground=c["hover"],
            activeforeground=c["text"], highlightthickness=0, padx=12,
            command=self._cycle_theme)
        self.theme_cycle_btn.pack(side="right", padx=(0, 12))
        self._bind_hover(self.theme_cycle_btn, "ghost")
        self.frameless.excluded = {min_btn, max_btn, close_btn,
                                   self.theme_cycle_btn}

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
        statusbar = tk.Frame(main, bg=c["panel"], height=34)
        statusbar.pack(fill="x", side="bottom")
        statusbar.pack_propagate(False)
        self.status_dot = tk.Label(statusbar, text="●", bg=c["panel"],
                                   fg=self.accent, font=("Segoe UI", 7))
        self.status_dot.pack(side="left", padx=(18, 0))
        tk.Label(statusbar, textvariable=self.proto_var, bg=c["panel"],
                 fg=c["muted"], font=("Segoe UI", 9)).pack(
                     side="left", padx=(7, 0))
        tk.Label(statusbar, textvariable=self.status_var, bg=c["panel"],
                 fg=c["text"], font=("Segoe UI", 9)).pack(
                     side="right", padx=(0, 18))
        tk.Frame(main, bg=c["border"], height=1).pack(
            fill="x", side="bottom")

        content = tk.Frame(main, bg=c["bg"])
        content.pack(fill="both", expand=True)
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)
        self.content = content

    def _build_sidebar(self, sidebar):
        c = self.c
        nav = tk.Frame(sidebar, bg=c["panel"])
        nav.pack(fill="x", padx=12, pady=(18, 0))
        items = {key: (icon, text) for key, icon, text in NAV_ITEMS}
        for title, keys in NAV_GROUPS:
            tk.Label(nav, text=title, bg=c["panel"], fg=c["muted"],
                     font=("Segoe UI", 8, "bold"), anchor="w",
                     justify="left").pack(
                         fill="x", padx=9, pady=(18 if title == "ЗАПУСК"
                                               else 16, 6))
            for key in keys:
                icon, text = items[key]
                item = SidebarItem(self, nav, key, icon, text)
                item.pack(fill="x")
                self.nav_buttons[key] = item

        tk.Frame(sidebar, bg=c["panel"]).pack(fill="both", expand=True)

        # статус Roblox внизу сайдбара
        card = tk.Frame(sidebar, bg=c["border"])
        card.pack(fill="x", padx=12, pady=(10, 0))
        inner = tk.Frame(card, bg=c["surface"], padx=12, pady=10)
        inner.pack(fill="x", padx=1, pady=1)
        head = tk.Frame(inner, bg=c["surface"])
        head.pack(fill="x")
        self.sidebar_status_dot = tk.Label(
            head, text="●", bg=c["surface"], fg=self.accent,
            font=("Segoe UI", 7))
        self.sidebar_status_dot.pack(side="left")
        tk.Label(head, text="ROBLOX", bg=c["surface"], fg=c["muted"],
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(6, 0))
        self.sidebar_roblox_var = tk.StringVar()
        tk.Label(inner, textvariable=self.sidebar_roblox_var,
                 bg=c["surface"], fg=c["text"],
                 font=("Segoe UI", 9, "bold"), anchor="w").pack(
                     fill="x", pady=(4, 0))
        self._refresh_sidebar_status()

        tk.Label(sidebar, text=f"MaklyxaLauncher v{VERSION}",
                 bg=c["panel"], fg=c["muted"], font=("Segoe UI", 8),
                 anchor="w").pack(fill="x", padx=21, pady=(14, 2))
        tk.Label(sidebar, text="официальный клиент Roblox",
                 bg=c["panel"], fg=c["muted"], font=("Segoe UI", 8),
                 anchor="w").pack(fill="x", padx=21, pady=(0, 16))

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
        idx = THEME_ORDER.index(self.theme_name) \
            if self.theme_name in THEME_ORDER else 0
        self.set_theme(THEME_ORDER[(idx + 1) % len(THEME_ORDER)])

    def _window_button(self, parent, glyph, command, danger=False):
        c = self.c
        base = c["titlebar"]
        btn = tk.Button(
            parent, text=glyph, relief="flat", bd=0, takefocus=False,
            bg=base, fg=c["text"], activebackground=base,
            activeforeground=c["text"], font=("Segoe UI", 11),
            cursor="hand2", highlightthickness=0, width=6,
            command=command)
        btn.pack(side="right", fill="y")

        def on_enter(_e):
            if danger:
                btn.configure(bg="#e81123", fg="#ffffff",
                              activebackground="#e81123",
                              activeforeground="#ffffff")
            else:
                hot = shade(base, 1.35)
                btn.configure(bg=hot, activebackground=hot)

        def on_leave(_e):
            btn.configure(bg=base, fg=c["text"], activebackground=base,
                          activeforeground=c["text"])
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        return btn

    def _on_max_change(self, maximized):
        try:
            self.max_button.configure(text="❐" if maximized else "□")
        except tk.TclError:
            pass

    # -- страницы -----------------------------------------------------------------

    def _scroll_page(self, key, padx=28, pady=18):
        """Страница со скроллом; возвращает фрейм с учётом отступов."""
        c = self.c
        holder = tk.Frame(self.content, bg=c["bg"])
        holder.grid(row=0, column=0, sticky="nsew")
        self.pages[key] = holder

        canvas = tk.Canvas(holder, bg=c["bg"], highlightthickness=0, bd=0)
        bar = self._vscroll(holder, canvas.yview, c["bg"], width=8)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

        inner = tk.Frame(canvas, bg=c["bg"])
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.scroll_canvases[key] = canvas

        pad = tk.Frame(inner, bg=c["bg"])
        pad.pack(fill="both", expand=True, padx=padx, pady=pady)
        return pad

    def show_page(self, key):
        page = self.pages.get(key)
        if page is None:
            return
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
        """Шапка страницы: бренд, крупный заголовок и иконка раздела."""
        c = self.c
        row = tk.Frame(pad, bg=c["bg"])
        row.pack(fill="x", pady=(0, 14))

        badge = tk.Frame(row, width=56, height=56, bg=c["surface2"],
                         highlightthickness=1,
                         highlightbackground=c["border"])
        badge.pack(side="right", anchor="n")
        badge.pack_propagate(False)
        glyph_lbl = tk.Label(badge, text=glyph, bg=c["surface2"],
                             fg=self.accent, font=("Segoe UI", 20, "bold"))
        glyph_lbl.pack(fill="both", expand=True)
        self.accent_fgs.append(glyph_lbl)

        col = tk.Frame(row, bg=c["bg"])
        col.pack(side="left", fill="x", expand=True, padx=(0, 18))
        eyebrow = tk.Frame(col, bg=c["bg"])
        eyebrow.pack(fill="x")
        line = tk.Frame(eyebrow, bg=self.accent, width=18, height=2)
        line.pack(side="left", pady=(5, 0))
        self.accent_bgs.append(line)
        eyebrow_lbl = tk.Label(
            eyebrow, text="M A K L Y X A", bg=c["bg"], fg=self.accent,
            font=("Segoe UI", 8, "bold"))
        eyebrow_lbl.pack(side="left", padx=(8, 0))
        self.accent_fgs.append(eyebrow_lbl)
        tk.Label(col, text=title, bg=c["bg"], fg=c["text"],
                 font=("Segoe UI", 22, "bold"), anchor="w").pack(
                     fill="x", pady=(6, 2))
        if subtitle:
            tk.Label(col, text=subtitle, bg=c["bg"], fg=c["muted"],
                     font=("Segoe UI", 10), anchor="w",
                     justify="left", wraplength=540).pack(fill="x")
        return row

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
                    font=("Segoe UI", 11, "bold"))
                glyph_lbl.pack(side="left", padx=(10, 7))
                self.accent_fgs.append(glyph_lbl)
                offset = 0
            tk.Label(head, text=title, bg=c["surface"], fg=c["text"],
                     font=("Segoe UI", 11, "bold"),
                     anchor="w").pack(side="left", padx=(offset, 0))
            if subtitle:
                tk.Label(inner, text=subtitle, bg=c["surface"],
                         fg=c["muted"], font=("Segoe UI", 9), anchor="w",
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
        factor = 0.95 if self.theme_name == "light" else 1.14
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
            font=("Segoe UI", 10, "bold" if kind == "primary" else "normal"),
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
                       font=("Segoe UI", 9), anchor="w", justify="left",
                       wraplength=wrap)
        lbl.pack(fill="x", pady=(6, 0))
        return lbl

    def _checkbox(self, parent, text, variable, command=None):
        return ThemedCheck(self, parent, text, variable, command=command)

    def _warn_fg(self):
        return "#b45309" if self.theme_name == "light" else "#ff9f43"

    def _vscroll(self, parent, command, trough, width=8):
        c = self.c
        light = self.theme_name == "light"
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
            highlightcolor=self.accent, font=("Segoe UI", 10),
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
                 bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9),
                 anchor="w", justify="left", wraplength=320).pack(
                     fill="x", pady=(0, 10))
        self.btn_client = tk.Button(
            left, text="▶   ПРОСТО В ROBLOX", relief="flat", bd=0,
            cursor="hand2", font=("Segoe UI", 12, "bold"), pady=12,
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

        # запуск игры по ссылке или Place ID
        inner = self._card(pad)
        tk.Label(inner, text="ВЫБРАННАЯ ИГРА", bg=c["surface"],
                 fg=c["muted"], font=("Segoe UI", 8, "bold"),
                 anchor="w").pack(fill="x")

        head = tk.Frame(inner, bg=c["surface"])
        head.pack(fill="x", pady=(9, 0))

        self.icon_box = tk.Frame(
            head, bg=c["surface2"], width=88, height=88,
            highlightthickness=1, highlightbackground=c["border"])
        self.icon_box.pack(side="left")
        self.icon_box.pack_propagate(False)
        self.icon_lbl = tk.Label(self.icon_box, text="▶", bg=c["surface2"],
                                 fg=c["muted"], font=("Segoe UI", 24))
        self.icon_lbl.pack(fill="both", expand=True)
        self.icon_img = None

        col = tk.Frame(head, bg=c["surface"])
        col.pack(side="left", fill="both", expand=True, padx=(16, 0))
        self.game_name_var = tk.StringVar(value="Игра не выбрана")
        self.info_var = tk.StringVar(
            value="Вставь ссылку на игру или введи Place ID")
        self.pid_var = tk.StringVar(value="")
        tk.Label(col, textvariable=self.game_name_var, bg=c["surface"],
                 fg=c["text"], font=("Segoe UI", 16, "bold"), anchor="w",
                 justify="left", wraplength=420).pack(fill="x")
        tk.Label(col, textvariable=self.info_var, bg=c["surface"],
                 fg=c["muted"], font=("Segoe UI", 9), anchor="w",
                 wraplength=420, justify="left").pack(fill="x", pady=(4, 4))
        pid_lbl = tk.Label(col, textvariable=self.pid_var, bg=c["surface"],
                           fg=self.accent, font=("Segoe UI", 9, "bold"),
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

        self.btn_play = tk.Button(
            inner, text="▶   ИГРАТЬ ПО ID", relief="flat", bd=0,
            cursor="hand2", font=("Segoe UI", 13, "bold"), pady=12,
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
                font=("Segoe UI", 10, "bold"))
            glyph_lbl.pack(side="left", padx=(10, 6))
            self.accent_fgs.append(glyph_lbl)
            offset = 0
        tk.Label(head, text=title, bg=c["surface"], fg=c["text"],
                 font=("Segoe UI", 11, "bold")).pack(
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
                     font=("Segoe UI", 10)).grid(
                row=row, column=0, sticky="w", pady=4, padx=(0, 14))
            widget.grid(row=row, column=1, sticky="ew", pady=4)

        fps_values = list(FPS_CHOICES)
        if (self.cfg["fps_limit"]
                and str(self.cfg["fps_limit"]) not in fps_values):
            fps_values.append(str(self.cfg["fps_limit"]))
        self.combo_fps = ttk.Combobox(gridf, values=fps_values,
                                      state="readonly")
        self.combo_fps.set(
            str(self.cfg["fps_limit"]) if self.cfg["fps_limit"]
            else "Как в игре")
        self.combo_fps.bind("<<ComboboxSelected>>", self._on_fps_change)
        _row(0, "Ограничение FPS", self.combo_fps)

        self.combo_api = ttk.Combobox(
            gridf, values=["Авто"] + GRAPHICS_MODES, state="readonly")
        self.combo_api.set(self.cfg["graphics_mode"] or "Авто")
        self.combo_api.bind("<<ComboboxSelected>>", self._on_api_change)
        _row(1, "Графический API", self.combo_api)

        self.api_hint = tk.Label(
            gridf, text="", bg=c["surface"], fg=self._warn_fg(),
            font=("Segoe UI", 9), anchor="w", justify="left",
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

        self.var_apply_flags = tk.BooleanVar(
            value=bool(self.cfg["apply_flags_on_launch"]))
        self._checkbox(
            gridf, "Применять флаги перед каждым запуском",
            self.var_apply_flags, command=self._save_apply_flags_var).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        btn_row = tk.Frame(gridf, bg=c["surface"])
        btn_row.grid(row=5, column=0, columnspan=2, sticky="ew",
                     pady=(10, 0))
        self._button(btn_row, "Применить сейчас", self.apply_flags_now,
                     side="left")
        self.flags_status = tk.StringVar(value="")
        tk.Label(btn_row, textvariable=self.flags_status, bg=c["surface"],
                 fg=c["muted"], font=("Segoe UI", 9)).pack(
                     side="left", padx=12)

        # -- панель бутстрапера
        inner2 = self._card(pad, title="Панель бутстрапера",
                            subtitle=("Читает и пишет конфиг твоего "
                                      "бутстрапера — только известные "
                                      "ключи, остальное не трогает."),
                            glyph="⚙")
        self.boot_title = tk.Label(inner2, text="", bg=c["surface"],
                                   fg=c["text"],
                                   font=("Segoe UI", 11, "bold"),
                                   anchor="w")
        self.boot_title.pack(fill="x", pady=(6, 0))
        self.boot_status = tk.StringVar(value="")
        tk.Label(inner2, textvariable=self.boot_status, bg=c["surface"],
                 fg=c["muted"], font=("Segoe UI", 9), anchor="w",
                 wraplength=520, justify="left").pack(fill="x")

        self.boot_body = tk.Frame(inner2, bg=c["surface"])

        # регион (только Froststrap)
        self.region_row = tk.Frame(self.boot_body, bg=c["surface"])
        tk.Label(self.region_row, text="Регион сервера", bg=c["surface"],
                 fg=c["text"], font=("Segoe UI", 10)).pack(
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
                                  fg=c["muted"], font=("Segoe UI", 9),
                                  anchor="w", wraplength=520, justify="left")
        self.boot_path.pack(fill="x", pady=(10, 0))

        btn_row2 = tk.Frame(self.boot_body, bg=c["surface"])
        btn_row2.pack(fill="x", pady=(8, 0))
        self.btn_boot_save = self._button(
            btn_row2, "Сохранить", self.save_boot_panel, side="left")
        self._button(btn_row2, "Обновить из файла", self.refresh_boot_panel,
                     side="left", padx=8)
        self.boot_body.pack(fill="x")

        self.boot_none = tk.Label(
            inner2, bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9),
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
        self.btn_boot_save.configure(text=f"Сохранить в {name}")
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

    def save_boot_panel(self):
        kind, path = find_bootstrapper_settings()
        if not path:
            self.boot_status.set("Конфиг не найден")
            return
        data = read_json_dict(path)
        if kind == "froststrap":
            data["SelectedRegion"] = self.combo_region.get().strip() or "Auto"
        for key, var in self.boot_vars:
            data[key] = bool(var.get())
        ok, err = write_json_dict(path, data)
        if not ok:
            self.boot_status.set("Ошибка записи")
            self.log(f"Не удалось записать {path}: {err}")
            return
        name = "Froststrap" if kind == "froststrap" else "Bloxstrap"
        note = f"Сохранено в {name}"
        if bootstrapper_running():
            note += " (бутстрапер запущен — при выходе может перезаписать)"
        self.boot_status.set(note)
        self.status_var.set(f"Настройки {name} сохранены")
        self.log(f"Настройки {name} сохранены: {path}")

    def refresh_boot_panel(self):
        self._load_boot_panel(note="Обновлено из файла")
        self.log("Конфиг бутстрапера перечитан")

    def _save_apply_flags_var(self):
        self.cfg["apply_flags_on_launch"] = bool(self.var_apply_flags.get())
        save_config(self.cfg)
        self.status_var.set("Настройка сохранена")

    def _on_fps_change(self, _event=None):
        raw = self.combo_fps.get()
        try:
            self.cfg["fps_limit"] = 0 if raw == "Как в игре" else int(raw)
        except ValueError:
            self.cfg["fps_limit"] = 0
        self._flags_changed("Ограничение FPS")

    def _on_api_change(self, _event=None):
        raw = self.combo_api.get()
        self.cfg["graphics_mode"] = (
            "" if raw == "Авто" else raw if raw in GRAPHICS_MODES else "")
        self._update_api_hint()
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
            self.cfg["quality_level"] = 0 if raw == "Как в игре" else int(raw)
        except ValueError:
            self.cfg["quality_level"] = 0
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
            font=("Segoe UI", 10), exportselection=False, height=10)
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
                 font=("Segoe UI", 10, "bold"), anchor="w").pack(
                     fill="x", pady=(10, 2))
        self.cursor_warn_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.cursor_warn_var, bg=c["surface"],
                 fg=self._warn_fg(), font=("Segoe UI", 9), anchor="w",
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
                 font=("Segoe UI", 9, "bold"), anchor="w",
                 justify="left", wraplength=150).pack(
                     side="left", fill="x", expand=True)
        remove = tk.Label(head, text="✕", bg=c["surface2"], fg=c["muted"],
                          font=("Segoe UI", 9, "bold"), cursor="hand2")
        remove.bind("<Button-1>",
                    lambda _e, k=slot["key"]: self._cursor_clear_slot(k))

        preview = tk.Frame(card, bg=c["entry"], height=68, cursor="hand2")
        preview.pack(fill="x", pady=(6, 4))
        preview.pack_propagate(False)
        image_label = tk.Label(preview, bg=c["entry"], text="+",
                               fg=c["muted"], font=("Segoe UI", 16),
                               cursor="hand2")
        image_label.place(relx=0.5, rely=0.5, anchor="center")
        for widget in (preview, image_label):
            widget.bind("<Button-1>",
                        lambda _e, k=slot["key"]: self._cursor_pick(k))

        meta = tk.Label(card, text="не задан", bg=c["surface2"],
                        fg=c["muted"], font=("Segoe UI", 8), anchor="w")
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
                                           font=("Segoe UI", 16))
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
                                           font=("Segoe UI", 10, "bold"))
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
        self._button(row, "Применить", self.apply_default,
                     side="left", padx=(8, 0))

        # Roblox на этом ПК
        inner3 = self._card(pad, title="Roblox на этом ПК", glyph="◉")
        tk.Label(inner3, textvariable=self.proto_var, bg=c["surface"],
                 fg=c["text"], font=("Segoe UI", 11), anchor="w",
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
                                 fg=c["muted"], font=("Segoe UI", 9),
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

        tk.Label(inner4, text="Акцентный цвет", bg=c["surface"],
                 fg=c["text"], font=("Segoe UI", 10)).pack(
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
                                          font=("Segoe UI", 9))
        self.accent_name_label.pack(side="left", padx=8)

        # о программе
        inner5 = self._card(pad, title="О программе", glyph="ⓘ")
        tk.Label(
            inner5, bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9),
            justify="left", anchor="w", wraplength=540, text=(
                "Лаунчер передаёт системе ссылку roblox:// — игру открывает "
                "официальный клиент Roblox.\n"
                "Код игры не изменяется: лаунчер только пишет конфиги и, "
                "если включены наборы курсоров, заменяет PNG-файлы "
                "курсоров.\n"
                "Название, версия, тема и списки хранятся локально."
            )).pack(fill="x")
        tk.Label(inner5, text=str(CONFIG_PATH), bg=c["surface"],
                 fg=c["muted"], font=("Segoe UI", 9), anchor="w",
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
                 font=("Segoe UI", 11, "bold")).pack(side="left")
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

    def apply_default(self):
        raw = self.var_default.get().strip()
        if not raw:
            self.cfg["default_place_id"] = ""
            save_config(self.cfg)
            self.status_var.set("Игра по умолчанию очищена")
            return
        pid = parse_place_id(raw)
        if not pid:
            self.status_var.set("Некорректный ID")
            if self.interactive:
                messagebox.showwarning(
                    APP_NAME, "Не удалось распознать Place ID.",
                    parent=self.root)
            return
        self.var_default.set(pid)
        self.cfg["default_place_id"] = pid
        save_config(self.cfg)
        self.status_var.set(f"Игра по умолчанию: {pid}")

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

    def close(self):
        """Закрытие: запоминаем геометрию окна и выходим."""
        if self._closing:
            return
        self._closing = True
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
            version = Path(detail).parent.name or Path(detail).name
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
        cursor_name = str(self.cfg.get("cursor_set") or "")
        if self.cfg.get("use_custom_cursors") and not before and cursor_name:
            cursor_result = apply_cursor_set(cursor_name)
            self.q.put(lambda r=cursor_result: self._cursor_launch_done(r))
        if before:
            focused = False
            try:
                focused = focus_roblox_window(before)
            except Exception:
                focused = False
            self.q.put(lambda: self._client_result("already", focused))
            return

        # 1) прямой запуск клиента
        if kind == "installed":
            exe = find_player_exe()
            if exe:
                try:
                    subprocess.Popen([str(exe)], cwd=str(exe.parent))
                    if self._wait_for_new_pids(before, 8):
                        self.q.put(lambda: self._client_result("exe", True))
                        return
                except OSError:
                    pass

        # 2) голый roblox:// протокол
        try:
            os.startfile("roblox://")
            if self._wait_for_new_pids(before, 6):
                self.q.put(lambda: self._client_result("protocol", True))
                return
        except OSError:
            pass

        self.q.put(lambda: self._client_result("fail", False))

    @staticmethod
    def _wait_for_new_pids(before, seconds):
        for _ in range(seconds):
            time.sleep(1)
            new = player_pids() - before
            if new:
                time.sleep(1.5)
                if player_pids() & new:
                    return True
        return False

    def _client_result(self, outcome, focused):
        if self._closing:
            return
        self._client_busy = False
        self._btn_state(self.btn_client, "primary", True,
                        text="▶   ПРОСТО В ROBLOX")
        if outcome == "already":
            if focused:
                self.status_var.set("Roblox уже запущен — окно впереди")
                self.log("Roblox уже запущен, окно выведено на передний план")
            else:
                self.status_var.set("Roblox уже запущен")
                self.log("Roblox уже запущен (окно вывести не удалось)")
        elif outcome in ("exe", "protocol"):
            how = ("прямым запуском клиента" if outcome == "exe"
                   else "через roblox://")
            self.status_var.set("Roblox запущен ✓")
            self.log(f"Клиент Roblox запущен {how}")
        else:
            self.status_var.set("Не удалось — открыл Roblox в браузере")
            self.log("Прямой запуск не сработал, открыт сайт Roblox")
            webbrowser.open(DISCOVER_URL)

    def _client_missing(self):
        if self._closing:
            return
        self._client_busy = False
        self._btn_state(self.btn_client, "primary", True,
                        text="▶   ПРОСТО В ROBLOX")
        self.log("Клиент не найден — предлагаю скачать")
        if self.interactive and messagebox.askyesno(
                APP_NAME, "Roblox не установлен.\n"
                "Скачать официальный установщик?",
                parent=self.root):
            self.show_page("settings")
            self.start_download()
        else:
            self.status_var.set("Roblox не установлен — см. Настройки")

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
        url = f"roblox://experiences/start?placeId={pid}"
        self.log(f"Запуск: {name or 'игра'} (place {pid})")
        try:
            os.startfile(url)
        except OSError as exc:
            self.log(f"Ошибка запуска: {exc}")
            self.status_var.set("Ошибка запуска")
            if self.interactive:
                messagebox.showerror(
                    APP_NAME,
                    f"Не удалось открыть ссылку roblox://\n\n{exc}",
                    parent=self.root)
            return

        self.entry_var.set(pid)
        self.status_var.set(f"Запускаю {name or pid}…")
        self.log(f"Ссылка передана системе: {url}")

        if self.cfg["save_history"]:
            history = [h for h in self.cfg["history"] if h["place_id"] != pid]
            history.insert(0, {"place_id": pid, "name": name,
                               "ts": int(time.time())})
            self.cfg["history"] = history[:30]
        if self.cfg["remember_last"]:
            self.cfg["last_place_id"] = pid
        save_config(self.cfg)
        self._refresh_lists()

        if self.cfg["auto_close"]:
            self.log("Автозакрытие через 1.5 с…")
            self.root.after(1500, self.close)

    # -- оформление ------------------------------------------------------------------------------

    def set_theme(self, name, save=True):
        if name not in THEMES:
            return
        label = THEMES[name]["label"]
        self.theme_name = name
        self.c = THEMES[name]
        if save:
            self.cfg["theme"] = name
            save_config(self.cfg)
        self._rebuild()
        self.status_var.set(f"Тема: {label}")
        if save:
            self.log(f"Тема оформления: {label}")

    def set_accent(self, color):
        color = color.lower()
        self.accent = color
        self.cfg["accent"] = color
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
                    getattr(self, "cursor_sets_box", None)):
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
        root = tk.Tk()
        root.withdraw()
        app = App(root, interactive=False)
        root.update_idletasks()
        root.update()

        for page_key in ("flags", "cursors", "favorites", "settings", "log",
                         "game"):
            app.show_page(page_key)
            root.update()
            if app.current_page != page_key:
                failures.append(f"show_page({page_key}) не сработал")

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
        if original_theme != app.theme_name:
            app.set_theme(original_theme, save=False)
            root.update()
        if app.cfg["theme"] != load_config()["theme"]:
            failures.append("set_theme(save=False) записал тему в конфиг")

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

        root.withdraw()
        app._closing = True        # не пишем тестовую геометрию в конфиг
        root.destroy()
    except Exception as exc:  # noqa: BLE001
        failures.append(f"UI smoke test: {type(exc).__name__}: {exc}")

    if failures:
        print("SELFTEST FAILED")
        for item in failures:
            print("  -", item)
        return 1
    print("SELFTEST OK: core, themes, frameless window, flags, cursors, "
          "UI rebuild — all good")
    return 0


_INSTANCE = None


def main():
    global _INSTANCE
    if "--selftest" in sys.argv:
        sys.exit(run_selftest())

    _INSTANCE = acquire_single_instance()
    if _INSTANCE is False:
        # второй экземпляр: его окно уже выведено вперёд
        return

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
