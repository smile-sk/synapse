"""Память о делах — «стало иначе» вместо «как было» (этап M5 плана).

Проверяем ровно то, ради чего слой написан, и КОНТРОЛЬ ИДЁТ ПЕРВЫМ: первый взгляд
не имеет права кричать об изменениях (сравнивать не с чем), повтор без изменений —
говорить «как было», а настоящее изменение — называться числами и попадать в дневник.

Живые data/ оператора не трогаем: файлы мира, дневник, очередь и запас уводит
tests/live_data.py::temp_live.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import config, i18n, life, missions, world             # noqa: E402
from synapse.agent import Synapse                                   # noqa: E402
from synapse.body import Body                                       # noqa: E402
from live_data import temp_live                                     # noqa: E402

NET = "10.0.0.0/30"


def net_rep(open_one=(22, 80), open_two=(445,)):
    """Отчёт обхода — той же формы, что отдаёт net_scan, иначе сравнение не о том."""
    return {"cidr": NET, "hosts": [
        {"ip": "10.0.0.1", "open": list(open_one)},
        {"ip": "10.0.0.2", "open": list(open_two)},
    ]}


def quiet_synapse():
    """Организм без мозга: слой мира проверяем без сервера."""
    cfg = dict(config.load())
    cfg["brain"] = dict(cfg.get("brain") or {}, mode="off")
    cfg["idle_thinking"] = True
    cfg["instinct_min_seconds"] = 3600
    return Synapse(cfg=cfg, body=Body.load())


class TestChangesAreSeen(unittest.TestCase):

    def setUp(self):
        self._live = temp_live()
        self._live.__enter__()
        self._keep_lang = i18n.lang()

    def tearDown(self):
        i18n.set_lang(self._keep_lang)
        self._live.__exit__(None, None, None)

    def test_control_the_first_look_is_not_a_change(self):
        """КОНТРОЛЬ: первый взгляд — не изменение. Иначе «стало иначе» значило бы «посмотрел»."""
        first = world.record("net_audit", net_rep(), {"cidr": NET})
        self.assertEqual(first["verdict"], "first")
        self.assertEqual((first["plus"], first["minus"]), (0, 0),
                         "первый взгляд выдал изменение — сравнивать было не с чем")

    def test_the_same_world_says_as_before(self):
        world.record("net_audit", net_rep(), {"cidr": NET})
        again = world.record("net_audit", net_rep(), {"cidr": NET})
        self.assertEqual(again["verdict"], "same")
        self.assertEqual((again["plus"], again["minus"]), (0, 0))

    def test_a_new_port_is_seen_as_change(self):
        world.record("net_audit", net_rep(), {"cidr": NET})
        changed = world.record("net_audit", net_rep(open_one=(22, 80, 3389)), {"cidr": NET})
        self.assertEqual(changed["verdict"], "shift")
        self.assertGreaterEqual(changed["plus"], 1)
        self.assertTrue(any("3389" in f for f in changed["added"]),
                        f"новый порт не назван: {changed['added']}")

    def test_a_vanished_host_is_seen_as_change(self):
        world.record("net_audit", net_rep(), {"cidr": NET})
        changed = world.record("net_audit", net_rep(open_two=()), {"cidr": NET})
        self.assertIn(changed["verdict"], ("shrank", "shift"))
        self.assertTrue(any("10.0.0.2" in f for f in changed["gone"]),
                        f"ушедший узел не назван: {changed['gone']}")

    def test_another_area_has_its_own_snapshot(self):
        """Своё сравнение — на каждую область: чужая сеть не делает вид, что мы её уже видели."""
        world.record("net_audit", net_rep(), {"cidr": NET})
        other = world.record("net_audit", net_rep(), {"cidr": "10.9.9.0/30"})
        self.assertEqual(other["verdict"], "first")
        self.assertNotEqual(other["scope"], f"net:{NET}")

    def test_one_rule_of_scope_for_both_layers(self):
        """Область у запаса и у памяти о делах — ОДНА функция, иначе сравнение рассыплется."""
        change = world.record("net_audit", net_rep(), {"cidr": NET})
        self.assertEqual(change["scope"], life.scope_key("net_audit", {"cidr": NET}, net_rep()))

    def test_diary_writes_its_own_note(self):
        world.record("net_audit", net_rep(), {"cidr": NET})
        world.record("net_audit", net_rep(open_one=(22, 80, 3389)), {"cidr": NET})
        lines = world.diary_tail(10)
        self.assertEqual(len(lines), 2, f"в дневник попало не то: {lines}")
        self.assertIn("3389", "\n".join(lines), "дневник не назвал, что изменилось")
        self.assertTrue(world.DIARY.exists(), "файла дневника нет")

    def test_brief_names_my_world(self):
        """Строка «мой мир» — то, чем кормится мысль: без неё она возвращает мой же промпт."""
        empty = world.brief()
        self.assertEqual(empty, i18n.t("world.brief.none"))
        world.record("net_audit", net_rep(), {"cidr": NET})
        world.record("net_audit", net_rep(open_one=(22, 80, 3389)), {"cidr": NET})
        brief = world.brief()
        self.assertIn("net_audit", brief)
        self.assertNotEqual(brief, empty)

    def test_the_mission_result_says_the_world_changed(self):
        """Путь живьём: слой зовётся из итога миссии, а не сам по себе."""
        syn = quiet_synapse()
        self.assertEqual(syn._world("net_audit", net_rep(), {"cidr": NET}), "",
                         "первый взгляд не должен ничего объявлять")
        told = syn._world("net_audit", net_rep(open_one=(22, 80, 3389)), {"cidr": NET})
        self.assertTrue(told, "изменение мира не сказано в итоге миссии")
        self.assertTrue(any(ch.isdigit() for ch in told), "изменение названо без чисел")
        self.assertEqual(syn._last_change["verdict"], "shift")

    def test_the_same_world_in_the_result_says_as_before(self):
        syn = quiet_synapse()
        syn._world("net_audit", net_rep(), {"cidr": NET})
        told = syn._world("net_audit", net_rep(), {"cidr": NET})
        self.assertEqual(told, i18n.t("ag.world_same"))


class TestTheDiaryIsKept(unittest.TestCase):

    def setUp(self):
        self._live = temp_live()
        self._live.__enter__()

    def tearDown(self):
        self._live.__exit__(None, None, None)

    def test_the_diary_keeps_its_tail(self):
        """Дневник — память, а не бесконечная простыня: держим последние строки."""
        world.KEEP_DIARY = 3
        try:
            for i in range(5):
                world.record("net_audit", net_rep(open_one=(22, 80 + i)), {"cidr": NET})
            lines = world.diary_tail(50)
            self.assertEqual(len(lines), 3, f"дневник не подрезан: {len(lines)}")
        finally:
            world.KEEP_DIARY = 400

    def test_the_world_json_is_readable_by_a_human(self):
        """Файл мира — обычный JSON: его читает и панель, и человек в блокноте."""
        import json
        world.record("net_audit", net_rep(), {"cidr": NET})
        data = json.loads(world.WORLD.read_text(encoding="utf-8"))
        self.assertIn(f"net:{NET}", data["scopes"])
        self.assertEqual(data["last"]["kind"], "net_audit")


if __name__ == "__main__":
    unittest.main()

class TestTheMaterialIsNotNoise(unittest.TestCase):
    """v0.32.0: в «Мой мир» идут САМЫЕ ВАЖНЫЕ строки, а не первые попавшиеся.

    Живой вечер 30.09.2026: в изменении первыми стояли внутренние DNS-запросы роутера
    (`router: inner udp … -> 192.168.1.1:53`), и модель девять минут писала «ползу
    к DNS-шлюзу» — хотя в том же снимке был незнакомый слушатель НАРУЖУ на VPS. Снимок
    делается ради дверей, а не ради шума; порядок показа теперь по весу факта.
    """

    def setUp(self):
        self._keep_lang = i18n.lang()
        i18n.set_lang("ru")

    def tearDown(self):
        i18n.set_lang(self._keep_lang)

    def test_control_a_listener_outweighs_inner_noise(self):
        """КОНТРОЛЬ: слушатель важнее внутреннего DNS — иначе порядок был бы слепым."""
        change = {"added": ["router: inner udp 192.168.1.15 -> 192.168.1.1:53",
                            "vps: listen udp 0.0.0.0:58018"],
                  "gone": [], "plus": 2, "minus": 0}
        text = world.items_text(change, limit=1)
        self.assertIn("listen", text, f"шум оказался важнее слушателя: {text}")
        self.assertNotIn(":53", text, f"в материал попал внутренний DNS: {text}")

    def test_inbound_goes_ahead_of_an_ordinary_outbound(self):
        change = {"added": ["router: out tcp 192.168.1.10 -> 1.1.1.1:443",
                            "vps: in tcp 203.0.113.10:22 <- 203.0.113.11"],
                  "gone": [], "plus": 2, "minus": 0}
        text = world.items_text(change, limit=1)
        self.assertIn(" in ", text, f"обычный выход важнее входящего: {text}")

    def test_the_order_is_deterministic(self):
        """Один и тот же снимок — один и тот же текст: иначе дневник дёргался бы зря."""
        change = {"added": ["router: out tcp 1.1.1.1:443", "router: out udp 8.8.8.8:53",
                            "router: inner udp 192.168.1.15 -> 192.168.1.1:53"],
                  "gone": [], "plus": 3, "minus": 0}
        self.assertEqual(world.items_text(change), world.items_text(dict(change)))
