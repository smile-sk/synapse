"""Synapse — тесты v0.17.0: КОПИЯ ПРОМПТА НЕ ЕСТЬ МЫСЛЬ.

Слово Стаса 25.09.2026: «почему она постоянно повторяет „Заметь, сколько прошло
времени.“ — ей какие то вопросы задаются постоянно?»

Ответ: да, вопросы задаются каждый такт (это поток), и эта фраза — МОЯ ЖЕ строка
промпта, ракурс наблюдения. Малая модель (в q2_k это было особенно заметно) склонна ПРОДОЛЖАТЬ поданный текст вместо
ответа: живой журнал дал три записи brain.thought подряд с этой фразой.

Лечение (v0.17.0): ракурс стал короткой МЕТКОЙ, в промпт добавлен прямой запрет
копирования, а копия ловится механически — она не выдаётся за мысль (журнал
brain.echo вместо brain.thought), и после трёх копий подряд мозг замолкает на
cooldown (brain.echo_limit / brain.echo_cooldown): существо живёт телом, а не
молотит одно и то же.

Что проверяется — КОНТРОЛЬ первым, как везде в проекте:

  * КОНТРОЛЬ: настоящая мысль копией НЕ считается. Без этого «эхо найдено» ничего
    не доказывает: тест прошёл бы и на детекторе, который эхом зовёт всё подряд;
  * КОНТРОЛЬ: одно слово не судим — «да» копией промпта быть не может;
  * КОНТРОЛЬ: настоящая мысль по-прежнему ЛОЖИТСЯ в журнал как brain.thought;
  * строка из живого журнала («Заметь, сколько прошло времени.») — эхо;
  * метка ракурса — эхо (то, что видно в потоке чаще всего);
  * пересказ описания тела с ДРУГИМИ числами — тоже эхо: концы строк не сходятся,
    поэтому судим по словам ПОДРЯД, а не по равенству текста;
  * копия системного промпта (modelfile.md) — тоже эхо;
  * в промпте нет длинных повелительных фраз-ракурсов: ракурс короткий, запрет
    копирования сказан прямо;
  * три копии подряд — мозг замолкает (brain.muted) и больше НЕ спрашивается;
  * КОНТРОЛЬ: при cooldown 0 молчания нет, мозг спрашивают и дальше — значит
    останавливает именно наш механизм, а не что-то ещё;
  * молчание — СОСТОЯНИЕ, а не событие: журнал от него не пухнет.

Запуск: pytest tests   (не pythonw.exe — там нет консольных дескрипторов)
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import audit, i18n                              # noqa: E402
from synapse.agent import Synapse                            # noqa: E402
from synapse.body import Body                                # noqa: E402
from live_data import temp_live                              # noqa: E402


class StubBrain:
    """Мозг-заглушка: отвечает ровно тем, чем велено, и считает вызовы."""

    def __init__(self, answer="живая мысль"):
        self.answer = answer
        self.calls = 0

    def mode(self):
        return "off"                       # такт не поднимает сервер

    def available(self, timeout=3.0, ttl=2.0):
        return True

    def think(self, prompt, system="", history=None):
        self.calls += 1
        return self.answer


def _copy_text() -> str:
    """Фраза, которая есть в промпте ВСЕГДА — годится как «копия» в любом такте."""
    return i18n.t("ag.observe.noecho")


def _genuine_text() -> str:
    """Настоящая мысль: слов из промпта в ней почти нет (одно имя нейрона)."""
    return "Я замечаю, что VD11 держится дольше остальных, и мне это любопытно"


def _syn(br, **cfg):
    """Организм с заглушкой мозга. Тело живое — иначе первым сработает инстинкт."""
    base = {"idle_thinking": True, "idle_think_seconds": 0,
            "instinct_min_seconds": 3600, "tick_log_seconds": 3600}
    base.update(cfg)
    s = Synapse(cfg=base, body=Body.load(), brain=br)
    s.body.stimulus("проснулась", amp=1.0, k=6)
    return s


def _state(s, ticks=7):
    s.save_state({"day": date.today().isoformat(), "wakeups": 0, "total_ticks": ticks})


class TestEchoDetector(unittest.TestCase):
    """Механическая проверка «это копия промпта» — с контрольными случаями."""

    def setUp(self):
        i18n.set_lang("ru")

    def tearDown(self):
        i18n.set_lang("en")

    def _prompt(self, ticks=7):
        with temp_live():
            s = _syn(StubBrain())
            _state(s, ticks)
            return s._thought_prompt()

    def test_control_a_real_thought_is_not_an_echo(self):
        """КОНТРОЛЬ: настоящая мысль копией не считается — иначе детектор бесполезен."""
        prompt = self._prompt()
        self.assertIsNone(Synapse._echo_of(_genuine_text(), prompt,
                                          "Ты — Synapse, небольшой организм."))

    def test_control_one_word_is_not_judged(self):
        """Одно слово копией промпта быть не может — судить не о чем."""
        self.assertIsNone(Synapse._echo_of("да", self._prompt()))
        self.assertIsNone(Synapse._echo_of("", self._prompt()))

    def test_control_the_lens_words_alone_are_not_an_echo(self):
        """КОНТРОЛЬ: мысль, где из промпта только «ракурс», копией не считается."""
        self.assertIsNone(Synapse._echo_of("ракурс мне сейчас не важен", self._prompt()))

    def test_the_line_from_the_live_journal_is_an_echo(self):
        """Та самая фраза из живого журнала 25.09.2026 — копия промпта."""
        prompt = ("Тело: зажжено 12/302 нейронов, энергия 0.0100. Намерение: тело спокойно. "
                  "Заметь, сколько прошло времени. Ответь одним предложением.")
        self.assertIsNotNone(Synapse._echo_of("Заметь, сколько прошло времени.", prompt))

    def test_the_lens_label_of_this_tick_is_an_echo(self):
        """Метка ракурса берётся в такте, когда она в промпте, — и это копия."""
        prompt = self._prompt(ticks=7)                 # 7 % 3 == 1 → ракурс «время»
        self.assertIn(i18n.t("ag.observe.time"), prompt)
        found = Synapse._echo_of(i18n.t("ag.observe.time"), prompt)
        self.assertIsNotNone(found, "метка ракурса не поймана как копия")
        self.assertIn("ракурс", found)

    def test_body_talk_is_NOT_an_echo(self):
        """КОНТРОЛЬ (переписан 26.09.2026): ответ ПРО ТЕЛО копией не считается.

        Прежняя версия этого теста требовала обратного — и была неправа: она
        закрепляла ровно ту поломку, из-за которой вкладка «Мысли» была пуста.
        Живой журнал 26.09.2026, 14:44:10: brain.answer chars=672 рядом с brain.echo —
        существо сказало про своё тело, а его ответ выбросили как копию промпта.
        Числа в задании — это ДАННЫЕ тела, и называть их — работа существа.
        Копия — возврат МОИХ СЛОВ (инструкции), и это проверяют тесты ниже.
        """
        prompt = ("Тело: зажжено 302/302 нейронов, энергия 0.0003; громче всех VD11(0.034), "
                  "VD12(0.029). Намерение: тело спокойно. Не копируй мои слова: они не ответ.")
        answer = ("Тело: зажжено 302 нейронов, энергия 0.0000; громче всех VD11(0.030), "
                  "VD12(0.029)")
        self.assertIsNone(Synapse._echo_of(answer, prompt))

    def test_copy_of_the_system_prompt_is_an_echo(self):
        """Копия системного промпта (modelfile.md) — тоже не мысль."""
        system = ("Ты — Synapse, небольшой организм с нервной системой червя. "
                  "Отвечай по делу, без выдумок.")
        answer = "Ты — Synapse, небольшой организм с нервной системой червя."
        self.assertIsNotNone(Synapse._echo_of(answer, "Тело: зажжено 3 нейрона.", system))

    def test_prompt_has_a_short_lens_and_a_ban_on_copying(self):
        """Ракурс короткий, запрет копирования сказан прямо — это и есть лечение."""
        prompt = self._prompt()
        lenses = [i18n.t(k) for k in ("ag.observe.body", "ag.observe.time", "ag.observe.memory")]
        self.assertTrue(any(l in prompt for l in lenses), "ракурс пропал из промпта")
        self.assertTrue(all(len(l) <= 25 for l in lenses),
                        "ракурс снова стал длинной фразой — её и скопируют")
        self.assertIn(i18n.t("ag.observe.noecho"), prompt, "в промпте нет запрета копирования")


class TestEchoInTheStream(unittest.TestCase):
    """Что происходит с копией в потоке: журнал, строка ответа, молчание."""

    def setUp(self):
        i18n.set_lang("ru")

    def tearDown(self):
        i18n.set_lang("en")

    def test_control_a_real_thought_lands_in_the_journal(self):
        """КОНТРОЛЬ: настоящая мысль пишется как brain.thought, эха нет."""
        with temp_live():
            brain = StubBrain(answer=_genuine_text())
            s = _syn(brain)
            _state(s, 1)
            said = s.idle_thought()
            tail = audit.tail(60)
        self.assertEqual(said, _genuine_text())
        thoughts = [r for r in tail if r.get("event") == "brain.thought"]
        self.assertEqual(len(thoughts), 1, "настоящая мысль не попала в журнал")
        self.assertEqual(thoughts[0].get("text"), _genuine_text())
        self.assertEqual([r for r in tail if r.get("event") == "brain.echo"], [],
                         "настоящую мысль приняли за копию промпта")
        self.assertEqual(s._last_idle_status, "thinking",
                         "настоящая мысль не отмечена как мысль")

    def test_copy_is_not_written_as_a_thought(self):
        """Копия промпта попадает в журнал как brain.echo, а не как мысль."""
        with temp_live():
            brain = StubBrain(answer=_copy_text())
            s = _syn(brain)
            _state(s, 7)
            said = s.idle_thought()
            tail = audit.tail(60)
        self.assertNotEqual(said, _copy_text(), "копия выдана как мысль существа")
        self.assertEqual([r for r in tail if r.get("event") == "brain.thought"], [],
                         "копия записана в журнал как мысль")
        echoes = [r for r in tail if r.get("event") == "brain.echo"]
        self.assertEqual(len(echoes), 1, f"копий в журнале: {len(echoes)}")
        self.assertIn("не копируй мои слова", echoes[0].get("copied_from", "").lower())
        self.assertEqual(s._last_idle_status, "echo")

    def test_three_copies_mute_the_brain(self):
        """Три копии подряд — мозг замолкает, и его больше не спрашивают."""
        with temp_live():
            brain = StubBrain(answer=_copy_text())
            s = _syn(brain)
            for i in (7, 8, 9):
                _state(s, i)
                s.idle_thought()
            asked = brain.calls
            muted = [r for r in audit.tail(80) if r.get("event") == "brain.muted"]
            said = ""
            for i in (10, 11):
                _state(s, i)
                said = s.idle_thought()
        self.assertEqual(len(muted), 1, "молчание не записано в журнал")
        self.assertEqual(brain.calls, asked, "мозг спрашивают, хотя он замолчал")
        self.assertEqual(said, i18n.t("ag.muted"))
        self.assertEqual(s._last_idle_status, "muted")

    def test_control_no_cooldown_keeps_asking(self):
        """КОНТРОЛЬ: при cooldown 0 молчания нет — значит останавливает наш механизм."""
        with temp_live():
            brain = StubBrain(answer=_copy_text())
            s = _syn(brain, brain={"echo_limit": 2, "echo_cooldown": 0})
            for i in range(5):
                _state(s, 7 + i)
                s.idle_thought()
            muted = [r for r in audit.tail(80) if r.get("event") == "brain.muted"]
        self.assertEqual(brain.calls, 5, "мозг замолчал, хотя cooldown 0")
        self.assertEqual(muted, [], "молчание включилось вопреки настройке")

    def test_muting_is_a_state_not_an_event(self):
        """Молчание не пухнет в журнал: за три такта максимум одна строка.

        Строка tick status=muted каждый такт — это ровно та плоская линия, из-за
        которой оператор читал «постоянно idle» и не видел настоящих событий.
        """
        with temp_live():
            brain = StubBrain(answer=_copy_text())
            s = _syn(brain, brain={"echo_limit": 1, "echo_cooldown": 600})
            _state(s, 7)
            s.idle_thought()                        # первая же копия — молчание
            for _ in range(3):
                s.tick()
            rows = [r for r in audit.tail(200)
                    if r.get("event") == "tick" and r.get("status") == "muted"]
        self.assertLessEqual(len(rows), 1,
                             f"молчание пишется каждый такт ({len(rows)} строк) — плоская линия вернулась")


if __name__ == "__main__":
    unittest.main()
