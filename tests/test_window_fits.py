"""v0.20.1: окно вписывается в ЛЮБОЙ экран, а не диктует экрану размер.

    python tests/test_window_fits.py

Слово Стаса 26.09.2026: «ты ерунду какую-то сделала - теперь у меня окно в экран не
влазит. при том что FullHD - сделай нормально универсально под любой экран».

ЗАМЕР ПРИЧИНЫ, а не догадка: окно требовало минимум 2246x765 при FullHD 1920. Виноват
закон Qt: у QLabel БЕЗ переноса слов `minimumSizeHint()` равен ширине ВСЕГО текста —
подпись тела просила 1488 px, строка ответа 960, а с полотном (430) выходило 2246.

КОНТРОЛЬ ИДЁТ ПЕРВЫМ в каждом классе: без него «стало 589 px» ничего не доказывает —
нужно видеть, что измерение вообще способно поймать распирание.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")     # настоящее окно не открываем

from synapse import config, window_fit                    # noqa: E402
from live_data import temp_live                           # noqa: E402

try:                                                      # noqa: E402
    from PySide6.QtWidgets import QApplication, QLabel, QScrollArea
    HAS_QT = True
except Exception:                                         # noqa: BLE001
    HAS_QT = False


#: Тот самый длинный текст, из-за которого окно не влезало (строка тела).
LONG = "задето 302 из 302 · связей 3004 · энергия 0.0003 · импульс 0.0 · режим off"


class TestClampSize(unittest.TestCase):
    """Чистая функция вписания — без Qt и без Tk, поэтому проверяется числом."""

    def test_control_size_that_fits_is_left_alone(self):
        """КОНТРОЛЬ: если места хватает, окно не трогают (иначе «вписал» = «обрезал»)."""
        self.assertEqual(window_fit.clamp_size(1360, 820, 1920, 1040), (1360, 820))

    def test_control_the_old_minimum_really_did_not_fit_fullhd(self):
        """КОНТРОЛЬ: 2246 px (наш замер до правки) не влезает в FullHD — боль настоящая."""
        self.assertGreater(2246, 1920 - window_fit.MARGIN_W)
        self.assertEqual(window_fit.clamp_size(2246, 765, 1920, 1000), (1880, 765))

    def test_fullhd_with_taskbar_clamps_the_height_only_when_needed(self):
        """FullHD с панелью задач: высота 1080 не влезала бы — вписываем."""
        self.assertEqual(window_fit.clamp_size(1360, 1040, 1920, 1000), (1360, 940))

    def test_laptop_screen_clamps_both_sides(self):
        """1366x768 (частый ноутбук): окно 1360x820 обязано ужаться по высоте."""
        w, h = window_fit.clamp_size(1360, 820, 1366, 728)
        self.assertEqual((w, h), (1326, 668))

    def test_small_screen_is_used_fully_but_not_beyond(self):
        """Экран 800x600: окно забирает всё место минус запас, а не нижний предел."""
        self.assertEqual(window_fit.clamp_size(1360, 820, 800, 600), (760, 540))

    def test_hopeless_screen_keeps_a_readable_floor(self):
        """Экран 400x300: ниже нижнего предела не отдаём — иначе читать нечего."""
        self.assertEqual(window_fit.clamp_size(1360, 820, 400, 300),
                         (window_fit.MIN_W, window_fit.MIN_H))


@unittest.skipUnless(HAS_QT, "нет PySide6 — окно не построить")
class TestFixedLineWidth(unittest.TestCase):
    """Подпись не имеет права просить ширину своего текста — иначе она распирает окно."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_control_a_plain_label_demands_the_whole_text_width(self):
        """КОНТРОЛЬ: обычный QLabel на том же тексте просит сотни пикселей."""
        plain = QLabel(LONG)
        self.assertGreater(plain.minimumSizeHint().width(), 600,
                           "контроль сломан: измерение больше не видит распирания")

    def test_fixed_line_asks_little_and_keeps_the_full_text(self):
        from synapse.qt_panel import FixedLine
        line = FixedLine(lines=2)
        line.setText(LONG)
        self.assertLessEqual(line.minimumSizeHint().width(), 100,
                             "подпись снова просит ширину текста — окно не влезет в экран")
        self.assertLessEqual(line.sizeHint().width(), 100, "sizeHint тоже обязан быть скромным")
        self.assertEqual(line.full_text(), LONG, "полный текст потерян — оператор не увидит, что было")
        self.assertEqual(line.minimumHeight(), line.maximumHeight(),
                         "высота подписи поехала — на каждом такте вид будет дёргаться")


class CfgSafe(unittest.TestCase):
    """Свой config.json: живой файл настроек оператора тест не трогает."""

    def setUp(self):
        self._live = temp_live()
        self._live.__enter__()
        self._tmp = tempfile.TemporaryDirectory()
        self._cfg = Path(self._tmp.name) / "config.json"
        self._keep = config.CONFIG_PATH
        config.CONFIG_PATH = self._cfg
        config.save(config.DEFAULTS, self._cfg)
        config.forget_cache()

    def tearDown(self):
        config.CONFIG_PATH = self._keep
        config.forget_cache()
        self._tmp.cleanup()
        self._live.__exit__(None, None, None)


@unittest.skipUnless(HAS_QT, "нет PySide6 — окно не построить")
class TestWindowFitsScreen(CfgSafe):
    """Настоящее окно: его минимум обязан влезать в маленький экран."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _win(self):
        from synapse.qt_panel import SynapseWindow
        win = SynapseWindow()
        win.core.brain.stop_server = lambda: None        # НАСТОЯЩИЙ сервер не гасим и не поднимаем
        win.show()
        self.app.processEvents()
        return win

    def test_control_the_settings_page_alone_is_taller_than_the_whole_window(self):
        """КОНТРОЛЬ: содержимое «Настроек» требует 700+ px по высоте САМО по себе.

        Значит окно влезает не потому, что стало мало чего просить, а потому что
        страница листается: без прокрутки эти пиксели становились минимумом окна.
        """
        win = self._win()
        try:
            page = win.book.widget(win._settings_tab)
            self.assertIsInstance(page, QScrollArea, "страница настроек без прокрутки")
            content = page.widget()
            self.assertGreater(content.minimumSizeHint().height(), 600,
                               "вкладка настроек стала низкой — контроль перестал что-то доказывать")
            self.assertLess(win.minimumSizeHint().height(), content.minimumSizeHint().height(),
                            "минимум окна всё ещё задаёт самая высокая страница")
        finally:
            win.close()

    def test_window_minimum_fits_a_1024x768_screen(self):
        """Главная проверка жалобы: минимум окна обязан быть меньше FullHD и ноутбука."""
        win = self._win()
        try:
            mw, mh = win.minimumSizeHint().width(), win.minimumSizeHint().height()
            self.assertLessEqual(mw, 1024, f"окно требует {mw} px ширины — на 1024 не влезет")
            self.assertLessEqual(mh, 700, f"окно требует {mh} px высоты — на ноутбуке не влезет")
        finally:
            win.close()

    def test_window_is_born_inside_the_screen(self):
        """Рождение окна: оно уже вписано (fit_to_screen), а не торчит за край.

        Честная форма проверки: Qt не даст окну стать МЕНЬШЕ его минимума, поэтому
        требуем «не больше экрана, если только минимум не вынуждает». Именно так и
        нашлась настоящая дыра: с темой QSS ряд кнопок просил 636 px, минимум окна
        вырастал до 767, и на площадке 800x800 окно вылезало за край.
        """
        win = self._win()
        try:
            av = win.screen().availableGeometry()
            mw, mh = win.minimumSizeHint().width(), win.minimumSizeHint().height()
            self.assertLessEqual(mw, av.width(), f"минимум {mw} px шире экрана — не влезет никак")
            self.assertLessEqual(mh, av.height(), f"минимум {mh} px выше экрана — не влезет никак")
            self.assertLessEqual(win.width(), max(av.width() - window_fit.MARGIN_W, mw),
                                 "окно шире экрана — за краем не видно края интерфейса")
            self.assertLessEqual(win.height(), max(av.height() - window_fit.MARGIN_H, mh),
                                 "окно выше экрана — нижние кнопки недоступны")
        finally:
            win.close()

    def test_window_fits_in_every_language_and_theme(self):
        """Контроль-ловушка: язык и тема меняют длину подписей и поля QSS.

        Тот же прогон, что нашёл 767 px: подписи на другом языке длиннее, и минимум
        окна не имеет права от этого вырастать до размера экрана.
        """
        from synapse import i18n, theme
        keep = (i18n.lang(), theme.theme())
        try:
            for lang in ("en", "ru"):
                for th in theme.available():
                    i18n.set_lang(lang)
                    theme.set_theme(th)
                    self.app.setStyleSheet(theme.qss())
                    win = self._win()
                    try:
                        av = win.screen().availableGeometry()
                        mw = win.minimumSizeHint().width()
                        self.assertLessEqual(mw, av.width(),
                                             f"{lang}/{th}: минимум окна {mw} px не влезает")
                    finally:
                        win.close()
        finally:
            i18n.set_lang(keep[0])
            theme.set_theme(keep[1])

    def test_every_tab_page_scrolls_and_the_legend_is_still_in_settings(self):
        """Каждая страница листается, и легенда осталась в «Настройках» (не сломали v0.20.0)."""
        win = self._win()
        try:
            for i in range(win.book.count()):
                self.assertIsInstance(win.book.widget(i), QScrollArea,
                                      f"вкладка {i} без прокрутки — она снова задаст минимум окну")
            page = win.book.widget(win._settings_tab)
            node = win.lbl_legend
            seen = False
            while node is not None:
                if node is page:
                    seen = True
                    break
                node = node.parent()
            self.assertTrue(seen, "легенда выпала из «Настроек» вместе с прокруткой")
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
