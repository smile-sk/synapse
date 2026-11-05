"""Тесты отзывчивости окна: вид трогаем ТОЛЬКО там, где данные реально изменились.

Запуск из корня проекта:  python tests/test_panel_refresh.py

Жалоба Стаса 25.09.2026: «впечатление, что ничего не изменилось — такие же тормоза,
журнал на русском». Замер объяснил: прежний _refresh() стоил 15-23 мс КАЖДУЮ секунду
в главном потоке окна (пересборка таблицы миссий, повторная вставка журнала, чтение
25 отчётов) и, хуже цены, сбрасывал выделение и прокрутку — выбрать миссию было нельзя.

ПЕРВЫМ идёт КОНТРОЛЬ: отпечаток обязан РЕАЛЬНО меняться, когда файл меняется. Иначе
«не менялось — не трогаем» ничего не значит: можно было просто ничего не обновлять.
"""
from __future__ import annotations

import os
import statistics
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import audit, config, missions                  # noqa: E402
from live_data import temp_live                              # noqa: E402
from synapse.agent import Synapse                           # noqa: E402
from synapse.panel_core import PanelCore                     # noqa: E402


def quiet_synapse():
    """Организм для тестов БЕЗ живого мозга (тест не имеет права поднимать сервер)."""
    cfg = config.load()
    cfg["brain"] = dict(cfg.get("brain", {}), mode="off")
    return Synapse(cfg=cfg)


def touch_later(path: Path) -> None:
    """Сделать файл «свежее» — так проверяем, что отпечаток это видит."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


class TestFingerprints(unittest.TestCase):
    """КОНТРОЛЬ: отпечатки чувствуют правку файла. Без этого всё остальное — фикция."""

    @classmethod
    def setUpClass(cls):
        cls.core = PanelCore(quiet_synapse())

    def test_control_missions_fingerprint_changes(self):
        # Данные создаёт САМ тест: песочница даёт пустую папку, а проверяем мы
        # отпечаток ФАЙЛА очереди. Раньше тест опирался на живой файл оператора и
        # падал, стоило очереди быть пустой (вскрылось 25.09.2026, когда
        # автофикстура conftest увела очередь в temp).
        missions.add("note", {"text": "seed для отпечатка"})
        before = self.core.fingerprints()["missions"]
        touch_later(missions.QUEUE)
        after = self.core.fingerprints()["missions"]
        self.assertNotEqual(before, after, "файл очереди изменился, а отпечаток — нет")

    def test_control_audit_fingerprint_changes(self):
        """Журнал — во временной папке: тест не пишет в живой журнал оператора.

        Отпечаток и запись обязаны смотреть на ОДИН файл — иначе проверка ничего
        не значит; поэтому подменяем оба пути разом и возвращаем как было.
        """
        with tempfile.TemporaryDirectory() as tmp:
            saved = (audit.LOG_DIR, audit.JSONL, audit.PLAIN)
            audit.LOG_DIR = Path(tmp)
            audit.JSONL = Path(tmp) / "audit.jsonl"
            audit.PLAIN = Path(tmp) / "synapse.log"
            try:
                before = self.core.fingerprints()["audit"]
                audit.log("test.fingerprint", note="проверка")
                after = self.core.fingerprints()["audit"]
            finally:
                audit.LOG_DIR, audit.JSONL, audit.PLAIN = saved
        self.assertNotEqual(before, after, "журнал вырос, а отпечаток — нет")

    def test_control_reports_folder_key_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            before = self.core._dir_key(folder)
            (folder / "report.json").write_text("{}", encoding="utf-8")
            self.assertNotEqual(before, self.core._dir_key(folder))

    def test_missing_file_is_not_a_crash(self):
        self.assertEqual(self.core._file_key(Path("нет-такого-файла.json")), (0, 0))

    def test_reports_list_is_remembered_while_folder_unchanged(self):
        first = self.core.reports(5)
        second = self.core.reports(5)
        self.assertEqual(first, second)
        self.assertTrue(self.core._reports_cache, "список отчётов не запомнился")

    def test_status_gives_numbers_not_none(self):
        """Раньше подпись тела показывала «тактов None»: панель брала st['state'], а его нет."""
        st = self.core.status()
        self.assertIsInstance(st["total_ticks"], int)
        self.assertIsInstance(st["wakeups"], int)
        self.assertIn("queued", st)

    def test_brain_state_reports_availability_key(self):
        """Ключ, который панель показывает: раньше она читала несуществующий 'server_alive'."""
        st = self.core.brain_state()
        self.assertIn("available", st)
        self.assertIn("mode", st)


class TestWindowRefresh(unittest.TestCase):
    """Само окно: неизменный вид не пересобирается, выделение переживает обновление."""

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as e:                        # noqa: BLE001
            raise unittest.SkipTest(f"Qt в этом интерпретаторе нет: {e}")
        # Песочница ОБЯЗАТЕЛЬНА: у окна появился closeEvent, который гасит свой сервер
        # и пишет panel.close в журнал. Без temp_live этот тест писал бы в ЖИВОЙ журнал
        # оператора (нашла 25.09.2026 сверкой sha256 data/ до и после прогона).
        cls._live = temp_live()
        cls._live.__enter__()
        # Данные тест создаёт САМ в песочнице. Раньше эти три проверки опирались на
        # живую очередь и живой журнал оператора — то есть тест был свидетелем его
        # данных и падал, стоило очереди опустеть. Дверь песочницы эту зависимость и
        # вскрыла (25.09.2026).
        for i in range(3):
            missions.add("note", {"text": f"seed {i}"})
        audit.log("test.seed", note="строка для проверки журнала окна")
        cls.app = QApplication.instance() or QApplication(sys.argv[:1])
        from synapse.qt_panel import SynapseWindow
        cls.win = SynapseWindow(PanelCore(quiet_synapse()))

    @classmethod
    def tearDownClass(cls):
        cls.win.close()                               # сначала закрыть окно (пишет в temp)
        cls._live.__exit__(None, None, None)

    def test_unchanged_refresh_keeps_the_same_table_items(self):
        """Ничего не менялось — ячейки те же объекты, то есть таблицу не пересобирали."""
        self.win._refresh()
        item = self.win.tbl.item(0, 0)
        self.assertIsNotNone(item, "в таблице миссий нет строк")
        self.win._refresh()
        self.assertIs(self.win.tbl.item(0, 0), item, "таблица миссий пересобрана без причины")

    def test_unchanged_refresh_keeps_journal_text(self):
        self.win._refresh()
        text = self.win.txt_journal.toPlainText()
        self.assertTrue(text.strip(), "журнал пуст")
        self.win._refresh()
        self.assertEqual(self.win.txt_journal.toPlainText(), text, "журнал переписан заново")

    def test_selection_survives_a_queue_change(self):
        """Очередь изменилась — таблица пересобралась, но выделение осталось на той же миссии."""
        self.win._refresh()
        row = min(2, max(0, self.win.tbl.rowCount() - 1))
        self.win.tbl.selectRow(row)
        mid = self.win._selected_id()
        self.assertTrue(mid, "нечего выделять — пустая таблица миссий")
        touch_later(missions.QUEUE)
        self.win._refresh()
        self.assertEqual(self.win._selected_id(), mid, "выделение потерялось при обновлении")

    def test_control_refresh_is_cheap_when_nothing_changed(self):
        """КОНТРОЛЬ по цене: было 15-23 мс каждую секунду, стало — доли миллисекунды."""
        for _ in range(3):
            self.win._refresh()
        took = []
        for _ in range(8):
            t0 = time.perf_counter()
            self.win._refresh()
            took.append((time.perf_counter() - t0) * 1000)
        med = statistics.median(took)
        print(f"   обновление окна без изменений: {med:.2f} мс (было 15-23 мс)")
        self.assertLess(med, 5.0, f"обновление окна стоит {med:.2f} мс — снова пересобираем всё")


if __name__ == "__main__":
    unittest.main(verbosity=2)
