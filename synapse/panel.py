"""Панель управления Synapse — графическое окно (tkinter, без зависимостей).

Что здесь есть:
  * ЖИВОЕ ТЕЛО — тот самый коннектом: 302 нейрона и 3004 связи. Крутится мышью,
    приближается колесом; цвет узла = роль (или медиатор, или зона тела),
    яркость = текущее возбуждение. По связям бежит импульс.
  * КНОПКИ ЖИЗНИ — жить / пауза / такт / пульс.
  * МОЗГ — включить свой llama-server (модель лежит в models/brain, вместе с телом),
    выключить, спросить.
  * МИССИИ — очередь: аудит сети, стимул телу, заметка, вопрос мозгу.
  * ЖУРНАЛ и ОТЧЁТЫ — всё, что организм сделал.
  * НЕЙРОНЫ — точная карта: кто сенсорный, кто мотонейрон, где сидит, чем связан.

Запуск:  python run.py panel    (или двойной клик по Synapse.pyw)
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import ttk

from synapse import audit, i18n, theme, window_fit
from synapse.panel_core import PanelCore, mix

# Быстрый путь отрисовки. SYNAPSE_NO_PIL=1 — принудительно запасной canvas-путь
# (нужен, чтобы проверять его живьём, а не надеяться, что «он тоже работает»).
HAVE_PIL = not os.environ.get("SYNAPSE_NO_PIL")
try:
    from synapse.render import LiveBody
except Exception:                      # noqa: BLE001 — отсутствие Pillow не поломка
    LiveBody = None
    HAVE_PIL = False

ANIM_MS = 33                       # целевой период кадра, мс. Замер 24.09 (гибрид, полный экран):
                                   # 45 -> 20.8 кадр/с при ~30% ядра, 33 -> 28.1 кадр/с при тех же
                                   # ~30%, 25 -> 33 кадр/с. Взяли 33: заметно плавнее бесплатно.
                                   # Раньше кадр стоил
                                   # РОВНО за пиксели. 700x650 — 6.4 рендер + 2.9 Tk +
                                   # 3.6 окно ~ 14 мс; 1920x1080 — 29 + 11 + 16 ~ 56 мс,
                                   # отсюда и жалоба «на весь экран хуже». Картинку держим
                                   # в бюджете MAX_AREA (synapse/render.py) и ставим в окно
                                   # 1:1; тело крупнее — колесом. Кадр без изменений (тело
                                   # спокойно и не повернулось) окну не показываем вовсе.
GEO_START = 6                      # каждый N-й кадр пересчитываем геометрию (вращение)
EDGE_BUDGET = 900                  # сколько связей вообще рисуем (замер: 3004 линии = 107 мс/кадр)
HOT_POOL = 120                     # пул ярких связей поверх сетки
FG = "#d7e2ee"
DIM = "#7d8b9a"
ACCENT = "#ffd166"


def _task_text(mission: dict) -> str:
    """Что именно просили в миссии — одной строкой."""
    params = mission.get("params") or {}
    bits = []
    if params.get("cidr"):
        bits.append(str(params["cidr"]))
    if params.get("text"):
        bits.append(str(params["text"]))
    if mission.get("note"):
        bits.append(str(mission["note"]))
    return " · ".join(bits) or "—"


class Panel(tk.Tk):
    def __init__(self, core: PanelCore | None = None, selftest: bool = False) -> None:
        super().__init__()
        self.core = core or PanelCore()
        self._selftest = selftest      # не self.selftest: это имя занято методом-прогоном
        self.title(i18n.t("tk.title", name=self.core.status()['name']))
        self.configure(bg=theme.bg())
        self.geometry("1180x760")
        self.minsize(900, 600)

        self.living = False
        self.tick_seconds = float(self.core.cfg.get("tick_seconds", 60))
        self._last_tick_at = 0.0
        self._geo_period = GEO_START
        self._geo_counter = 0
        self.edge_keep: list[int] = []
        self._node_state: list = []
        self._hot_state: list = []
        self._fps = 0.0
        self._frame_ms = 0.0            # сколько реально стоит кадр (видно в строке состояния)
        self._tick_manual = False
        # Тело собирается ГИБРИДОМ: неподвижное — одной запечённой картинкой, живое —
        # мелкими предметами Tk поверх. Замер 24.09 на развороте окна: 8.5 мс против 24.5.
        self.renderer = LiveBody(self.core) if HAVE_PIL else None
        self.image_item = None
        self.pic_box = (0, 0, 0, 0)     # где на холсте лежит картинка (для клика и подписи)
        self._resize_job = None         # пересборка фигуры после смены размера — с задержкой
        # предметы запасного (canvas) пути: объявлены здесь, а не в _relayout —
        # первый кадр animate обязан видеть их существующими (грабля 24.09)
        self.node_items: list[int] = []
        self.edge_items: list[int] = []
        self.hot_items: list[int] = []
        self.msgs: deque[str] = deque(maxlen=200)
        self._results: list = []      # результаты фоновых дел -> забираем в главном потоке
        self._centers: list = []      # центры узлов с последнего пересчёта геометрии
        self._drag = None
        self.busy = False

        self._style()
        self._build_top()
        self._build_middle()
        self._build_status()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(200, self.animate)
        self.after(400, self._drain_messages)
        self.after(600, self._auto_refresh)
        self.log(i18n.t("tk.body_log", nodes=self.core.body.size,
                        edges=len(self.core.body.edges)))
        self.log(i18n.t("tk.roles_log", roles=self.core.body.roles_summary()))
        if self.core.cfg.get("brain", {}).get("autostart") and self.core.cfg.get("brain", {}).get("mode") == "server":
            self.log(i18n.t("tk.brain_was_on"))
            self._bg(self.core.brain_on, self._after_brain)

    # ------------------------------------------------------------------ вид
    def _style(self) -> None:
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=theme.bg(), foreground=FG, fieldbackground="#111922",
                     bordercolor="#1d2a36", lightcolor="#1d2a36", darkcolor="#0e151d")
        st.configure("TFrame", background=theme.bg())
        st.configure("TLabel", background=theme.bg(), foreground=FG)
        st.configure("Dim.TLabel", background=theme.bg(), foreground=DIM)
        st.configure("TButton", background="#17222d", foreground=FG, borderwidth=0, padding=(10, 6))
        st.map("TButton", background=[("active", "#22303d"), ("disabled", "#131b24")])
        st.configure("Go.TButton", background="#1d3b2f", foreground="#c9ffdd")
        st.map("Go.TButton", background=[("active", "#27543f")])
        st.configure("Stop.TButton", background="#3b1d22", foreground="#ffd2d2")
        st.map("Stop.TButton", background=[("active", "#54272e")])
        st.configure("TNotebook", background=theme.bg(), borderwidth=0)
        st.configure("TNotebook.Tab", background="#111922", foreground=DIM, padding=(12, 6))
        st.map("TNotebook.Tab", background=[("selected", "#1b2836")], foreground=[("selected", FG)])
        st.configure("Treeview", background="#0e151d", fieldbackground="#0e151d", foreground=FG,
                     borderwidth=0, rowheight=20)
        st.map("Treeview", background=[("selected", "#24384a")])
        st.configure("Treeview.Heading", background="#16222d", foreground=DIM, borderwidth=0)
        st.configure("TEntry", fieldbackground="#111922", foreground=FG, insertcolor=FG)
        st.configure("TCombobox", fieldbackground="#111922", foreground=FG)
        st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor="#16222d", borderwidth=0)

    def _build_top(self) -> None:
        top = ttk.Frame(self)
        top.pack(side="top", fill="x", padx=10, pady=(10, 4))

        self.lbl_title = ttk.Label(top, text="Synapse", font=("Segoe UI", 15, "bold"))
        self.lbl_title.pack(side="left")
        self.lbl_mode = ttk.Label(top, text=i18n.t("tk.asleep"), style="Dim.TLabel", font=("Segoe UI", 11))
        self.lbl_mode.pack(side="left", padx=(8, 0))

        self.bar = ttk.Progressbar(top, length=180, maximum=1.0)
        self.bar.pack(side="left", padx=16)
        self.lbl_energy = ttk.Label(top, text=i18n.t("dash"), style="Dim.TLabel")
        self.lbl_energy.pack(side="left")

        self.lbl_brain = ttk.Label(top, text=i18n.t("tk.brain_off"), style="Dim.TLabel")
        self.lbl_brain.pack(side="right", padx=(8, 0))
        self.btn_brain = ttk.Button(top, text=i18n.t("btn.brain_on"), style="Go.TButton", command=self.toggle_brain)
        self.btn_brain.pack(side="right")

    def _build_middle(self) -> None:
        mid = ttk.Frame(self)
        mid.pack(side="top", fill="both", expand=True, padx=10, pady=6)

        # --- левая половина: тело ---
        left = ttk.Frame(mid)
        left.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(left, bg=theme.bg(), highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        # Окно во время разворота шлёт <Configure> десятками событий подряд, и каждый раз
        # пересобирались слои (10-15 мс) — отсюда рывки при развороте. Ждём 120 мс тишины.
        self.canvas.bind("<Configure>", lambda e: self._on_configure())
        if self.renderer is not None:
            self.renderer.attach(self.canvas)
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<MouseWheel>", self.on_wheel)

        ctl = ttk.Frame(left)
        ctl.pack(side="bottom", fill="x", pady=(6, 0))
        self.btn_live = ttk.Button(ctl, text=i18n.t("btn.live"), style="Go.TButton", command=self.toggle_live)
        self.btn_live.pack(side="left")
        ttk.Button(ctl, text=i18n.t("btn.step"), command=lambda: self.do_tick(manual=True)).pack(side="left", padx=4)
        ttk.Button(ctl, text=i18n.t("btn.pulse"), command=self.do_pulse).pack(side="left", padx=4)
        ttk.Button(ctl, text=i18n.t("color.roles"), command=lambda: self.set_coloring("role")).pack(side="left", padx=4)
        ttk.Button(ctl, text=i18n.t("color.nt"), command=lambda: self.set_coloring("nt")).pack(side="left")
        ttk.Button(ctl, text=i18n.t("color.zone"), command=lambda: self.set_coloring("zone")).pack(side="left", padx=4)
        self.lbl_pick = ttk.Label(ctl, text="", style="Dim.TLabel")
        self.lbl_pick.pack(side="right")

        # --- правая половина: вкладки ---
        right = ttk.Frame(mid, width=440)
        right.pack(side="right", fill="y", padx=(10, 0))
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill="both", expand=True)
        self._tab_missions()
        self._tab_journal()
        self._tab_reports()
        self._tab_neurons()
        self._tab_brain()

    def _build_status(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(side="bottom", fill="x")
        self.lbl_status = ttk.Label(bar, text=i18n.t("tk.ready"), style="Dim.TLabel", anchor="w")
        self.lbl_status.pack(side="left", padx=10, pady=(0, 8), fill="x", expand=True)

    # ------------------------------------------------------------- вкладки
    def _tab_missions(self) -> None:
        """Миссии: форма, из которой понятно, ЧТО случится после нажатия.

        Стас сказал: «как давать миссии я не понял — писать вручную?» Значит:
        инструмент выбирается подписью по-русски, поля включаются по делу,
        под формой — подсказка и проверка двери, а рядом кнопка «выполнить сейчас».
        """
        f = ttk.Frame(self.tabs)
        self.tabs.add(f, text=i18n.t("tab.missions"))
        form = ttk.Frame(f)
        form.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(form, text=i18n.t("tk.label.what")).grid(row=0, column=0, sticky="w")
        self.cmb_kind = ttk.Combobox(form, state="readonly", width=18,
                                     values=[k["label"] for k in self.core.mission_kinds()])
        self.cmb_kind.current(0)
        self.cmb_kind.grid(row=0, column=1, sticky="w", padx=4)
        self.cmb_kind.bind("<<ComboboxSelected>>", lambda _e: self.on_kind_change())

        self.lbl_cidr = ttk.Label(form, text=i18n.t("tk.label.subnet"))
        self.lbl_cidr.grid(row=1, column=0, sticky="w", pady=3)
        self.ent_cidr = ttk.Entry(form, width=22)
        self.ent_cidr.grid(row=1, column=1, sticky="we", padx=4, pady=3)
        self.lbl_text = ttk.Label(form, text=i18n.t("tk.label.task"))
        self.lbl_text.grid(row=2, column=0, sticky="w")
        self.ent_text = ttk.Entry(form, width=22)
        self.ent_text.grid(row=2, column=1, sticky="we", padx=4)
        form.columnconfigure(1, weight=1)

        self.lbl_hint = ttk.Label(f, text="", style="Dim.TLabel", wraplength=405, justify="left")
        self.lbl_hint.pack(fill="x", padx=8, pady=(4, 2))
        self.lbl_plan = ttk.Label(f, text="", style="Dim.TLabel", wraplength=405, justify="left")
        self.lbl_plan.pack(fill="x", padx=8)

        row = ttk.Frame(f)
        row.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Button(row, text=i18n.t("btn.run_now"), style="Go.TButton",
                   command=self.add_and_run).pack(side="left")
        ttk.Button(row, text=i18n.t("btn.queue"), command=self.add_mission).pack(side="left", padx=4)
        ttk.Button(row, text=i18n.t("btn.my_network"), command=self.set_my_net).pack(side="left", padx=4)
        ttk.Button(row, text=i18n.t("btn.delete_selected"), command=self.drop_mission).pack(side="right")

        self.tree_m = ttk.Treeview(f, columns=("id", "what", "status", "created", "result"),
                                   show="headings", height=9)
        for c, w, t in (("id", 112, i18n.t("tk.col.id")), ("what", 90, i18n.t("col.what")),
                        ("status", 62, i18n.t("col.status")), ("created", 74, i18n.t("col.when")),
                        ("result", 190, i18n.t("col.result"))):
            self.tree_m.heading(c, text=t)
            self.tree_m.column(c, width=w, anchor="w")
        self.tree_m.pack(fill="both", expand=True, padx=8, pady=(6, 2))
        self.tree_m.bind("<<TreeviewSelect>>", self.on_mission_pick)

        ttk.Label(f, text=i18n.t("tk.result_full"), style="Dim.TLabel").pack(anchor="w", padx=8)
        self.txt_result = tk.Text(f, bg="#0e151d", fg="#cfe0f0", font=("Consolas", 9),
                                  height=6, wrap="word", bd=0)
        self.txt_result.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._kind = "net_audit"
        self.on_kind_change()

    def _tab_journal(self) -> None:
        f = ttk.Frame(self.tabs)
        self.tabs.add(f, text=i18n.t("tab.journal"))
        ttk.Button(f, text=i18n.t("btn.refresh"), command=self.refresh_journal).pack(anchor="e", padx=8, pady=(8, 0))
        self.txt_log = tk.Text(f, bg="#0e151d", fg="#cfe0f0", insertbackground=FG,
                               font=("Consolas", 9), wrap="none", bd=0)
        self.txt_log.pack(fill="both", expand=True, padx=8, pady=8)

    def _tab_reports(self) -> None:
        """Отчёты — человеческим языком: подпись в списке, разбор в окне ниже."""
        f = ttk.Frame(self.tabs)
        self.tabs.add(f, text=i18n.t("tab.reports"))
        top = ttk.Frame(f)
        top.pack(fill="x", padx=8, pady=8)
        ttk.Button(top, text=i18n.t("btn.refresh"), command=self.refresh_reports).pack(side="left")
        ttk.Button(top, text=i18n.t("btn.open_file"), command=self.open_report_file).pack(side="left", padx=4)
        self.lst_reports = tk.Listbox(top, bg="#0e151d", fg=FG, height=5, bd=0,
                                      selectbackground="#24384a", highlightthickness=0)
        self.lst_reports.pack(fill="x", expand=True, pady=(6, 0))
        self.lst_reports.bind("<<ListboxSelect>>", self.on_report)
        self.txt_report = tk.Text(f, bg="#0e151d", fg="#cfe0f0", font=("Consolas", 9), wrap="word", bd=0)
        self.txt_report.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _tab_neurons(self) -> None:
        f = ttk.Frame(self.tabs)
        self.tabs.add(f, text=i18n.t("tab.neurons"))
        top = ttk.Frame(f)
        top.pack(fill="x", padx=8, pady=8)
        ttk.Label(top, text=i18n.t("tk.neuron.name")).pack(side="left")
        self.ent_neuron = ttk.Entry(top, width=10)
        self.ent_neuron.pack(side="left", padx=4)
        ttk.Button(top, text=i18n.t("tk.btn.find"), command=self.find_neuron).pack(side="left")
        self.txt_neuron = tk.Text(f, bg="#0e151d", fg="#cfe0f0", font=("Consolas", 9),
                                  height=10, wrap="word", bd=0)
        self.txt_neuron.pack(fill="x", padx=8)
        self.tree_n = ttk.Treeview(f, columns=("role", "n", "hubs", "ex"), show="headings", height=10)
        for c, w, t in (("role", 90, i18n.t("tk.col.role")), ("n", 40, i18n.t("tk.col.count")),
                        ("hubs", 45, i18n.t("tk.col.hubs")), ("ex", 260, i18n.t("tk.col.examples"))):
            self.tree_n.heading(c, text=t)
            self.tree_n.column(c, width=w, anchor="w")
        self.tree_n.pack(fill="both", expand=True, padx=8, pady=8)

    def _tab_brain(self) -> None:
        f = ttk.Frame(self.tabs)
        self.tabs.add(f, text=i18n.t("tab.brain"))
        self.txt_brain = tk.Text(f, bg="#0e151d", fg="#cfe0f0", font=("Consolas", 9),
                                 height=10, wrap="word", bd=0)
        self.txt_brain.pack(fill="x", padx=8, pady=8)
        row = ttk.Frame(f)
        row.pack(fill="x", padx=8)
        ttk.Button(row, text=i18n.t("tk.btn.provision"), command=self.provision).pack(side="left")
        ttk.Button(row, text=i18n.t("btn.refresh"), command=self.refresh_brain).pack(side="left", padx=4)
        q = ttk.Frame(f)
        q.pack(fill="x", padx=8, pady=8)
        self.ent_ask = ttk.Entry(q, width=30)
        self.ent_ask.pack(fill="x")
        ttk.Button(q, text=i18n.t("btn.ask"), command=self.ask).pack(anchor="e", pady=4)
        self.txt_answer = tk.Text(f, bg="#0e151d", fg="#ffe9a8", font=("Consolas", 9),
                                  height=8, wrap="word", bd=0)
        self.txt_answer.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    # ---------------------------------------------------------------- тело
    def _on_configure(self) -> None:
        """Размер окна изменился — пересобираем фигуру, но не на каждое событие."""
        if self._resize_job:
            try:
                self.after_cancel(self._resize_job)
            except tk.TclError:
                pass
        self._resize_job = self.after(120, self._relayout)

    def _relayout(self) -> None:
        """Размер изменился — пересобрать фигуру (узлы и связи рисуем заново)."""
        w = max(200, self.canvas.winfo_width())
        h = max(200, self.canvas.winfo_height())
        self.canvas.delete("all")
        self.edge_items = []
        self.hot_items = []
        self.node_items = []
        self.image_item = None
        body = self.core.body
        if not body.size:
            return
        if self.renderer is not None:                 # быстрый путь: запечённое + мелкие предметы
            self.renderer.forget_items()              # холст чистили — номера предметов устарели
            self.renderer.resize(w, h)
            iw, ih = self.renderer.picture_box()      # картинка НЕ растянута на окно (замер: пиксели)
            ox, oy = (w - iw) // 2, (h - ih) // 2
            self.renderer.offset = (ox, oy)           # предметы живут в координатах картинки
            self.pic_box = (ox, oy, iw, ih)
            self.renderer.rebuild()
            self.image_item = self.renderer._image_item
            self.canvas.create_rectangle(ox, oy, ox + iw, oy + ih, outline="#18242f")
            self.canvas.create_text(ox + 14, oy + 12, anchor="nw", text=i18n.t("tk.head_label"), fill=DIM,
                                    font=("Segoe UI", 8))
            self.canvas.create_text(ox + iw - 14, oy + 12, anchor="ne", text=i18n.t("tk.tail_label"), fill=DIM,
                                    font=("Segoe UI", 8))
            return
        # Связей на экране — только сильнейшие. Две причины: 3004 линии рисуются
        # 107 мс на кадр (замер), и вся сетка целиком выглядит кашей, а не телом.
        self.edge_keep = list(self.core.edge_keep)
        pts = self.core.screen(w, h)
        for k in self.edge_keep:
            a, b, _w = body.edges[k]
            self.edge_items.append(self.canvas.create_line(
                pts[a][0], pts[a][1], pts[b][0], pts[b][1], fill=theme.edge_dim(), width=1))
        self.hot_pool = int(self.core.hot_pool)
        for _ in range(self.hot_pool):             # пул «горячих» связей — поверх сетки
            self.hot_items.append(self.canvas.create_line(0, 0, 0, 0, fill=theme.bg(), width=2,
                                                      state="hidden"))
        for i in range(body.size):
            x, y, _d = pts[i]
            r = self.core.node_radius(i)
            self.node_items.append(self.canvas.create_oval(x - r, y - r, x + r, y + r,
                                                           fill="#22303d", outline=""))
        self.canvas.create_text(14, 12, anchor="nw", text=i18n.t("tk.head_label"), fill=DIM, font=("Segoe UI", 8))
        self.canvas.create_text(w - 14, 12, anchor="ne", text=i18n.t("tk.tail_label"), fill=DIM, font=("Segoe UI", 8))
        self._node_state = [(None, None)] * body.size
        self._hot_state = [(None, None)] * self.hot_pool
        self._put_geometry(w, h, force=True)

    def _put_geometry(self, w: int, h: int, force: bool = False) -> None:
        pts = self.core.screen(w, h)
        self._centers = [(px, py) for px, py, _d in pts]
        body = self.core.body
        for slot, k in enumerate(self.edge_keep):
            a, b, _w = body.edges[k]
            self.canvas.coords(self.edge_items[slot], pts[a][0], pts[a][1], pts[b][0], pts[b][1])
        self._update_hot(pts)
        self._node_state = [(None, None)] * body.size   # тело повернулось — перекрасим всё

    def _update_hot(self, pts=None) -> None:
        """Яркие связи: перерисовываем только те, что реально изменились."""
        body = self.core.body
        hot = self.core.hot_edges(self.hot_pool)
        for slot in range(self.hot_pool):
            item = self.hot_items[slot]
            if slot >= len(hot):
                if self._hot_state[slot][0] is not None:
                    self.canvas.itemconfigure(item, state="hidden")
                    self._hot_state[slot] = (None, None)
                continue
            k, v = hot[slot]
            q = int(v * 8)
            if self._hot_state[slot] == (k, q) and pts is None:
                continue
            a, b, _w = body.edges[k]
            if pts is not None:
                x1, y1, x2, y2 = pts[a][0], pts[a][1], pts[b][0], pts[b][1]
            elif self._centers:
                x1, y1 = self._centers[a]
                x2, y2 = self._centers[b]
            else:
                continue
            self.canvas.coords(item, x1, y1, x2, y2)
            self.canvas.itemconfigure(item, fill=mix("#3a5a3a", theme.hot(), min(1.0, v * 2.2)),
                                      width=1 + 2 * min(1.0, v * 3), state="normal")
            self._hot_state[slot] = (k, q)

    def _update_nodes(self) -> None:
        """Узлы: трогаем canvas только если яркость или размер заметно изменились."""
        body = self.core.body
        for i, item in enumerate(self.node_items):
            a = body.activity[i]
            q = int(a * 10)
            r = self.core.node_radius(i)
            was = self._node_state[i]
            if was[0] == q and was[1] is not None and abs(was[1] - r) < 0.35:
                continue
            cxp, cyp = self._centers[i]
            self.canvas.coords(item, cxp - r, cyp - r, cxp + r, cyp + r)
            self.canvas.itemconfigure(item, fill=self.core.node_color(i))
            self._node_state[i] = (q, r)

    def animate(self) -> None:
        """Кадр жизни: тело дышит, цвета и яркость — по возбуждению."""
        if not self.winfo_viewable():          # окно свёрнуто/скрыто — не жжём кадры впустую
            self.after(ANIM_MS, self.animate)
            return
        try:
            t0 = time.perf_counter()
            self.core.breathe()
            self._geo_counter += 1
            if self._geo_counter % self._geo_period == 0:
                self.core.yaw += 0.03
            w = max(200, self.canvas.winfo_width())
            h = max(200, self.canvas.winfo_height())
            if self.renderer is not None:
                if self.image_item is None:
                    self._relayout()
                elif self._geo_counter % self._geo_period == 0:
                    self.renderer.rebuild()              # тело повернулось — перезапекаем неподвижное
                self.renderer.step()                     # живое: горячие связи и свечение
            elif not self.node_items:
                self._relayout()
            elif self._geo_counter % self._geo_period == 0:
                self._put_geometry(w, h)
            else:
                self._update_hot()
            if self.renderer is None and len(self._centers) == len(self.node_items):
                self._update_nodes()
            if self._geo_counter % 5 == 0:          # метры — не каждый кадр (это чтение с диска)
                self._update_meters()
            if self.living and time.time() - self._last_tick_at >= self.tick_seconds:
                self.do_tick()
            spent = time.perf_counter() - t0
            self._frame_ms = 0.85 * self._frame_ms + 0.15 * spent * 1000.0
            self._fps = 0.85 * self._fps + 0.15 * (1.0 / max(spent, 1e-3))
            if spent > 0.06:                       # не успеваем — реже крутим геометрию
                self._geo_period = min(24, self._geo_period + 1)
                if spent > 0.14 and self.core.edge_budget > 300:   # и показываем меньше связей
                    self.core.edge_keep = self.core._pick_edges(self.core.edge_budget - 150)
                    if self.renderer is None:
                        self.node_items = []                      # -> пересборка на след. кадре
                    else:
                        self.renderer._reset()
            elif spent < 0.02 and self._geo_period > GEO_START:
                self._geo_period -= 1
                if self.core.edge_budget < 900 and self._geo_counter % 120 == 0:
                    # кадр стал дешёвым — возвращаем связи, которые когда-то урезали
                    self.core.edge_keep = self.core._pick_edges(self.core.edge_budget + 150)
                    if self.renderer is not None:
                        self.renderer._reset()
                    else:
                        self.node_items = []
        except tk.TclError:
            return
        # держим ровный темп: вычитаем время своей работы, иначе after() и работа
        # складываются и кадр выходит вдвое длиннее задуманного (замер: 176 мс вместо 60)
        delay = max(5, int(ANIM_MS - (time.perf_counter() - t0) * 1000))
        self.after(delay, self.animate)

    def _update_meters(self) -> None:
        st = self.core.status()
        self.bar["value"] = min(1.0, st["energy"] * 40)
        extra = ""
        if self.renderer is not None:            # видно, чем именно платим за кадр
            extra = i18n.t("tk.renderer_extra", w=self.renderer.w, h=self.renderer.h,
                           cw=self.renderer.cw, ch=self.renderer.ch,
                           rebuilds=self.renderer.rebuilds,
                           sprites=len(self.renderer._sprites),
                           skipped=self.renderer.frames_skipped)
        self.lbl_energy.config(text=i18n.t(
            "tk.status_line", energy=f"{st['energy']:.4f}", lit=st["lit"], size=st["size"],
            ticks=st["total_ticks"], wakeups=st["wakeups"], cap=st["max_wakeups"],
            fps=f"{self._fps:.0f}", ms=f"{self._frame_ms:.1f}",
            engine=i18n.t("tk.engine.picture" if self.renderer is not None
                          else "tk.engine.canvas"),
            edges=self.core.edge_budget, extra=extra))
        mode = i18n.t("tk.state.awake") if self.living else i18n.t("tk.state.asleep")
        self.lbl_mode.config(text=f"· {mode} · {st['intent']['action']} ({st['intent']['why']})")

    # ------------------------------------------------------------ действия
    def toggle_live(self) -> None:
        self.living = not self.living
        self.btn_live.config(text=i18n.t("btn.pause") if self.living else i18n.t("btn.live"),
                             style="Stop.TButton" if self.living else "Go.TButton")
        self._last_tick_at = 0.0
        self.log(i18n.t("tk.live_log") if self.living else i18n.t("tk.pause_log"))

    def do_tick(self, manual: bool = False) -> None:
        """Один такт — В ФОНЕ.

        Такт может спросить свой мозг (idle_thinking), а модель отвечает секундами.
        Раньше это шло в главном потоке окна и окно на это время вставало колом —
        ровно то, что Стас назвал «очень тормозит всё в приложении».
        """
        if self.busy:
            if manual:
                self.log(i18n.t("tk.busy"))
            return
        self._last_tick_at = time.time()
        self._tick_manual = manual
        self._bg(self.core.tick, self._after_tick)

    def _after_tick(self, out: dict) -> None:
        if not isinstance(out, dict):
            return
        if out.get("mission"):
            self.log(i18n.t("tk.tick_mission", id=out["mission"], status=out.get("status"),
                            text=str(out.get("text"))[:300]))
            self.refresh_missions()
        elif out.get("text") and self._tick_manual:
            self.log(i18n.t("tk.tick_line", tick=out.get("tick"), text=str(out.get("text"))[:300]))
        self.refresh_journal()

    def do_pulse(self) -> None:
        text = self.ent_text.get().strip() or i18n.t("tk.pulse_default")
        self.log(i18n.t("tk.body_pulse", text=self.core.pulse(text, amp=1.0)))

    def set_coloring(self, how: str) -> None:
        self.core.coloring = how
        self._node_state = [(None, None)] * self.core.body.size   # перекрасить всё
        if self.renderer is not None:
            self.renderer._reset()
        # Название раскраски — словом интерфейса, а не кодом 'role' (жалоба
        # 25.09.2026: «что за роли цвета?» — оператору нужны слова, не коды).
        self.log(i18n.t("tk.colouring", how=self.core.coloring_name()))

    def on_click(self, e) -> None:
        self._drag = (e.x, e.y)             # с этого места можно крутить тело мышью
        name = self._nearest(e.x, e.y)
        if name:
            self.lbl_pick.config(text=f"{name}")
            self.show_neuron(name)

    def on_drag(self, e) -> None:
        if self._drag is None:
            return
        dx = e.x - self._drag[0]
        dy = e.y - self._drag[1]
        self._drag = (e.x, e.y)
        self.core.yaw += dx * 0.006
        self.core.pitch = max(-1.3, min(1.3, self.core.pitch + dy * 0.004))
        if self.renderer is None:
            self._put_geometry(self.canvas.winfo_width(), self.canvas.winfo_height())
        else:
            self.renderer._reset()

    def on_wheel(self, e) -> None:
        self.core.zoom = max(0.3, min(3.0, self.core.zoom * (1.1 if e.delta > 0 else 0.9)))
        if self.renderer is not None:
            self.renderer._reset()

    def _nearest(self, x: int, y: int) -> str:
        if self.renderer is not None:            # картинка по центру: переводим в её координаты
            ox, oy, iw, ih = self.pic_box
            x, y = x - ox, y - oy
            pts = self.core.screen(iw, ih)
        else:
            pts = self.core.screen(self.canvas.winfo_width(), self.canvas.winfo_height())
        best, bestd = "", 16 ** 2
        for i, (px, py, _d) in enumerate(pts):
            d = (px - x) ** 2 + (py - y) ** 2
            if d < bestd:
                best, bestd = self.core.body.ids[i], d
        return best

    # ------------------------------------------------------------- миссии
    # ------------------------------------------------------------- миссии
    def on_kind_change(self) -> None:
        """Выбрали инструмент — включили только нужные поля и объяснили, что будет."""
        label = self.cmb_kind.get()
        meta = next((k for k in self.core.mission_kinds() if k["label"] == label),
                    self.core.mission_kinds()[0])
        self._kind = meta["kind"]
        needs = meta.get("needs")
        self.ent_cidr.config(state="normal" if needs == "cidr" else "disabled")
        self.ent_text.config(state="disabled" if needs == "cidr" else "normal")
        self.lbl_cidr.config(foreground=FG if needs == "cidr" else DIM)
        self.lbl_text.config(foreground=DIM if needs == "cidr" else FG)
        self.lbl_hint.config(text=meta.get("hint", ""))
        self.check_plan()

    def check_plan(self) -> None:
        """Проверка ДО нажатия: скажет и про пустое поле, и про дверь.

        Так оператор не узнаёт из журнала, что задание отклонили.
        """
        ok, why = self.core.plan_mission(self._kind, self.ent_cidr.get().strip(),
                                         self.ent_text.get().strip())
        self.lbl_plan.config(text=(i18n.t("tk.plan_ok", why=why) if ok
                                   else i18n.t("tk.plan_no", why=why)),
                             foreground="#8de08d" if ok else "#ffb7a8")
        return ok

    def add_mission(self, run_now: bool = False) -> None:
        if not self.check_plan():
            self.log(i18n.t("tk.not_queued", kind=self._kind))
            return
        cidr, text = self.ent_cidr.get().strip(), self.ent_text.get().strip()
        rec = self.core.add_mission(self._kind, cidr=cidr, text=text)
        what = self.core.mission_label(self._kind)
        self.log(i18n.t("tk.queued_log", id=rec["id"], what=what))
        self.lbl_plan.config(text=i18n.t("tk.queued_hint", id=rec["id"], what=what),
                             foreground="#8de08d")
        self.refresh_missions()
        if run_now:
            self.do_tick(manual=True)

    def add_and_run(self) -> None:
        self.add_mission(run_now=True)

    def set_my_net(self) -> None:
        cidr = self.core.my_network()
        self.ent_cidr.config(state="normal")
        self.ent_cidr.delete(0, "end")
        self.ent_cidr.insert(0, cidr)
        self.log(i18n.t("tk.my_net", cidr=cidr or i18n.t("tk.not_found")))
        self.check_plan()

    def drop_mission(self) -> None:
        sel = self.tree_m.selection()
        if not sel:
            return
        mid = self.tree_m.item(sel[0], "values")[0]
        res = self.core.drop_mission(mid)      # вернёт, что ВЫШЛО, а не «ок»
        self.log(str(res.get("why") or ""))
        self.refresh_missions()

    def refresh_missions(self) -> None:
        rows = self.core.mission_rows(30)
        self.tree_m.delete(*self.tree_m.get_children())
        for r in rows:
            self.tree_m.insert("", "end", iid=r["id"], tags=("wait",) if r["queued"] else (),
                               values=(r["id"], r["what"], r["status"], r["created"], r["result"]))

    def on_mission_pick(self, _e=None) -> None:
        """Полный итог выбранной миссии (в таблице он обрезан)."""
        sel = self.tree_m.selection()
        if not sel:
            return
        mid = self.tree_m.item(sel[0], "values")[0]
        found = None
        for m in self.core.queue() + self.core.history(50):
            if m["id"] == mid:
                found = m
                break
        self.txt_result.delete("1.0", "end")
        if not found:
            self.txt_result.insert("end", i18n.t("tk.result_missing", id=mid) + "\n")
            return
        self.txt_result.insert("end", (
            f"{found['id']} · {self.core.mission_label(found['kind'])} · "
            f"{found.get('status', '')}\n"        # статус — как в файле очереди
            + i18n.t("tk.created", created=found.get("created") or i18n.t("dash"),
                   finished=found.get("finished") or i18n.t("dash")) + "\n"
            + i18n.t("tk.task_line", task=_task_text(found)) + "\n\n"
            + (found.get("result") or i18n.t("tk.no_result_yet")) + "\n"))

    # ------------------------------------------------------- журнал/отчёты
    def refresh_journal(self) -> None:
        rows = self.core.audit_tail(80)
        self.txt_log.delete("1.0", "end")
        for r in rows:
            rest = " ".join(f"{k}={v}" for k, v in r.items() if k not in ("at", "event"))
            self.txt_log.insert("end", f"{r.get('at', '')}  {r.get('event', ''):<16} {rest}\n")
        self.txt_log.see("end")

    def refresh_reports(self) -> None:
        self._reports = self.core.reports()
        self.lst_reports.delete(0, "end")
        for r in self._reports:
            self.lst_reports.insert("end", i18n.t("tk.bytes", title=r["title"], size=r["size"]))

    def on_report(self, _e=None) -> None:
        sel = self.lst_reports.curselection()
        if not sel or not getattr(self, "_reports", None):
            return
        row = self._reports[sel[0]]
        self.txt_report.delete("1.0", "end")
        self.txt_report.insert("end", self.core.report_text(row["path"]))
        self.log(i18n.t("tk.report_log", title=row["title"]))

    def open_report_file(self) -> None:
        """Открыть человеческий .txt системным просмотрщиком (блокнот и чем угодно)."""
        sel = self.lst_reports.curselection()
        if not sel or not getattr(self, "_reports", None):
            self.log(i18n.t("tk.pick_report"))
            return
        path = self._reports[sel[0]]["text"]
        if not Path(path).exists():
            self.log(i18n.t("tk.no_text_file", name=Path(path).name))
            path = self._reports[sel[0]]["path"]
        try:
            if hasattr(os, "startfile"):
                os.startfile(path)                      # noqa: S606 — Windows: открыть «как двойной клик»
            else:
                subprocess.Popen(["xdg-open", path])
            self.log(i18n.t("tk.opened", name=Path(path).name))
        except OSError as e:
            self.log(i18n.t("tk.open_failed", error=e))

    # ------------------------------------------------------------- нейроны
    def find_neuron(self) -> None:
        self.show_neuron(self.ent_neuron.get().strip())

    def show_neuron(self, name: str) -> None:
        rec = self.core.neuron(name)
        self.txt_neuron.delete("1.0", "end")
        if not rec:
            self.txt_neuron.insert("end", i18n.t("tk.no_neuron", name=name))
            return
        # Карточка узла: числа и имена — как в атласе, подписи — на языке интерфейса.
        dash = i18n.t("dash")
        self.txt_neuron.insert("end", (
            i18n.t("tk.neuron_head", name=rec["name"], role=rec.get("role"),
                   role_label=i18n.role_name(rec.get("role"))) + "\n"
            + i18n.t("tk.neuron_types", value=", ".join(rec.get("types") or []) or dash) + "\n"
            + i18n.t("tk.neuron_nt", nt=rec.get("nt") or dash,
                     receptors=rec.get("receptors", 0)) + "\n"
            + i18n.t("tk.neuron_zone", zone=i18n.zone_name(rec.get("zone")), deg=rec.get("deg"),
                     w_in=rec.get("w_in"), w_out=rec.get("w_out"))
            + (i18n.t("tk.neuron_hub") if rec.get("hub") else "") + "\n"
            + i18n.t("tk.neuron_activity", activity=rec.get("activity"),
                     xyz=rec.get("xyz")) + "\n"
            + i18n.t("tk.neuron_atlas", value=rec.get("marker") or dash) + "\n"
            + i18n.t("tk.neuron_source", value=rec.get("source") or dash) + "\n"))
        self.refresh_role_table()

    def refresh_role_table(self) -> None:
        self.tree_n.delete(*self.tree_n.get_children())
        for row in self.core.role_table():
            self.tree_n.insert("", "end", values=(f"{row['role']} ({row['ru']})", row["count"],
                                                  row["hubs"], row["examples"]))

    # --------------------------------------------------------------- мозг
    def refresh_brain(self) -> None:
        st = self.core.brain_state()
        self.txt_brain.delete("1.0", "end")
        self.txt_brain.insert("end", (
            i18n.t("tk.brain_mode_line", mode=st["mode"],
                   answer=i18n.t("tk.yes") if st["available"] else i18n.t("tk.no")) + "\n"
            + i18n.t("tk.brain_endpoint", value=st["endpoint"]) + "\n"
            + i18n.t("tk.brain_model_line", value=st["model"]) + "\n"
            + i18n.t("tk.brain_model_local",
                     value=i18n.t("tk.yes") if st["model_local"] else i18n.t("tk.no")) + "   "
            + i18n.t("tk.brain_runtime_local",
                     value=i18n.t("tk.yes") if st["exe_local"] else i18n.t("tk.no")) + "\n"
            + i18n.t("tk.brain_self_line",
                     value=i18n.t("tk.brain_self_yes") if st["self_contained"]
                     else i18n.t("tk.brain_self_no")) + "\n"
            + i18n.t("tk.brain_dir_line", value=st["local_dir"]) + "\n"))
        self.lbl_brain.config(
            text=(i18n.t("tk.brain_on") if st["mode"] == "server" else i18n.t("tk.brain_off"))
                 + (i18n.t("tk.brain_alive") if st["available"] else ""))

    def toggle_brain(self) -> None:
        if self.core.brain.mode() == "server":
            self._bg(self.core.brain_off, self._after_brain)
        else:
            self._bg(self.core.brain_on, self._after_brain)

    def provision(self) -> None:
        self._bg(lambda: self.core.brain.provision(on_progress=self._note_from_thread), self._after_brain)

    def ask(self) -> None:
        q = self.ent_ask.get().strip() or i18n.t("tk.ask_default")
        self.txt_answer.delete("1.0", "end")
        self.txt_answer.insert("end", i18n.t("tk.asking") + "\n")
        self._bg(lambda: {"ok": True, "steps": [self.core.ask(q)]}, lambda r: self._show_answer(r))

    def _show_answer(self, res: dict) -> None:
        self.txt_answer.delete("1.0", "end")
        self.txt_answer.insert("end", "\n".join(res.get("steps", [])))

    def _after_brain(self, res: dict) -> None:
        for s in res.get("steps", []):
            self.log(s)
        self.refresh_brain()

    # --------------------------------------------------------- поток/потоки
    def _bg(self, fn, on_done=None) -> None:
        """Долгое дело (копирование модели, старт сервера) — в отдельном потоке,
        иначе окно замёрзнет и Стас справедливо скажет, что панель — тормоз."""
        if self.busy:
            self.log(i18n.t("tk.busy_other"))
            return
        self.busy = True

        def run():
            try:
                res = fn()
            except Exception as e:                     # noqa: BLE001 — панель не падает
                res = {"ok": False, "steps": [i18n.t("tk.error",
                                                     error=f"{type(e).__name__}: {e}")]}
            self.msgs.append("__done__" + str(len(res.get("steps", []))))
            if on_done:
                self._results.append((on_done, res))   # окно трогаем ТОЛЬКО из главного потока
            self.busy = False

        self.log(i18n.t("tk.started_bg"))
        threading.Thread(target=run, daemon=True).start()

    def _note_from_thread(self, name: str, total: int) -> None:
        self.msgs.append(i18n.t("tk.copied", name=name, mb=round(total / 1048576, 1)))

    def log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.lbl_status.config(text=f"{stamp}  {text}")
        self.msgs.append(f"{stamp}  {text}")

    def _drain_messages(self) -> None:
        if getattr(self, "_closing", False):
            # Окно закрывают: итог такта уже никому не нужен, а СВОЙ сервер,
            # поднятый фоном в этот момент, иначе остался бы сиротой.
            try:
                self.core.brain.stop_server()
            except Exception:                       # noqa: BLE001
                pass
            return
        while self._results:
            cb, res = self._results.pop(0)
            try:
                cb(res)
            except Exception:                       # noqa: BLE001 — окно не падает из-за колбэка
                pass
        while self.msgs:
            msg = self.msgs.popleft()
            if msg.startswith("__done__"):
                continue
            try:
                self.txt_log.insert("end", msg + "\n")
                self.txt_log.see("end")
            except tk.TclError:
                pass
        self.after(400, self._drain_messages)

    def _auto_refresh(self) -> None:
        self.refresh_missions()
        self.refresh_journal()
        self.refresh_reports()
        self.refresh_role_table()
        self.refresh_brain()
        self.after(8000, self._auto_refresh)

    def stop_own_brain(self) -> str:
        """Погасить СВОЙ сервер по закрытию окна.

        Находка QA Ореста 25.09.2026: условие было ПЕРЕВЁРНУТО — сервер гасился
        ровно тогда, когда его и так не было, а при brain.mode=server закрытие
        окна оставляло llama-server сиротой в системе. Отдельным методом, а не
        внутри on_close: так это можно проверить тестом, не открывая окно.
        """
        self._closing = True                  # фоновому такту больше нечего запускать
        try:
            mode = self.core.brain.mode()
            self.core.brain.stop_server()
            audit.log("panel.close", engine="tk", brain="stopped", was=mode)
            return "stopped"
        except Exception as e:                      # noqa: BLE001 — окно не падает
            audit.log("panel.close", engine="tk", error=f"{type(e).__name__}: {e}")
            return "error"

    def on_close(self) -> None:
        self.stop_own_brain()
        self.destroy()

    # ------------------------------------------------------------ selftest
    def selftest(self) -> int:
        """Прогон без человека: окно живёт 3 секунды, кадры идут, потом закрываемся."""
        self.after(3000, self.destroy)
        self.toggle_live()
        self.do_tick(manual=True)
        self.mainloop()
        print(f"selftest ok: узлов {self.core.body.size}, связей {self.core.edge_budget}, "
              f"путь отрисовки: {'гибрид (запечённое + предметы)' if self.renderer is not None else 'canvas'}, "
              f"кадров {self._geo_counter}, ~{self._fps:.0f} кадр/с")
        return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    core = PanelCore()
    # Запасной путь — не молча: пишем, ЧЕМ открылись и с каким Pillow. Замер 25.09.2026:
    # двойной клик брал интерпретатор без PySide6, и Qt-панель просто не запускалась.
    audit.log("panel.open", engine="tk", python=sys.executable, pil=bool(HAVE_PIL))
    if "--selftest" in argv:
        p = Panel(core)
        return p.selftest()
    Panel(core).mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
