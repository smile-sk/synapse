"""Тесты запуска окна: правильный интерпретатор ищется сам, запасной путь не молчит.

Запуск из корня проекта:  python tests/test_launcher.py

Почему это отдельные тесты. Замер 25.09.2026 по жалобе «впечатление, что ничего не
изменилось — такие же тормоза, журнал на русском»: двойной клик по Synapse.pyw
запускал окно на C:\\Python314\\pythonw.exe, где нет ни PySide6, ни Pillow. Окно молча
уходило на запасной tkinter-путь и рисовало тело canvas-ом — то есть вся работа над
панелью на видеокарте не участвовала. Теперь запуск сам находит интерпретатор с Qt.

ПЕРВЫМ идёт КОНТРОЛЬ: проба обязана РАЗЛИЧАТЬ интерпретаторы (в этом Qt есть, в том
нет). Иначе «нашли правильный» ничего не значит — можно было бы всегда говорить «да».
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import config, launcher                     # noqa: E402


class TestProbe(unittest.TestCase):
    def test_control_current_interpreter_has_qt(self):
        """КОНТРОЛЬ: в интерпретаторе, которым гоняем тесты, Qt обязан быть."""
        self.assertTrue(launcher.has_qt(sys.executable),
                        "в этом интерпретаторе нет PySide6 — гоняй тесты тем, где он есть")

    def test_probe_says_no_for_interpreter_without_qt(self):
        """Тот же вопрос про питон без PySide6 обязан дать «нет», иначе проба врёт."""
        without = [p for p in launcher.candidates()
                   if p != Path(sys.executable) and not launcher.has_qt(p)]
        if not without:
            self.skipTest("на этой машине все найденные интерпретаторы с PySide6")
        self.assertFalse(launcher.has_qt(without[0]))

    def test_probe_survives_missing_file(self):
        self.assertFalse(launcher.has_qt(Path("нет-такого-интерпретатора.exe")))

    def test_candidates_exist_and_are_unique(self):
        found = launcher.candidates()
        self.assertTrue(found, "не нашлось ни одного интерпретатора — как искать Qt?")
        self.assertEqual(len(found), len(set(found)), "в списке кандидатов есть повторы")
        for p in found:
            self.assertTrue(p.exists(), f"кандидат не существует: {p}")


class TestNoRelaunchWhenFine(unittest.TestCase):
    def test_no_relaunch_when_qt_is_present(self):
        """Qt на месте — никакого перезапуска: иначе открывались бы две панели."""
        if not launcher.has_qt(sys.executable):
            self.skipTest("тесты идут без Qt — здесь перезапуск был бы настоящим")
        self.assertFalse(launcher.ensure_right_python())

    def test_prefer_tk_disables_the_search(self):
        """Оператор выбрал запасной путь — искать Qt и перезапускаться не наше дело."""
        self.assertFalse(launcher.ensure_right_python(prefer="tk"))

    def test_ui_section_is_in_config(self):
        ui = config.load().get("ui")
        self.assertIsInstance(ui, dict)
        self.assertIn(ui.get("prefer"), ("qt", "tk"))
        self.assertIn("python", ui)
        self.assertIsInstance(ui.get("python_candidates"), list)

    def test_default_prefer_is_qt(self):
        self.assertEqual(config.DEFAULTS["ui"]["prefer"], "qt")


if __name__ == "__main__":
    unittest.main(verbosity=2)
