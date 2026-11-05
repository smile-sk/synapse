"""Мозг предлагает работу — но НЕ делает её (этап M6 плана).

Слово Стаса 30.09.2026: «сначала посмотрим что он будет предлагать (и как я это кстати
апрувить? кнопка какая то что ли) — а потом хочу посмотреть если его этим не тормозить
и он будет что то делать сам просто давая отчеты. Можно это сделать галкой в настройках
— типа подтверждение действий».

ЧТО ЗДЕСЬ ЕСТЬ. Мозг видит свои дела (последний отчёт, изменения своего мира, запас,
цели — всё это попадает в промпт мысли) и может предложить ОДНУ работу, написав строку
с меткой 🎯. Эта строка вынимается из ответа до всех проверок на копию и мусор:

    🎯 peers
    🎯 remote host=203.0.113.10
    🎯 net_audit cidr=192.168.1.0/24

ЧЕГО ЗДЕСЬ НЕТ — и это главное. У мозга нет права исполнить: он только предлагает.
Предложение ложится в ту же очередь, что и миссии оператора, и дальше решает код:

  * `brain.propose_confirm = on` (галка «подтверждение действий») — работа стоит
    статусом `proposed` и ждёт кнопки «Подтвердить»; `missions.take_next` берёт только
    `queued`, поэтому сама она не побежит никогда;
  * `brain.propose_confirm = off` — существо берёт предложенное сразу и только
    отчитывается отчётом миссии.

СПИСОК РАЗРЕШЁННОГО — только чтение (`ALLOWED`), и он ЖЕЛЕЗНЫЙ: ни `write_probe`, ни
`stimulus`, ни `note`, ни `ask` от мозга не принимаются. Параметры тоже по списку
(`PARAMS`): чужие поля отбрасываются вместе с предложением. Дверь `scope` спрашивается
ДО записи в очередь — как у любой другой миссии, поэтому предложить то, что дверь не
пустит, нельзя (проверяется тестом).

ПОТОЛКИ — чтобы мозг не завалил очередь: не больше `brain.propose_max` за сутки, не
чаще `brain.propose_seconds` за то же самое место и не повторять то, что уже лежит в
очереди. Причина отказа — КОДОМ (`off` / `cap` / `again` / `inflight` / `door` /
`bad_tool` / `bad_param` / `alien`), слова берёт вызывающий: в журнале видно, почему
не взяли.

МАТЕРИАЛ ОБЯЗАН БЫТЬ НАСТОЯЩИМ (v0.32.0, код `alien`). Живой вечер 30.09.2026:
мозг предложил `net_audit cidr=192.168.1.0/24` (сеть, которой у нас нет) и
`remote host=127.0.0.1` (петлю). Дверь их пропускала: `net.allowed_cidrs` теперь
`0.0.0.0/0` — «любые сети», так решил оператор, и это про право, а не про смысл.
Поэтому у предложения есть второй, узкий список: смотреть можно СВОЁ (peers.targets,
peers.mine_cidrs) и УЖЕ ВИДЕННОЕ (зацепки data/leads.json из своих отчётов). Всё
остальное — выдумка, и она отклоняется кодом `alien` ДО двери.

ОКНО ПОВТОРА НЕ ГЛУШИТ НОВОЕ (v0.32.0). Живой журнал 30.09.2026: после одной миссии
`peers` мозг трижды получил отказ `again` (окно `brain.propose_seconds` = 1800 с),
хотя снимок соединений уже сменился — 43 новых факта. Теперь окно остаётся, но
уступает: если с момента прошлого предложения мир изменился (world.last), то же
место можно предложить снова — «предлагай, если действительно есть что смотреть».
"""
from __future__ import annotations

import ipaddress
import json
import os
import time
from datetime import datetime
from pathlib import Path

from synapse import config, i18n, missions, scope, world

PROPOSALS = config.DATA / "propose.json"

#: Метки предложения. 🎯 — как 💭 у внутренней речи: коротко и не спутать со словами.
MARKERS = ("🎯", "[миссия]", "[mission]", "(миссия)", "(mission)")

#: Что мозг может предложить: ТОЛЬКО ЧТЕНИЕ. Записи, стимулы и вопросы сюда не входят,
#: даже если модель их придумает: список проверяется до записи в очередь.
ALLOWED = ("peers", "net_audit", "remote", "certs", "diff")

#: Поля, которые принимаем у каждого инструмента. Всё остальное — не предложение.
PARAMS = {
    "peers": (),
    "net_audit": ("cidr",),
    "remote": ("host",),
    "certs": ("host", "port"),
    "diff": (),
}

#: Статусы, при которых то же самое уже в работе: второй раз не предлагаем.
INFLIGHT = ("queued", "proposed", "running")

#: Сколько ключей «недавно предлагали» помним.
KEEP_KEYS = 100

#: Убранные с обеих сторон украшения строки: «**🎯 peers**» и «- 🎯 peers» — одно и то же.
_TRIM = "*_`#>~•·+-–— \t"


def _file(path: str | Path | None = None) -> Path:
    return Path(path) if path else PROPOSALS


def _load(path: str | Path | None = None) -> dict:
    p = _file(path)
    base = {"day": None, "count": 0, "keys": {}, "last": None, "at": None}
    if p.exists():
        try:
            disk = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(disk, dict):
                base.update(disk)
        except (OSError, ValueError):
            pass
    if not isinstance(base.get("keys"), dict):
        base["keys"] = {}
    return base


def _save(state: dict, path: str | Path | None = None) -> None:
    p = _file(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def key_of(prop: dict) -> str:
    """Ключ предложения: инструмент + его поля. Им же ловим повтор."""
    prop = prop or {}
    params = prop.get("params") or {}
    return f"{prop.get('kind')}|" + json.dumps(params, sort_keys=True, ensure_ascii=False)


def parse_line(line: str) -> dict | None:
    """Строка ответа -> предложение. None значит «это не предложение».

    Формат простой и его же видно в промпте: метка, имя инструмента, поля `имя=значение`.
    Мусор не угадываем: нет имени инструмента — нет предложения.
    """
    text = " ".join(str(line or "").split())
    left = text.lstrip(_TRIM)
    low = left.lower()
    mark = ""
    for m in MARKERS:
        if low.startswith(m.lower()):
            mark = m
            break
    if not mark:
        return None
    rest = left[len(mark):].strip(_TRIM + " \t").strip()
    if not rest:
        return None
    tokens = rest.split()
    kind = tokens[0].strip().lower()
    if not kind or not kind.isascii():
        return None
    params: dict = {}
    for tok in tokens[1:]:
        if "=" not in tok:
            continue
        name, _, value = tok.partition("=")
        name, value = name.strip().lower(), value.strip().strip(".,;")
        if not name or not value or name not in PARAMS.get(kind, ()):
            continue
        if name == "port":
            try:
                params[name] = int(value)
            except ValueError:
                continue
        else:
            params[name] = value
    return {"kind": kind, "params": params, "raw": text[:200]}


def split(text: str) -> tuple[list[dict], str]:
    """Вынуть предложения из ответа. Возврат: (предложения, ответ без них)."""
    offers, keep = [], []
    for ln in (text or "").splitlines():
        prop = parse_line(ln)
        if prop is None:
            keep.append(ln)
        else:
            offers.append(prop)
    return offers, "\n".join(keep).strip()


def known_target(host: str, cfg: dict | None = None) -> bool:
    """Своё или уже виденное? (v0.32.0, код отказа `alien`.)

    Своё — узел из настроек (peers.targets), адрес внутри своих сетей
    (peers.mine_cidrs) и петля (127.0.0.0/8 — это сама машина). Виденное — зацепка из
    своих же отчётов (data/leads.json). Выдуманный адрес не проходит: дверь его
    пустит, а смотреть там нечего.

    Раздел `peers` берём из настроек, а если его в переданном слепке нет — из живого
    файла: свои сети есть у оператора, а не у теста, и «своё» не должно зависеть от
    того, какой словарь принесли.
    """
    try:
        ip = ipaddress.ip_address(str(host).strip())
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    cfg = cfg or {}
    peers = cfg.get("peers") or (config.load().get("peers") or {})
    for t in (peers.get("targets") or []):
        try:
            if ipaddress.ip_address(str(t.get("host")).strip()) == ip:
                return True
        except ValueError:
            continue
    for c in (peers.get("mine_cidrs") or []):
        try:
            if ip in ipaddress.ip_network(str(c), strict=False):
                return True
        except ValueError:
            continue
    try:
        for item in leads_targets():
            try:
                if ipaddress.ip_address(item) == ip:
                    return True
            except ValueError:
                continue
    except Exception:                            # памяти о зацепках нет — не беда
        pass
    return False


def leads_targets() -> list[str]:
    """Адреса из зацепок (data/leads.json) — то, что организм уже видел сам."""
    from synapse import leads
    out = []
    for item in (leads.all_leads() or []):
        t = str(item.get("target") or "").strip()
        if t:
            out.append(t)
    return out


def known_net(cidr: str, cfg: dict | None = None) -> bool:
    """Своя сеть (или та, где стоит свой узел) — то же правило для net_audit."""
    try:
        net = ipaddress.ip_network(str(cidr).strip(), strict=False)
    except ValueError:
        return False
    cfg = cfg or {}
    peers = cfg.get("peers") or (config.load().get("peers") or {})
    try:                                         # петля — сама машина, это «своё»
        loop = ipaddress.ip_network("127.0.0.0/8")
        if net.subnet_of(loop) or loop.subnet_of(net):
            return True
    except (TypeError, ValueError):
        pass
    for c in (peers.get("mine_cidrs") or []):
        try:
            mine = ipaddress.ip_network(str(c), strict=False)
            if net.subnet_of(mine) or mine.subnet_of(net):
                return True
        except (ValueError, TypeError):
            continue
    for t in (peers.get("targets") or []):
        try:
            if ipaddress.ip_address(str(t.get("host")).strip()) in net:
                return True
        except ValueError:
            continue
    return False


def check(prop: dict, cfg: dict | None = None) -> tuple[bool, str]:
    """Можно ли вообще принять это предложение. Возврат — (можно, код отказа)."""
    cfg = cfg or config.load()
    prop = prop or {}
    kind = str(prop.get("kind") or "")
    if kind not in ALLOWED:
        return False, "brain.bad_tool"
    params = prop.get("params") or {}
    allowed = PARAMS.get(kind, ())
    if any(k not in allowed for k in params):
        return False, "brain.bad_param"
    if kind in ("remote", "certs"):
        host = str(params.get("host") or "").strip()
        if not host:
            return False, "brain.bad_param"
        if not known_target(host, cfg):
            return False, "brain.propose_alien"
        ok, _code, _nums = scope.check_code(host, cfg)
        return (True, "") if ok else (False, "door")
    if kind == "net_audit":
        cidr = str(params.get("cidr") or "").strip()
        if not cidr:
            return False, "brain.bad_param"
        if not known_net(cidr, cfg):
            return False, "brain.propose_alien"
        ok, _code, _nums = scope.check_cidr_code(cidr, cfg)
        return (True, "") if ok else (False, "door")
    return True, ""


def _new_material(since) -> bool:
    """Мир изменился с тех пор? Тогда то же место стоит предложить снова (v0.32.0)."""
    try:
        change = world.last()
    except Exception:                            # памяти о мире нет — окно держит
        return False
    if not isinstance(change, dict):
        return False
    try:
        return float(change.get("at") or 0.0) > float(since or 0.0)
    except (TypeError, ValueError):
        return False


def _inflight(prop: dict, path: Path | None = None) -> bool:
    """То же самое уже стоит в очереди или идёт — второй раз не предлагаем."""
    want = key_of(prop)
    for m in missions.all_missions(path):
        if m.get("status") in INFLIGHT and key_of({"kind": m.get("kind"),
                                                  "params": m.get("params") or {}}) == want:
            return True
    return False


def offer(prop: dict, cfg: dict | None = None, life=None,
          path: Path | None = None, queue_path: Path | None = None) -> tuple[dict | None, str]:
    """Положить предложение в очередь. Возврат — (запись миссии или None, код/статус).

    Код отказа: `off` (мозгу предложения запрещены), `cap` (потолок на сутки),
    `again` (об этом месте уже предлагали недавно), `inflight` (уже в очереди),
    `door` / `bad_tool` / `bad_param` (предложение не проходит дверь или список).
    Успех возвращает код `proposed` (ждёт кнопки «Подтвердить») или `queued`
    (взяла сразу — галка подтверждения снята).
    """
    cfg = cfg or config.load()
    brain = (cfg.get("brain") or {})
    if not bool(brain.get("propose", True)):
        return None, "off"
    ok, code = check(prop, cfg)
    if not ok:
        return None, code
    state = _load(path)
    today = datetime.now().date().isoformat()
    if state.get("day") != today:               # новый день — счёт с нуля
        state["day"], state["count"] = today, 0
    cap = int(brain.get("propose_max", 6) or 0)
    if cap > 0 and int(state.get("count", 0)) >= cap:
        return None, "cap"
    key = key_of(prop)
    every = float(brain.get("propose_seconds", 1800) or 0)
    was = state["keys"].get(key)
    if every > 0 and was:
        try:
            # Окно уступает новому материалу (v0.32.0): за 30 минут мир менялся —
            # 43 новых факта в живом прогоне, — а мозг получал `again` и замолкал.
            if time.time() - float(was) < every and not _new_material(was):
                return None, "again"
        except (TypeError, ValueError):
            pass
    if _inflight(prop, queue_path):
        return None, "inflight"
    confirm = bool(brain.get("propose_confirm", True))
    rec = missions.add(str(prop.get("kind")), dict(prop.get("params") or {}),
                       note=i18n.t("brain.propose_note"), by="brain",
                       status=missions.PROPOSED if confirm else "queued",
                       path=queue_path)
    state["count"] = int(state.get("count", 0)) + 1
    state["keys"][key] = time.time()
    if len(state["keys"]) > KEEP_KEYS:          # помним только недавние
        order = sorted(state["keys"], key=lambda k: float(state["keys"][k] or 0))
        for old in order[: len(state["keys"]) - KEEP_KEYS]:
            state["keys"].pop(old, None)
    state["last"] = {"mission": rec["id"], "kind": rec["kind"], "status": rec.get("status"),
                     "at": datetime.now().isoformat(timespec="seconds")}
    state["at"] = time.time()
    _save(state, path)
    return rec, ("proposed" if confirm else "queued")


def stats(path: str | Path | None = None) -> dict:
    """Сводка слоя: сколько предложено сегодня и сколько всего помним."""
    state = _load(path)
    today = datetime.now().date().isoformat()
    return {"today": int(state.get("count", 0)) if state.get("day") == today else 0,
            "keys": len(state.get("keys") or {}),
            "last": state.get("last")}
