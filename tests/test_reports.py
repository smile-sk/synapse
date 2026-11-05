"""Тесты v0.4: отчёты человеческим языком + миссии понятной формой.

    python tests/test_reports.py        (запускать из корня проекта)

Проверяем то, что видит оператор: подпись отчёта, сам текст, разбор баннера,
человеческие слова про порты и отказ двери ДО постановки миссии.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import missions, reports             # noqa: E402
from synapse.panel_core import PanelCore          # noqa: E402


import pytest                                          # noqa: E402

from synapse import i18n                               # noqa: E402


@pytest.fixture(autouse=True)
def _russian_language():
    """Эти тесты проверяют РУССКИЙ текст — тот, который читает Стас.

    Так и надо: язык по умолчанию теперь английский (ui.lang в настройках), а
    английский путь отдельно проверяет tests/test_i18n_theme.py. Иначе проверка
    «отчёт читается» зависела бы от того, какой язык случайно стоит в конфиге.
    """
    i18n.set_lang("ru")
    yield
    i18n.set_lang("en")

LIVE = ROOT / "data" / "reports" / "20260924-162750-m-20260924-162624-22.json"

FAKE = {
    "at": "2026-09-24T16:26:52", "cidr": "192.168.1.0/24", "mission": "m-1",
    "scanned": 254, "alive_count": 2,
    "hosts": [
        {"ip": "192.168.1.15", "open": [139, 445], "banners": {"445": ""}, "shares": []},
        {"ip": "192.168.1.88", "open": [22, 80, 445],
         "banners": {"22": "SSH-2.0-OpenSSH_7.4", "80": "HTTP/1.1 302 Moved Temporarily Date: Thu",
                     "445": ""},
         "shares": ["Install", "Stas"]},
    ],
    "error": None,
}


class TestPorts(unittest.TestCase):
    def test_service_names_are_services_not_verdicts(self):
        """445 — это «SMB, общие папки», а не «дыра»: отчёт не пугает, а называет."""
        self.assertIn("SMB", reports.port_ru(445))
        self.assertIn("SSH", reports.port_ru(22))
        self.assertIn("RDP", reports.port_ru(3389))
        self.assertIn("служба не опознана", reports.port_ru(9999))

    def test_banner_translated_to_human(self):
        http = "HTTP/1.1 501 Not Implemented Content-Length: 615 Content-Type: text/html"
        self.assertEqual(reports.banner_ru(80, http), "ответ: 501 Not Implemented")
        ssh = reports.banner_ru(22, "SSH-2.0-OpenSSH_7.4")
        self.assertIn("OpenSSH_7.4", ssh)
        self.assertEqual(reports.banner_ru(445, ""), "", "молчащую службу не выдумываем")

    def test_http_server_header_not_glued_to_next(self):
        b = "HTTP/1.1 200 OK Server: Web server Date: Thu, 24 Sep 2026 13:27:44 GMT"
        got = reports.banner_ru(80, b)
        self.assertIn("200 OK", got)
        self.assertIn("Web server", got)
        self.assertNotIn("Date", got, "заголовки не должны прилипать к имени сервера")


class TestHumanReport(unittest.TestCase):
    def test_live_report_reads_like_a_report(self):
        rep = reports.load(LIVE)
        text = reports.render(rep)
        for needle in ("АУДИТ СЕТИ 192.168.1.0/24", "24.09.2026 16:26", "КОРОТКО, ЧТО НАШЛОСЬ",
                       "ОБЩИЕ ПАПКИ", "ХОСТЫ ПО ОДНОМУ", "ЧТО ЭТО ЗНАЧИТ"):
            self.assertIn(needle, text, f"в отчёте нет раздела: {needle}")
        self.assertIn("192.168.1.88", text)
        self.assertIn("Install", text)
        self.assertIn("OpenSSH", text)

    def test_report_says_out_loud_what_it_did_not_do(self):
        """Честность в тексте, а не в голове: права записи не проверялись — это сказано."""
        text = reports.render(reports.load(LIVE))
        self.assertIn("НЕ проверялись", text)
        self.assertIn("права НЕ проверялись", text)

    def test_title_is_human_not_a_filename(self):
        t = reports.title(reports.load(LIVE))
        # слово Стаса 24.09: первым в подписи — КОМАНДА, как её зовут в очереди
        self.assertTrue(t.startswith("net_audit"), t)
        self.assertIn("192.168.1.0/24", t)
        self.assertIn("живых 7", t)
        self.assertNotIn(".json", t)

    def test_summary_line_counts_services(self):
        line = reports.summary_line(FAKE)
        self.assertIn("живых хостов 2", line)
        self.assertIn("SMB: 2", line)
        self.assertIn("SSH: 1", line)
        self.assertIn("192.168.1.88", line)          # у кого общие папки

    def test_empty_scan_explains_itself(self):
        text = reports.render({"cidr": "127.0.0.0/29", "hosts": [], "alive_count": 0,
                               "at": "2026-09-24T15:21:59", "error": None})
        self.assertIn("Никто не отозвался", text)

    def test_error_report_says_why(self):
        text = reports.render({"cidr": "8.8.8.0/24", "hosts": [], "error": "подсеть не своя"})
        self.assertIn("НЕ ВЫШЛО", text)
        self.assertIn("подсеть не своя", text)

    def test_unknown_json_shows_fields_not_python(self):
        text = reports.render({"какой-то": "отчёт", "число": 3})
        self.assertIn("какой-то: отчёт", text)
        self.assertIn("число: 3", text)


class TestFiles(unittest.TestCase):
    def test_text_saved_next_to_json_and_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = Path(tmp) / "20260924-000000-m-1.json"
            j.write_text(json.dumps(FAKE, ensure_ascii=False), encoding="utf-8")
            t = reports.save_text(FAKE, j)
            self.assertEqual(t, Path(tmp) / "20260924-000000-m-1.txt")
            self.assertTrue(t.exists())
            got = reports.read_text(j)
            self.assertIn("АУДИТ СЕТИ", got)
            self.assertEqual(got, reports.render(FAKE)[:20000])

    def test_read_text_falls_back_to_json(self):
        """.txt может не быть (старые отчёты) — читаем JSON и рисуем на лету."""
        with tempfile.TemporaryDirectory() as tmp:
            j = Path(tmp) / "rep.json"
            j.write_text(json.dumps(FAKE, ensure_ascii=False), encoding="utf-8")
            self.assertIn("АУДИТ СЕТИ", reports.read_text(j))

    def test_broken_file_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = Path(tmp) / "bad.json"
            j.write_text("{это не json", encoding="utf-8")
            self.assertEqual(reports.load(j), {})
            self.assertIn("не прочитать", reports.read_text(j))


class TestMissionForm(unittest.TestCase):
    """Форма миссий: что можно дать, чего нельзя и почему — ДО нажатия.

    Здесь проверяется АНГЛИЙСКИЙ интерфейс — он теперь язык по умолчанию:
    подписи инструментов, слова двери, подсказка про свою сеть. Русские подписи
    живут в блоке отчётов (там свой язык) и в tests/test_i18n_theme.py.
    """

    @classmethod
    def setUpClass(cls):
        cls.core = PanelCore()

    def setUp(self):
        i18n.set_lang("en")

    def test_kinds_have_english_labels_and_hints(self):
        """Подписи инструментов переводимы, а КОМАНДА (kind) — всегда английская."""
        kinds = self.core.mission_kinds()
        # v0.7: инструментов стало семь — три новых команды (write_probe, certs, diff)
        # v0.24.0: восьмой — remote (удалённое управление указанным адресом)
        # v0.28.0: девятый — peers (взгляд на соединения своих узлов, только чтение)
        self.assertEqual([k["kind"] for k in kinds],
                         ["net_audit", "peers", "ask", "remote", "stimulus", "note",
                          "write_probe", "certs", "diff"])
        for k in kinds:
            self.assertTrue(k["label"], f"{k['kind']}: нет подписи")
            self.assertTrue(k["hint"], f"{k['kind']}: нет подсказки")
            # "host" и "none" — команды, которые умеют работать по последнему аудиту
            self.assertIn(k["needs"], ("cidr", "text", "host", "none"))

    def test_label_lookup(self):
        self.assertEqual(self.core.mission_label("net_audit"), "network audit")
        self.assertEqual(self.core.mission_label("ask"), "ask the brain")

    def test_door_checked_before_queueing(self):
        """Живой конфиг разрешает любые сети (слово Стаса 30.09.2026), дверь при этом на месте."""
        ok, why = self.core.plan_mission("net_audit", "8.8.8.0/24")
        self.assertTrue(ok, "с allowed_cidrs 0.0.0.0/0 чужая сеть ставится в очередь")
        self.assertIn("254", why)
        ok, why = self.core.plan_mission("net_audit", "192.168.1.0/24")
        self.assertTrue(ok)
        self.assertIn("254", why)
        # КОНТРОЛЬ: со своим списком из приватных сетей панель обязана отбить ДО очереди.
        own = PanelCore()
        own.cfg = {"net": {"allowed_cidrs": ["192.168.0.0/16"], "max_hosts": 256}}
        ok, why = own.plan_mission("net_audit", "8.8.8.0/24")
        self.assertFalse(ok, "чужую сеть нельзя даже поставить в очередь")
        self.assertTrue(why.startswith(i18n.t("plan.door_no", why="")),
                        f"отказ обязан назвать дверь: {why}")

    def test_empty_fields_explained_humanly(self):
        self.assertFalse(self.core.plan_mission("net_audit", "")[0])
        self.assertIn("My network", self.core.plan_mission("net_audit", "")[1])
        self.assertFalse(self.core.plan_mission("ask", "", "")[0])
        self.assertTrue(self.core.plan_mission("note", "", "пометка")[0])

    def test_rows_are_human_and_statuses_are_identifiers(self):
        # Данные создаёт САМ тест (v0.15.0). Раньше проверка опиралась на ЖИВУЮ
        # очередь оператора: тест был свидетелем его данных и падал, стоило
        # очереди опустеть — что и вскрыла песочница tests/conftest.py.
        m = missions.add("net_audit", {"cidr": "127.0.0.0/29"})
        missions.mark_running(m["id"], "done", "аудит сети 127.0.0.0/29")
        reports.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (reports.REPORTS_DIR / f"20260925-170000-{m['id']}.json").write_text(
            json.dumps({"kind": "net_audit", "at": "2026-09-25T17:00:00",
                        "cidr": "127.0.0.0/29", "hosts": [], "alive_count": 0,
                        "mission": m["id"]}, ensure_ascii=False), encoding="utf-8")
        rows = self.core.mission_rows(5)
        self.assertTrue(rows, "история миссий пуста — нечего показать оператору")
        for r in rows:
            # Статус — слово ИЗ ФАЙЛА очереди (подмены failed -> error больше нет:
            # её отменили после находки QA Ореста — две панели говорили разное).
            self.assertIn(r["status"], missions.STATUSES)
            self.assertTrue(r["what"] and r["created"])
        done = [r for r in rows if r["status"] == "done"]
        if done:
            self.assertTrue(done[0]["result"], "итог миссии не должен быть пустым")
        # Живая очередь бывает любой: ищем СВОЮ команду, а не «первую попавшуюся».
        # Итог миссии собирается заново из JSON НА ТЕКУЩЕМ языке, поэтому проверяем
        # оба языка явно, а не «какой оказался в процессе» (раньше тест молча
        # опирался на русский интерфейс живого config.json).
        audits = [r for r in rows if r["kind"] == "net_audit" and r["result"]]
        self.assertTrue(audits, "своя миссия net_audit не попала в строки таблицы")
        if audits:
            was = i18n.lang()
            try:
                i18n.set_lang("ru")
                self.assertIn("аудит", self.core.mission_result(audits[0]))
                i18n.set_lang("en")
                self.assertIn("audit", self.core.mission_result(audits[0]))
            finally:
                i18n.set_lang(was)

    def test_reports_list_has_human_titles(self):
        rows = self.core.reports(5)
        if not rows:
            self.skipTest("отчётов нет")
        for r in rows:
            self.assertNotIn(".json", r["title"])
            self.assertTrue(r["title"].strip())


class TestFrameBudget(unittest.TestCase):
    """Кадр не должен снова стать дорогим: слои кэшируются, а не собираются каждый раз."""

    @classmethod
    def setUpClass(cls):
        try:
            from synapse.render import ImageRenderer
        except Exception as e:                        # noqa: BLE001
            raise unittest.SkipTest(f"Pillow нет: {e}")
        cls.core = PanelCore()
        cls.r = ImageRenderer(cls.core)
        cls.r.resize(600, 520)

    def test_layers_are_cached_between_frames(self):
        pts = self.core.screen(600, 520)
        a = self.r._static_layer(pts)
        b = self.r._static_layer(pts)
        self.assertIs(a, b)
        n1 = self.r._node_layer(pts)
        n2 = self.r._node_layer(pts)
        self.assertIs(n1, n2, "слой узлов обязан кэшироваться вместе со связями")

    def test_cached_frame_is_much_cheaper_than_full_rebuild(self):
        import time
        pts = self.core.screen(600, 520)
        self.r.render_image(pts)                      # прогрев
        t0 = time.perf_counter()
        for _ in range(6):
            self.r.render_image(pts)
        cached = (time.perf_counter() - t0) / 6
        t0 = time.perf_counter()
        for _ in range(6):
            self.r._reset()                           # как если бы тело крутилось каждый кадр
            self.r.render_image(pts)
        rebuilt = (time.perf_counter() - t0) / 6
        self.assertLess(cached, rebuilt * 0.8,
                        f"кэш не экономит время: {cached * 1000:.2f} мс против "
                        f"{rebuilt * 1000:.2f} мс на пересборке")

    def test_excited_body_brighter_than_calm(self):
        """Порядок рисования: связи, свечение, ПОВЕРХ них узлы. Иначе горячие связи
        затирают узлы и возбуждённое тело выглядит тусклее спокойного (было именно так)."""
        body = self.core.body

        def bright(img):
            return sum(1 for px in img.get_flattened_data() if px[0] + px[1] + px[2] > 260)

        body.activity = [0.0] * body.size
        calm = bright(self.r.render_image())
        body.poke(body.pick("sensory", k=10, seed=5), 1.0)
        body.step()
        excited = bright(self.r.render_image())
        self.assertGreater(excited, calm)


if __name__ == "__main__":
    unittest.main(verbosity=2)
