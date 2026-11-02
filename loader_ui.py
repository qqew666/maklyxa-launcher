"""Стартовый экран-лоадер: маленькое окно 500×400 со стопкой карточек.

Экран рисуется целиком на canvas: окно раскрывается из плитки 200×200,
карточки разделов прокручиваются на пружине с затухающей полосой.
Края фигур сглаживаются полупрозрачными обводами, все переходы считаются
от реально прошедшего времени, поэтому анимация одинакова при любых кадрах.
Оформление, тексты и разделы берутся из лаунчера.
"""

import math
import time
import tkinter as tk
from tkinter import font as tkfont

SPLASH_SIZE = 200
SPLASH_SEC = 2.00
EXPAND_SEC = 0.36
CONTENT_FADE = 0.44
BOOT_FADE = 0.24
EXIT_FADE = 0.30
FADE_OUT = 0.20

WIN_W = 500
WIN_H = 400
FOOTER_H = 56.0

LOGO_X = 18
LOGO_Y = 15
LOGO_SIZE = 38
TITLE_Y = 19
SUB_Y = 48
HEADER_RIGHT_PAD = 50

LINK_X = 20
LINK_Y = 82
LINK_STEP = 30
LINK_H = 22

CARDS_Y = 80
CARD_H = 76
CARD_GAP = 10
CARD_ROUND = 11
CARD_BORDER = 1.5
CARD_PADX = 15
CARD_TITLE_Y = 15
CARD_STATUS_Y = 40
CARD_ICON = 22
CARD_ICON_PAD = 12
CARD_ICON_ROUND = 6
CARD_DOT_X = 15
CARD_TEXT_SHIFT = 12
CARD_BAR_W = 3.0

INACTIVE_DIM = 0.5
DIM_SPEED = 6.5
CONTAINER_ANIM_SEC = 0.5

WHEEL_SPEED = 60.0
SPRING_K = 200.0
SPRING_D = 25.0
SNAP_DIST = 0.35
SNAP_VEL = 8.0
OVERSHOOT_VEL = 40.0
REST_VEL = 0.01
SCROLL_IDLE = 0.60
BAR_SPEED = 10.0
BAR_W = 4.0
BAR_MIN = 24.0

HOVER_SPEED = 10.0
PRESS_SPEED = 13.0
NOTE_SPEED = 9.0
SOON_ALPHA = 180.0 / 255.0

# Tk не сглаживает края полигонов: снаружи заливаем полупрозрачными обводами,
# лесенка пикселей уходит в эту мягкую кромку.
SPLINE_STEPS = 24
FEATHER = ((0.6, 0.62), (1.6, 0.30), (2.6, 0.13))
FEATHER_SOFT = ((0.6, 0.45), (1.6, 0.18))
ENTRY_STAGGER = 0.07
ENTRY_TRAVEL = 9.0

SPLASH_R = 18.0
SPLASH_THICK = 3.0
SPIN_SEGS = 90
SPIN_SPEED = 17.0
SPIN_FADE = 0.75 * 2.0 * math.pi

LOOK_X0 = 20
LOOK_R = 7.0
LOOK_GAP = 26.0

BODY_PT = 12
SMALL_PT = 10
TITLE_PT = 15
LOGO_PT = 30


def clamp01(t):
    return 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)


def ease_in_out_cubic(t):
    t = clamp01(t)
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0


def _bezier1d(t, a, b):
    u = 1.0 - t
    return 3.0 * u * u * t * a + 3.0 * u * t * t * b + t * t * t


def _bezier1d_deriv(t, a, b):
    u = 1.0 - t
    return 3.0 * u * u * a + 6.0 * u * t * (b - a) + 3.0 * t * t * (1.0 - b)


def cubic_bezier_ease(x, x1, y1, x2, y2):
    x = clamp01(x)
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    t = x
    for _ in range(8):
        err = _bezier1d(t, x1, x2) - x
        deriv = _bezier1d_deriv(t, x1, x2)
        if abs(deriv) < 1e-6:
            break
        t = clamp01(t - err / deriv)
    return _bezier1d(t, y1, y2)


def ease_out_expand(t):
    t = clamp01(t)
    e = cubic_bezier_ease(t, 0.22, 1.0, 0.36, 1.0)
    if t <= 0.55:
        return e
    mid = cubic_bezier_ease(0.55, 0.22, 1.0, 0.36, 1.0)
    u = (t - 0.55) / 0.45
    u = u * u * (3.0 - 2.0 * u)
    return mid + (1.0 - mid) * u


def _hex(value):
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def mix(fg, bg, alpha):
    """Цвет fg поверх bg с прозрачностью alpha — Tk не умеет альфу элементов."""
    if alpha >= 0.999:
        return fg
    a = clamp01(alpha)
    f, b = _hex(fg), _hex(bg)
    return "#%02x%02x%02x" % tuple(
        int(f[i] * a + b[i] * (1.0 - a) + 0.5) for i in range(3))


def approach(cur, target, dt, speed):
    """Сглаживание, не зависящее от частоты кадров."""
    return cur + (target - cur) * (1.0 - math.exp(-speed * dt))


def smooth_scroll(cur, tgt, vel, dt, k=SPRING_K, d=SPRING_D):
    diff = tgt - cur
    if abs(diff) < SNAP_DIST and abs(vel) < SNAP_VEL:
        return tgt, 0.0
    vel += (k * diff - d * vel) * dt
    nxt = cur + vel * dt
    overshot = (diff > 0.0 and nxt > tgt) or (diff < 0.0 and nxt < tgt)
    if overshot and abs(vel) < OVERSHOOT_VEL:
        return tgt, 0.0
    return nxt, vel


class ScrollState:
    def __init__(self):
        self.target = self.current = self.velocity = self.overscroll = 0.0
        self.bar = 0.0
        self.moving = False
        self.last = 0.0

    def reset(self):
        self.__init__()

    def tick(self, max_scroll, wheel, allow, dt, now):
        if allow and wheel:
            self.target -= wheel * WHEEL_SPEED
            self.moving = True
            self.last = now
        elif not wheel:
            if self.current < 0.0:
                self.target = 0.0
            elif self.current > max_scroll:
                self.target = max_scroll
            else:
                self.target = min(max(self.target, 0.0), max_scroll)

        self.current, self.velocity = smooth_scroll(
            self.current, self.target, self.velocity, dt)
        self.overscroll = 0.0
        if self.current < 0.0:
            self.overscroll = self.current
        elif self.current > max_scroll:
            self.overscroll = self.current - max_scroll

        settled = (abs(self.velocity) < REST_VEL
                   and abs(self.current - self.target) < SNAP_DIST)
        if settled:
            self.current = min(max(self.current, 0.0), max_scroll)
            self.overscroll = 0.0
        if self.moving and (now - self.last > SCROLL_IDLE or settled):
            self.moving = False
        want = 1.0 if self.moving else 0.0
        self.bar += (want - self.bar) * BAR_SPEED * dt
        return settled


class Card:
    """Одна карточка стопки: позиция, прозрачность и наведение."""

    def __init__(self, key, index, y, active, dim=1.0):
        self.key = key
        self.index = index
        self.current_y = self.target_y = self.start_y = y
        self.progress = 1.0
        self.active = active
        self.dim = dim
        self.hover = 0.0
        self.press = 0.0


class LoaderScreen:
    SPLASH = "splash"
    EXPAND = "expand"
    READY = "ready"
    OUT = "out"
    GROW = "grow"
    EXIT = "exit"
    IDLE = "idle"

    def __init__(self, root, api):
        self.root = root
        self.api = api
        self.phase = self.IDLE
        self.visible = False
        self.closing = False
        self.base_alpha = 1.0

        self.win_w, self.win_h = WIN_W, WIN_H
        self.target_w, self.target_h = WIN_W, WIN_H
        self.splash_t = self.expand_t = 0.0
        self.content_t = self.content_alpha = 0.0
        self.window_alpha = 0.0
        self.boot_t = 0.0
        self.loader_alpha = 1.0
        self.hide_t = 0.0
        self.out_t = 0.0
        self.exit_from = 1.0
        self.time_sec = 0.0
        self._last = time.monotonic()
        self._job = None
        self._geom_from = None
        self._geom_to = None
        self._geom_t = 0.0
        self._geom_dur = EXPAND_SEC
        self._on_geom_done = None
        self._size_anim = False

        self.scroll = ScrollState()
        self.cards = []
        self.card_meta = []
        self.links = []
        self.link_hover = {}
        self.link_press = {}
        self.close_hover = 0.0
        self.min_hover = 0.0
        self.look_hover = {}
        self.looks = []
        self.look_key = ""
        self.note_text = ""
        self.note_amount = 0.0
        self.note_live = False
        self.prev_active = None
        self._meta_cache = (0.0, [], "", None, [])
        self._mouse = (0, 0)
        self._pressed = None
        self._hit = []
        self._font_cache = {}

        self.canvas = tk.Canvas(root, bg="#000000", highlightthickness=0,
                                bd=0, takefocus=False)
        self.canvas.bind("<Motion>", self._on_motion, add="+")
        self.canvas.bind("<Button-1>", self._on_press, add="+")
        self.canvas.bind("<ButtonRelease-1>", self._on_release, add="+")
        self.canvas.bind("<MouseWheel>", self._on_wheel, add="+")
        self.canvas.bind("<Button-4>", self._on_wheel_linux, add="+")
        self.canvas.bind("<Button-5>", self._on_wheel_linux, add="+")
        self.canvas.bind("<Configure>", self._on_configure, add="+")
        self._fonts()

    # -- шрифты ---------------------------------------------------------------

    FONT_SIZES = {"body": (BODY_PT, False), "small": (SMALL_PT, False),
                  "title": (TITLE_PT, True), "logo": (LOGO_PT, True)}

    def _f(self, key):
        font = self._font_cache.get(key)
        if font is None:
            size, bold = self.FONT_SIZES[key]
            font = tkfont.Font(family=self.api.font_family, size=size,
                               weight="bold" if bold else "normal")
            self._font_cache[key] = font
        return font

    def _fonts(self):
        for key in self.FONT_SIZES:
            self._f(key)

    # -- палитра --------------------------------------------------------------

    def _pal(self):
        return self.api.palette()

    # -- показ / скрытие ------------------------------------------------------

    def show(self, boot=True):
        """Показать экран; boot=True — сSplash-анимацией 200×200."""
        if self.visible:
            return
        self.visible = True
        pal = self._pal()
        self.canvas.configure(bg=pal["bg"])
        self.canvas.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)
        self.base_alpha = float(pal.get("alpha", 1.0) or 1.0)
        self._sync_meta(force=True)
        if boot:
            self.phase = self.SPLASH
            self.splash_t = 0.0
            self.window_alpha = 0.0
            self.boot_t = 0.0
            self.loader_alpha = 1.0
            self.content_t = self.content_alpha = 0.0
            self._set_size(SPLASH_SIZE, SPLASH_SIZE)
        else:
            self.phase = self.READY
            self.window_alpha = 1.0
            self.content_t = 0.0
            self.content_alpha = 0.0
            self._set_size(WIN_W, WIN_H)
        self._last = time.monotonic()
        self._schedule()

    def enter(self, x=None, y=None):
        """Свернуть хаб обратно в экран-лоадер."""
        if self.visible:
            return
        self.visible = True
        pal = self._pal()
        self.canvas.configure(bg=pal["bg"])
        self.canvas.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)
        self.base_alpha = float(pal.get("alpha", 1.0) or 1.0)
        self._sync_meta(force=True)
        self.phase = self.READY
        self.window_alpha = 1.0
        self.loader_alpha = 0.0
        self.content_t = 0.0
        self.content_alpha = 0.0
        self.scroll.reset()
        self._apply_alpha()
        self.animate_rect(WIN_W, WIN_H, EXPAND_SEC, x=x, y=y)
        self._last = time.monotonic()
        self._schedule()

    def refresh(self):
        """Один кадр по запросу ядра: строка-отклик показалась или истекла."""
        if not self.visible:
            return
        self._sync_meta(force=True)
        self._schedule()

    def hide(self):
        if not self.visible:
            return
        self.visible = False
        self.phase = self.IDLE
        try:
            self.canvas.place_forget()
        except tk.TclError:
            pass
        self._cancel()

    def destroy(self):
        self._cancel()
        try:
            self.canvas.destroy()
        except tk.TclError:
            pass

    # -- геометрия окна -------------------------------------------------------

    def _geom(self):
        try:
            return (self.root.winfo_rootx(), self.root.winfo_rooty(),
                    self.root.winfo_width(), self.root.winfo_height())
        except tk.TclError:
            return (0, 0, WIN_W, WIN_H)

    def _set_rect(self, x, y, w, h):
        ax, ay, aw, ah = self.api.screen_rect()
        if w <= aw:
            x = min(max(x, ax), ax + aw - w)
        if h <= ah:
            y = min(max(y, ay), ay + ah - h)
        try:
            self.root.geometry("%dx%d+%d+%d" % (int(round(w)),
                                                int(round(h)),
                                                int(round(x)),
                                                int(round(y))))
        except tk.TclError:
            pass
        self.win_w, self.win_h = w, h
        # регион окна обновляют с задержкой, а во время раскрытия углы
        # должны следовать за размером каждый кадр
        self.api.refresh_shape()

    def _set_size(self, w, h):
        x, y, cw, ch = self._geom()
        self._set_rect(x + (cw - w) / 2.0, y + (ch - h) / 2.0, w, h)

    def animate_rect(self, w, h, dur, x=None, y=None, done=None,
                     curve=ease_out_expand):
        cx, cy, cw, ch = self._geom()
        if x is None:
            x = cx + (cw - w) / 2.0
        if y is None:
            y = cy + (ch - h) / 2.0
        self._geom_from = (cx, cy, cw, ch)
        self._geom_to = (x, y, w, h)
        self._geom_t = 0.0
        self._geom_dur = max(dur, 0.02)
        self._on_geom_done = done
        self._curve = curve
        self._size_anim = True
        self._schedule()

    # -- события --------------------------------------------------------------

    def _on_configure(self, event):
        if event.widget is self.canvas:
            self.win_w = max(event.width, 1)
            self.win_h = max(event.height, 1)
            self._draw()

    def _zone(self, x, y):
        for item in self._hit:
            if item[0] <= x <= item[2] and item[1] <= y <= item[3]:
                return item
        return None

    def _on_motion(self, event):
        self._mouse = (event.x, event.y)
        self._schedule()

    def _on_press(self, event):
        item = self._zone(event.x, event.y)
        self._pressed = item[4] if item else None
        if item is None or self.phase != self.READY:
            return
        if item[4] == "card":
            self.press_card(item[5])
        elif item[4] == "link":
            self.link_press[item[5]] = 1.0
            self._schedule()

    def _on_release(self, event):
        item = self._zone(event.x, event.y)
        if item is None or self._pressed != item[4] or self.phase != self.READY:
            self._pressed = None
            return
        self._pressed = None
        kind = item[4]
        if kind == "close":
            self.begin_exit()
        elif kind == "min":
            self.api.minimize()
        elif kind == "card":
            self.open_card(item[5])
        elif kind == "link":
            self.api.open_page(item[5])
        elif kind == "look":
            self.api.set_look(item[5])

    def press_card(self, key):
        """Короткая пружина отклика: карточка проминается под нажатием."""
        for card in self.cards:
            if card.key == key:
                card.press = 1.0
        self._schedule()

    def _on_wheel(self, event):
        self._wheel(event.delta)

    def _on_wheel_linux(self, event):
        self._wheel(120 if event.num == 4 else -120)

    def _wheel(self, delta):
        if self.phase != self.READY:
            return
        steps = delta / 120.0
        if steps:
            self.scroll.tick(self._max_scroll(), steps, True,
                             0.016, time.monotonic())
            self._schedule()

    # -- действия --------------------------------------------------------------

    def open_card(self, key):
        meta = next((m for m in self.card_meta if m["key"] == key), None)
        if meta is None or not meta.get("enabled", True):
            return
        self.api.open_page(key)

    def collapse_to_hub(self, x, y, w, h, done=None):
        """Карточка открыта: развернуть окно до хаба и погасить экран."""
        if self.phase != self.READY:
            return
        self.phase = self.OUT
        self.out_t = 0.0

        def _finish():
            self.hide()
            if callable(done):
                done()

        self.animate_rect(w, h, EXPAND_SEC, x=x, y=y, done=_finish,
                          curve=ease_out_expand)

    def begin_exit(self):
        if self.phase == self.EXIT:
            return
        self.phase = self.EXIT
        self.hide_t = 0.0
        self.exit_from = self.window_alpha
        self._schedule()

    # -- данные -----------------------------------------------------------

    def _sync_meta(self, force=False):
        now = time.monotonic()
        if not force and now - self._meta_cache[0] < 0.25:
            return
        cards = self.api.cards()
        self._meta_cache = (now, cards, self.api.nick(), self.api.avatar(),
                            self.api.links())
        self.card_meta = cards
        self.links = self._meta_cache[4]
        self.nick = self._meta_cache[2]
        self.avatar = self._meta_cache[3]
        self.looks = self.api.looks()
        self.look_key = self.api.look_key()
        text, _life = self.api.note()
        self.note_live = bool(text)
        if text or self.note_amount <= 0.01:
            # текст держим до конца гашения, иначе строка исчезнет мгновенно
            self.note_text = text
        self._rebuild_cards()

    def _rebuild_cards(self):
        active_key = self.api.active_key()
        order = [c["key"] for c in self.card_meta]
        if order and active_key not in order:
            # раздел не из карточек (аккаунты, избранное) — держим первую
            active_key = order[0]
        old = {c.key: c for c in self.cards}
        items = []
        for idx, key in enumerate(order):
            meta = self.card_meta[idx]
            is_active = key == active_key
            card = old.get(key)
            if card is None:
                card = Card(key, idx, self._card_y(idx), is_active,
                            1.0 if is_active else INACTIVE_DIM)
            card.index = idx
            card.active = is_active
            items.append((0 if is_active else 1, idx, card))
        items.sort(key=lambda t: (t[0], t[1]))
        self.cards = [c for _a, _b, c in items]
        for pos, card in enumerate(self.cards):
            card.target_y = self._card_y(pos)
            if card.current_y == 0.0 and card.start_y == 0.0:
                card.current_y = card.start_y = card.target_y
        if active_key != self.prev_active:
            self.prev_active = active_key
            for card in self.cards:
                card.start_y = card.current_y
                card.progress = 0.0

    def _card_y(self, pos):
        return CARDS_Y + pos * (CARD_H + CARD_GAP)

    def _layout(self):
        title = self.api.title()
        w = self.win_w
        tw = self._f("title").measure(title)
        start_x = (w - tw - HEADER_RIGHT_PAD) / 2.0
        card_w = w - start_x - 20.0
        # низ отдан аккаунту и образам: карточки под полосу не заезжают
        area_h = max(60.0, self.win_h - CARDS_Y - FOOTER_H)
        content_h = len(self.cards) * (CARD_H + CARD_GAP)
        max_scroll = max(0.0, content_h - area_h)
        return start_x, card_w, area_h, content_h, max_scroll

    def _max_scroll(self):
        return self._layout()[4]

    # -- цикл анимации ---------------------------------------------------------

    def _cancel(self):
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _schedule(self):
        if self._job is not None or not self.visible:
            return
        try:
            self._job = self.root.after(16, self._tick)
        except tk.TclError:
            self._job = None

    def _busy(self):
        if self.phase in (self.SPLASH, self.EXPAND, self.OUT, self.EXIT):
            return True
        if self._size_anim:
            return True
        if self.scroll.moving or abs(self.scroll.velocity) > REST_VEL:
            return True
        if abs(self.scroll.current - self.scroll.target) > SNAP_DIST:
            return True
        if self.scroll.bar > 0.01:
            return True
        for card in self.cards:
            if card.progress < 1.0:
                return True
            target = 1.0 if card.active else INACTIVE_DIM
            if abs(card.dim - target) > 0.004:
                return True
        for value in (self.close_hover, self.min_hover, *self.link_hover.values(),
                      *self.look_hover.values(), *self.link_press.values(),
                      *[c.hover for c in self.cards],
                      *[c.press for c in self.cards]):
            if value > 0.004:
                return True
        if self.content_alpha < 0.999:
            return True
        if self.note_amount > 0.004:
            # гаснем до нуля независимо от того, жив ли ещё текст строки
            return True
        if self.note_live and self.note_amount < 0.996:
            return True
        return False

    def _tick(self):
        self._job = None
        if not self.visible:
            return
        now = time.monotonic()
        dt = min(max(now - self._last, 0.0001), 0.05)
        self._last = now
        self.time_sec += dt
        try:
            self._sync_meta()
            self._tick_anim(dt, now)
            self._draw()
        except tk.TclError:
            return
        if self._busy():
            self._schedule()

    def _tick_anim(self, dt, now):
        if self.phase == self.EXIT:
            self.hide_t = clamp01(self.hide_t + dt / EXIT_FADE)
            self.window_alpha = self.exit_from * (1.0 - ease_in_out_cubic(
                self.hide_t))
            self._apply_alpha()
            if self.hide_t >= 1.0:
                self.visible = False
                self._cancel()
                self.api.close()
            return

        if self.phase in (self.SPLASH, self.EXPAND):
            self.boot_t = clamp01(self.boot_t + dt / BOOT_FADE)
            self.window_alpha = ease_in_out_cubic(self.boot_t)
            self._apply_alpha()

        want = 1.0 if self.note_live else 0.0
        self.note_amount = approach(self.note_amount, want, dt, NOTE_SPEED)
        if not self.note_live and self.note_amount <= 0.01:
            self.note_text = ""

        if self.phase == self.SPLASH:
            self.splash_t = clamp01(self.splash_t + dt / SPLASH_SEC)
            if self.splash_t >= 1.0:
                self.phase = self.EXPAND
                self.expand_t = 0.0
                self.animate_rect(WIN_W, WIN_H, EXPAND_SEC,
                                  done=self._after_expand)
        elif self.phase == self.EXPAND:
            self.expand_t = clamp01(self.expand_t + dt / EXPAND_SEC)
            t = ease_out_expand(self.expand_t)
            self.loader_alpha = 1.0 - t
            if t > 0.88:
                self.content_t = clamp01((t - 0.88) / 0.12) * 0.08
                self.content_alpha = ease_in_out_cubic(self.content_t)
        elif self.phase == self.READY:
            if self.content_t < 1.0:
                self.content_t = clamp01(self.content_t + dt / CONTENT_FADE)
                self.content_alpha = ease_in_out_cubic(self.content_t)
        elif self.phase == self.OUT:
            self.out_t = clamp01(self.out_t + dt / FADE_OUT)
            self.content_alpha = 1.0 - ease_in_out_cubic(self.out_t)

        self._tick_geom(dt)
        self._tick_cards(dt)
        mouse = getattr(self, "_mouse", (0, 0))
        hover = self._zone(mouse[0], mouse[1])
        tag = hover[4] if hover else ""
        payload = hover[5] if (hover and len(hover) > 5) else None
        self.close_hover = approach(
            self.close_hover, 1.0 if tag == "close" else 0.0, dt, HOVER_SPEED)
        self.min_hover = approach(
            self.min_hover, 1.0 if tag == "min" else 0.0, dt, HOVER_SPEED)
        for key in [k for k, _l in self.links]:
            want = 1.0 if tag == "link" and payload == key else 0.0
            self.link_hover[key] = approach(self.link_hover.get(key, 0.0),
                                            want, dt, HOVER_SPEED)
            self.link_press[key] = approach(
                self.link_press.get(key, 0.0), 0.0, dt, PRESS_SPEED)
        for look, _l, _c in self.looks:
            want = 1.0 if tag == "look" and payload == look else 0.0
            self.look_hover[look] = approach(
                self.look_hover.get(look, 0.0), want, dt, HOVER_SPEED)
        for card in self.cards:
            card.hover = approach(
                card.hover,
                1.0 if tag == "card" and payload == card.key else 0.0,
                dt, HOVER_SPEED)
            card.press = approach(card.press, 0.0, dt, PRESS_SPEED)
        settled = self.scroll.tick(self._max_scroll(), 0.0, False, dt, now)
        if settled and self.phase == self.READY:
            pass

    def _after_expand(self):
        if self.phase == self.EXPAND:
            self.phase = self.READY
            self.loader_alpha = 0.0
            self.win_w, self.win_h = WIN_W, WIN_H

    def _tick_geom(self, dt):
        if not self._size_anim:
            return
        self._geom_t = clamp01(self._geom_t + dt / max(self._geom_dur, 0.02))
        t = getattr(self, "_curve", ease_out_expand)(self._geom_t)
        frm, to = self._geom_from, self._geom_to
        self._set_rect(*[frm[i] + (to[i] - frm[i]) * t for i in range(4)])
        if self._geom_t >= 1.0:
            self._size_anim = False
            done, self._on_geom_done = self._on_geom_done, None
            if callable(done):
                done()

    def _tick_cards(self, dt):
        for card in self.cards:
            if card.progress < 1.0:
                card.progress = min(1.0, card.progress + dt
                                    / CONTAINER_ANIM_SEC)
                t = ease_in_out_cubic(card.progress)
                card.current_y = card.start_y + (card.target_y
                                                 - card.start_y) * t
            else:
                card.current_y = card.target_y
            target = 1.0 if card.active else INACTIVE_DIM
            card.dim = min(max(approach(card.dim, target, dt, DIM_SPEED),
                               INACTIVE_DIM), 1.0)

    def _apply_alpha(self):
        try:
            self.root.attributes("-alpha",
                                 max(0.02, self.base_alpha * self.window_alpha))
        except tk.TclError:
            pass

    # -- отрисовка -------------------------------------------------------------

    def _rrect(self, cv, x1, y1, x2, y2, r, **kw):
        r = max(1.0, min(r, (y2 - y1) / 2.0, (x2 - x1) / 2.0))
        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        pts = [x1 + r, y1, mx, y1, x2 - r, y1,
               x2, y1 + r, x2, my, x2, y2 - r,
               x2 - r, y2, mx, y2, x1 + r, y2,
               x1, y2 - r, x1, my, x1, y1 + r]
        cv.create_polygon(pts, smooth=True, splinesteps=SPLINE_STEPS, **kw)

    def _feather(self, cv, x1, y1, x2, y2, r, color, bg, alpha,
                 feather=FEATHER):
        """Наружные полупрозрачные обводы: гасят лесенку на границе фигуры."""
        if alpha <= 0.03:
            return
        for off, strength in feather:
            a = alpha * strength
            if a < 0.02:
                continue
            self._rrect(cv, x1 - off, y1 - off, x2 + off, y2 + off, r + off,
                        fill="", outline=mix(color, bg, a), width=1)

    def _soft_rect(self, cv, x1, y1, x2, y2, r, bg, alpha, fill=None,
                   edge=None, edge_width=None, feather=FEATHER):
        """Заливка и/или обводка со смягчёнными краями."""
        if alpha <= 0.008:
            return
        if fill is not None:
            self._rrect(cv, x1, y1, x2, y2, r, fill=mix(fill, bg, alpha),
                        outline="")
            self._feather(cv, x1, y1, x2, y2, r, fill, bg, alpha, feather)
        if edge is not None:
            self._rrect(cv, x1, y1, x2, y2, r, fill="",
                        outline=mix(edge, bg, alpha),
                        width=CARD_BORDER if edge_width is None else edge_width)
            self._feather(cv, x1, y1, x2, y2, r, edge, bg, alpha, feather)

    def _soft_circle(self, cv, cx, cy, radius, color, bg, alpha, ring=False):
        """Круг (или ободок) с мягкой кромкой."""
        if alpha <= 0.008 or radius <= 0.4:
            return
        box = (cx - radius, cy - radius, cx + radius, cy + radius)
        cv.create_oval(*box, fill="" if ring else mix(color, bg, alpha),
                       outline=mix(color, bg, alpha) if ring else "")
        for off, strength in FEATHER:
            a = alpha * strength
            if a < 0.02:
                continue
            rr = radius + off
            cv.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, fill="",
                           outline=mix(color, bg, a), width=1)

    def _draw(self):
        cv = self.canvas
        pal = self._pal()
        self.canvas.configure(bg=pal["bg"])
        cv.delete("all")
        self._hit = []
        w, h = self.win_w, self.win_h
        if self.phase in (self.SPLASH, self.EXPAND) and self.loader_alpha > 0.01:
            self._draw_splash(cv, pal, w, h)
            if self.phase == self.SPLASH:
                return
        self._draw_panel(cv, pal, w, h)

    def _draw_splash(self, cv, pal, w, h):
        a = clamp01(self.loader_alpha) * clamp01(self.window_alpha)
        img = self.api.logo()
        cx, cy = w / 2.0, h / 2.0 - 12
        if img is not None:
            cv.create_image(cx, cy, image=img, anchor="c")
        else:
            cv.create_text(cx, cy, text=self.api.brand(), fill=pal["text"],
                           font=self._f("logo"), anchor="c")
        col = mix(pal["accent"], pal["bg"], a)
        ang = self.time_sec * SPIN_SPEED
        for i in range(1, SPIN_SEGS + 1):
            t0 = (i - 1) / float(SPIN_SEGS)
            t1 = i / float(SPIN_SEGS)
            a0, a1 = ang - t0 * SPIN_FADE, ang - t1 * SPIN_FADE
            cv.create_line(cx + math.cos(a0) * SPLASH_R,
                           cy + 34 + math.sin(a0) * SPLASH_R,
                           cx + math.cos(a1) * SPLASH_R,
                           cy + 34 + math.sin(a1) * SPLASH_R,
                           fill=mix(pal["accent"], pal["bg"],
                                    a * (1.0 - t1)),
                           width=SPLASH_THICK, capstyle="round")

    def _draw_panel(self, cv, pal, w, h):
        ca = clamp01(self.content_alpha)
        if ca <= 0.004:
            return
        start_x, card_w, area_h, content_h, max_scroll = self._layout()
        self._draw_cards(cv, pal, start_x, card_w, area_h, ca)
        self._clip_cards(cv, pal, start_x, w, area_h)
        self._draw_header(cv, pal, w, ca)
        self._draw_links(cv, pal, ca)
        self._draw_scrollbar(cv, pal, start_x, card_w, area_h, content_h,
                             max_scroll, ca)
        self._draw_footer(cv, pal, w, h, ca)
        self._draw_chrome(cv, pal, w, ca)

    def _clip_cards(self, cv, pal, start_x, w, area_h):
        """Tk не умеет clip: выход карточек за стопку замазываем фоном."""
        x = start_x - 8
        cv.create_rectangle(x, 0, w, CARDS_Y - 6, fill=pal["bg"], outline="")
        cv.create_rectangle(x, CARDS_Y + area_h, w, self.win_h + 8,
                            fill=pal["bg"], outline="")

    def _draw_header(self, cv, pal, w, ca):
        bg = pal["bg"]
        logo = self.api.logo_small()
        if logo is not None:
            self._soft_rect(cv, LOGO_X - 1, LOGO_Y - 1,
                            LOGO_X + 1 + LOGO_SIZE, LOGO_Y + 1 + LOGO_SIZE,
                            9, bg, 0.5 * ca, fill=pal["accent"],
                            feather=FEATHER_SOFT)
            cv.create_image(LOGO_X + LOGO_SIZE / 2.0,
                            LOGO_Y + LOGO_SIZE / 2.0, image=logo, anchor="c")
        else:
            self._hit.append((LOGO_X, LOGO_Y, LOGO_X + LOGO_SIZE,
                              LOGO_Y + LOGO_SIZE, "brand"))
        title = self.api.title()
        tx = (w - self._f("title").measure(title) - HEADER_RIGHT_PAD) / 2.0
        cv.create_text(tx, TITLE_Y, text=title, anchor="nw",
                       font=self._f("title"),
                       fill=mix(pal["text"], bg, 0.9 * ca))
        cv.create_text(tx, SUB_Y, text=self.api.subtitle(), anchor="nw",
                       font=self._f("small"),
                       fill=mix(pal["muted"], bg, ca))

    def _draw_links(self, cv, pal, ca):
        bg = pal["bg"]
        y = LINK_Y
        for key, label in self.links:
            hv = self.link_hover.get(key, 0.0)
            pr = self.link_press.get(key, 0.0)
            col = mix(mix(pal["dim"], bg, ca), mix(pal["text"], bg, ca), hv)
            x = LINK_X + 1.5 * hv - 1.0 * pr
            cv.create_text(x, y, text=label, anchor="nw",
                           font=self._f("body"), fill=col)
            if hv > 0.02:
                tw = self._f("body").measure(label)
                self._soft_rect(cv, x, y + LINK_H - 3, x + tw,
                                y + LINK_H - 2, 0.5, bg, hv * ca * 0.75,
                                fill=pal["accent"], feather=FEATHER_SOFT)
            self._hit.append((LINK_X, y, LINK_X + 120, y + LINK_H,
                              "link", key))
            y += LINK_STEP

    def _draw_cards(self, cv, pal, start_x, card_w, area_h, ca):
        overscroll = self.scroll.overscroll * 0.5
        for card in self.cards:
            meta = next((m for m in self.card_meta if m["key"] == card.key),
                        None)
            if meta is None:
                continue
            y = card.current_y - self.scroll.current + overscroll
            if y > CARDS_Y + area_h or y + CARD_H < CARDS_Y:
                continue
            self._draw_card(cv, pal, meta, card, start_x, y, card_w, ca)

    def _draw_card(self, cv, pal, meta, card, x, y, cw, ca):
        enabled = meta.get("enabled", True)
        bg = pal["bg"]
        # карточки встают по очереди, а не все одним пластом
        if self.phase in (self.EXPAND, self.READY):
            entry = clamp01((ca - card.index * ENTRY_STAGGER) / 0.55)
        else:
            entry = 1.0
        ease = ease_in_out_cubic(entry)
        hv, pr = card.hover, card.press
        grow = 2.2 * hv - 1.6 * pr
        x1 = x - grow / 2.0
        y1 = y + (1.0 - ease) * ENTRY_TRAVEL - grow / 2.0
        x2 = x + cw + grow / 2.0
        y2 = y1 + CARD_H + grow
        vis = ca * card.dim * ease
        if not enabled:
            vis *= SOON_ALPHA

        self._soft_rect(cv, x1, y1, x2, y2, CARD_ROUND, bg, vis,
                        fill=pal["card"])
        self._soft_rect(cv, x1, y1, x2, y2, CARD_ROUND, bg,
                        min(1.0, vis * (0.85 + 0.55 * hv - 0.3 * pr)),
                        edge=pal["card_line"])
        if hv > 0.004 and enabled:
            self._soft_rect(cv, x1, y1, x2, y2, CARD_ROUND, bg,
                            0.13 * hv * ca, fill=pal["hover"],
                            feather=FEATHER_SOFT)
        if card.active:
            self._soft_rect(cv, x1 + 3.0, y1 + 12, x1 + 3.0 + CARD_BAR_W,
                            y2 - 12, CARD_BAR_W / 2.0, bg, vis,
                            fill=pal["accent"], feather=FEATHER_SOFT)

        tx = x1 + CARD_PADX
        ty = y1 + CARD_TITLE_Y
        if not card.active:
            cx = x1 + CARD_DOT_X
            cy = ty + self._f("body").metrics("linespace") * 0.5 + 1
            self._soft_circle(cv, cx, cy, 6.5, pal["accent"], bg, vis * 0.14)
            self._soft_circle(cv, cx, cy, 3.0, pal["accent"], bg, vis)
            tx += CARD_TEXT_SHIFT

        cv.create_text(tx, ty, text=meta["title"], anchor="nw",
                       font=self._f("body"),
                       fill=mix(pal["text"], bg, vis))
        cv.create_text(x1 + CARD_PADX, y1 + CARD_STATUS_Y,
                       text=meta["status"], anchor="nw",
                       font=self._f("small"),
                       fill=mix(pal["muted"], bg, vis))

        icon = meta.get("icon")
        if icon:
            ix = x2 - CARD_ICON - CARD_ICON_PAD
            iy = y1 + (CARD_H - CARD_ICON) / 2.0
            self._soft_rect(cv, ix, iy, ix + CARD_ICON, iy + CARD_ICON,
                            CARD_ICON_ROUND, bg, vis, fill=pal["surface"])
            cv.create_text(ix + CARD_ICON / 2.0, iy + CARD_ICON / 2.0,
                           text=icon, anchor="c", font=self._f("small"),
                           fill=mix(pal["accent"], bg, vis))
        if enabled:
            self._hit.append((x, y, x + cw, y + CARD_H, "card", meta["key"]))

    def _draw_scrollbar(self, cv, pal, start_x, card_w, area_h, content_h,
                        max_scroll, ca):
        alpha = self.scroll.bar * ca
        if alpha < 0.01 or max_scroll <= 0.0 or content_h <= 0.0:
            return
        thumb = min(max(BAR_MIN, area_h * (area_h / content_h)), area_h)
        ratio = clamp01(self.scroll.current / max_scroll)
        # полоса живёт в правом поле, а не поверх карточек
        x = start_x + card_w + 7.0
        y = CARDS_Y + ratio * (area_h - thumb)
        self._soft_rect(cv, x, y, x + BAR_W, y + thumb, BAR_W / 2.0,
                        pal["bg"], alpha, fill=pal["accent"],
                        feather=FEATHER_SOFT)

    def _draw_footer(self, cv, pal, w, h, ca):
        bg = pal["bg"]
        img = self.avatar
        cy = h - FOOTER_H / 2.0
        if img is not None:
            cv.create_image(LOOK_X0, cy, image=img, anchor="w")
            nx = LOOK_X0 + img.width() + 10
        else:
            nx = LOOK_X0
        cv.create_text(nx, cy, text=self.nick, anchor="w",
                       font=self._f("body"),
                       fill=mix(pal["text"], bg, 0.9 * ca))
        lx = w - 20 - LOOK_R
        for look, _label, color in reversed(self.looks):
            hv = self.look_hover.get(look, 0.0)
            r = LOOK_R + 1.2 * hv
            if look == self.look_key:
                self._soft_circle(cv, lx, cy, r + 3.2, pal["text"], bg,
                                  ca * 0.5, ring=True)
            self._soft_circle(cv, lx, cy, r, color, bg,
                              ca * (1.0 if look == self.look_key
                                     else 0.35 + 0.5 * hv))
            self._hit.append((lx - r - 4, cy - r - 4, lx + r + 4,
                              cy + r + 4, "look", look))
            lx -= LOOK_GAP
        if self.note_amount > 0.01 and self.note_text:
            cv.create_text(LOOK_X0, h - 14, text=self.note_text, anchor="w",
                           font=self._f("small"),
                           fill=mix(pal["muted"], bg,
                                    ca * self.note_amount))

    def _soft_line(self, cv, x1, y1, x2, y2, color, bg, alpha, width=2.0):
        """Отрезок с мягкой кромкой: под ним широкие полупрозрачные слои."""
        if alpha <= 0.02:
            return
        for extra, strength in ((2.0, 0.20), (1.0, 0.42)):
            col = mix(color, bg, alpha * strength)
            cv.create_line(x1, y1, x2, y2, fill=col, width=width + extra,
                           capstyle="round")
        cv.create_line(x1, y1, x2, y2, fill=mix(color, bg, alpha),
                       width=width, capstyle="round")

    def _draw_chrome(self, cv, pal, w, ca):
        bg = pal["bg"]

        def tone(hover):
            return mix(mix(pal["dim"], bg, ca), mix(pal["text"], bg, ca),
                       hover)

        pull = 1.0 - 0.6 * self.close_hover
        self._soft_line(cv, w - 30 + pull, 25 + pull, w - 20 - pull,
                        35 - pull, tone(self.close_hover), bg, ca)
        self._soft_line(cv, w - 30 + pull, 35 - pull, w - 20 - pull,
                        25 + pull, tone(self.close_hover), bg, ca)
        self._hit.append((w - 35, 20, w - 15, 40, "close"))
        self._soft_line(cv, w - 55, 30, w - 43, 30, tone(self.min_hover),
                        bg, ca)
        self._hit.append((w - 65, 20, w - 38, 40, "min"))
