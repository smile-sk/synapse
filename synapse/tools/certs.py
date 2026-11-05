"""Инструмент: сроки сертификатов на своих службах с шифром.

ЗАЧЕМ: сертификат истекает МОЛЧА. Сначала браузер ругается, потом панель или
сайт просто перестаёт открываться — и об этом узнают от людей, а не от машины.
Это ровно та работа, которую живой организм делает лучше скрипта: он помнит
прошлый замер и видит, что срок стал короче.

ТОЛЬКО ЧТЕНИЕ: TCP-соединение и рукопожатие TLS. Ни одной записи на чужую машину.
"""
from __future__ import annotations

import base64
import os
import socket
import ssl
import tempfile
import time
from datetime import datetime, timezone

from pathlib import Path

from synapse import audit, config, i18n, scope
from synapse.tools import diff, report_io, net_scan


def _pem_from_der(der: bytes) -> str:
    b64 = base64.encodebytes(der).decode("ascii")
    return "-----BEGIN CERTIFICATE-----\n" + b64 + "-----END CERTIFICATE-----\n"


def _decode_cert(pem: str) -> dict:
    """Разобрать сертификат средствами стандартной библиотеки.

    ГРАБЛЯ (нашлась живьём, 24.09.2026): через tempfile.mkstemp() файл остаётся
    ОТКРЫТЫМ (возвращается и дескриптор), и повторная запись по тому же пути на
    Windows падала с PermissionError — наружу это выглядело как «сертификат не
    разобрать», хотя рукопожатие прошло. Поэтому файл создаём сами и сами убираем.
    """
    path = Path(tempfile.gettempdir()) / f"synapse_cert_{os.getpid()}_{int(time.time() * 1000)}.pem"
    path.write_text(pem, encoding="ascii")
    try:
        return ssl._ssl._test_decode_cert(str(path))          # noqa: SLF001 — штатного API нет
    finally:
        path.unlink(missing_ok=True)


def _when(text: str):
    """'Sep 24 16:26:52 2026 GMT' -> datetime (UTC)."""
    for fmt in ("%b %d %H:%M:%S %Y %Z", "%b %d %H:%M:%S %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def read_cert(host: str, port: int = 443, timeout: float = 5.0) -> dict:
    """Сертификат одной службы. Никогда не бросает: любая беда — в поле error."""
    row = {"host": host, "port": int(port), "ok": False, "error": None, "tls": "",
           "subject": "", "issuer": "", "not_after": "", "not_before": "",
           "days_left": None, "names": [], "handshake": False}
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as s:
                row["tls"] = s.version() or ""
                row["handshake"] = True
                der = s.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError) as e:
        row["error"] = f"{type(e).__name__}: {e}"
        return row
    if not der:
        row["error"] = i18n.t("err.certs.no_cert")
        return row
    try:
        info = _decode_cert(_pem_from_der(der))
    except Exception as e:                                    # noqa: BLE001
        row["error"] = i18n.t("err.certs.decode", reason=type(e).__name__)
        return row
    row["subject"] = _name_of(info.get("subject"))
    row["issuer"] = _name_of(info.get("issuer"))
    row["not_after"] = str(info.get("notAfter") or "")
    row["not_before"] = str(info.get("notBefore") or "")
    row["names"] = [v for k, v in (info.get("subjectAltName") or ()) if k == "DNS"][:8]
    until = _when(row["not_after"])
    if until is not None:
        row["days_left"] = (until - datetime.now(timezone.utc)).days
    row["ok"] = True
    audit.log("certs.read", host=host, port=int(port), tls=row["tls"], days_left=row["days_left"])
    return row


def _name_of(parts) -> str:
    """Разобрать имя из сертификата в человеческую строку.

    Форма данных неочевидна: это кортеж НАБОРОВ, а в каждом наборе — пары
    ((('commonName', 'NAS'),),). Проверено живьём на своём же сертификате.
    """
    d: dict = {}
    for rdn in parts or ():
        for pair in rdn if isinstance(rdn, (tuple, list)) else (rdn,):
            if isinstance(pair, (tuple, list)) and len(pair) == 2:
                d.setdefault(str(pair[0]), str(pair[1]))
    for key in ("commonName", "organizationName", "organizationalUnitName"):
        if d.get(key):
            return d[key]
    return ", ".join(f"{k}={v}" for k, v in list(d.items())[:3])[:80]


def targets(params: dict, cfg: dict) -> tuple[list[tuple[str, int]], str]:
    """Кого проверять: сказано словом или взято из последнего аудита."""
    ports = [int(p) for p in (params.get("ports") or cfg["net"].get("cert_ports", [443]))]
    if params.get("host"):
        return [(str(params["host"]), p) for p in ports], i18n.t("src.from_mission")
    rep = diff.latest_audit()
    if not rep:
        return [], i18n.t("err.no_audit")
    pairs = []
    for h in rep.get("hosts") or []:
        for p in h.get("open") or []:
            if int(p) in ports:
                pairs.append((str(h.get("ip")), int(p)))
    return pairs, (i18n.t("src.from_audit", cidr=rep.get("cidr") or "")
                if pairs else i18n.t("src.no_tls"))


def check(params: dict | None = None, mission: str = "", cfg: dict | None = None) -> dict:
    """Проверить сроки сертификатов и положить отчёт."""
    params = params or {}
    cfg = cfg or config.load()
    rep: dict = {"at": datetime.now().isoformat(timespec="seconds"), "kind": "certs",
                 "mission": mission, "targets": [], "expiring": [], "source": "", "error": None}
    pairs, why = targets(params, cfg)
    rep["source"] = why
    if not pairs:
        rep["error"] = why
        audit.log("certs.failed", mission=mission or None, why=why)
        return rep
    for host, port in pairs:
        if not scope.guard(host, cfg):
            rep["targets"].append({"host": host, "port": port, "ok": False, "error": i18n.t("err.door")})
            continue
        rep["targets"].append(read_cert(host, port))
    rep["expiring"] = [{"host": r["host"], "port": r["port"], "days_left": r["days_left"]}
                       for r in rep["targets"]
                       if r.get("days_left") is not None and r["days_left"] <= 30]
    if not any(r.get("handshake") for r in rep["targets"]):
        # Ошибка — только если рукопожатие не прошло НИГДЕ: разбор сертификата
        # в конкретной строке это не «не вышел», а честная причина по этой службе.
        rep["error"] = i18n.t("err.certs.no_tls")
    report_io.save(rep, mission=mission, kind="certs")
    audit.log("certs.done", mission=mission or None, checked=len(rep["targets"]),
              expiring=len(rep["expiring"]))
    return rep
