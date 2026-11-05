"""Жизнь — запас сил организма: то, чем он живёт между миссиями (v0.27.0).

Слово Стаса 30.09.2026: «я хочу сотворить живой киберорганизм ещё и с пользой.
зачем по твоему мы ему давали инстинкт самовыживания?»

ЧТО БЫЛО НЕ ТАК. Прежняя energy() (synapse/body.py:311) — среднее возбуждение
импульса по коннектому. Её нельзя потратить и нечем пополнить: тело «дышит», но
жизни в этом нет, и инстинкт лишь подкармливал гаснущее тело — повода жить он не
давал. Отсюда и вопрос оператора «зачем он тогда».

ЧТО ТЕПЕРЬ. ЗАПАС (reserve, 0..1) — настоящий ресурс:
  * тратится ВРЕМЕНЕМ: жизнь идёт — запас уходит (life.spend_per_hour);
  * приходит ПОЛЬЗОЙ: миссия нашла в мире то, чего организм ещё не знал, — сыта
    (life.feed_per_fact за каждую новую находку, life.feed_max за один раз);
  * тишина и повтор НЕ кормят: тот же порт на том же хосте второй раз — не находка;
  * скука тоже тратит (v0.32.0): находок нет дольше life.bored_after — ставка растёт
    на life.bored_spend. Так польза становится НУЖДОЙ, а не приятным дополнением:
    прожил час без находок — голоден и идёт искать сам. Живой вечер 30.09.2026
    показал, зачем это: запас стоял 0.99, расход 0.12/час давал голод только через
    шесть часов, и «пусть живёт и работает» оператор не увидел бы и за вечер.

ПОЧЕМУ НАХОДКИ СЧИТАЮТСЯ ПО ФАКТАМ, А НЕ ПО «МИССИЯ ПРОШЛА». Иначе кормило бы
само нажатие кнопки: повторный аудит той же сети «приносил» бы еду вечно, и запас
рос бы от безделья. Факты (хост и его порты, отпечаток баннера, срок сертификата,
изменившийся узел) лежат в data/life.json в разделе known — по ОБЛАСТИ того, что
смотрели. Новая находка — факт, которого в known не было.

ГРАНИЦА ЧЕСТНОСТИ. Пока окно закрыто, организм не живёт: простой списывается не
больше, чем на life.max_gap_seconds (иначе утро начиналось бы с истощения), а сам
запас не может уйти ниже нуля и выше единицы.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from synapse import audit, config

LIFE = config.DATA / "life.json"

#: Уровни запаса — словами, не числами: по ним пишется журнал и говорят рефлексы.
LEVELS = ("full", "ok", "hungry", "starving")

def scope_key(kind: str, params: dict | None, rep: dict | None) -> str:
    """ОДНО правило «что именно смотрели» — общее у запаса и памяти о делах (M5).

    Зачем отдельной функцией: запас считает находки по области (net:<cidr>,
    remote:<host>), и память о делах сравнивает снимки ТОЙ ЖЕ областью. Пока правило
    было методом Life, у второго слоя была бы своя копия — и «та же область» значило
    бы у них разное: сравнение с прошлым показывало бы изменения там, где ничего
    не менялось, или молчало там, где менялось.
    """
    params = params or {}
    rep = rep or {}
    if kind == "net_audit":
        return "net:" + str(params.get("cidr") or rep.get("cidr") or "local")
    if kind == "diff":
        return "diff:" + str(params.get("path") or rep.get("path") or "latest")
    if kind == "certs":
        hosts = sorted({f"{t.get('host')}:{t.get('port')}" for t in (rep.get("targets") or [])})
        return "certs:" + ",".join(hosts)
    if kind == "remote":
        return "remote:" + str(params.get("host") or rep.get("host") or "")
    if kind == "peers":
        names = sorted({str(t.get("name") or t.get("host") or "")
                        for t in (rep.get("targets") or [])})
        return "peers:" + ",".join(n for n in names if n)
    return f"{kind}:" + str(params or "")


def _num(value, default: float) -> float:
    """Число из конфига — с терпимостью: кривое значение не роняет организм."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


class Life:
    """Запас сил: расход временем, приход пользой.

    `path` — для тестов и проб (песочница): если не задан, файл берётся из
    module-global LIFE В МОМЕНТ ЗАПИСИ, а не при создании объекта. Так песочница
    tests/live_data.py::temp_live уводит и жизнь во временную папку.
    """

    def __init__(self, path: str | Path | None = None, cfg: dict | None = None) -> None:
        self.path = Path(path) if path else None
        self.cfg = cfg if isinstance(cfg, dict) else config.load()
        self._defaults = dict(config.DEFAULTS.get("life") or {})
        self.state = self._load()

    def reload(self, cfg: dict | None = None) -> None:
        """Настройки перечитаны (v0.31.0): рубильник и потолки запаса — на ходу.

        Запас читает `self.cfg` при каждом обращении (см. `_sec`), поэтому одной
        подмены слепка достаточно: `life.enabled`, `spend_per_hour`, пороги и
        потолки самозадания начинают действовать СРАЗУ, без перезапуска окна.
        """
        self.cfg = cfg if isinstance(cfg, dict) else config.load()

    # ---------- файл ----------
    def _file(self) -> Path:
        return self.path or LIFE

    def _sec(self, key: str):
        """Значение ключа жизни: память главнее умолчания (как config._deep_merge)."""
        live = (self.cfg.get("life") or {}).get(key)
        return self._defaults.get(key) if live is None else live

    def _load(self) -> dict:
        p = self._file()
        base = {"reserve": _num(self._sec("reserve"), 0.5), "fed_total": 0.0,
                "spent_total": 0.0, "last_feed": None, "last_feed_reason": None,
                "at": time.time(), "born": time.time(), "bored": False,
                "known": {}, "runs": {},
                "self_work": {"day": None, "count": 0, "last": None, "kind": None}}
        if p.exists():
            try:
                disk = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(disk, dict):
                    base.update(disk)
            except (OSError, ValueError):
                pass
        if not isinstance(base.get("known"), dict):
            base["known"] = {}
        if not isinstance(base.get("runs"), dict):
            base["runs"] = {}
        if not isinstance(base.get("self_work"), dict):
            base["self_work"] = {"day": None, "count": 0, "last": None, "kind": None}
        base["reserve"] = max(0.0, min(1.0, _num(base.get("reserve"), 0.5)))
        return base

    def save(self) -> None:
        p = self._file()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, p)                     # половину файла никто не прочтёт

    # ---------- сам запас ----------
    def reserve(self) -> float:
        return max(0.0, min(1.0, _num(self.state.get("reserve"), 0.5)))

    def level(self) -> str:
        """Уровень запаса. Жизнь выключена рубильником — голода нет вовсе.

        Иначе выходило бы противоречие: расход не идёт, а существо всё равно
        голодает по старому запасу. Один рубильник — одно поведение.
        """
        if not self.enable():
            return "ok"
        r = self.reserve()
        if r <= _num(self._sec("starving_at"), 0.05):
            return "starving"
        if r < _num(self._sec("hungry_at"), 0.30):
            return "hungry"
        if r >= 0.9:
            return "full"
        return "ok"

    def hungry(self) -> bool:
        return self.level() in ("hungry", "starving")

    def starving(self) -> bool:
        return self.level() == "starving"

    def enable(self) -> bool:
        return bool(self._sec("enabled"))

    # ---------- скука (v0.32.0) ----------
    def idle_seconds(self, now: float | None = None) -> float | None:
        """Сколько секунд без новой находки. None — не было ни одной.

        Точка отсчёта — последнее кормление (state.last_feed), а если не ела ни разу —
        рождение (state.born): у новорождённого есть life.bored_after времени найти
        первое, а не голод с первого такта. Мерим СЕКУНДАМИ, а не тактами: такт
        бывает и пять раз в секунду (тот же урок, что у note_run).
        """
        stamp = self.state.get("last_feed") or self.state.get("born")
        if not stamp:
            return None
        try:
            when = datetime.fromisoformat(str(stamp)).timestamp() \
                if isinstance(stamp, str) else float(stamp)
        except (TypeError, ValueError):
            return None
        now = time.time() if now is None else float(now)
        return max(0.0, now - when)

    def bored(self, now: float | None = None) -> bool:
        """Скучно ли: находок нет дольше life.bored_after (0 — скука выключена)."""
        if not self.enable():
            return False
        after = _num(self._sec("bored_after"), 0.0)
        if after <= 0:
            return False
        idle = self.idle_seconds(now)
        if idle is None:
            return True
        return idle >= after

    # ---------- расход и приход ----------
    def metabolize(self, now: float | None = None) -> float:
        """Списать время. Возврат — сколько ушло (0.0, если жизнь выключена)."""
        if not self.enable():
            return 0.0
        now = time.time() if now is None else float(now)
        last = _num(self.state.get("at"), now)
        dt = max(0.0, now - last)
        gap = _num(self._sec("max_gap_seconds"), 900)
        if gap > 0:
            dt = min(dt, gap)                  # окно было закрыто — жизнь стояла
        self.state["at"] = now
        if dt <= 0:
            return 0.0
        # СКУКА ТРАТИТ БЫСТРЕЕ (v0.32.0): ставка = обычный расход времени + скука.
        # Флаг держим в файле, чтобы агент писал в журнал ПЕРЕХОД (стало скучно /
        # перестало), а не метроном на каждом такте.
        bored = self.bored(now)
        self.state["bored"] = bool(bored)
        rate = _num(self._sec("spend_per_hour"), 0.12)
        if bored:
            rate += _num(self._sec("bored_spend"), 0.0)
        spent = dt * rate / 3600.0
        if spent <= 0:
            return 0.0
        before = self.reserve()
        self.state["reserve"] = max(0.0, before - spent)
        self.state["spent_total"] = _num(self.state.get("spent_total"), 0.0) + spent
        self.save()
        return before - self.reserve()

    def feed(self, amount: float, reason: str = "") -> float:
        """Покормить. Возврат — сколько запаса реально прибавилось."""
        if amount <= 0:
            return 0.0
        before = self.reserve()
        self.state["reserve"] = min(1.0, before + float(amount))
        gain = self.reserve() - before
        if gain > 0:
            self.state["fed_total"] = _num(self.state.get("fed_total"), 0.0) + gain
            self.state["last_feed"] = datetime.now().isoformat(timespec="seconds")
            self.state["last_feed_reason"] = reason or None
            self.save()
        return gain

    # ---------- когда что было (для выбора самозадания) ----------
    def note_run(self, kind: str, now: float | None = None) -> None:
        """Отметить, что работа такого рода только что прошла.

        Нужно самозаданию (synapse/work.py): «снимок соединений был пять минут
        назад» — причина НЕ бегать по сети каждую минуту. Мерим секундами, а не
        тактами: такт бывает и пять раз в секунду (урок tick_log_seconds).
        """
        now = time.time() if now is None else float(now)
        runs = self.state.get("runs") if isinstance(self.state.get("runs"), dict) else {}
        runs[str(kind)] = now
        self.state["runs"] = runs
        self.save()

    def age(self, kind: str, now: float | None = None) -> float | None:
        """Сколько секунд прошло с последней такой работы (None — не было вовсе)."""
        runs = self.state.get("runs") if isinstance(self.state.get("runs"), dict) else {}
        last = runs.get(str(kind))
        if last is None:
            return None
        now = time.time() if now is None else float(now)
        return max(0.0, now - float(last))

    # ---------- самозадание (M3) ----------
    def self_work_state(self) -> dict:
        """Счётчик своей работы: день, сколько раз, когда в последний раз."""
        block = self.state.get("self_work")
        if not isinstance(block, dict):
            block = {"day": None, "count": 0, "last": None, "kind": None}
            self.state["self_work"] = block
        today = datetime.now().date().isoformat()
        if block.get("day") != today:               # новый день — счёт с нуля
            block.update({"day": today, "count": 0})
        return block

    def self_work_allowed(self, cfg: dict | None = None) -> tuple[bool, str]:
        """Пора ли взяться за работу самой. Возврат — (можно, код отказа).

        Коды: `off` (рубильник), `interval` (недавно работала), `cap` (потолок на
        день). Код нужен журналу: без него не отличить «не голодна» от «уже была».
        """
        cfg = cfg or self.cfg
        block = (cfg.get("life") or {})
        if not bool(block.get("self_work", True)):
            return False, "off"
        st = self.self_work_state()
        cap = int(_num(block.get("self_work_max"), 8))
        if cap > 0 and int(st.get("count", 0)) >= cap:
            return False, "cap"
        every = _num(block.get("self_work_seconds"), 1800)
        last = st.get("last")
        if every > 0 and last:
            try:
                spent = time.time() - float(last)
            except (TypeError, ValueError):
                spent = every
            if spent < every:
                return False, "interval"
        return True, ""

    def self_work_taken(self, kind: str, mission: str = "") -> dict:
        """Записать, что работа взята: счётчик дня, время, род работы."""
        st = self.self_work_state()
        st["count"] = int(st.get("count", 0)) + 1
        st["last"] = time.time()
        st["kind"] = str(kind)
        st["mission"] = mission or None
        self.state["self_work"] = st
        self.save()
        return st

    def self_work_left(self, cfg: dict | None = None) -> int:
        """Сколько своих дел ещё можно взять сегодня (-1 значит «без потолка»)."""
        cfg = cfg or self.cfg
        cap = int(_num((cfg.get("life") or {}).get("self_work_max"), 8))
        if cap <= 0:
            return -1
        return max(0, cap - int(self.self_work_state().get("count", 0)))

    # ---------- находки ----------
    def scope_key(self, kind: str, params: dict | None, rep: dict | None) -> str:
        """Область, в которой сравниваются находки: что именно смотрели.

        Само правило живёт ОДНОЙ функцией ниже: им пользуются и запас (тут), и память
        о делах (synapse/world.py) — иначе «та же область» у двух слоёв означала бы
        разное, и сравнение с прошлым снимком рассыпалось бы.
        """
        return scope_key(kind, params, rep)

    def observe(self, kind: str, rep: dict | None, params: dict | None = None) -> dict:
        """Миссия закончилась: посчитать НОВЫЕ факты и накормить по ним.

        Возврат: {"new": сколько новых, "fed": сколько запаса, "scope": область}.
        `new = 0` — это тоже ответ: смотрели знакомое, еды нет.
        """
        scope = self.scope_key(kind, params, rep)
        found = facts_from(kind, rep)
        known = self.state.get("known") if isinstance(self.state.get("known"), dict) else {}
        seen = known.get(scope) or []
        seen_set = set(map(str, seen))
        fresh = [f for f in found if f not in seen_set]
        result = {"new": len(fresh), "fed": 0.0, "scope": scope, "facts": len(found)}
        if fresh:
            amount = min(_num(self._sec("feed_max"), 0.35),
                         _num(self._sec("feed_per_fact"), 0.03) * len(fresh))
            result["fed"] = self.feed(amount, reason=f"{kind} {len(fresh)} new")
        keep = int(_num(self._sec("keep_facts"), 2000))
        merged = list(seen) + fresh
        if keep > 0:
            merged = merged[-keep:]
        known[scope] = merged
        self.state["known"] = known
        self.save()
        if fresh:
            audit.log("life.fed", scope=scope, new=len(fresh), fed=round(result["fed"], 4),
                      reserve=round(self.reserve(), 4))
        else:
            audit.log("life.quiet", scope=scope, facts=len(found),
                      reserve=round(self.reserve(), 4))
        return result

    def describe(self) -> str:
        """Строкой для журнала и речи: сколько запаса и на каком уровне."""
        return f"{self.reserve():.2f} ({self.level()})"


def _banner(row: dict, port: int) -> str:
    text = str((row.get("banners") or {}).get(str(port)) or "").strip()
    return " ".join(text.split())[:80]


def facts_from(kind: str, rep: dict | None) -> list[str]:
    """Факты из отчёта миссии — то, чем можно наесться.

    Каждый факт — короткая строка, устойчивая между запусками: её и сравниваем
    с remembered (known). Порядок не важен, важна повторяемость текста.
    """
    rep = rep or {}
    out: list[str] = []
    if kind == "net_audit":
        for row in rep.get("hosts") or []:
            ip = str(row.get("ip") or "")
            ports = sorted(int(p) for p in (row.get("open") or []))
            if ip:
                out.append(f"{ip} open={','.join(map(str, ports))}")
            for p in ports:
                b = _banner(row, p)
                if b:
                    out.append(f"{ip}:{p} banner={b}")
            for s in sorted(map(str, row.get("shares") or [])):
                out.append(f"{ip} share={s}")
    elif kind == "diff":
        for row in rep.get("new_hosts") or []:
            ports = sorted(int(p) for p in (row.get("open") or []))
            out.append(f"new {row.get('ip')} open={','.join(map(str, ports))}")
        for row in rep.get("gone_hosts") or []:
            ports = sorted(int(p) for p in (row.get("open") or []))
            out.append(f"gone {row.get('ip')} open={','.join(map(str, ports))}")
        for row in rep.get("changed") or []:
            ip = row.get("ip") if isinstance(row, dict) else row
            out.append(f"changed {ip} " + json.dumps(row, ensure_ascii=False, sort_keys=True))
    elif kind == "certs":
        for row in rep.get("targets") or []:
            out.append(f"{row.get('host')}:{row.get('port')} ok={bool(row.get('ok'))} "
                       f"days={row.get('days_left')} tls={str(row.get('tls') or '')[:60]}")
    elif kind == "peers":
        # Факт здесь — ОТНОШЕНИЕ, а не строка соединения: случайный порт клиента в
        # факт не попадает (иначе каждый снимок приносил бы «всё новое» и кормил бы
        # воздухом). Отношения считает сам инструмент (см. synapse/tools/peers.py).
        rels = {str(r.get("rel")) for r in (rep.get("rows") or []) if r.get("rel")}
        out += sorted(rels)
    elif kind == "remote":
        host = str(rep.get("host") or "")
        if host:
            out.append(f"{host} state={rep.get('state')} method={rep.get('method')}")
            for w in rep.get("ways") or []:
                if isinstance(w, dict) and w.get("open"):
                    out.append(f"{host} way={w.get('method')} port={w.get('port')}")
            out.append(f"{host} output={str(rep.get('output') or '')[:80]}")
    return out
