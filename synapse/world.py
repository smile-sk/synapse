"""Память о делах — «стало иначе» вместо «как было» (этап M5 плана).

Слово Стаса 30.09.2026: «я хочу сотворить живой киберорганизм ещё и с пользой» и
«нужно так же чтоб он понимал что из тех отчетов — есть что». Польза здесь не в том,
что существо СМОТРИТ, а в том, что оно понимает: стало хуже, стало лучше или всё так
же. Без этого слоя каждый обход — первый: порт открылся вчера и сегодня выглядит
одинаково, а «новое устройство в своей сети» неотличимо от «как всегда».

ЧТО ЗДЕСЬ ЛЕЖИТ (файл `data/world.json`) — по ОБЛАСТЯМ (`synapse/life.py:scope_key`,
одно правило на два слоя):

    scopes["net:192.168.1.0/24"] = {"facts": [...], "at": ..., "count": 24}
    last = {"kind": "net_audit", "plus": 2, "minus": 1, "verdict": "shift", ...}

Разница считается по ФАКТАМ той же формы, что кормят запас (`life.facts_from`):
факт — «узел и его порты», «кто ↔ к кому:служба», «узел путь=ssh порт=22». Поэтому
«пришло» и «ушло» — это про мир, а не про текст отчёта.

ВЕРДИКТ — кодом (`first` / `same` / `grew` / `shrank` / `shift`), слова берёт словарь:
код видно в журнале, фразу — в окне и в речи существа.

ДНЕВНИК (`data/diary.md`) — короткая СВОЯ запись о своём мире, строка на изменение.
Это не пересказ промпта и не отчёт миссии: «30.09 15:12 · peers · появилось 2, ушло 1 ·
+router: listen tcp 58018». Такой дневник читается глазами и попадает в промпт мысли —
именно он даёт мозгу живой материал вместо «распределено 6 нейронов из 302».
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from synapse import config, i18n, leads, life as life_mod

WORLD = config.DATA / "world.json"
DIARY = config.DATA / "diary.md"

#: Сколько областей помним (старые уходят первыми) и сколько строк дневника держим.
KEEP_SCOPES = 200
KEEP_DIARY = 400


def _file(path: str | Path | None = None) -> Path:
    return Path(path) if path else WORLD


def _diary_file(path: str | Path | None = None) -> Path:
    return Path(path) if path else DIARY


#: Память файла по отпечатку (mtime, размер): мысль зовёт brief() каждый такт, а при
#: потоке такт идёт несколько раз в секунду — читать JSON с диска каждый раз незачем.
_CACHE: dict[str, tuple] = {}


def _load(path: str | Path | None = None) -> dict:
    p = _file(path)
    if path is None:                       # кэш только для живого файла, не для проб
        try:
            st = p.stat()
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            key = (0, 0)
        hit = _CACHE.get(str(p))
        if hit is not None and hit[0] == key:
            return hit[1]
    base = {"at": None, "scopes": {}, "last": None}
    if p.exists():
        try:
            disk = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(disk, dict):
                base.update(disk)
        except (OSError, ValueError):
            pass
    if not isinstance(base.get("scopes"), dict):
        base["scopes"] = {}
    if path is None:
        _CACHE[str(p)] = (key, base)
    return base


def _save(state: dict, path: str | Path | None = None) -> None:
    p = _file(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)                     # половину файла никто не прочтёт


def _short(fact: str, limit: int = 56) -> str:
    """Факт короткой строкой: дневник читают глазами, а не глазами робота.

    `::ffff:` (адрес IPv4, записанный как IPv6) убираем: соединение с VPS приходит
    как `in tcp ::ffff:203.0.113.10:443 <- ::ffff:162.158.190.118`, и на это уходит
    полстроки, а оператору и модели нужен адрес. Живая проба 30.09.2026 (22:35).
    """
    text = " ".join(str(fact or "").split()).replace("::ffff:", "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _when(at: float | None) -> str:
    """Когда это было: сегодня — «15:12», другой день — «30.09 15:12»."""
    if not at:
        return i18n.t("dash")
    stamp = time.localtime(float(at))
    today = time.localtime()
    same_day = (stamp.tm_year, stamp.tm_yday) == (today.tm_year, today.tm_yday)
    fmt = "%H:%M" if same_day else "%d.%m %H:%M"
    return time.strftime(fmt, stamp)


def _weight(fact: str) -> int:
    """Насколько строка важна для показа: меньше — раньше.

    Зачем (v0.32.0). Живой вечер 30.09.2026: в «Мой мир» попадали ПЕРВЫЕ строки
    изменения, а первыми шли внутренние DNS-запросы роутера
    (`router: inner udp … -> 192.168.1.1:53`) — модель зацепилась за них и девять
    минут писала «ползу к DNS-шлюзу». Слушатель и входящее извне — это то, ради
    чего снимок и делался; внутренний шум идёт в конец.
    """
    s = f" {str(fact or '').lower()} "
    if " listen " in s:
        return 0                      # кто-то слушает — это открытая дверь
    if " in " in s:
        return 2                      # входят к нам — тоже событие
    if "cert" in s or "host:" in s:
        return 3
    if " out " in s:
        return 12                     # выходы: их много и они обычны
    if "inner" in s or ":53" in s or "127.0.0.1" in s:
        return 20                     # внутренний шум: не материал для мысли
    return 10


def _top(facts, limit: int) -> list[str]:
    """Самые важные факты изменения: порядок детерминированный (вес, потом строка)."""
    rows = [str(f) for f in (facts or []) if str(f).strip()]
    return sorted(rows, key=lambda f: (_weight(f), f))[:limit]


def items_text(change: dict, limit: int = 3) -> str:
    """Перечень изменений словами: «+router: listen tcp 58018, −203.0.113.10 open=22».

    Показываем не первые строки, а САМЫЕ ВАЖНЫЕ (`_top`): слушатель и входящее важнее
    внутреннего DNS-шума — иначе мысль цепляется за шум (живой вечер 30.09.2026).
    """
    change = change or {}
    added = [f"+{_short(f)}" for f in _top(change.get("added"), limit)]
    gone = [f"−{_short(f)}" for f in _top(change.get("gone"), limit)]
    out = added + gone
    rest = (len(change.get("added") or []) - limit) + (len(change.get("gone") or []) - limit)
    if rest > 0:
        out.append(i18n.t("world.items.more", n=rest))
    return ", ".join(out)


def _diary(change: dict, diary_path: str | Path | None = None) -> None:
    """Дописать строку в дневник. Дневник — память, а не журнал: строку не теряем."""
    when = _when(change.get("at"))
    kind = str(change.get("kind") or "")
    verdict = str(change.get("verdict") or "")
    facts = int(change.get("facts") or 0)
    if verdict == "first":
        line = i18n.t("world.diary.first", when=when, kind=kind, facts=facts)
    elif verdict == "same":
        line = i18n.t("world.diary.same", when=when, kind=kind, facts=facts)
    else:
        line = i18n.t("world.diary.line", when=when, kind=kind,
                      plus=change.get("plus", 0), minus=change.get("minus", 0),
                      what=items_text(change))
    p = _diary_file(diary_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    keep: list[str] = []
    if p.exists():
        try:
            keep = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            keep = []
    keep.append(line)
    if KEEP_DIARY > 0:
        keep = keep[-KEEP_DIARY:]
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def record(kind: str, rep: dict | None, params: dict | None = None,
           path: str | Path | None = None, diary_path: str | Path | None = None) -> dict:
    """Сравнить нынешний отчёт с прошлым снимком ЭТОЙ области.

    Возврат: {"scope", "kind", "at", "verdict", "added" (новые факты), "gone"
    (что ушло), "plus", "minus", "facts", "first_time"}.
    `verdict = "first"` — смотреть было не с чем, это первый взгляд.
    """
    scope = life_mod.scope_key(kind, params, rep)
    facts = sorted({str(f) for f in life_mod.facts_from(kind, rep or {}) if str(f).strip()})
    state = _load(path)
    scopes = state["scopes"]
    prev = scopes.get(scope) if isinstance(scopes.get(scope), dict) else None
    known = {str(f) for f in ((prev or {}).get("facts") or [])}
    now_set = set(facts)
    # ПЕРВЫЙ ВЗГЛЯД НЕ СЧИТАЕТ «ПРИШЛО» (v0.29.0). Иначе «стало иначе» означало бы
    # «посмотрел»: у первого снимка сравнивать не с чем, все факты были бы «новыми» —
    # и дневник кричал бы об изменении там, где ничего не менялось. Что существо
    # нашло в первый раз, говорит запас («нашла нового: N») — это его слой, не этот.
    added = sorted(now_set - known) if prev is not None else []
    gone = sorted(known - now_set) if prev is not None else []
    if prev is None:
        verdict = "first"
    elif added and gone:
        verdict = "shift"
    elif added:
        verdict = "grew"
    elif gone:
        verdict = "shrank"
    else:
        verdict = "same"
    now = time.time()
    scopes[scope] = {"facts": facts, "at": now, "count": len(facts),
                     "first_at": (prev or {}).get("first_at") or now}
    if len(scopes) > KEEP_SCOPES:                # сначала уходят давно не смотренное
        order = sorted(scopes, key=lambda k: float(scopes[k].get("at") or 0))
        for old in order[: len(scopes) - KEEP_SCOPES]:
            scopes.pop(old, None)
    change = {"scope": scope, "kind": kind, "at": now, "verdict": verdict,
              "added": added, "gone": gone, "plus": len(added), "minus": len(gone),
              "facts": len(facts), "first_time": prev is None}
    state["last"] = change
    state["at"] = now
    _save(state, path)
    _diary(change, diary_path)
    return change


def last(path: str | Path | None = None) -> dict:
    """Последнее изменение мира (или {} — если ещё ничего не смотрели)."""
    got = _load(path).get("last")
    return got if isinstance(got, dict) else {}


def brief(cfg: dict | None = None, path: str | Path | None = None) -> str:
    """Строка «мой мир» для речи и промпта мысли (M5).

    Здесь не «отчёт», а ответ на вопрос «что у меня в мире происходит»: когда смотрела,
    что изменилось, сколько целей открыто. Именно это делает мысль дневником, а не
    эхом промпта: 1.5b получает живой материал — 304 соединения, 2 необычных слушателя,
    запас 0.85, — и ей есть что сказать.
    """
    change = last(path)
    st = leads.stats()
    if not change:
        return i18n.t("world.brief.none")
    items = items_text(change)
    return i18n.t("world.brief", kind=str(change.get("kind") or ""),
                  when=_when(change.get("at")),
                  plus=int(change.get("plus") or 0), minus=int(change.get("minus") or 0),
                  items=(i18n.t("world.items", items=items) if items else ""),
                  open=st["open"], mine=st["mine"])


def diary_tail(lines: int = 40, path: str | Path | None = None) -> list[str]:
    """Хвост дневника — для окна и для пробы живьём."""
    p = _diary_file(path)
    if not p.exists():
        return []
    try:
        rows = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return rows[-max(1, int(lines)):]
