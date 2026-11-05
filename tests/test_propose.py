"""Мозг предлагает работу — но НЕ делает её (этап M6 плана).

Слово Стаса 30.09.2026: «сначала посмотрим что он будет предлагать (и как я это кстати
апрувить? кнопка какая то что ли) — а потом … если его этим не тормозить, он будет что
то делать сам просто давая отчеты. Можно это сделать галкой в настройках — типа
подтверждение действий».

КОНТРОЛЬ ИДЁТ ПЕРВЫМ, и он здесь главный: у мозга нет права исполнить. Проверяем, что
предложенная работа СТОИТ статусом `proposed` и не берётся тактом, что записи и
`write_probe` не принимаются вовсе, что дверь спрашивается до очереди, и что снятая
галка подтверждения переводит предложение сразу в `queued`.

Живые data/ оператора не трогаем: очередь, журналы, мир и счётчик предложений уводит
tests/live_data.py::temp_live.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import audit, config, i18n, leads, missions, propose, world    # noqa: E402
from synapse.agent import Synapse                                         # noqa: E402
from synapse.body import Body                                             # noqa: E402
from synapse.panel_core import PanelCore                                  # noqa: E402
from live_data import temp_live                                           # noqa: E402


class StubBrain:
    """Мозг-заглушка: отдаёт ровно тот ответ, который нужен проверке."""

    def __init__(self, answer="вижу открытый порт"):
        self.answer = answer
        self.calls = 0

    def mode(self):
        return "off"                       # такт не зовёт ensure()

    def available(self, timeout=3.0, ttl=2.0):
        return True

    def think(self, prompt, system="", history=None):
        self.calls += 1
        return self.answer


def cfg_with(**brain) -> dict:
    """Настройки живого файла + правки раздела brain (живой config.json не трогаем).

    ГАЛКА ПОДТВЕРЖДЕНИЯ ЗАДАЁТСЯ ЯВНО (v0.31.0). До этой правки тест брал её из
    живого файла, и вечером 30.09.2026 он начал падать не по делу: оператор снял
    галку в настройках (живой `brain.propose_confirm: false`) — «контроль» ждал
    `proposed`, а получал `queued`. Настройка оператора не имеет права менять
    исход теста: здесь она стоит по умолчанию ВКЛ, а нужное состояние проверки
    передаёт сама (`propose_confirm=False`).
    """
    cfg = dict(config.load())
    base = {"mode": "off", "propose": True, "propose_confirm": True}
    base.update(brain)
    cfg["brain"] = dict(cfg.get("brain") or {}, **base)
    return cfg


def syn_with(answer: str, **brain) -> Synapse:
    """Организм с заглушкой мозга. Тело живое: иначе первым сработает инстинкт."""
    cfg = cfg_with(**brain)
    cfg["idle_thinking"] = True
    cfg["instinct_min_seconds"] = 3600
    cfg["tick_log_seconds"] = 3600
    s = Synapse(cfg=cfg, body=Body.load(), brain=StubBrain(answer))
    s.body.stimulus("проснулась", amp=1.0, k=6)
    return s


class TestParsing(unittest.TestCase):

    def test_control_a_plain_line_is_not_an_offer(self):
        """КОНТРОЛЬ: обычная строка ответа предложением не считается."""
        self.assertIsNone(propose.parse_line("вижу открытый порт"))
        offers, clean = propose.split("вижу открытый порт\nвторой взгляд")
        self.assertEqual(offers, [])
        self.assertEqual(clean, "вижу открытый порт\nвторой взгляд")

    def test_marker_forms_are_read(self):
        cases = {
            "🎯 peers": ("peers", {}),
            "**🎯 remote host=203.0.113.10**": ("remote", {"host": "203.0.113.10"}),
            "[mission] net_audit cidr=192.168.1.0/24": ("net_audit",
                                                         {"cidr": "192.168.1.0/24"}),
            "🎯 certs host=example.com port=443": ("certs", {"host": "example.com",
                                                             "port": 443}),
        }
        for line, want in cases.items():
            got = propose.parse_line(line)
            self.assertIsNotNone(got, line)
            self.assertEqual((got["kind"], got["params"]), want, line)

    def test_a_foreign_field_does_not_become_a_command(self):
        """Поле не из списка отбрасывается: подсунуть команду через предложение нельзя."""
        got = propose.parse_line("🎯 peers cmd=rm -rf /")
        self.assertIsNotNone(got)
        self.assertEqual(got["params"], {}, f"чужое поле прошло: {got['params']}")

    def test_the_offer_is_taken_out_of_the_answer(self):
        offers, clean = propose.split("🎯 peers\nя хочу посмотреть соединения")
        self.assertEqual([o["kind"] for o in offers], ["peers"])
        self.assertNotIn("🎯", clean)
        self.assertEqual(clean, "я хочу посмотреть соединения")


class TestWhatTheBrainMayOffer(unittest.TestCase):

    def test_only_read_only_tools(self):
        """Записи, стимулы и вопросы от мозга не принимаются — список железный."""
        self.assertNotIn("write_probe", propose.ALLOWED)
        self.assertNotIn("stimulus", propose.ALLOWED)
        self.assertNotIn("note", propose.ALLOWED)
        for kind in propose.ALLOWED:
            self.assertIn(kind, missions.KINDS, f"{kind} нет среди инструментов")

    def test_the_check_refuses_what_is_not_allowed(self):
        cfg = cfg_with()
        self.assertEqual(propose.check({"kind": "write_probe", "params": {}}, cfg)[1],
                         "brain.bad_tool")
        self.assertEqual(propose.check({"kind": "remote", "params": {}}, cfg)[1],
                         "brain.bad_param")
        self.assertEqual(propose.check({"kind": "peers", "params": {"cmd": "x"}}, cfg)[1],
                         "brain.bad_param")

    def test_the_door_is_asked_before_the_queue(self):
        """То, что дверь не пустит, в очередь не попадает вовсе.

        v0.32.0: у предложения ДВА сита — сначала «наше ли это» (код `alien`), потом
        дверь. Здесь взят СВОЙ адрес (192.168.2.0/24 есть в peers.mine_cidrs), который
        оператор не пустил списком сетей: значит, отказать обязана именно дверь.
        """
        cfg = cfg_with()
        cfg["net"] = dict(cfg.get("net") or {}, allowed_cidrs=["192.168.1.0/24"],
                          max_hosts=256)
        ok, code = propose.check({"kind": "net_audit", "params": {"cidr": "192.168.2.0/24"}},
                                 cfg)
        self.assertFalse(ok)
        self.assertEqual(code, "door")


class TestTheQueueWaitsForTheOperator(unittest.TestCase):

    def setUp(self):
        self._live = temp_live()
        self._live.__enter__()
        self._keep_lang = i18n.lang()

    def tearDown(self):
        i18n.set_lang(self._keep_lang)
        self._live.__exit__(None, None, None)

    def test_control_it_does_not_run_by_itself(self):
        """КОНТРОЛЬ: без слова оператора предложенное не берётся тактом."""
        cfg = cfg_with(propose=True, propose_confirm=True)
        rec, code = propose.offer({"kind": "peers", "params": {}}, cfg)
        self.assertEqual(code, "proposed")
        self.assertEqual(rec["status"], missions.PROPOSED)
        self.assertEqual(rec["by"], "brain")
        self.assertIsNone(missions.take_next(), "предложенная работа ушла в дело сама")
        self.assertEqual(missions.status_of(rec["id"]), missions.PROPOSED)

    def test_approve_turns_it_into_work(self):
        cfg = cfg_with(propose=True, propose_confirm=True)
        rec, _ = propose.offer({"kind": "peers", "params": {}}, cfg)
        core = PanelCore.__new__(PanelCore)
        core.cfg = cfg
        res = core.approve_mission(rec["id"])
        self.assertTrue(res["ok"], res)
        self.assertEqual(missions.status_of(rec["id"]), "queued")
        taken = missions.take_next()
        self.assertIsNotNone(taken, "после подтверждения такт не взял работу")
        self.assertEqual(taken["id"], rec["id"])

    def test_approve_says_when_there_is_nothing_to_approve(self):
        core = PanelCore.__new__(PanelCore)
        core.cfg = cfg_with()
        missing = core.approve_mission("m-19700101-000000-99")
        self.assertFalse(missing["ok"])
        self.assertTrue(missing["why"])

    def test_without_confirmation_it_takes_the_offer_at_once(self):
        """Галка подтверждения снята — существо делает само и только отчитывается."""
        cfg = cfg_with(propose=True, propose_confirm=False)
        rec, code = propose.offer({"kind": "peers", "params": {}}, cfg)
        self.assertEqual(code, "queued")
        self.assertEqual(rec["status"], "queued")
        taken = missions.take_next()
        self.assertIsNotNone(taken)

    def test_the_switch_off_stops_offers(self):
        cfg = cfg_with(propose=False)
        rec, code = propose.offer({"kind": "peers", "params": {}}, cfg)
        self.assertIsNone(rec)
        self.assertEqual(code, "off")

    def test_the_cap_stops_a_flood(self):
        cfg = cfg_with(propose=True, propose_confirm=True, propose_max=1,
                       propose_seconds=0)
        propose.offer({"kind": "peers", "params": {}}, cfg)
        rec, code = propose.offer({"kind": "remote", "params": {"host": "203.0.113.10"}}, cfg)
        self.assertIsNone(rec)
        self.assertEqual(code, "cap")

    def test_the_same_place_is_not_offered_twice(self):
        cfg = cfg_with(propose=True, propose_confirm=True, propose_seconds=3600)
        propose.offer({"kind": "peers", "params": {}}, cfg)
        rec, code = propose.offer({"kind": "peers", "params": {}}, cfg)
        self.assertIsNone(rec)
        self.assertEqual(code, "again")

    def test_what_is_already_in_the_queue_is_not_offered_again(self):
        cfg = cfg_with(propose=True, propose_confirm=True, propose_seconds=0)
        propose.offer({"kind": "peers", "params": {}}, cfg)
        rec, code = propose.offer({"kind": "peers", "params": {}}, cfg)
        self.assertIsNone(rec)
        self.assertEqual(code, "inflight")


class TestTheBrainPath(unittest.TestCase):

    def setUp(self):
        self._live = temp_live()
        self._live.__enter__()
        self._keep_lang = i18n.lang()
        i18n.set_lang("ru")

    def tearDown(self):
        i18n.set_lang(self._keep_lang)
        self._live.__exit__(None, None, None)

    def test_control_a_thought_without_a_marker_leaves_no_missions(self):
        syn = syn_with("просто мысль про тело")
        syn.idle_thought()
        self.assertEqual(missions.all_missions(), [], "мысль создала миссию")

    def test_an_offer_becomes_a_proposed_mission_and_is_told(self):
        syn = syn_with("вижу открытый порт\n🎯 remote host=203.0.113.10")
        said = syn.idle_thought()
        rows = missions.all_missions()
        self.assertEqual(len(rows), 1, f"очередь: {rows}")
        self.assertEqual(rows[0]["status"], missions.PROPOSED)
        self.assertEqual(rows[0]["kind"], "remote")
        self.assertEqual(rows[0]["params"], {"host": "203.0.113.10"})
        self.assertIn(i18n.t("brain.propose_note"), rows[0]["note"])
        self.assertIn("203.0.113.10", said, "оператор не увидел, что предложено")
        self.assertNotIn("🎯", said, "метка предложения утекла в речь")
        self.assertTrue(any(e.get("event") == "brain.proposed" for e in audit.tail(20)),
                        "предложение не попало в журнал")

    def test_the_proposed_mission_still_waits_after_a_tick(self):
        """Такт живёт, но предложенное не берёт: права исполнить у мозга нет."""
        syn = syn_with("открытый порт\n🎯 peers")
        syn.idle_thought()
        rec = missions.all_missions()[0]
        syn.tick(with_mission=True)
        self.assertEqual(missions.status_of(rec["id"]), missions.PROPOSED,
                         "предложенная работа ушла в дело без подтверждения")

    def test_an_offer_the_door_refuses_is_not_queued(self):
        """Мусор и запрещённое от мозга в очередь не попадают — но и не молчат."""
        syn = syn_with("🎯 write_probe host=10.0.0.1")
        said = syn.idle_thought()
        self.assertEqual(missions.all_missions(), [], "запрещённое предложение легло в очередь")
        self.assertTrue(said, "молчание вместо честного «не взяли»")


if __name__ == "__main__":
    unittest.main()

class TestAlienTarget(unittest.TestCase):
    """v0.32.0: предлагать можно СВОЁ и УЖЕ ВИДЕННОЕ — выдуманный адрес не принимаем.

    Живой вечер 30.09.2026 показал, зачем этот код: дверь разрешает любые сети
    (`net.allowed_cidrs` = `0.0.0.0/0`), и мозг предложил `net_audit cidr=192.168.1.0/24`
    (сеть, которой у нас нет) и `remote host=127.0.0.1` (петлю). Право формально есть,
    смысла нет: за таким адресом не стоит ни одной виденной строки. Своё — узлы и сети
    из настроек и петля; виденное — зацепки из своих же отчётов (`data/leads.json`),
    то есть ровно то, что оператор просил уметь брать как цель.
    """

    def setUp(self):
        self._live = temp_live()
        self._live.__enter__()
        self._keep_lang = i18n.lang()
        i18n.set_lang("ru")

    def tearDown(self):
        i18n.set_lang(self._keep_lang)
        self._live.__exit__(None, None, None)

    def test_control_our_own_node_passes(self):
        """КОНТРОЛЬ: свой узел из настроек предложить можно — иначе тест был бы слепым."""
        cfg = cfg_with()
        host = ((cfg.get("peers") or {}).get("targets") or [{}])[0].get("host", "192.168.1.1")
        ok, code = propose.check({"kind": "remote", "params": {"host": host}}, cfg)
        self.assertTrue(ok, f"свой узел отбит: {code}")

    def test_control_our_own_net_passes(self):
        ok, code = propose.check({"kind": "net_audit",
                                  "params": {"cidr": "192.168.1.0/24"}}, cfg_with())
        self.assertTrue(ok, code)

    def test_invented_host_is_refused(self):
        ok, code = propose.check({"kind": "remote", "params": {"host": "203.0.113.9"}},
                                 cfg_with())
        self.assertFalse(ok, "выдуманный хост прошёл")
        self.assertEqual(code, "brain.propose_alien")
        self.assertTrue(i18n.has(code), "у отказа нет слов — оператор не поймёт, почему")

    def test_invented_net_is_refused(self):
        for cidr in ("192.168.1.0/24", "10.9.0.0/16"):
            ok, code = propose.check({"kind": "net_audit", "params": {"cidr": cidr}}, cfg_with())
            self.assertFalse(ok, f"{cidr} прошёл")
            self.assertEqual(code, "brain.propose_alien", cidr)

    def test_a_host_we_have_seen_is_no_longer_an_invention(self):
        """Зацепка из СВОЕГО отчёта — законная цель (слово Стаса 30.09.2026)."""
        leads.remember([{"target": "203.0.113.9", "what": "listen", "port": 8443,
                         "why": "listen_unusual", "mine": False}])
        ok, code = propose.check({"kind": "remote", "params": {"host": "203.0.113.9"}},
                                 cfg_with())
        self.assertTrue(ok, f"виденный хост отбит: {code}")

    def test_the_offer_of_a_stranger_never_becomes_a_mission(self):
        """И через полный путь: выдумка не ложится в очередь, но и не молчит."""
        syn = syn_with("вижу порт\n🎯 remote host=203.0.113.9")
        said = syn.idle_thought()
        self.assertEqual(missions.all_missions(), [], "выдумка легла в очередь")
        self.assertTrue(said, "молчание вместо честного «не взяли»")
        why = [e.get("why") for e in audit.tail(20) if e.get("event") == "brain.propose_blocked"]
        self.assertIn("brain.propose_alien", why)


class TestTheWindowYieldsToNewMaterial(unittest.TestCase):
    """v0.32.0: окно повтора не глушит предложение, когда мир изменился.

    Живой журнал 30.09.2026: после одной миссии `peers` мозг трижды получил отказ
    `again` (окно 1800 с), хотя снимок соединений уже сменился — 43 новых факта.
    """

    def setUp(self):
        self._live = temp_live()
        self._live.__enter__()

    def tearDown(self):
        self._live.__exit__(None, None, None)

    def test_control_without_changes_the_window_holds(self):
        cfg = cfg_with(propose=True, propose_confirm=True, propose_seconds=3600)
        propose.offer({"kind": "peers", "params": {}}, cfg)
        self.assertEqual(propose.offer({"kind": "peers", "params": {}}, cfg)[1], "again")

    def test_the_same_place_returns_when_the_world_changed(self):
        cfg = cfg_with(propose=True, propose_confirm=True, propose_seconds=3600)
        first, _code = propose.offer({"kind": "peers", "params": {}}, cfg)
        missions.remove(first["id"])          # первое предложение убрали — иначе поймали
        #                                        бы `inflight`, а проверяем именно окно
        world.record("peers", {"targets": [{"name": "router", "host": "192.168.1.1",
                                            "connections": [{"proto": "tcp",
                                                             "src": "192.168.1.10",
                                                             "dst": "1.1.1.1:443"}]}]},
                     {})
        rec, code = propose.offer({"kind": "peers", "params": {}}, cfg)
        self.assertIsNotNone(rec, f"новый материал не открыл окно: {code}")
        self.assertIn(code, ("proposed", "queued"))
