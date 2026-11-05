"""v0.21.1: карточка узла работает и заполняется полями, которые у узла ЕСТЬ.

    python tests/test_neuron_card.py

Жалоба Стаса 26.09.2026: «клик по нейрону по прежнему не работает, хотя ты говорила что
пару раз чинила это — я клацаю по нейрону, он выделяется и всё, нигде никакой информации
о нем не происходит». Диагноз по коду и по данным:

  * в карточке стояли поля, которых у узла НЕТ — "partners" (такого поля не существует),
    "degree" (настоящее имя "deg");
  * "receptors" — это ЧИСЛО (roles.py кладёт len(...)), а код звал ", ".join(число) и
    падал с TypeError;
  * падало ПОСЛЕ вспышки, поэтому узел моргал, а информации не было — и молча (в
    оконном приложении stderr никто не видит).

КОНТРОЛЬ идёт первым: он повторяет старое выражение на настоящей записи узла и обязан
упасть — иначе тест «карточка работает» ничего не доказывает.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from synapse import config, i18n                                     # noqa: E402
from synapse.panel_core import PanelCore                             # noqa: E402
from live_data import temp_live                                      # noqa: E402

try:                                                                 # noqa: E402
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except Exception:                                                    # noqa: BLE001
    HAS_QT = False


class BodySafe(unittest.TestCase):
    """Тело читаем из временной папки: живые data/ оператора тест не трогает."""

    def setUp(self):
        self._live = temp_live()
        self._live.__enter__()
        self.core = PanelCore()
        i18n.set_lang("ru")

    def tearDown(self):
        self._live.__exit__(None, None, None)


class TestControl(BodySafe):
    """Без контроля «карточка заполнена» не значит ничего."""

    def test_control_old_expression_dies_on_the_real_record(self):
        """СТАРЫЙ код: ", ".join(rec["receptors"]). Обязан падать — это и была поломка."""
        rec = self.core.neuron("AVAL")
        self.assertTrue(rec, "запись узла не собралась")
        self.assertIsInstance(rec["receptors"], int,
                              "receptors снова стал списком — перечитай roles.py")
        with self.assertRaises(TypeError):
            ", ".join(rec["receptors"])           # ровно прежняя строка карточки

    def test_control_fake_fields_are_still_fake(self):
        """Полей, на которые опирался прежний код, у узла нет — их не вернуть по ошибке."""
        rec = self.core.neuron("AVAL")
        self.assertNotIn("degree", rec, "вернулось выдуманное поле degree (настоящее — deg)")
        self.assertIn("deg", rec)


class TestRealFields(BodySafe):

    def test_card_fields_exist(self):
        rec = self.core.neuron("AVAL")
        for key in ("name", "role", "zone", "nt", "deg", "hub", "partners", "receptors"):
            self.assertIn(key, rec, f"в карточке нет поля {key}")
        self.assertIsInstance(rec["partners"], list)

    def test_partners_are_real_neighbours_in_both_directions(self):
        """Соседи считаются по РЕАЛЬНЫМ связям тела (и входящим, и исходящим)."""
        ids = self.core.body.ids
        idx = ids.index("AVAL")
        got = self.core.body.neighbours(idx, limit=6)
        self.assertTrue(got, "у хаба AVAL нет соседей — считаем не то")
        self.assertTrue(set(got) <= set(ids), "сосед назван именем, которого в теле нет")
        self.assertNotIn("AVAL", got, "узел попал в свои же соседи")
        edge_pairs = set()
        for a, b, _w in self.core.body.edges:
            edge_pairs.add((a, b))
        self.assertTrue(any((idx, ids.index(n)) in edge_pairs or (ids.index(n), idx) in edge_pairs
                            for n in got), "соседи взяты не из связей")
        self.assertLessEqual(len(got), 6)


@unittest.skipUnless(HAS_QT, "нет PySide6 — окно не построить")
class TestCardInWindow(BodySafe):
    """QApplication обязателен: без него создание виджета роняет процесс нативно
    (0xC0000409), а не «падает тестом» — проверено 26.09.2026."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_card_text_has_the_truth_about_the_node(self):
        from synapse.qt_panel import SynapseWindow
        try:
            win = SynapseWindow(self.core)
        except Exception as e:                    # noqa: BLE001 — среда без GL
            self.skipTest(f"окно в этой среде не создаётся: {type(e).__name__}: {e}")
        try:
            rec = self.core.neuron("AVAL")
            text = win._card_text(rec)
            self.assertTrue(text, "карточка пуста — оператор снова ничего не узнает")
            self.assertIn("AVAL", text)
            self.assertIn(str(rec["deg"]), text, "число связей не показано")
            self.assertIn(rec["partners"][0], text, "соседи не показаны")
            self.assertEqual(win._card_text({}), "", "пустая запись дала непустую карточку")
        finally:
            win.close()

    def test_show_neuron_never_raises_and_fills_the_card(self):
        """Клик обязан дать карточку И НЕ УРОНИТЬ окно: слот молчит при исключении."""
        from synapse.qt_panel import SynapseWindow
        try:
            win = SynapseWindow(self.core)
        except Exception as e:                    # noqa: BLE001
            self.skipTest(f"окно в этой среде не создаётся: {type(e).__name__}: {e}")
        try:
            win._show_neuron("AVAL")
            self.assertTrue(win.lbl_card.full_text(), "карточка не заполнилась")
            self.assertEqual(win._picked, "AVAL", "выбранный узел не запомнен")
            before = win.lbl_card.full_text()
            win._show_neuron("такого-узла-нет")   # чужое имя: не падать и не стирать
            self.assertEqual(win.lbl_card.full_text(), before,
                             "неизвестное имя стёрло карточку предыдущего узла")
        finally:
            win.close()

    def test_two_status_lines_are_reserved_and_empty_by_default(self):
        """Слово Стаса: «оставить только 2 статусные строки» — подсказок под телом нет.

        Место под карточку ЗАРЕЗЕРВИРОВАНО (иначе текст сдвигал бы колонку на клике),
        но до клика она пуста.
        """
        from synapse.qt_panel import SynapseWindow
        try:
            win = SynapseWindow(self.core)
        except Exception as e:                    # noqa: BLE001
            self.skipTest(f"окно в этой среде не создаётся: {type(e).__name__}: {e}")
        try:
            self.assertEqual(win.lbl_card.text(), "", "карточка висит подсказкой до клика")
            self.assertEqual(win.lbl_action.text(), "", "строка ответа висит подсказкой")
            self.assertGreater(win.lbl_card.minimumHeight(), 0, "место под карточку не зарезервировано")
            win._retranslate()
            self.assertEqual(win.lbl_card.text(), "", "перевод вернул подсказку в карточку")
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
