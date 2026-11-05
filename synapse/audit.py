"""Аудит Synapse: кто, что, когда, чем кончилось.

Пишем ДВА вида: машиночитаемый JSONL (data/logs/audit.jsonl) и человеческую
строку (data/logs/synapse.log). Ротация — своим размером, чтобы организм,
живущий неделями, не съел диск.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from synapse import config

_LOCK = threading.Lock()
LOG_DIR = config.DATA / "logs"
JSONL = LOG_DIR / "audit.jsonl"
PLAIN = LOG_DIR / "synapse.log"


def _rotate(path: Path, max_bytes: int, keep: int) -> None:
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return
    except OSError:
        return
    for i in range(keep - 1, 0, -1):
        src, dst = path.with_suffix(path.suffix + f".{i}"), path.with_suffix(path.suffix + f".{i + 1}")
        if src.exists():
            try:
                os.replace(src, dst)
            except OSError:
                pass
    try:
        os.replace(path, path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass


#: Строка-разделитель («---», «===», «```») — не мысль и не запись.
_SEPARATORS = re.compile(r"^[\s\-=_*#~`·•—]+$")


def _flat(value, cap: int = 160) -> str:
    """Поле — ОДНОЙ строкой: перевод строки в журнале РВЁТ запись.

    Найдено 26.09.2026 по жалобе «ничего не пишет в логи». Мозг ответил 2000 знаков
    разделителей «---», поле text=... разорвало человеческий журнал: из 1264 строк
    641 оказалась мусором (посчитано, не на глаз), а запись выглядела состоящей из
    одних «---». В журнале действий одна запись ОБЯЗАНА быть одной строкой — иначе он
    перестаёт читаться и парситься.
    """
    s = "" if value is None else str(value)
    for bad in ("\r\n", "\r", "\n"):
        s = s.replace(bad, " ⏎ ")
    s = " ".join(s.split())
    if cap and len(s) > cap:
        s = s[:cap] + "…"
    return s


def log(event: str, **fields) -> dict:
    """Одна запись аудита. Возвращает то, что записано (для тестов и отчётов)."""
    cfg = config.cached().get("audit", {})
    max_bytes = int(cfg.get("max_bytes", 2000000))
    keep = int(cfg.get("keep", 3))
    rec = {"at": datetime.now().isoformat(timespec="seconds"), "event": event}
    rec.update({k: v for k, v in fields.items() if v is not None})
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False)
    with _LOCK:
        _rotate(JSONL, max_bytes, keep)
        with JSONL.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        _rotate(PLAIN, max_bytes, keep)
        with PLAIN.open("a", encoding="utf-8") as f:
            f.write(f"{rec['at']} {event} " +
                    " ".join(f"{k}={_flat(v)}" for k, v in rec.items()
                             if k not in ("at", "event")) + "\n")
    return rec


_TAIL_CACHE: dict[int, tuple] = {}


def tail(n: int = 20) -> list[dict]:
    """Последние записи аудита (для панели и для памяти организма).

    Панель спрашивает журнал каждые 8 секунд, а журнал растёт до 2 МБ — читать его
    целиком ради 80 строк незачем. Поэтому держим ответ в памяти, пока файл не
    изменился (отпечаток mtime+размер).
    """
    if not JSONL.exists():
        return []
    try:
        st = JSONL.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = (0, 0)
    hit = _TAIL_CACHE.get(n)
    if hit is not None and hit[0] == key:
        return list(hit[1])
    out = []
    for line in JSONL.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    _TAIL_CACHE[n] = (key, out)
    return list(out)


def thoughts_log() -> Path:
    """Путь к отдельному логу внутренней речи (💭).

    ФУНКЦИЯ, а не константа: temp_live() переназначает audit.LOG_DIR, и путь обязан
    считаться от ТЕКУЩЕЙ папки журналов — иначе тесты писали бы мысли в живые data/.
    """
    return LOG_DIR / "thoughts.log"


def thought(text: str) -> None:
    """Внутренняя речь (💭) — ОТДЕЛЬНЫЙ канал, не журнал действий.

    Слово Стаса 25.09.2026: «дай ему отдельный канал внутренней речи — как у Миланы».
    Мысль пишется в свой файл (как милана_мышление.лог) И в журнал событием brain.inner:
    так «думает» и «говорит» становятся разными вещами, и поток можно читать отдельно.
    """
    line = _flat(text, 400)
    if not line:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with thoughts_log().open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    log("brain.inner", text=line)


def thoughts_tail(n: int = 400) -> list[str]:
    """Последние строки внутренней речи — для вкладки «Мысли» в окне."""
    if not thoughts_log().exists():
        return []
    try:
        lines = thoughts_log().read_text(encoding="utf-8", errors="replace").splitlines()
        # ЧИСТО В ГЛАЗАХ ОПЕРАТОРА (v0.20.2): прошлые записи могли быть испорчены
        # разделителями и пустыми строками — во вкладке «Мысли» это выглядело как
        # пустота или каша. Пустое и «---» мыслью не считаем.
        keep = [s for s in (l.strip() for l in lines) if s and not _SEPARATORS.match(s)]
        return keep[-n:]
    except OSError:
        return []
