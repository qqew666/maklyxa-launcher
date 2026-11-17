"""Presentation-only menu widgets. No Roblox, account or updater operations.

Companion preferences have their own file so the existing core config schema
and the independent window-edge mascot remain unchanged.
"""
import json
import math
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk
from types import MethodType


class CompanionCard(tk.Frame):
    NAMES = {"boykisser": "Boykisser", "mimi": "Mimi (Typh)"}
    INTERVAL = 15.0

    def __init__(self, parent, app, settings_path):
        self.app = app
        self.path = Path(settings_path)
        self._job = None
        self._alive = True
        self._next = time.monotonic() + self.INTERVAL
        self._last_pose = None
        self._settings_note = None
        data = self._read()
        c = app.c
        super().__init__(parent, bg=c["surface"], highlightthickness=1,
                         highlightbackground=c["border"])
        self.character = tk.StringVar(self, value=data["character"])
        self.automatic = tk.BooleanVar(self, value=data["automatic"])
        self.motion = tk.BooleanVar(self, value=data["motion"])
        self.pack(fill="x", pady=(0, 18))
        art = tk.Canvas(self, width=210, height=210, bg=c["surface"],
                        bd=0, highlightthickness=0, takefocus=False)
        art.pack(side="right", padx=(0, 12))
        self.art = art
        copy = tk.Frame(self, bg=c["surface"], padx=24, pady=21)
        copy.pack(side="left", fill="both", expand=True)
        tk.Label(copy, text="ТВОЙ КОМПАНЬОН / 01", bg=c["surface"],
                 fg=app.accent, font=(app.font_family, 8, "bold"),
                 anchor="w").pack(fill="x")
        self.name = tk.Label(copy, bg=c["surface"], fg=c["text"],
                             font=(app.font_family, 25, "bold"), anchor="w")
        self.name.pack(fill="x", pady=(9, 4))
        tk.Label(copy, text="Уютное место перед новым приключением.",
                 bg=c["surface"], fg=c["muted"], justify="left",
                 wraplength=290, font=(app.font_family, 10), anchor="w").pack(fill="x")
        buttons = tk.Frame(copy, bg=c["surface"])
        buttons.pack(fill="x", pady=(15, 0))
        app._button(buttons, "Сменить персонажа  ↗", self.swap, side="left")
        self.bind("<Destroy>", self._destroyed, add="+")
        self.refresh()
        self._job = self.after(100, self._tick)

    def _read(self):
        data = {}
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                data = parsed
        except (OSError, ValueError):
            pass
        return {"character": data.get("character") if data.get("character")
                in tuple(self.NAMES) else "boykisser",
                "automatic": data.get("automatic") is True,
                "motion": data.get("motion", True) is True}

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"character": self.character.get(),
                "automatic": self.automatic.get(), "motion": self.motion.get()},
                ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
            message = "Изменения сохранены автоматически"
        except OSError:
            message = "Не удалось сохранить. Настройки действуют до закрытия."
            self.app.status_var.set(message)
        if self._settings_note is not None:
            self._settings_note.set(message)

    def refresh(self):
        if self.character.get() not in self.NAMES:
            self.character.set("boykisser")
        self.name.configure(text=self.NAMES[self.character.get()])
        self._last_pose = None
        self._draw(0)

    def changed(self):
        self._next = time.monotonic() + self.INTERVAL
        self.refresh()
        self._save()

    def swap(self, persist=True):
        self.character.set("mimi" if self.character.get() == "boykisser"
                           else "boykisser")
        self.refresh()
        self._next = time.monotonic() + self.INTERVAL
        if persist:
            self._save()

    def _tick(self):
        self._job = None
        if not self._alive:
            return
        now = time.monotonic()
        if self.automatic.get() and now >= self._next:
            # Automatic cycling is transient; do not write disk every 15 s.
            self.swap(persist=False)
        if self.winfo_viewable():
            pose = round(math.sin(now * 1.5) * 3) if self.motion.get() else 0
            if pose != self._last_pose:
                self._draw(pose)
        self._job = self.after(100, self._tick)

    def _draw(self, pose):
        self._last_pose = pose
        cv, c = self.art, self.app.c
        cv.delete("all")
        pink = self.character.get() == "mimi"
        fur = "#f1b5d8" if pink else "#e8e5ff"
        ink = "#43334f"
        cv.create_oval(16, 12, 201, 197, fill=c["surface2"], outline="")
        cv.create_text(24, 48, text="✦", fill=self.app.accent,
                       font=(self.app.font_family, 21))
        cv.create_text(184, 154, text="✦", fill=self.app.accent,
                       font=(self.app.font_family, 16))
        # Both are intentionally simple, original vector interpretations.
        cv.create_polygon(59, 199, 71, 163, 137, 163, 154, 199,
                           fill="#b978a3" if pink else "#8876b1", outline=ink,
                           width=3)
        points = [(57,100),(43,34),(87,67),(125,67),(168,34),(155,100),
                  (164,124),(153,147),(137,158),(149,168),(128,166),
                  (107,177),(83,168),(64,170),(72,157),(54,142),(48,122)]
        cv.create_polygon(*[v for x,y in points for v in (x,y+pose)],
                           fill=fur, outline=ink, width=3, smooth=False)
        for points in [(51,47,77,74,59,89),(159,47,133,74,151,89)]:
            cv.create_polygon(*[v + (pose if i % 2 else 0)
                                for i, v in enumerate(points)],
                               fill="#be8ab5", outline="")
        for x in (72,118):
            cv.create_line(x,111+pose,x+8,106+pose,x+16,111+pose,
                           smooth=True, width=3, fill=ink)
        cv.create_polygon(101,123+pose,110,123+pose,106,128+pose,fill=ink)
        cv.create_line(93,133+pose,100,138+pose,106,128+pose,
                       112,138+pose,120,133+pose,smooth=True,fill=ink,width=2)
        for x in (63,135):
            cv.create_line(x,128+pose,x+11,131+pose,fill="#d880ac",width=4)
        cv.create_polygon(83,163+pose,106,170+pose,129,163+pose,
                           124,181+pose,106,175+pose,87,181+pose,fill=ink)

    def build_settings(self, parent, skin=None):
        app = skin or self.app
        c = app.c
        inner = app._card(parent, title="Твой пушистый плюс один", glyph="✦",
            subtitle="Выбор персонажа, автоматическая смена и движение")
        row = tk.Frame(inner, bg=c["surface"])
        row.pack(fill="x", pady=(12, 8))
        for key, name in self.NAMES.items():
            rb = tk.Radiobutton(row, text=name, variable=self.character,
                value=key, command=self.changed, bg=c["surface"],
                fg=c["text"], selectcolor=c["entry"],
                activebackground=c["surface"], activeforeground=app.accent,
                font=(app.font_family, 10), cursor="hand2")
            rb.pack(side="left", padx=(0, 20))
        app._checkbox(inner, "Менять персонажа каждые 15 секунд",
                      self.automatic, command=self.changed).pack(fill="x")
        app._checkbox(inner, "Плавная анимация персонажа",
                      self.motion, command=self.changed).pack(fill="x")
        self._settings_note = tk.StringVar(self,
            value="Изменения сохраняются автоматически")
        tk.Label(inner, textvariable=self._settings_note, bg=c["surface"],
                 fg=c["muted"], font=(app.font_family, 9), anchor="w").pack(
                     fill="x", pady=(10, 0))
        app._hint(inner, "Векторные стилизации. Питомец на кромке окна "
                  "настраивается отдельно ниже.")

    def _destroyed(self, event):
        if event.widget is self:
            self._alive = False
            if self._job is not None:
                self.after_cancel(self._job)
                self._job = None


LOOKS = {
    "pink": ("Клубничное молоко", "#fff0f5", "#fffafb", "#f9dfe9", "#ed8bb4", "#522c40", "#886072", "#edc5d7"),
    "cream": ("Ванильный крем", "#fff6e8", "#fffdf5", "#f4e5d0", "#df8bad", "#594137", "#8d7063", "#ebd8bf"),
    "white": ("Белое облачко", "#f8f7fb", "#ffffff", "#eee9f2", "#e488b0", "#493547", "#80707f", "#e4dce7"),
    "black": ("Ночной котик", "#17131b", "#241e2a", "#332a3b", "#efa6cb", "#fff0f8", "#baa4bb", "#443349"),
}


class SettingsSkin:
    # Only presentation helpers are rebound; application actions always belong
    # to the actual App. Nothing here changes the core theme/config schema.
    HELPERS = {"_card", "_rounded_shell", "_card_head", "_hint", "_button",
               "_checkbox", "_btn_colors", "_bind_hover", "_animate_btn"}

    def __init__(self, app, path):
        self.real = app
        self.path = Path(path)
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            mode = value.get("look") if isinstance(value, dict) else "pink"
        except (OSError, ValueError):
            mode = "pink"
        self.set_palette(mode if isinstance(mode, str) and mode in LOOKS else "pink")

    def __getattr__(self, name):
        if name in self.HELPERS:
            return MethodType(getattr(type(self.real), name), self)
        return getattr(self.real, name)

    @property
    def on_accent(self):
        return "#422334"

    def set_palette(self, mode):
        self.mode = mode
        _, bg, surface, secondary, accent, text, muted, border = LOOKS[mode]
        self.accent = accent
        self.theme_name = "dark" if mode == "black" else "light"
        self.c = {"bg": bg, "panel": bg, "surface": surface,
            "surface2": secondary, "entry": secondary, "list": secondary,
            "hover": border, "text": text, "muted": muted, "border": border,
            "hairline": border, "on_accent": self.on_accent,
            "base": self.theme_name}

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"look": self.mode}), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            self.real.status_var.set("Не удалось сохранить оформление настроек")


class SettingsSections(tk.Frame):
    """Soft settings room with independent colorways and existing controls."""
    def __init__(self, parent, app, skin):
        self.app, self.skin = app, skin
        c = skin.c
        super().__init__(parent, bg=c["bg"], padx=16, pady=14)
        self.pack(fill="x")
        self.buttons, self.groups = {}, {}
        self.current = "general"
        hero = tk.Frame(self, bg=c["bg"])
        hero.pack(fill="x", pady=(0, 18))
        art = tk.Canvas(hero, width=120, height=110, bg=c["bg"], bd=0,
                        highlightthickness=0)
        art.pack(side="right")
        self.art = art
        tk.Label(hero, text="♡  MAKE IT CUTE, MAKE IT YOURS", bg=c["bg"],
            fg=c["muted"], font=(app.font_family, 9, "bold"), anchor="w").pack(fill="x",pady=(4,10))
        tk.Label(hero, text="Уголок настроечек", bg=c["bg"], fg=c["text"],
            font=(app.font_family, 24, "bold"), anchor="w").pack(fill="x")
        tk.Label(hero, text="Немного нежности. Много твоих правил. :3", bg=c["bg"],
            fg=c["muted"],font=(app.font_family, 10),anchor="w").pack(fill="x",pady=(8,0))
        looks = tk.Frame(self, bg=c["bg"])
        looks.pack(fill="x",pady=(0,20))
        self.look_buttons = {}
        for key in LOOKS:
            button = tk.Button(looks, text=LOOKS[key][0], bd=0, relief="flat",
                font=(app.font_family, 9), padx=9,pady=11,cursor="hand2",
                command=lambda k=key:self.change_look(k))
            button.pack(side="left",fill="x",expand=True,padx=(0,5))
            self.look_buttons[key]=button
        strip = tk.Frame(self,bg=c["surface2"],padx=5,pady=5)
        strip.pack(fill="x",pady=(0,18))
        for key,label in (("general","♡ Мои привычки"),("appearance","✧ Гардероб"),
                          ("client","▷ Игровое"),("updates","↻ Всё свеженькое")):
            button=tk.Button(strip,text=label,relief="flat",bd=0,padx=8,pady=12,
                cursor="hand2",font=(app.font_family,9,"bold"),command=lambda k=key:self.show(k))
            button.pack(side="left",fill="x",expand=True,padx=2)
            button.bind("<Right>",lambda e,k=key:self._step(k,1))
            button.bind("<Left>",lambda e,k=key:self._step(k,-1))
            self.buttons[key]=button
            self.groups[key]=tk.Frame(self,bg=c["bg"])
        self.show("general")

    def _step(self,key,step):
        keys=list(self.groups)
        target=keys[(keys.index(key)+step)%len(keys)]
        self.show(target)
        self.buttons[target].focus_set()
        return "break"

    def show(self,key):
        self.current=key
        for group in self.groups.values():group.pack_forget()
        self.groups[key].pack(fill="x")
        self._paint_chrome()
        view=self.app.scroll_canvases.get("settings")
        if view is not None:view.yview_moveto(0)
        self.app._schedule_page_layout("settings",delay=20)

    def _paint_chrome(self):
        c=self.skin.c
        for key,button in self.buttons.items():
            chosen=key==self.current
            button.configure(bg=self.skin.accent if chosen else c["surface2"],
                fg=self.skin.on_accent if chosen else c["muted"],
                activebackground=c["hover"],activeforeground=c["text"])
        for key,button in self.look_buttons.items():
            button.configure(text=("♥ " if key==self.skin.mode else "")+LOOKS[key][0],
                bg=c["surface"],fg=c["text"],activebackground=c["hover"],
                activeforeground=c["text"])
        cv=self.art
        cv.delete("all")
        cv.create_oval(9,5,112,108,fill=c["surface2"],outline="")
        cv.create_polygon(27,54,20,19,49,35,75,35,102,19,96,58,101,77,
            84,92,61,99,37,91,21,78,fill=c["surface"],outline=self.skin.accent,width=2)
        cv.create_line(38,62,45,57,52,62,smooth=True,fill=c["text"],width=2)
        cv.create_line(72,62,79,57,86,62,smooth=True,fill=c["text"],width=2)
        cv.create_text(62,77,text="ω",fill=c["text"],font=(self.app.font_family,17))
        cv.create_text(16,92,text="♡",fill=self.skin.accent,font=(self.app.font_family,15))
        cv.create_text(106,18,text="✧",fill=self.skin.accent,font=(self.app.font_family,15))

    def finish(self):
        for group in self.groups.values():
            for child in group.pack_slaves():child.pack_configure(fill="x")
        data=self.app._page_views["settings"]
        for widget in (data["view"],data["pad"],self.app.pages["settings"]):
            widget.configure(bg=self.skin.c["bg"])
        self.restyle()

    def restyle(self):
        c=self.skin.c
        style=self.app.style
        style.configure("Soft.TEntry",fieldbackground=c["entry"],foreground=c["text"],
            insertcolor=c["text"],bordercolor=c["border"])
        style.configure("Soft.Horizontal.TProgressbar",background=self.skin.accent,
            troughcolor=c["surface2"],bordercolor=c["border"])
        stack=[self]
        while stack:
            widget=stack.pop();stack.extend(widget.winfo_children())
            if isinstance(widget,ttk.Entry):widget.configure(style="Soft.TEntry")
            if isinstance(widget,ttk.Progressbar):widget.configure(style="Soft.Horizontal.TProgressbar")
            if getattr(widget,"app",None) is self.skin and hasattr(widget,"_draw"):
                if hasattr(widget,"_bg"):widget._bg=None
                widget._draw()
        self._paint_chrome()

    def change_look(self,mode):
        old=self.skin.c.copy();old_accent=self.skin.accent
        self.skin.set_palette(mode)
        mapping={color:self.skin.c[key] for key,color in old.items() if isinstance(color,str)}
        mapping[old_accent]=self.skin.accent
        stack=[self,self.app._page_views["settings"]["view"],
            self.app._page_views["settings"]["pad"],self.app.pages["settings"]]
        seen=set()
        while stack:
            widget=stack.pop()
            if str(widget) in seen:continue
            seen.add(str(widget));stack.extend(widget.winfo_children())
            for option in ("bg","fg","activebackground","activeforeground",
                           "highlightbackground","highlightcolor","selectcolor"):
                try:
                    color=widget.cget(option)
                    if color in mapping:widget.configure(**{option:mapping[color]})
                except tk.TclError:pass
            if getattr(widget,"app",None) is self.skin and hasattr(widget,"bg"):
                widget.bg=mapping.get(widget.bg,widget.bg)
        self.skin.save()
        self.restyle()
        self.app._schedule_page_layout("settings",delay=20)
