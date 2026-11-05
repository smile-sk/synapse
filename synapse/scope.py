"""Дверь в сеть: список сетей задаёт ОПЕРАТОР, дверь его слушает.

Слово Стаса 30.09.2026: «любые сети должны быть». До этого публичный адрес
отбивался по УСТРОЙСТВУ (`is_private`) — настройкой разрешить его было нельзя
вообще, и это была ложная вывеска: в конфиге лежал список сетей, а решал не он.
Теперь решает ровно `net.allowed_cidrs` (по умолчанию `0.0.0.0/0` — любые).

Это тот же принцип, что exec_guard в AI_UI: у организма есть права, и они
проверяются ДО действия, а не после. Отказ по-прежнему ложится в журнал.

ПРИЧИНА ОТКАЗА — КОДОМ, а не готовой строкой. Нашла при разборе QA-отчёта
Ореста (25.09.2026): в русском окне стояло «дверь не пустит: 192.168.1.0/24:
254 addresses, inside the allowed networks» — английский хвост внутри русской
фразы, потому что текст рождался прямо здесь. Теперь проверка говорит кодом и
числами, а слова берёт словарь: в окне один язык, в журнале — код.
"""
from __future__ import annotations

import ipaddress
import socket

from synapse import audit, i18n

#: Коды причин. Каждый обязан быть в словаре (проверяет tests/test_qa_fixes.py):
#: дверь — то, что оператор видит чаще всего, полупереведённой она быть не может.
REASON_CODES = (
    "door.no_resolve", "door.inside", "door.any", "door.outside_nets",
    "door.cidr_bad", "door.cidr_outside", "door.cidr_big", "door.cidr_ok",
)


def say(code: str, **params) -> str:
    """Причина двери человеческими словами, на языке интерфейса."""
    return i18n.t(code, **params)


def resolve(host: str) -> str | None:
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


def is_private(ip: str) -> bool:
    """Приватный ли адрес. В двери это больше НЕ решает (решает список сетей),
    но этим пользуется net_scan.local_cidr — «какая сеть моя»."""
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def _allowed(net: dict) -> list:
    return [ipaddress.ip_network(c, strict=False) for c in net.get("allowed_cidrs", [])]


def check_code(host: str, cfg: dict) -> tuple[bool, str, dict]:
    """(можно, код причины, числа) — без слов: слова подставит вызывающий."""
    net = cfg.get("net", {})
    allowed = _allowed(net)
    ip = resolve(host)
    if ip is None:
        return False, "door.no_resolve", {"host": host}
    for a in allowed:
        if ipaddress.ip_address(ip) in a:
            if a.prefixlen == 0:        # 0.0.0.0/0 = оператор разрешил любые сети
                return True, "door.any", {"host": host, "ip": ip, "net": str(a)}
            return True, "door.inside", {"host": host, "ip": ip, "net": str(a)}
    return False, "door.outside_nets", {"host": host, "ip": ip,
                                        "allowed": ", ".join(str(a) for a in allowed)}


def check_cidr_code(cidr: str, cfg: dict) -> tuple[bool, str, dict]:
    """(можно, код, числа) для подсети целиком — проверка ДО развёртывания в хосты."""
    net = cfg.get("net", {})
    try:
        target = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        return False, "door.cidr_bad", {"cidr": cidr, "error": e}
    allowed = _allowed(net)
    if not any(target.subnet_of(a) for a in allowed if a.version == target.version):
        return False, "door.cidr_outside", {"cidr": cidr,
                                            "allowed": ", ".join(str(a) for a in allowed)}
    hosts = target.num_addresses - (2 if target.prefixlen < 31 else 0)
    limit = int(net.get("max_hosts", 256))
    if hosts > limit:
        return False, "door.cidr_big", {"cidr": cidr, "hosts": hosts, "limit": limit}
    return True, "door.cidr_ok", {"cidr": cidr, "hosts": hosts}


def check(host: str, cfg: dict) -> tuple[bool, str]:
    """Можно ли трогать этот адрес. Возвращает (можно, причина словами)."""
    ok, code, params = check_code(host, cfg)
    return ok, say(code, **params)


def check_cidr(cidr: str, cfg: dict) -> tuple[bool, str]:
    """Можно ли сканировать подсеть целиком (словами интерфейса)."""
    ok, code, params = check_cidr_code(cidr, cfg)
    return ok, say(code, **params)


def guard(host: str, cfg: dict) -> bool:
    """Проверка с записью в аудит — то, чем реально пользуется инструмент."""
    ok, code, params = check_code(host, cfg)
    if not ok:
        audit.log("scope.denied", target=host, reason=code, why=say(code, **params))
    return ok
