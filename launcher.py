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
from tkinter import colorchooser, filedialog, messagebox, ttk

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
    HAVE_PIL = True
except Exception:  # Pillow не обязателен для запуска
    HAVE_PIL = False

from core import (
    ACCENT_PRESETS, APP_NAME, BOOT_BOOLS_FROST, BOOT_BOOLS_SHARED,
    BUNDLE_DIR, CONFIG_DIR, CONFIG_PATH, CURSOR_SLOTS,
    DEFAULT_CONFIG, DEFAULT_THEME,
    DISCORD_PORTAL_URL, DISCOVER_URL, FPS_CHOICES, FLAG_API, FLAG_FPS, FLAG_QUALITY,
    GRAPHICS_MODES, HTTP_HEADERS, INSTALLER_URL,
    IMAGE_ONLY_ACCENT, IMAGE_ONLY_PALETTE,
    THEMES, THEME_ORDER, VERSION, WINDOW_TITLE,
    THEME_SETS_DIR, THEME_TINTS,
    account_avatar_path, account_session, accounts_state, add_account,
    acquire_single_instance, apply_cursor_set, apply_fastflags,
    bootstrapper_running, browser_login, build_theme_palette,
    client_settings_paths, close_roblox_players, copy_current_cursor_set,
    create_cursor_set, create_theme_set, cursor_install_roots,
    cursor_set_files, cursor_set_warnings, cursor_slot_path,
    cursor_state_info, datacenter_regions, delete_cursor_set,
    delete_theme_set, ensure_cursor_sets_dir, ensure_theme_sets_dir,
    export_cursor_set, export_theme_set, fetch_game_icon, fetch_game_info,
    fastflag_values, find_bootstrapper_settings, find_player_exe,
    focus_roblox_window, fmt_num, game_launch_url, hit_test, import_cursor_set,
    import_theme_set, launch_game_as, list_cursor_sets, list_theme_sets,
    load_config, load_theme_palette, mb, merge_flags_into,
    migrate_old_config, parse_place_id, png_info, player_pids,
    probe_protocol, protect_secret, read_json_dict, remove_account,
    remove_cursor_slot, remove_theme_image, rename_cursor_set,
    rename_theme_set, resize_geometry, restore_custom_cursors,
    roblox_status, save_config, set_active_account, set_cursor_slot,
    set_theme_image, shade, theme_set_info, trunc, unprotect_secret,
    update_theme_set, valid_cursor_set_name,
    valid_discord_app_id, valid_geometry, valid_theme_set_name, work_area,
    write_json_dict, DiscordPresence, discord_activity, discord_frame,
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

CURSORS = {
    "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
    "e": "sb_h_double_arrow", "w": "sb_h_double_arrow",
    "ne": "sizing", "nw": "sizing", "se": "sizing", "sw": "sizing",
}

NAV_ITEMS = [
    ("game", "▶", "Игра"),
    ("accounts", "◉", "Аккаунты"),
    ("favorites", "★", "Избранное"),
    ("flags", "⚑", "Флаги"),
    ("cursors", "◈", "Курсоры"),
    ("themes", "◐", "Темы"),
    ("settings", "⚙", "Настройки"),
    ("log", "≡", "Журнал"),
]

NAV_GROUPS = [
    ("ЗАПУСК", ("game", "accounts", "favorites")),
    ("ПРЕДУСТАНОВЛЕННЫЕ\nМОДИФИКАЦИИ", ("flags", "cursors", "themes")),
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
        self._start_presence()
        self.discord_status_var.set(self._discord_status_text())
        if self.interactive:
            self._poll_discord_status()

    # -- сборка / пересборка ----------------------------------------------------

    def _build(self, keep_page=None):
        self._build_styles()
        self._build_shell()
        self._build_page_game()
        self._build_page_accounts()
        self._build_page_favorites()
        self._build_page_flags()
        self._build_page_cursors()
        self._build_page_themes()
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

        theme_lbl = self.c["label"]
        self.theme_cycle_btn = tk.Button(
            titlebar, text=f"◐  {theme_lbl}", relief="flat", bd=0,
            font=("Segoe UI", 9), cursor="hand2", takefocus=False,
            bg=c["surface2"], fg=c["text"], activebackground=c["hover"],
            activeforeground=c["text"], highlightthickness=0, padx=12,
            command=self._cycle_theme)
        self.theme_cycle_btn.pack(side="right", padx=(0, 12))
        self._bind_hover(self.theme_cycle_btn, "ghost")

        self.account_switch_btn = tk.Menubutton(
            titlebar, text="◉  Войти", relief="flat", bd=0,
            font=("Segoe UI", 9), cursor="hand2", takefocus=False,
            bg=c["surface2"], fg=c["text"], activebackground=c["hover"],
            activeforeground=c["text"], highlightthickness=0, padx=12)
        self.account_menu = tk.Menu(
            self.account_switch_btn, tearoff=0, bg=c["surface"],
            fg=c["text"], activebackground=self.accent,
            activeforeground=c["on_accent"], bd=0, font=("Segoe UI", 9))
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

        # активный аккаунт — быстрый переход к переключателю
        acc_card = tk.Frame(sidebar, bg=c["border"], cursor="hand2")
        acc_card.pack(fill="x", padx=12, pady=(10, 0))
        acc_inner = tk.Frame(acc_card, bg=c["surface"], padx=12, pady=8,
                             cursor="hand2")
        acc_inner.pack(fill="x", padx=1, pady=1)
        acc_head = tk.Frame(acc_inner, bg=c["surface"], cursor="hand2")
        acc_head.pack(fill="x")
        acc_dot = tk.Label(acc_head, text="●", bg=c["surface"],
                           fg=self.accent, font=("Segoe UI", 7),
                           cursor="hand2")
        acc_dot.pack(side="left")
        tk.Label(acc_head, text="АККАУНТ", bg=c["surface"], fg=c["muted"],
                 font=("Segoe UI", 8, "bold"), cursor="hand2").pack(
                     side="left", padx=(6, 0))
        self.sidebar_account_dot = acc_dot
        acc_name = tk.Label(acc_inner, textvariable=self.account_name_var,
                            bg=c["surface"], fg=c["text"],
                            font=("Segoe UI", 9, "bold"), anchor="w",
                            cursor="hand2")
        acc_name.pack(fill="x", pady=(4, 0))
        acc_user = tk.Label(acc_inner, textvariable=self.account_user_var,
                            bg=c["surface"], fg=c["muted"],
                            font=("Segoe UI", 8), anchor="w",
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

    # -- фон из картинки своей темы -------------------------------------------------

    def _theme_image_source(self):
        source = str(self.c.get("image") or "")
        try:
            if source and Path(source).is_file():
                return source
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
                               fill=accent, font=("Segoe UI", 8, "bold"))
            canvas.create_text(0, 24, text=title, anchor="nw", fill=c["text"],
                               font=("Segoe UI", 19, "bold"))
            badge = 44
        else:
            canvas.create_rectangle(0, 18, 18, 20, fill=accent, outline="")
            canvas.create_text(26, 11, text="M A K L Y X A", anchor="nw",
                               fill=accent, font=("Segoe UI", 8, "bold"))
            canvas.create_text(0, 30, text=title, anchor="nw", fill=c["text"],
                               font=("Segoe UI", 22, "bold"))
            badge = 56
            if subtitle:
                canvas.create_text(
                    0, 72, text=subtitle, anchor="nw", fill=c["muted"],
                    font=("Segoe UI", 10), width=max(220, width - 130),
                    justify="left")
        by1 = 8
        bx2 = width - 8
        _round_rect(canvas, bx2 - badge, by1, bx2, by1 + badge, 12,
                    fill=c["surface2"], outline=c["border"], width=1)
        canvas.create_text(bx2 - badge // 2, by1 + badge // 2, text=glyph,
                           fill=accent,
                           font=("Segoe UI", 16 if compact else 20, "bold"))

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

        acc_strip = tk.Frame(inner, bg=c["surface2"], padx=11, pady=8)
        acc_strip.pack(fill="x", pady=(12, 0))
        acc_glyph = tk.Label(acc_strip, text="◉", bg=c["surface2"],
                             fg=self.accent, font=("Segoe UI", 12, "bold"))
        acc_glyph.pack(side="left")
        self.accent_fgs.append(acc_glyph)
        acc_text = tk.Frame(acc_strip, bg=c["surface2"])
        acc_text.pack(side="left", fill="x", expand=True, padx=(10, 8))
        tk.Label(acc_text, text="АККАУНТ ДЛЯ ЗАПУСКА", bg=c["surface2"],
                 fg=c["muted"], font=("Segoe UI", 8, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(acc_text, textvariable=self.game_account_var,
                 bg=c["surface2"], fg=c["text"], font=("Segoe UI", 9),
                 anchor="w", justify="left", wraplength=420).pack(fill="x")
        self._button(acc_strip, "Сменить",
                     lambda: self.show_page("accounts"), side="right")

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
            font=("Segoe UI", 26, "bold"))
        self.account_avatar_label.pack(fill="both", expand=True)
        self.accent_fgs.append(self.account_avatar_label)

        col = tk.Frame(head, bg=c["surface"])
        col.pack(side="left", fill="both", expand=True, padx=(16, 0))
        tk.Label(col, textvariable=self.active_display_var, bg=c["surface"],
                 fg=c["text"], font=("Segoe UI", 16, "bold"), anchor="w",
                 justify="left", wraplength=280).pack(fill="x")
        tk.Label(col, textvariable=self.active_login_var, bg=c["surface"],
                 fg=self.accent, font=("Segoe UI", 10, "bold"),
                 anchor="w").pack(fill="x", pady=(3, 3))
        tk.Label(col, textvariable=self.active_meta_var, bg=c["surface"],
                 fg=c["muted"], font=("Segoe UI", 8), anchor="w",
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
                 bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9),
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
                     fg=c["muted"], font=("Segoe UI", 9), anchor="w",
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
                 font=("Segoe UI", 16, "bold"), anchor="w").pack(fill="x")
        tk.Label(body, text="Вход только через сайт Roblox в браузере. "
                            "Пароль лаунчер не запрашивает и не сохраняет — "
                            "только зашифрованную сессию для запуска игр.",
                 bg=c["bg"], fg=c["muted"], font=("Segoe UI", 9),
                 anchor="w", justify="left", wraplength=430).pack(
                     fill="x", pady=(4, 14))

        outer = tk.Frame(body, bg=c["border"])
        outer.pack(fill="x", pady=(0, 12))
        br = tk.Frame(outer, bg=c["surface"], padx=14, pady=12)
        br.pack(fill="x", padx=1, pady=1)
        tk.Label(br, text="ВХОД ЧЕРЕЗ САЙТ", bg=c["surface"],
                 fg=c["muted"], font=("Segoe UI", 8, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(br, text="Откроется отдельное окно Edge или Chrome с "
                          "временным профилем. Войди на сайте Roblox — "
                          "лаунчер заберёт сессию и закроет окно. Капча "
                          "и 2FA вводятся прямо на сайте.",
                 bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9),
                 anchor="w", justify="left", wraplength=430).pack(
                     fill="x", pady=(8, 10))
        self._login_browser_btn = self._button(
            br, "Войти через сайт Roblox", self._account_login_browser,
            kind="primary")
        self._login_browser_btn.pack_configure(fill="x")
        tk.Label(br, text="Основной браузер не затрагивается: профиль "
                          "создаётся во временной папке и удаляется.",
                 bg=c["surface"], fg=c["muted"], font=("Segoe UI", 8),
                 anchor="w", justify="left", wraplength=430).pack(
                     fill="x", pady=(8, 0))

        self._login_status_var = tk.StringVar(value="")
        self._login_status_lbl = tk.Label(
            body, textvariable=self._login_status_var, bg=c["bg"],
            fg=c["muted"], font=("Segoe UI", 9), anchor="w",
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

        tk.Label(gridf, text="FPS можно ввести вручную: число от 1 до 1000. "
                            "«Как в игре» или пустое поле — без ограничения.",
                 bg=c["surface"], fg=c["muted"], font=("Segoe UI", 8),
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

    # -- страница «Темы» --------------------------------------------------------------

    def _build_page_themes(self):
        c = self.c
        pad = self._scroll_page("themes")

        self._page_header(
            pad, "◐", "Свои темы",
            "Собери оформление из базовых цветов, оттенка и своей картинки. "
            "Картинка становится фоном всего окна, а режим «только картинка» "
            "убирает цвета темы и оставляет фото.")

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
            font=("Segoe UI", 10), exportselection=False, height=10)
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
                 fg=c["muted"], font=("Segoe UI", 9), anchor="w",
                 justify="left", wraplength=460).pack(fill="x", pady=(7, 0))

        opts = tk.Frame(right, bg=c["surface"])
        opts.pack(fill="x", pady=(10, 0))
        opts.grid_columnconfigure(1, weight=1)

        def opt_row(row, label):
            tk.Label(opts, text=label, bg=c["surface"], fg=c["muted"],
                     font=("Segoe UI", 9), anchor="w").grid(
                         row=row, column=0, sticky="w", pady=4,
                         padx=(0, 12))
            holder = tk.Frame(opts, bg=c["surface"])
            holder.grid(row=row, column=1, sticky="w", pady=4)
            return holder

        self.theme_base_row = opt_row(0, "База")
        self.theme_tint_row = opt_row(1, "Оттенок")
        self.theme_accent_row = opt_row(2, "Акцент")
        tk.Label(opts, text="Картинка", bg=c["surface"], fg=c["muted"],
                 font=("Segoe UI", 9), anchor="w").grid(
                     row=3, column=0, sticky="w", pady=4, padx=(0, 12))
        image_row = tk.Frame(opts, bg=c["surface"])
        image_row.grid(row=3, column=1, sticky="w", pady=4)
        self.theme_pick_btn = self._button(
            image_row, "Выбрать", self._theme_pick_image, side="left")
        self.theme_image_remove_btn = self._button(
            image_row, "Убрать", self._theme_remove_image, side="left",
            padx=(6, 0))
        tk.Label(opts, textvariable=self.theme_image_var, bg=c["surface"],
                 fg=c["muted"], font=("Segoe UI", 8), anchor="w",
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
                 fg=c["text"], font=("Segoe UI", 9), anchor="w",
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
                font=("Segoe UI", 9, "bold" if active else "normal"),
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
                    fill=palette["muted"], font=("Segoe UI", 10))
        except tk.TclError:
            pass

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
            self.var_discord_rpc, command=self.apply_discord_settings).pack(
                anchor="w", pady=(4, 8))
        tk.Label(inner_d, text="Application ID", bg=c["surface"],
                 fg=c["text"], font=("Segoe UI", 10)).pack(anchor="w")
        row_d = tk.Frame(inner_d, bg=c["surface"])
        row_d.pack(fill="x", pady=(4, 0))
        ttk.Entry(row_d, textvariable=self.var_discord_id).pack(
            side="left", fill="x", expand=True, ipady=3)
        self._button(row_d, "Сохранить", self.apply_discord_settings,
                     kind="primary", side="left", padx=(8, 0))
        row_d2 = tk.Frame(inner_d, bg=c["surface"])
        row_d2.pack(fill="x", pady=(8, 0))
        self._button(row_d2, "Создать приложение",
                     self.open_discord_portal, side="left")
        tk.Label(inner_d, textvariable=self.discord_status_var,
                 bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9),
                 anchor="w").pack(fill="x", pady=(8, 0))
        self._hint(
            inner_d,
            "Application ID берётся у приложения с именем MaklyxaLauncher "
            "на discord.com/developers/applications. Для статуса нужен "
            "запущенный Discord для ПК.", wrap=540)

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
                 font=("Segoe UI", 8)).pack(side="left", padx=8)

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
        self.var_discord_rpc.set(bool(self.cfg.get("discord_rpc", True)))
        self.var_discord_id.set(self.cfg.get("discord_app_id") or "")

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

    def apply_discord_settings(self):
        raw = self.var_discord_id.get().strip()
        app_id = valid_discord_app_id(raw)
        if raw and not app_id:
            self.status_var.set("Application ID — только цифры")
            if self.interactive:
                messagebox.showwarning(
                    APP_NAME, "Application ID приложения Discord "
                    "состоит только из цифр.", parent=self.root)
            return
        self.cfg["discord_rpc"] = bool(self.var_discord_rpc.get())
        self.cfg["discord_app_id"] = app_id
        self.var_discord_id.set(app_id)
        save_config(self.cfg)
        self._stop_presence()
        self._start_presence()
        if not app_id:
            self.status_var.set("Discord: укажи Application ID")
        elif self.cfg["discord_rpc"]:
            self.status_var.set("Discord: статус включён")
        else:
            self.status_var.set("Discord: статус выключен")
        self.log("Discord Rich Presence: "
                 f"{'вкл' if self.cfg['discord_rpc'] else 'выкл'}"
                 + (f", ID {app_id}" if app_id else ""))

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

    def close(self):
        """Закрытие: запоминаем геометрию окна и выходим."""
        if self._closing:
            return
        self._closing = True
        self._stop_presence()
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
        self._rpc_set("Клиент Roblox", "Открываю клиент…")
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

    def _client_missing(self):
        if self._closing:
            return
        self._client_busy = False
        self._btn_state(self.btn_client, "primary", True,
                        text="▶   ПРОСТО В ROBLOX")
        self._rpc_set("В меню", "Roblox не установлен")
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
        game = name or f"Place {pid}"
        self.log(f"Запуск: {name or 'игра'} (place {pid})")
        active = self._active_account()
        if active is not None:
            who = active.get("display") or active.get("name") or "аккаунт"
            if player_pids():
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
                self._start_account_launch(pid, name, game, active)
                return
        self._launch_plain(pid, name, game)

    def _launch_plain(self, pid, name, game):
        """Обычный запуск roblox:// — без входа под аккаунтом."""
        if self._play_busy:
            return
        url = f"roblox://experiences/start?placeId={pid}"
        self._rpc_set(game, "Запускаю игру…")
        try:
            os.startfile(url)
        except OSError as exc:
            self.log(f"Ошибка запуска: {exc}")
            self.status_var.set("Ошибка запуска")
            self._rpc_set("В меню", "Ошибка запуска")
            if self.interactive:
                messagebox.showerror(
                    APP_NAME,
                    f"Не удалось открыть ссылку roblox://\n\n{exc}",
                    parent=self.root)
            return
        self.entry_var.set(pid)
        self.status_var.set(f"Запускаю {name or pid}…")
        self.log(f"Ссылка передана системе: {url}")
        self.root.after(
            10000, lambda g=game: self._rpc_set(g, "Играет в Roblox"))
        self._after_game_launch(pid, name)

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
        threading.Thread(
            target=self._account_launch_worker,
            args=(pid, name, game, account), daemon=True).start()

    def _account_launch_worker(self, pid, name, game, account):
        if player_pids():
            close_roblox_players()
            for _ in range(8):
                if not player_pids():
                    break
                time.sleep(0.5)
        result = launch_game_as(pid, account["id"])
        self.q.put(lambda: self._account_launch_done(
            pid, name, game, result))

    def _account_launch_done(self, pid, name, game, result):
        if self._closing:
            return
        self._play_busy = False
        self._btn_state(self.btn_play, "primary", True,
                        text="▶   ИГРАТЬ ПО ID")
        account = result.get("account") or {}
        if result.get("ok"):
            who = account.get("name") or "аккаунт"
            self.entry_var.set(pid)
            self.status_var.set(f"Запускаю {name or pid} под @{who}")
            self.log(f"Игра запущена под аккаунтом @{who} (place {pid})")
            self._rpc_set(game, "Играет в Roblox")
            self.root.after(
                10000, lambda g=game: self._rpc_set(g, "Играет в Roblox"))
            self._after_game_launch(pid, name)
            return
        error = result.get("error") or "Не удалось войти под аккаунтом."
        self.log(f"Аккаунт: {error}")
        self.status_var.set("Не удалось войти под аккаунтом")
        self._rpc_set("В меню", "Ошибка аккаунта")
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
    except Exception as exc:  # noqa: BLE001
        failures.append(f"accounts test: {exc}")

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

        root = tk.Tk()
        root.withdraw()
        app = App(root, interactive=False)
        root.update_idletasks()
        root.update()

        for page_key in ("game", "accounts", "favorites", "flags",
                         "cursors", "themes", "settings", "log"):
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
        if app._rpc is not None:
            failures.append("UI: presence запустился без интерактива")
        if app.discord_status_var.get() == "":
            failures.append("UI: пустой статус Discord")

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
          "cursors, UI rebuild — all good")
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
