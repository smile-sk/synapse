"""Очередь миссий — то, чем оператор управляет организмом.

Панель (дашборд) кладёт задание сюда, Synapse берёт следующее на своём такте,
исполняет, пишет отчёт и помечает статус. Обычный JSON-файл: его одинаково
читают и панель, и организм, и человек в блокноте.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from synapse import config

QUEUE = config.DATA / "missions" / "queue.json"
STATUSES = ("queued", "proposed", "running", "done", "failed", "refused")
#: Статус «предложено мозгом» (v0.29.0, этап M6). Смысл ровно один: работа ждёт
#: СЛОВА ОПЕРАТОРА. take_next() берёт только queued, поэтому предложенное не
#: исполняется само — ни тактом, ни самозаданием. Подтверждение переводит запись
#: в queued (approve), снятие — обычное удаление кнопкой.
PROPOSED = "proposed"
def _ids_used_in_reports() -> set:
    """id, уже занятые ФАЙЛАМИ отчётов.

    Зачем: панель ищет отчёт миссии ПО id, поэтому новая миссия не должна получить
    id удалённой — иначе оператор увидит чужой отчёт (нашёл QA Орест 25.09.2026,
    разбирая генерацию id: «len(items) + 1» повторял уже занятый номер).
    """
    folder = config.DATA / "reports"
    out: set = set()
    if not folder.exists():
        return out
    for p in folder.glob("*.json"):
        parts = p.stem.split("-m-", 1)
        if len(parts) == 2:
            out.add("m-" + parts[1])
    return out


#: ИНСТРУМЕНТЫ — ОДИН список на весь организм: его знают агент (что умеет),
#: панель (что показать в списке) и консоль (что можно поставить в очередь).
#: Раньше списков было два, а add() не проверял НИЧЕГО — и моя проба с придуманным
#: именем команды легла в живую очередь 39 раз (нашла 25.09.2026 по жалобе Стаса
#: «такого_нет глаз мозолит»). Закон тот же, что у двери в сеть: проверка ДО записи.
KINDS = ("net_audit", "write_probe", "certs", "diff", "remote", "peers",
         "stimulus", "note", "ask")


def _path() -> Path:
    return QUEUE


def _read(p: Path | None = None) -> list[dict]:
    p = p or _path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _write(items: list[dict], p: Path | None = None) -> None:
    p = p or _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)                    # панель никогда не увидит половину файла


def add(kind: str, params: dict | None = None, note: str = "", path: Path | None = None,
        status: str = "queued", by: str = "") -> dict:
    """Поставить миссию в очередь. id читаемый: m-ГГГГММДД-ЧЧММСС."""
    # Дверь ДО записи: незнакомое имя в очередь не кладём — очередь это работа,
    # а не мусорник. Ошибка техническая, поэтому по-английски (правило языка).
    if kind not in KINDS:
        raise ValueError(f"no such tool: {kind} (have: {', '.join(KINDS)})")
    if status not in ("queued", PROPOSED):
        raise ValueError(f"cannot add a mission as: {status}")
    items = _read(path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # id обязан быть уникальным. Прежнее «len(items) + 1» повторяло уже занятый
    # номер, если хвост очереди удалили в ту же секунду (находка QA Ореста 25.09.2026).
    taken = {str(m.get("id")) for m in items} | _ids_used_in_reports()
    n = len(items) + 1
    while f"m-{stamp}-{n}" in taken:
        n += 1
    mid = f"m-{stamp}-{n}"
    rec = {"id": mid, "kind": kind, "params": params or {}, "note": note,
           "created": datetime.now().isoformat(timespec="seconds"), "status": status,
           "result": None, "finished": None}
    if by:
        rec["by"] = by          # кто предложил: "brain" ставится предложением мозга
    items.append(rec)
    _write(items, path)
    return rec


def all_missions(path: Path | None = None) -> list[dict]:
    return _read(path)


def queued(path: Path | None = None) -> list[dict]:
    return [m for m in _read(path) if m.get("status") == "queued"]


def proposed(path: Path | None = None) -> list[dict]:
    """Миссии, предложенные мозгом и ждущие слова оператора."""
    return [m for m in _read(path) if m.get("status") == PROPOSED]


def approve(mid: str, path: Path | None = None) -> dict | None:
    """Подтвердить предложенное: запись становится обычной работой в очереди.

    Возврат None значит «подтверждать нечего» — вызывающий говорит это словами.
    Такт после этого возьмёт миссию как любую другую (take_next берёт queued).
    """
    items = _read(path)
    for m in items:
        if str(m.get("id")) == str(mid):
            if m.get("status") != PROPOSED:
                return None
            m["status"] = "queued"
            m["approved"] = datetime.now().isoformat(timespec="seconds")
            _write(items, path)
            return dict(m)
    return None


def take_next(path: Path | None = None) -> dict | None:
    """Взять первую ждущую и пометить running (одна миссия за такт)."""
    items = _read(path)
    for m in items:
        if m.get("status") == "queued":
            m["status"] = "running"
            m["started"] = datetime.now().isoformat(timespec="seconds")
            _write(items, path)
            return dict(m)
    return None


def mark(mid: str, status: str, result: str | None = None, path: Path | None = None) -> dict | None:
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    items = _read(path)
    for m in items:
        if m.get("id") == mid:
            m["status"] = status
            m["result"] = result
            m["finished"] = datetime.now().isoformat(timespec="seconds")
            _write(items, path)
            return dict(m)
    return None


def status_of(mid: str, path: Path | None = None) -> str | None:
    """Текущий статус миссии или None, если её нет в очереди."""
    for m in _read(path):
        if m.get("id") == mid:
            return str(m.get("status") or "")
    return None


def mark_running(mid: str, status: str, result: str | None = None,
                 allow: tuple[str, ...] = ("running", "queued"),
                 path: Path | None = None) -> dict | None:
    """Записать ИТОГ миссии — если её не снял оператор.

    Находка QA Ореста 25.09.2026: оператор нажимал «Delete selected» для миссии,
    которая уже шла, ставилось refused — а здешний mark(done) по завершении
    затирал отмену, и выходило «снята», хотя работа дошла до конца.

    Правило простое и проверяется тестом: refused НЕ перезаписывается никогда.
    Пишем из рабочих состояний — running (обычный путь: take_next) и queued
    (когда такт позвали напрямую, не через очередь).
    Возврат: запись, если записали; None — если миссии нет или её сняли.
    """
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    items = _read(path)
    for m in items:
        if m.get("id") == mid:
            if m.get("status") not in allow:
                return None                       # снята оператором — не трогаем
            m["status"] = status
            m["result"] = result
            m["finished"] = datetime.now().isoformat(timespec="seconds")
            _write(items, path)
            return dict(m)
    return None


def remove(mid: str, path: Path | None = None) -> bool:
    """УБРАТЬ миссию из очереди по-настоящему.

    Слово Стаса 26.09.2026: «останавливать и удалять нужно — если на кнопке
    написано УДАЛИТЬ!». Прежний drop_mission только менял статус на refused:
    запись оставалась в очереди (и в истории), хотя кнопка обещала удаление, а
    идущая работа вообще продолжалась. Здесь — честное удаление записи; что
    именно было снято, остаётся строкой в журнале, а не в очереди.

    Возврат False значит «такой записи нет» — вызывающий говорит об этом прямо.
    """
    items = _read(path)
    left = [m for m in items if str(m.get("id")) != str(mid)]
    if len(left) == len(items):
        return False
    _write(left, path)
    return True


def add_and_get(kind: str, params: dict | None = None, note: str = "") -> dict:
    return add(kind, params, note)
