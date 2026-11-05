"""v0.10.2: кнопки под телом ОТВЕЧАЮТ, импульс видно, цвета объяснены.

    python tests/test_panel_actions.py        (запускать из корня проекта)

Слово Стаса 25.09.2026: «что делают кнопки под графическим окном? я на них нажимаю
и ничего не происходит. что за роли цвета? ничего не меняется. Жить - значит
включить и потом пауза остановить?»

ЗАМЕРЫ, а не догадки (что нашлось и что здесь проверяется):

  * ЖИВОСТЬ ТЕЛА. Вдох шёл каждые 10 кадров — при темпе 16 мс это 0.16 с, то есть
    тело мерцало без пауз: в среднем 18 зажжено из 302, до 31, и спокойных кадров
    почти не было. Теперь вдохи редкие (breathe_every=90) и слабые: за 400 кадров
    спокойны 330, максимум 9 зажжено.
  * ИМПУЛЬС. В этом шуме он был неразличим (+10 нейронов на 0.16 с). Теперь импульс
    ПОМЕЧАЕТ узлы вспышкой: задетые на flash_seconds становятся белыми и вдвое
    крупнее — видно, куда он попал (замер: радиус 6.2 против 3.0 у спокойного).
  * ОТВЕТ НА НАЖАТИЕ. В запасной tkinter-панели каждое действие писалось в лог, а
    в Qt-панели не писалось НИЧЕГО — кнопки работали молча. Теперь на каждое
    нажатие есть строка в окне (проверяется на настоящем окне, offscreen).
  * ЛЕГЕНДА. Цвета были верные (5 по ролям, 7 по медиаторам, 3 по зонам), но
    оператору никто не сказал, что они значат. Легенда меняется вместе с раскраской.

КОНТРОЛЬ идёт первым: без него «импульс виден» и «кнопка отвечает» ничего не
доказывают — сначала убеждаемся, что ДО действия вспышки нет и строка пуста.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Окно в тестах рисуется БЕЗ экрана: тест не имеет права показать окно оператору.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from synapse import config, i18n, theme                       # noqa: E402
from synapse.panel_core import PanelCore                      # noqa: E402
from live_data import temp_live                               # noqa: E402

NEW_KEYS = ("act.live", "act.pause", "act.ticking", "act.pulse",
            "act.coloring", "act.reset", "act.busy", "btn.live.tip", "btn.pause.tip",
            "btn.step.tip", "btn.pulse.tip", "btn.reset_view.tip", "color.tip",
            "legend.title", "legend.unknown_nt",
            "coloring.role", "coloring.nt", "coloring.zone", "card.hub")


class LiveSafe(unittest.TestCase):
    """Ядро панели ПИШЕТ в журнал (импульс — точно), поэтому тест живёт в temp.

    Найдено 25.09.2026 сразу после правки: тесты импульса оставили 11 строк
    panel.pulse в ЖИВОМ журнале оператора — та же болезнь, что «test.event» и
    мусорные миссии до этого. Лечение одно: temp_live() на весь класс, иначе
    тест «проверяет кнопку» ценой мусора в данных хозяина.
    """

    def setUp(self):
        self._live = temp_live()
        self._live.__enter__()

    def tearDown(self):
        self._live.__exit__(None, None, None)


class TestControl(LiveSafe):
    """Без этих проверок остальные ничего не доказывают."""

    def setUp(self):
        super().setUp()
        self.core = PanelCore()

    def test_no_flash_before_any_pulse(self):
        self.assertEqual(self.core.flash_count(), 0, "вспышка есть ДО импульса")
        quiet = [i for i in range(self.core.body.size) if self.core.flashing(i)]
        self.assertEqual(quiet, [], "узлы уже отмечены — тест слепой")

    def test_missing_key_still_shows_itself(self):
        self.assertEqual(i18n.t("act.такого.нет"), "act.такого.нет")


class TestBreatheIsQuiet(LiveSafe):
    """Тело дышит — но не мерцает без пауз: иначе импульс не видно."""

    def test_body_has_quiet_moments(self):
        core = PanelCore()
        lit = []
        for _ in range(400):
            core.breathe()
            lit.append(core.body.lit())
        quiet = sum(1 for v in lit if v == 0)
        self.assertGreater(quiet, len(lit) * 0.5,
                           f"спокойных кадров всего {quiet} из {len(lit)} — тело мерцает")
        self.assertLess(max(lit), 40, f"шум тела слишком громкий: {max(lit)} зажжено")

    def test_breathe_settings_are_read_from_config(self):
        """Темп вдоха — настройка, а не число в коде: иначе её не подкрутить."""
        body_cfg = config.load()["body"]
        for key in ("breathe_every", "breathe_amp", "flash_seconds"):
            self.assertIn(key, body_cfg, f"нет настройки {key}")


class TestPulseIsVisible(LiveSafe):
    """Импульс оператора обязан быть ВИДЕН, а не «нажал и ничего»."""

    def test_pulse_marks_exactly_the_neurons_it_touched(self):
        core = PanelCore()
        for _ in range(200):
            core.breathe()                      # тело живёт своей жизнью
        self.assertEqual(core.flash_count(), 0, "до импульса вспышки нет")
        text = core.pulse("")
        marked = sorted(core._flash_idx)
        self.assertTrue(marked, "импульс никого не задел")
        self.assertEqual(core.flash_count(), len(marked))
        self.assertLessEqual(len(marked), 6, "отметка не должна накрывать всё тело")
        for i in marked:
            self.assertGreater(core.body.activity[i], 0.0, "отмечен узел, который не горит")
        self.assertIn("зажжено", text, "тело не сказало, что с ним стало")

    def test_marked_neuron_is_white_and_bigger(self):
        core = PanelCore()
        core.pulse("")
        marked = sorted(core._flash_idx)[0]
        quiet = next(i for i in range(core.body.size)
                     if i not in set(core._flash_idx) and core.body.activity[i] == 0.0)
        self.assertEqual(core.node_color(marked), "#ffffff", "отмеченный узел не белый")
        self.assertNotEqual(core.node_color(quiet), "#ffffff")
        self.assertGreater(core.node_radius(marked), core.node_radius(quiet),
                           "отмеченный узел не крупнее — не видать")

    def test_flash_expires_and_is_swept_by_breathe(self):
        import time
        core = PanelCore()
        core.pulse("")
        marked = sorted(core._flash_idx)
        self.assertTrue(marked, "импульс никого не задел")
        self.assertTrue(core.flashing(marked[0]))
        core._flash_until = time.monotonic() - 0.01        # время вспышки вышло
        self.assertEqual(core.flash_count(), 0)
        self.assertFalse(core.flashing(marked[0]), "вспышка живёт дольше своего времени")
        core.breathe()                                     # кадр подметает отметку
        self.assertEqual(core._flash_idx, set(), "отметка не убрана — светилась бы вечно")

    def test_pulse_stands_out_above_the_breathing_noise(self):
        """Импульс виден не числом, а отметкой: тело к нему тихое, задетые — белые.

        Замер 25.09.2026: ДО правки тело мерцало без пауз (в среднем 18 зажжено,
        до 31) и импульс в этом шуме пропадал. Теперь собственный шум тела мал,
        а задетые узлы помечены — их и видно.
        """
        core = PanelCore()
        for _ in range(200):
            core.breathe()
        before = core.body.lit()
        core.pulse("")
        peak = core.body.lit()
        noise = []
        for _ in range(200):
            core.breathe()
            noise.append(core.body.lit())
        self.assertGreater(peak, before, "после импульса тело не громче")
        self.assertLess(max(noise), 40,
                        f"собственный шум тела перекрыл бы импульс: {max(noise)} зажжено")


class TestColourLegend(LiveSafe):
    """«Что за роли цвета?» — легенда отвечает словами и меняется вместе с выбором."""

    def test_legend_covers_the_whole_body_in_every_mode(self):
        core = PanelCore()
        sizes = {}
        for mode in ("role", "nt", "zone"):
            core.coloring = mode
            rows = core.legend()
            self.assertTrue(rows, f"{mode}: легенда пуста")
            self.assertEqual(sum(r["count"] for r in rows), core.body.size,
                             f"{mode}: легенда покрывает не все нейроны")
            for r in rows:
                self.assertRegex(r["color"], r"^#[0-9a-fA-F]{6}$", f"{mode}: цвет не hex")
                self.assertTrue(str(r["label"]).strip(), f"{mode}: пункт без подписи")
                self.assertGreater(r["count"], 0, f"{mode}: пункт с нулём")
            sizes[mode] = len(rows)
        self.assertEqual(sizes["role"], 5, "ролей в теле пять")
        # В теле шесть медиаторов и 59 узлов БЕЗ медиатора: они рисуются серым,
        # и легенда обязана их объяснить — иначе на теле есть цвета без ответа.
        self.assertEqual(sizes["nt"], 7, "шесть медиаторов + «без медиатора»")
        self.assertEqual(sizes["zone"], 3, "зон три: голова, середина, хвост")

    def test_legend_labels_follow_the_language(self):
        core = PanelCore()
        core.coloring = "role"
        keep = i18n.lang()
        try:
            i18n.set_lang("en")
            en = [r["label"] for r in core.legend()]
            i18n.set_lang("ru")
            ru = [r["label"] for r in core.legend()]
        finally:
            i18n.set_lang(keep)
        self.assertNotEqual(en, ru, "легенда не переводится")
        self.assertIn("sensory", en)
        self.assertIn("сенсорный", ru)

    def test_coloring_name_is_translated_words_not_a_code(self):
        core = PanelCore()
        seen = []
        for mode in ("role", "nt", "zone"):
            core.coloring = mode
            name = core.coloring_name()
            self.assertNotEqual(name, mode, f"{mode}: на экран ушёл код, а не слово")
            seen.append(name)
        self.assertEqual(len(set(seen)), 3, "названия раскрасок совпали")

    def test_legend_colours_match_the_canvas(self):
        """Плашка в легенде — ровно тот цвет, которым нарисован узел этой роли."""
        core = PanelCore()
        core.coloring = "role"
        for row in core.legend():
            for i in range(core.body.size):
                if core.body.roles[i] == "unknown":
                    continue
                if core.calm_color(i) == row["color"]:
                    break
            else:
                self.fail(f"цвет {row['color']} из легенды не нарисован ни на одном узле")


class TestWindowAnswers(unittest.TestCase):
    """Нажатие обязано ОТВЕЧАТЬ строкой в окне — это и была жалоба."""

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as e:                               # noqa: BLE001
            raise unittest.SkipTest(f"PySide6 недоступен: {type(e).__name__}: {e}")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._live = temp_live()          # журнал от нажатий — в temp, живые data/ не трогаем
        self._live.__enter__()
        self._lang, self._theme = i18n.lang(), theme.theme()
        from synapse.qt_panel import SynapseWindow
        self.win = SynapseWindow()
        # ТАКТ ПОДМЕНЯЕМ. Настоящий tick() панель гоняет в ФОНОВОМ потоке, и найден
        # 25.09.2026 убегал из песочницы: две записи «tick: 1» осели в живом журнале
        # оператора. Здесь проверяется ОТВЕТ ОКНА, а не жизнь организма, поэтому такт
        # отдаёт заранее известный словарь и на диск не ходит вовсе.
        self.win.core.tick = lambda *a, **k: {"tick": 7, "status": "idle",
                                              "text": "alive (test)", "mission": None}
        i18n.set_lang("en")               # тест не зависит от языка в живом конфиге
        theme.set_theme("dark")
        self.win._retranslate()

    def tearDown(self):
        self.win.close()
        i18n.set_lang(self._lang)
        theme.set_theme(self._theme)
        self._live.__exit__(None, None, None)

    def test_two_status_lines_are_empty_until_something_happens(self):
        """Слово Стаса 26.09.2026: «убери кучу переносов и ненужных подсказок под кнопками
        и графикой — оставить только 2 статусные строки».

        Значит до первого нажатия обе строки МОЛЧАТ: подсказок-заглушек под телом нет.
        ПОПРАВКА ТЕСТА, А НЕ КОДА: раньше здесь ждали act.nothing — это и была та
        подсказка, которую оператор попросил убрать.
        """
        self.assertEqual(self.win.lbl_action.text(), "", "под кнопками снова висит подсказка")
        self.assertEqual(self.win.lbl_card.text(), "",
                         "карточка показывает заглушку вместо пустого места")

    def test_pulse_button_answers(self):
        self.win._pulse()
        text = self.win.lbl_action.text()
        self.assertIn(i18n.t("act.pulse").split("{")[0].strip(), text)
        self.assertIn("302", text, "в ответе нет того, что тело о себе сказало")

    def test_step_button_answers_with_a_tick(self):
        self.win._tick_async(manual=True)
        self.win._on_tick({"tick": 7, "status": "idle", "text": "живу"})
        self.assertIn("7", self.win.lbl_action.text(), "такт не назван")

    def test_colouring_answers_and_shows_the_legend(self):
        before = self.win.lbl_legend.text()
        self.win.cmb_color.setCurrentIndex(1)          # медиаторы
        self.win._set_coloring()
        self.assertIn(i18n.t("act.coloring").split("{")[0].strip(),
                      self.win.lbl_action.text())
        self.assertIn(self.win.core.coloring_name(), self.win.lbl_action.text())
        self.assertNotEqual(self.win.lbl_legend.text(), before, "легенда не сменилась")
        self.assertIn(i18n.t("legend.title"), self.win.lbl_legend.text())

    def test_reset_view_answers(self):
        self.win.core.yaw = 1.1
        self.win._reset_view()
        self.assertEqual(self.win.core.yaw, 0.0)
        self.assertEqual(self.win.lbl_action.text(), i18n.t("act.reset"))

    def test_live_and_pause_explain_themselves(self):
        """«Жить — значит включить и потом пауза остановить?» — да, и это сказано."""
        # Такт задаём САМИ: иначе тест зависит от живого config.json оператора
        # (он может держать 5, 60 или 0 — проверяем ответ, а не его настройку).
        self.win.core.cfg["tick_seconds"] = 60.0
        self.win.btn_run.setChecked(True)
        self.win._toggle_run()
        self.assertTrue(self.win._running)
        self.assertIn("60", self.win.lbl_action.text(), "не сказано, с каким тактом живёт")
        self.assertEqual(self.win.btn_run.text(), i18n.t("btn.pause"))
        self.win.btn_run.setChecked(False)
        self.win._toggle_run()
        self.assertFalse(self.win._running)
        self.assertEqual(self.win.lbl_action.text(), i18n.t("act.pause"))
        self.assertEqual(self.win.btn_run.text(), i18n.t("btn.live"))

    def test_every_button_has_a_tooltip(self):
        for btn in (self.win.btn_run, self.win.btn_step, self.win.btn_pulse,
                    self.win.btn_reset, self.win.cmb_color):
            tip = btn.toolTip()
            self.assertTrue(tip and not tip.startswith("btn."), f"нет подсказки: {btn}")


class TestNewKeysInDictionary(unittest.TestCase):

    def test_keys_exist_in_both_languages_and_differ(self):
        for key in NEW_KEYS:
            self.assertTrue(i18n.has(key), f"нет в словаре: {key}")
            rec = i18n.CATALOG[key]
            self.assertTrue(rec["en"].strip() and rec["ru"].strip(), f"{key}: пустая строка")
            self.assertNotEqual(rec["en"], rec["ru"], f"{key}: не переведён")


if __name__ == "__main__":
    unittest.main(verbosity=2)
