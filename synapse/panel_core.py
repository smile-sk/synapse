"""Ядро панели Synapse — без tkinter, чтобы это можно было проверить тестом.

Здесь вся «математика вида» и все действия, которые панель дёргает кнопками:
проекция коннектома на экран, цвета ролей и медиаторов, очередь миссий, отчёты,
журнал, включение мозга. Окно (synapse/panel.py) — только рисовалка и кнопки.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from synapse import (audit, cancel, config, creds, i18n, missions, online,
                     pharmacology as pharm,
                     reports, roles as roles_mod, scope, theme, world)
from synapse.agent import Synapse
from synapse.brain import Brain
from synapse.tools import net_scan

# Инструменты оператора: какие есть, что им нужно и как они называются.
# ПОДПИСЬ И ПОДСКАЗКА — из словаря (synapse/i18n.py), а не строкой в коде: язык
# интерфейса переключается целиком, иначе и получался «хаус с языком».
#: Порядок в списке панели — для глаз оператора; СОСТАВ берём из missions.KINDS,
#: поэтому новый инструмент появится в панели сам (забыть его здесь нельзя).
_DISPLAY_ORDER = ("net_audit", "peers", "ask", "remote", "stimulus", "note", "write_probe",
                   "certs", "diff")
MISSION_KIND_IDS = (tuple(k for k in _DISPLAY_ORDER if k in missions.KINDS) +
                    tuple(k for k in missions.KINDS if k not in _DISPLAY_ORDER))
MISSION_NEEDS = {"net_audit": "cidr", "ask": "text", "stimulus": "text", "note": "text",
                 "write_probe": "host", "certs": "host", "diff": "none", "remote": "host",
                 "peers": "none"}


def mission_kind_list() -> list[dict]:
    """Инструменты, доступные оператору: подпись, подсказка и что нужно заполнить."""
    return [{"kind": k, "label": i18n.t(f"tool.{k}.label"), "needs": MISSION_NEEDS[k],
             "hint": i18n.t(f"tool.{k}.hint")} for k in MISSION_KIND_IDS]


#: Прежнее имя списка (совместимость со старыми вызовами).
MISSION_KINDS = mission_kind_list()

# СТАТУС — идентификатор из файла очереди, а не подпись: queued/running/done/
# failed/refused, и обе панели показывают ровно его. Раньше Qt отдавал слово как
# есть, а tkinter подменял (failed -> error, refused -> cancelled): две панели
# говорили о состоянии миссии РАЗНЫМИ словами (находка QA Ореста 25.09.2026).
# Оператор должен видеть то же слово, что лежит в data/missions/queue.json.
STATUSES = missions.STATUSES

#: Инструменты, у которых есть ОТЧЁТ на диске: их итог собирается заново из JSON
#: на текущем языке (как .txt у отчёта), а не берётся строкой из очереди.
REPORT_KINDS = ("net_audit", "write_probe", "certs", "diff", "remote", "peers")


def _short_time(iso) -> str:
    """'2026-09-24T16:26:52' -> '24.09 16:26' (или что было, если формат чужой)."""
    text = str(iso or "")
    if len(text) >= 16 and text[4] == "-" and text[13] == ":":
        return f"{text[8:10]}.{text[5:7]} {text[11:16]}"
    return text[:16]


# Цвета тела приходят из ТЕМЫ (synapse/theme.py): тёмная и светлая настраивают
# и фон полотна, и цвет связей, и цвета ролей. Прежние имена (BG, EDGE_DIM, HOT,
# ROLE_COLORS) отдаются НИЖЕ, через __getattr__: они всегда берут значение
# ТЕКУЩЕЙ темы, поэтому старые вызовы не падают и не «застывают» на тёмном.


def __getattr__(name: str):
    """BG / EDGE_DIM / HOT / ROLE_COLORS живыми значениями текущей темы."""
    if name == "BG":
        return theme.bg()
    if name == "EDGE_DIM":
        return theme.edge_dim()
    if name == "HOT":
        return theme.hot()
    if name == "ROLE_COLORS":
        return theme.role_colors()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def hex_rgb(c: str) -> tuple[int, int, int]:
    """'#rrggbb' -> (r, g, b) для Pillow-пути отрисовки."""
    return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))


def mix(c1: str, c2: str, t: float) -> str:
    """Смешать два цвета '#rrggbb' (t=0 -> c1, t=1 -> c2)."""
    t = 0.0 if t < 0 else (1.0 if t > 1 else float(t))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def heat(base: str, activity: float) -> str:
    """Цвет узла: спокойный — свой, разогретый — к белому."""
    return mix(base, "#ffffff", min(1.0, activity * 1.4) ** 0.6)


class View3D:
    """Проекция коннектома: длинная ось тела — по экрану, вокруг неё можно крутить."""

    def __init__(self, xyz: list[tuple[float, float, float]]) -> None:
        self.xyz = list(xyz)
        if self.xyz:
            cy = sum(p[1] for p in self.xyz) / len(self.xyz)
            cx = sum(p[0] for p in self.xyz) / len(self.xyz)
            cz = sum(p[2] for p in self.xyz) / len(self.xyz)
            self.center = (cx, cy, cz)
            self.radius = max(0.05, max(math.dist(p, self.center) for p in self.xyz))
        else:
            self.center = (0.0, 0.0, 0.0)
            self.radius = 1.0

    def unit(self, p) -> tuple[float, float, float]:
        """В систему координат тела: вдоль оси — в [-1,1], сечение — в тех же долях."""
        return ((p[1] - self.center[1]) / self.radius,
                (p[0] - self.center[0]) / self.radius,
                (p[2] - self.center[2]) / self.radius)

    def project(self, yaw: float, pitch: float, zoom: float,
                w: int, h: int) -> list[tuple[float, float, float]]:
        """Вернуть [(экран_x, экран_y, глубина)] на каждую точку."""
        cy_, sy_ = math.cos(yaw), math.sin(yaw)
        cp, sp = math.cos(pitch), math.sin(pitch)
        # масштаб: вдоль оси вписываем в ширину, поперёк — в высоту (не растягиваем)
        s = min(w * 0.45, h * 1.1) * max(0.2, min(3.0, zoom))
        cxp, cyp = w / 2.0, h / 2.0
        out = []
        for p in self.xyz:
            bx, by, bz = self.unit(p)
            x1 = bx * cy_ + bz * sy_
            z1 = -bx * sy_ + bz * cy_
            y1 = by
            y2 = y1 * cp - z1 * sp
            z2 = y1 * sp + z1 * cp
            out.append((cxp + x1 * s, cyp - y2 * s, z2))
        return out


class PanelCore:
    """Всё, что делает панель, кроме рисования."""

    def __init__(self, syn: Synapse | None = None) -> None:
        self.cfg = config.load()
        i18n.sync_from_config(self.cfg)      # язык интерфейса — из настроек
        theme.sync_from_config(self.cfg)     # и тема тоже
        self.syn = syn or Synapse(self.cfg)
        self.brain: Brain = self.syn.brain
        self.body = self.syn.body
        self.view = View3D(self.body.xyz)
        self.yaw = 0.0
        self.pitch = 0.18
        self.zoom = 1.0
        self.coloring = "role"          # role | nt | zone
        self.frame = 0
        # ВСПЫШКА ИМПУЛЬСА — замер 25.09.2026. Импульс впрыскивался, но его было
        # НЕ ВИДНО: вдох тела каждые 10 кадров давал постоянный шум (в среднем 18
        # зажжено), а импульс поднимал всего +10 нейронов и гас за 0.16 с. Поэтому
        # отмеченные импульсом узлы держат белый цвет и крупный размер, пока идёт
        # вспышка: нажатие кнопки становится видимым, а не «ничего не произошло».
        self._flash_idx: set[int] = set()
        self._flash_until = 0.0
        self.last_tick: dict = {}
        # сколько связей вообще показываем: 3004 линии = 200 мс на кадр (замер)
        self.edge_budget = 900
        self.hot_pool = 120
        self.edge_keep = self._pick_edges(self.edge_budget)
        self._reports_cache: tuple | None = None     # список отчётов, пока папка не менялась

    def _pick_edges(self, budget: int) -> list[int]:
        self.edge_budget = max(100, int(budget))
        return sorted(range(len(self.body.edges)), key=lambda k: -self.body.edges[k][2])[:self.edge_budget]

    # ---------- отпечатки: что панели показывать, а что не трогать ----------
    @staticmethod
    def _file_key(path) -> tuple:
        """Отпечаток файла (mtime, размер). Нет файла — (0, 0), и это честно."""
        try:
            st = Path(path).stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return (0, 0)

    @staticmethod
    def _dir_key(folder) -> tuple:
        """Отпечаток папки с отчётами: время правки плюс сколько в ней .json."""
        folder = Path(folder)
        try:
            st = folder.stat()
            return (st.st_mtime_ns, len(list(folder.glob("*.json"))))
        except OSError:
            return (0, 0)

    def fingerprints(self) -> dict:
        """Отпечатки всего, что показывает окно.

        Замер 25.09.2026 по жалобе «впечатление, что ничего не изменилось — такие же
        тормоза»: прежний _refresh() каждую секунду пересобирал таблицу миссий
        (39 строк x 5 ячеек), заново вставлял журнал (7 КБ текста) и перечитывал
        25 отчётов — 15-23 мс в главном потоке окна И сброс выделения с прокруткой:
        выбрать миссию было нельзя, журнал прыгал в начало. Теперь у каждой части
        свой отпечаток: не менялось — вид не трогаем вовсе.
        """
        return {
            "missions": self._file_key(missions.QUEUE),
            "reports": self._dir_key(config.DATA / "reports"),
            "audit": self._file_key(audit.JSONL),
            "thoughts": self._file_key(audit.thoughts_log()),
            "state": self._file_key(config.DATA / "state.json"),
            "config": self._file_key(config.CONFIG_PATH),
        }

    # ---------- вид ----------
    def screen(self, w: int, h: int):
        return self.view.project(self.yaw, self.pitch, self.zoom, w, h)

    def calm_color(self, i: int) -> str:
        """Цвет узла БЕЗ нагрева — его запекают в статический слой кадра.

        Разделение важное: спокойные 302 узла рисуются один раз, а каждый кадр
        перерисовываются только зажжённые (замер: 8.5 мс -> 1.5 мс).
        """
        roles = theme.role_colors()
        base = roles.get(self.body.roles[i], roles.get("unknown", "#7f8c99"))
        if self.coloring == "nt":
            pal = (self.body.meta or {}).get("nt_palette") or {}
            nt = self.body.info[i].get("nt") if i < len(self.body.info) else ""
            # палитра медиаторов приходит ИЗ ФАЙЛА тела и настроена на тёмный фон:
            # на светлой теме theme.adapt() её приглушает, иначе цвета слепнут.
            base = theme.adapt(pal.get(nt, theme.nt_fallback()))
        elif self.coloring == "zone":
            zone = self.body.info[i].get("zone") if i < len(self.body.info) else ""
            base = theme.zone_colors().get(str(zone or ""), theme.nt_fallback())
        return base

    def node_color(self, i: int) -> str:
        if self.flashing(i):
            return heat(self.calm_color(i), 1.0)      # отмеченный импульсом — белый
        return heat(self.calm_color(i), self.body.activity[i])

    def node_radius(self, i: int, base: float = 3.0) -> float:
        a = self.body.activity[i]
        if self.flashing(i):
            a = 1.0                                   # и крупнее: видно, куда попал
        r = base + 3.2 * (a ** 0.5)
        if i < len(self.body.info) and self.body.info[i].get("hub"):
            r += 1.6
        return r

    def hot_color(self, v: float) -> tuple[int, int, int]:
        return hex_rgb(mix("#3a5a3a", theme.hot(), min(1.0, v * 2.2)))

    def glow_color(self, i: int, a: float) -> tuple[int, int, int]:
        r, g, b = hex_rgb(self.node_color(i))
        k = min(1.0, 0.35 + 0.65 * a)
        return (int(r * 0.55 * k), int(g * 0.55 * k), int(b * 0.55 * k))

    def hot_edges(self, top: int = 220) -> list[tuple[int, float]]:
        """Самые «горячие» связи — их и рисуем поверх тусклой сетки."""
        act = self.body.activity
        scored = []
        for k in self.edge_keep:
            a, b, _w = self.body.edges[k]
            v = act[a] if act[a] < act[b] else act[b]
            if v > 0.015:
                scored.append((k, v))
        scored.sort(key=lambda t: -t[1])
        return scored[:top]

    # ---------- жизнь ----------
    def breathe(self) -> None:
        """Дыхание тела: без него коннектом просто стоит (и это выглядит мёртвым).

        ЗАМЕР 25.09.2026 (жалоба «нажимаю на них и ничего не происходит»): вдох
        каждые 10 кадров — это 0.16 с при темпе 16 мс, то есть тело мерцало БЕЗ
        ПАУЗ (в среднем 18 зажжено из 302, до 31), и импульс оператора в этом шуме
        был неразличим. Теперь вдохи редкие и слабые (config: breathe_every,
        breathe_amp), у тела есть спокойные мгновения — и импульс видать.
        """
        self.frame += 1
        self.body.step()
        self._flash_prune()
        cfg = (config.cached().get("body") or {})
        every = max(1, int(cfg.get("breathe_every", 90) or 90))
        if self.frame % every == 0:
            amp = float(cfg.get("breathe_amp", 0.55) or 0.55)
            self.body.stimulus(f"breathe-{self.frame}", amp=amp, k=2)

    def tick(self, with_mission: bool = True) -> dict:
        out = self.syn.tick(with_mission=with_mission, step_body=False)
        self.last_tick = out
        self.cfg = config.load()          # настройки могли поменять в панели
        return out

    # ---------- вспышка импульса ----------
    def _flash_prune(self) -> None:
        """Погасить отметку импульса, когда её время вышло (зовётся каждый кадр)."""
        if self._flash_idx and time.monotonic() >= self._flash_until:
            self._flash_idx = set()

    def flashing(self, i: int) -> bool:
        """Узел сейчас отмечен импульсом оператора (держится flash_seconds)."""
        if not self._flash_idx or time.monotonic() >= self._flash_until:
            return False
        return i in self._flash_idx

    def mark_flash(self, idx) -> None:
        """Отметить узлы вспышкой. Отметку держим ПО ВРЕМЕНИ, а не по кадрам: темп
        кадра оператор меняет в настройках, а секунды остаются секундами."""
        if not idx:
            return
        secs = float((config.cached().get("body") or {}).get("flash_seconds", 1.6) or 1.6)
        self._flash_idx = {int(i) for i in idx}
        self._flash_until = time.monotonic() + max(0.2, secs)

    def flash_count(self) -> int:
        """Сколько узлов отмечено ПРЯМО СЕЙЧАС (для строки ответа в окне и теста)."""
        return len(self._flash_idx) if time.monotonic() < self._flash_until else 0

    def pulse(self, text: str, amp: float = 1.0) -> str:
        """Импульс оператора: зажечь сенсоры И ПОМЕТИТЬ их — чтобы было ВИДНО.

        Возвращает не «ок», а то, что тело о себе говорит: оператор нажал кнопку,
        и в окне должна появиться строка, а не тишина.
        """
        idx = self.body.stimulus(text or i18n.t("tk.pulse_default"), amp=amp, k=6)
        self.mark_flash(idx)
        audit.log("panel.pulse", neurons=len(idx), flash=self.flash_count())
        return self.body.describe()

    def coloring_name(self) -> str:
        """Слово для текущей раскраски — чтобы и в окне, и в логе было по-человечески."""
        return i18n.t(f"coloring.{self.coloring}") if self.coloring in ("role", "nt", "zone") \
            else str(self.coloring)

    def legend(self) -> list[dict]:
        """Что означает каждый цвет при ТЕКУЩЕЙ раскраске — легенда под телом.

        Слово Стаса 25.09.2026: «что за роли цвета? ничего не меняется». Цвета были
        ВЕРНЫЕ — замер: в буфер кадра уходят 5 цветов по ролям, 7 по медиаторам,
        3 по зонам, — но оператору никто не сказал, ЧТО они значат. Теперь легенда
        стоит под телом и меняется вместе с раскраской: переключение видно глазом.
        """
        rows: list[dict] = []
        if self.coloring == "nt":
            pal = (self.body.meta or {}).get("nt_palette") or {}
            counts = self._count_by(lambda rec: str(rec.get("nt") or ""))
            # Порядок берём из ДАННЫХ, а не из палитры файла тела: палитра знает и
            # такие медиаторы, которых в теле нет (Tyramine — 0 узлов), а в теле
            # есть узлы БЕЗ медиатора (59 штук, рисуются серым). Пока легенда шла по
            # палитре, эти 59 серых узлов оставались БЕЗ ОБЪЯСНЕНИЯ — поймал тест на
            # покрытие (сумма 243 вместо 302), а не глаз.
            keys = [k for k in sorted(pal, key=lambda t: (t != "", t)) if counts.get(k)]
            keys += sorted(k for k in counts if k and counts[k] and k not in pal)
            if counts.get(""):
                keys.append("")
            for nt in keys:
                label = nt or i18n.t("legend.unknown_nt")
                # ЗНАК ПЕРЕДАЧИ рядом с цветом (25.09.2026). Медиатор — это не
                # только краска: GABA тормозит цель, моноамины задают тонус. Если
                # написать это не там же, где цвет, оператор снова не поймёт.
                if nt:
                    label += " · " + i18n.t(pharm.kind_key(nt))
                rows.append({"color": theme.adapt(pal.get(nt) or theme.nt_fallback()),
                             "label": label,
                             "count": counts[nt]})
        elif self.coloring == "zone":
            counts = self._count_by(lambda rec: str(rec.get("zone") or ""))
            for zone, key in i18n.ZONE_KEYS.items():
                if not counts.get(zone):
                    continue
                rows.append({"color": theme.zone_colors().get(zone, theme.nt_fallback()),
                             "label": i18n.t(key), "count": counts.get(zone, 0)})
        else:
            roles = theme.role_colors()
            present = self.body.roles_summary()
            for role in ("sensory", "inter", "motor", "polymodal", "other", "unknown"):
                if not present.get(role):
                    continue
                rows.append({"color": roles.get(role, roles.get("unknown", "#7f8c99")),
                             "label": i18n.role_name(role), "count": present[role]})
        return rows

    def physiology_line(self) -> str:
        """Строка «что тело ДЕЛАЕТ со знаком» — рядом с легендой цветов.

        Ответ на вопрос Стаса 25.09.2026: «что это за названия, на что они
        влияют?» Названия — реальные нейроны и медиаторы C. elegans; влияют так:
        тормозные вычитают возбуждение цели, моноамины подкручивают её тонус,
        остальные передают импульс дальше. Когда физиология выключена — так и
        написано, а не тишина.
        """
        cfg = (config.cached().get("body") or {})
        live = bool((cfg.get("nt") or {}).get("enabled", True))
        p = self.body.physiology()
        if not live:
            return i18n.t("legend.physiology_off")
        return i18n.t("legend.physiology", exc=p[pharm.EXC], inh=p[pharm.INH], mod=p[pharm.MOD])

    def _count_by(self, key_of) -> dict:
        """Сколько узлов на каждое значение признака (для легенды)."""
        out: dict = {}
        for rec in (self.body.info or []):
            v = key_of(rec)
            out[v] = out.get(v, 0) + 1
        return out

    # ---------- миссии ----------
    def add_mission(self, kind: str, cidr: str = "", text: str = "", note: str = "",
                    host: str = "") -> dict:
        params: dict = {}
        if cidr:
            params["cidr"] = cidr
        if text:
            params["text"] = text
        if host:
            params["host"] = host
        rec = missions.add(kind, params, note=note)
        audit.log("mission.added", mission=rec["id"], kind=kind, params=params)
        return rec

    def queue(self) -> list[dict]:
        """Ждущие работы: миссии оператора и предложенное мозгом (M6).

        Предложенное стоит статусом `proposed` и само не исполняется — но оператор
        должен видеть его ТАМ ЖЕ, где очередь: иначе «что оно предлагает» не найти.
        """
        return missions.queued() + missions.proposed()

    def mission_kinds(self) -> list[dict]:
        """Инструменты, доступные оператору: подпись, подсказка и что нужно заполнить."""
        return mission_kind_list()

    def mission_kind(self, kind: str) -> dict:
        for k in mission_kind_list():
            if k["kind"] == kind:
                return dict(k)
        return {"kind": kind, "label": kind, "needs": "",
                "hint": i18n.t("plan.no_tool", kind=kind)}

    def mission_label(self, kind: str) -> str:
        return self.mission_kind(kind)["label"]

    def mission_result(self, m: dict) -> str:
        """Итог миссии на ТЕКУЩЕМ языке интерфейса.

        В очереди лежит строка, написанная в тот день и на том языке, на котором
        существо тогда говорило. Для миссий с отчётом собираем её заново из JSON —
        так же, как панель рисует заново человеческий .txt. Если отчёта нет
        (стимул телу, ошибка, старая запись) — показываем что записано: не выдумываем.
        """
        text = str(m.get("result") or "")
        if not text or str(m.get("kind") or "") not in REPORT_KINDS:
            return text
        rep = reports.find_for_mission(str(m.get("id") or ""))
        if not rep:
            return text
        try:
            line = reports.summary_line(rep)
        except Exception:                     # noqa: BLE001 — отчёт чужой формы не повод падать
            return text
        return line or text

    def plan_mission(self, kind: str, cidr: str = "", text: str = "") -> tuple[bool, str]:
        """Проверить миссию ДО постановки: поля и ДВЕРЬ.

        Смысл: оператор не должен узнать из журнала, что задание отклонили. Пусть
        панель скажет заранее и человеческими словами.
        """
        meta = self.mission_kind(kind)
        # Известность инструмента спрашиваем у ОЧЕРЕДИ (missions.KINDS), а не у себя:
        # прежняя проверка «meta["kind"] != kind» не отказывала НИКОГДА — mission_kind()
        # на незнакомое имя отдаёт словарь с тем же самым kind, и миссия-мусор проходила
        # план. Нашла 25.09.2026, когда закрывала дверь add() и проверяла, чем панель
        # отличается от консоли.
        if kind not in missions.KINDS:
            return False, i18n.t("plan.no_tool", kind=kind)
        if meta.get("needs") == "cidr":
            cidr = (cidr or "").strip()
            if not cidr:
                return False, i18n.t("plan.subnet_required")
            ok, code, params = scope.check_cidr_code(cidr, self.cfg)
            why = scope.say(code, **params)     # причина — словами интерфейса
            if not ok:
                return False, i18n.t("plan.door_no", why=why)
            return True, why
        if meta.get("needs") in ("host", "none"):
            # Эти инструменты умеют работать по ПОСЛЕДНЕМУ аудиту: хост не обязателен.
            if meta["needs"] == "host" and not (text or "").strip():
                return True, i18n.t("plan.host_optional")
            return True, i18n.t("plan.ready")
        if not (text or "").strip():
            return False, i18n.t("plan.text_required")
        return True, i18n.t("plan.ready")

    def mission_rows(self, limit: int = 30) -> list[dict]:
        """Очередь и история одной таблицей, человеческими словами.

        Нумерация («seq») идёт по ПОРЯДКУ РОЖДЕНИЯ, а не по месту в таблице: самая
        новая миссия получает САМОЕ БОЛЬШОЕ число. Слово Стаса 25.09.2026: «по
        времени у тебя вверху последнее событие, но 1 — не логично». Раньше
        вертикальный заголовок таблицы нумеровал строки сверху вниз (1, 2, 3…), и
        свежая миссия сверху выглядела как «№1». Теперь номер растёт со временем:
        верхняя (самая свежая) строка несёт высший номер.
        """
        all_m = missions.all_missions()
        seq = {str(m.get("id")): i + 1 for i, m in enumerate(all_m)}
        rows = []
        for m in self.queue():
            rows.append({"id": m["id"], "seq": seq.get(m["id"], 0), "kind": m["kind"],
                         "what": self.mission_label(m["kind"]),
                         "status": m.get("status", ""),        # команда как она есть в файле
                         "created": _short_time(m.get("created")),
                         "result": self.mission_result(m)[:400],
                         "queued": True})
        for m in self.history(limit):
            rows.append({"id": m["id"], "seq": seq.get(m["id"], 0), "kind": m["kind"],
                         "what": self.mission_label(m["kind"]),
                         "status": m.get("status", ""),        # команда как она есть в файле
                         "created": _short_time(m.get("finished") or m.get("created")),
                         "result": self.mission_result(m)[:400],
                         "queued": False})
        return rows

    def history(self, limit: int = 40) -> list[dict]:
        rows = [m for m in missions.all_missions() if m.get("status") != "queued"]
        return rows[-limit:][::-1]

    def approve_mission(self, mid: str) -> dict:
        """Подтвердить предложенную мозгом работу (M6).

        Слово Стаса 30.09.2026: «и как я это кстати апрувить? кнопка какая то что ли».
        Да — кнопка «Подтвердить» в таблице миссий. Подтверждение переводит запись из
        `proposed` в `queued`: дальше её берёт обычный такт, как любую миссию. Никаких
        новых прав у мозга не появляется — он лишь предложил, а решил оператор.
        """
        rec = missions.approve(mid)
        if rec is None:
            current = missions.status_of(mid)
            why = (i18n.t("tk.approve_missing", id=mid) if current is None else
                   i18n.t("tk.approve_not_proposed", id=mid, status=current))
            return {"ok": False, "why": why}
        audit.log("mission.approved", mission=mid, kind=rec.get("kind"),
                  params=rec.get("params") or {})
        return {"ok": True, "why": i18n.t("tk.approved", id=mid, kind=rec.get("kind"))}

    def drop_mission(self, mid: str) -> dict:
        """Удалить миссию — и, если она РАБОТАЕТ, сначала остановить работу.

        Слово Стаса 26.09.2026: «останавливать и удалять нужно — если на кнопке
        написано УДАЛИТЬ!» и «оно просто пишет running уже 5 минут».

        Что было не так: у идущей миссии ставился статус refused, а САМА РАБОТА
        продолжалась — аудит сети с полным диапазоном портов молотил ещё минуты,
        потому что остановить функцию внутри такта было нечем. Находка QA Ореста
        (25.09) закрыла только ложь в статусе, а не работу.

        Теперь два честных шага:
          * работа получает сигнал остановки (synapse/cancel.py) — инструмент
            встаёт между кусками обхода и пишет ЧАСТИЧНЫЙ отчёт;
          * запись уходит из очереди по-настоящему (missions.remove), а не
            остаётся «снятой» в истории.
        """
        current = missions.status_of(mid)
        if current is None:
            return {"ok": False, "status": None, "running": False, "stopped": False,
                    "why": i18n.t("tk.result_missing", id=mid)}
        stopping = current == "running"
        if stopping:
            cancel.request(mid)
            audit.log("mission.stop_requested", mission=mid)
        removed = missions.remove(mid)
        audit.log("mission.removed", mission=mid, was=current, stopped=stopping,
                  removed=removed)
        if stopping:
            return {"ok": True, "status": current, "running": True, "stopped": True,
                    "why": i18n.t("tk.deleted_running", id=mid)}
        return {"ok": True, "status": current, "running": False, "stopped": False,
                "why": i18n.t("tk.deleted", id=mid)}

    # ---------- журнал и отчёты ----------
    def audit_tail(self, n: int = 60) -> list[dict]:
        return audit.tail(n)

    def diary_tail(self, n: int = 6) -> list[str]:
        """Дневник организма (M5): короткие СВОИ записи о своём мире.

        Нужен окну: «стало иначе» должно быть видно глазами, а не только в файле
        data/diary.md. Мир не роняет панель — при ошибке отдаём пусто.
        """
        try:
            return world.diary_tail(n)
        except Exception:
            return []

    def thoughts_tail(self, n: int = 400) -> list[str]:
        """Последние строки внутренней речи (💭) — для вкладки «Мысли»."""
        return audit.thoughts_tail(n)

    def reports(self, limit: int = 20) -> list[dict]:
        """Отчёты списком — с ЧЕЛОВЕЧЕСКОЙ подписью, а не именем файла.

        Стас сказал прямо: «отчёты не читаемы» — значит и в списке должно быть
        «аудит сети 192.168.1.0/24 · живых 7 · 24.09 16:26», а не
        «20260924-162750-m-20260924-162624-22.json».
        """
        folder = config.DATA / "reports"
        if not folder.exists():
            return []
        key = (limit, self._dir_key(folder))
        if self._reports_cache is not None and self._reports_cache[0] == key:
            return list(self._reports_cache[1])      # папка не менялась — не перечитываем
        rows = []
        for p in sorted(folder.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
            rep = reports.load(p)
            rows.append({"name": p.name, "path": str(p),
                         "size": p.stat().st_size, "at": int(p.stat().st_mtime),
                         "title": reports.title(rep, p.stem),
                         "kind": rep.get("kind") or ("net_audit" if "hosts" in rep else ""),
                         "alive": rep.get("alive_count"),
                         "text": str(reports.text_path(p))})
        self._reports_cache = (key, rows)
        return list(rows)

    def report_text(self, path: str, limit: int = 20000) -> str:
        """Человеческий текст отчёта (готовый .txt рядом или собранный из JSON)."""
        return reports.read_text(path, limit=limit)

    # ---------- мозг ----------
    def brain_state(self, ttl: float = 5.0) -> dict:
        """Состояние мозга. Пробу сервера помним ttl секунд: это сетевой запрос, и в
        главном потоке окна ему делать нечего (замер: первая проба 108 мс)."""
        st = self.brain.state(ttl=ttl)
        st["roles"] = i18n.role_name("sensory")
        return st

    def brain_on(self, on_progress=None, on_note=None, wait: float = 90.0) -> dict:
        """Включить мозг: при необходимости скопировать своё, поднять сервер, включить режим.

        ОБЛАКО (v0.30.0): в режиме «только облако» свой сервер НЕ поднимаем и модель
        не копируем — мозг живёт в облаке, и запускать локальный сервер значило бы
        жечь память впустую. Зовём честно: включён, источник — облако.
        """
        if self.brain.cloud_mode() == "online":
            self.brain.set_mode("server")
            self.brain.cfg["mode"] = "server"
            why = online.why_not(self.brain.cfg)
            if why:
                return {"ok": False, "steps": [i18n.t(f"cloud.why.{why}")]}
            cl = online.state(self.brain.cfg)
            return {"ok": True, "steps": [i18n.t("brain.step.cloud", provider=cl["provider"],
                                                  model=cl["model"])]}
        steps: list[str] = []
        extra = self.brain.stop_extra_listeners()      # два сервера на порту — двойная память
        if extra:
            steps.append(i18n.t("brain.step.extra_server",
                                list=", ".join(str(p) for p in extra)))
        st = self.brain.state()
        if not st["self_contained"]:
            on_note and on_note(i18n.t("brain.step.copying"))
            rep = self.brain.provision(on_progress=on_progress)
            steps.append(i18n.t("brain.step.provision", copied=len(rep["copied"]),
                                skipped=len(rep["skipped"]),
                                mb=round(rep["bytes"] / 1048576, 1)))
            if rep["missing"]:
                steps.append(i18n.t("brain.step.source_missing", list=", ".join(rep["missing"])))
                return {"ok": False, "steps": steps}
            if rep["errors"]:
                steps.append(i18n.t("brain.step.copy_errors", list="; ".join(rep["errors"][:2])))
        self.brain.set_mode("server")
        self.brain.cfg["mode"] = "server"
        if not self.brain.available():
            on_note and on_note(i18n.t("brain.step.starting"))
            ok = self.brain.start_server(wait_seconds=wait, on_note=on_note)
            steps.append(i18n.t("brain.step.up") if ok else i18n.t("brain.step.down"))
            if not ok:
                return {"ok": False, "steps": steps}
        steps.append(i18n.t("brain.step.alive", endpoint=self.brain.cfg.get("endpoint")))
        return {"ok": True, "steps": steps}

    def brain_off(self) -> dict:
        self.brain.set_mode("off")
        self.brain.cfg["mode"] = "off"
        self.brain.stop_server()
        return {"ok": True, "steps": [i18n.t("brain.step.off")]}

    def model_short(self) -> str:
        """Короткое имя живой модели для подписей окна («1.5b»)."""
        try:
            return self.brain.model_short()
        except Exception:                              # noqa: BLE001 — ядро без мозга
            return "?"

    def ask(self, prompt: str) -> str:
        """Спросить у своего мозга (панель может и просто поболтать с ним)."""
        plan = self.brain.route()
        if plan == "none":
            # Мозг выключен или облако не готово — говорим причину, а не «мозг молчит».
            why = online.why_not(self.brain.cfg) if self.brain.mode() == "server" else ""
            return i18n.t(f"cloud.why.{why}") if why else i18n.t("brain.silent")
        if plan == "local" and not self.brain.available() and not self.brain.start_server():
            return i18n.t("brain.silent")
        # Язык ответа задаёт интерфейс: существо говорит с оператором на его языке.
        ans = self.brain.think(prompt)
        return ans or i18n.t("brain.no_answer")

    def cloud_probe(self) -> str:
        """ЖИВАЯ проба облака: короткий вопрос и ответ ПО ФАКТУ (v0.30.0).

        Зачем отдельно от «готов»: готовность по настройкам — это только ключ, адрес
        и бюджет. Ключ могут отозвать, модель — снять с обслуживания (живой пример
        30.09.2026: gemini-2.5-flash-lite отвечает 404 «no longer available to new
        users»). Поэтому проверка — это настоящий запрос, и ответ показывается словами.
        """
        res = online.probe(self.brain.cfg)
        if res.get("ok"):
            return i18n.t("cloud.probe.ok", provider=res.get("provider"),
                          model=res.get("model"), ms=res.get("ms"),
                          spent=res.get("spent"), text=str(res.get("text") or "")[:200])
        why = str(res.get("error") or "")
        if why in online.WHY_CODES:
            return i18n.t(f"cloud.why.{why}")
        return i18n.t("cloud.probe.fail", error=why, code=res.get("code") or 0,
                      detail=str(res.get("detail") or "")[:200])

    # ---------- статус ----------
    def status(self) -> dict:
        st = self.syn.load_state()
        return {
            "name": self.cfg.get("name", "Synapse"),
            "tick_seconds": self.cfg.get("tick_seconds", 60),
            "max_wakeups": self.cfg.get("max_wakeups_per_day", 0),
            "wakeups": st.get("wakeups", 0),
            "total_ticks": st.get("total_ticks", 0),
            "born": st.get("born"),
            "energy": round(self.body.energy(), 5),
            "lit": self.body.lit(),
            "size": self.body.size,
            "roles": self.body.roles_summary(),
            "zones": self.body.zone_summary(),
            "intent": self.body.intent(),
            "brain": self.brain.mode(),
            "queued": len(self.queue()),
        }

    def neuron(self, name: str) -> dict:
        rec = self.body.neuron(name)
        if rec:
            # Слово о передаче — на языке интерфейса; технический код (rec["kind"])
            # остаётся кодом, его читает код, а не человек.
            rec["kind_label"] = i18n.t(pharm.kind_key(rec.get("nt") or ""))
            # Соседи — по НАСТОЯЩИМ связям тела. Прежняя карточка брала поле "partners",
            # которого у узла нет вовсе, поэтому соседей не показывала никогда
            # (найдено 26.09.2026 вместе с жалобой «клик по нейрону — и ничего»).
            rec["partners"] = self.body.neighbours(rec.get("index"), limit=6)
        return rec

    def my_network(self) -> str:
        """Подсеть этой машины — чтобы кнопка «просканировать мою сеть» знала, куда идти."""
        return net_scan.local_cidr() or ""

    def role_table(self) -> list[dict]:
        """Кто есть кто — для вкладки «Нейроны»: роль, сколько, кого слушает."""
        rows = []
        for role, n in sorted(self.body.roles_summary().items(), key=lambda t: -t[1]):
            names = [self.body.ids[i] for i, r in enumerate(self.body.roles) if r == role]
            rows.append({"role": role, "ru": i18n.role_name(role), "count": n,
                         "examples": ", ".join(names[:8]),
                         "hubs": sum(1 for i, r in enumerate(self.body.roles)
                                     if r == role and self.body.info[i].get("hub"))})
        return rows

    # ---------- настройки (вкладка «Настройки») ----------
    def settings(self) -> dict:
        """Что показывать в настройках. Источник — data/config.json, больше неоткуда."""
        cfg = config.load()
        ui = cfg.get("ui") or {}
        net = cfg.get("net") or {}
        # МОЗГ В НАСТРОЙКАХ (v0.20.0). Список моделей берём из САМОЙ папки существа:
        # положил рядом файл — он появился в списке, ничего не надо прописывать руками.
        # Именно так Стас и просил: «положи рядом 1.5b — я сам попробую и решу».
        brain = cfg.get("brain") or {}
        local = Path(str(brain.get("local_dir", "models/brain")))
        if not local.is_absolute():
            local = config.ROOT / local
        try:
            models = sorted(p.name for p in local.glob("*.gguf"))
        except OSError:
            models = []
        cur_model = Path(str(brain.get("model", ""))).name
        if cur_model and cur_model not in models:
            models.insert(0, cur_model)
        return {
            "lang": str(ui.get("lang") or i18n.DEFAULT),
            "theme": str(ui.get("theme") or theme.DEFAULT),
            "fps_ms": int(ui.get("fps_ms") or 16),
            "tick_seconds": float(cfg.get("tick_seconds")
                                  if cfg.get("tick_seconds") is not None else 60),
            "idle_thinking": bool(cfg.get("idle_thinking")),
            "allow_write_probe": bool(net.get("allow_write_probe", True)),
            # ДИАПАЗОН, А НЕ СПИСОК (v0.22.0): «все порты» списком служб не выразить.
            "port_from": int(net.get("port_from") or 1),
            "port_to": int(net.get("port_to") or 1024),
            "port_workers": int(net.get("port_workers") or 256),
            "brain_model": cur_model,
            "brain_models": models,
            "brain_device": str(brain.get("device", "gpu") or "gpu").strip().lower(),
            "brain_ngl": int(brain.get("ngl", 10) or 0),
            "brain_ctx": int(brain.get("ctx", 4096) or 4096),
            "brain_port": int(brain.get("server_port", 11435) or 11435),
            # МОЗГ ПРЕДЛАГАЕТ РАБОТУ (v0.29.0, M6). Слово Стаса 30.09.2026: «Можно
            # это сделать галкой в настройках — типа подтверждение действий».
            "brain_propose": bool(brain.get("propose", True)),
            "brain_propose_confirm": bool(brain.get("propose_confirm", True)),
            # ОБЛАЧНЫЙ МОЗГ (v0.30.0). Окно показывает всё, что нужно, чтобы выбрать
            # источник мысли и увидеть, есть ли чем его кормить: режим, провайдер,
            # модель, потолок, маска и НАЛИЧИЕ КЛЮЧА (сам ключ в окно не попадает).
            "brain_cloud": online.mode(brain),
            "brain_providers": online.provider_names(),
            "brain_provider": online.provider(brain),
            "brain_cloud_model": online.model(brain),
            "brain_cloud_models": online.state(brain)["models"],
            "brain_cloud_url": online.base_url(brain),
            "brain_cloud_budget": online.limit(brain),
            "brain_cloud_mask": bool(online.section(brain).get("mask", True)),
            "brain_cloud_key": bool(online.key(brain)),
            "brain_cloud_spent": online.used(brain),
            "brain_cloud_left": online.left(brain),
            "brain_cloud_why": online.why_not(brain),
        }

    def cloud_key_info(self) -> dict:
        """Есть ли чем кормить облако — БЕЗ показа самого ключа. Окно должно честно
        сказать «ключа нет, положи сюда», а не молча слать запрос без заголовка."""
        prov = online.provider(self.brain.cfg)
        k = online.key(self.brain.cfg)
        return {"path": str(creds.CREDS), "provider": prov,
                "has": bool(k), "n": len(k)}

    def settings_path(self) -> str:
        """Где лежат настройки — показываем во вкладке «Настройки», а не держим в тайне."""
        return str(config.CONFIG_PATH)

    #: Замер 26.09.2026 на своей сети: 256 потоков дают 1.7 мс на порт (16 потоков —
    #: 22 мс, то есть полный диапазон 1..65535 занял бы на один хост часы). Этим числом
    #: окно считает ЦЕНУ обхода и говорит её вслух ДО нажатия, а не после.
    MS_PER_PORT = 1.7

    @staticmethod
    def _duration(secs: float) -> str:
        if secs < 60:
            return i18n.t("set.time.sec", n=f"{secs:.1f}")
        if secs < 3600:
            return i18n.t("set.time.min", n=f"{secs / 60:.0f}")
        return i18n.t("set.time.hour", n=f"{secs / 3600:.1f}")

    @classmethod
    def probe_estimate(cls, lo: int, hi: int, workers: int = 256) -> str:
        """Сколько займёт обход этого диапазона — ЧЕЛОВЕЧЕСКОЙ фразой (v0.22.0).

        Считаем на ОДИН живой хост: сколько хостов отзовётся, заранее не знает никто,
        а обещать за всех — гадание. Зато стоимость одного хоста видна заранее.
        """
        count = max(0, int(hi) - int(lo) + 1)
        scale = max(1.0, 256.0 / max(1, int(workers or 256)))
        secs = count * cls.MS_PER_PORT * scale / 1000.0
        return i18n.t("set.ports.estimate", lo=int(lo), hi=int(hi), count=count,
                      time=cls._duration(secs))

    @staticmethod
    def life_now_text(tick: float) -> str:
        """ОДИН темп, и это такт (v0.22.0).

        Слово Стаса 26.09.2026: «время такта и есть количество мыслей — ведь если мозг
        выключен, то не будет мыслей… время такта 0 — поток. всё». Раньше здесь считался
        максимум из двух чисел и писалась инструкция на простыню; теперь фраза короткая:
        либо поток, либо такт.
        """
        t = float(tick or 0)
        if t <= 0:
            return i18n.t("set.life.stream")
        return i18n.t("set.life.tick", tick=f"{t:g}")

    def parse_port_range(self, lo, hi) -> tuple[int, int]:
        """Диапазон портов, который ПОНЯТ: два числа вместо строки (v0.22.0).

        Слово Стаса 26.09.2026: «я по прежнему вижу не диапазон портов… а строку Порты
        для проверки». Список служб заменён диапазоном ОТ-ДО: он выражает и «только
        службы», и «все порты». Потолок — сам размер диапазона (1..65535), поэтому
        отвергать нечего; время обхода считает probe_estimate(), и видно его ДО нажатия.
        """
        out: list[int] = []
        for field, value in (("port_from", lo), ("port_to", hi)):
            try:
                n = int(str(value).strip())
            except (TypeError, ValueError):
                raise ValueError(i18n.t("set.bad_number", field=field, value=value))
            if not (1 <= n <= 65535):
                raise ValueError(i18n.t("set.bad_number", field=field, value=value))
            out.append(n)
        return (out[0], out[1]) if out[0] <= out[1] else (out[1], out[0])

    def apply_settings(self, values: dict) -> dict:
        """Записать настройки в config.json и применить их НА ХОДУ.

        Язык и тему применяем сразу — их видно, не закрывая окно. Числа проверяем
        и о плохом СКАЗЫВАЕМ, оставив прежнее значение: «упало» тут недопустимо,
        настройки — это то место, где оператор ошибки и делает.

        Возврат: {"ok", "changed" (человеческие строки), "errors", "path", "settings"}.
        """
        cfg = config.load()
        ui = dict(cfg.get("ui") or {})
        net = dict(cfg.get("net") or {})
        was = self.settings()
        changed: list[str] = []
        errors: list[str] = []

        def num(field: str, value, cast, current):
            try:
                return cast(str(value).strip().replace(",", "."))
            except (TypeError, ValueError):
                errors.append(i18n.t("set.bad_number", field=field, value=value))
                return current

        def flag(value, current: bool) -> bool:
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "on", "yes", "да")
            return bool(value) if value is not None else current

        lang = str(values.get("lang") or was["lang"]).strip().lower()
        if lang not in i18n.available():
            errors.append(i18n.t("set.bad_choice", field="lang", value=lang))
            lang = was["lang"]
        them = str(values.get("theme") or was["theme"]).strip().lower()
        if them not in theme.available():
            errors.append(i18n.t("set.bad_choice", field="theme", value=them))
            them = was["theme"]
        tick = num("tick_seconds", values.get("tick_seconds", was["tick_seconds"]),
                   float, was["tick_seconds"])
        fps = num("fps_ms", values.get("fps_ms", was["fps_ms"]), int, was["fps_ms"])
        idle = flag(values.get("idle_thinking"), was["idle_thinking"])
        # ГАЛКИ ПРО МОЗГ (v0.29.0, M6): предложения работы и подтверждение действий.
        propose_on = flag(values.get("brain_propose"), was["brain_propose"])
        confirm_on = flag(values.get("brain_propose_confirm"), was["brain_propose_confirm"])
        write = flag(values.get("allow_write_probe"), was["allow_write_probe"])
        try:
            port_from, port_to = self.parse_port_range(
                values.get("port_from", was["port_from"]),
                values.get("port_to", was["port_to"]))
        except ValueError as exc:            # мусор в диапазоне — говорим вслух
            errors.append(str(exc))
            port_from, port_to = int(was["port_from"]), int(was["port_to"])

        tick = max(0.0, min(86400.0, float(tick)))
        fps = max(8, min(500, int(fps)))
        # 0 — это ПОТОК, а не «почти ноль»: снизу стоял зажим 1.0, и «без пауз»
        # нельзя было поставить в окне вовсе (v0.16.0). Второго темпа (пауза до мысли)
        # больше нет совсем — темп мыслей ЭТО И ЕСТЬ такт (v0.22.0).

        # МОЗГ: модель, счёт (видеокарта/процессор), слои на GPU, порт сервера.
        # Мусор не принимаем — оставляем прежнее и СКАЗЫВАЕМ об этом (это настройки,
        # тут оператор и ошибается).
        brain = dict(cfg.get("brain") or {})
        b_model = str(values.get("brain_model") or was["brain_model"]).strip()
        if b_model and was.get("brain_models") and b_model not in was["brain_models"]:
            errors.append(i18n.t("set.bad_choice", field="brain.model", value=b_model))
            b_model = was["brain_model"]
        b_device = str(values.get("brain_device") or was["brain_device"]).strip().lower()
        if b_device not in ("gpu", "cpu"):
            errors.append(i18n.t("set.bad_choice", field="brain.device", value=b_device))
            b_device = was["brain_device"]
        b_ngl = int(max(0, min(999, num("brain.ngl", values.get("brain_ngl", was["brain_ngl"]),
                                        float, was["brain_ngl"]))))
        b_port = int(num("brain.port", values.get("brain_port", was["brain_port"]),
                         float, was["brain_port"]))
        if not (0 < b_port < 65536):
            errors.append(i18n.t("set.bad_number", field="brain.port", value=b_port))
            b_port = int(was["brain_port"])
        # ОБЛАЧНЫЙ МОЗГ (v0.30.0). Мусор не принимаем — как и везде в настройках:
        # оставляем прежнее значение и ГОВОРИМ об этом, а не гадаем, чего хотел оператор.
        cloud = dict(brain.get("online") or {})
        c_mode = str(values.get("brain_cloud") or was["brain_cloud"]).strip().lower()
        if c_mode not in online.MODES:
            errors.append(i18n.t("set.bad_choice", field="brain.online.mode", value=c_mode))
            c_mode = was["brain_cloud"]
        c_prov = str(values.get("brain_provider") or was["brain_provider"]).strip().lower()
        if c_prov not in online.PROVIDERS:
            errors.append(i18n.t("set.bad_choice", field="brain.online.provider", value=c_prov))
            c_prov = was["brain_provider"]
        c_model = str(values.get("brain_cloud_model",
                                 was["brain_cloud_model"]) or "").strip()
        c_budget = int(max(0.0, num("brain.online.daily_tokens",
                                    values.get("brain_cloud_budget", was["brain_cloud_budget"]),
                                    float, was["brain_cloud_budget"])))
        c_mask = flag(values.get("brain_cloud_mask"), was["brain_cloud_mask"])
        # Свой адрес: меняем ТОЛЬКО поле provider=custom — иначе адрес каталога
        # (gemini/groq) уехал бы в настройки и «провайдер» перестал бы значить что-либо.
        c_url = str(cloud.get("base_url") or "")
        cloud.update({"mode": c_mode, "provider": c_prov, "model": c_model,
                      "daily_tokens": c_budget, "mask": c_mask, "base_url": c_url})
        brain.update({"model": b_model, "device": b_device, "ngl": b_ngl,
                      "server_port": b_port, "propose": propose_on,
                      "propose_confirm": confirm_on, "online": cloud})
        cfg["brain"] = brain
        brain_changed = (b_model != was["brain_model"] or b_device != was["brain_device"]
                         or b_ngl != int(was["brain_ngl"])
                         or b_port != int(was["brain_port"]))

        ui.update({"lang": lang, "theme": them, "fps_ms": fps})
        net["allow_write_probe"] = bool(write)
        net["port_from"], net["port_to"] = int(port_from), int(port_to)
        cfg["ui"], cfg["net"] = ui, net
        cfg["tick_seconds"] = float(tick)
        cfg["idle_thinking"] = bool(idle)
        path = config.save(cfg)
        config.forget_cache()
        self.cfg = config.load()
        # Мозга может НЕ БЫТЬ: тесты настроек собирают «ядро без организма»
        # (PanelCore.__new__ + cfg) — окно от этого падать не должно.
        if getattr(self, "brain", None) is not None:
            self.brain.reload(self.cfg)      # модель/счёт/порт перечитываются СРАЗУ
        # ОРГАНИЗМ ТОЖЕ ПЕРЕЧИТЫВАЕТ (v0.31.0). Галки «мозг предлагает» и
        # «подтверждение действий» и рубильник запаса читает СУЩЕСТВО — а оно
        # держало слепок `agent.cfg` с момента запуска, поэтому снятая галка не
        # работала до перезапуска. Живой случай 30.09.2026: снял в 21:42:45 — в
        # 21:43:48 и 21:44:18 предложения всё равно встали на кнопку.
        if getattr(self, "syn", None) is not None:
            self.syn.reload(self.cfg)
        i18n.set_lang(lang)
        theme.set_theme(them)

        on_off = {True: i18n.t("set.on"), False: i18n.t("set.off")}
        now = self.settings()
        if lang != was["lang"]:
            changed.append(i18n.t("set.changed.lang", value=i18n.name(lang)))
        if them != was["theme"]:
            changed.append(i18n.t("set.changed.theme",
                                  value=i18n.t(f"set.theme.{them}")))
        if float(now["tick_seconds"]) != float(was["tick_seconds"]):
            changed.append(i18n.t("set.changed.tick", value=now["tick_seconds"]))
        if now["fps_ms"] != was["fps_ms"]:
            changed.append(i18n.t("set.changed.fps", value=now["fps_ms"]))
        if now["idle_thinking"] != was["idle_thinking"]:
            changed.append(i18n.t("set.changed.idle", value=on_off[now["idle_thinking"]]))
        if ((int(now["port_from"]), int(now["port_to"]))
                != (int(was["port_from"]), int(was["port_to"]))):
            changed.append(i18n.t("set.changed.ports",
                                  value=f"{int(now['port_from'])}-{int(now['port_to'])}"))
        dev_names = {"gpu": i18n.t("device.gpu"), "cpu": i18n.t("device.cpu")}
        if now["brain_model"] != was["brain_model"]:
            changed.append(i18n.t("set.changed.brain_model", value=now["brain_model"]))
        if now["brain_device"] != was["brain_device"]:
            changed.append(i18n.t("set.changed.brain_device",
                                  value=dev_names.get(now["brain_device"], now["brain_device"])))
        if int(now["brain_ngl"]) != int(was["brain_ngl"]):
            changed.append(i18n.t("set.changed.brain_ngl", value=now["brain_ngl"]))
        if int(now["brain_port"]) != int(was["brain_port"]):
            changed.append(i18n.t("set.changed.brain_port", value=now["brain_port"]))
        if brain_changed:
            # Модель и счёт читаются при ЗАПУСКЕ сервера — на ходу их не подменить.
            # Поэтому гасим свой сервер и прямо говорим, что поднять его надо заново.
            if getattr(self, "brain", None) is not None:
                self.brain.stop_server()
            changed.append(i18n.t("set.changed.brain_restart"))
            audit.log("settings.brain", model=now["brain_model"], device=now["brain_device"],
                      ngl=now["brain_ngl"], port=now["brain_port"])
        if now["allow_write_probe"] != was["allow_write_probe"]:
            changed.append(i18n.t("set.changed.write_probe",
                                  value=on_off[now["allow_write_probe"]]))
        if now["brain_propose"] != was["brain_propose"]:
            changed.append(i18n.t("set.changed.propose",
                                  value=on_off[now["brain_propose"]]))
        if now["brain_propose_confirm"] != was["brain_propose_confirm"]:
            changed.append(i18n.t("set.changed.propose_confirm",
                                  value=on_off[now["brain_propose_confirm"]]))
        # ОБЛАКО: каждую правку называем вслух своей строкой — оператор должен видеть,
        # ЧТО он только что поменял, а не догадываться по молчанию.
        if now["brain_cloud"] != was["brain_cloud"]:
            changed.append(i18n.t("set.changed.cloud_mode",
                                  value=i18n.t(f"set.cloud.{now['brain_cloud']}")))
        if now["brain_provider"] != was["brain_provider"]:
            changed.append(i18n.t("set.changed.cloud_provider", value=now["brain_provider"]))
        if now["brain_cloud_model"] != was["brain_cloud_model"]:
            changed.append(i18n.t("set.changed.cloud_model", value=now["brain_cloud_model"]))
        if int(now["brain_cloud_budget"]) != int(was["brain_cloud_budget"]):
            changed.append(i18n.t("set.changed.cloud_budget", value=now["brain_cloud_budget"]))
        if now["brain_cloud_mask"] != was["brain_cloud_mask"]:
            changed.append(i18n.t("set.changed.cloud_mask",
                                  value=on_off[now["brain_cloud_mask"]]))
        if (now["brain_cloud"] != was["brain_cloud"] or now["brain_provider"] != was["brain_provider"]
                or now["brain_cloud_model"] != was["brain_cloud_model"]):
            audit.log("settings.cloud", mode=now["brain_cloud"], provider=now["brain_provider"],
                      model=now["brain_cloud_model"], budget=now["brain_cloud_budget"],
                      mask=now["brain_cloud_mask"], url=now["brain_cloud_url"])
        if not changed and not errors:
            changed.append(i18n.t("set.changed.none"))
        audit.log("settings.saved", file=path.name, changed=len(changed), errors=len(errors))
        return {"ok": not errors, "changed": changed, "errors": errors,
                "path": str(path), "settings": now}
