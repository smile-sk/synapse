"""Инструмент: что изменилось с прошлого аудита.

ЗАЧЕМ ЭТО СУЩЕСТВУ. Скрипт замечает разницу только если его запустят дважды и
кто-то сравнит два отчёта глазами. Существо живёт между запусками: оно помнит
прошлый замер и на следующем такте само говорит, что стало иначе.

Сравниваем два последних аудита сети: появившиеся хосты, пропавшие, новые и
закрытые порты, новые и исчезнувшие общие папки. Только чтение файлов.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from synapse import audit, config, i18n, reports
from synapse.tools import report_io

REPORTS = report_io.REPORTS


def _load(path: Path) -> dict:
    return reports.load(path)


def audits() -> list[Path]:
    """Аудиты сети на диске, от свежих к старым (только net_audit)."""
    if not REPORTS.exists():
        return []
    out = []
    for p in REPORTS.glob("*.json"):
        rep = _load(p)
        if rep.get("hosts") is not None:
            out.append((p.stat().st_mtime, p))
    return [p for _, p in sorted(out, key=lambda x: x[0], reverse=True)]


def latest_audit() -> dict:
    """Последний аудит сети (пустой словарь, если аудитов ещё не было)."""
    items = audits()
    return _load(items[0]) if items else {}


def compare(old: dict, new: dict) -> dict:
    """Разница между двумя аудитами (старый -> новый)."""
    rep: dict = {"at": datetime.now().isoformat(timespec="seconds"), "kind": "diff",
                 "error": None, "older_at": old.get("at"), "newer_at": new.get("at"),
                 "older_cidr": old.get("cidr"), "newer_cidr": new.get("cidr"),
                 "older_alive": old.get("alive_count"), "newer_alive": new.get("alive_count"),
                 "new_hosts": [], "gone_hosts": [], "changed": []}
    if not old or not new:
        rep["error"] = i18n.t("err.diff.need_two")
        return rep
    a = {str(h.get("ip")): h for h in old.get("hosts") or []}
    b = {str(h.get("ip")): h for h in new.get("hosts") or []}
    for ip in sorted(b, key=lambda s: [int(x) for x in s.split(".") if x.isdigit()] or [0]):
        if ip not in a:
            rep["new_hosts"].append({"ip": ip, "open": sorted(b[ip].get("open") or [])})
    for ip in sorted(a, key=lambda s: [int(x) for x in s.split(".") if x.isdigit()] or [0]):
        if ip not in b:
            rep["gone_hosts"].append({"ip": ip, "open": sorted(a[ip].get("open") or [])})
    for ip in sorted(set(a) & set(b)):
        ports_a, ports_b = set(a[ip].get("open") or []), set(b[ip].get("open") or [])
        sh_a, sh_b = set(a[ip].get("shares") or []), set(b[ip].get("shares") or [])
        row = {"ip": ip,
               "added_ports": sorted(ports_b - ports_a),
               "removed_ports": sorted(ports_a - ports_b),
               "shares_added": sorted(sh_b - sh_a),
               "shares_removed": sorted(sh_a - sh_b)}
        if any(row[k] for k in ("added_ports", "removed_ports", "shares_added", "shares_removed")):
            rep["changed"].append(row)
    return rep


def compare_latest(params: dict | None = None, mission: str = "", cfg: dict | None = None) -> dict:
    """Сравнить два последних аудита и положить отчёт."""
    params = params or {}
    items = audits()
    if params.get("old") and params.get("new"):
        rep = compare(_load(Path(params["old"])), _load(Path(params["new"])))
    elif len(items) >= 2:
        rep = compare(_load(items[1]), _load(items[0]))
    else:
        rep = {"at": datetime.now().isoformat(timespec="seconds"), "kind": "diff",
               "error": i18n.t("err.diff.have", count=len(items))}
    if not rep.get("error"):
        report_io.save(rep, mission=mission, kind="diff")
        audit.log("diff.done", mission=mission or None,
                  new=len(rep.get("new_hosts") or []), gone=len(rep.get("gone_hosts") or []),
                  changed=len(rep.get("changed") or []))
    else:
        audit.log("diff.failed", mission=mission or None, why=rep["error"])
    return rep
