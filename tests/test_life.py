"""Тесты ЖИЗНИ — запаса сил (v0.27.0).

Слово Стаса 30.09.2026: «я хочу сотворить живой киберорганизм ещё и с пользой.
зачем по твоему мы ему давали инстинкт самовыживания?»

Прежняя energy() тела — среднее возбуждение импульса: её нельзя потратить и нечем
пополнить. Запас (synapse/life.py) — настоящий ресурс: тратится ВРЕМЕНЕМ, приходит
ПОЛЬЗОЙ (миссия нашла то, чего организм не знал), а тишина и повтор не кормят.

Порядок как везде в проекте: сначала КОНТРОЛЬ (выключенный рубильник, повторная
находка), потом само поведение.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                            # noqa: E402

from synapse import audit, config, i18n, life           # noqa: E402
from synapse.tools import net_scan                       # noqa: E402
from synapse.agent import Synapse                        # noqa: E402
from live_data import temp_live                          # noqa: E402


@pytest.fixture(autouse=True)
def _russian():
    """Речь существа проверяем по-русски — это то, что читает Стас."""
    i18n.set_lang("ru")
    yield
    i18n.set_lang("en")


def _cfg(**life_over):
    cfg = config.load()
    cfg["brain"] = dict(cfg.get("brain", {}), mode="off")   # тесты не жгут GPU
    cfg["life"] = dict(config.DEFAULTS["life"], **life_over)
    return cfg


def _life(tmp_path, **life_over):
    return life.Life(path=tmp_path / "life.json", cfg=_cfg(**life_over))


def _quiet(**life_over):
    return Synapse(cfg=_cfg(**life_over))


HOSTS = {"hosts": [{"ip": "192.168.1.1", "open": [22, 3389], "banners": {},
                    "shares": []}]}


class TestReserve:
    def test_spends_time(self, tmp_path):
        """Запас тратится ВРЕМЕНЕМ: час жизни стоит ровно spend_per_hour."""
        l = _life(tmp_path, spend_per_hour=1.0, max_gap_seconds=100000)
        l.state["reserve"], l.state["at"] = 1.0, 1000.0
        spent = l.metabolize(now=1000.0 + 600)          # десять минут
        assert abs(spent - 1.0 / 6) < 1e-6, spent
        assert abs(l.reserve() - (1.0 - 1.0 / 6)) < 1e-6

    def test_closed_window_does_not_starve(self, tmp_path):
        """КОНТРОЛЬ: окно было закрыто — жизнь стояла. Простой списан не больше потолка."""
        l = _life(tmp_path, spend_per_hour=1.0, max_gap_seconds=900)
        l.state["reserve"], l.state["at"] = 1.0, 1000.0
        l.metabolize(now=1000.0 + 86400)                # сутки простоя
        assert abs(l.reserve() - 0.75) < 1e-6, "списан ровно max_gap_seconds, не сутки"

    def test_switch_off_means_no_life(self, tmp_path):
        """КОНТРОЛЬ: рубильник выключен — жизни нет: ни расхода, ни голода."""
        l = _life(tmp_path, enabled=False, spend_per_hour=1.0)
        l.state["reserve"], l.state["at"] = 0.01, 1000.0
        assert l.metabolize(now=1000.0 + 3600) == 0.0
        assert l.reserve() == 0.01
        assert l.level() == "ok", "выключенная жизнь не голодает по старому запасу"
        assert not l.hungry() and not l.starving()

    def test_floor_and_levels(self, tmp_path):
        """Запас не уходит ниже нуля, и уровни называются словами."""
        l = _life(tmp_path, spend_per_hour=10.0, max_gap_seconds=100000)
        l.state["reserve"], l.state["at"] = 0.05, 1000.0
        l.metabolize(now=1000.0 + 3600)
        assert l.reserve() == 0.0
        assert l.level() == "starving"
        l.state["reserve"] = 0.29
        assert l.level() == "hungry"
        l.state["reserve"] = 0.5
        assert l.level() == "ok"
        l.state["reserve"] = 0.95
        assert l.level() == "full"


class TestFeeding:
    def test_new_facts_feed(self, tmp_path):
        """КОНТРОЛЬ: находка кормит — запас растёт, и это записано в журнал."""
        l = _life(tmp_path, feed_per_fact=0.05, feed_max=0.4)
        l.state["reserve"] = 0.5
        with temp_live():
            got = l.observe("net_audit", HOSTS, {"cidr": "192.168.1.0/24"})
            events = [r["event"] for r in audit.tail(10)]
        assert got["new"] == 1, got
        assert l.reserve() > 0.5
        assert "life.fed" in events

    def test_repeat_is_no_food(self, tmp_path):
        """КОНТРОЛЬ: тот же обход второй раз — не находка: еды нет, запас не вырос."""
        l = _life(tmp_path, feed_per_fact=0.05)
        l.state["reserve"] = 0.5
        l.observe("net_audit", HOSTS, {"cidr": "192.168.1.0/24"})
        before = l.reserve()
        with temp_live():
            got = l.observe("net_audit", HOSTS, {"cidr": "192.168.1.0/24"})
        assert got["new"] == 0 and got["fed"] == 0.0
        assert l.reserve() == before, "повтор не должен кормить"

    def test_change_is_a_new_fact(self, tmp_path):
        """Появился новый порт — это снова находка (мир изменился)."""
        l = _life(tmp_path, feed_per_fact=0.05)
        l.state["reserve"] = 0.5
        l.observe("net_audit", HOSTS, {"cidr": "192.168.1.0/24"})
        changed = {"hosts": [{"ip": "192.168.1.1", "open": [22, 3389, 80],
                              "banners": {}, "shares": []}]}
        got = l.observe("net_audit", changed, {"cidr": "192.168.1.0/24"})
        assert got["new"] == 1, got

    def test_cap_per_mission_and_ceiling(self, tmp_path):
        """Потолок на одну миссию держит, а полный запас не переполняется."""
        l = _life(tmp_path, feed_per_fact=0.1, feed_max=0.15)
        l.state["reserve"] = 0.0
        hosts = {"hosts": [{"ip": f"10.0.0.{i}", "open": [22], "banners": {}, "shares": []}
                           for i in range(1, 11)]}
        got = l.observe("net_audit", hosts, {"cidr": "10.0.0.0/24"})
        assert got["new"] == 10
        assert abs(got["fed"] - 0.15) < 1e-9, got["fed"]
        l.state["reserve"] = 0.95
        got2 = l.observe("net_audit", {"hosts": [{"ip": "10.9.9.9", "open": [80],
                                                  "banners": {}, "shares": []}]},
                         {"cidr": "10.9.9.0/24"})
        assert l.reserve() <= 1.0 and got2["fed"] <= 0.05 + 1e-9

    def test_remote_and_certs_have_facts(self):
        """У удалённой миссии и сертификатов факты тоже есть (жизнь не только про сеть)."""
        rem = {"host": "203.0.113.10", "state": "ok", "method": "ssh",
               "output": "linux", "ways": [{"method": "ssh", "port": 22, "open": True},
                                           {"method": "winrm", "port": 5985, "open": False}]}
        cert = {"targets": [{"host": "example.com", "port": 443, "ok": True,
                             "days_left": 41, "tls": "TLSv1.3"}]}
        assert len(life.facts_from("remote", rem)) == 3
        assert len(life.facts_from("certs", cert)) == 1


class TestHunger:
    def test_instinct_names_hunger(self):
        """Голод — рефлекс: существо само говорит, что запаса нет."""
        syn = _quiet(reserve=0.2)
        with temp_live():
            text = syn.instinct()
            events = [(r["event"], r.get("level")) for r in audit.tail(20)]
        assert text, "голодное существо обязано сказать об этом"
        assert ("instinct.hungry", "hungry") in events

    def test_starving_dims_the_body(self):
        """Истощение видно телом: оно не бодрится, а гаснет."""
        syn = _quiet(reserve=0.02)
        syn.body.poke(list(range(40)), 0.5)
        before = syn.body.energy()
        assert before > 0
        with temp_live():
            text = syn.instinct()
            events = [(r["event"], r.get("level")) for r in audit.tail(20)]
        assert syn.body.energy() < before
        assert text
        assert ("instinct.hungry", "starving") in events

    def test_full_reserve_keeps_the_old_reflex(self):
        """КОНТРОЛЬ: сытое существо живёт по-прежнему — голод не вмешивается."""
        syn = _quiet(reserve=0.9)
        assert syn.body.lit() == 0
        with temp_live():
            text = syn.instinct()
            events = [r["event"] for r in audit.tail(20)]
        assert "instinct.feed" in events, "прежний рефлекс обязан остаться"
        assert "instinct.hungry" not in events
        assert text


class TestMissionFeeds:
    def test_net_audit_mission_feeds(self, monkeypatch):
        """Миссия нашла новое — существо сыто, и это видно в итоге миссии."""
        syn = _quiet(feed_per_fact=0.1, feed_max=0.4)
        syn.life.state["reserve"] = 0.4
        monkeypatch.setattr(net_scan, "audit_network",
                            lambda *a, **kw: dict(HOSTS, at="now", kind="net_audit",
                                                  cidr="192.168.1.0/24"))
        with temp_live():
            status, text = syn.handle({"id": "m-test-life-1", "kind": "net_audit",
                                       "params": {"cidr": "192.168.1.0/24"}})
            events = [r["event"] for r in audit.tail(30)]
        assert status == "done"
        assert syn.life.reserve() > 0.4, "находка обязана накормить"
        assert "life.fed" in events
        assert "нашла нового" in text, text

    def test_empty_rescan_does_not_feed(self, monkeypatch):
        """КОНТРОЛЬ: повторный обход того же мира еды не даёт — сказано прямо."""
        syn = _quiet(feed_per_fact=0.1)
        syn.life.state["reserve"] = 0.4
        monkeypatch.setattr(net_scan, "audit_network",
                            lambda *a, **kw: dict(HOSTS, at="now", kind="net_audit",
                                                  cidr="192.168.1.0/24"))
        params = {"cidr": "192.168.1.0/24"}
        with temp_live():
            syn.handle({"id": "m-test-life-2", "kind": "net_audit", "params": params})
            reserve_after_first = syn.life.reserve()
            status, text = syn.handle({"id": "m-test-life-3", "kind": "net_audit",
                                       "params": params})
        assert status == "done"
        assert syn.life.reserve() == reserve_after_first
        assert "нового нет" in text, text


class TestTick:
    def test_tick_reports_reserve_and_logs_level_change(self, tmp_path, monkeypatch):
        """Такт показывает запас, а смену уровня пишет в журнал СОБЫТИЕМ, не метрономом."""
        syn = _quiet(reserve=0.5, spend_per_hour=1.0)
        syn.life.state["at"] = 1.0                     # «жизнь идёт с незапамятных времён»
        syn.body.poke([0, 1], 0.2)                     # тело живо: рефлекс молчит
        with temp_live():
            out = syn.tick(with_mission=False)
            events = [r["event"] for r in audit.tail(30)]
        assert "reserve" in out
        assert out["reserve"] <= 0.5
        assert "life.level" in events

class TestBoredom:
    """v0.32.0: скука тратит — польза становится НУЖДОЙ, а не приятным дополнением.

    Живой вечер 30.09.2026 показал, зачем: запас стоял 0.99 при расходе 0.12/час,
    то есть голод наступил бы через 5.8 часов ОТКРЫТОГО окна — «пусть живёт и
    работает сам» оператор не увидел бы и за вечер, одни мысли про нейроны.
    Теперь находок нет дольше life.bored_after — ставка расхода растёт на
    life.bored_spend. Контроль идёт первым: без скуки тратится ровно время.
    """

    def test_control_newborn_is_not_bored(self, tmp_path):
        """КОНТРОЛЬ: сразу после рождения скуки нет — есть время найти первое."""
        l = _life(tmp_path, bored_after=600, bored_spend=0.9)
        assert l.bored() is False, "новорождённый уже скучает"

    def test_control_switch_off_means_no_boredom(self, tmp_path):
        """КОНТРОЛЬ: рубильник жизни выключен — скуки нет, как нет и голода."""
        l = _life(tmp_path, enabled=False, bored_after=600, bored_spend=0.9)
        l.state["born"] = time.time() - 100000
        assert l.bored() is False

    def test_silence_makes_it_bored(self, tmp_path):
        l = _life(tmp_path, bored_after=600, bored_spend=0.9)
        l.state["born"] = time.time() - 601
        assert l.bored() is True
        assert l.idle_seconds() >= 600

    def test_boredom_drains_faster_than_time(self, tmp_path):
        """Час скуки стоит spend_per_hour + bored_spend — это и есть нужда."""
        l = _life(tmp_path, spend_per_hour=0.12, bored_spend=0.9, bored_after=600,
                  max_gap_seconds=100000)
        l.state["reserve"], l.state["at"], l.state["born"] = 1.0, 1000.0, 0.0
        spent = l.metabolize(now=1000.0 + 600)      # десять минут скуки
        assert abs(spent - 1.02 * 600 / 3600) < 1e-6, spent

    def test_control_without_boredom_only_time_is_spent(self, tmp_path):
        """КОНТРОЛЬ: скука выключена (bored_after=0) — тратится только время."""
        l = _life(tmp_path, spend_per_hour=0.12, bored_spend=0.9, bored_after=0,
                  max_gap_seconds=100000)
        l.state["reserve"], l.state["at"] = 1.0, 1000.0
        spent = l.metabolize(now=1000.0 + 3600)
        assert abs(spent - 0.12) < 1e-6, spent

    def test_food_ends_boredom(self, tmp_path):
        """Находка снимает скуку: иначе голод было бы не закрыть никогда."""
        l = _life(tmp_path, bored_after=600, bored_spend=0.9)
        l.state["born"], l.state["reserve"] = time.time() - 5000, 0.5
        assert l.bored() is True
        l.feed(0.1, "peers 5 new")
        assert l.bored() is False, "еда не сняла скуку"

    def test_full_to_hungry_is_about_fifty_minutes(self, tmp_path):
        """Смысл правки числами: от полного до голода не шесть часов, а ~50 минут."""
        l = _life(tmp_path, spend_per_hour=0.12, bored_spend=0.9, bored_after=600,
                  hungry_at=0.30, max_gap_seconds=100000)
        # Время в пробе своё: рождение в 1000.0, дальше идём минутами. Так проба не
        # зависит от часов машины, а «скучно» наступает ровно через bored_after.
        l.state["reserve"], l.state["at"], l.state["born"] = 1.0, 1000.0, 1000.0
        minute = 60
        i = 0
        for i in range(1, 91):
            l.metabolize(now=1000.0 + i * minute)
            if l.hungry():
                break
        assert l.hungry(), "за полтора часа так и не проголодалась"
        assert 45 <= i <= 58, f"голод на {i}-й минуте — числа разъехались"

    def test_the_pace_is_a_lever_in_settings(self, tmp_path):
        """Рычаг в настройках: bored_after меняется на ходу (как галки, v0.31.0)."""
        l = _life(tmp_path, bored_after=600, bored_spend=0.9)
        l.state["born"] = time.time() - 700
        assert l.bored() is True
        l.reload(_cfg(bored_after=3600, bored_spend=0.9))
        assert l.bored() is False, "правка потолка не дошла до запаса"
