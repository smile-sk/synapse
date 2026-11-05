"""v0.10.1: дверь очереди — незнакомое имя инструмента НЕ попадает в очередь.

Жалоба Стаса 25.09.2026: «"такого_нет" ещё глаз мозолит — сделай тоже англ».
Разобралась: это была НЕ строка языка, а МОЙ МУСОР В ДАННЫХ — 39 записей в живой
очереди, которые я сама наставила, гоняя проверку отказа через консоль. Причина:
missions.add() не проверял имя команды вообще (панель проверяла), а список
инструментов жил в ДВУХ местах — в agent.KINDS и в panel_core.

Лечение не переводом, а ДВЕРЬЮ — так же, как scope.py закрывает сеть:
  1) один список инструментов — missions.KINDS;
  2) агент и панель берут его оттуда же (своих копий больше нет);
  3) add() проверяет имя ДО записи, поэтому консоль не может накидать мусора.

ПЕРВЫМ идёт КОНТРОЛЬ: известное имя обязано в очередь попасть. Без этого
«мусор не пишется» ничего не значило бы — можно было бы не писать вообще никогда.

ЖИВЫЕ ДАННЫЕ НЕ ТРОГАЕМ: очередь подменяется временной папкой (missions.QUEUE),
поэтому тест не может испортить рабочую очередь оператора — та самая болезнь,
из которой эта правка и выросла.

Запуск:  python tests/test_mission_kinds.py   (или pytest из корня проекта)
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import run                                                    # noqa: E402
from synapse import agent as agent_mod, config, i18n, missions, reports  # noqa: E402
from live_data import temp_live                                          # noqa: E402
from synapse.tools import certs, diff, write_probe                     # noqa: E402
from synapse.panel_core import MISSION_KIND_IDS, PanelCore      # noqa: E402

CYRILLIC = re.compile("[А-Яа-яЁё]")
#: Имя, которое я гоняла как «заведомо несуществующую команду» — оно и всплыло в живых данных.
JUNK = "такого_нет"


class TempQueue:
    """Очередь, журнал и state — во временной папке: живые данные тест не трогает.

    Обёртка над общим помощником tests/live_data.py, чтобы в этом файле код читался
    коротко. Он же уводит из-под удара state.json: такт живого организма тесты не
    имеют права крутить (счётчик тактов у оператора рос от прогонов).
    """

    def __enter__(self) -> Path:
        self._ctx = temp_live()
        self._ctx.__enter__()
        return missions.QUEUE

    def __exit__(self, *exc) -> bool:
        self._ctx.__exit__(*exc)
        return False


def rows(p: Path) -> list[dict]:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def cli(kind: str, **kw) -> argparse.Namespace:
    """То, что argparse отдаёт cmd_mission (сам парсер для этого не нужен)."""
    base = dict(action="add", kind=kind, cidr=None, text=None, host=None,
                ports=None, note="проверка")
    base.update(kw)
    return argparse.Namespace(**base)


class TestControl(unittest.TestCase):
    """Без этих двух проверок всё остальное ничего не доказывает."""

    def test_known_tool_does_go_to_the_queue(self):
        with TempQueue() as q:
            rec = missions.add("net_audit", {"cidr": "127.0.0.0/30"})
            self.assertTrue(q.exists(), "известная команда не попала в очередь — дверь сломана")
            got = rows(q)
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0]["id"], rec["id"])
            self.assertEqual(got[0]["status"], "queued")

    def test_refusal_text_is_translated_at_all(self):
        """Русский отказ обязан быть русским, иначе проверка «английский без кириллицы» пуста."""
        ru = i18n.t_in("ru", "ag.no_tool", kind=JUNK, kinds="net_audit").replace(JUNK, "")
        en = i18n.t_in("en", "ag.no_tool", kind=JUNK, kinds="net_audit").replace(JUNK, "")
        self.assertTrue(CYRILLIC.search(ru), "русский отказ без кириллицы — словарь не тот")
        self.assertFalse(CYRILLIC.search(en), f"в английском отказе осталась кириллица: {en}")


class TestDoor(unittest.TestCase):
    """Дверь: имя проверяется ДО записи, а технические ошибки — по-английски."""

    def test_junk_tool_is_refused_and_queue_untouched(self):
        with TempQueue() as q:
            missions.add("net_audit")
            before = q.read_bytes()
            with self.assertRaises(ValueError) as ctx:
                missions.add(JUNK)
            self.assertEqual(q.read_bytes(), before, "очередь всё-таки изменилась")
            self.assertEqual(len(rows(q)), 1)
            self.assertIn(JUNK, str(ctx.exception), "в отказе не названо виноватое имя")

    def test_technical_error_is_english(self):
        with TempQueue():
            with self.assertRaises(ValueError) as ctx:
                missions.add(JUNK)
        msg = str(ctx.exception).replace(JUNK, "")
        self.assertEqual(CYRILLIC.findall(msg), [], f"технический текст не английский: {msg}")
        self.assertIn("no such tool", msg)

    def test_bad_status_is_english_too(self):
        with TempQueue() as q:
            missions.add("note", {"text": "x"})
            mid = rows(q)[0]["id"]
            with self.assertRaises(ValueError) as ctx:
                missions.mark(mid, "bogus")
        self.assertEqual(CYRILLIC.findall(str(ctx.exception)), [])

    def test_console_refuses_junk_without_writing(self):
        with TempQueue() as q:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = run.cmd_mission(cli(JUNK))
            self.assertEqual(rc, 2, "консоль не отказала")
            self.assertEqual(rows(q), [], "консоль записала мусор в очередь")
            expected = i18n.t("cli.no_tool", kind=JUNK, kinds=", ".join(missions.KINDS))
            self.assertIn(expected, out.getvalue(), "консоль сказала не словами интерфейса")

    def test_panel_plan_refuses_junk_before_any_click(self):
        """Панель обязана отказать ДО нажатия.

        Ровно здесь был второй промах: прежняя проверка плана «meta[kind] != kind» не
        отказывала НИКОГДА, потому что mission_kind() на незнакомое имя возвращает
        словарь с тем же самым kind. Мусор проходил план и ложился в очередь.
        """
        core = PanelCore.__new__(PanelCore)      # для плана ядру нужен только cfg
        core.cfg = config.load()
        ok, why = core.plan_mission(JUNK)
        self.assertFalse(ok, "план пропустил незнакомое имя инструмента")
        self.assertEqual(why, i18n.t("plan.no_tool", kind=JUNK))

    def test_panel_plan_control_accepts_known_tool(self):
        """КОНТРОЛЬ к предыдущему: известный инструмент план обязан пропустить."""
        core = PanelCore.__new__(PanelCore)
        core.cfg = config.load()
        self.assertTrue(core.plan_mission("note", text="проверка")[0],
                        "план отказал своему же инструменту — отказ ничего не значит")


    def test_console_control_known_kind_writes(self):
        """КОНТРОЛЬ к предыдущему: известная команда консолью обязана попасть в очередь."""
        with TempQueue() as q:
            with redirect_stdout(io.StringIO()):
                rc = run.cmd_mission(cli("note", text="проверка двери"))
            self.assertEqual(rc, 0)
            self.assertEqual(len(rows(q)), 1, "контроль: известная команда не записалась")


class TestToolText(unittest.TestCase):
    """Проза инструментов тоже идёт через словарь — иначе отчёт остаётся русским.

    До этой правки фразы вида «дверь не пустила» и «нет аудита сети — сначала net_audit»
    были вписаны жёстко в код certs/diff/write_probe/net_scan: панель переведёшь, а
    отчёт всё равно русский. Проверяем ЖИВЫЕ ветки инструментов, которым сеть не нужна
    (выключенный тест записи, служебная папка, нет аудитов, хост из миссии).
    """

    def setUp(self):
        self._lang = i18n.lang()
        self.cfg = config.load()

    def tearDown(self):
        i18n.set_lang(self._lang)

    def texts(self) -> list[str]:
        quiet = dict(self.cfg)
        quiet["net"] = {**self.cfg["net"], "allow_write_probe": False}
        with temp_live():            # эти ветки пишут в журнал — только во временный
            return [
                write_probe.probe_share("192.168.1.88", "Install", quiet)["detail"],
                write_probe.probe_share("192.168.1.88", "IPC$", self.cfg)["detail"],
                diff.compare({}, {})["error"],
                certs.targets({"host": "192.168.1.88"}, self.cfg)[1],
            ]

    def test_english_reports_have_no_cyrillic(self):
        i18n.set_lang("en")
        for text in self.texts():
            self.assertEqual(CYRILLIC.findall(text), [], f"в английском отчёте кириллица: {text}")

    def test_russian_reports_stay_russian(self):
        """КОНТРОЛЬ: те же места на русском обязаны быть русскими, иначе проверка пуста."""
        i18n.set_lang("ru")
        for text in self.texts():
            self.assertTrue(CYRILLIC.search(text), f"русская проза потерялась: {text}")


class TestResultLanguage(unittest.TestCase):
    """Итог миссии — на языке интерфейса, если отчёт на диске есть.

    В очереди лежит строка, написанная в тот день и на том языке: при английском
    интерфейсе она выглядела бы русской (тот же «хаус»). Поэтому итог миссий с
    отчётом собирается заново из JSON — как панель перерисовывает человеческий .txt.
    """

    FAKE = {
        "kind": "net_audit", "at": "2026-09-24T16:26:52", "cidr": "192.168.1.0/24",
        "mission": "m-20260924-162624-9", "scanned": 254, "alive_count": 2,
        "hosts": [{"ip": "192.168.1.88", "open": [22, 445], "shares": ["Install"],
                   "banners": {"22": "SSH-2.0-OpenSSH_7.4", "445": ""}}],
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved_dir = reports.REPORTS_DIR
        self._saved_lang = i18n.lang()
        reports.REPORTS_DIR = Path(self._tmp.name)
        (reports.REPORTS_DIR / "20260924-162624-m-20260924-162624-9.json").write_text(
            json.dumps(self.FAKE, ensure_ascii=False), encoding="utf-8")
        self.core = PanelCore.__new__(PanelCore)
        self.core.cfg = config.load()
        self.row = {"id": "m-20260924-162624-9", "kind": "net_audit",
                    "result": "аудит 192.168.1.0/24: живых хостов 2 (строка того дня)"}

    def tearDown(self):
        reports.REPORTS_DIR = self._saved_dir
        i18n.set_lang(self._saved_lang)
        self._tmp.cleanup()

    def test_english_line_is_rebuilt_without_cyrillic(self):
        i18n.set_lang("en")
        got = self.core.mission_result(self.row)
        self.assertEqual(CYRILLIC.findall(got), [], f"в английском итоге кириллица: {got}")
        self.assertIn("192.168.1.0/24", got, "итог собран не из отчёта")
        self.assertNotEqual(got, self.row["result"], "показана старая строка, а не отчёт")

    def test_russian_line_stays_russian(self):
        """КОНТРОЛЬ: на русском тот же отчёт обязан быть русским — иначе проверка пуста."""
        i18n.set_lang("ru")
        got = self.core.mission_result(self.row)
        self.assertTrue(CYRILLIC.search(got), f"русский итог без кириллицы: {got}")

    def test_legacy_russian_prose_is_translated_on_read(self):
        """Старый отчёт: в JSON проза русская — показ идёт за языком окна, файл не трогаем."""
        i18n.set_lang("en")
        plain = reports.translate_legacy("ни одна служба не ответила рукопожатием")
        self.assertEqual(CYRILLIC.findall(plain), [], f"старая проза осталась русской: {plain}")
        with_reason = reports.translate_legacy("сертификат не разобрать: PermissionError")
        self.assertIn("PermissionError", with_reason, "причина потерялась при переводе")
        self.assertEqual(CYRILLIC.findall(with_reason), [])
        unknown = reports.translate_legacy("какая-то своя фраза")
        self.assertEqual(unknown, "какая-то своя фраза", "незнакомую фразу переводить не вправе")
        i18n.set_lang("ru")
        self.assertTrue(CYRILLIC.search(
            reports.translate_legacy("ни одна служба не ответила рукопожатием")),
            "на русском та же фраза обязана остаться русской")

    def test_without_report_the_stored_line_is_shown(self):
        """Отчёта нет — показываем что записано: выдумывать нечего."""
        row = {"id": "m-нет-такого-отчёта", "kind": "net_audit", "result": "как было записано"}
        i18n.set_lang("en")
        self.assertEqual(self.core.mission_result(row), "как было записано")

    def test_body_speech_is_not_rewritten(self):
        """Стимул телу отчёта не имеет — строка остаётся как записана (не перевираем)."""
        row = {"id": "m-20260924-162624-9", "kind": "stimulus", "result": "стимул принят телом"}
        self.assertEqual(self.core.mission_result(row), "стимул принят телом")


class TestOneListEverywhere(unittest.TestCase):
    """Один список инструментов на весь организм — иначе дверь снова разойдётся."""

    def test_agent_takes_the_same_list(self):
        self.assertIs(agent_mod.KINDS, missions.KINDS, "у агента снова свой список инструментов")

    def test_panel_shows_every_tool(self):
        self.assertEqual(set(MISSION_KIND_IDS), set(missions.KINDS),
                         "панель и очередь знают разные наборы инструментов")
        self.assertEqual(len(MISSION_KIND_IDS), len(set(MISSION_KIND_IDS)), "в списке повторы")

    def test_live_queue_has_no_foreign_kinds(self):
        """Живая очередь: посторонних имён инструментов там быть не должно."""
        p = ROOT / "data" / "missions" / "queue.json"
        if not p.exists():
            self.skipTest("живой очереди пока нет")
        foreign = sorted({str(r.get("kind")) for r in rows(p)} - set(missions.KINDS))
        self.assertEqual(foreign, [], f"в живой очереди посторонние имена: {foreign}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
