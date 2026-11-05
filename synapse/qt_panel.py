"""Synapse — окно управления на ВИДЕОКАРТЕ (Qt + OpenGL).

ЗАЧЕМ ПЕРЕПИСАНО (замер 24.09.2026, не вкусовщина)
--------------------------------------------------
Прежняя панель рисовала тело на процессоре в картинку и отдавала её окну целиком:
цена кадра росла ровно с числом пикселей — 700x650 это 14 мс, а 1920x1080 это 56 мс
(рисование 29 + передача картинки в Tk 11 + перерисовка окна 16). Отсюда и жалоба
«на весь экран стало ещё хуже».

Здесь тело рисует видеокарта: связи — линии, узлы — точки, цвет и размер приходят
на каждую вершину. Процессор только пересчитывает проекцию (302 точки) и отдаёт
буфер. Цена кадра от размера окна больше не зависит — можно развернуть на весь экран.

ЯЗЫК И ТЕМА (слово Стаса 25.09.2026: «какой то хаус у нас с языком вышел»)
-------------------------------------------------------------------------
  * Каждая подпись берётся из synapse/i18n.py. Строк интерфейса в этом файле нет —
    поэтому смена языка в настройках применяется сразу, без перезапуска окна:
    _retranslate() проходит по своим виджетам и ставит текст заново из словаря.
  * ТЕХНИЧЕСКОЕ не переводится никогда и приходит прямо из данных: имена команд
    (net_audit, write_probe, certs, diff), состояния миссий (queued/running/done),
    события журнала. Оператор видит ровно то слово, которое лежит в очереди.
  * Тема (synapse/theme.py) — одна палитра на виджеты (QSS) и на само тело
    (фон полотна, цвет связей и узлов): окно и кадр не выглядят сборными.

ЧТО ЖИВЁТ САМО: тело дышит (breathe) по таймеру кадров; такт с миссиями и мозгом
идёт в ФОНОВОМ потоке, поэтому окно не стояло колом, пока модель думает.
"""
from __future__ import annotations

import array
import json
import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QOpenGLContext, QSurfaceFormat, QVector2D
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QFrame,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QPushButton, QScrollArea,
                               QSizePolicy, QSpinBox, QSplitter, QTabWidget, QTableWidget,
                               QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

from synapse import audit, i18n, theme, window_fit
from synapse.panel_core import PanelCore, hex_rgb

# Константы OpenGL: Qt их не отдаёт в Python, поэтому числом (значения из gl.h).
GL_POINTS, GL_LINES = 0x0000, 0x0001
GL_BLEND, GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA = 0x0BE2, 0x0302, 0x0303
GL_FLOAT, GL_PROGRAM_POINT_SIZE = 0x1406, 0x8642
GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT = 0x4000, 0x0100
GL_MULTISAMPLE = 0x809D

#: x, y, r, g, b, a, размер — семь чисел на вершину.
FLOATS = 7
STRIDE = FLOATS * 4

VERT = """
attribute vec2 pos;
attribute vec4 col;
attribute float psize;
uniform vec2 res;
varying vec4 vcol;
void main() {
    vec2 p = pos / res * 2.0 - 1.0;
    gl_Position = vec4(p.x, -p.y, 0.0, 1.0);
    gl_PointSize = psize;
    vcol = col;
}
"""

FRAG = """
varying vec4 vcol;
void main() { gl_FragColor = vcol; }
"""


class BodyView(QOpenGLWidget):
    """Живое тело: линии связей и точки нейронов рисует видеокарта."""

    picked = Signal(str)

    def __init__(self, core: PanelCore, fps_ms: int = 16, parent=None) -> None:
        super().__init__(parent)
        self.core = core
        self.fps_ms = int(fps_ms)
        self.frames = 0
        self.last_ms = 0.0
        self.build_ms = 0.0
        self.draw_ms = 0.0
        self._prog = None
        self._vbo = None
        self._flat = array.array("f")
        self._edge_verts = 0
        self._node_verts = 0
        self._view_key = None
        self._lvl: list[int] = []
        self._pts: list = []
        self.positions = 0
        self.full_rebuilds = 0
        self._drag = None
        self._theme = theme.theme()      # смена темы = пересобрать кадр целиком
        self._heat: list[tuple] = []
        self._build_heat()
        # Полотно умеет быть маленьким (v0.20.1): жёсткие 430x400 становились
        # МИНИМУМОМ левой колонки, а значит и всего окна — на узком экране окно
        # влезало только потому, что Qt вырастал сверх экрана. Меньше 240x200 не
        # отдаём: тело должно остаться различимым.
        self.setMinimumSize(240, 200)
        self.setFocusPolicy(Qt.StrongFocus)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(self.fps_ms)

    def _build_heat(self) -> None:
        """Таблица цветов нагрева связей — ЦВЕТА ТЕКУЩЕЙ ТЕМЫ.

        hot_color() разбирает hex-строки, а связей 900 — считать это на каждую связь
        каждый кадр незачем (замер: кадр дешевеет вдвое). Но и собрать таблицу один
        раз на всю жизнь нельзя: замер 25.09.2026 показал, что после смены темы
        горячие связи оставались в цветах ПРЕЖНЕЙ темы — поэтому пересобираем.
        """
        self._heat = []
        for i in range(33):
            r, g, b = self.core.hot_color(i / 32.0)
            self._heat.append((r / 255.0, g / 255.0, b / 255.0, 1.0))   # цвет + альфа

    # ---------- жизнь ----------
    def _advance(self) -> None:
        if not self.isVisible() or self.width() < 8 or self.height() < 8:
            return                       # свёрнутое окно кадры не считает
        if theme.theme() != self._theme:
            self._theme = theme.theme()  # тему сменили на ходу — пересобрать кадр
            self._build_heat()           # и цвета горячих связей тоже
            self._view_key = None
        self.core.breathe()
        self.update()

    def set_fps(self, ms: int) -> None:
        self.fps_ms = max(5, int(ms))
        self._timer.start(self.fps_ms)

    # ---------- OpenGL ----------
    def initializeGL(self) -> None:
        self._prog = QOpenGLShaderProgram(self)
        self._prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, VERT)
        self._prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, FRAG)
        self._prog.link()
        self._vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._vbo.create()
        self._vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)

    def _key(self, w: int, h: int):
        """Отпечаток вида: меняется — пересобираем положения, нет — только цвет."""
        core = self.core
        return (round(core.yaw, 4), round(core.pitch, 4), round(core.zoom, 4), w, h,
                round(core.frame / 10.0), theme.theme())

    def _full_rebuild(self, w: int, h: int) -> None:
        """Пересобрать ВСЁ. Делается редко: поворот, зум, ресайз, смена темы.

        Замер 24.09.2026 расставил всё по местам: полная сборка кадра — 16 мс, а
        сама отрисовка на видеокарте — 0.3 мс. Значит дорого НЕ GPU, а цикл Python:
        поэтому положения считаем редко, а цвет и яркость — точечно, по изменению.
        """
        core = self.core
        pts = core.screen(w, h)
        self._pts = pts
        edges = core.body.edges
        flat = self._flat
        del flat[:]
        dim = theme.edge_dim_f() + (1.0,)
        for k in core.edge_keep:
            a, b, _weight = edges[k]
            x1, y1, _ = pts[a]
            x2, y2, _ = pts[b]
            flat.extend((x1, y1, dim[0], dim[1], dim[2], dim[3], 1.0))
            flat.extend((x2, y2, dim[0], dim[1], dim[2], dim[3], 1.0))
        self._edge_verts = len(core.edge_keep) * 2
        for i in range(core.body.size):
            x, y, _ = pts[i]
            r, g, bl = hex_rgb(core.node_color(i))
            flat.extend((x, y, r / 255.0, g / 255.0, bl / 255.0, 0.6, 3.0))
        self._node_verts = core.body.size
        self._lvl = [-1] * len(core.edge_keep)
        self._view_key = self._key(w, h)
        self.full_rebuilds += 1
        self.positions = self._edge_verts + self._node_verts
        self._paint_colors()

    def _paint_colors(self) -> None:
        """Цвет и яркость по изменившемуся: связи — только изменившиеся, узлы — все."""
        core = self.core
        arr = self._flat
        act = core.body.activity
        edges = core.body.edges
        heat = self._heat
        lvl = self._lvl
        dim = theme.edge_dim_f() + (1.0,)
        for j, k in enumerate(core.edge_keep):
            a, b, _weight = edges[k]
            va, vb = act[a], act[b]
            v = va if va < vb else vb               # связь горит, если горят оба конца
            level = -1 if v <= 0.015 else min(32, int(v * 2.2 * 32))
            if level == lvl[j]:
                continue
            lvl[j] = level
            col = dim if level < 0 else heat[level]
            off = j * 14
            arr[off + 2] = col[0]
            arr[off + 3] = col[1]
            arr[off + 4] = col[2]
            arr[off + 5] = col[3]
            arr[off + 9] = col[0]
            arr[off + 10] = col[1]
            arr[off + 11] = col[2]
            arr[off + 12] = col[3]
        base = len(core.edge_keep) * 14
        for i in range(core.body.size):
            r, g, bl = hex_rgb(core.node_color(i))
            hot = act[i] * 3.0
            off = base + i * 7
            arr[off + 2] = r / 255.0
            arr[off + 3] = g / 255.0
            arr[off + 4] = bl / 255.0
            arr[off + 5] = 0.5 + 0.5 * (1.0 if hot > 1.0 else hot)
            arr[off + 6] = core.node_radius(i, base=3.0) * 1.5

    def paintGL(self) -> None:
        import time
        t0 = time.perf_counter()
        f = QOpenGLContext.currentContext().functions()
        r, g, b = hex_rgb(theme.bg())              # фон полотна = фон темы
        f.glClearColor(r / 255.0, g / 255.0, b / 255.0, 1.0)
        f.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        f.glEnable(GL_BLEND)
        f.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        try:
            f.glEnable(GL_PROGRAM_POINT_SIZE)      # размер точки из шейдера
            f.glEnable(GL_MULTISAMPLE)
        except Exception:                          # noqa: BLE001 — старый драйвер не знает
            pass
        w, h = max(1, self.width()), max(1, self.height())
        tb = time.perf_counter()
        if self._key(w, h) != self._view_key:
            self._full_rebuild(w, h)          # поворот/зум/ресайз — редкий путь
        else:
            self._paint_colors()              # обычный кадр: только цвет и яркость
        data = self._flat.tobytes()
        self.build_ms = (time.perf_counter() - tb) * 1000.0
        prog = self._prog
        prog.bind()
        prog.setUniformValue("res", QVector2D(float(w), float(h)))
        self._vbo.bind()
        self._vbo.allocate(data, len(data))
        loc_pos = prog.attributeLocation("pos")
        loc_col = prog.attributeLocation("col")
        loc_sz = prog.attributeLocation("psize")
        prog.enableAttributeArray(loc_pos)
        prog.setAttributeBuffer(loc_pos, GL_FLOAT, 0, 2, STRIDE)
        prog.enableAttributeArray(loc_col)
        prog.setAttributeBuffer(loc_col, GL_FLOAT, 8, 4, STRIDE)
        prog.enableAttributeArray(loc_sz)
        prog.setAttributeBuffer(loc_sz, GL_FLOAT, 24, 1, STRIDE)
        if self._edge_verts:
            f.glDrawArrays(GL_LINES, 0, self._edge_verts)
        if self._node_verts:
            f.glDrawArrays(GL_POINTS, self._edge_verts, self._node_verts)
        for loc in (loc_pos, loc_col, loc_sz):
            prog.disableAttributeArray(loc)
        self.frames += 1
        self.last_ms = (time.perf_counter() - t0) * 1000.0
        self.draw_ms = self.last_ms - self.build_ms

    # ---------- мышь ----------
    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self._drag = (ev.position().x(), ev.position().y(), self.core.yaw, self.core.pitch)

    def mouseMoveEvent(self, ev) -> None:
        if self._drag is None:
            return
        x0, y0, yaw0, pitch0 = self._drag
        self.core.yaw = yaw0 + (ev.position().x() - x0) * 0.01
        self.core.pitch = max(-1.35, min(1.35, pitch0 + (ev.position().y() - y0) * 0.01))

    def mouseReleaseEvent(self, ev) -> None:
        if self._drag is not None:
            x0, y0, _yaw, _p = self._drag
            moved = abs(ev.position().x() - x0) + abs(ev.position().y() - y0)
            self._drag = None
            if moved < 4:                       # это был клик, а не поворот
                self._pick(ev.position().x(), ev.position().y())

    def wheelEvent(self, ev) -> None:
        step = 1.12 if ev.angleDelta().y() > 0 else 1 / 1.12
        self.core.zoom = max(0.3, min(4.0, self.core.zoom * step))

    def _pick(self, x: float, y: float) -> None:
        pts = self._pts or self.core.screen(self.width(), self.height())
        best, dist = -1, 14.0
        for i, (px, py, _d) in enumerate(pts):
            d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if d < dist:
                best, dist = i, d
        if best >= 0:
            self.picked.emit(self.core.body.ids[best])


class FixedLine(QLabel):
    """Подпись ФИКСИРОВАННОЙ высоты: длинный текст обрезается, а не ломает колонку.

    Зачем (v0.20.0, жалоба 26.09.2026): «постоянно в каждый тик что-то моргает в
    статусной строке под графическим экраном, смещая все строки и ломая интерфейс на
    секунду». Подписи менялись каждый такт, длина текста гуляла, и Qt пересобирал всю
    левую колонку: тело, цифры и карточка дёргались. С переносом слов высота строки
    ЗАВИСИТ от длины текста — поэтому здесь переноса нет: текст обрезается многоточием,
    а полный лежит в подсказке при наведении. Высота задана раз и навсегда.
    """

    def __init__(self, lines: int = 1, parent=None, wrap: bool = False) -> None:
        super().__init__(parent)
        # wrap=True — для КАРТОЧКИ УЗЛА: её текст длиннее строки, и обрезать его в одну
        # строку с многоточием значило бы «клик работает, а прочитать нельзя». Перенос
        # внутри подписи высоту НЕ меняет: она задана числом строк, поэтому вид не дёргается.
        self._wrap = bool(wrap)
        self.setWordWrap(self._wrap)
        self._lines = max(1, int(lines))
        self._full = ""
        f = self.fontMetrics()
        h = f.lineSpacing() * self._lines + 4
        self.setMinimumHeight(h)
        self.setMaximumHeight(h)
        # ШИРИНА БОЛЬШЕ НЕ ЗАВИСИТ ОТ ТЕКСТА (v0.20.1). Найдено 26.09.2026: «окно в
        # экран не влазит, при том что FullHD». Причина — закон Qt: у QLabel БЕЗ
        # переноса слов minimumSizeHint() равен ширине ВСЕГО текста, а длинная строка
        # тела просила 1488 px (строка ответа — 960). С телом (430) окно требовало
        # 2246 px и физически не могло стать уже экрана 1920.
        # Теперь подпись просит MIN_W, а свободную ширину забирает целиком (Ignored):
        # что не влезло — режет многоточием _elide(), полный текст лежит в подсказке.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    #: Сколько ширины просит подпись. Мало — лишнее обрежется, но экран не распрёт.
    MIN_W = 72

    def sizeHint(self) -> QSize:                  # noqa: N802 — имя Qt
        return QSize(self.MIN_W, self.minimumHeight())

    def minimumSizeHint(self) -> QSize:           # noqa: N802 — имя Qt
        return QSize(self.MIN_W, self.minimumHeight())

    def setText(self, text: str) -> None:        # noqa: N802 — имя Qt
        self._full = str(text or "")
        self.setToolTip(self._full)
        self._elide()

    def full_text(self) -> str:
        """Что в подписи ЦЕЛИКОМ (обрезанный текст — только для глаз)."""
        return self._full

    def resizeEvent(self, ev) -> None:            # noqa: N802
        super().resizeEvent(ev)
        self._elide()

    def _elide(self) -> None:
        if self._wrap:
            # Переносимую подпись по ШИРИНЕ не режем: Qt переносит сам, лишнее обрежется
            # по высоте. Иначе многострочная карточка схлопнулась бы в одну строку.
            super().setText(self._full)
            return
        # Совсем узкую подпись (до раскладки) не режем — иначе на экране было бы «…».
        # Порог опущен 120 -> 60 в v0.20.1: подпись теперь ВПРАВЕ быть узкой (окно
        # вписывается в экран), и при ширине 72-119 она обязана работать, а не
        # показывать сырой текст шире себя.
        if not self.isVisible() or self.width() < 60:
            super().setText(self._full)
            return
        cut = self.fontMetrics().elidedText(self._full.replace("\n", " "),
                                            Qt.ElideRight, max(20, self.width() - 4))
        super().setText(cut)


def _scroll(page: QWidget) -> QScrollArea:
    """Страница вкладки в ПРОКРУТКЕ — чтобы окно влезало в любой экран.

    Зачем (v0.20.1): вкладка «Настройки» требовала минимум 737 px по высоте (пять
    групп, подсказки под каждой), и это становилось минимумом ВСЕГО окна: на экране
    1366x768 (доступно ~728) окно уже не помещалось. В прокрутке страница просит
    копейки, а не влезшее — листается, как в любой нормальной программе.
    """
    area = QScrollArea()
    area.setWidgetResizable(True)                 # поле растягивается по ширине окна
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setWidget(page)
    return area


def fit_to_screen(win: QMainWindow, w: int | None = None, h: int | None = None) -> tuple[int, int]:
    """Вписать окно в ЭКРАН, а не диктовать экрану размер (v0.20.1, слово Стаса).

    Берём не размер экрана, а доступную площадь (за вычетом панели задач), и ставим
    окно по центру.
    """
    scr = win.screen()
    cw, ch = int(w or win.width()), int(h or win.height())
    if scr is not None:
        av = scr.availableGeometry()
        cw, ch = window_fit.clamp_size(cw, ch, av.width(), av.height())
        win.resize(cw, ch)
        win.move(av.center() - win.rect().center())
        return cw, ch
    win.resize(cw, ch)
    return cw, ch


class SynapseWindow(QMainWindow):
    """Окно управления: тело слева, вкладки справа."""

    tick_done = Signal(dict)

    def __init__(self, core: PanelCore | None = None) -> None:
        super().__init__()
        self.core = core or PanelCore()
        st = self.core.settings()            # язык и тему берём из настроек
        i18n.set_lang(st["lang"])
        theme.set_theme(st["theme"])
        self.setWindowTitle(i18n.t("win.title"))
        # ЖЕЛАЕМЫЙ размер, а не приказ экрану: настоящий ставит fit_to_screen() в
        # конце сборки, когда известны минимальные размеры детей (иначе окно 2246x765
        # не влезало даже в FullHD — найдено 26.09.2026).
        self._want_size = (1360, 820)
        self._ticking = False
        self._running = False
        # СТРОКА ОТВЕТА МОЛЧИТ, ПОКА НИЧЕГО НЕ НАЖАТО (v0.21.1). Слово Стаса 26.09.2026:
        # «убери кучу переносов и ненужных подсказок под кнопками и графикой — оставить
        # только 2 статусные строки». Подсказка-заглушка убрана вместе с ключом act.nothing.
        self._last_key: tuple | None = None
        self._picked = ""                   # узел, по которому кликнули (для карточки)
        self._kind = "net_audit"
        self._fp: dict = {}                 # отпечатки: по ним решаем, трогать ли вид
        self._journal_last: str | None = None
        self._brain_at = 0.0                # когда в последний раз спрашивали мозг
        self._build()
        self._retranslate()
        # НАСТРОЙКИ ЧИТАЕМ СРАЗУ, А НЕ ТОЛЬКО ПО КНОПКЕ (v0.15.0). Найдено живьём
        # 25.09.2026 по жалобе Стаса: «я нажимаю сохранить с русского интерфейса и
        # не замечаю, что там английский подставляется, как открываешь вкладку
        # настроек». Причина: поля вкладки заполнялись ТОЛЬКО кнопкой «Перечитать»
        # и после сохранения, а при старте стояли значения по умолчанию (язык en,
        # «думать вслух» выключено). Оператор жал «Сохранить» — и окно послушно
        # записывало в config.json эти умолчания. Так существо и лишилось думания:
        # в журнале 16:28:56 видно settings.saved changed=2, и одно из двух —
        # idle_thinking: false.
        self._load_settings()
        self.tick_done.connect(self._on_tick)
        self._ui = QTimer(self)
        self._ui.timeout.connect(self._refresh)
        self._ui.start(1000)
        self._run_timer = QTimer(self)
        self._run_timer.timeout.connect(self._tick_async)
        self._refresh()
        fit_to_screen(self, *self._want_size)       # и сразу виден целиком: любой экран

    # ---------- сборка окна ----------
    def _build(self) -> None:
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._build_left())
        split.addWidget(self._build_right())
        split.setSizes([720, 620])
        self.setCentralWidget(split)

    def _build_left(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(6, 6, 6, 6)
        self.view = BodyView(self.core, fps_ms=int(self.core.settings().get("fps_ms") or 16))
        self.view.picked.connect(self._show_neuron)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.btn_run = QPushButton()
        self.btn_run.setCheckable(True)
        self.btn_run.clicked.connect(self._toggle_run)
        self.btn_step = QPushButton()
        self.btn_step.clicked.connect(lambda: self._tick_async(manual=True))
        self.btn_pulse = QPushButton()
        self.btn_pulse.clicked.connect(lambda: self._pulse())
        self.btn_reset = QPushButton()
        self.btn_reset.clicked.connect(self._reset_view)
        self.cmb_color = QComboBox()
        for key, code in (("color.roles", "role"), ("color.nt", "nt"), ("color.zone", "zone")):
            self.cmb_color.addItem("", code)
        self.cmb_color.currentIndexChanged.connect(self._set_coloring)
        for w in (self.btn_run, self.btn_step, self.btn_pulse, self.btn_reset, self.cmb_color):
            row.addWidget(w)
        # РЯД КНОПОК БОЛЬШЕ НЕ ЗАДАЁТ МИНИМУМ ОКНА (v0.20.1). Замер: с темой QSS ряд
        # пяти кнопок просил 636 px — это становилось минимумом ВСЕГО окна, и на узком
        # экране окно вырастало сверх него (Qt не даёт окну стать меньше минимума).
        # Теперь каждая кнопка умеет сжаться до 60 px, а её полное имя лежит в
        # подсказке — так что обрезка текста ничего не прячет.
        for w in (self.btn_run, self.btn_step, self.btn_pulse, self.btn_reset, self.cmb_color):
            w.setMinimumWidth(60)
        lay.addLayout(row)
        # ОТВЕТ НА НАЖАТИЕ, ЦИФРЫ ТЕЛА И КАРТОЧКА УЗЛА (v0.20.0).
        # 1) ЛЕГЕНДА УШЛА В «НАСТРОЙКИ» — слово Стаса 26.09.2026: «легенду цветов
        #    вынеси куда то в настройки, оно не нужно постоянно смотреть на неё».
        #    Под телом она ещё и смещала всё: её высота гуляла вместе с раскраской.
        # 2) СТРОКИ БОЛЬШЕ НЕ ДЁРГАЮТ ВИД — жалоба: «постоянно в каждый тик что то
        #    моргает в статусной строке, смещая все строки, ломая интерфейс». Текст
        #    менялся каждый такт, длина гуляла, Qt пересобирал колонку. Теперь у всех
        #    трёх подписей ФИКСИРОВАННАЯ высота (FixedLine), длинный текст обрезается,
        #    полный — в подсказке.
        # 3) КАРТОЧКА УЗЛА СТОИТ ОТДЕЛЬНОЙ СТРОКОЙ НАД КНОПКАМИ — раньше она была
        #    последней в колонке, за легендой, и до неё было не добраться.
        # КАРТОЧКА — ПУСТОЕ МЕСТО, А НЕ ПОДСКАЗКА (v0.21.1; слово Стаса 26.09.2026:
        # «убери кучу переносов и ненужных подсказок под кнопками и графикой — оставить
        # только 2 статусные строки»). Прежняя подсказка «клик по нейрону — карточка узла»
        # висела здесь всегда. Теперь место под карточку ЗАРЕЗЕРВИРОВАНО (текст появляется
        # на клике и ничего не сдвигает), а глаз видит две живые строки: ответ и цифры тела.
        self.lbl_card = FixedLine(lines=2, wrap=True)
        self.lbl_card.setObjectName("dim")
        lay.addWidget(self.lbl_card)
        self.lbl_action = FixedLine(lines=2, wrap=True)
        lay.addWidget(self.lbl_action)
        self.lbl_body = FixedLine(lines=1)
        lay.addWidget(self.lbl_body)
        self.lbl_legend = QLabel()          # живёт во вкладке «Настройки», не здесь
        self.lbl_legend.setWordWrap(True)
        self.lbl_legend.setObjectName("dim")
        return box

    def _build_right(self) -> QWidget:
        book = QTabWidget()
        self.book = book
        # КАЖДАЯ страница — в прокрутке (v0.20.1): страница не имеет права задавать
        # минимум всему окну, иначе оно перестаёт влезать в экран (нашлось на вкладке
        # «Настройки»: 737 px по высоте при доступных 728 на ноутбучном экране).
        book.addTab(_scroll(self._tab_missions()), "")
        book.addTab(_scroll(self._tab_journal()), "")
        self._thoughts_tab = book.addTab(_scroll(self._tab_thoughts()), "")
        book.addTab(_scroll(self._tab_reports()), "")
        book.addTab(_scroll(self._tab_brain()), "")
        self._settings_tab = book.addTab(_scroll(self._tab_settings()), "")
        # Открытие вкладки настроек = «покажи, что ЛЕЖИТ В ФАЙЛЕ». Иначе в полях
        # остаётся то, что было при рождении окна, и «Сохранить» пишет умолчания.
        book.currentChanged.connect(self._tab_changed)
        return book

    def _tab_missions(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        self.cmb_kind = QComboBox()
        for k in self.core.mission_kinds():
            # КОМАНДА первой (её видно в очереди и в журнале), пояснение — рядом.
            self.cmb_kind.addItem(f"{k['kind']} — {k['label']}", k["kind"])
        self.cmb_kind.currentIndexChanged.connect(self._kind_changed)
        lay.addWidget(self.cmb_kind)
        self.ed_cidr = QLineEdit()
        self.ed_host = QLineEdit()
        self.ed_text = QLineEdit()
        for w in (self.ed_cidr, self.ed_host, self.ed_text):
            lay.addWidget(w)
        self.lbl_hint = QLabel("")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setObjectName("dim")
        lay.addWidget(self.lbl_hint)
        row = QHBoxLayout()
        self.btn_add = QPushButton()
        self.btn_add.clicked.connect(lambda: self._add_mission(now=False))
        self.btn_now = QPushButton()
        self.btn_now.clicked.connect(lambda: self._add_mission(now=True))
        self.btn_net = QPushButton()
        self.btn_net.clicked.connect(lambda: self.ed_cidr.setText(self.core.my_network() or ""))
        # ПОДТВЕРДИТЬ ПРЕДЛОЖЕННОЕ (v0.29.0, этап M6). Слово Стаса 30.09.2026:
        # «и как я это кстати апрувить? кнопка какая то что ли». Кнопка стоит рядом с
        # удалением и жмётся по выделенной строке: предложенная мозгом работа лежит
        # статусом `proposed` и до этого нажатия не исполняется вообще.
        self.btn_ok = QPushButton()
        self.btn_ok.clicked.connect(self._approve_mission)
        self.btn_del = QPushButton()
        self.btn_del.clicked.connect(self._drop_mission)
        for w in (self.btn_add, self.btn_now, self.btn_net, self.btn_ok, self.btn_del):
            row.addWidget(w)
        lay.addLayout(row)
        self.tbl = QTableWidget(0, 6)
        self.tbl.setColumnWidth(0, 52)
        self.tbl.setColumnWidth(1, 110)
        self.tbl.setColumnWidth(2, 90)
        self.tbl.setColumnWidth(3, 150)
        self.tbl.setSelectionBehavior(QTableWidget.SelectRows)
        # Вертикальный заголовок нумерует строки сверху вниз (1, 2, 3…), а самая
        # свежая миссия стоит СВЕРХУ — выходило «новая = №1», что не логично
        # (слово Стаса 25.09.2026). Прячем его: номер теперь в своей колонке «№»
        # и растёт со временем (верхняя строка несёт высший номер).
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.itemSelectionChanged.connect(self._show_result)
        lay.addWidget(self.tbl, 1)
        self.lbl_result = QLabel("")
        self.lbl_result.setWordWrap(True)
        lay.addWidget(self.lbl_result)
        self._kind_changed()
        return box

    def _tab_journal(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        # ЖУРНАЛ НЕ ПЕРЕВОДИТСЯ: событие и его поля — идентификаторы, по ним ищут.
        self.txt_journal = QTextEdit()
        self.txt_journal.setReadOnly(True)
        self.txt_journal.setStyleSheet("font-family:Consolas,monospace;")
        lay.addWidget(self.txt_journal)
        return box

    def _tab_thoughts(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        # ВНУТРЕННЯЯ РЕЧЬ — отдельный канал (v0.18.0). Не журнал действий: здесь
        # существо ДУМАЕТ (💭-строки), а говорит оно в «Журнал» (brain.thought).
        hint = QLabel()
        hint.setWordWrap(True)
        hint.setObjectName("dim")
        self.lbl_thoughts_hint = hint
        lay.addWidget(hint)
        self.txt_thoughts = QTextEdit()
        self.txt_thoughts.setReadOnly(True)
        self.txt_thoughts.setStyleSheet("font-family:Consolas,monospace;")
        lay.addWidget(self.txt_thoughts)
        # ДНЕВНИК МИРА (v0.29.0, M5). Слово Стаса 30.09.2026: «я хочу сотворить живой
        # киберорганизм ещё и с пользой». Свои заметки о своём мире должны быть ВИДНЫ,
        # а не только лежать файлом: здесь последние строки data/diary.md.
        self.lbl_diary = QLabel()
        self.lbl_diary.setWordWrap(True)
        self.lbl_diary.setObjectName("dim")
        lay.addWidget(self.lbl_diary)
        return box

    def _tab_reports(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        self.lst_reports = QListWidget()
        self.lst_reports.currentItemChanged.connect(self._show_report)
        lay.addWidget(self.lst_reports, 1)
        row = QHBoxLayout()
        self.btn_open = QPushButton()
        self.btn_open.clicked.connect(self._open_report)
        row.addWidget(self.btn_open)
        row.addStretch(1)
        lay.addLayout(row)
        self.txt_report = QTextEdit()
        self.txt_report.setReadOnly(True)
        self.txt_report.setStyleSheet("font-family:Consolas,monospace;")
        lay.addWidget(self.txt_report, 3)
        return box

    def _tab_brain(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        self.lbl_brain = QLabel()
        self.lbl_brain.setWordWrap(True)
        lay.addWidget(self.lbl_brain)
        row = QHBoxLayout()
        self.btn_brain_on = QPushButton()
        self.btn_brain_on.clicked.connect(self._brain_on)
        self.btn_brain_off = QPushButton()
        self.btn_brain_off.clicked.connect(self._brain_off)
        # ПРОВЕРИТЬ ОБЛАКО (v0.30.0): живая проба, а не «должно работать». «Готов»
        # по настройкам ещё не значит «отвечает»: ключ могли отозвать, модель снять
        # с обслуживания. Кнопка спрашивает облако коротким вопросом и показывает ответ.
        self.btn_cloud_check = QPushButton()
        self.btn_cloud_check.clicked.connect(self._cloud_check)
        row.addWidget(self.btn_brain_on)
        row.addWidget(self.btn_brain_off)
        row.addWidget(self.btn_cloud_check)
        lay.addLayout(row)
        self.ed_ask = QLineEdit()
        self.ed_ask.returnPressed.connect(self._ask)
        lay.addWidget(self.ed_ask)
        self.btn_ask = QPushButton()
        self.btn_ask.clicked.connect(self._ask)
        lay.addWidget(self.btn_ask)
        self.txt_ask = QTextEdit()
        self.txt_ask.setReadOnly(True)
        lay.addWidget(self.txt_ask, 1)
        return box

    def _tab_settings(self) -> QWidget:
        """Настройки: то, что раньше приходилось править руками в config.json."""
        box = QWidget()
        lay = QVBoxLayout(box)

        grp_view = QGroupBox()
        form = QFormLayout(grp_view)
        self.cmb_lang = QComboBox()
        for code in i18n.available():
            self.cmb_lang.addItem(i18n.name(code), code)
        self.cmb_theme = QComboBox()
        for code in theme.available():
            self.cmb_theme.addItem("", code)
        self.cmb_fps = QComboBox()
        self.cmb_fps.setEditable(True)
        for ms in (8, 16, 25, 33, 50):
            self.cmb_fps.addItem(str(ms), ms)
        self.lbl_lang_hint = QLabel()
        self.lbl_lang_hint.setWordWrap(True)
        self.lbl_theme_hint = QLabel()
        self.lbl_fps_hint = QLabel()
        self.lbl_lang = QLabel()
        self.lbl_theme = QLabel()
        self.lbl_fps = QLabel()
        form.addRow(self.lbl_lang, self.cmb_lang)
        form.addRow(self.lbl_lang_hint)
        form.addRow(self.lbl_theme, self.cmb_theme)
        form.addRow(self.lbl_theme_hint)
        form.addRow(self.lbl_fps, self.cmb_fps)
        form.addRow(self.lbl_fps_hint)
        lay.addWidget(grp_view)

        grp_life = QGroupBox()
        form2 = QFormLayout(grp_life)
        # ОДИН ТЕМП, И ЭТО ТАКТ (v0.22.0). Слово Стаса 26.09.2026: «Зачем мне отдельно
        # время такта и какая то хренотень с инструкцией на простыню, второе число 0?
        # время такта и есть количество мыслей… время такта 0 — поток. всё».
        # Второе поле («пауза до мысли») убрано из окна совсем — остался такт.
        self.ed_tick = QLineEdit()
        self.chk_idle = QCheckBox()
        self.lbl_tick = QLabel()
        self.lbl_idle = QLabel()
        self.lbl_tick_hint = QLabel()
        self.lbl_tick_hint.setWordWrap(True)
        form2.addRow(self.lbl_tick, self.ed_tick)
        form2.addRow(self.lbl_tick_hint)
        form2.addRow(self.lbl_idle)
        # ЖИВАЯ СТРОКА (v0.21.0). Слово Стаса 26.09.2026: «объясни мне настройки
        # противоречивые — такт 30 сек, потом непонятное поле поток… как этим управлять?».
        # Теперь окно считает последствие двух полей и пишет его человеческой фразой.
        self.lbl_life_now = QLabel()
        self.lbl_life_now.setWordWrap(True)
        self.lbl_life_now.setObjectName("dim")
        form2.addRow(self.lbl_life_now)
        self.ed_tick.textChanged.connect(self._update_life_now)
        lay.addWidget(grp_life)

        grp_net = QGroupBox()
        form3 = QFormLayout(grp_net)
        self.chk_write = QCheckBox()
        # ДИАПАЗОН, А НЕ СТРОКА (v0.22.0). Слово Стаса 26.09.2026: «я по прежнему вижу
        # не диапазон портов… а строку Порты для проверки». Два числа: от и до.
        self.sp_port_from = QSpinBox()
        self.sp_port_to = QSpinBox()
        for spin in (self.sp_port_from, self.sp_port_to):
            spin.setRange(1, 65535)
            spin.valueChanged.connect(self._update_port_estimate)
        self.lbl_ports = QLabel()
        self.lbl_ports_to = QLabel()
        self.lbl_ports_hint = QLabel()
        self.lbl_ports_hint.setWordWrap(True)
        self.lbl_ports_hint.setObjectName("dim")
        self.lbl_write_hint = QLabel()
        self.lbl_write_hint.setWordWrap(True)
        ports_row = QHBoxLayout()
        ports_row.addWidget(self.sp_port_from)
        ports_row.addWidget(self.lbl_ports_to)
        ports_row.addWidget(self.sp_port_to)
        ports_row.addStretch(1)
        form3.addRow(self.chk_write)
        form3.addRow(self.lbl_write_hint)
        form3.addRow(self.lbl_ports, ports_row)
        form3.addRow(self.lbl_ports_hint)
        lay.addWidget(grp_net)

        # МОЗГ В НАСТРОЙКАХ (v0.20.0). Слово Стаса 26.09.2026: «сделай переключение
        # CPU/GPU и выбор модели тоже в настройках и положи рядом 1.5b — я сам
        # попробую и решу». Модель выбирается из файлов в собственной папке существа,
        # поэтому 1.5b достаточно положить рядом — он появится в списке сам.
        grp_brain = QGroupBox()
        form4 = QFormLayout(grp_brain)
        self.cmb_brain_model = QComboBox()
        self.cmb_device = QComboBox()
        for code in ("gpu", "cpu"):
            self.cmb_device.addItem("", code)
        self.ed_ngl = QLineEdit()
        self.ed_brain_port = QLineEdit()
        self.lbl_model = QLabel()
        self.lbl_device = QLabel()
        self.lbl_ngl = QLabel()
        self.lbl_brain_port = QLabel()
        self.lbl_brain_hint = QLabel()
        self.lbl_brain_hint.setWordWrap(True)
        # ГАЛКА «ПОДТВЕРЖДЕНИЕ ДЕЙСТВИЙ» (v0.29.0, M6). Слово Стаса 30.09.2026:
        # «Можно это сделать галкой в настройках — типа подтверждение действий».
        self.chk_propose = QCheckBox()
        self.lbl_propose_hint = QLabel()
        self.lbl_propose_hint.setWordWrap(True)
        self.lbl_propose_hint.setObjectName("dim")
        self.chk_propose_confirm = QCheckBox()
        self.lbl_propose_confirm_hint = QLabel()
        self.lbl_propose_confirm_hint.setWordWrap(True)
        self.lbl_propose_confirm_hint.setObjectName("dim")
        form4.addRow(self.lbl_model, self.cmb_brain_model)
        form4.addRow(self.lbl_device, self.cmb_device)
        form4.addRow(self.lbl_ngl, self.ed_ngl)
        form4.addRow(self.lbl_brain_port, self.ed_brain_port)
        form4.addRow(self.lbl_brain_hint)
        form4.addRow(self.chk_propose)
        form4.addRow(self.lbl_propose_hint)
        form4.addRow(self.chk_propose_confirm)
        form4.addRow(self.lbl_propose_confirm_hint)
        # ОБЛАЧНЫЙ МОЗГ В НАСТРОЙКАХ (v0.30.0). Слово Стаса 30.09.2026: «давай сделаем
        # выбор режимов — только облако, только локальный и локальный + облако… чтоб
        # OpenAI совместимые в будущем можно было тот же groq к примеру подключить».
        # Здесь выбор источника мысли, провайдер, модель (поле редактируемое — имена
        # моделей у провайдера меняются), суточный потолок, маска адресов и строка про
        # ключ: окно говорит, ГДЕ ключ лежит и есть ли он, но сам ключ не показывает.
        self.lbl_cloud_mode = QLabel()
        self.cmb_cloud_mode = QComboBox()
        for code in ("local", "online", "hybrid"):
            self.cmb_cloud_mode.addItem("", code)
        self.lbl_cloud_hint = QLabel()
        self.lbl_cloud_hint.setWordWrap(True)
        self.lbl_cloud_hint.setObjectName("dim")
        self.lbl_provider = QLabel()
        self.cmb_provider = QComboBox()
        self.lbl_provider_hint = QLabel()
        self.lbl_provider_hint.setWordWrap(True)
        self.lbl_provider_hint.setObjectName("dim")
        self.lbl_cloud_model = QLabel()
        self.cmb_cloud_model = QComboBox()
        self.cmb_cloud_model.setEditable(True)      # имя модели вписывается руками
        self.lbl_cloud_model_hint = QLabel()
        self.lbl_cloud_model_hint.setWordWrap(True)
        self.lbl_cloud_model_hint.setObjectName("dim")
        self.lbl_cloud_budget = QLabel()
        self.ed_cloud_budget = QLineEdit()
        self.lbl_cloud_budget_hint = QLabel()
        self.lbl_cloud_budget_hint.setWordWrap(True)
        self.lbl_cloud_budget_hint.setObjectName("dim")
        self.chk_cloud_mask = QCheckBox()
        self.lbl_cloud_mask_hint = QLabel()
        self.lbl_cloud_mask_hint.setWordWrap(True)
        self.lbl_cloud_mask_hint.setObjectName("dim")
        self.lbl_cloud_key = QLabel()
        self.lbl_cloud_key.setWordWrap(True)
        self.lbl_cloud_key.setObjectName("dim")
        form4.addRow(self.lbl_cloud_mode, self.cmb_cloud_mode)
        form4.addRow(self.lbl_cloud_hint)
        form4.addRow(self.lbl_provider, self.cmb_provider)
        form4.addRow(self.lbl_provider_hint)
        form4.addRow(self.lbl_cloud_model, self.cmb_cloud_model)
        form4.addRow(self.lbl_cloud_model_hint)
        form4.addRow(self.lbl_cloud_budget, self.ed_cloud_budget)
        form4.addRow(self.lbl_cloud_budget_hint)
        form4.addRow(self.chk_cloud_mask)
        form4.addRow(self.lbl_cloud_mask_hint)
        form4.addRow(self.lbl_cloud_key)
        lay.addWidget(grp_brain)

        # ЛЕГЕНДА ЦВЕТОВ — здесь, а не под телом (слово Стаса 26.09.2026).
        grp_legend = QGroupBox()
        lay_leg = QVBoxLayout(grp_legend)
        self.lbl_legend_hint = QLabel()
        self.lbl_legend_hint.setWordWrap(True)
        self.lbl_legend_hint.setObjectName("dim")
        lay_leg.addWidget(self.lbl_legend)
        lay_leg.addWidget(self.lbl_legend_hint)
        lay.addWidget(grp_legend)

        row = QHBoxLayout()
        self.btn_save = QPushButton()
        self.btn_save.clicked.connect(self._save_settings)
        self.btn_reload = QPushButton()
        self.btn_reload.clicked.connect(self._load_settings)
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_reload)
        row.addStretch(1)
        lay.addLayout(row)
        self.lbl_settings = QLabel()
        self.lbl_settings.setWordWrap(True)
        lay.addWidget(self.lbl_settings)
        self._set_groups = (grp_view, grp_life, grp_net, grp_brain, grp_legend)
        lay.addStretch(1)
        return box

    # ---------- язык: каждая подпись ставится ЗАНОВО ----------
    def _retranslate(self) -> None:
        """Применить язык ко всем виджетам окна (вызывается и на старте, и на смене).

        Строк интерфейса в этом файле нет намеренно: смена языка не требует
        перезапуска — мы просто проходим по своим виджетам и берём текст из словаря.
        """
        self.setWindowTitle(i18n.t("win.title"))
        for i, key in enumerate(("tab.missions", "tab.journal", "tab.thoughts",
                                 "tab.reports", "tab.brain", "tab.settings")):
            self.book.setTabText(i, i18n.t(key))
        self.lbl_thoughts_hint.setText(i18n.t("thoughts.hint"))
        # тело
        self.btn_run.setText(i18n.t("btn.pause") if self._running else i18n.t("btn.live"))
        self.btn_step.setText(i18n.t("btn.step"))
        self.btn_pulse.setText(i18n.t("btn.pulse"))
        self.btn_reset.setText(i18n.t("btn.reset_view"))
        keys = ("color.roles", "color.nt", "color.zone")
        for i, key in enumerate(keys):
            self.cmb_color.setItemText(i, i18n.t(key))
        # Карточка узла: переводим ЕЁ, если узел выбран; иначе пусто — заглушки нет.
        self.lbl_card.setText(self._card_text(self.core.neuron(self._picked)) if self._picked else "")
        # Подсказки на кнопках: что именно делает каждая — вместо догадок.
        self.btn_run.setToolTip(i18n.t("btn.pause.tip" if self._running else "btn.live.tip"))
        self.btn_step.setToolTip(i18n.t("btn.step.tip"))
        self.btn_pulse.setToolTip(i18n.t("btn.pulse.tip"))
        self.btn_reset.setToolTip(i18n.t("btn.reset_view.tip"))
        self.cmb_color.setToolTip(i18n.t("color.tip"))
        # Строка ответа: что произошло. До первого нажатия — пусто, заглушки нет.
        if self._last_key is not None:
            key, kw = self._last_key
            self.lbl_action.setText(i18n.t(key, **kw))
        self._update_legend()
        # миссии
        self.ed_cidr.setPlaceholderText(i18n.t("ph.subnet"))
        self.ed_host.setPlaceholderText(i18n.t("ph.host"))
        self.ed_text.setPlaceholderText(i18n.t("ph.text"))
        self.btn_add.setText(i18n.t("btn.queue"))
        self.btn_now.setText(i18n.t("btn.run_now"))
        self.btn_net.setText(i18n.t("btn.my_network"))
        self.btn_ok.setText(i18n.t("btn.approve"))
        self.btn_del.setText(i18n.t("btn.delete_selected"))
        self.tbl.setHorizontalHeaderLabels([i18n.t("col.num"), i18n.t("col.command"),
                                            i18n.t("col.status"), i18n.t("col.what"),
                                            i18n.t("col.when"), i18n.t("col.result")])
        # отчёты и мозг
        self.btn_open.setText(i18n.t("btn.open_file"))
        self.btn_brain_on.setText(i18n.t("btn.brain_on"))
        self.btn_brain_off.setText(i18n.t("btn.brain_off"))
        self.btn_cloud_check.setText(i18n.t("btn.cloud_check"))
        self.ed_ask.setPlaceholderText(i18n.t("ph.ask", model=self.core.model_short()))
        self.btn_ask.setText(i18n.t("btn.ask"))
        # настройки
        self._set_groups[0].setTitle(i18n.t("set.section.view"))
        self._set_groups[1].setTitle(i18n.t("set.section.life"))
        self._set_groups[2].setTitle(i18n.t("set.section.net"))
        self._set_groups[3].setTitle(i18n.t("set.section.brain"))
        self._set_groups[4].setTitle(i18n.t("set.section.colors"))
        self.lbl_model.setText(i18n.t("set.brain_model"))
        self.lbl_device.setText(i18n.t("set.brain_device"))
        self.lbl_ngl.setText(i18n.t("set.brain_ngl"))
        self.lbl_brain_port.setText(i18n.t("set.brain_port"))
        self.lbl_brain_hint.setText(i18n.t("set.brain.hint"))
        for i, key in enumerate(("device.gpu", "device.cpu")):
            self.cmb_device.setItemText(i, i18n.t(key))
        self.lbl_legend_hint.setText(i18n.t("set.legend.hint"))
        self.lbl_lang.setText(i18n.t("set.language"))
        self.lbl_lang_hint.setText(i18n.t("set.language.hint"))
        self.lbl_theme.setText(i18n.t("set.theme"))
        self.lbl_theme_hint.setText(i18n.t("set.theme.hint"))
        self.lbl_fps.setText(i18n.t("set.fps"))
        self.lbl_fps_hint.setText(i18n.t("set.fps.hint"))
        self.lbl_tick.setText(i18n.t("set.tick"))
        self.lbl_tick_hint.setText(i18n.t("set.tick.hint"))
        self.lbl_idle.setText(i18n.t("set.idle_thinking"))
        self.chk_propose.setText(i18n.t("set.propose"))
        self.lbl_propose_hint.setText(i18n.t("set.propose.hint"))
        self.chk_propose_confirm.setText(i18n.t("set.propose_confirm"))
        self.lbl_propose_confirm_hint.setText(i18n.t("set.propose_confirm.hint"))
        self.lbl_cloud_mode.setText(i18n.t("set.cloud"))
        self.lbl_cloud_hint.setText(i18n.t("set.cloud.hint"))
        for i, code in enumerate(("local", "online", "hybrid")):
            self.cmb_cloud_mode.setItemText(i, i18n.t(f"set.cloud.{code}"))
        self.lbl_provider.setText(i18n.t("set.cloud_provider"))
        self.lbl_provider_hint.setText(i18n.t("set.cloud.provider.hint"))
        self.lbl_cloud_model.setText(i18n.t("set.cloud_model"))
        self.lbl_cloud_model_hint.setText(i18n.t("set.cloud.model.hint"))
        self.lbl_cloud_budget.setText(i18n.t("set.cloud_budget"))
        self.lbl_cloud_budget_hint.setText(i18n.t("set.cloud.budget.hint"))
        self.chk_cloud_mask.setText(i18n.t("set.cloud_mask"))
        self.lbl_cloud_mask_hint.setText(i18n.t("set.cloud.mask.hint"))
        self.lbl_ports.setText(i18n.t("set.ports_from"))
        self.lbl_ports_to.setText(i18n.t("set.ports_to"))
        self.lbl_ports_hint.setText(i18n.t("set.ports.hint"))
        self._update_port_estimate()
        self.lbl_write_hint.setText(i18n.t("set.write_probe.hint"))
        self.chk_write.setText(i18n.t("set.write_probe"))
        self.btn_save.setText(i18n.t("btn.save_settings"))
        self.btn_reload.setText(i18n.t("btn.reload_settings"))
        for i, code in enumerate(theme.available()):
            self.cmb_theme.setItemText(i, i18n.t(f"set.theme.{code}"))
        if getattr(self, "lbl_life_now", None) is not None:
            self._update_life_now()
        self._kind_changed()

    # ---------- тема ----------
    def _apply_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.qss())        # виджеты
        self.view._view_key = None                # и полотно тела пересобрать

    # ---------- действия ----------
    def _say(self, text: str) -> None:
        """Строка «что вышло от нажатия» — ответ на каждую кнопку.

        Речь существа переводить нечем (это его слова), поэтому такие строки просто
        показываем: важно, что оператор ВИДИТ, что нажатие сработало.
        """
        self._last_key = None
        self.lbl_action.setText(str(text)[:700])

    def _say_key(self, key: str, **kw) -> None:
        """То же, но фразой из словаря: при смене языка строка останется верной."""
        self._last_key = (key, kw)
        self.lbl_action.setText(i18n.t(key, **kw))

    def _update_legend(self) -> None:
        """Легенда: что означает каждый цвет при текущей раскраске.

        Цвета рисуем плашками прямо в подписи — так оператор видит не слово
        «сенсорный», а тот самый цвет, которым эти нейроны нарисованы на теле.
        """
        rows = self.core.legend()
        chips = []
        for r in rows:
            chip = f'<span style="color:{r["color"]}">&#9632;</span> {r["label"]}'
            if r.get("count"):
                chip += f' <span style="color:{theme.dim()}">{r["count"]}</span>'
            chips.append(chip)
        head = i18n.t("legend.title")
        text = f"{head}: " + " &nbsp; ".join(chips) if chips else head
        # Рядом с цветом — ЗНАК передачи: «что означает цвет» и «что он ДЕЛАЕТ»
        # обязаны отвечать вместе (слово Стаса 25.09.2026: «на что они влияют?»).
        text += (f' <span style="color:{theme.dim()}">&nbsp;· '
                 f'{self.core.physiology_line()}</span>')
        self.lbl_legend.setText(text)

    def _toggle_run(self) -> None:
        """Жить / Пауза. Состояние берём У САМОЙ КНОПКИ — один источник правды.

        Честно про смысл (слово Стаса: «Жить — значит включить и потом пауза
        остановить?»): да, кнопка включает и выключает ТАКТ — по расписанию
        существо берёт миссии и думает. Тело при этом живёт всегда: оно дышит и
        в паузе, потому что дыхание — это его рефлексы, а не расписание.
        """
        self._running = bool(self.btn_run.isChecked())
        if self._running:
            pause = max(0.0, float(self.core.cfg.get("tick_seconds", 60)))
            if pause <= 0:
                # Непрерывный поток: такт перетекает в такт. Таймер короткий (100 мс),
                # а _ticking не даёт двум тактам пересечься — темп задаёт сам организм.
                self._run_timer.start(100)
                self._say_key("act.live_stream")
                audit.log("panel.live", tick_seconds=0, continuous=True)
            else:
                self._run_timer.start(int(pause * 1000))
                self._say_key("act.live", seconds=int(pause))
                audit.log("panel.live", tick_seconds=pause)
            self.btn_run.setText(i18n.t("btn.pause"))
            self._tick_async()
        else:
            self._run_timer.stop()
            self.btn_run.setText(i18n.t("btn.live"))
            self._say_key("act.pause")
            audit.log("panel.pause")

    def _tick_async(self, manual: bool = False) -> None:
        if self._ticking:
            self._say_key("act.busy")
            return
        self._ticking = True
        if manual:
            self._say_key("act.ticking")

        def work() -> None:
            try:
                out = self.core.tick()
            except Exception as e:                       # noqa: BLE001 — окно не падает
                out = {"status": "error", "text": f"{type(e).__name__}: {e}"}
            self.tick_done.emit(out)

        threading.Thread(target=work, daemon=True).start()

    def _on_tick(self, out: dict) -> None:
        """Показать, чем кончился такт. Раньше ответ выбрасывался — отсюда тишина."""
        self._ticking = False
        if getattr(self, "_closing", False):
            # Окно закрывают, а такт в фоне ещё поднимал СВОЙ сервер — иначе он
            # остался бы сиротой уже ПОСЛЕ того, как closeEvent всё погасил.
            try:
                self.core.brain.stop_server()
            except Exception:                       # noqa: BLE001
                pass
            return
        status = str(out.get("status") or "")
        text = str(out.get("text") or "")
        if out.get("mission"):
            line = i18n.t("tk.tick_mission", id=out["mission"], status=status,
                          text=text[:300])
        elif out.get("tick"):
            line = i18n.t("tk.tick_line", tick=out["tick"],
                          text=text[:300] or i18n.t("tk.no_result_yet"))
        elif status == "cloud":
            # ОТВЕТ ОБЛАКА — В ЧАТ: оператор должен видеть словами, что оно ответило.
            self.txt_ask.append(f"{i18n.t('ask.cloud')} {text}")
            line = text
        elif status == "ask":
            # ОТВЕТ НА РЕПЛИКУ ИДЁТ В ЧАТ (v0.23.0). Жалоба 26.09.2026: «на вкладке
            # мозг существо не отвечает на мои реплики». Ответ возвращался, но
            # показывался только в ОДНОСТРОЧНОЙ строке ответа и обрезался многоточием:
            # оператор видел свой вопрос и тишину. Теперь ответ ложится в поле чата.
            self.txt_ask.append(f"{i18n.t('ask.brain')} {text}")
            line = text
        else:
            line = text or status
        self._say(line)
        self._refresh()

    def _pulse(self) -> None:
        """Импульс телу: показать, кого он задел, и написать строку ответа."""
        self._say_key("act.pulse", text=self.core.pulse(""))

    def _reset_view(self) -> None:
        self.core.yaw, self.core.pitch, self.core.zoom = 0.0, 0.18, 1.0
        self.view._view_key = None                       # пересобрать кадр целиком
        self._say_key("act.reset")

    def _set_coloring(self) -> None:
        self.core.coloring = str(self.cmb_color.currentData() or "role")
        self.view._view_key = None                       # перекрасить всё тело
        self._update_legend()
        self._say_key("act.coloring", how=self.core.coloring_name())

    def _kind_changed(self) -> None:
        kind = self.cmb_kind.currentData()
        self._kind = kind
        meta = self.core.mission_kind(kind)
        needs = meta.get("needs") or ""
        self.ed_cidr.setEnabled(needs == "cidr")
        self.ed_host.setEnabled(needs == "host")
        self.ed_text.setEnabled(needs not in ("cidr", "none"))
        if needs != "cidr":
            self.ed_cidr.clear()
        if needs != "host":
            self.ed_host.clear()
        self.lbl_hint.setText(meta.get("hint") or "")

    def _add_mission(self, now: bool) -> None:
        kind = self._kind
        cidr = self.ed_cidr.text().strip()
        text = self.ed_text.text().strip()
        host = self.ed_host.text().strip()
        ready, why = self.core.plan_mission(kind, cidr, text or host)
        self.lbl_hint.setText(why)
        if not ready:
            return
        rec = self.core.add_mission(kind, cidr=cidr, text=text, host=host)
        self.core.syn.cfg = self.core.cfg
        self.ed_text.clear()
        if now:
            self._tick_async()
        self._refresh()
        key = "msg.running_now" if now else "msg.queued"
        self.lbl_result.setText(i18n.t(key, id=rec["id"], kind=kind))

    def _approve_mission(self) -> None:
        """Подтвердить выделенную предложенную миссию (M6) — кнопка «Подтвердить»."""
        row = self.tbl.currentRow()
        if row < 0:
            return
        mid = self.tbl.item(row, 1).data(Qt.UserRole)
        if not mid:
            return
        res = self.core.approve_mission(mid)
        self.lbl_result.setText(str(res.get("why") or ""))
        self._refresh()

    def _drop_mission(self) -> None:
        row = self.tbl.currentRow()
        if row < 0:
            return
        mid = self.tbl.item(row, 1).data(Qt.UserRole)
        if not mid:
            return
        res = self.core.drop_mission(mid)
        # Честный ответ: у running миссии отмена не останавливает работу.
        self.lbl_result.setText(str(res.get("why") or ""))
        self._refresh()

    def _show_result(self) -> None:
        row = self.tbl.currentRow()
        if row < 0:
            return
        item = self.tbl.item(row, 5)
        self.lbl_result.setText(item.text() if item else "")

    def _card_text(self, n: dict) -> str:
        """Текст карточки узла. Пустая запись — пустая карточка.

        ПОЧЕМУ ОТДЕЛЬНЫМ МЕТОДОМ (v0.21.1). Жалоба Стаса 26.09.2026: «клик по нейрону —
        он выделяется и всё, нигде никакой информации о нём». Причина была НЕ в клике: в
        карточке стояли поля, которых у узла НЕТ, — "partners" (такого поля не существует
        вовсе), "degree" (настоящее имя — "deg") и "receptors" как СПИСОК, хотя это ЧИСЛО
        (roles.py кладёт len(receptors)). На числе ", ".join(3) падало с TypeError — уже
        ПОСЛЕ вспышки, поэтому узел моргал, а информации не было, и ошибка молчала (окно
        stderr не показывает). Теперь каждое поле взято по факту.
        """
        if not n:
            return ""
        dash = i18n.t("dash")
        text = i18n.t("card.base", name=n.get("name") or "",
                      role=i18n.role_name(n.get("role")) if n.get("role") else dash,
                      zone=i18n.zone_name(n.get("zone")),
                      nt=n.get("nt") or dash, links=n.get("deg") or 0)
        if n.get("hub"):
            text += i18n.t("card.hub")
        peers = ", ".join(str(x) for x in (n.get("partners") or [])[:6])
        if peers:
            text += i18n.t("card.peers", peers=peers)
        if n.get("kind_label"):
            text += i18n.t("card.transmission", kind=n["kind_label"])
        if n.get("receptors"):
            text += i18n.t("card.receptors", receptors=n["receptors"])
        return text

    def _show_neuron(self, name: str) -> None:
        """Клик по узлу: сначала карточка, потом вспышка.

        Порядок именно такой: если в карточке что-то сломается, оператор обязан увидеть
        ИНФОРМАЦИЮ, а не пустоту после красивой вспышки. И падать в слоте Qt нельзя —
        исключение тут молчит (окно stderr не показывает), поэтому оно ловится и пишется
        в журнал как panel.card_error: тихий отказ опаснее громкого.
        """
        try:
            n = self.core.neuron(name) or {}
        except Exception as e:                        # noqa: BLE001
            n = {}
            audit.log("panel.card_error", neuron=str(name), error=f"{type(e).__name__}: {e}")
        if not n:
            return
        self._picked = str(n.get("name") or name)
        try:
            self.lbl_card.setText(self._card_text(n))
        except Exception as e:                        # noqa: BLE001
            self.lbl_card.setText("")
            audit.log("panel.card_error", neuron=str(name), error=f"{type(e).__name__}: {e}")
        try:
            self.core.mark_flash([int(n.get("index"))])
        except Exception:                             # noqa: BLE001 — вспышка не главное
            pass

    def _show_report(self, cur, _prev=None) -> None:
        if cur is None:
            return
        self.txt_report.setPlainText(self.core.report_text(cur.data(Qt.UserRole)))

    def _open_report(self) -> None:
        """Открыть человеческий .txt системным просмотрщиком (блокнот и чем угодно)."""
        item = self.lst_reports.currentItem()
        if item is None:
            return
        path = Path(str(item.data(Qt.UserRole))).with_suffix(".txt")
        if not path.exists():
            self.lbl_result.setText(i18n.t("tk.no_text_file", name=path.name))
            return
        try:
            os.startfile(str(path))                       # noqa: S606 — Windows-путь
        except OSError as e:
            self.lbl_result.setText(i18n.t("tk.open_failed", error=e))

    def _ask(self) -> None:
        q = self.ed_ask.text().strip() or i18n.t("ask.default")
        self.txt_ask.append(f"{i18n.t('ask.me')} {q}")
        self.ed_ask.clear()

        def work() -> None:
            answer = self.core.ask(q)
            self.tick_done.emit({"status": "ask", "text": answer})

        threading.Thread(target=work, daemon=True).start()

    def _brain_on(self) -> None:
        self.lbl_brain.setText(i18n.t("brain.starting"))

        def work() -> None:
            res = self.core.brain_on()
            text = "; ".join(res.get("steps") or []) or str(res)
            self.tick_done.emit({"status": "brain", "text": text})

        threading.Thread(target=work, daemon=True).start()

    def _cloud_check(self) -> None:
        """Проверить облако живьём — в фоне, чтобы окно не замирало на таймауте."""
        self.lbl_brain.setText(i18n.t("cloud.checking"))

        def work() -> None:
            try:
                text = self.core.cloud_probe()
            except Exception as e:                      # noqa: BLE001
                text = f"{type(e).__name__}: {e}"
            self.tick_done.emit({"status": "cloud", "text": text})

        threading.Thread(target=work, daemon=True).start()

    def _brain_off(self) -> None:
        res = self.core.brain_off()
        self._refresh()
        self.lbl_brain.setText("; ".join(res.get("steps") or []))

    # ---------- закрытие окна ----------
    def stop_own_brain(self) -> str:
        """Погасить СВОЙ сервер при закрытии окна.

        Находка QA Ореста 25.09.2026: closeEvent у Qt-панели не было ВООБЩЕ —
        закрыв окно при brain.mode=server, оператор молча оставлял llama-server
        (сотни мегабайт) в системе и должен был помнить про `brain --action off`.
        Гасим только свой процесс (brain.stop_server сверяет pid и имя), и пишем
        строку в журнал. Отдельным методом — чтобы это проверялось тестом без Qt.
        """
        self._closing = True                  # фоновому такту больше нечего запускать
        try:
            mode = self.core.brain.mode()
            self.core.brain.stop_server()
            audit.log("panel.close", engine="qt-ogl", brain="stopped", was=mode)
            return "stopped"
        except Exception as e:                      # noqa: BLE001 — окно не падает
            audit.log("panel.close", engine="qt-ogl", error=f"{type(e).__name__}: {e}")
            return "error"

    def closeEvent(self, event) -> None:            # noqa: N802 — имя Qt
        self.stop_own_brain()
        try:
            self._run_timer.stop()                  # такт по расписанию больше не нужен
        except Exception:                           # noqa: BLE001
            pass
        super().closeEvent(event)

    # ---------- настройки ----------
    def _tab_changed(self, index: int) -> None:
        """Переключили вкладку. Открыли настройки — перечитываем файл настроек.

        Слово Стаса 25.09.2026: «язык в окне открывается по умолчанию тот, что
        сейчас стоит в программе» — а было наоборот: в полях стояли умолчания, и
        «Сохранить» записывало их как выбор оператора.
        """
        if index == getattr(self, "_settings_tab", -1):
            self._load_settings()

    def _update_life_now(self) -> None:
        """Показать ЧЕЛОВЕЧЕСКОЕ последствие полей «такт» и «мысль» (v0.21.0).

        Смысл: у мысли и такта ТЕПЕРЬ ОДИН темп (v0.22.0), поэтому считать максимум из
        двух чисел больше не из чего. Фраза короткая: либо поток, либо такт — и оператор
        не держит правило в голове.
        """
        label = getattr(self, "lbl_life_now", None)
        if label is None:
            return
        try:
            tick = max(0.0, float(str(self.ed_tick.text()).replace(",", ".")))
        except ValueError:
            tick = 0.0
        fn = getattr(self.core, "life_now_text", None)
        label.setText("" if fn is None else fn(tick))

    def _update_port_estimate(self) -> None:
        """Цена обхода диапазона — фразой, ДО нажатия (v0.22.0).

        Раньше диапазона не было вовсе, а слишком широкий список отвергался потолком в
        4096 записей. Теперь разрешено всё до 65535, поэтому вместо запрета — ЧЕСТНОЕ
        число: сколько обход займёт на один живой хост (замер 26.09.2026: 256 потоков =
        1.7 мс на порт). Сколько хостов отзовётся, заранее не знает никто — за всех не
        обещаем.
        """
        hint = getattr(self, "lbl_ports_hint", None)
        if hint is None or not hasattr(self, "sp_port_from"):
            return
        fn = getattr(self.core, "probe_estimate", None)
        if fn is None:
            return
        workers = 256
        try:
            workers = int(self.core.settings().get("port_workers") or 256)
        except Exception:
            pass
        hint.setText(fn(int(self.sp_port_from.value()), int(self.sp_port_to.value()), workers))

    def _load_settings(self) -> None:
        """Показать в полях то, что лежит в config.json."""
        st = self.core.settings()
        idx = self.cmb_lang.findData(st["lang"])
        self.cmb_lang.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self.cmb_theme.findData(st["theme"])
        self.cmb_theme.setCurrentIndex(idx if idx >= 0 else 0)
        self.cmb_fps.setCurrentText(str(st["fps_ms"]))
        self.ed_tick.setText(str(st["tick_seconds"]))
        self.chk_idle.setChecked(bool(st["idle_thinking"]))
        self.sp_port_from.setValue(int(st["port_from"]))
        self.sp_port_to.setValue(int(st["port_to"]))
        self.chk_write.setChecked(bool(st["allow_write_probe"]))
        self._update_port_estimate()
        # МОЗГ: список моделей — из файлов существа (models/brain/*.gguf), а не из
        # выдуманного перечня: положил файл рядом — он появился в списке.
        names = [str(x) for x in (st.get("brain_models") or [])]
        keep = self.cmb_brain_model.currentText()
        self.cmb_brain_model.clear()
        self.cmb_brain_model.addItems(names)
        idx = self.cmb_brain_model.findText(str(st.get("brain_model") or keep))
        self.cmb_brain_model.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self.cmb_device.findData(str(st.get("brain_device") or "gpu"))
        self.cmb_device.setCurrentIndex(idx if idx >= 0 else 0)
        self.ed_ngl.setText(str(st.get("brain_ngl")))
        self.ed_brain_port.setText(str(st.get("brain_port")))
        self.chk_propose.setChecked(bool(st.get("brain_propose", True)))
        self.chk_propose_confirm.setChecked(bool(st.get("brain_propose_confirm", True)))
        # ОБЛАКО: режим, провайдер, модель, потолок, маска и строка про ключ.
        idx = self.cmb_cloud_mode.findData(str(st.get("brain_cloud") or "local"))
        self.cmb_cloud_mode.setCurrentIndex(idx if idx >= 0 else 0)
        keep = self.cmb_provider.currentData()
        self.cmb_provider.clear()
        for code in [str(x) for x in (st.get("brain_providers") or [])]:
            self.cmb_provider.addItem(code, code)
        idx = self.cmb_provider.findData(str(st.get("brain_provider") or keep))
        self.cmb_provider.setCurrentIndex(idx if idx >= 0 else 0)
        models = [str(x) for x in (st.get("brain_cloud_models") or [])]
        cur = str(st.get("brain_cloud_model") or "")
        if cur and cur not in models:
            models.insert(0, cur)
        self.cmb_cloud_model.clear()
        self.cmb_cloud_model.addItems(models)
        self.cmb_cloud_model.setCurrentText(cur) if cur else None
        self.ed_cloud_budget.setText(str(st.get("brain_cloud_budget")))
        self.chk_cloud_mask.setChecked(bool(st.get("brain_cloud_mask", True)))
        try:
            ki = self.core.cloud_key_info()
            self.lbl_cloud_key.setText(i18n.t(
                "set.cloud.key_yes" if ki["has"] else "set.cloud.key_no",
                path=ki["path"], provider=ki["provider"], n=ki["n"]))
        except Exception:                       # noqa: BLE001 — ядро без мозга
            self.lbl_cloud_key.setText("")
        self.lbl_settings.setText(i18n.t("set.where", path=self.core.settings_path()))
        self._update_life_now()

    def _save_settings(self) -> None:
        """Сохранить настройки и применить их СРАЗУ (язык, тему, темп кадра)."""
        values = {
            "lang": self.cmb_lang.currentData(),
            "theme": self.cmb_theme.currentData(),
            "fps_ms": self.cmb_fps.currentText(),
            "tick_seconds": self.ed_tick.text(),
            "idle_thinking": self.chk_idle.isChecked(),
            "allow_write_probe": self.chk_write.isChecked(),
            "port_from": self.sp_port_from.value(),
            "port_to": self.sp_port_to.value(),
            "brain_model": self.cmb_brain_model.currentText(),
            "brain_device": self.cmb_device.currentData(),
            "brain_ngl": self.ed_ngl.text(),
            "brain_port": self.ed_brain_port.text(),
            "brain_propose": self.chk_propose.isChecked(),
            "brain_propose_confirm": self.chk_propose_confirm.isChecked(),
            "brain_cloud": self.cmb_cloud_mode.currentData(),
            "brain_provider": self.cmb_provider.currentData(),
            "brain_cloud_model": self.cmb_cloud_model.currentText(),
            "brain_cloud_budget": self.ed_cloud_budget.text(),
            "brain_cloud_mask": self.chk_cloud_mask.isChecked(),
        }
        res = self.core.apply_settings(values)
        self._apply_theme()
        self._retranslate()
        self.view.set_fps(int(res["settings"]["fps_ms"]))
        if self._running:                     # такт мог измениться — перезапустить таймер
            secs = max(0.0, float(res["settings"]["tick_seconds"]))
            self._run_timer.start(100 if secs <= 0 else int(secs * 1000))
        lines = list(res["changed"]) + list(res["errors"])
        self.lbl_settings.setText("; ".join(lines) or i18n.t("set.changed.none"))
        self._load_settings()
        # Язык могли сменить: подписи отчётов и сам текст рисуем заново.
        self.core._reports_cache = None
        self.lst_reports.clear()
        self._fp.clear()
        self._journal_last = None
        self.txt_journal.clear()
        self._refresh()
        audit.log("panel.settings", lang=res["settings"]["lang"], theme=res["settings"]["theme"],
                  changed=len(res["changed"]), errors=len(res["errors"]))

    # ---------- обновление вида ----------
    def _refresh(self) -> None:
        """Обновить окно. Тяжёлое — ТОЛЬКО там, где данные правда изменились.

        Замер 25.09.2026 по жалобе «впечатление, что ничего не изменилось — такие же
        тормоза»: прежний _refresh() стоил 15-23 мс КАЖДУЮ СЕКУНДУ в главном потоке
        окна — пересобирал таблицу миссий (39 строк x 5 ячеек), заново вставлял
        журнал (7 КБ текста) и перечитывал 25 отчётов. Хуже цены был сброс
        выделения и прокрутки: выбрать миссию было нельзя, журнал прыгал в начало.
        Теперь у каждой части свой отпечаток файла, а журнал дописывается.
        """
        fp = self.core.fingerprints()
        self._refresh_body()
        self._refresh_missions(fp.get("missions"))
        self._refresh_journal(fp.get("audit"))
        self._refresh_thoughts(fp.get("thoughts"))
        self._refresh_reports(fp.get("reports"))
        self._refresh_brain()

    def _refresh_body(self) -> None:
        """Дешёвая часть: цифры тела (одно чтение state.json)."""
        st = self.core.status()
        body = self.core.body
        self.lbl_body.setText(i18n.t(
            "body.line", nodes=body.size, edges=len(body.edges), lit=st.get("lit"),
            energy=st.get("energy"), brain=st.get("brain"), ticks=st.get("total_ticks"),
            wakeups=st.get("wakeups"), queued=st.get("queued"), frames=self.view.frames,
            ms=f"{self.view.last_ms:.1f}"))

    def _refresh_missions(self, key) -> None:
        """Таблица миссий: пересобираем ТОЛЬКО когда очередь изменилась.

        Ячейки не создаём заново (иначе Qt теряет выделение и прокрутку), а обновляем
        текст у существующих и возвращаем выделение на ту же миссию.
        """
        if key == self._fp.get("missions"):
            return
        self._fp["missions"] = key
        keep = self._selected_id()
        rows = self.core.mission_rows(40)
        self.tbl.setUpdatesEnabled(False)
        self.tbl.setRowCount(len(rows))
        for r, m in enumerate(rows):
            # «what» — переводимая подпись, статус и команда — как в файле.
            cells = [m["seq"], m["kind"], m["status"], m["what"], m["created"],
                     m["result"]]
            for cidx, val in enumerate(cells):
                item = self.tbl.item(r, cidx)
                if item is None:
                    item = QTableWidgetItem()
                    self.tbl.setItem(r, cidx, item)
                item.setText(str(val))
                if cidx == 1:                     # id миссии живёт в колонке команды
                    item.setData(Qt.UserRole, m["id"])
                if keep is not None and m["id"] == keep:
                    self.tbl.selectRow(r)
        self.tbl.setUpdatesEnabled(True)

    def _selected_id(self):
        """id выбранной миссии — чтобы после пересборки выделение осталось на ней."""
        row = self.tbl.currentRow()
        if row < 0:
            return None
        item = self.tbl.item(row, 1)
        return item.data(Qt.UserRole) if item else None

    def _refresh_journal(self, key) -> None:
        """Журнал: ДОПИСЫВАЕМ новое, а не вставляем весь текст заново."""
        rows = self.core.audit_tail(400)
        if not rows:
            return
        last = json.dumps(rows[-1], sort_keys=True, ensure_ascii=False)
        if last == self._journal_last:
            return
        self._fp["audit"] = key
        start = 0
        if self._journal_last is not None:
            keys = [json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows]
            if self._journal_last in keys:
                start = keys.index(self._journal_last) + 1
            else:
                self.txt_journal.clear()          # журнал ушёл в ротацию — начинаем заново
        bar = self.txt_journal.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 2
        lines = [" ".join([str(rec.get("at", ""))[11:19], str(rec.get("event", "")),
                           " ".join(f"{k}={v}" for k, v in rec.items() if k not in ("at", "event"))])
                 for rec in rows[start:]]
        if lines:
            self.txt_journal.append("\n".join(lines))
        self._journal_last = last
        if follow:
            bar.setValue(bar.maximum())

    def _refresh_thoughts(self, key) -> None:
        """Вкладка «Мысли»: дописываем внутреннюю речь, когда лог изменился."""
        if key == self._fp.get("thoughts"):
            return
        self._fp["thoughts"] = key
        lines = self.core.thoughts_tail(400)
        # Дневник перерисовываем всегда: файл крошечный, а пропустить изменение
        # мира из-за «того же отпечатка мыслей» нельзя.
        rows = self.core.diary_tail(6)
        self.lbl_diary.setText(i18n.t("thoughts.diary")
                               + ("\n" + "\n".join(rows) if rows else ""))
        bar = self.txt_thoughts.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 2
        self.txt_thoughts.setPlainText("\n".join(lines))
        if follow:
            bar.setValue(bar.maximum())

    def _refresh_reports(self, key) -> None:
        if key == self._fp.get("reports"):
            return
        self._fp["reports"] = key
        known = {self.lst_reports.item(i).data(Qt.UserRole) for i in range(self.lst_reports.count())}
        for rep in self.core.reports(25):
            if rep["path"] not in known:
                item = QListWidgetItem(rep.get("title") or os.path.basename(rep["path"]))
                item.setData(Qt.UserRole, rep["path"])
                self.lst_reports.insertItem(0, item)

    def _refresh_brain(self) -> None:
        """Мозг — раз в 5 секунд: проба сервера это сетевой запрос, ему не место в кадре."""
        now = time.time()
        if now - self._brain_at < 5.0:
            return
        self._brain_at = now
        b = self.core.brain_state()
        model = Path(str(b.get("model") or "")).name or i18n.t("dash")
        line = i18n.t("brain.line", mode=b.get("mode"),
                      server="alive" if b.get("available") else "none", model=model)
        # ОБЛАКО видно на вкладке «Мозг» (v0.30.0): иначе оператор гадал бы, откуда
        # пришёл ответ, и почему он приходит при погашенном локальном сервере.
        cl = b.get("cloud") or {}
        if cl.get("mode") and cl["mode"] != "local":
            line += " · " + i18n.t("brain.cloud.line",
                                     mode=i18n.t(f"set.cloud.{cl['mode']}"),
                                     provider=cl.get("provider") or "?",
                                     model=cl.get("model") or "?",
                                     spent=cl.get("spent") or 0,
                                     cap=cl.get("cap") or "—")
        self.lbl_brain.setText(line)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    fmt = QSurfaceFormat()
    fmt.setSamples(int(os.environ.get("SYNAPSE_GL_SAMPLES", "4")))   # сглаживание линий
    fmt.setDepthBufferSize(0)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyleSheet(theme.qss())        # тему ставим ДО окна: виджеты родятся в ней
    win = SynapseWindow()
    win.show()
    audit.log("panel.open", engine="qt-ogl", size=f"{win.width()}x{win.height()}",
              lang=i18n.lang(), theme=theme.theme())
    if "--selftest" in argv:
        seconds = 3.0
        for i, a in enumerate(argv):
            if a == "--seconds" and i + 1 < len(argv):
                seconds = float(argv[i + 1])
        for i, a in enumerate(argv):
            if a == "--size" and i + 1 < len(argv):
                w_s, _, h_s = argv[i + 1].partition("x")
                fit_to_screen(win, int(w_s), int(h_s))   # и этот размер вписываем в экран
        stats: dict = {}

        def finish() -> None:
            stats["frames"] = win.view.frames
            stats["ms"] = round(win.view.last_ms, 2)
            stats["build"] = round(win.view.build_ms, 2)
            stats["draw"] = round(win.view.draw_ms, 2)
            stats["samples"] = int(os.environ.get("SYNAPSE_GL_SAMPLES", "4"))
            stats["rebuilds"] = win.view.full_rebuilds
            stats["size"] = f"{win.width()}x{win.height()}"
            stats["lang"] = i18n.lang()
            stats["theme"] = theme.theme()
            print("SELFTEST", stats, flush=True)
            win.close()
            app.quit()

        QTimer.singleShot(int(seconds * 1000), finish)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
