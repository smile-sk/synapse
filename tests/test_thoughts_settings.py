"""v0.21.0: мысль узнаётся по метке в ЛЮБОМ месте строки, а настройки говорят фразой.

    python tests/test_thoughts_settings.py        (запускать из корня проекта)

Что здесь проверяется по-настоящему (жалобы оператора 26.09.2026):

  * КОНТРОЛЬ первым: обычная фраза БЕЗ метки мыслью не считается — иначе
    «мысли теперь видны» ничего не значит;
  * «**💭** Внутреннее рассуждение: …» — НАСТОЯЩИЙ ответ модели из живого журнала
    13:34 — обязан стать мыслью, а не «сказанным»: из-за этого вкладка «Мысли» была
    пуста, а ответ выбрасывался проверкой копий (brain.echo);
  * сказанное не подменяется целым ответом, когда мысль в ответе одна;
  * «такт» и «мысль» объясняются ФРАЗОЙ (PanelCore.life_now_text): мысль не идёт чаще
    такта — такт 30 с и 0 в поле мысли значит «раз в 30 с», а не поток;
  * порты понимают диапазон «1-1024» и не дают опечатке «1-65535» подвесить аудит.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import config, i18n             # noqa: E402
from live_data import temp_live              # noqa: E402
from synapse.agent import Synapse            # noqa: E402
from synapse.panel_core import PanelCore     # noqa: E402

#: Настоящий ответ модели из живого журнала Synapse 26.09.2026, 13:34:11:
#: brain.answer chars=445, а рядом brain.echo — ответ был ВЫБРОШЕН как копия промпта.
LIVE_ANSWER = ("**💭** Внутреннее рассуждение: - **Зажигание нейронов**: Зажигание нейронов "
               "для активации мотонейронов. - **Энергия**: Недостаточная энергия для "
               "поддержания активности.")


class TestThoughtMarkers(unittest.TestCase):
    """Метка 💭 — где угодно в строке; украшения markdown не мешают."""

    def test_control_plain_sentence_is_not_a_thought(self):
        self.assertIsNone(Synapse._thought_of_line("Тело спокойно, мне хорошо"))
        self.assertIsNone(Synapse._thought_of_line("**просто жирный текст**"))

    def test_markdown_wrapped_marker(self):
        """Тот самый случай из журнала: **💭** — мысль, а не пропавший ответ."""
        self.assertEqual(Synapse._thought_of_line("**💭** Внутреннее рассуждение: энергия мала"),
                         "Внутреннее рассуждение: энергия мала")

    def test_marker_in_other_places_and_forms(self):
        self.assertEqual(Synapse._thought_of_line("💭 простая"), "простая")
        self.assertEqual(Synapse._thought_of_line("- 💭 с пунктиром"), "с пунктиром")
        self.assertEqual(Synapse._thought_of_line("## 💭 с заголовком"), "с заголовком")
        self.assertEqual(Synapse._thought_of_line("[мысль] по-русски"), "по-русски")
        # КОНТРОЛЬ: метка В СЕРЕДИНЕ строки мыслью НЕ считается. Это не придирка: в моём
        # же промпте стоит «…строками, начинающимися с 💭 (отдельный канал…)», и правило
        # «где угодно» пускало копию промпта в канал мыслей существа (поймал
        # test_ui_v020::test_a_copy_of_the_prompt_is_not_a_thought).
        self.assertIsNone(Synapse._thought_of_line("Рассуждение: 💭 метка в середине"))

    def test_live_answer_becomes_a_thought_not_speech(self):
        inner, spoken = Synapse._split_thoughts(LIVE_ANSWER)
        self.assertTrue(inner, "настоящий ответ модели обязан стать мыслью")
        self.assertEqual(spoken, "", "сказанного в нём нет — как и у Миланы")
        self.assertIn("Зажигание нейронов", inner[0])

    def test_control_mixed_reply_keeps_what_was_said(self):
        inner, spoken = Synapse._split_thoughts("💭 сначала посмотрю\nМне любопытно")
        self.assertEqual(inner, ["сначала посмотрю"])
        self.assertEqual(spoken, "Мне любопытно")


class TestLifeNow(unittest.TestCase):
    """«Такт» и «мысль» объясняются фразой, а не двумя числами."""

    def setUp(self):
        self._lang = i18n.lang()
        i18n.set_lang("ru")

    def tearDown(self):
        i18n.set_lang(self._lang)

    def test_tick_is_the_thought_rate(self):
        """ОДИН ТЕМП — ТАКТ (v0.22.0): второго числа в настройках нет."""
        text = PanelCore.life_now_text(30)
        self.assertIn("30", text, "не сказано, что выйдет на самом деле")
        self.assertIn("мысль", text)

    def test_control_zero_tick_is_the_flow(self):
        self.assertEqual(PanelCore.life_now_text(0), i18n.t("set.life.stream"))
        self.assertNotEqual(PanelCore.life_now_text(30), i18n.t("set.life.stream"),
                            "такт 30 назван потоком — тест слепой")


class TestPortRange(unittest.TestCase):
    """Диапазон портов: ДВА ЧИСЛА и честная цена обхода (v0.22.0).

    Слово Стаса 26.09.2026: «я по прежнему вижу не диапазон портов… а строку Порты для
    проверки» и «ВСЕ порты для проверки, любые открытые». Поэтому список служб заменён
    диапазоном ОТ-ДО, а вместо потолка в 4096 записей — расчёт времени обхода.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._live = temp_live()
        self._live.__enter__()

    def tearDown(self):
        self._live.__exit__(None, None, None)
        self._tmp.cleanup()

    def core(self) -> PanelCore:
        core = PanelCore.__new__(PanelCore)
        core.cfg = config.load()
        return core

    def test_control_plain_range_is_understood(self):
        self.assertEqual(self.core().parse_port_range(1, 1024), (1, 1024))

    def test_reversed_range_is_understood(self):
        self.assertEqual(self.core().parse_port_range(5000, 4998), (4998, 5000))

    def test_all_ports_are_allowed(self):
        """«ВСЕ порты, любые открытые»: 1-65535 обязан проходить."""
        self.assertEqual(self.core().parse_port_range(1, 65535), (1, 65535))

    def test_mess_is_refused_with_words(self):
        with self.assertRaises(ValueError) as ctx:
            self.core().parse_port_range("много", 80)
        self.assertIn("port_from", str(ctx.exception), "не сказано, какое поле плохое")

    def test_estimate_names_the_price(self):
        text = PanelCore.probe_estimate(1, 1024, 256)
        self.assertIn("1024", text, "не сказано, сколько портов пойдёт в обход")
        self.assertIn("1-1024", text, "не назван сам диапазон")


class TestSettingsWords(unittest.TestCase):
    """Слова настроек больше не обещают поток там, где между тактами есть сон."""

    def setUp(self):
        self._lang = i18n.lang()

    def tearDown(self):
        i18n.set_lang(self._lang)

    def test_tick_hint_is_short_not_a_wall(self):
        """«Хренотень с инструкцией на простыню» — слово Стаса 26.09.2026."""
        for lang in ("ru", "en"):
            i18n.set_lang(lang)
            self.assertLess(len(i18n.t("set.tick.hint")), 260,
                            f"подсказка такта снова стала простынёй ({lang})")

    def test_the_second_rate_field_is_gone(self):
        for key in ("set.idle_seconds", "set.idle_seconds.hint", "set.every.zero"):
            self.assertEqual(i18n.t(key), key, f"ключ {key} обязан исчезнуть из словаря")


if __name__ == "__main__":
    unittest.main(verbosity=2)
