"""Инструмент: тест «куда есть доступ на запись» в СВОИХ общих папках.

ЕДИНСТВЕННЫЙ инструмент, который ПИШЕТ на другую машину. Поэтому у него особые
правила, и они в коде, а не в обещаниях:

  * дверь (scope) спрашивается ДО касания папки;
  * выключатель в настройках: net.allow_write_probe;
  * пишем ОДИН маленький файл-маркер, читаем его обратно и сразу удаляем;
  * каждая попытка — и успех, и отказ — идёт в журнал с полным путём.

Итог — не «дыра», а факт: эта папка пускает писать / пускает только читать /
не пускает вовсе. Это и есть ответ на вопрос «где может лежать копия».
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from synapse import audit, config, i18n, scope
from synapse.tools import diff, net_scan, report_io

BS = "\\"
RESERVED = ("IPC$", "PRINT$", "ADMIN$", "C$", "D$")


def probe_dir(base: str | Path, marker: str = "synapse_write_probe.txt",
              payload: str | None = None) -> dict:
    """Проверить ОДНУ папку: пишем маркер, читаем обратно, убираем.

    Работает с любым путём (в тестах — с временной папкой, в жизни — с UNC).
    """
    base = str(base)
    row = {"path": base, "state": "unknown", "detail": "", "entries": None}
    text = payload or f"Synapse write probe {datetime.now().isoformat(timespec='seconds')}\n"
    target = os.path.join(base, marker)
    try:
        row["entries"] = len(os.listdir(base))
    except FileNotFoundError as e:
        row["state"], row["detail"] = "missing", f"{type(e).__name__}: {e}"
        return row
    except PermissionError as e:
        row["state"], row["detail"] = "denied", f"{type(e).__name__}: {e}"
        return row
    except OSError as e:
        row["state"], row["detail"] = "denied", f"{type(e).__name__}: {e}"
        return row
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(text)
        with open(target, "r", encoding="utf-8") as f:
            back = f.read()
        row["state"] = "writable" if back == text else "writable_unverified"
        row["detail"] = "" if back == text else i18n.t("wp.unverified")
    except PermissionError as e:
        row["state"], row["detail"] = "read_only", f"{type(e).__name__}: {e}"
    except OSError as e:
        row["state"], row["detail"] = "denied", f"{type(e).__name__}: {e}"
    finally:
        try:
            if os.path.exists(target):
                os.remove(target)
                row["removed"] = True
        except OSError as e:
            row["removed"] = False
            row["detail"] = (row["detail"] + i18n.t("wp.not_removed", error=e)).strip(" |")
    return row


def probe_share(host: str, share: str, cfg: dict, mission: str = "",
                marker: str | None = None) -> dict:
    """Проверить одну общую папку хоста (UNC-путь). Дверь — до касания."""
    marker = marker or str(cfg["net"].get("write_marker") or "synapse_write_probe.txt")
    base = f"{BS}{BS}{host}{BS}{share}"
    if not cfg["net"].get("allow_write_probe", True):
        audit.log("write_probe.off", host=host, share=share)
        return {"path": base, "state": "off", "detail": i18n.t("wp.off")}
    if str(share).upper() in RESERVED:
        return {"path": base, "state": "refused", "detail": i18n.t("wp.reserved")}
    if not scope.guard(host, cfg):
        audit.log("write_probe.refused", host=host, share=share)
        return {"path": base, "state": "refused", "detail": i18n.t("err.door")}
    row = probe_dir(base, marker=marker)
    audit.log("write_probe.attempt", mission=mission or None, host=host, share=share,
              path=row["path"], state=row["state"], detail=row.get("detail") or None)
    return row


def probe(params: dict | None = None, mission: str = "", cfg: dict | None = None) -> dict:
    """Тест записи: по слову оператора или по последнему аудиту сети."""
    params = params or {}
    cfg = cfg or config.load()
    rep: dict = {"at": datetime.now().isoformat(timespec="seconds"), "kind": "write_probe",
                 "mission": mission, "shares": [], "source": "", "error": None}
    jobs: list[tuple[str, str]] = []
    if params.get("host"):
        host = str(params["host"])
        shares = [str(s) for s in (params.get("shares") or [])] or net_scan.smb_shares(host, cfg)
        jobs = [(host, s) for s in shares]
        rep["source"] = i18n.t("src.from_mission_host", host=host)
    else:
        last = diff.latest_audit()
        if not last:
            rep["error"] = i18n.t("err.no_audit")
            audit.log("write_probe.failed", mission=mission or None, why=rep["error"])
            return rep
        for h in last.get("hosts") or []:
            for s in h.get("shares") or []:
                jobs.append((str(h.get("ip")), str(s)))
        rep["source"] = i18n.t("src.from_audit", cidr=last.get("cidr") or "")
        if not jobs:
            rep["error"] = i18n.t("err.wp.no_shares")
            audit.log("write_probe.failed", mission=mission or None, why=rep["error"])
            return rep
    for host, share in jobs:
        rep["shares"].append(probe_share(host, share, cfg, mission=mission))
    report_io.save(rep, mission=mission, kind="write_probe")
    writable = [s for s in rep["shares"] if s.get("state") == "writable"]
    audit.log("write_probe.done", mission=mission or None, checked=len(rep["shares"]),
              writable=len(writable))
    return rep
