"""Тесты САМОЗАДАНИЯ — работа, которую голод ставит себе сам (v0.28.0, этап M3).

Слово Стаса 30.09.2026: «зачем по твоему мы ему давали инстинкт самовыживания?» — и
«нужно это сделать… пусть живет и работает».

Здесь проверяется не «код запускается», а ПОВЕДЕНИЕ:

  * КОНТРОЛЬ первым: сытое существо не берёт работу себе вовсе; выключенный рубильник —
    тоже; занятая очередь оператора — тоже;
  * голодное СТАВИТ себе миссию, и она видна в общей очереди (её можно снять кнопкой);
  * потолки держат: не чаще, чем раз в N секунд, и не больше N за сутки;
  * сначала продолжается СВОЯ зацепка (цель), и миссия идёт БЕЗ логина и команды —
    то есть смотреть, а не управлять;
  * чужая цель работой не становится: это «спросить оператора».
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                            # noqa: E402

from synapse import audit, config, i18n, leads, life, missions, work   # noqa: E402
from synapse.agent import Synapse                        # noqa: E402
from live_data import temp_live                          # noqa: E402


@pytest.fixture(autouse=True)
def _russian():
    i18n.set_lang("ru")
    yield
    i18n.set_lang("en")


def _cfg(**life_over):
    cfg = config.load()
    cfg["brain"] = dict(cfg.get("brain", {}), mode="off")
    cfg["life"] = dict(config.DEFAULTS["life"], **life_over)
    return cfg


def _syn(reserve=0.2, **life_over):
    """Существо с заданным запасом. Работу оно берёт ТОЛЬКО при включённом мозге-режиме
    off — то есть без обращения к модели: самозадание должно жить без видеокарты."""
    syn = Synapse(cfg=_cfg(**life_over))
    syn.life.state["reserve"] = reserve
    return syn


class TestWhenItWorks:
    def test_fed_creature_takes_no_work(self):
        """КОНТРОЛЬ: сытое существо не выдумывает себе дела."""
        syn = _syn(reserve=0.9)
        assert work.why_not(syn.cfg, syn.life) == "fed"
        with temp_live():
            out = syn.tick(with_mission=True)
            events = [r["event"] for r in audit.tail(20)]
            queued = missions.queued()
        assert out.get("self_work") is None and queued == []
        assert "self.work" not in events

    def test_switch_off_means_no_self_work(self):
        """Рубильник выключен — голод не ставит работу (один рубильник, одно поведение)."""
        syn = _syn(reserve=0.01, self_work=False)
        assert work.why_not(syn.cfg, syn.life) == "off"
        assert work.choose(syn.cfg, syn.life) is None

    def test_hunger_sets_a_mission_in_the_common_queue(self):
        """Голод ставит себе работу — и она лежит в ОБЩЕЙ очереди, рядом с делами Стаса."""
        syn = _syn(reserve=0.2)
        with temp_live():
            out = syn.tick(with_mission=True)
            queued = missions.queued()
            events = [r for r in audit.tail(30) if r["event"] == "self.work"]
        assert out["status"] == "selfwork", out
        assert out["self_work"] and len(queued) == 1
        assert queued[0]["kind"] == "peers", queued[0]
        assert "поставлено голодом" in str(queued[0]["note"])
        assert events and events[0]["kind"] == "peers" and events[0]["reserve"] <= 0.2

    def test_body_keeps_the_ledger_of_its_own_work(self):
        """Счётчик дня ведётся: сколько своих дел взято и когда последнее."""
        syn = _syn(reserve=0.2)
        assert syn.life.self_work_left(syn.cfg) == 8
        with temp_live():
            syn.tick(with_mission=True)
        st = syn.life.self_work_state()
        assert st["count"] == 1 and st["kind"] == "peers" and st["last"]
        assert syn.life.self_work_left(syn.cfg) == 7


class TestCeilings:
    def test_not_oftener_than_the_interval(self):
        """Второй раз подряд работа не берётся — и почему, видно в журнале."""
        syn = _syn(reserve=0.2, self_work_seconds=1800)
        with temp_live():
            syn.tick(with_mission=True)
            missions.mark(missions.queued()[0]["id"], "done")     # первую закрыли
            out2 = syn.tick(with_mission=True)
            events = [r for r in audit.tail(30) if r["event"] == "self.blocked"]
            queued = missions.queued()
        assert out2.get("self_work") is None and queued == []
        assert events and events[0]["why"] == "interval"

    def test_daily_cap_holds(self):
        """Потолок на сутки: за ним существо не берёт работу, даже если голодно."""
        syn = _syn(reserve=0.2, self_work_max=1, self_work_seconds=0)
        with temp_live():
            syn.tick(with_mission=True)
            missions.mark(missions.queued()[0]["id"], "done")
            assert work.why_not(syn.cfg, syn.life) == "cap"
            out2 = syn.tick(with_mission=True)
            events = [(r["event"], r.get("why")) for r in audit.tail(30)]
        assert out2.get("self_work") is None
        assert ("self.blocked", "cap") in events

    def test_the_operators_queue_comes_first(self):
        """Есть дело оператора — своё не ставится: чужая работа впереди."""
        syn = _syn(reserve=0.2)
        with temp_live():
            missions.add("note", {"text": "дело Стаса"})
            out = syn.tick(with_mission=True)
            events = [r["event"] for r in audit.tail(30)]
            left = missions.queued()
        assert out["mission"] and out["status"] == "done", out
        assert "self.work" not in events
        assert left == [], "своё дело не должно появиться рядом с делом оператора"


class TestPreference:
    def test_own_lead_is_taken_first_and_read_only(self):
        """Сначала продолжается своя зацепка — и миссия идёт БЕЗ логина и команды."""
        with temp_live():
            leads.remember([{"target": "192.168.1.88", "what": "host",
                             "why": "open=22,445", "mine": True}])
            syn = _syn(reserve=0.2)
            plan = work.choose(syn.cfg, syn.life)
            out = syn.tick(with_mission=True)
            queued = missions.queued()
        assert plan["kind"] == "remote" and plan["params"] == {"host": "192.168.1.88"}
        assert "password" not in plan["params"] and "command" not in plan["params"]
        assert "свою зацепку" in plan["because"]
        assert queued[0]["kind"] == "remote", queued[0]

    def test_cert_lead_asks_for_the_certificate(self):
        """Зацепка «сертификат» становится проверкой сертификата, а не чем попало."""
        with temp_live():
            leads.remember([{"target": "example.com", "what": "cert", "port": 8443,
                             "why": "cert expires in 7 d", "mine": True}])
            syn = _syn(reserve=0.2)
            plan = work.choose(syn.cfg, syn.life)
        assert plan["kind"] == "certs" and plan["params"]["host"] == "example.com"
        assert plan["params"]["ports"] == [8443]

    def test_alien_lead_is_not_work(self, monkeypatch):
        """Чужая цель работой не становится: это «спросить оператора»."""
        monkeypatch.setattr(work.net_scan, "local_cidr", lambda: None)
        cfg = _cfg()
        cfg["peers"] = dict(cfg["peers"], enabled=False)
        with temp_live():
            leads.remember([{"target": "8.8.8.8", "what": "peer",
                             "why": "frequent", "mine": False}])
            syn = Synapse(cfg=cfg)
            syn.life.state["reserve"] = 0.2
            plan = work.choose(cfg, syn.life)
        assert plan is None, "чужая цель не должна становиться миссией"

    def test_peers_snapshot_is_taken_when_stale(self):
        """Нет зацепок и снимка давно не было — существо идёт смотреть соединения."""
        with temp_live():
            syn = _syn(reserve=0.2)
            plan = work.choose(syn.cfg, syn.life)
            syn.life.note_run("peers")
            second = work.choose(syn.cfg, syn.life)
        assert plan["kind"] == "peers" and plan["why"] == "peers"
        assert "ни разу не снимала" in plan["because"]
        assert second is None or second["kind"] != "peers", second

    def test_own_subnet_is_the_last_resort(self, monkeypatch):
        """Совсем нет зацепок и снимок свежий — обходим свою сеть."""
        monkeypatch.setattr(work.net_scan, "local_cidr", lambda: "192.168.1.0/24")
        with temp_live():
            syn = _syn(reserve=0.2)
            syn.life.note_run("peers")
            plan = work.choose(syn.cfg, syn.life)
        assert plan["kind"] == "net_audit" and plan["params"]["cidr"] == "192.168.1.0/24"

    def test_a_researched_lead_is_not_taken_twice(self):
        """Отработанная цель не берётся снова — существо не бегает по кругу."""
        with temp_live():
            leads.remember([{"target": "192.168.1.88", "what": "host",
                             "why": "open=22", "mine": True}])
            leads.mark("192.168.1.88", "host", mission="m-1")
            syn = _syn(reserve=0.2)
            syn.life.note_run("peers")
            plan = work.choose(syn.cfg, syn.life)
        assert plan is None or plan["kind"] != "remote", plan
