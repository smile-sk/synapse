"""v0.18.0: ПОТОК С НИТЬЮ — склейка, память мыслей и отдельный канал 💭.

Слово Стаса 25.09.2026: «делай всё так, как в AgentWorld устроен поток, кроме
вклинивания». Раньше наш «поток» был лупом без паузы: think() слал system + один
вопрос, ИСТОРИИ НЕ БЫЛО ВОВСЕ, каждый такт мозг начинал с нуля. Теперь:

  * СКЛЕЙКА: промпт просит ПРОДОЛЖИТЬ ту же мысль, а не «заметь что-то» заново;
  * НИТЬ: прошлые мысли идут в мозг как сообщения (history) — он помнит свой поток;
  * КАНАЛ 💭: строки с 💭 / [мысль] / (мысль) вынимаются из ответа и пишутся в
    ОТДЕЛЬНЫЙ лог (data/logs/thoughts.log + событие brain.inner), а не в речь/журнал
    (brain.thought). «Думает» и «говорит» — разное, как милана_мышление.лог у Миланы.

КОНТРОЛЬ первым, как везде в проекте.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import audit, i18n                      # noqa: E402
from synapse.agent import Synapse                    # noqa: E402
from synapse.body import Body                        # noqa: E402
from live_data import temp_live                      # noqa: E402


class StubBrain:
    """Мозг-заглушка: отвечает чем велено, запоминает history и промпты."""

    def __init__(self, answer="живая мысль"):
        self.answer = answer
        self.calls = 0
        self.histories = []
        self.prompts = []

    def mode(self):
        return "off"

    def available(self, timeout=3.0, ttl=2.0):
        return True

    def think(self, prompt, system="", history=None):
        self.calls += 1
        self.prompts.append(prompt)
        self.histories.append(list(history or []))
        return self.answer


class CountingBrain(StubBrain):
    """Возвращает РАЗНЫЕ мысли каждый раз — иначе повтор не попадает в нить."""

    def think(self, prompt, system="", history=None):
        self.calls += 1
        self.prompts.append(prompt)
        self.histories.append(list(history or []))
        return f"мысль {self.calls}"


def _syn(br, **cfg):
    base = {"idle_thinking": True, "idle_think_seconds": 0,
            "instinct_min_seconds": 3600, "tick_log_seconds": 3600}
    base.update(cfg)
    s = Synapse(cfg=base, body=Body.load(), brain=br)
    s.body.stimulus("проснулась", amp=1.0, k=6)     # тело живое: голод не мешает
    return s


def _state(s, ticks=7):
    s.save_state({"day": date.today().isoformat(), "wakeups": 0, "total_ticks": ticks})


class TestSplicePrompt(unittest.TestCase):
    """Промпт просит ПРОДОЛЖИТЬ мысль и писать внутреннюю речь 💭."""

    def setUp(self):
        i18n.set_lang("en")

    def tearDown(self):
        i18n.set_lang("en")

    def test_prompt_is_a_continuation_not_a_fresh_question(self):
        with temp_live():
            s = _syn(StubBrain())
            _state(s, 1)
            p = s._thought_prompt()
        self.assertIn("continue the SAME thought", p,
                      "промпт не просит продолжить ту же мысль — это луп, а не поток")
        self.assertIn("💭", p, "в промпте нет инструкции писать внутреннюю речь")
        self.assertIn(i18n.t("ag.observe.noecho"), p)


class TestThread(unittest.TestCase):
    """Нить: прошлые мысли доходят до мозга и копятся с потолком."""

    def test_control_history_really_reaches_the_brain(self):
        """КОНТРОЛЬ: история доходит до think() как сообщения, первый вызов — без неё."""
        br = CountingBrain()
        with temp_live():
            s = _syn(br)
            _state(s, 1)
            s.idle_thought()
            s.idle_thought()
        self.assertEqual(br.calls, 2)
        self.assertEqual(br.histories[0], [], "первый вызов не должен нести нить")
        self.assertEqual(br.histories[1], [{"role": "assistant", "content": "мысль 1"}],
                         "прошлая мысль не попала в нить для мозга")

    def test_thread_accumulates_and_is_capped(self):
        br = CountingBrain()
        with temp_live():
            s = _syn(br, flow={"thread": 3})
            for i in range(6):
                _state(s, 7 + i)
                s.idle_thought()
        self.assertEqual(len(s._thread), 3, "нить не обрезана до потолка")
        self.assertEqual(s._thread, ["мысль 4", "мысль 5", "мысль 6"],
                         "в нити не последние мысли")
        self.assertEqual(len(br.histories[-1]), 3,
                         "мозгу передана не вся нить")

    def test_control_zero_thread_passes_no_history(self):
        """КОНТРОЛЬ: thread=0 отключает нить — иначе тест выше слепой.

        Это и есть грабля `or 6`: 0 это falsy, и «без нити» превращалось в нить из
        шести (тот же класс бага, что `tick_seconds or 60` — запись (10) чейнджлога).
        """
        br = CountingBrain()
        with temp_live():
            s = _syn(br, flow={"thread": 0})
            _state(s, 1)
            s.idle_thought()
            s.idle_thought()
        self.assertEqual(br.histories[1], [], "thread=0 обязан отключить нить")
        self.assertEqual(s._thread, [])


class TestInnerSpeechChannel(unittest.TestCase):
    """Внутренняя речь 💭 идёт в свой лог, а не в речь/журнал."""

    def test_control_split_parses_markers(self):
        """КОНТРОЛЬ: 💭-строки вынимаются, остальное остаётся речью."""
        reply = "💭 сначала посмотрю на тело\nТело спокойно, мне хорошо"
        inner, spoken = Synapse._split_thoughts(reply)
        self.assertEqual(inner, ["сначала посмотрю на тело"])
        self.assertEqual(spoken, "Тело спокойно, мне хорошо")

    def test_inner_thoughts_go_to_their_own_channel(self):
        br = StubBrain(answer="💭 внутри думаю\nМне сейчас любопытно")
        with temp_live():
            s = _syn(br)
            _state(s, 1)
            said = s.idle_thought()
            tail = audit.tail(80)
        self.assertEqual(said, "Мне сейчас любопытно")
        inner = [r for r in tail if r.get("event") == "brain.inner"]
        spoken = [r for r in tail if r.get("event") == "brain.thought"]
        self.assertEqual([r.get("text") for r in inner], ["внутри думаю"])
        self.assertEqual([r.get("text") for r in spoken], ["Мне сейчас любопытно"])
        # и в отдельный файл (как милана_мышление.лог)
        self.assertIn("внутри думаю", "\n".join(audit.thoughts_tail()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
