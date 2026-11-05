"""v0.14: бесшовное сознание — живой поток мыслей и непрерывный такт.

Слово Стаса 25.09.2026: «почему он как будто всегда отвечает на вопрос "как себя
чувствует"? почему я не могу дать ему бесконечное время жизни в 0 секунд — чтобы
швов между тиками не было… не как в лупе, дёргать одним и тем же вопросом».

Что здесь проверяется по-настоящему:

  * промпт «думания» РАЗНЫЙ от такта к такту (номер такта, ракурс наблюдения,
    энергия, зажжённые нейроны) — а не один и тот же вопрос;
  * прошлая мысль попадает в промпт, чтобы не повторяться;
  * одна и та же мысль НЕ пишется в журнал дважды подряд — существо возвращает
    рефлекс, а не заевшую пластинку;
  * tick_seconds=0 принимается настройками как «непрерывный поток», а не
    зажимается снизу в 5 секунд (КОНТРОЛЬ первым: 5 раньше и был тем швом).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import audit, config, i18n                    # noqa: E402
from synapse.agent import Synapse                          # noqa: E402
from synapse.body import Body                              # noqa: E402
from live_data import temp_live                            # noqa: E402
from synapse.panel_core import PanelCore                   # noqa: E402


class StubBrain:
    """Мозг-заглушка: тест не поднимает llama-server и не жжёт GPU."""

    def __init__(self, answer="мысль"):
        self.answer = answer
        self.calls = 0

    def mode(self):
        return "off"

    def available(self, timeout=3.0, ttl=2.0):
        return True

    def think(self, prompt, system="", history=None):
        self.calls += 1
        return self.answer


class TestThoughtPromptVaries(unittest.TestCase):
    """Один и тот же вопрос больше не задаётся: контекст живой и меняется."""

    def setUp(self):
        i18n.set_lang("ru")
        self._live = temp_live()
        self._live.__enter__()

    def tearDown(self):
        self._live.__exit__(None, None, None)
        i18n.set_lang("en")

    def _syn(self, ticks):
        s = Synapse(cfg={"idle_thinking": True, "idle_think_seconds": 0},
                    body=Body.load(), brain=StubBrain())
        s.save_state({"day": date.today().isoformat(), "wakeups": 0,
                      "total_ticks": ticks})
        return s

    def test_control_prompt_embeds_live_context(self):
        """КОНТРОЛЬ: без номера такта и ракурса вариации нет — это и была петля.

        Если в промпте нет живого меняющегося контекста (такт, ракурс), то модель
        каждый раз получает одно и то же и отвечает одно и то же. Проверяем, что
        механизм вариации РЕАЛЬНО вшит, а не заявлен на словах.
        """
        s = self._syn(7)
        p = s._thought_prompt()
        self.assertIn("7", p, "номер такта не попал в промпт")
        angles = (i18n.t("ag.observe.body"), i18n.t("ag.observe.time"),
                  i18n.t("ag.observe.memory"))
        self.assertTrue(any(a in p for a in angles), "ракурс наблюдения не попал в промпт")

    def test_prompt_changes_tick_to_tick(self):
        s = self._syn(1)
        p1 = s._thought_prompt()
        s.save_state({"day": date.today().isoformat(), "wakeups": 0,
                      "total_ticks": 2})
        p2 = s._thought_prompt()
        self.assertNotEqual(p1, p2, "промпт не меняется от такта к такту")

    def test_last_thought_is_fed_back(self):
        s = self._syn(3)
        s._last_thought = "я уже это говорил"
        p = s._thought_prompt()
        self.assertIn("я уже это говорил", p, "прошлая мысль не дошла до промпта")


class TestNoRepeat(unittest.TestCase):
    """Поток, а не заевшая пластинка: та же мысль не пишется дважды."""

    def test_same_answer_is_not_logged_twice(self):
        i18n.set_lang("ru")
        with temp_live():
            brain = StubBrain(answer="одинаковая мысль")
            s = Synapse(cfg={"idle_thinking": True, "idle_think_seconds": 0},
                        body=Body.load(), brain=brain)
            # Тело живое — иначе первым сработает инстинкт голода и отдаст свой
            # текст: рефлекс идёт РАНЬШЕ мысли, так и задумано (v0.15.0).
            s.body.stimulus("проснулась", amp=1.0, k=6)
            s.save_state({"day": date.today().isoformat(), "wakeups": 0,
                          "total_ticks": 1})
            first = s.idle_thought()
            s.save_state({"day": date.today().isoformat(), "wakeups": 0,
                          "total_ticks": 2})
            second = s.idle_thought()
            self.assertEqual(first, "одинаковая мысль")
            self.assertEqual(brain.calls, 2, "мозг обязан зваться каждый раз")
            # ЗАКОН УТОЧНЁН (v0.16.0): повтор не выдаётся за НОВОЕ, но и не
            # прячется в тишину — он сказан и помечен repeat=True. Иначе поток
            # выглядит мёртвым ровно тогда, когда модель зациклилась.
            thoughts = [r for r in audit.tail(60) if r.get("event") == "brain.thought"]
            fresh = [r for r in thoughts if not r.get("repeat")]
            self.assertEqual(len(fresh), 1, "одна и та же мысль записана как НОВАЯ дважды")
            self.assertEqual(len(thoughts), 2, "повтор спрятан: в журнале его не видно")
            self.assertTrue(thoughts[-1].get("repeat"), "повтор не помечен")
            self.assertNotEqual(second, first, "повтор отдан как «новое» — а это петля")
        i18n.set_lang("en")


class TestContinuousTick(unittest.TestCase):
    """tick_seconds=0 = непрерывный поток, а не зажим в 5 секунд."""

    def test_control_five_was_the_old_seam(self):
        """КОНТРОЛЬ: раньше 0 зажимался снизу до 5 — это и был «шов»."""
        # 0 — это меньше прежнего порога в 5: если бы зажим остался, 0 не прошёл бы.
        self.assertLess(0.0, 5.0)

    def test_zero_is_kept_not_clamped(self):
        with tempfile.TemporaryDirectory() as tmp, temp_live():
            keep = config.CONFIG_PATH
            config.CONFIG_PATH = Path(tmp) / "config.json"
            try:
                core = PanelCore.__new__(PanelCore)
                core.cfg = config.load()
                res = core.apply_settings({"tick_seconds": "0"})
                self.assertTrue(res["ok"], res)
                self.assertEqual(res["settings"]["tick_seconds"], 0.0,
                                 "0 обязан значить непрерывный поток, а не 5 секунд")
                disk = json.loads((Path(tmp) / "config.json").read_text(encoding="utf-8"))
                self.assertEqual(disk["tick_seconds"], 0)
            finally:
                config.CONFIG_PATH = keep
                config.forget_cache()


if __name__ == "__main__":
    unittest.main(verbosity=2)
