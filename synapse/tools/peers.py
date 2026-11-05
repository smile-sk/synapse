"""Инструмент: взгляд на соединения — кто с кем, откуда и не чужое ли (миссия peers).

Слово Стаса 30.09.2026: «пускай смотрит и анализирует соединения на моем роутере и
анализирует их ВСТРЕЧНО — что это, откуда, вредное ли, потом тоже самое по соединениям
на нашем VPS — ну только все конечно нужно с делать ДЛЯ ЧТЕНИЯ — просто как способ найти
"енергию" — пусть живет и работает».

ПОЧЕМУ ОТДЕЛЬНЫЙ ИНСТРУМЕНТ, А НЕ `remote`. `remote` умеет ВЫПОЛНИТЬ команду на чужой
машине: для взгляда это лишние права, и объяснять «мы только смотрим» словами пришлось бы
вечно. Здесь узкий путь:

  * команды лежат БЕЛЫМ СПИСКОМ В КОДЕ (`READ_ONLY` по роду узла) — из конфига приходят
    только имя, адрес и род; вписать свою команду оператору НЕКУДА. Это устройство, а не
    обещание, и оно проверяется тестом;
  * дверь (`scope.guard`) спрашивается ДО касания каждого узла;
  * логин и пароль приходят из файла секретов (`synapse/creds.py`) и в отчёт или журнал
    НЕ ПОПАДАЮТ никогда — в отчёте только «кто ходил»;
  * отказ не прячем: нет доступа — так и сказано строкой отчёта.

ВСТРЕЧНЫЙ РАЗБОР — то, ради чего это существует. Один снимок ещё не картина; картина
появляется, когда снимки сопоставляются: адрес, видимый С ДВУХ СТОРОН (роутер и VPS);
вход на НЕОБЫЧНЫЙ порт; слушатель, которого мы не знаем; внешний партнёр, к которому
свои лезут постоянно. Каждой строке выносится вердикт СЛОВАМИ (`watch.v.*`), а то, что
стоит внимания, становится ЦЕЛЬЮ (`synapse/leads.py`) — чтобы существо могло взять хост
или порт как цель следующей миссии, а не просто «посмотреть и забыть».

ЧЕСТНОСТЬ ФАКТА. Соединения живут минутами, и одно и то же соединение в двух снимках
отличается ЭФЕМЕРНЫМ портом клиента. Если писать в факты строку как есть, каждый снимок
приносил бы «всё новое» — и существо жрало бы воздух. Поэтому факт — это ОТНОШЕНИЕ:
кто (адрес, без случайного порта) ↔ к кому (адрес и СЛУЖБА-порт) каким протоколом, плюс
«слушает порт N». Повтор даёт те же строки и не кормит.
"""
from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
from datetime import datetime

from synapse import audit, cancel, config, creds, i18n, reports, scope
from synapse.tools import net_scan, report_io

#: ЧТО МЫ ЧИТАЕМ НА УЗЛЕ. Один словарь на весь инструмент: род узла -> ключ -> команда.
#: Здесь только взгляд: ни правил, ни блокировок, ни записей, ни установок.
READ_ONLY: dict[str, dict[str, str]] = {
    "routeros": {
        "connections": "/ip firewall connection print without-paging",
        "addresses": "/ip address print terse without-paging",
    },
    "linux": {
        "connections": "ss -tunap",
        "addresses": "ip -brief address",
    },
}

#: Род узла по умолчанию — если в настройках его не назвали.
DEFAULT_OS = "linux"

#: Сколько строк соединений держим в отчёте (остальное считаем, но не храним).
MAX_ROWS = 500

#: Сколько строк отрисовываем в человеческом отчёте (дальше — «и ещё N»).
RENDER_ROWS = 40

#: С какого числа соединений к одному внешнему адресу считаем его «частым партнёром».
FREQUENT_PEER = 3

#: Состояния сокета, при которых это СЛУШАТЕЛЬ, а не сессия.
LISTEN_STATES = ("LISTEN", "UNCONN")

_IP4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_IP4_PORT = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3})(?::(\d+|\*))?$")
_PORT = re.compile(r":(\d+|\*)$")
_PROTO = re.compile(r"^[a-z][a-z0-9-]*$")
_TCP_STATES = ("established", "syn-sent", "syn-received", "fin-wait", "fin-wait-2",
               "close-wait", "time-wait", "last-ack", "closed", "listening")
_SS_PROTO = re.compile(r"^(tcp|udp|tcp6|udp6|raw|raw6|netlink|u_str|u_dgr)$")


# ---------------------------------------------------------------- чтение узла
def _paramiko():
    """paramiko — если он есть. Нет модуля — путь через ключи, без обмана."""
    try:
        import paramiko                                     # noqa: PLC0415
        return paramiko
    except ImportError:
        return None


def read_node(host: str, os_kind: str, cred: dict | None = None,
              timeout: float = 20) -> tuple[bool, dict[str, str], str]:
    """Прочитать узел: `{ключ: вывод}`. Возврат — (получилось, тексты, ошибка).

    Два пути, оба без записи на узле:
      1) paramiko с паролем или ключом (так входят и на роутер, и на VPS);
      2) системный `ssh` по ключам (BatchMode) — если пароля нет.
    Из конфига команда прийти не может: берём её из `READ_ONLY` по роду узла.
    """
    cmds = READ_ONLY.get(os_kind) or READ_ONLY[DEFAULT_OS]
    cred = cred or {}
    paramiko = _paramiko()
    texts: dict[str, str] = {}
    if paramiko is not None and (cred.get("password") or cred.get("key") or cred.get("user")):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            kwargs = {"username": cred.get("user") or None, "timeout": 8,
                      "allow_agent": False, "look_for_keys": False}
            if cred.get("password"):
                kwargs["password"] = cred["password"]
            if cred.get("key"):
                for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
                    try:
                        kwargs["pkey"] = cls.from_private_key_file(cred["key"])
                        break
                    except Exception:                       # noqa: BLE001 — пробуем следующий род ключа
                        continue
            client.connect(host, **kwargs)
            for key, command in cmds.items():
                _, stdout, stderr = client.exec_command(command, timeout=timeout)
                out = stdout.read().decode("utf-8", "replace")
                err = stderr.read().decode("utf-8", "replace")
                if not out.strip() and err.strip():
                    return False, texts, f"{key}: {err.strip()[:200]}"
                texts[key] = out
            return True, texts, ""
        except Exception as e:                              # noqa: BLE001 — отказ узла не роняет организм
            return False, texts, f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            try:
                client.close()
            except Exception:                               # noqa: BLE001
                pass
    exe = shutil.which("ssh")
    if not exe:
        return False, texts, "no ssh client"
    target = f"{cred.get('user')}@{host}" if cred.get("user") else host
    for key, command in cmds.items():
        try:
            r = subprocess.run([exe, "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
                                "-o", "StrictHostKeyChecking=accept-new",
                                target, command],
                               capture_output=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as e:
            return False, texts, f"{type(e).__name__}: {str(e)[:160]}"
        out = net_scan._decode((r.stdout or b"") + (r.stderr or b""))
        if r.returncode != 0 and not out.strip():
            return False, texts, out.strip()[:200] or f"rc={r.returncode}"
        if r.returncode == 255:
            return False, texts, out.strip()[:200]
        texts[key] = out
    return True, texts, ""


# ---------------------------------------------------------------- разбор вывода
def parse_routeros_connections(text: str) -> tuple[list[dict], int]:
    """Соединения MikroTik. Возврат — (строки, сколько строк не разобрали).

    Формат таблицы RouterOS (проверено живым выводом 30.09.2026):

        #        PROTOCOL   SRC-ADDRESS          DST-ADDRESS           TCP-STATE
        1 SACFs  tcp        192.168.1.21:54240  142.251.98.100:443    established

    Флаги бывают и в два слова («0 S C  ipsec-esp …»), поэтому разбираем не по
    фиксированным столбцам: ищем ДВА адреса (первый — источник, второй — приёмник),
    протокол — последнее нижнерегистровое слово перед первым адресом, состояние —
    слово после второго адреса, если оно из известных.
    """
    rows, skipped = [], 0
    for line in (text or "").splitlines():
        t = line.split()
        if len(t) < 5 or not t[0].isdigit():
            continue
        addrs = [i for i, tok in enumerate(t) if _IP4_PORT.match(tok)]
        if len(addrs) < 2:
            skipped += 1                            # IPv6 и прочее считаем, но не разбираем
            continue
        i, j = addrs[0], addrs[1]
        mid = t[1:i]
        proto = next((tok for tok in reversed(mid) if _PROTO.fullmatch(tok)), "")
        flags = "".join(tok for tok in mid if tok != proto)
        state = t[j + 1].lower() if j + 1 < len(t) else ""
        src_ip, src_port = split_addr(t[i])
        dst_ip, dst_port = split_addr(t[j])
        rows.append({"view": "router", "proto": proto, "client": src_ip, "client_port": src_port,
                     "server": dst_ip, "server_port": dst_port,
                     "state": state if state in _TCP_STATES else "", "flags": flags,
                     "raw": " ".join(t)})
    return rows, skipped


def parse_routeros_addresses(text: str) -> list[str]:
    """Свои сети роутера из `/ip address print terse`: ['192.168.1.1/24', …].

    Зачем: чтобы понимать, ЧТО здесь «своё». Список берём у самого узла, а не
    только из настроек, — тогда «своё» не разъедется с живой сетью.
    """
    out = []
    for line in (text or "").splitlines():
        m = re.search(r"address=([0-9./a-fA-F:]+)", line)
        if m:
            out.append(m.group(1))
    return out


def parse_ss(text: str) -> tuple[list[dict], int]:
    """Сокеты Linux из `ss -tunap`. Возврат — (строки, сколько строк не разобрали).

    Место (local) — НАША сторона, peer — чужая. У слушателя peer пустой (`0.0.0.0:*`).
    """
    rows, skipped = [], 0
    for line in (text or "").splitlines():
        t = line.split()
        if len(t) < 6 or not _SS_PROTO.match(t[0]):
            if line.strip() and not line.lower().startswith(("netid", "state", "recv-q")):
                skipped += 1
            continue
        proto, state, local, peer = t[0], t[1], t[4], t[5]
        proc = " ".join(t[6:])[:160] if len(t) > 6 else ""
        my_ip, my_port = split_addr(local)
        peer_ip, peer_port = split_addr(peer)
        rows.append({"view": "host", "proto": proto.replace("6", ""), "state": state,
                     "local": local,
                     "peer": peer, "client": peer_ip, "client_port": peer_port,
                     "server": my_ip, "server_port": my_port, "proc": proc,
                     "raw": " ".join(t)})
    return rows, skipped


def parse_ip_brief(text: str) -> list[str]:
    """Свои адреса Linux: 'eth0 UP 203.0.113.10/22' -> ['203.0.113.10/22']."""
    out = []
    for line in (text or "").splitlines():
        for tok in line.split():
            if "/" in tok and re.match(r"^[0-9a-fA-F:.]+/\d+$", tok):
                out.append(tok)
    return out


def split_addr(text: str) -> tuple[str, int | None]:
    """'192.168.1.21:54240' -> ('192.168.1.21', 54240); '0.0.0.0:*' -> ('0.0.0.0', None)."""
    s = str(text or "").strip()
    if s.startswith("[") and "]" in s:                       # [::]:22
        ip, _, rest = s[1:].partition("]")
        port = rest.lstrip(":")
        return ip, int(port) if port.isdigit() else None
    m = _IP4_PORT.match(s)
    if m:
        ip, port = m.group(1), m.group(2)
        return ip, int(port) if port and port.isdigit() else None
    m = _PORT.search(s)
    if m:
        port = m.group(1)
        return s[: m.start()], int(port) if port.isdigit() else None
    return s, None


# ---------------------------------------------------------------- разбор смысла
def _mine(ip: str, cidrs: list[str]) -> bool:
    """Наш ли адрес — по списку сетей (свои сети узла + сказанные оператором)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for c in cidrs:
        try:
            net = ipaddress.ip_network(c, strict=False)
        except ValueError:
            continue
        if addr.version == net.version and addr in net:
            return True
    return False


def _service(port: int | None) -> tuple[bool, str]:
    """(известна ли служба, её имя) — по общей таблице портов проекта."""
    if not port:
        return False, ""
    if port in reports.KNOWN_PORTS:
        return True, i18n.t(f"svc.{port}")
    return False, ""


def _private(ip: str) -> bool:
    """Адрес из своей (домашней) сети, а не публичный."""
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def classify(rows: list[dict], cidrs: list[str]) -> list[dict]:
    """Вынести вердикт каждой строке: направление, своё/чужое, служба, «стоит внимания».

    НАПРАВЛЕНИЕ — не догадка, а разная арифметика для двух родов данных:

      * роутер даёт СОЕДИНЕНИЕ (кто начал): источник наш — значит выход, источник
        чужой — значит к нам входят. Порт тут не при чём: 443 бывает и входом, и
        выходом, и по порту направление не угадывается;
      * Linux (`ss`) даёт СВОЙ СОКЕТ: где у нас порт службы (занятый, известный) —
        вход, где у нас случайный порт — выход. Здесь порт как раз и есть признак.

    Смешать эти два правила нельзя: именно так 30.09.2026 вышло, что наши выходы
    на 443 были подписаны «кто входит к нам» — и отчёт врал.

    Вердикт — код (`watch.v.*`-подобный), слова берёт словарь. Здесь же рождается
    ОТНОШЕНИЕ (`rel`) — устойчивая строка факта: без случайного порта клиента, иначе
    каждый снимок приносил бы «всё новое» (см. шапку модуля).
    """
    out = []
    for row in rows:
        r = dict(row)
        proto = r.get("proto") or "?"
        # ИМЯ УЗЛА — В КАЖДЫЙ ФАКТ (v0.28.0): один и тот же порт на роутере и на VPS
        # это РАЗНЫЕ факты, и без имени узла они бы склеились в один.
        who = str(r.get("target") or r.get("host") or "?")
        wildcard = (not r.get("client")) or r.get("client") in ("0.0.0.0", "*", "::")
        if r.get("state") == "LISTEN" or (r.get("state") == "UNCONN" and wildcard):
            port = r.get("server_port")
            known, name = _service(port)
            # СЛУШАТЕЛЬ НА ПЕТЛЕ — НЕ ТРЕВОГА (правка 30.09.2026, живой отчёт). На VPS
            # тридцать слушателей, и половина сидит на 127.0.0.1: снаружи к ним не
            # постучаться, и называть их «необычными» значило бы заваливать оператора
            # шумом. Тревожно только то, что выставлено НАРУЖУ (0.0.0.0 / *) и не
            # опознано таблицей служб.
            # Наружу выставлено то, что сидит НЕ на петле. Смотрим ЛОКАЛЬНЫЙ адрес
            # сокета: у слушателя поле client — это peer (0.0.0.0/*), и судить по нему
            # значило бы объявлять тревогой вообще всё (поймано живым отчётом).
            local_ip = str(r.get("server") or r.get("local") or "")
            loop = (local_ip.startswith("127.") or local_ip.startswith("::1")
                    or local_ip.startswith("[::1"))
            exposed = not loop
            verdict = "listen_usual" if known else ("listen_unusual" if exposed
                                                   else "listen_local")
            r.update({"direction": "listen", "known": known, "service": name,
                      "other": None, "our": r.get("server"), "service_port": port,
                      "verdict": verdict,
                      "rel": f"{who}: listen {proto} {r.get('local') or port}"})
            out.append(r)
            continue
        if r.get("view") == "host":
            our_ip, our_port = r.get("server"), r.get("server_port")
            other_ip, other_port = r.get("client"), r.get("client_port")
            if _mine(other_ip or "", cidrs) and _private(other_ip) and _private(our_ip or ""):
                direction = "inner"
            elif our_port and (our_port in reports.KNOWN_PORTS or our_port <= 1024):
                direction = "in"
            else:
                direction = "out"
        else:
            client, server = r.get("client"), r.get("server")
            client_mine = _mine(client or "", cidrs)
            server_mine = _mine(server or "", cidrs)
            our_ip, our_port = ((client, r.get("client_port")) if client_mine
                                else (server, r.get("server_port")))
            other_ip, other_port = ((server, r.get("server_port")) if client_mine
                                    else (client, r.get("client_port")))
            if client_mine and server_mine and _private(client or "") and _private(server or ""):
                direction = "inner"
            elif client_mine:
                direction = "out"
            else:
                direction = "in"
        service_port = our_port if direction == "in" else other_port
        known, name = _service(service_port)
        if direction == "in":
            verdict = "in_usual" if known else "in_unusual"
            rel = f"{who}: in {proto} {our_ip}:{our_port} <- {other_ip}"
        elif direction == "inner":
            verdict = "lan_inner"
            rel = f"{who}: inner {proto} {our_ip} -> {other_ip}:{other_port}"
        else:
            verdict = "out"
            rel = f"{who}: out {proto} {our_ip} -> {other_ip}:{other_port}"
        r.update({"direction": direction, "known": known, "service": name,
                  "other": other_ip, "our": our_ip, "our_port": our_port,
                  "service_port": service_port, "verdict": verdict, "rel": rel})
        out.append(r)
    return out


def cross(rows: list[dict], targets: list[dict], cidrs: list[str]) -> tuple[list[dict], list[dict]]:
    """ВСТРЕЧНЫЙ РАЗБОР: что видно с двух сторон и кто наш частый внешний партнёр.

    Возврат — (обновлённые строки с пометкой «виден с двух сторон», что стоит внимания).
    Смысл именно встречного взгляда: адрес, замеченный и на роутере, и на VPS, — это
    уже не случайный гость, а отношение между нашими узлами или общий внешний партнёр.
    """
    seen_on: dict[str, set] = {}
    for r in rows:
        for ip in (r.get("client"), r.get("server")):
            if ip and not _mine(ip, cidrs) and _IP4.match(ip):
                seen_on.setdefault(ip, set()).add(r.get("target"))
    attention: list[dict] = []
    for ip, names in seen_on.items():
        if len(names) > 1:
            attention.append({"what": "both_sides", "peer": ip,
                              "targets": sorted(n for n in names if n)})
    # ЧАСТЫЙ ПАРТНЁР — ТОЛЬКО КОГДА ЭТО ЧТО-ТО ГОВОРИТ (правка по живому отчёту
    # 30.09.2026). Первый прогон объявил «стоит внимания» девять раз: Google push,
    # Telegram, NTP — то есть обычную жизнь дома. Тревожно другое: частые выходы на
    # порт, которого нет в таблице служб (там живут игры, P2P и всякое самодельное).
    # Осмысленное внимание лучше длинного списка: 443, 5228 и DNS — это не новость.
    counts: dict[str, int] = {}
    for r in rows:
        port = r.get("service_port")
        if r.get("direction") != "out" or not r.get("other") or _mine(r["other"], cidrs):
            continue
        if r.get("known"):
            continue
        counts[r["other"]] = counts.get(r["other"], 0) + 1
    ports_by_peer: dict[str, int] = {}
    for r in rows:
        if r.get("direction") == "out" and r.get("other") in counts:
            ports_by_peer[r["other"]] = r.get("service_port")
    for ip, n in counts.items():
        if n >= FREQUENT_PEER:
            attention.append({"what": "frequent", "peer": ip, "times": n,
                              "port": ports_by_peer.get(ip)})
    for r in rows:
        if r.get("verdict") in ("in_unusual", "listen_unusual"):
            attention.append({"what": r["verdict"], "target": r.get("target"),
                              "port": r.get("server_port"), "state": r.get("state")})
    peers_seen = {a["peer"] for a in attention if a.get("peer")}
    for r in rows:
        for ip in (r.get("client"), r.get("server"), r.get("other")):
            if ip and ip in peers_seen:
                r["both_sides"] = True
                if r.get("verdict") == "out":
                    r["verdict"] = "partner"
    return rows, attention


# ---------------------------------------------------------------- сама миссия
def _targets(cfg: dict) -> list[dict]:
    """Кого смотрим: из настроек `peers.targets`, по умолчанию — свой роутер и VPS."""
    block = cfg.get("peers") or {}
    items = block.get("targets")
    if not isinstance(items, list) or not items:
        items = [{"name": "router", "host": "", "os": "routeros"},
                 {"name": "vps", "host": "", "os": "linux"}]
    out = []
    for it in items:
        if not isinstance(it, dict) or not str(it.get("host") or "").strip():
            continue
        out.append({"name": str(it.get("name") or it.get("host")).strip(),
                    "host": str(it["host"]).strip(),
                    "os": str(it.get("os") or DEFAULT_OS).strip().lower()})
    return out


def _mine_cidrs(cfg: dict, own: list[str]) -> list[str]:
    """Свои сети: сказанные оператором + добытые у самих узлов (без повторов).

    Link-local (fe80::/64) выкидываем: это не «наша сеть», а служебный адрес
    интерфейса, и в списке он только засорял бы картину.
    """
    block = cfg.get("peers") or {}
    cidrs: list[str] = []
    for c in [*(block.get("mine_cidrs") or []), *own]:
        s = str(c)
        try:
            net = ipaddress.ip_network(s, strict=False)
        except ValueError:
            continue
        if net.version == 6 and net.is_link_local:
            continue
        if s not in cidrs:
            cidrs.append(s)
    return cidrs or ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]


def collect(params: dict | None = None, mission: str = "", cfg: dict | None = None) -> dict:
    """Миссия peers: снять соединения с узлов, разобрать встречно, назвать цели."""
    params = params or {}
    cfg = cfg or config.load()
    block = cfg.get("peers") or {}
    rep: dict = {"at": datetime.now().isoformat(timespec="seconds"), "kind": "peers",
                 "mission": mission, "targets": [], "rows": [], "watch": [], "leads": [],
                 "summary": {"connections": 0, "listeners": 0, "unusual": 0, "ok": 0,
                             "skipped": 0},
                 "error": None}
    if not bool(block.get("enabled", True)):
        rep["error"] = i18n.t("err.peers_off")
        audit.log("peers.off", mission=mission or None)
        return rep
    wanted = str(params.get("name") or "").strip()
    targets = [t for t in _targets(cfg) if not wanted or t["name"] == wanted]
    if not targets:
        rep["error"] = i18n.t("err.peers_none")
        audit.log("peers.failed", mission=mission or None, why="no targets")
        return rep
    timeout = float(block.get("ssh_timeout", 20) or 20)
    own: list[str] = []
    all_rows: list[dict] = []
    skipped_total = 0
    for t in targets:
        if mission and cancel.requested(mission):            # «Удалить» прекращает обход
            rep["error"] = i18n.t("ag.stopped_mission")
            break
        row = {"name": t["name"], "host": t["host"], "os": t["os"], "state": "ok",
               "error": None, "who": "", "connections": 0, "skipped": 0, "own": []}
        if not scope.guard(t["host"], cfg):                  # дверь — ДО касания
            row["state"], row["error"] = "denied", i18n.t("err.door")
            rep["targets"].append(row)
            audit.log("peers.denied", mission=mission or None, name=t["name"], host=t["host"])
            continue
        cred = creds.for_target(t["name"])
        row["who"] = (cred.get("user") or "ssh-key") + "@" + t["host"]
        ok, texts, err = read_node(t["host"], t["os"], cred, timeout)
        if not ok:
            row["state"] = "nologin"
            row["error"] = err or i18n.t("err.peers_nologin")
            rep["targets"].append(row)
            audit.log("peers.nologin", mission=mission or None, name=t["name"],
                      host=t["host"], why=(err or "")[:160])
            continue
        mine_here = (parse_routeros_addresses(texts.get("addresses", ""))
                     if t["os"] == "routeros" else parse_ip_brief(texts.get("addresses", "")))
        row["own"] = mine_here
        own += mine_here
        raw = texts.get("connections", "")
        if t["os"] == "routeros":
            found, skipped = parse_routeros_connections(raw)
        else:
            found, skipped = parse_ss(raw)
        row["connections"], row["skipped"] = len(found), skipped
        skipped_total += skipped
        for r in found:
            r["target"] = t["name"]
            r["host"] = t["host"]
        all_rows += found
        rep["targets"].append(row)
        audit.log("peers.read", mission=mission or None, name=t["name"], host=t["host"],
                  lines=len(raw.splitlines()), connections=len(found), skipped=skipped,
                  who=row["who"])

    cidrs = _mine_cidrs(cfg, own)
    all_rows = classify(all_rows, cidrs)
    all_rows, attention = cross(all_rows, rep["targets"], cidrs)
    rep["rows"] = all_rows[:MAX_ROWS]
    rep["watch"] = attention
    rep["summary"]["connections"] = len(all_rows)
    rep["summary"]["listeners"] = sum(1 for r in all_rows if r.get("direction") == "listen")
    rep["summary"]["unusual"] = sum(1 for r in all_rows
                                    if r.get("verdict") in ("in_unusual", "listen_unusual"))
    rep["summary"]["ok"] = sum(1 for t in rep["targets"] if t["state"] == "ok")
    rep["summary"]["skipped"] = skipped_total
    rep["mine_cidrs"] = cidrs
    rep["leads"] = leads_from(rep)
    report_io.save(rep, mission=mission, kind="peers")
    audit.log("peers.done", mission=mission or None, connections=rep["summary"]["connections"],
              listeners=rep["summary"]["listeners"], unusual=rep["summary"]["unusual"],
              targets=rep["summary"]["ok"])
    return rep


def leads_from(rep: dict) -> list[dict]:
    """Цели из разбора: что стоит досмотреть дальше (хост, порт, внешний партнёр).

    Только чтение и только по делу: незнакомый слушатель на НАШЕМ узле, вход на
    необычный порт, частый внешний партнёр. Чужие адреса становятся целью «спросить
    оператора», а не объектом для щупанья — граница проекта.
    """
    out: list[dict] = []
    for r in rep.get("rows") or []:
        if r.get("verdict") in ("in_unusual", "listen_unusual"):
            out.append({"target": r.get("host") or r.get("target"),
                        "what": "listen" if r.get("direction") == "listen" else "inbound",
                        "port": r.get("server_port"),
                        "why": "listen_unusual" if r.get("direction") == "listen"
                               else "in_unusual", "mine": True})
    for a in rep.get("watch") or []:
        if a.get("what") == "both_sides":
            out.append({"target": a.get("peer"), "what": "partner", "port": None,
                        "why": "both_sides", "mine": False})
        elif a.get("what") == "frequent":
            out.append({"target": a.get("peer"), "what": "peer",
                        "port": a.get("port"), "why": "frequent", "mine": False})
    return out
