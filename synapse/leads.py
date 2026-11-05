"""Цели — память о том, что стоит досмотреть (файл `data/leads.json`).

Слово Стаса 30.09.2026: «нужно так же чтоб он понимал что из тех отчётов — есть что —
чтобы мог брать например хост как цель для других миссий и исследований».

Зачем отдельный слой, если отчёты и так лежат на диске. Отчёт — это СНИМОК дня: он
отвечает «что было тогда». Цель — это ВОПРОС к следующему шагу: «вот здесь есть что
посмотреть». Без такого слоя существо смотрит и забывает: у него нет повода вернуться
к найденному хосту, и «исследование» не продолжается.

Правила слоя (те же, что у жизни и памяти о делах):

  1. **Цель рождается из ДЕЛА, а не из разговора.** Источник — отчёт миссии: открытые
     порты на хосте, открытый путь управления, истекающий сертификат, незнакомый
     слушатель, частый внешний партнёр.
  2. **Дубль не плодится.** Одинаковые цели сходятся в одну запись: растёт `times`,
     обновляется `last`. Так видно, что случай не разовый.
  3. **Чужое — не объект для щупанья.** У цели есть признак `mine`: своё (в нашей сети,
     наш узел) можно досматривать, чужое — только «спросить оператора», и это
     записано в самой записи, а не в чьей-то памяти.
  4. **Отработанное закрывается.** Когда миссия по цели прошла, цель помечается
     `researched` — иначе существо бегало бы по одному и тому же кругу.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from synapse import config, reports

LEADS = config.DATA / "leads.json"

#: Сколько целей держим (старые отработанные уходят первыми).
KEEP = 200

#: Что значит «цель»: хост с портами, открытый путь управления, срок сертификата,
#: незнакомый слушатель, входящее на необычный порт, внешний партнёр.
KINDS = ("host", "ways", "cert", "listen", "inbound", "peer", "partner")


def _file(path: Path | None = None) -> Path:
    return Path(path) if path else LEADS


def _load(path: Path | None = None) -> dict:
    p = _file(path)
    base = {"at": None, "items": []}
    if p.exists():
        try:
            disk = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(disk, dict):
                base.update(disk)
        except (OSError, ValueError):
            pass
    if not isinstance(base.get("items"), list):
        base["items"] = []
    return base


def _save(state: dict, path: Path | None = None) -> None:
    p = _file(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def key_of(item: dict) -> str:
    """Ключ цели: что именно и где. Порт входит в ключ — 'порт 22' и 'порт 445' разное."""
    return f"{item.get('what')}|{item.get('target')}|{item.get('port') or ''}"


# ---------------------------------------------------------------- рождение целей
def from_report(kind: str, rep: dict | None, cfg: dict | None = None) -> list[dict]:
    """Цели из отчёта миссии. Пусто — тоже ответ: смотреть было нечего или всё знакомо.

    `mine=True` значит «своё»: хозяйство оператора (сеть, которой он владеет, его узлы).
    Чужой адрес целью становится, но помечается `mine=False` — по нему существо само
    ничего не делает.
    """
    rep = rep or {}
    out: list[dict] = []
    if kind == "net_audit":
        for h in rep.get("hosts") or []:
            ip = str(h.get("ip") or "")
            ports = sorted(int(p) for p in (h.get("open") or []))
            if ip and ports:
                out.append({"target": ip, "what": "host", "port": None,
                            "why": "open=" + ",".join(map(str, ports)), "mine": True})
    elif kind == "diff":
        for h in rep.get("new_hosts") or []:
            ports = sorted(int(p) for p in (h.get("open") or []))
            out.append({"target": str(h.get("ip") or ""), "what": "host", "port": None,
                        "why": "new host open=" + ",".join(map(str, ports)), "mine": True})
    elif kind == "remote":
        host = str(rep.get("host") or "")
        ways = [str(w.get("method")) for w in (rep.get("ways") or []) if w.get("open")]
        if host and ways:
            out.append({"target": host, "what": "ways", "port": None,
                        "why": "ways: " + ", ".join(ways), "mine": True})
    elif kind == "certs":
        for t in rep.get("targets") or []:
            days = t.get("days_left")
            if t.get("ok") and isinstance(days, int) and days <= reports.WARN_DAYS:
                out.append({"target": str(t.get("host")), "what": "cert",
                            "port": int(t.get("port") or 443),
                            "why": f"cert expires in {days} d", "mine": True})
            elif not t.get("ok"):
                out.append({"target": str(t.get("host")), "what": "cert",
                            "port": int(t.get("port") or 443),
                            "why": "cert not read", "mine": True})
    elif kind == "peers":
        for item in rep.get("leads") or []:
            out.append({"target": str(item.get("target") or ""),
                        "what": str(item.get("what") or "host"),
                        "port": item.get("port"),
                        "why": str(item.get("why") or ""),
                        "mine": bool(item.get("mine"))})
    return [i for i in out if i["target"] and i["what"] in KINDS]


def remember(items: list[dict], path: Path | None = None) -> dict:
    """Запомнить цели. Возврат — {'new': сколько новых, 'known': сколько уже было}."""
    state = _load(path)
    by_key = {key_of(i): i for i in state["items"]}
    now = datetime.now().isoformat(timespec="seconds")
    new = known = 0
    for item in items or []:
        k = key_of(item)
        if k in by_key:
            rec = by_key[k]
            rec["times"] = int(rec.get("times", 1)) + 1
            rec["last"] = now
            rec["why"] = item.get("why") or rec.get("why")
            # Уже отработанную цель новый отчёт открывает снова: мир изменился.
            if rec.get("state") == "researched":
                rec["state"] = "open"
                rec["mission"] = None
            known += 1
        else:
            rec = {"target": item.get("target"), "what": item.get("what"),
                   "port": item.get("port"), "why": item.get("why") or "",
                   "mine": bool(item.get("mine")), "first": now, "last": now,
                   "times": 1, "state": "open", "mission": None}
            state["items"].append(rec)
            by_key[k] = rec
            new += 1
    keep = int(KEEP)
    if len(state["items"]) > keep:                 # сначала уходят отработанные
        order = sorted(state["items"], key=lambda i: (i.get("state") != "researched",
                                                      str(i.get("last") or "")))
        state["items"] = order[-keep:]
    state["at"] = now
    _save(state, path)
    return {"new": new, "known": known}


# ---------------------------------------------------------------- чтение целей
def all_leads(path: Path | None = None) -> list[dict]:
    return list(_load(path).get("items") or [])


def open_leads(mine: bool | None = True, path: Path | None = None) -> list[dict]:
    """Открытые цели. `mine=True` — только своё (по нему существо работает само)."""
    out = [i for i in all_leads(path) if i.get("state") == "open"]
    if mine is not None:
        out = [i for i in out if bool(i.get("mine")) == bool(mine)]
    return sorted(out, key=lambda i: (not i.get("mine"), str(i.get("first") or "")))


def mark(target: str, what: str = "", mission: str = "", state: str = "researched",
         path: Path | None = None) -> int:
    """Пометить цель отработанной (или снова открытой). Возврат — сколько записей тронуто."""
    st = _load(path)
    touched = 0
    for rec in st["items"]:
        if str(rec.get("target")) == str(target) and (not what or rec.get("what") == what):
            rec["state"] = state
            rec["mission"] = mission or rec.get("mission")
            rec["last"] = datetime.now().isoformat(timespec="seconds")
            touched += 1
    if touched:
        _save(st, path)
    return touched


def stats(path: Path | None = None) -> dict:
    """Сводка слоя — для журнала и отчёта: сколько целей, сколько открыто."""
    items = all_leads(path)
    return {"total": len(items),
            "open": sum(1 for i in items if i.get("state") == "open"),
            "mine": sum(1 for i in items if i.get("mine") and i.get("state") == "open"),
            "alien": sum(1 for i in items if not i.get("mine") and i.get("state") == "open")}
