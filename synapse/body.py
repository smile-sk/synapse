"""Тело Synapse — коннектом, а не картинка.

Файл вида data/body/worm.json — отсканированная нервная система (формат
connectome-graph/1: узлы с координатами, весом и нейромедиатором + связи).
Тело живёт само: импульс растекается по РЕАЛЬНЫМ связям и гаснет.
Модель для этого не нужна — это слой рефлексов (бесплатный).

Роли нейронов берутся из ТОЧНОЙ карты data/body/neuron_roles.json (её собирает
tools/build_roles.py из выгрузки OpenWorm — см. synapse/roles.py). Если карты
нет, работает осторожная эвристика по именам, а всё неопознанное честно
остаётся "unknown" — мы не выдумываем биологию на ходу.
"""
from __future__ import annotations

import json
import random
import threading
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from synapse import config, i18n, pharmacology as pharm, roles as roles_mod

# Корни имён (без хвостовой L/R). Списки намеренно короткие и осторожные:
# это ЛИШЬ ЗАПАСНОЙ путь, когда точной карты на диске нет.
MOTOR_RADICALS = {
    "VA", "VB", "VC", "VD", "DA", "DB", "DD", "AS", "MD", "MV",
    "SMD", "SMB", "RMD", "RME", "RMF", "RMH", "RIV", "RIM",
    "URA", "URB", "MC", "MI", "M1", "M2", "M3", "M4", "M5",
}
SENSORY_RADICALS = {
    "ADL", "ADR", "ADF", "ADE", "AFD", "ASE", "ASG", "ASH", "ASI", "ASJ", "ASK",
    "AWA", "AWB", "AWC", "ALM", "AVM", "PLM", "PVM", "PVD", "FLP",
    "IL1", "IL2", "OLQ", "OLL", "CEP", "BAG", "URX", "AQR", "PQR",
    "PHB", "PHC", "PDE", "PVD", "ALA",
}
INTER_RADICALS = {
    "AVA", "AVB", "AVD", "AVE", "AVF", "AVG", "AVH", "AVJ", "AVK", "AVL",
    "PVC", "PVN", "PVP", "PVQ", "PVR", "PVT", "PVW", "PVX", "PVY",
    "RIA", "RIB", "RIC", "RID", "RIF", "RIG", "RIH", "RIP", "RIR", "RIS",
    "DVA", "DVB", "DVC", "LUA", "SDQ", "SIA", "SIB",
}


def radical(name: str) -> str:
    n = name.upper()
    if len(n) > 1 and n[-1] in "LR" and n[:-1] in (MOTOR_RADICALS | SENSORY_RADICALS | INTER_RADICALS):
        return n[:-1]
    return n


def role_of(name: str, overrides: dict) -> str:
    """Роль из карты (строка или запись словарём), иначе — эвристика по имени."""
    rec = overrides.get(name)
    if isinstance(rec, dict) and rec.get("role"):
        return str(rec["role"])
    if isinstance(rec, str):
        return rec
    r = radical(name)
    if r in MOTOR_RADICALS:
        return "motor"
    if r in SENSORY_RADICALS:
        return "sensory"
    if r in INTER_RADICALS:
        return "inter"
    return "unknown"


@dataclass
class Body:
    path: Path
    ids: list[str] = field(default_factory=list)
    xyz: list[tuple[float, float, float]] = field(default_factory=list)
    weight: list[float] = field(default_factory=list)
    nt: list[str] = field(default_factory=list)
    # ФИЗИОЛОГИЯ ПЕРЕДАЧИ (слово Стаса 25.09.2026: «делай всё по настоящему»).
    # До этого медиатор влиял ТОЛЬКО на краску: волна шла по весам связей, и
    # тормозной нейрон вёл себя как возбуждающий. Теперь знак связи — свойство
    # ПРЕСИНАПТИЧЕСКОГО нейрона и берётся из данных (его медиатора):
    #   sign      +1 возбуждает, −1 тормозит (GABA), 0 не проводит быстрый ток
    #   kind      exc | inh | mod — тип передачи (для легенды и карточки)
    #   tone      накопленный тонус модуляторов (моноамины не проводят ток)
    sign: list[float] = field(default_factory=list)
    kind: list[str] = field(default_factory=list)
    tone_sign: list[float] = field(default_factory=list)
    tone: list[float] = field(default_factory=list)
    edges: list[tuple[int, int, float]] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    info: list[dict] = field(default_factory=list)      # точная карта на каждый узел
    activity: list[float] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    # исходящие связи: для узла — [(j, доля веса)] (доля, чтобы импульс делился)
    _out: list[list[tuple[int, float]]] = field(default_factory=list, repr=False)
    _mod_out: list[list[tuple[int, float]]] = field(default_factory=list, repr=False)
    _out_all: list[list[tuple[int, float]]] = field(default_factory=list, repr=False)
    _phys_sum: dict | None = field(default=None, repr=False)
    _index: dict = field(default_factory=dict, repr=False)
    _roles_sum: dict | None = field(default=None, repr=False)   # сводки считаем один раз
    _zones_sum: dict | None = field(default=None, repr=False)
    # ЖИВОСТЬ ЗА ПРОМЕЖУТОК (v0.32.0): сколько импульсов прошло и до скольких нейронов
    # тело зажигалось с прошлого вопроса. Живой вечер 30.09.2026 показал, зачем: такт
    # раз в 30 секунд ловил МГНОВЕНИЕ между импульсами, в промпт уходило «зажжено
    # 0/302 нейронов, энергия 0.0000», и модель шестнадцать раз подряд писала «энергия
    # на исходе, ползу искать еду». Снимок мгновения — правда о теле, но не о жизни.
    _live_n: int = field(default=0, repr=False)
    _live_peak: float = field(default=0.0, repr=False)
    _live_at: float = field(default_factory=time.time, repr=False)
    # ЗАМОК ТЕЛА (находка QA Ореста 25.09.2026): список activity мутировали ДВА
    # потока — поток окна (дыхание каждый кадр, импульс по кнопке) и фоновый такт
    # (миссия: stimulus + step + реакция тела). GIL не даёт порчи памяти, но
    # допускает ПОТЕРЯННЫЕ приращения: step() читает activity, считает и заменяет
    # список целиком (последний записавший побеждает), а poke() делает
    # неатомарное «прочитал-прибавил-записал». RLock, а не Lock: stimulus() зовёт
    # poke() внутри, то есть замок берётся повторно.
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ---------- сборка ----------
    @classmethod
    def load(cls, path: str | Path | None = None, roles_file: str | Path | None = None) -> "Body":
        cfg = config.load()
        rel = path or cfg["body"]["file"]
        p = Path(rel)
        if not p.is_absolute():
            p = config.ROOT / p
        raw = json.loads(p.read_text(encoding="utf-8"))
        nodes, edges = raw.get("nodes", []), raw.get("edges", [])
        b = cls(path=p, meta=raw.get("meta", {}))
        for n in nodes:
            b.ids.append(str(n.get("id", "?")))
            b.xyz.append((float(n.get("x", 0.0)), float(n.get("y", 0.0)), float(n.get("z", 0.0))))
            b.weight.append(float(n.get("w", 0.0)))
            b.nt.append(str(n.get("nt", "")))
        for e in edges:
            b.edges.append((int(e["a"]), int(e["b"]), float(e.get("w", 1.0))))

        of = roles_file or cfg["body"].get("roles_file")
        overrides: dict = {}
        if of:
            op = Path(of)
            if not op.is_absolute():
                op = config.ROOT / op
            overrides = roles_mod.load(op)
        # Физиология: тип передачи и знак для КАЖДОГО узла — из его медиатора.
        ntcfg = (cfg.get("body") or {}).get("nt") or {}
        glu_inh = bool(ntcfg.get("glutamate_inhibitory", False))
        b.kind = [pharm.kind(x, glutamate_inhibitory=glu_inh) for x in b.nt]
        b.sign = [pharm.sign(x, glutamate_inhibitory=glu_inh) for x in b.nt]
        b.tone_sign = [pharm.tone_sign(x) for x in b.nt]
        b.tone = [0.0] * len(b.ids)
        b._index = {name: i for i, name in enumerate(b.ids)}
        b.roles = [role_of(i, overrides) for i in b.ids]
        b.info = [dict(overrides.get(i, {})) for i in b.ids]
        b.activity = [0.0] * len(b.ids)
        b._build_out()
        return b

    def _build_out(self) -> None:
        """Разложить связи по путям передачи.

        БЫСТРЫЙ путь (_out) — возбуждение и торможение: импульс идёт по связи со
        знаком пресинаптического нейрона. Путь ТОНУСА (_mod_out) — моноамины: они
        не проводят быстрый ток, а «подкручивают» возбудимость своих целей на
        секунды (объёмная передача). _out_all — те же связи БЕЗ разделения, для
        контрольного режима body.nt.enabled=false: он обязан воспроизводить
        прежний закон тела, иначе «стало по-другому» не с чем сравнивать.
        """
        n = len(self.ids)
        fast: list[list[tuple[int, float]]] = [[] for _ in range(n)]
        mod: list[list[tuple[int, float]]] = [[] for _ in range(n)]
        every: list[list[tuple[int, float]]] = [[] for _ in range(n)]
        for a, c, w in self.edges:
            if not (0 <= a < n and 0 <= c < n):
                continue
            share = max(0.0, w)
            every[a].append((c, share))
            bucket = mod if (a < len(self.kind) and self.kind[a] == pharm.MOD) else fast
            bucket[a].append((c, share))

        def norm(buckets: list[list[tuple[int, float]]]) -> list[list[tuple[int, float]]]:
            for i, lst in enumerate(buckets):
                total = sum(w for _, w in lst) or 1.0
                buckets[i] = [(j, w / total) for j, w in lst]
            return buckets

        self._out = norm(fast)
        self._mod_out = norm(mod)
        self._out_all = norm(every)

    # ---------- жизнь тела ----------
    @property
    def size(self) -> int:
        return len(self.ids)

    def poke(self, indices, amp: float = 1.0) -> list[int]:
        touched = []
        with self.lock:                       # приращение целиком, а не «прочитал-прибавил»
            for i in indices:
                if 0 <= i < self.size:
                    self.activity[i] = min(1.0, self.activity[i] + amp)
                    touched.append(i)
            self._live_n += len(touched)      # живость за промежуток (v0.32.0)
        return touched

    def poke_names(self, names, amp: float = 1.0) -> list[int]:
        want = {n.upper() for n in names}
        idx = [i for i, n in enumerate(self.ids) if n.upper() in want]
        return self.poke(idx, amp)

    def soothe(self, factor: float = 0.5) -> None:
        """Успокоиться: погасить долю возбуждения (рефлекс самосохранения).

        Зовёт инстинкт при перегрузе (agent.instinct): когда зажжено слишком много
        нейронов и тело не гаснет само, существо приглушает себя, чтобы не уйти в
        припадок. Множит активность, а не обнуляет — это «выдох», а не смерть.
        """
        with self.lock:
            self.activity = [a * factor for a in self.activity]

    def liveliness(self, reset: bool = False) -> dict:
        """Живость ЗА ПРОМЕЖУТОК, а не мгновенный снимок (v0.32.0).

        Найдено живым журналом 30.09.2026 (22:11-22:19): существо дышит — окно гонит
        вдох каждые 90 кадров, инстинкт бьёт импульсом каждые 20-30 секунд, — но
        энергия() считается как среднее МГНОВЕННОЙ активности, а импульс гаснет за
        секунду. Такт раз в 30 секунд попадал ровно в тишину, и в промпт уходило
        «зажжено 0/302 нейрона, энергия 0.0000; громче всех тихо». Модель читала это
        как «я умираю». Здесь то, о чём и надо спрашивать тело: сколько импульсов
        прошло и до скольких нейронов оно зажигалось.

        `reset=True` обнуляет счётчик — зовёт тот, кому нужен отрезок между тактами.
        """
        with self.lock:
            out = {"impulses": int(self._live_n),
                   "peak": round(float(self._live_peak), 4),
                   "size": self.size,
                   "seconds": round(max(0.0, time.time() - self._live_at), 1)}
            if reset:
                self._live_n, self._live_peak, self._live_at = 0, 0.0, time.time()
            return out

    def pool(self, role: str) -> list[int]:
        """Индексы узлов роли. Полимодальные (IL1/M3/MC/NSM/URA/VB) отзываются
        и на стимул, и как «руки» — поэтому входят в оба набора."""
        want = {role}
        if role == "sensory":
            want = {"sensory"} | {"polymodal"}
        elif role == "motor":
            want = {"motor"} | {"polymodal"}
        return [i for i, r in enumerate(self.roles) if r in want]

    def pick(self, role: str, k: int = 3, seed: int | None = None) -> list[int]:
        pool = self.pool(role) or list(range(self.size))
        rnd = random.Random(seed) if seed is not None else random.Random()
        return rnd.sample(pool, min(k, len(pool)))

    def step(self, decay: float | None = None, gain: float | None = None,
             threshold: float | None = None, sustain: float | None = None) -> float:
        """Один такт жизни. Возвращает энергию (среднюю активность).

        ПЕРЕДАЧА ИДЁТ СО ЗНАКОМ (25.09.2026): тормозной нейрон (GABA — 26 узлов:
        DD/VD, RME, AVL/DVB, RIS) ВЫЧИТАЕТ из возбуждения своих целей, а
        моноамины (дофамин, серотонин, октопамин) не проводят быстрый ток вовсе,
        а подкручивают тонус — объёмная передача. Знак берётся из данных, а не из
        краски: synapse/pharmacology.py. Режим body.nt.enabled=false возвращает
        прежний закон тела — он и есть контроль для сравнения.

        eff — коэффициент СОХРАНЕНИЯ (act <- eff*act), а не «скорость затухания».
        Отсюда честный вывод замеров 25.09.2026, который я нашла при разборе
        QA-отчёта Ореста: параметр `sustain` сохранение УВЕЛИЧИВАЕТ, то есть
        САМОПОДДЕРЖКА (не тормоз) — при sustain 4 и восьми
        стимулированных нейронах тело уходит в припадок (пик 219, не гаснет),
        тогда как при sustain 0 те же восемь гаснут за 43 такта. Настоящая
        страховка — decay и порог. Переименовано в sustain по слову Стаса 25.09.2026.
        """
        with self.lock:                       # шаг целиком: читать, считать и заменить — нераздельно
            bcfg = config.cached()["body"]    # с памятью по mtime: такт идёт 20 раз в секунду
            decay = float(bcfg["decay"] if decay is None else decay)
            gain = float(bcfg["gain"] if gain is None else gain)
            thr = float(bcfg["threshold"] if threshold is None else threshold)
            sustain = float(bcfg.get("sustain", 1.2) if sustain is None else sustain)
            ntcfg = bcfg.get("nt") or {}
            live = bool(ntcfg.get("enabled", True))          # знак синапса работает?
            mod_on = live and bool(ntcfg.get("modulation", True))
            inh_gain = float(ntcfg.get("inhibitory_gain", 1.0) or 1.0)
            tone_amp = float(ntcfg.get("tone_strength", 0.25) or 0.0)
            tone_decay = float(ntcfg.get("tone_decay", 0.86) or 0.0)
            tone_cap = float(ntcfg.get("tone_cap", 0.6) or 0.6)
            src = self.activity
            eff = min(0.95, decay + sustain * self.energy())
            # ТОНУС МЕНЯЕТ ВОЗБУДИМОСТЬ, А НЕ ПОДАЁТ ТОК — и это не придирка, а
            # замер (25.09.2026). Первая версия тонус ДОБАВЛЯЛА, и тело залипало:
            # серотониновые NSM держали свою петлю вечно (замер: k=3 не гас НИКОГДА,
            # 12 зажжённых на 120-м такте — I4, M3L/R, M4, NSML/R). Моноамины
            # работают через метаботропные рецепторы: сами канал не открывают, а
            # меняют возбудимость цели. Отсюда и структурная гарантия: коэффициент
            # сохранения остаётся < 0.95, поэтому тонус НЕ МОЖЕТ держать тело живым
            # без импульса — но может заставить уже возбуждённый контур жить дольше.
            if mod_on and any(self.tone):
                nxt = [min(0.95, eff * (1.0 + tone_i)) * v
                       for v, tone_i in zip(src, self.tone)]
            else:
                nxt = [v * eff for v in src]
            if mod_on:
                next_tone = [t * tone_decay for t in self.tone]
            else:
                next_tone = None
                if any(self.tone):
                    self.tone = [0.0] * self.size
            out_tables = self._out if live else self._out_all
            for i, v in enumerate(src):
                if v <= thr:
                    continue
                if live and i < len(self.kind) and self.kind[i] == pharm.MOD:
                    # Моноамин: не ток по связи, а тонус её целям (знак — какой).
                    if mod_on and tone_amp and next_tone is not None:
                        s = self.tone_sign[i] if i < len(self.tone_sign) else 0.0
                        add = tone_amp * v * s
                        if add:
                            for j, share in self._mod_out[i]:
                                nv = next_tone[j] + add * share
                                next_tone[j] = max(-tone_cap, min(tone_cap, nv))
                    continue
                out = out_tables[i]
                if not out:
                    continue
                add = gain * v
                sgn = self.sign[i] if (live and i < len(self.sign)) else 1.0
                if sgn >= 0:
                    for j, share in out:
                        nxt[j] += add * share
                else:
                    # ТОРМОЖЕНИЕ: гиперполяризация цели — вычитание, а не слабое
                    # возбуждение. Ниже нуля активность не уходит (clamp в конце).
                    add *= inh_gain
                    for j, share in out:
                        nxt[j] -= add * share
            if next_tone is not None:
                self.tone = next_tone
            self.activity = [v if v > thr else 0.0 for v in (min(1.0, max(0.0, w)) for w in nxt)]
            peak = max(self.activity) if self.activity else 0.0
            if peak > self._live_peak:
                self._live_peak = peak         # пик за промежуток, а не снимок (v0.32.0)
            return self.energy()

    def energy(self) -> float:
        with self.lock:
            return sum(self.activity) / max(1, self.size)

    def active(self, role: str | None = None, top: int = 5, thr: float = 0.02) -> list[tuple[str, float]]:
        with self.lock:
            act = list(self.activity)      # снимок под замком: тело живёт в двух потоках
        rows = []
        for i, a in enumerate(act):
            if a <= thr:
                continue
            if role is not None and not self.matches(i, role):
                continue
            rows.append((i, a))
        rows.sort(key=lambda t: -t[1])
        return [(self.ids[i], round(a, 3)) for i, a in rows[:top]]

    def matches(self, i: int, role: str) -> bool:
        r = self.roles[i]
        if role in ("sensory", "motor"):
            return r == role or r == "polymodal"
        return r == role

    def lit(self, thr: float = 0.02) -> int:
        with self.lock:
            return sum(1 for a in self.activity if a > thr)

    def describe(self) -> str:
        a = self.active(top=4)
        # Тело говорит на языке интерфейса: это его речь, а не идентификатор.
        names = ", ".join(f"{n}({v})" for n, v in a) if a else i18n.t("body.quiet")
        return i18n.t("body.describe", lit=self.lit(), size=self.size,
                      energy=f"{self.energy():.4f}", names=names)

    # ---------- что тело знает о себе (точная карта) ----------
    def neuron(self, name: str) -> dict:
        """Всё, что известно об узле: роль, медиатор, связи, зона, активность."""
        i = self._index.get(name.upper())
        if i is None:
            i = self._index.get(name)
        if i is None:
            return {}
        rec = dict(self.info[i]) if i < len(self.info) else {}
        rec.update({"name": self.ids[i], "index": i, "role": self.roles[i],
                    "nt": rec.get("nt") or self.nt[i],
                    # Тип передачи и знак — самое главное в карточке: узел не
                    # только «чем пахнет», но и «что делает» с соседями.
                    "kind": self.kind[i] if i < len(self.kind) else pharm.EXC,
                    "sign": self.sign[i] if i < len(self.sign) else 1.0,
                    "activity": round(self.activity[i], 4),
                    "xyz": [round(v, 4) for v in self.xyz[i]], "w": self.weight[i]})
        return rec

    def neighbours(self, i, limit: int = 6) -> list[str]:
        """Самые сильные соседи узла — по связям в ОБЕ стороны.

        Нужен карточке узла. Прежняя карточка брала поле "partners", которого у узла
        НЕТ вовсе, поэтому соседей не показывала никогда (найдено 26.09.2026).
        """
        if i is None:
            return []
        try:
            i = int(i)
        except (TypeError, ValueError):
            return []
        if not (0 <= i < self.size):
            return []
        w: dict[int, float] = {}
        for a, b, weight in self.edges:
            if a == i and b != i:
                w[b] = max(w.get(b, 0.0), float(weight))
            elif b == i and a != i:
                w[a] = max(w.get(a, 0.0), float(weight))
        top = sorted(w.items(), key=lambda t: -t[1])[:max(1, int(limit))]
        return [self.ids[j] for j, _w in top]

    def hubs(self, top: int = 10) -> list[tuple[str, int]]:
        rows = sorted(range(self.size), key=lambda i: -(self.info[i].get("deg", 0) if i < len(self.info) else 0))
        return [(self.ids[i], (self.info[i].get("deg", 0) if i < len(self.info) else 0)) for i in rows[:top]]

    # ---------- рефлекс: внешнее -> тело, тело -> решение ----------
    def stimulus(self, text: str, amp: float = 1.0, k: int = 3) -> list[int]:
        """Стимул бьёт по сенсорным нейронам. Отображение детерминированное:
        один и тот же текст всегда задевает один и тот же участок тела —
        иначе нельзя ни проверить, ни привыкнуть."""
        seed = zlib.crc32(text.encode("utf-8"))
        return self.poke(self.pick("sensory", k=k, seed=seed), amp)

    def intent(self) -> dict:
        """Что тело хочет сделать — по возбуждению МОТОНЕЙРОНОВ."""
        m = self.active("motor", top=3)
        s = self.active("sensory", top=3)
        e = self.energy()
        if e < 0.001 and not m:
            return {"action": "idle", "why": i18n.t("body.intent.idle"), "motor": [], "sensory": s}
        if m:
            return {"action": "act", "why": i18n.t("body.intent.act"), "motor": m, "sensory": s}
        return {"action": "listen", "why": i18n.t("body.intent.listen"),
                "motor": [], "sensory": s}

    def physiology(self) -> dict:
        """Сколько в теле передач каждого типа: exc / inh / mod.

        Это ответ на вопрос «на что влияют названия медиаторов»: считаем по ЖИВОЙ
        карте (с учётом glutamate_inhibitory), а не по таблице модуля. Считается
        один раз — карта передач за жизнь тела не меняется.
        """
        if self._phys_sum is None:
            out = {pharm.EXC: 0, pharm.INH: 0, pharm.MOD: 0}
            for k in self.kind:
                out[k] = out.get(k, 0) + 1
            self._phys_sum = out
        return dict(self._phys_sum)

    def roles_summary(self) -> dict:
        """Кто есть кто. Считается один раз: карта ролей за жизнь тела не меняется."""
        if self._roles_sum is None:
            out: dict[str, int] = {}
            for r in self.roles:
                out[r] = out.get(r, 0) + 1
            self._roles_sum = out
        return dict(self._roles_sum)

    def zone_summary(self) -> dict:
        if self._zones_sum is None:
            out: dict[str, int] = {}
            for r in self.info:
                z = r.get("zone")
                if z:
                    out[z] = out.get(z, 0) + 1
            self._zones_sum = out
        return dict(self._zones_sum)

    def base_radius(self, i: int, base: float = 2.2) -> float:
        """Радиус «спокойного» узла: без активности — его можно запечь в статический слой."""
        r = base
        if i < len(self.info) and self.info[i].get("hub"):
            r += 1.6
        return r
