"""Тесты ВЗГЛЯДА НА СОЕДИНЕНИЯ — миссия `peers` (v0.28.0, этап M4 плана).

Слово Стаса 30.09.2026: «пускай смотрит и анализирует соединения на моем роутере и
анализирует их встречно — что это, откуда, вредное ли, потом тоже самое по соединениям
на нашем VPS — ну только все конечно нужно с делать для чтения».

Что здесь проверяется ПО-НАСТОЯЩЕМУ:

  * КОНТРОЛЬ первым: без ключа нет перевода, без доступа нет чтения, без запроса нет
    похода в сеть — то есть все проверки ниже опираются на то, что инструмент вообще
    способен НЕ работать;
  * команды приходят ТОЛЬКО из белого списка в коде — конфиг команду подсунуть не может;
  * дверь спрашивается ДО касания узла, отказ виден в журнале;
  * направление соединения НЕ угадывается по порту (живая ошибка 30.09.2026: наши
    выходы на 443 были подписаны «кто входит к нам»);
  * факт — ОТНОШЕНИЕ, а не строка сессии: смена случайного порта не кормит заново;
  * встречный разбор: «виден с двух сторон», частый партнёр, выставленный наружу
    незнакомый слушатель — и всё это становится ЦЕЛЬЮ, а чужое — целью «спросить».
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                            # noqa: E402

from synapse import audit, config, i18n, life, reports   # noqa: E402
from synapse.tools import peers                          # noqa: E402
from live_data import temp_live                          # noqa: E402


@pytest.fixture(autouse=True)
def _russian():
    i18n.set_lang("ru")
    yield
    i18n.set_lang("en")


# --- живые формы данных (сняты с настоящих узлов 30.09.2026) ---
ROUTER_OUT = """Flags: S - SEEN-REPLY; A - ASSURED; C - CONFIRMED; F - FASTTRACK; s - SRCNAT
Columns: PROTOCOL, SRC-ADDRESS, DST-ADDRESS, TCP-STATE
  #        PROTOCOL   SRC-ADDRESS          DST-ADDRESS           TCP-STATE
  0 S C    ipsec-esp  203.0.113.11        203.0.113.10
  1 SACFs  tcp        192.168.1.21:54240  142.251.98.100:443    established
  2 SAC    tcp        45.66.10.7:51000     203.0.113.11:3389    established
  3       tcp        192.168.1.15:51282  149.154.167.51:443    established
"""
ROUTER_ADDR = """ 0 R name=bridge1 address=192.168.1.1/24 network=192.168.1.0
 1 R name=ether1 address=203.0.113.11/26 network=203.0.113.0
"""
SS_OUT = """Netid  State   Recv-Q  Send-Q   Local Address:Port   Peer Address:Port  Process
udp    UNCONN  0       0            0.0.0.0:5514        0.0.0.0:*
tcp    LISTEN  0       128          0.0.0.0:22          0.0.0.0:*
tcp    LISTEN  0       128          127.0.0.1:3306      0.0.0.0:*
tcp    LISTEN  0       128          127.0.0.1:34243     0.0.0.0:*
udp    UNCONN  0       0            0.0.0.0:34119       0.0.0.0:*
tcp    ESTAB   0       0       203.0.113.10:22        45.66.10.7:51000
tcp    ESTAB   0       0       203.0.113.10:41234     142.250.1.5:443
"""
IP_BRIEF = """lo               UNKNOWN        127.0.0.1/8 ::1/128
eth0             UP             203.0.113.10/22 fe80::1/64
"""


def _cfg(**peers_over):
    cfg = config.load()
    cfg["brain"] = dict(cfg.get("brain", {}), mode="off")
    block = dict(config.DEFAULTS["peers"], **peers_over)
    cfg["peers"] = block
    return cfg


def _fake_read(mapping):
    """Подменить чтение узла: словарь 'имя узла' -> (ok, texts, err)."""
    def fake(host, os_kind, cred=None, timeout=20):
        for name, value in mapping.items():
            if name in (host, os_kind):
                return value
        return False, {}, "no route"
    return fake


def _router_ok():
    return True, {"connections": ROUTER_OUT, "addresses": ROUTER_ADDR}, ""


def _vps_ok():
    return True, {"connections": SS_OUT, "addresses": IP_BRIEF}, ""


class TestReading:
    """Чтение: форма данных, белый список команд, дверь и отсутствие доступа."""

    def test_parsers_handle_real_shapes(self):
        """Живые формы разбираются: флаги в два слова, шапки, адреса с портом и без."""
        rows, skipped = peers.parse_routeros_connections(ROUTER_OUT)
        assert len(rows) == 4 and skipped == 0
        assert rows[0]["proto"] == "ipsec-esp" and rows[0]["flags"] == "SC"
        assert rows[1]["client_port"] == 54240 and rows[1]["state"] == "established"
        assert peers.parse_routeros_addresses(ROUTER_ADDR) == ["192.168.1.1/24",
                                                              "203.0.113.11/26"]
        host_rows, host_skipped = peers.parse_ss(SS_OUT)
        assert len(host_rows) == 7 and host_skipped == 0
        assert host_rows[0]["local"] == "0.0.0.0:5514"
        assert peers.parse_ip_brief(IP_BRIEF) == ["127.0.0.1/8", "::1/128",
                                                  "203.0.113.10/22", "fe80::1/64"]

    def test_commands_come_only_from_the_whitelist(self, monkeypatch):
        """КОНТРОЛЬ: команда берётся из кода. Подсунуть её через настройки НЕКУДА."""
        seen = {"commands": [], "host": None}

        class FakeStd:
            def read(self):
                return b"ok\n"

        class FakeSSH:
            def set_missing_host_key_policy(self, policy):
                pass

            def connect(self, host, **kwargs):
                seen["host"] = host

            def exec_command(self, command, timeout=None):
                seen["commands"].append(command)
                return None, FakeStd(), FakeStd()

            def close(self):
                pass

        class FakeParamiko:
            SSHClient = FakeSSH

            @staticmethod
            def AutoAddPolicy():
                return None

        monkeypatch.setattr(peers, "_paramiko", lambda: FakeParamiko)
        # Логин и пароль берём «из файла секретов» — иначе путь был бы ssh по ключам,
        # то есть настоящий поход в сеть из теста (а этого делать нельзя).
        monkeypatch.setattr(peers.creds, "for_target", lambda name: {"user": "cody",
                                                                    "password": "x"})
        cfg = _cfg(targets=[{"name": "router", "host": "192.168.1.1", "os": "routeros",
                             "command": "rm -rf /"}])
        peers.collect({}, "m-test-readonly", cfg)
        assert seen["commands"], "чтение узла вообще не состоялось"
        assert seen["commands"][0] == peers.READ_ONLY["routeros"]["connections"]
        assert seen["host"] == "192.168.1.1"
        assert set(seen["commands"]) <= set(peers.READ_ONLY["routeros"].values())
        assert all("rm -rf" not in c for c in seen["commands"]), "команда пришла из настроек!"

    def test_the_whitelist_is_really_read_only(self):
        """Белый список — только взгляд: ни записи, ни правил, ни перезагрузки."""
        forbidden = ("set ", "add ", "remove", "reboot", "reset", "export", ">", "|",
                     "chmod", "systemctl", "kill", "rm ")
        for os_kind, cmds in peers.READ_ONLY.items():
            for command in cmds.values():
                assert command.startswith(("/ip ", "ss ", "ip ")), (os_kind, command)
                assert not any(f in command.lower() for f in forbidden), command

    def test_door_is_asked_before_touching_the_node(self, monkeypatch):
        """Дверь спрашивается ДО касания: отказ виден в отчёте И в журнале."""
        monkeypatch.setattr(peers, "read_node", _fake_read({"192.168.1.1": _router_ok(),
                                                           "203.0.113.10": _vps_ok()}))
        cfg = _cfg(allowed_cidrs=[])
        cfg["net"] = dict(cfg["net"], allowed_cidrs=[])
        with temp_live():
            rep = peers.collect({}, "m-test-door", cfg)
            events = [(r["event"], r.get("host")) for r in audit.tail(30)]
        assert rep["summary"]["ok"] == 0
        assert all(row["state"] == "denied" for row in rep["targets"])
        assert ("peers.denied", "192.168.1.1") in events
        assert rep["rows"] == [] and rep["leads"] == []

    def test_without_access_it_says_so_instead_of_dying(self, monkeypatch):
        """КОНТРОЛЬ: нет доступа — честная строка, а не падение и не пустая тишина."""
        monkeypatch.setattr(peers, "read_node",
                            _fake_read({"192.168.1.1": (False, {}, "Permission denied")}))
        with temp_live():
            rep = peers.collect({}, "m-test-nologin", _cfg())
            events = [r["event"] for r in audit.tail(30)]
            text = reports.render(rep, "ru")
        assert "peers.nologin" in events
        assert "Permission denied" in text
        assert rep["error"] is None, "недоступность одного узла — не ошибка всей миссии"
        assert "0/2" in reports.summary_line(rep, "ru")

    def test_switched_off_is_honest(self):
        """Рубильник выключен — миссия говорит об этом, а не делает вид, что работала."""
        with temp_live():
            rep = peers.collect({}, "m-test-off", _cfg(enabled=False))
            events = [r["event"] for r in audit.tail(10)]
        assert rep["error"] and "peers.off" in events


class TestMeaning:
    """Смысл: направление, служба, тревожное и нет."""

    def _rows(self, monkeypatch, cfg):
        monkeypatch.setattr(peers, "read_node",
                            _fake_read({"192.168.1.1": _router_ok(),
                                        "203.0.113.10": _vps_ok()}))
        with temp_live():
            return peers.collect({}, "m-test-meaning", cfg)

    def _by_raw(self, rep, needle):
        return next(r for r in rep["rows"] if needle in r.get("raw", ""))

    def test_direction_is_not_guessed_from_the_port(self, monkeypatch):
        """КОНТРОЛЬ (живая ошибка 30.09.2026): выход на 443 — это ВЫХОД, а не вход."""
        rep = self._rows(monkeypatch, _cfg())
        out = self._by_raw(rep, "192.168.1.21:54240")
        assert out["direction"] == "out", "наш выход к чужому 443 назван входом"
        assert out["service_port"] == 443 and out["known"] is True
        inside = self._by_raw(rep, "45.66.10.7:51000")
        assert inside["direction"] == "in", "чужой стук на наш порт — это вход"
        assert inside["service_port"] == 3389
        vps_out = self._by_raw(rep, "142.250.1.5:443")
        assert vps_out["direction"] == "out", "у сокета свой счёт направления"

    def test_listener_on_the_loopback_is_not_a_worry(self, monkeypatch):
        """Слушатель на петле снаружи не виден — тревогой он быть не может."""
        rep = self._rows(monkeypatch, _cfg())
        own = self._by_raw(rep, "127.0.0.1:34243")
        assert own["direction"] == "listen" and own["verdict"] == "listen_local"
        db = self._by_raw(rep, "127.0.0.1:3306")
        assert db["verdict"] == "listen_usual", "знакомая служба тревогой не бывает"
        exposed = self._by_raw(rep, "0.0.0.0:34119")
        assert exposed["verdict"] == "listen_unusual", "выставленный наружу порт — тревога"
        known = self._by_raw(rep, "0.0.0.0:5514")
        assert known["verdict"] == "listen_usual" and known["service"]

    def test_cross_check_sees_both_sides(self, monkeypatch):
        """ВСТРЕЧНЫЙ РАЗБОР: адрес на двух узлах — уже не случайный гость."""
        router = """  #        PROTOCOL   SRC-ADDRESS          DST-ADDRESS           TCP-STATE
  1 SACFs  tcp        192.168.1.21:54240  5.5.5.5:9001          established
"""
        vps = """udp    UNCONN  0       0            0.0.0.0:5514   0.0.0.0:*
tcp    ESTAB   0       0       203.0.113.10:41234     5.5.5.5:9001
"""
        monkeypatch.setattr(peers, "read_node",
                            _fake_read({"192.168.1.1": (True, {"connections": router,
                                                                "addresses": ROUTER_ADDR}, ""),
                                        "203.0.113.10": (True, {"connections": vps,
                                                                "addresses": IP_BRIEF}, "")}))
        with temp_live():
            rep = peers.collect({}, "m-test-cross", _cfg())
        both = [a for a in rep["watch"] if a["what"] == "both_sides"]
        assert both and both[0]["peer"] == "5.5.5.5"
        assert both[0]["targets"] == ["router", "vps"]
        assert any(l["what"] == "partner" and not l["mine"] for l in rep["leads"])

    def test_frequent_partner_only_on_unusual_ports(self, monkeypatch):
        """Частые выходы на ОБЫЧНУЮ службу — не новость; на незнакомый порт — новость."""
        many = "".join(f"  {i} SACFs  tcp        192.168.1.21:{50000 + i}  9.9.9.9:{port}"
                       f"    established\n"
                       for i, port in enumerate([443, 443, 443, 9023, 9023, 9023], start=1))
        router = ("  #        PROTOCOL   SRC-ADDRESS          DST-ADDRESS           TCP-STATE\n"
                  + many)
        monkeypatch.setattr(peers, "read_node",
                            _fake_read({"192.168.1.1": (True, {"connections": router,
                                                                "addresses": ROUTER_ADDR}, ""),
                                        "203.0.113.10": (False, {}, "skip")}))
        with temp_live():
            rep = peers.collect({}, "m-test-freq", _cfg())
        freq = [a for a in rep["watch"] if a["what"] == "frequent"]
        assert len(freq) == 1 and freq[0]["peer"] == "9.9.9.9"
        assert freq[0]["port"] == 9023, "443 в тревогу не попадает — это обычная жизнь"

    def test_leads_are_only_about_our_own_hosts(self, monkeypatch):
        """Своё — цель для дела; чужое — цель «спросить оператора»."""
        rep = self._rows(monkeypatch, _cfg())
        mine = [l for l in rep["leads"] if l["mine"]]
        assert mine and all(l["target"] == "203.0.113.10" for l in mine)
        assert all(l["why"] in ("listen_unusual", "in_unusual") for l in mine)

    def test_report_reads_like_a_report(self, monkeypatch):
        """Отчёт на русском: разделы есть, чужая цель помечена, ключ не торчит."""
        rep = self._rows(monkeypatch, _cfg())
        text = reports.render(rep, "ru")
        for head in ("куда ходят наши", "кто входит к нам", "что слушает", "стоит внимания",
                     "цели для следующих шагов"):
            assert head in text, head
        assert "rep.peers" not in text and "{target}" not in text
        assert "не обычная служба" in text
        en = reports.render(rep, "en")
        assert "what listens" in en


class TestFood:
    """Еда: находки считаются по ОТНОШЕНИЯМ, а не по строкам сессий."""

    def _report(self, ephemeral: int) -> dict:
        router = (f"  #        PROTOCOL   SRC-ADDRESS          DST-ADDRESS           TCP-STATE\n"
                  f"  1 SACFs  tcp        192.168.1.21:{ephemeral}  142.251.98.100:443"
                  f"    established\n")
        rows, _ = peers.parse_routeros_connections(router)
        for r in rows:
            r["target"] = "router"
        cfg = _cfg()
        rows = peers.classify(rows, ["192.168.1.0/24"])
        return {"kind": "peers", "at": "now", "targets": [{"name": "router", "state": "ok"}],
                "rows": rows, "watch": [], "leads": [], "mine_cidrs": ["192.168.1.0/24"]}

    def test_same_relation_with_another_port_is_not_new_food(self, tmp_path):
        """КОНТРОЛЬ: смена случайного порта — тот же факт. Иначе еда росла бы из воздуха."""
        l = life.Life(path=tmp_path / "life.json", cfg=_cfg())
        l.state["reserve"] = 0.5
        with temp_live():
            first = l.observe("peers", self._report(50001))
            second = l.observe("peers", self._report(50002))
        assert first["new"] == 1 and second["new"] == 0, (first, second)
        assert l.reserve() == pytest.approx(0.5 + first["fed"])

    def test_a_new_relation_is_food(self, tmp_path):
        """А вот новый партнёр или новая служба — это правда находка."""
        l = life.Life(path=tmp_path / "life.json", cfg=_cfg())
        l.state["reserve"] = 0.5
        rep = self._report(50001)
        rep["rows"][0]["rel"] = "router: out tcp 192.168.1.21 -> 8.8.8.8:53"
        with temp_live():
            got = l.observe("peers", rep)
        assert got["new"] == 1 and got["fed"] > 0
