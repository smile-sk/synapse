"""Инструмент №1: аудит своей сети (первые миссии Synapse).

Что делает: обходит СВОЮ подсеть, находит живые хосты, открытые порты и
баннеры служб. Это не «поиск дыры», а инвентаризация собственной поверхности:
где я доступен, где есть доступ на запись, что открыто зря.

Зависимостей нет — только стандартная библиотека (socket, subprocess, threading).
Каждый адрес проходит проверку ДВЕРИ (scope) до того, как его коснутся.
"""
from __future__ import annotations

import ipaddress
import json
import platform
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from synapse import audit, cancel, config, i18n, reports, scope

IS_WIN = platform.system().lower().startswith("win")
REPORTS = config.DATA / "reports"
#: Размер куска при обходе (v0.24.0): между кусками спрашиваем, не пора ли встать.
MAX_CHUNK_PORTS = 2048
MAX_CHUNK_HOSTS = 64


def _decode(raw: bytes | None) -> str:
    if not raw:
        return ""
    for enc in ("cp866", "cp1251", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def local_addresses() -> list[dict]:
    """Адреса этой машины с масками: ipconfig (Windows) / ip addr (Linux)."""
    out: list[dict] = []
    try:
        if IS_WIN:
            raw = subprocess.run(["ipconfig"], capture_output=True, timeout=15).stdout
            text = _decode(raw)
            ip = mask = None
            for line in text.splitlines():
                line = line.strip()
                m = re.search(r"IPv4[^:]*:\s*([0-9.]+)", line)
                if m:
                    ip = m.group(1)
                m = re.search(r"(Маска подсети|Subnet Mask)[^:]*:\s*([0-9.]+)", line)
                if m and ip:
                    mask = m.group(2)
                    out.append({"ip": ip, "mask": mask})
                    ip = mask = None
        else:
            raw = subprocess.run(["ip", "-o", "-4", "addr"], capture_output=True, timeout=15).stdout
            for line in _decode(raw).splitlines():
                m = re.search(r"inet\s+([0-9.]+)/(\d+)", line)
                if m:
                    bits = int(m.group(2))
                    mask = str(ipaddress.IPv4Network(f"0.0.0.0/{bits}").netmask)
                    out.append({"ip": m.group(1), "mask": mask})
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def local_cidr() -> str | None:
    """Подсеть этой машины (первый приватный адрес)."""
    for a in local_addresses():
        try:
            net = ipaddress.IPv4Network(f"{a['ip']}/{a['mask']}", strict=False)
        except ValueError:
            continue
        if scope.is_private(a["ip"]) and net.num_addresses > 2:
            return str(net)
    return None


def ping(host: str, timeout_ms: int = 400) -> bool:
    cmd = (["ping", "-n", "1", "-w", str(max(timeout_ms, 800)), host] if IS_WIN
           else ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), host])
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=max(3, timeout_ms / 1000 + 3))
    except (OSError, subprocess.SubprocessError):
        return False
    return "TTL=" in _decode(r.stdout).upper() or "ttl=" in _decode(r.stdout)


def tcp_alive(host: str, ports: list[int] | None = None, timeout: float = 0.25) -> bool:
    """Живость, когда ping молчит (ICMP режут фаервол/VPN): пробуем TCP."""
    for p in (ports or [445, 139, 80, 22, 3389, 8080]):
        if _try_port(host, int(p), timeout):
            return True
    return False


def is_alive(host: str) -> bool:
    """Хост жив, если отозвался ping ИЛИ открыл порт: замер показал,
    что ping под нагрузкой врёт, а TCP-проба видит ту же машину."""
    return ping(host) or tcp_alive(host)


def sweep(cidr: str, cfg: dict, on_host=None, mission: str = "") -> list[str]:
    """Живые хосты подсети. Дверь проверяется ОДИН раз на подсеть.

    Идёт КУСКАМИ и спрашивает про остановку (v0.24.0): раньше список хостов
    отдавался пулу целиком, и прервать обход было нечем — оператор нажимал
    «Удалить», а прощупывание сети продолжалось.
    """
    ok, why = scope.check_cidr(cidr, cfg)
    audit.log("net.sweep", cidr=cidr, allowed=ok, why=why)
    if not ok:
        return []
    net = ipaddress.ip_network(cidr, strict=False)
    hosts = [str(h) for h in net.hosts()]
    workers = max(1, min(int(cfg["net"].get("workers", 64)), len(hosts) or 1))
    alive: list[str] = []
    for i in range(0, len(hosts), MAX_CHUNK_HOSTS):
        if mission and cancel.requested(mission):
            break
        chunk = hosts[i:i + MAX_CHUNK_HOSTS]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for host, up in zip(chunk, ex.map(lambda h: is_alive(h), chunk)):
                if up:
                    alive.append(host)
                    if on_host:
                        on_host(host)
    return alive


def scan_ports(host: str, ports: list[int], timeout: float = 0.35) -> dict[int, bool]:
    out: dict[int, bool] = {}
    for p in ports:
        try:
            with socket.create_connection((host, int(p)), timeout=timeout):
                out[int(p)] = True
        except OSError:
            out[int(p)] = False
    return out


def probe_ports(cfg: dict) -> tuple[int, int]:
    """Диапазон портов для обхода — ОТ и ДО (v0.22.0).

    Почему диапазон, а не список. Слово Стаса 26.09.2026: «я по прежнему вижу не
    диапазон портов… а строку Порты для проверки» и «ВСЕ порты для проверки, любые
    открытые». Списком служб «все порты» не выразить, а держать в настройках 65 535
    чисел — глупость. Старый ключ net.ports, если он ещё лежит в чьём-то конфиге,
    читается как ЕГО крайние значения: обновление никого не сужает молча.
    """
    net = cfg.get("net") or {}
    lo, hi = net.get("port_from"), net.get("port_to")
    if lo is None or hi is None:
        old = []
        for p in (net.get("ports") or []):
            try:
                old.append(int(p))
            except (TypeError, ValueError):
                continue
        if old:
            lo, hi = min(old), max(old)
    try:
        lo = 1 if lo is None else int(lo)
        hi = 65535 if hi is None else int(hi)
    except (TypeError, ValueError):
        lo, hi = 1, 1024
    lo, hi = max(1, min(65535, lo)), max(1, min(65535, hi))
    return (lo, hi) if lo <= hi else (hi, lo)


def scan_ports_fast(host: str, ports: list[int], timeout: float = 0.35, workers: int = 256,
                    mission: str = "") -> dict[int, bool]:
    """Обход портов — КУСКАМИ, с проверкой остановки между ними (v0.24.0).

    Почему кусками: 65 535 портов при 256 потоках и таймауте 0.35 с — это около
    полутора минут на ОДИН хост. Прежний вариант отдавал весь список пулу сразу,
    поэтому «Удалить» не останавливало ничего: оператор видел «running» ещё
    минуты. Куском в MAX_CHUNK_PORTS останавливаемся за доли секунды.
    """
    found: dict[int, bool] = {}
    for i in range(0, len(ports), MAX_CHUNK_PORTS):
        if mission and cancel.requested(mission):
            break
        chunk = [int(p) for p in ports[i:i + MAX_CHUNK_PORTS]]
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(chunk) or 1))) as ex:
            found.update(dict(ex.map(lambda p: (p, _try_port(host, p, timeout)), chunk)))
    return found


def _try_port(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_open(host: str, port: int, timeout: float = 0.35) -> bool:
    """Открыт ли порт — ПУБЛИЧНОЕ имя: им пользуются инструменты (remote)."""
    return _try_port(host, port, timeout)


def banner(host: str, port: int, timeout: float = 0.6) -> str:
    """Первые байты службы: часто называют себя сами (SSH, HTTP, SMB)."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            if port in (80, 8080, 8000):
                s.sendall(b"HEAD / HTTP/1.0\r\nHost: %b\r\n\r\n" % host.encode())
            s.settimeout(timeout)
            data = s.recv(160)
    except OSError:
        return ""
    text = _decode(data)
    return " ".join(text.split())[:120]


def smb_shares(host: str, cfg: dict) -> list[str]:
    """Имена общих папок (Windows: net view). Права НЕ пробуем — только список."""
    if not scope.guard(host, cfg) or not IS_WIN:
        return []
    try:
        r = subprocess.run(["net", "view", f"\\\\{host}"], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    text = _decode(r.stdout)
    names = []
    for line in text.splitlines():
        m = re.match(r"^\s*(\S+)\s+(Диск|Disk|\\\\)?\s*$", line)
        if m and not m.group(1).startswith(("Общий", "Share", "Команда", "The")):
            names.append(m.group(1))
    return names[:20]


def audit_network(cidr: str | None = None, mission: str = "", cfg: dict | None = None) -> dict:
    """Полный аудит подсети: живые хосты, порты, баннеры, шары. Пишет отчёт."""
    cfg = cfg or config.load()
    cidr = cidr or local_cidr()
    rep: dict = {"at": datetime.now().isoformat(timespec="seconds"), "cidr": cidr,
                 "mission": mission, "hosts": [], "error": None}
    if not cidr:
        rep["error"] = i18n.t("err.no_subnet")
        audit.log("net.audit.failed", mission=mission, why=rep["error"])
        return rep
    ok, why = scope.check_cidr(cidr, cfg)
    if not ok:
        rep["error"] = why
        audit.log("net.audit.refused", mission=mission, cidr=cidr, why=why)
        return rep

    port_from, port_to = probe_ports(cfg)
    ports = list(range(port_from, port_to + 1))
    # Потоков на обход — из настроек (замер 26.09.2026: 256 потоков = 1.7 мс на порт,
    # 16 потоков = 22 мс, то есть полный диапазон занял бы на хост часы).
    port_workers = int(cfg["net"].get("port_workers", 256) or 256)
    timeout = float(cfg["net"].get("connect_timeout", 0.35))
    target = ipaddress.ip_network(cidr, strict=False)
    rep["scanned"] = target.num_addresses - (2 if target.prefixlen < 31 else 0)
    rep["port_from"], rep["port_to"] = port_from, port_to
    alive = sweep(cidr, cfg, mission=mission)
    rep["alive_count"] = len(alive)
    stopped = False
    for host in alive:
        if mission and cancel.requested(mission):
            # ОСТАНОВКА ПО СЛОВУ ОПЕРАТОРА (v0.24.0): отдаём ЧАСТИЧНЫЙ отчёт —
            # что успели, то и есть правда; молча выйти и не сказать нельзя.
            stopped = True
            audit.log("net.audit.stopped", mission=mission, hosts_done=len(rep["hosts"]))
            break
        found = scan_ports_fast(host, ports, timeout, port_workers, mission=mission)
        open_ports = [p for p, is_open in found.items() if is_open]
        row = {"ip": host, "open": open_ports, "banners": {},
               "shares": smb_shares(host, cfg) if 445 in open_ports else []}
        for p in open_ports:
            if p in (22, 80, 443, 445, 8080):
                row["banners"][str(p)] = banner(host, p)
        rep["hosts"].append(row)
        audit.log("net.host", mission=mission, ip=host, open=open_ports, shares=row["shares"])

    # Остановку могли попросить и ДО старта (пустая подсеть, мгновенное «Удалить»):
    # тогда цикла не было вовсе, а отчёт обязан сказать правду, а не «всё хорошо».
    rep["stopped"] = bool(stopped or (mission and cancel.requested(mission)))
    REPORTS.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{mission or 'audit'}.json"
    path = REPORTS / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    rep["report"] = str(path)
    # рядом с машинным JSON — человеческий .txt: отчёт читает человек, а не только код
    rep["report_text"] = str(reports.save_text(rep, path))
    audit.log("net.audit.done", mission=mission, cidr=cidr, alive=len(alive), report=str(path))
    return rep
