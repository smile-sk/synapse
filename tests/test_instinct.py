"""Тесты инстинкта самосохранения и нумерации миссий (слово Стаса 25.09.2026).

Стас: «нумерацию в таблице главное поменяй — не логично: по времени у тебя вверху
последнее событие, но 1» и «он ничего не делает, даже не думает — дай ему хоть
какой-то инстинкт самосохранения».

Порядок тот же, что везде в проекте: ПЕРВЫМ идёт контроль (что инстинкт правда
меняет тело, а не возвращает строку даром), потом — само поведение.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                            # noqa: E402

from synapse import audit, config, i18n, missions        # noqa: E402
from synapse.agent import Synapse                        # noqa: E402
from synapse.panel_core import PanelCore                 # noqa: E402
from live_data import temp_live                          # noqa: E402


@pytest.fixture(autouse=True)
def _russian():
    """Речь существа проверяем по-русски — это то, что читает Стас."""
    i18n.set_lang("ru")
    yield
    i18n.set_lang("en")


def _quiet():
    """Организм без живого мозга (тесты не жгут GPU и не оставляют сирот)."""
    cfg = config.load()
    cfg["brain"] = dict(cfg.get("brain", {}), mode="off")
    return Synapse(cfg=cfg)


class TestInstinct:
    def test_feed_when_quiet(self):
        """КОНТРОЛЬ: свежее тело мертво. Инстинкт голода оживляет его и пишет в журнал."""
        syn = _quiet()
        assert syn.body.lit() == 0
        assert syn.body.energy() < 0.0005
        with temp_live():
            text = syn.instinct()
            events = [r["event"] for r in audit.tail(20)]
        assert syn.body.lit() > 0, "инстинкт голода обязан оживить тело"
        assert text, "инстинкт должен вернуть строку для окна"
        assert "instinct.feed" in events, "голод обязан попасть в журнал"

    def test_overload_soothes(self):
        """КОНТРОЛЬ: перегруз (много горящих нейронов) инстинкт гасит — энергия падает."""
        syn = _quiet()
        syn.body.poke(list(range(70)), 0.5)
        energy_before = syn.body.energy()
        assert syn.body.lit() > 60, "контроль: тело должно быть в перегрузе"
        with temp_live():
            text = syn.instinct()
            events = [r["event"] for r in audit.tail(20)]
        assert syn.body.energy() < energy_before, "инстинкт перегруза обязан приглушить тело"
        assert text
        assert "instinct.overload" in events

    def test_quiet_body_returns_empty(self):
        """Спокойное, но живое тело — инстинкт молчит (ничего не делает)."""
        syn = _quiet()
        syn.body.poke([0, 1], 0.2)          # чуть живо, но не мертво и не в перегрузе
        with temp_live():
            assert syn.instinct() == ""


class TestIdleThinking:
    class _Brain:
        def __init__(self):
            self.asked = []

        def mode(self):
            return "off"          # такт не зовёт ensure()

        def available(self, timeout=3.0, ttl=2.0):
            return True

        def think(self, prompt, system="", history=None):
            self.asked.append(prompt)
            return "мне спокойно"

    def test_thought_is_logged(self):
        """Мысль существа видна в журнале — раньше оно думало молча (как «овощ»)."""
        cfg = config.load()
        cfg["idle_thinking"] = True
        cfg["idle_think_seconds"] = 0      # мера времени — секунды: 0 = думать сразу
        brain = self._Brain()
        syn = Synapse(cfg=cfg, brain=brain)
        with temp_live():
            syn.tick(with_mission=False)
            events = [r["event"] for r in audit.tail(30)]
        assert brain.asked, "мозг должен был подумать"
        assert "brain.thought" in events, "мысль обязана попасть в журнал"


class TestNumbering:
    def test_seq_newest_highest(self):
        """Номер растёт со временем: самая новая миссия несёт высший номер."""
        core = PanelCore()
        with temp_live():
            a = missions.add("note", {"text": "первая"})
            b = missions.add("note", {"text": "вторая"})
            rows = core.mission_rows(30)
        seq = {r["id"]: r["seq"] for r in rows}
        assert seq[b["id"]] > seq[a["id"]], "новая миссия должна нести больший номер"
        # и первая (самая старая) начинается с 1
        assert seq[a["id"]] == 1
