#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Питомец Maklyxa: сидит на верхней кромке окна лаунчера.

Отдельное прозрачное окно без рамки, которое держится над шапкой лаунчера:
роблоксер свесил ноги через кромку, покачивается, моргает и подпрыгивает
на клик. Вместо роблоксера можно загрузить свою картинку или GIF — она
так же качается и подпрыгивает.
"""

import ctypes
import math
import random
import sys
import time
import tkinter as tk

try:
    from PIL import Image, ImageSequence, ImageTk
    HAVE_PIL = True
except Exception:  # noqa: BLE001 — без Pillow остаётся роблоксер
    HAVE_PIL = False

KEY_COLOR = "#010203"          # цвет-ключ прозрачности окна
KEY_RGB = (1, 2, 3)
YELLOW = "#f5cd30"
BLUE = "#0d69ac"
GREEN = "#4b974b"
INK = "#2a2118"
WHITE = "#ffffff"

WIDTH = 150
HEIGHT = 152
FOOT_DROP = 16                 # насколько ступни заходят на шапку лаунчера
RIGHT_OFFSET = 236             # отступ от правого края окна
TICK_MS = 40
SWAY_SPEED = 0.075
BODY_PIVOT = (76.0, 130.0)
HIP = (76.0, 104.0)
SHOULDER_Y = 74.0

CUSTOM_BOX = 128               # максимум для своей картинки по стороне
CUSTOM_BOTTOM = 146            # нижняя кромка картинки в окне питомца
MAX_CUSTOM_FRAMES = 48
SW_SHOWNOACTIVATE = 4
SW_HIDE = 0


def _rotate(x, y, angle, pivot):
    px, py = pivot
    if not angle:
        return (x, y)
    ca, sa = math.cos(angle), math.sin(angle)
    dx, dy = x - px, y - py
    return (px + dx * ca - dy * sa, py + dx * sa + dy * ca)


def rounded_box(cx, cy, width, height, radius, angle=0.0, pivot=None):
    """Контур скруглённого прямоугольника, повёрнутый вокруг pivot."""
    hw, hh = width / 2.0, height / 2.0
    radius = max(0.0, min(radius, hw, hh))
    ix, iy = hw - radius, hh - radius
    corners = ((ix, -iy, -90.0), (ix, iy, 0.0),
               (-ix, iy, 90.0), (-ix, -iy, 180.0))
    points = []
    for ox, oy, start in corners:
        for step in range(5):
            a = math.radians(start + step * 22.5)
            points.append((cx + ox + radius * math.cos(a),
                           cy + oy + radius * math.sin(a)))
    if angle and pivot is not None:
        points = [_rotate(x, y, angle, pivot) for x, y in points]
    return points


def _virtual_screen():
    try:
        user32 = ctypes.windll.user32
        return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
                user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))
    except Exception:
        return (0, 0, 1920, 1080)


def _our_window_foreground():
    try:
        import os
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == os.getpid()
    except Exception:
        return True


class Mascot:
    """Окно-питомец поверх верхней кромки окна лаунчера."""

    def __init__(self, root, enabled=True, is_maximized=None, custom_path=""):
        self.root = root
        self.is_maximized = is_maximized or (lambda: False)
        self.enabled = False
        self.win = None
        self.canvas = None
        self._items = {}
        self._supported = True
        self._job = None
        self._phase = 0.0
        self._kick = 0.0
        self._pos = None
        self._shown = False
        self._visible = False
        self._hwnd = None
        self._topmost = None
        self._topmost_ts = 0.0
        self._blink_at = time.monotonic() + 3.0
        self._blink_until = 0.0
        self._destroyed = False
        self._errors = 0
        self._custom = False
        self._frames = []
        self._durations = []
        self._frame_idx = 0
        self._frame_age = 0.0
        self._photo_cache = {}
        self._photo = None
        self._build()
        self._map_window()
        self.set_enabled(bool(enabled))
        if custom_path:
            self.load_custom(custom_path)

    # -- создание ---------------------------------------------------------------

    def _build(self):
        try:
            win = tk.Toplevel(self.root)
        except tk.TclError:
            self._supported = False
            return
        win.withdraw()
        win.overrideredirect(True)
        win.configure(bg=KEY_COLOR, bd=0, highlightthickness=0)
        for name, value in (("-topmost", True), ("-toolwindow", True)):
            try:
                win.attributes(name, value)
            except tk.TclError:
                pass
        try:
            win.attributes("-transparentcolor", KEY_COLOR)
        except tk.TclError:
            self._supported = False
            win.destroy()
            return
        canvas = tk.Canvas(win, width=WIDTH, height=HEIGHT, bg=KEY_COLOR,
                           bd=0, highlightthickness=0, takefocus=False)
        canvas.pack()
        canvas.bind("<Button-1>", self._on_click)
        self.win, self.canvas = win, canvas

        def polygon(color):
            return canvas.create_polygon(*([0, 0] * 3), fill=color,
                                         outline=color)

        self._items = {
            "leg_l": polygon(GREEN),
            "leg_r": polygon(GREEN),
            "arm_l": polygon(YELLOW),
            "arm_r": polygon(YELLOW),
            "torso": polygon(BLUE),
            "head": polygon(YELLOW),
            "eye_l": canvas.create_oval(0, 0, 0, 0, fill=INK, outline=INK),
            "eye_r": canvas.create_oval(0, 0, 0, 0, fill=INK, outline=INK),
            "mouth": canvas.create_line(0, 0, 0, 0, fill=INK, width=3,
                                        capstyle="round", smooth=True),
            "brand": canvas.create_line(0, 0, 0, 0, fill=WHITE, width=3,
                                        capstyle="round", joinstyle="round"),
            "pet_image": canvas.create_image(
                WIDTH // 2, HEIGHT // 2, anchor="center", state="hidden"),
        }
        self._paint()

    def _map_window(self):
        """Окно создаётся и отображается один раз, дальше им правит WinAPI.

        Tk плохо переносит withdraw/deiconify у overrideredirect-окон, а
        Windows сам прячет дочерние окна вместе со свёрнутым родителем.
        Поэтому окно всегда «показано» для Tk, а видимость и прозрачность
        выставляются атрибутом alpha и ShowWindow.
        """
        if not self._supported or self.win is None:
            return
        vx, vy, _vw, _vh = _virtual_screen()
        try:
            self.win.geometry(f"{WIDTH}x{HEIGHT}+{vx - WIDTH - 80}+{vy}")
            self.win.attributes("-alpha", 0.0)
            self.win.deiconify()
            self._hwnd = self._top_hwnd()
        except tk.TclError:
            self._supported = False

    def _top_hwnd(self):
        """Актуальный HWND окна: Tk пересоздаёт его при смене атрибутов."""
        try:
            hwnd = int(self.win.winfo_id())
            top = ctypes.windll.user32.GetAncestor(
                ctypes.c_void_p(hwnd), 2) or hwnd  # GA_ROOT
            return int(top)
        except Exception:  # noqa: BLE001
            return None

    # -- включение и выключение ----------------------------------------------------

    def set_enabled(self, enabled):
        self.enabled = bool(enabled) and self._supported
        if not self._supported:
            return
        if self.enabled:
            self._phase = 0.0
            self._kick = 0.0
            self._follow(force_show=True)
            if self._job is None:
                self._job = self.root.after(TICK_MS, self._tick)
        else:
            self._cancel_job()
            self._hide()

    def destroy(self):
        self._destroyed = True
        self._cancel_job()
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None

    def _cancel_job(self):
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except (tk.TclError, ValueError):
                pass
            self._job = None

    # -- свой питомец ----------------------------------------------------------------

    @property
    def custom(self):
        return self._custom

    def load_custom(self, path):
        """Ставит свою картинку вместо роблоксера. Возвращает (ok, ошибка)."""
        if not HAVE_PIL:
            return False, "Для своей картинки нужен Pillow"
        try:
            frames, durations = [], []
            with Image.open(str(path)) as src:
                for frame in ImageSequence.Iterator(src):
                    rgba = frame.convert("RGBA")
                    rgba.thumbnail((CUSTOM_BOX, CUSTOM_BOX), Image.LANCZOS)
                    frames.append(rgba)
                    durations.append(
                        max(60, int(frame.info.get("duration") or 100)))
                    if len(frames) >= MAX_CUSTOM_FRAMES:
                        break
        except Exception as exc:  # noqa: BLE001 — битый файл
            return False, f"Не удалось прочитать картинку:\n{exc}"
        if not frames:
            return False, "В файле нет картинки"
        if not self._supported or self.canvas is None:
            return False, "Окно питомца недоступно"
        self._frames = frames
        self._durations = durations
        self._frame_idx = 0
        self._frame_age = 0.0
        self._photo_cache = {}
        self._photo = None
        self._custom = True
        self._set_body_visible(False)
        self._paint()
        return True, ""

    def clear_custom(self):
        if not self._custom:
            return
        self._custom = False
        self._frames = []
        self._durations = []
        self._photo_cache = {}
        self._photo = None
        if self.canvas is not None:
            self._set_body_visible(True)
            self._paint()

    def _set_body_visible(self, visible):
        body = "normal" if visible else "hidden"
        image = "hidden" if visible else "normal"
        for key, item in self._items.items():
            state = image if key == "pet_image" else body
            try:
                self.canvas.itemconfigure(item, state=state)
            except tk.TclError:
                pass

    def _custom_photo(self):
        if not self._frames:
            return None
        sway = 0.075 * math.sin(self._phase)
        lift = 1.5 * math.sin(self._phase * 2.0) - 7.0 * self._kick
        angle = round(math.degrees(sway * 1.3), 1)
        lift_px = int(round(lift))
        key = (self._frame_idx, angle, lift_px)
        photo = self._photo_cache.get(key)
        if photo is not None:
            return photo
        frame = self._frames[self._frame_idx]
        turned = frame.rotate(angle, resample=Image.BICUBIC, expand=True)
        board = Image.new("RGB", (WIDTH, HEIGHT), KEY_RGB)
        board.paste(turned, ((WIDTH - turned.width) // 2,
                             CUSTOM_BOTTOM - turned.height - lift_px),
                    turned)
        photo = ImageTk.PhotoImage(board)
        if len(self._photo_cache) > 96:
            self._photo_cache.clear()
        self._photo_cache[key] = photo
        return photo

    # -- цикл ---------------------------------------------------------------------------

    def _tick(self):
        self._job = None
        if self._destroyed or not self.enabled:
            return
        try:
            self._follow()
            if self._shown:
                self._animate()
        except tk.TclError:
            pass
        except Exception as exc:  # noqa: BLE001 — цикл не должен умирать молча
            if self._errors < 4:
                print(f"mascot: {type(exc).__name__}: {exc}", file=sys.stderr)
            self._errors += 1
        if not self._destroyed:
            try:
                self._job = self.root.after(TICK_MS, self._tick)
            except tk.TclError:
                self._job = None

    def _animate(self):
        self._phase += SWAY_SPEED
        if self._kick > 0:
            self._kick = max(0.0, self._kick - 0.05)
        now = time.monotonic()
        if self._custom:
            if len(self._frames) > 1:
                self._frame_age += TICK_MS / 1000.0
                delay = self._durations[self._frame_idx] / 1000.0
                while self._frame_age >= delay:
                    self._frame_age -= delay
                    self._frame_idx = (self._frame_idx + 1) % len(self._frames)
                    delay = self._durations[self._frame_idx] / 1000.0
        elif now >= self._blink_at:
            self._blink_until = now + 0.11
            self._blink_at = now + random.uniform(2.4, 5.2)
        self._paint()

    def _follow(self, force_show=False):
        if not self._supported or self.win is None:
            return
        root = self.root
        if not root.winfo_exists():
            return
        if (root.winfo_width() < 320 or not root.winfo_ismapped()
                or self._iconic() or self.is_maximized()):
            self._hide()
            return
        rx, ry = root.winfo_rootx(), root.winfo_rooty()
        rw = root.winfo_width()
        x = rx + rw - RIGHT_OFFSET
        if x < rx + 16:
            x = rx + rw - WIDTH - 16
        y = ry - (HEIGHT - FOOT_DROP)
        vx, vy, vw, vh = _virtual_screen()
        x = max(vx, min(x, vx + vw - WIDTH))
        y = max(vy, min(y, vy + vh - HEIGHT))
        self._show(x, y)
        self._sync_topmost(force=force_show)

    def _show(self, x, y):
        if (x, y) != self._pos:
            try:
                self.win.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
            except tk.TclError:
                return
            self._pos = (x, y)
        try:
            if not self._visible:
                self.win.attributes("-alpha", 1.0)
                self._visible = True
        except tk.TclError:
            return
        if not self._shown:
            self._shown = True
            try:
                self.win.lift()
            except tk.TclError:
                pass
            self._sync_topmost(force=True)
            self._paint()
        self._ensure_visible()

    def _ensure_visible(self):
        """Возвращает окно, если его спрятала Windows вместе с родителем."""
        try:
            user32 = ctypes.windll.user32
            hwnd = self._top_hwnd()
            if hwnd and not user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
                user32.ShowWindow(ctypes.c_void_p(hwnd), SW_SHOWNOACTIVATE)
                self.win.lift()
        except Exception:  # noqa: BLE001 — WinAPI не обязательно
            pass

    def _hide(self):
        self._shown = False
        self._pos = None
        if not self._visible or self.win is None:
            return
        self._visible = False
        try:
            self.win.attributes("-alpha", 0.0)
            vx, vy, _vw, _vh = _virtual_screen()
            self.win.geometry(f"{WIDTH}x{HEIGHT}+{vx - WIDTH - 80}+{vy}")
        except tk.TclError:
            pass

    def _sync_topmost(self, force=False):
        now = time.monotonic()
        if not force and now - self._topmost_ts < 0.4:
            return
        self._topmost_ts = now
        want = _our_window_foreground()
        if want == self._topmost:
            return
        self._topmost = want
        try:
            self.win.attributes("-topmost", bool(want))
            if want:
                self.win.lift()
        except tk.TclError:
            pass

    def _iconic(self):
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.root.winfo_id()) \
                or self.root.winfo_id()
            return bool(user32.IsIconic(ctypes.c_void_p(hwnd)))
        except Exception:
            return False

    def _on_click(self, _event=None):
        self._kick = 1.0
        self._blink_until = 0.0
        try:
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    # -- отрисовка ---------------------------------------------------------------------

    def _paint(self):
        if self.canvas is None:
            return
        if self._custom:
            photo = self._custom_photo()
            if photo is not None:
                self._photo = photo
                try:
                    self.canvas.itemconfigure(self._items["pet_image"],
                                              image=photo)
                except tk.TclError:
                    pass
            return
        c = self.canvas
        phase = self._phase
        kick = self._kick
        blink = time.monotonic() < self._blink_until
        sway = 0.075 * math.sin(phase)
        body = sway - 0.06 * kick
        head = sway * 1.55
        lift = 1.5 * math.sin(phase * 2.0) - 7.0 * kick
        arm_l = body + 0.11 * math.sin(phase + 1.2) - 0.45 * kick
        arm_r = body - 0.11 * math.sin(phase + 1.2) - 0.45 * kick
        leg_l = 0.16 * math.sin(phase) + 0.14 * kick
        leg_r = 0.12 * math.sin(phase + 0.5) + 0.14 * kick
        cx = 76.0

        def put(key, points):
            c.coords(self._items[key], *[v for point in points for v in point])

        put("leg_l", rounded_box(cx - 17, 122, 18, 42, 8, leg_l, HIP))
        put("leg_r", rounded_box(cx + 17, 122, 18, 42, 8, leg_r, HIP))
        put("arm_l", rounded_box(cx - 33, 90 + lift, 15, 38, 7, arm_l,
                                 (cx - 24, SHOULDER_Y + lift)))
        put("arm_r", rounded_box(cx + 33, 90 + lift, 15, 38, 7, arm_r,
                                 (cx + 24, SHOULDER_Y + lift)))
        put("torso", rounded_box(cx, 84 + lift, 46, 44, 11, body, BODY_PIVOT))
        put("head", rounded_box(cx, 38 + lift, 62, 52, 13, head, BODY_PIVOT))

        eye_w, eye_h = 4.2, (0.9 if blink else 4.6)
        for key, ex in (("eye_l", cx - 12.5), ("eye_r", cx + 12.5)):
            ex, ey = _rotate(ex, 38 + lift, head, BODY_PIVOT)
            c.coords(self._items[key], ex - eye_w, ey - eye_h,
                     ex + eye_w, ey + eye_h)

        mouth = []
        for step in range(9):
            t = step / 8.0
            mx = cx - 10 + 20 * t
            my = 50 + lift + 5.5 * math.sin(math.pi * t)
            mouth.append(_rotate(mx, my, head, BODY_PIVOT))
        c.coords(self._items["mouth"], *[v for point in mouth for v in point])

        brand = [(-9, 92), (-9, 77), (0, 87), (9, 77), (9, 92)]
        c.coords(self._items["brand"],
                 *[v for x, y in brand
                   for v in _rotate(cx + x, y + lift, body, BODY_PIVOT)])
