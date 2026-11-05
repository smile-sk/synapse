"""v0.25.0: ИНСТИНКТ — РЕФЛЕКС, А НЕ ЗАМЕНА МЫСЛИ.

Живой журнал 26.09.2026, 15:23-15:33 и жалоба Стаса: «мозг на моём вопросе упал и
больше не включался, прошло уже почти 20 мин». Тело стояло мёртвым (energy 0.0,
lit 0), голод срабатывал КАЖДЫЙ такт (разрежение 20 с, а такт 30 с), и `return
instinct` выходил из idle_thought ДО вопроса мозгу: за десять минут ни одной мысли,
в канал «Мысли» и в журнал не приходило ничего.

КОНТРОЛЬ идёт ПЕРВЫМ: заглушка обязана НЕ спрашиваться, когда думание выключено.
Без этого «мозг спрошен» ничего не доказывает — счётчик мог бы быть слепым.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                            # noqa: E402

from synapse import audit, i18n                          # noqa: E402
from synapse.agent import Synapse                        # noqa: E402
from live_data import temp_live                          # noqa: E402


@pytest.fixture(autouse=True)
def _russian():
    """Речь существа проверяем по-русски — это то, что читает Стас."""
    i18n.set_lang("ru")
    yield
    i18n.set_lang("en")


class StubBrain:
    """Заглушка мозга: считает вопросы и умеет молчать."""

    def __init__(self, answer="живая мысль", ready=True):
        self.answer = answer
        self.ready = ready
        self.calls = 0

    def mode(self):
        return "off"          # такт не зовёт ensure — своего сервера тест не поднимает

    def available(self, timeout=3.0, ttl=2.0):
        return self.ready

    def think(self, prompt, system="", history=None):
        self.calls += 1
        return self.answer


def _dead(br, **cfg):
    """Организм с МЁРТВЫМ телом — такт БЕЗ шага тела, как его зовёт панель.

    Важно (нашлось первым прогоном этих тестов): с `step_body=True` такт сам делает
    вдох, тело оживает, и рефлекс голода не срабатывает вовсе — тест мерил бы не тот
    режим. Панель ведёт тело сама (20 кадров в секунду) и зовёт такт с
    step_body=False; именно так в живом журнале 26.09.2026 голод срабатывал каждый
    такт и съедал мысль.
    """
    base = {"idle_thinking": True, "tick_log_seconds": 3600}
    base.update(cfg)
    return Synapse(cfg=base, brain=br)


class TestInstinctDoesNotEatTheThought:
    def test_control_thinking_off_never_asks(self):
        """КОНТРОЛЬ: думание выключено — заглушку не спрашивают вовсе."""
        br = StubBrain()
        syn = _dead(br, idle_thinking=False)
        with temp_live():
            out = syn.tick(step_body=False)
        assert br.calls == 0, "счётчик вопросов слепой: спрашивают и при выключенном думании"
        assert out["status"] == "fed", f"рефлекс не сработал: {out['status']}"

    def test_dead_body_still_thinks(self):
        """ГЛАВНОЕ: мёртвое тело (голод каждый такт) мысль НЕ отменяет."""
        br = StubBrain()
        syn = _dead(br)
        with temp_live():
            out = syn.tick(step_body=False)
            events = [r.get("event") for r in audit.tail(30)]
        assert "instinct.feed" in events, "рефлекс голода не сработал — тест не о том"
        assert br.calls == 1, "инстинкт съел мысль: мозг не спрошен"
        assert out["text"] == br.answer
        assert out["status"] == "thinking"

    def test_reflex_is_the_answer_only_when_the_thought_is_silent(self):
        """Рефлекс становится строкой ответа, только если мысль не сказалась."""
        br = StubBrain(answer="")            # мозг ответил пустотой
        syn = _dead(br)
        with temp_live():
            out = syn.tick(step_body=False)
        assert br.calls == 1
        assert "импульс" in out["text"], f"рефлекс потерялся: {out['text']!r}"


def _alive(br, **cfg):
    """Организм с ЖИВЫМ телом: рефлекс не срабатывает — видно молчание мозга как есть."""
    syn = _dead(br, **cfg)
    syn.body.stimulus("проснулась", amp=1.0, k=6)
    return syn


class TestSilenceIsVisible:
    def test_brain_down_is_named_and_in_the_journal(self):
        """Мозг не отвечает — это СКАЗАНО и видно в журнале, а не молчание."""
        br = StubBrain(ready=False)
        syn = _alive(br)
        with temp_live():
            out = syn.tick(step_body=False)
            events = [r.get("event") for r in audit.tail(30)]
        assert out["status"] == "nobrain", out["status"]
        assert "brain.down" in events, "молчание мозга не попало в журнал"
        assert i18n.t("ag.nobrain", body="тело") != "ag.nobrain"

    def test_silent_brain_with_a_hungry_body_says_both(self):
        """Мозг молчит, тело голодно: видно и рефлекс, и что мозг не ответил."""
        br = StubBrain(ready=False)
        syn = _dead(br)
        with temp_live():
            out = syn.tick(step_body=False)
            events = [r.get("event") for r in audit.tail(30)]
        assert "instinct.feed" in events, "рефлекс не сработал"
        assert "brain.down" in events, "молчание мозга не попало в журнал"
        assert br.calls == 0, "спрашивать молчащий мозг — значит стоять колом"

    def test_ensure_retry_is_not_five_minutes(self):
        """Разрежение попыток поднять мозг — секунды, а не пять минут."""
        import inspect
        from synapse import brain as brain_mod
        sig = inspect.signature(brain_mod.Brain.ensure)
        default = sig.parameters["retry_after"].default
        assert default <= 60, f"после неудачи ждём {default} с: оператор видит «не включался»"
