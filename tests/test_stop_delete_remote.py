"""v0.24.0: настоящая остановка миссии, настоящее удаление и миссия remote.

    python -m pytest tests/test_stop_delete_remote.py

Слово Стаса 26.09.2026: «останавливать и удалять нужно — если на кнопке написано
УДАЛИТЬ!» и «новая миссия — удалённое управление указанным IP устройством,
например по psexec, или свои предложение».

КОНТРОЛЬ ПЕРВЫМ. Прежде чем доказывать, что остановка работает, проверяем, что
САМ МЕХАНИЗМ — причина: цикл, который спрашивает cancel, без просьбы проходит
до конца, а с просьбой встаёт. Иначе «встал» ничего не доказывает.

Что проверяется по-настоящему:
  * cancel.requested — это то, что останавливает работу (контроль + остановка);
  * drop_mission у идущей миссии ставит сигнал остановки И убирает запись из
    очереди (раньше оставался refused, а работа шла дальше);
  * аудит сети встаёт на середине и пишет ЧАСТИЧНЫЙ отчёт, а не молчит;
  * remote: дверь не пускает чужую сеть, выключатель работает, без доступа
    инструмент только ищет пути, с доступом — выполняет и отчитывается;
  * команда не собирается из подозрительного адреса;
  * общий лог приложения (app.log) пишется — и в песочницу, не в живые данные.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import applog, cancel, config, i18n, missions, scope   # noqa: E402
from synapse.panel_core import MISSION_KINDS, PanelCore            # noqa: E402
from synapse.tools import net_scan, remote, report_io              # noqa: E402
from live_data import temp_live                                    # noqa: E402

CYRILLIC = __import__("re").compile("[А-Яа-яЁё]")
CFG = {"net": {"allow_remote": True, "connect_timeout": 0.05, "remote_timeout": 5,
               "allowed_cidrs": ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12",
                                 "192.168.0.0/16"]}}
HOST = "192.168.1.88"
FOREIGN = "8.8.8.8"


class TestCancelIsTheReason(unittest.TestCase):
    """Контроль: крутит именно cancel, а не что-то ещё."""

    def test_loop_finishes_without_a_request(self):
        steps = 0
        for _ in range(5):
            if cancel.requested("m-control"):
                break
            steps += 1
        self.assertEqual(steps, 5, "без просьбы цикл обязан пройти целиком")

    def test_loop_stops_after_a_request(self):
        cancel.clear("m-stop")
        steps = 0
        for _ in range(5):
            if cancel.requested("m-stop"):
                break
            steps += 1
            if steps == 2:
                cancel.request("m-stop")
        cancel.clear("m-stop")
        self.assertEqual(steps, 2, "просьба обязана остановить цикл на следующем шаге")

    def test_clear_removes_the_request(self):
        cancel.request("m-x")
        self.assertTrue(cancel.requested("m-x"))
        cancel.clear("m-x")
        self.assertFalse(cancel.requested("m-x"), "след отмены не должен жить вечно")
        self.assertNotIn("m-x", cancel.asked())


class TestDeleteIsReal(unittest.TestCase):

    def setUp(self):
        with temp_live() as root:            # очередь — в песочнице
            self.root = root
        self.core = PanelCore.__new__(PanelCore)

    def test_queued_mission_is_removed_from_the_queue(self):
        rec = missions.add("note", {"text": "привет"})
        res = PanelCore.__new__(PanelCore).drop_mission(rec["id"])
        self.assertTrue(res["ok"])
        self.assertNotIn(rec["id"], [m["id"] for m in missions.all_missions()],
                         "кнопка УДАЛИТЬ обязана убрать запись, а не пометить её")

    def test_running_mission_is_stopped_and_removed(self):
        rec = missions.add("note", {"text": "работа"})
        missions.take_next()                            # теперь она running
        res = PanelCore.__new__(PanelCore).drop_mission(rec["id"])
        self.assertTrue(res["stopped"], "идущую работу надо ОСТАНОВИТЬ")
        self.assertTrue(cancel.requested(rec["id"]), "сигнал остановки не поставлен")
        self.assertIsNone(missions.status_of(rec["id"]), "запись осталась в очереди")
        cancel.clear(rec["id"])

    def test_missing_mission_is_reported_not_silent(self):
        res = PanelCore.__new__(PanelCore).drop_mission("m-нет-такой")
        self.assertFalse(res["ok"])
        self.assertTrue(res["why"])


class TestNetworkAuditStops(unittest.TestCase):

    def test_audit_that_is_already_stopped_does_not_scan(self):
        cancel.request("m-audit-1")
        try:
            rep = net_scan.audit_network("10.13.37.0/30", mission="m-audit-1", cfg=CFG)
        finally:
            cancel.clear("m-audit-1")
        self.assertTrue(rep.get("stopped"))
        self.assertEqual(rep.get("hosts"), [], "при остановке ни один хост не трогаем")
        self.assertTrue(rep.get("report_text"), "частичный отчёт обязан остаться на диске")

    def test_audit_stops_between_hosts_and_keeps_what_it_saw(self):
        """Середина работы: первый хост осмотрен, второго не касаемся."""
        seen: list[str] = []
        real_sweep, real_scan = net_scan.sweep, net_scan.scan_ports_fast

        def fake_sweep(cidr, cfg, on_host=None, mission=""):
            return ["10.13.37.1", "10.13.37.2"]

        def fake_scan(host, ports, timeout=0.35, workers=256, mission=""):
            seen.append(host)
            cancel.request(mission)                     # оператор нажал «Удалить»
            return {80: True}

        net_scan.sweep, net_scan.scan_ports_fast = fake_sweep, fake_scan
        try:
            rep = net_scan.audit_network("10.13.37.0/30", mission="m-audit-2", cfg=CFG)
        finally:
            net_scan.sweep, net_scan.scan_ports_fast = real_sweep, real_scan
            cancel.clear("m-audit-2")
        self.assertEqual(seen, ["10.13.37.1"], "второй хост трогать было нечем")
        self.assertTrue(rep.get("stopped"))
        self.assertEqual(len(rep["hosts"]), 1, "то, что успели увидеть, терять нельзя")


class TestRemoteControl(unittest.TestCase):

    def setUp(self):
        self.real_probe, self.real_run = remote.probe_ways, remote.run_method

    def tearDown(self):
        remote.probe_ways, remote.run_method = self.real_probe, self.real_run

    def test_door_refuses_a_foreign_network(self):
        rep = remote.control({"host": FOREIGN, "command": "whoami"}, mission="m-1", cfg=CFG)
        self.assertEqual(rep["error"], i18n.t("err.door"))
        self.assertFalse(scope.guard(FOREIGN, CFG))

    def test_off_switch_is_respected(self):
        cfg = {"net": {"allow_remote": False}}
        rep = remote.control({"host": HOST, "command": "whoami"}, mission="m-2", cfg=cfg)
        self.assertEqual(rep["error"], i18n.t("err.remote_off"))

    def test_no_host_is_reported(self):
        rep = remote.control({}, mission="m-3", cfg=CFG)
        self.assertEqual(rep["error"], i18n.t("err.no_host"))

    def test_without_credentials_it_only_looks_for_ways(self):
        """Без доступа инструмент НЕ подбирает пароли — он показывает пути."""
        remote.probe_ways = lambda host, cfg, mission="": [
            {"port": 5985, "method": "winrm", "open": True},
            {"port": 445, "method": "smb", "open": True},
            {"port": 3389, "method": "rdp", "open": False}]
        rep = remote.control({"host": HOST}, mission="m-4", cfg=CFG)
        self.assertEqual(rep["state"], "found")
        self.assertEqual(rep["attempts"], [], "без логина и команды выполнять нечего")
        self.assertIn("winrm", [w["method"] for w in rep["ways"] if w["open"]])
        self.assertTrue(rep.get("report_text"), "отчёт обязан лечь на диск")

    def test_with_credentials_it_runs_and_reports_what_worked(self):
        calls: list[str] = []
        remote.probe_ways = lambda host, cfg, mission="": [
            {"port": 5985, "method": "winrm", "open": True},
            {"port": 22, "method": "ssh", "open": True}]

        def fake_run(method, host, user, password, command, timeout, port=None):
            calls.append(method)
            if method == "winrm":
                return {"method": method, "port": port, "state": "denied", "exit": 1,
                        "output": "access denied", "detail": ""}
            return {"method": method, "port": port, "state": "ok", "exit": 0,
                    "output": "stas-pc\\admin", "detail": ""}

        remote.run_method = fake_run
        rep = remote.control({"host": HOST, "command": "whoami", "user": "stas",
                              "password": "x"}, mission="m-5", cfg=CFG)
        self.assertEqual(calls, ["winrm", "ssh"], "способы пробуются по очереди")
        self.assertEqual(rep["state"], "ok")
        self.assertEqual(rep["method"], "ssh")
        self.assertIn("admin", rep["output"])

    def test_stop_signal_interrupts_the_attempts(self):
        remote.probe_ways = lambda host, cfg, mission="": [
            {"port": 5985, "method": "winrm", "open": True}]
        cancel.request("m-6")
        try:
            rep = remote.control({"host": HOST, "command": "whoami", "user": "u",
                                  "password": "p"}, mission="m-6", cfg=CFG)
        finally:
            cancel.clear("m-6")
        self.assertEqual(rep["state"], "stopped")
        self.assertEqual(rep["attempts"], [], "после остановки не выполняем ничего")

    def test_command_is_not_built_from_a_dirty_address(self):
        row = remote.run_method("winrm", "192.168.1.88'; whoami #", "u", "p", "id", 5)
        self.assertEqual(row["state"], "skipped")
        self.assertEqual(row["detail"], i18n.t("rm.bad_host"))

    def test_windows_methods_refuse_to_pretend_on_other_systems(self):
        keep = remote.IS_WIN
        remote.IS_WIN = False
        try:
            row = remote.run_method("wmi_dcom", HOST, "u", "p", "whoami", 5)
        finally:
            remote.IS_WIN = keep
        self.assertEqual(row["state"], "skipped")
        self.assertEqual(row["detail"], i18n.t("rm.need_windows"))


class TestRemoteIsWiredIn(unittest.TestCase):

    def test_remote_is_a_known_tool_with_a_label(self):
        self.assertIn("remote", missions.KINDS)
        kinds = {k["kind"]: k for k in MISSION_KINDS}
        self.assertIn("remote", kinds)
        self.assertNotEqual(kinds["remote"]["label"], "tool.remote.label",
                            "подпись инструмента не переведена")
        self.assertEqual(kinds["remote"]["needs"], "host")

    def test_unknown_tool_is_still_refused(self):
        with self.assertRaises(ValueError):
            missions.add("взломай_соседа")

    def test_report_reads_in_both_languages(self):
        rep = {"kind": "remote", "at": "2026-09-26T16:00:00", "mission": "m-9",
               "host": HOST, "command": "whoami", "user": "stas", "state": "ok",
               "method": "winrm", "output": "stas-pc", "error": None,
               "ways": [{"port": 5985, "method": "winrm", "open": True},
                        {"port": 3389, "method": "rdp", "open": False}],
               "attempts": [{"method": "winrm", "state": "ok", "output": "stas-pc"}]}
        from synapse import reports
        ru, en = reports.render(rep, "ru"), reports.render(rep, "en")
        self.assertRegex(ru, CYRILLIC)
        self.assertNotRegex(en, CYRILLIC, "в английском отчёте осталась кириллица")
        self.assertIn("5985", ru)
        self.assertIn("whoami", en)
        self.assertNotEqual(reports.summary_line(rep, "ru"), reports.summary_line(rep, "en"))

    def test_audit_summary_says_it_was_stopped(self):
        from synapse import reports
        rep = {"kind": "net_audit", "cidr": "10.13.37.0/30", "at": "2026-09-26T16:00:00",
               "stopped": True, "hosts": [{"ip": "10.13.37.1", "open": [80]}],
               "alive_count": 2}
        self.assertIn("остановлен", reports.summary_line(rep, "ru"))
        self.assertNotRegex(reports.summary_line(rep, "en"), CYRILLIC)


class TestReportsStayInTheSandbox(unittest.TestCase):
    """Отчёты инструментов — тоже данные оператора, и тесты их не трогают."""

    def test_report_io_writes_into_the_sandbox(self):
        with temp_live() as root:
            rep = {"kind": "remote", "host": HOST}
            out = report_io.save(rep, mission="m-sandbox", kind="remote")
            self.assertTrue(Path(out["report"]).exists())
            self.assertEqual(Path(out["report"]).parent, root / "reports",
                             "отчёт ушёл в живые data/reports оператора")


class TestAppLog(unittest.TestCase):

    def test_app_log_is_written_where_logs_live(self):
        with temp_live() as root:
            path = applog.install("тест")
            applog.note("проверка")
            self.assertEqual(path.parent, root, "лог приложения ушёл в живые данные")
            text = path.read_text(encoding="utf-8")
            self.assertIn("app.start", text)
            self.assertIn("проверка", text)

    def test_the_window_entry_point_installs_the_log(self):
        """Тот, чем оператор запускает окно, обязан взводить лог — раньше не взводил.

        Механизм проверен выше по факту, а здесь именно ПОДКЛЮЧЕНИЕ: .pyw шёл мимо
        run.py, поэтому app.log не появлялся вообще.
        """
        text = (ROOT / "Synapse.pyw").read_text(encoding="utf-8", errors="replace")
        self.assertIn("applog.install", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
