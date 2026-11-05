"""Общая запись отчётов: машинный JSON + человеческий .txt рядом.

Отчётов стало четыре вида (net_audit, certs, write_probe, diff), и ложатся они
одинаково: одна схема имени, одна атомарная запись, одна человеческая версия.
Держим это в одном месте, а не копией в каждом инструменте.
"""
from __future__ import annotations

import json
from datetime import datetime

from synapse import audit, config, reports

REPORTS = config.DATA / "reports"


def save(rep: dict, mission: str = "", kind: str = "") -> dict:
    """Положить отчёт на диск. Возвращает тот же словарь с путями внутри."""
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{mission or kind or rep.get('kind') or 'report'}.json"
    path = REPORTS / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    rep["report"] = str(path)
    rep["report_text"] = str(reports.save_text(rep, path))
    audit.log(f"{rep.get('kind') or 'report'}.saved", mission=mission or None, report=str(path))
    return rep
