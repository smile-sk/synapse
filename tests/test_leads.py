"""Тесты ЦЕЛЕЙ — памяти о том, что стоит досмотреть (v0.28.0, этап M4).

Слово Стаса 30.09.2026: «нужно так же чтоб он понимал что из тех отчётов — есть что —
чтобы мог брать например хост как цель для других миссий и исследований».

Отчёт — снимок дня, цель — вопрос к следующему шагу. Здесь проверяется:

  * КОНТРОЛЬ первым: пустой отчёт цели не рождает; повтор не плодит запись;
  * цель рождается из ДЕЛА (отчёт миссии), а не из разговора;
  * у цели есть признак «своё/чужое»: по своему существо работает, чужое — «спросить»;
  * отработанная цель закрывается, а новый отчёт по тому же месту открывает её снова;
  * память не пухнет: старые отработанные уходят первыми.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                            # noqa: E402

from synapse import i18n, leads                           # noqa: E402
from live_data import temp_live                           # noqa: E402


@pytest.fixture(autouse=True)
def _russian():
    i18n.set_lang("ru")
    yield
    i18n.set_lang("en")


AUDIT = {"hosts": [{"ip": "192.168.1.88", "open": [22, 445], "banners": {}, "shares": []},
                   {"ip": "192.168.1.90", "open": [], "banners": {}, "shares": []}]}
REMOTE = {"host": "203.0.113.10", "ways": [{"method": "ssh", "port": 22, "open": True},
                                           {"method": "winrm", "port": 5985, "open": False}]}
CERTS = {"targets": [{"host": "example.com", "port": 443, "ok": True, "days_left": 12},
                     {"host": "old.example", "port": 443, "ok": False, "days_left": None},
                     {"host": "far.example", "port": 443, "ok": True, "days_left": 300}]}
PEERS = {"leads": [{"target": "203.0.113.10", "what": "listen", "port": 34119,
                    "why": "listen_unusual", "mine": True},
                   {"target": "142.250.1.5", "what": "peer", "port": None,
                    "why": "frequent", "mine": False}]}
DIFF = {"new_hosts": [{"ip": "192.168.1.44", "open": [3389]}]}


class TestBirth:
    def test_hosts_with_ports_become_targets(self):
        """Аудит нашёл хост с портами — это цель «досмотреть его»."""
        items = leads.from_report("net_audit", AUDIT)
        assert len(items) == 1, items
        assert items[0]["target"] == "192.168.1.88" and items[0]["what"] == "host"
        assert "22,445" in items[0]["why"] and items[0]["mine"] is True

    def test_empty_report_gives_nothing(self):
        """КОНТРОЛЬ: смотреть было нечего — целей нет."""
        assert leads.from_report("net_audit", {"hosts": []}) == []
        assert leads.from_report("net_audit", {}) == []

    def test_remote_ways_and_certificates(self):
        """Открытые пути управления и сроки сертификатов — тоже цели."""
        ways = leads.from_report("remote", REMOTE)
        assert ways and ways[0]["what"] == "ways" and "ssh" in ways[0]["why"]
        cert = leads.from_report("certs", CERTS)
        assert sorted(i["target"] for i in cert) == ["example.com", "old.example"], cert

    def test_diff_and_peers(self):
        """Новый хост в своей сети и находки соединений — цели того же слоя."""
        new = leads.from_report("diff", DIFF)
        assert new and new[0]["target"] == "192.168.1.44"
        from_peers = leads.from_report("peers", PEERS)
        assert [i["mine"] for i in from_peers] == [True, False]
        assert from_peers[0]["port"] == 34119


class TestMemory:
    def test_repeat_does_not_breed(self):
        """КОНТРОЛЬ: та же цель второй раз — одна запись и счётчик раз."""
        with temp_live():
            first = leads.remember(leads.from_report("net_audit", AUDIT))
            second = leads.remember(leads.from_report("net_audit", AUDIT))
            items = leads.all_leads()
        assert first["new"] == 1 and second["new"] == 0 and second["known"] == 1
        assert len(items) == 1 and items[0]["times"] == 2

    def test_researched_goal_closes_and_reopens(self):
        """Закрытую цель закрываем, а новый отчёт по тому же месту открывает снова."""
        with temp_live():
            leads.remember(leads.from_report("net_audit", AUDIT))
            assert leads.stats()["open"] == 1
            assert leads.mark("192.168.1.88", "host", mission="m-1") == 1
            assert leads.stats()["open"] == 0
            leads.remember(leads.from_report("net_audit", AUDIT))
            item = leads.all_leads()[0]
        assert item["state"] == "open" and item["mission"] is None

    def test_mine_and_alien_are_kept_apart(self):
        """Своё открыто для дела, чужое — только «спросить оператора»."""
        with temp_live():
            leads.remember(leads.from_report("peers", PEERS))
            mine = leads.open_leads(mine=True)
            alien = leads.open_leads(mine=False)
            st = leads.stats()
        assert [i["target"] for i in mine] == ["203.0.113.10"]
        assert [i["target"] for i in alien] == ["142.250.1.5"]
        assert st["open"] == 2 and st["mine"] == 1 and st["alien"] == 1

    def test_memory_does_not_swell(self, monkeypatch):
        """Потолок памяти: места хватает, но старые отработанные уходят первыми."""
        monkeypatch.setattr(leads, "KEEP", 2)
        with temp_live():
            for i in range(3):
                leads.remember([{"target": f"10.0.0.{i}", "what": "host", "port": None,
                                 "why": "open=22", "mine": True}])
            assert len(leads.all_leads()) == 2
            leads.mark("10.0.0.1", "host")
            leads.mark("10.0.0.2", "host")
            leads.remember([{"target": "10.0.0.9", "what": "host", "port": None,
                             "why": "open=22", "mine": True}])
            left = {i["target"] for i in leads.all_leads()}
        assert "10.0.0.9" in left and len(left) == 2, left

    def test_missing_file_is_not_an_error(self):
        """Файла целей нет — слой просто пуст, и это не поломка."""
        with temp_live():
            assert leads.all_leads() == [] and leads.stats()["total"] == 0
            assert leads.open_leads(mine=True) == []
