"""Отрисовка тела Synapse: быстрый путь (Pillow) и запасной (canvas).

Замеры, из которых выросли эти решения (секундомер, а не глаз):
  * СТАРЫЙ кадр 700x680 стоил 34 мс: полнокадровый GaussianBlur свечения (~15 мс
    вместе с растяжкой), ImageChops.add по всему кадру (~20 мс), 302 узла
    кружками (8.5 мс). При кадре 50 мс это почти весь бюджет — окно живёт
    «в натяг», и это ровно то, что Стас почувствовал как «тормозит».
  * НОВЫЙ кадр — 8.4 мс: связи и узлы лежат двумя готовыми слоями (пересборка
    ~12 мс, но только при повороте/зуме/смене раскраски), а каждый кадр это
    копия слоя связей + горячие связи + свечение спрайтами + вставка слоя узлов
    + только зажжённые узлы поверх.

Почему узлы — ОТДЕЛЬНЫЙ слой, а не часть основного: горячие связи рисуются поверх
связей и, если узлы уже впечены в тот же слой, они затирают узлы тёмно-зелёными
полосами (нашла тестом: «спокойное тело светилось ярче возбуждённого»). Слой узлов
вставляется ПОСЛЕДНИМ — порядок как в старом кадре: связи, свечение, узлы.

Свечение — маленький радиальный спрайт, складывается аддитивно ВЫРЕЗКОЙ вокруг
зажжённого узла (никакого блюра всего кадра). Вырезка ОБЯЗАНА обрезаться по кадру:
за границей картинки Pillow подкладывает чёрное, и paste затирает узлы пятном.

ЗАМЕР 24.09 (вторая жалоба на тормоза, уже на развороте окна на весь экран):
цена кадра растёт РОВНО с числом пикселей — 700x650 это 6.4 мс, 1920x1080 это
29 мс; передача картинки в Tk 2.9 -> 11.4 мс, перерисовка окна 3.6 -> 15.8 мс.
Итого разворот стоил ~56 мс на кадр (18 кадров в секунду) против ~14 мс в
обычном окне. Поэтому картинка рисуется НЕ по размеру окна, а по бюджету
площади (MAX_AREA) и ставится в окно как есть, а кадр, который не изменился
(тело спокойно и не повернулось), окну вообще не показывается — см. frame().
Растягивать маленькую картинку на весь экран пробовала: BILINEAR 48 мс,
BICUBIC 58 мс (хуже прямого рендера), NEAREST дешёв, но 1-пиксельные линии
превращаются в ступеньки, а Tk всё равно платит за полноэкранную вставку.

ТРЕТИЙ замер 24.09 (разворот на весь экран, картинка 1049x590): 24.5 мс на кадр,
из них ~13 мс — одна только передача картинки в Tk, и она тоже растёт с числом
пикселей. Поэтому в окне тело собирается ГИБРИДОМ (класс LiveBody): неподвижное
(связи и спокойные узлы) лежит ОДНОЙ картинкой и пересобирается только при
повороте/зуме, а живое (горячие связи и свечение зажжённых узлов) — это ~120
линий и до 64 спрайтов, которые Tk двигает сам. Тот же кадр: 8.5 мс против 24.5.
ImageRenderer остаётся: он рисует неподвижное для гибрида, даёт спрайты свечения,
и на нём же проверяются тесты без окна.

Pillow НЕ обязателен: нет его — панель молча уходит на canvas (медленнее, но
работает без единой зависимости).
"""
from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw

from synapse.panel_core import hex_rgb

BG_RGB = (10, 14, 19)
HOT_DIM = "#3a5a3a"     # цвет горячей связи «на нуле» — с него начинается разогрев


def rgb_hex(rgb) -> str:
    """(255, 233, 168) -> '#ffe9a8'. Tk не понимает кортеж: скажет «unknown color name"."""
    return "#%02x%02x%02x" % (int(rgb[0]), int(rgb[1]), int(rgb[2]))
LIT_MIN = 0.05          # ниже — узел считается спокойным (живёт в слое узлов)
GRAD_SIDE = 32          # сторона радиального градиента до растяжки на радиус свечения
MAX_AREA = 620_000      # бюджет площади картинки, пикселей (замер 24.09: ~9 мс на кадр
                        # при 0.6 млн; 2.07 млн стоили 29 мс). Окно больше — картинка
                        # та же, вокруг просто воздух; тело крупнее — колесом (проекция)


def picture_size(cw: int, ch: int, max_area: int = MAX_AREA) -> tuple[int, int]:
    """Размер картинки по бюджету площади. Один закон на картинку и на гибрид.

    Замер 24.09: кадр стоит ровно за пиксели (0.6 млн — 9 мс, 2.07 млн — 29 мс),
    а передача в Tk прибавляет ещё столько же. Поэтому окно на весь экран картинку
    больше НЕ делает: растёт поле вокруг. Крупнее тело — колесом (это проекция).
    """
    cw, ch = max(200, int(cw)), max(200, int(ch))
    scale = (max_area / float(cw * ch)) ** 0.5
    if scale > 1.0:
        scale = 1.0                      # маленькое окно не растягиваем
    return max(200, int(cw * scale)), max(200, int(ch * scale))


class ImageRenderer:
    """Кадры тела картинкой: одна картинка на canvas вместо тысячи предметов."""

    def __init__(self, core) -> None:
        self.core = core
        self.w = 0
        self.h = 0
        self.cw = 0                  # размер ОКНА (больше картинки — вокруг воздух)
        self.ch = 0
        self._static: Image.Image | None = None      # связи
        self._nodes: Image.Image | None = None       # спокойные узлы (RGBA)
        self._key: tuple | None = None
        self._photo = None
        self._grads: dict[int, Image.Image] = {}
        self._glows: dict[tuple, Image.Image] = {}
        self.frames = 0
        self.frames_skipped = 0      # кадры, которые окну не показывали: картинка та же
        self.last_lit = 0
        self._sig: tuple | None = None            # сколько узлов реально дорисовано поверх слоёв

    # ---------- слои ----------
    def resize(self, w: int, h: int) -> None:
        """Окно назвало размер — считаем размер КАРТИНКИ (бюджет площади) и запоминаем окно.

        Замер 24.09: кадр стоит ровно за пиксели, а не за фигуру. Поэтому окно на весь
        экран больше картинку НЕ делает — растёт только поле вокруг. Крупнее тело —
        колесом (zoom меняет проекцию, а не число пикселей).
        """
        cw, ch = max(200, int(w)), max(200, int(h))
        iw, ih = picture_size(cw, ch)
        if (cw, ch, iw, ih) != (self.cw, self.ch, self.w, self.h):
            self.cw, self.ch, self.w, self.h = cw, ch, iw, ih
            self._reset()

    def picture_box(self) -> tuple[int, int]:
        """Размер картинки — панель по нему ставит её в центр окна и ловит клик."""
        return self.w, self.h

    def _reset(self) -> None:
        self._static = None
        self._nodes = None
        self._key = None
        self._photo = None
        self._sig = None

    def _static_key(self) -> tuple:
        return (round(self.core.yaw, 4), round(self.core.pitch, 4), round(self.core.zoom, 3),
                self.w, self.h, len(self.core.edge_keep), self.core.coloring)

    def _build(self, pts) -> None:
        """Пересобрать оба слоя: связи и спокойные узлы. Зовётся только при повороте."""
        base = Image.new("RGB", (self.w, self.h), BG_RGB)
        draw = ImageDraw.Draw(base)
        body = self.core.body
        for k in (self.core.edge_keep or range(len(body.edges))):
            a, b, _w = body.edges[k]
            draw.line([pts[a][0], pts[a][1], pts[b][0], pts[b][1]], fill=(22, 32, 43), width=1)

        nodes = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        nd = ImageDraw.Draw(nodes)
        for i in range(body.size):
            r = body.base_radius(i)               # радиус БЕЗ активности: слой статичен
            x, y = pts[i][0], pts[i][1]
            nd.ellipse([x - r, y - r, x + r, y + r], fill=hex_rgb(self.core.calm_color(i)) + (255,))

        self._static, self._nodes, self._key = base, nodes, self._static_key()

    def _static_layer(self, pts) -> Image.Image:
        """Слой связей. Пересобираем только когда тело повернулось/приблизилось."""
        key = self._static_key()
        if self._static is None or key != self._key:
            self._build(pts)
        return self._static

    def _node_layer(self, pts) -> Image.Image:
        self._static_layer(pts)                  # он же собирает оба слоя
        return self._nodes

    # ---------- свечение ----------
    def _grad(self, r: int) -> Image.Image:
        """Радиальный градиент свечения нужного радиуса (строим один раз на радиус)."""
        g = self._grads.get(r)
        if g is None:
            g0 = Image.new("L", (GRAD_SIDE, GRAD_SIDE), 0)
            px = g0.load()
            c = (GRAD_SIDE - 1) / 2.0
            for y in range(GRAD_SIDE):
                for x in range(GRAD_SIDE):
                    d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5 / c
                    px[x, y] = int(255 * (0.0 if d >= 1 else (1.0 - d) ** 2))
            g = g0.resize((int(r) * 2 + 1, int(r) * 2 + 1), Image.BILINEAR)
            self._grads[r] = g
        return g

    def _glow_sprite(self, r: int, rgb: tuple[int, int, int]) -> Image.Image:
        """Готовое пятно свечения (цвет загрублён до 32 — глаз разницы не видит, кэш цел)."""
        q = tuple(int(v) - int(v) % 32 for v in rgb)
        sp = self._glows.get((r, q))
        if sp is None:
            mask = self._grad(r).convert("RGB")
            sp = ImageChops.multiply(mask, Image.new("RGB", mask.size, q))
            self._glows[(r, q)] = sp
        return sp

    def _add_glow(self, img: Image.Image, pts) -> Image.Image:
        """Аддитивное свечение вокруг зажжённых узлов — вырезка за вырезкой, без блюра кадра."""
        body = self.core.body
        w, h = self.w, self.h
        for i, a in enumerate(body.activity):
            if a <= LIT_MIN:
                continue
            hub = bool(i < len(body.info) and body.info[i].get("hub"))
            r = int(max(2, round(2.5 + 7.0 * a) * (1.4 if hub else 1.0)))
            sp = self._glow_sprite(r, self.core.glow_color(i, a))
            # ОБРЕЗКА ПО КАДРУ — с двух сторон сразу: за границей картинки crop
            # подкладывает чёрное, и paste затёр бы узлы тёмным пятном.
            x, y = int(pts[i][0] - r), int(pts[i][1] - r)
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(w, x + sp.size[0]), min(h, y + sp.size[1])
            if x1 <= x0 or y1 <= y0:
                continue                          # пятно целиком за кадром
            box = (x0, y0, x1, y1)
            part = sp.crop((x0 - x, y0 - y, x1 - x, y1 - y))
            img.paste(ImageChops.add(img.crop(box), part), box)
        return img

    def glow_rgba(self, r: int, rgb: tuple[int, int, int]) -> Image.Image:
        """То же свечение, но с ПРОЗРАЧНОСТЬЮ: canvas не умеет складывать аддитивно,
        зато умеет класть спрайт с альфой поверх фона — для гибрида это и нужно."""
        q = tuple(int(v) - int(v) % 32 for v in rgb)
        side = int(r) * 2 + 1
        img = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        px = img.load()
        c = (side - 1) / 2.0
        for y in range(side):
            for x in range(side):
                d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5 / c
                px[x, y] = (q[0], q[1], q[2], 0 if d >= 1 else int(255 * (1.0 - d) ** 2))
        return img

    # ---------- кадр ----------
    def render_image(self, pts=None) -> Image.Image:
        """Кадр целиком (без Tk — эту часть и проверяем тестом)."""
        self.resize(self.w or 900, self.h or 600)
        pts = pts if pts is not None else self.core.screen(self.w, self.h)
        body = self.core.body
        img = self._static_layer(pts).copy()
        draw = ImageDraw.Draw(img)

        for k, v in self.core.hot_edges(self.core.hot_pool):
            a, b, _w = body.edges[k]
            draw.line([pts[a][0], pts[a][1], pts[b][0], pts[b][1]], fill=self.core.hot_color(v),
                      width=1 + int(2 * min(1.0, v * 3)))

        self._add_glow(img, pts)

        nodes = self._node_layer(pts)
        img.paste(nodes, (0, 0), nodes)          # узлы — поверх связей: порядок как задумано

        draw = ImageDraw.Draw(img)
        lit = 0
        for i, a in enumerate(body.activity):
            if a <= LIT_MIN:
                continue                          # спокойный узел уже лежит в слое узлов
            lit += 1
            r = self.core.node_radius(i, base=2.2)
            x, y = pts[i][0], pts[i][1]
            draw.ellipse([x - r, y - r, x + r, y + r], fill=hex_rgb(self.core.node_color(i)))

        self.frames += 1
        self.last_lit = lit
        return img

    def _signature(self) -> tuple:
        """Отпечаток кадра: поворот/зум/раскраска + какие узлы зажжены (загрублённо).

        Тело между поворотами выглядит ОДИНАКОВО, а пересобирать и пересылать в Tk
        ту же картинку — чистая потеря: на развороте это 40-56 мс за кадр.
        """
        lit = tuple((i, round(a, 3)) for i, a in enumerate(self.core.body.activity)
                    if a > LIT_MIN)
        return (self._static_key(), lit)

    def frame(self, pts=None):
        """Кадр в Tk (PhotoImage одной картинкой). Tk зовём только отсюда.

        Кадр, совпавший с прошлым, возвращаем ТЕМ ЖЕ объектом — панель по нему
        видит, что показывать нечего, и не трогает окно вообще.
        """
        from PIL import ImageTk
        sig = self._signature()
        if self._photo is not None and sig == self._sig:
            self.frames_skipped += 1
            return self._photo
        img = self.render_image(pts)
        self._sig = sig
        if self._photo is None:
            self._photo = ImageTk.PhotoImage(img)
        else:
            try:
                self._photo.paste(img)          # без новой аллокации каждый кадр
            except Exception:                   # noqa: BLE001 — версия Pillow постарее
                self._photo = ImageTk.PhotoImage(img)
        return self._photo

class LiveBody:
    """Тело в окне: запечённая картинка + мелкие предметы Tk поверх неё.

    Почему так (замер 24.09, окно 1920x1080): обновлять каждый кадр ВСЮ картинку —
    24.5 мс (рендер ~12 + передача в Tk ~13, и оба слагаемых растут с числом
    пикселей). Тот же кадр, собранный иначе, — 8.5 мс: неподвижное пересобирается
    только при повороте, а живое Tk двигает сам. Порядок слоёв снизу вверх:
    картинка (связи + спокойные узлы) -> горячие связи -> спрайты свечения.
    Свечение СВЕРХУ, поэтому возбуждённое тело ярче спокойного (та грабля, что
    нашлась тестом в v0.4, здесь закрыта по построению).
    """

    def __init__(self, core, hot_pool: int = 120, glow_pool: int = 64) -> None:
        self.core = core
        self.canvas = None
        self._ir = ImageRenderer(core)       # он рисует неподвижное и даёт спрайты
        self.hot_pool = int(hot_pool)
        self.glow_pool = int(glow_pool)
        self.w = self.h = self.cw = self.ch = 0
        self.offset = (0, 0)                # где картинка стоит в холсте (окно больше её)
        self.pts: list = []
        self._photo = None
        self._image_item = None
        self._hot_items: list = []
        self._hot_state: list = []
        self._glow_items: list = []
        self._glow_state: list = []
        self._sprites: dict = {}
        self._keep: list = []               # ссылки на спрайты: Tk теряет картинку без них
        self.frames = 0
        self.frames_skipped = 0             # кадры, где живое не менялось и Tk не трогали
        self.rebuilds = 0
        self.last_lit = 0
        self._dirty = True

    # ---------- окно ----------
    def attach(self, canvas) -> None:
        self.canvas = canvas
        self._dirty = True

    def picture_box(self) -> tuple[int, int]:
        return self.w, self.h

    def resize(self, w: int, h: int) -> None:
        self._ir.resize(w, h)
        self.cw, self.ch = self._ir.cw, self._ir.ch
        if (self._ir.w, self._ir.h) != (self.w, self.h):
            self.w, self.h = self._ir.w, self._ir.h
            self._dirty = True

    def _reset(self) -> None:
        """Неподвижное надо пересобрать (поворот, зум, раскраска, смена размера)."""
        self._dirty = True

    def forget_items(self) -> None:
        """Холст почистили (canvas.delete) — старые номера предметов больше не годятся."""
        self._image_item = None
        self._hot_items = []
        self._hot_state = []
        self._glow_items = []
        self._glow_state = []
        self._dirty = True

    # ---------- сборка ----------
    def rebuild(self) -> None:
        """Запечь неподвижное: связи и спокойные узлы одной картинкой."""
        from PIL import ImageTk
        cv = self.canvas
        if cv is None or not self.w or not self.h:
            return
        self.pts = self.core.screen(self.w, self.h)
        img = self._ir._static_layer(self.pts)
        if self._photo is None or (self._photo.width(), self._photo.height()) != (self.w, self.h):
            self._photo = ImageTk.PhotoImage(img)
            self._keep.append(self._photo)
        else:
            self._photo.paste(img)                    # дешевле, чем новая картинка Tk
        if self._image_item is None:
            self._image_item = cv.create_image(self.offset[0], self.offset[1], anchor="nw",
                                               image=self._photo)
        else:
            cv.itemconfigure(self._image_item, image=self._photo)
        self._ensure_hot(len(self.core.hot_edges(self.hot_pool)))
        for _ in range(self.glow_pool - len(self._glow_items)):
            it = cv.create_image(-100, -100, anchor="center", state="hidden")
            self._glow_items.append(it)
            self._glow_state.append(None)
        self.rebuilds += 1
        self._dirty = False

    def _ensure_hot(self, need: int) -> None:
        """Пул горячих связей по числу нужных сейчас.

        Досоздавать приходится и в кадре: тело успокоилось (горячих нет), потом
        связь разогрелась — и если пул растёт только при сборке, линия появится
        лишь на следующем повороте. Поймано тестом, не глазом.
        """
        cv = self.canvas
        while len(self._hot_items) < need:
            self._hot_items.append(cv.create_line(0, 0, 0, 0, fill=HOT_DIM))
            self._hot_state.append(None)
        while len(self._hot_items) > need:
            cv.delete(self._hot_items.pop())
            self._hot_state.pop()

    def _sprite(self, r: int, rgb: tuple[int, int, int]):
        """Спрайт свечения с прозрачностью, кэш по (радиус, цвет загрублённо до 32)."""
        from PIL import ImageTk
        key = (int(r), tuple(int(v) - int(v) % 32 for v in rgb))
        sp = self._sprites.get(key)
        if sp is None:
            sp = ImageTk.PhotoImage(self._ir.glow_rgba(key[0], key[1]))
            self._sprites[key] = sp
            self._keep.append(sp)
        return sp

    # ---------- кадр ----------
    def step(self) -> None:
        """Живое: горячие связи и свечение зажжённых узлов. Неподвижное не трогаем."""
        cv = self.canvas
        if cv is None:
            return
        if self._dirty or self._image_item is None:
            self.rebuild()
        self.frames += 1
        touched = 0
        body = self.core.body
        pts = self.pts
        ox, oy = self.offset
        # горячие связи: трогаем холст только у тех, что заметно изменились
        hot_now = self.core.hot_edges(self.hot_pool)
        self._ensure_hot(len(hot_now))
        for idx, (k, v) in enumerate(hot_now):
            if idx >= len(self._hot_items):
                break
            st = (k, round(v, 2))
            if st == self._hot_state[idx]:
                continue
            self._hot_state[idx] = st
            a, b, _w = body.edges[k]
            cv.coords(self._hot_items[idx], pts[a][0] + ox, pts[a][1] + oy,
                      pts[b][0] + ox, pts[b][1] + oy)
            touched += 1
            cv.itemconfigure(self._hot_items[idx], fill=rgb_hex(self.core.hot_color(v)),
                             width=1 + int(2 * min(1.0, v * 3)))
        # свечение зажжённых узлов (спрайты с альфой, сверху всего).
        # Состояние спрайта — (ключ спрайта, виден ли): трогаем холст только на изменение.
        n = 0
        for i, a in enumerate(body.activity):
            if a <= LIT_MIN:
                continue
            if n >= len(self._glow_items):
                break
            hub = bool(i < len(body.info) and body.info[i].get("hub"))
            r = int(max(2, round(2.5 + 7.0 * a) * (1.4 if hub else 1.0)))
            col = self.core.glow_color(i, a)
            key = (r, tuple(int(v) - int(v) % 32 for v in col))
            it = self._glow_items[n]
            st = self._glow_state[n]
            if st is None or st[0] != key:
                cv.itemconfigure(it, image=self._sprite(r, col))
            if st is None or not st[1] or st[0] != key:
                cv.itemconfigure(it, state="normal")
            self._glow_state[n] = (key, True)
            cv.coords(it, pts[i][0] + ox, pts[i][1] + oy)
            touched += 1
            n += 1
        for idx in range(n, len(self._glow_items)):
            st = self._glow_state[idx]
            if st is None or st[1]:
                cv.itemconfigure(self._glow_items[idx], state="hidden")
                self._glow_state[idx] = (st[0] if st else None, False)
                touched += 1
        self.last_lit = n
        if not touched:
            self.frames_skipped += 1        # тело замерло — окно в этом кадре не тронули

    def frame(self, pts=None):
        """Совместимость с прежним путём: кадр — это сама картинка Tk."""
        if self._dirty or self._image_item is None:
            self.rebuild()
        return self._photo
