"""Тесты по находкам независимого QA (Орест, 25.09.2026).

Каждый тест закрывает НАХОДКУ, а не украшает отчёт: замок тела берётся по-настоящему,
снятая миссия не перезаписывается итогом, окно гасит свой сервер, версия сходится с
чейнджлогом, у словаря нет сирот, числа закона тела воспроизводимы.

Правило проекта то же: контроль идёт ПЕРВЫМ, иначе «стало лучше» ничего не значит.
Живые данные оператора не трогаем — очередь, журналы и state уводит temp_live().
"""
from __future__ import annotations

import importlib
import io
import json
import re
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import config, i18n, missions, online, reports, scope, theme   # noqa: E402
from synapse.body import Body                                       # noqa: E402
from synapse.agent import Synapse                                   # noqa: E402
from live_data import nt_mode, temp_live                            # noqa: E402


def quiet_synapse():
    """Организм без живого мозга: тесты не поднимают llama-server."""
    cfg = config.load()
    cfg["brain"] = dict(cfg.get("brain", {}), mode="off")
    return Synapse(cfg=cfg)


def measure(decay, gain, k, text, thr=0.02, sustain=1.2, limit=600):
    """Пик зажжённых и такт, когда тело погасло совсем (None — не гаснет)."""
    b = Body.load()
    b.stimulus(text, amp=1.0, k=k)
    peak, gone = 0, None
    for i in range(limit):
        b.step(decay=decay, gain=gain, threshold=thr, sustain=sustain)
        lit = b.lit(thr)
        peak = max(peak, lit)
        if lit == 0:
            gone = i + 1
            break
    return peak, gone


class _CountingLock:
    """Обёртка вокруг настоящего RLock: считает, сколько раз тело ВЗЯЛО замок."""

    def __init__(self, real):
        self.real = real
        self.entered = 0

    def __enter__(self):
        self.entered += 1
        return self.real.__enter__()

    def __exit__(self, *exc):
        return self.real.__exit__(*exc)


class TestBodyLock(unittest.TestCase):
    """Находка 1: activity мутировали два потока (окно и фоновый такт)."""

    def test_control_without_lock_the_step_loses_an_increment(self):
        """КОНТРОЛЬ: без замка «читал -> спал -> записал» затирает приращение.

        Так вело себя тело ДО правки, и именно это ловил Орест: шаг читает activity,
        считает и заменяет список целиком, а poke в это время прибавляет в старый.
        """
        b = Body.load()
        snapshot = list(b.activity)

        def slow_step():
            src = snapshot                      # прочитал
            time.sleep(0.02)                    # посчитал
            b.activity = [v * 0.5 for v in src]  # записал целиком

        t = threading.Thread(target=slow_step)
        t.start()
        time.sleep(0.005)
        b.poke([0], 1.0)                        # приращение в «старый» список
        t.join()
        self.assertEqual(b.activity[0], 0.0, "контроль сломан: приращение выжило")

    def test_step_and_poke_take_the_lock(self):
        """Тело реально берёт замок — это видно счётчиком, а не глазами."""
        b = Body.load()
        counter = _CountingLock(b.lock)
        b.lock = counter
        b.poke([0], 0.5)
        b.step()
        b.stimulus("qa-lock", k=2)
        self.assertGreaterEqual(counter.entered, 3, "шаг/приращение/стимул обязаны брать замок")

    def test_two_threads_keep_the_body_sane(self):
        """Окно и фоновый такт одновременно: тело не рвётся и не выходит за 0..1."""
        b = Body.load()
        stop = threading.Event()
        errors = []

        def churn():
            try:
                while not stop.is_set():
                    b.step()
                    b.stimulus("qa-thread", amp=0.5, k=2)
            except Exception as e:              # noqa: BLE001 — тест обязан увидеть поломку
                errors.append(e)

        t = threading.Thread(target=churn, daemon=True)
        t.start()
        try:
            end = time.monotonic() + 0.4
            while time.monotonic() < end:
                b.step()
                b.poke(b.pick("motor", k=2, seed=7), 0.9)
                vals = list(b.activity)
                self.assertEqual(len(vals), b.size, "activity потерял узлы")
                self.assertTrue(all(0.0 <= v <= 1.0 for v in vals), "активность вне 0..1")
        finally:
            stop.set()
            t.join(timeout=3)
        self.assertEqual(errors, [])


class TestCancelRunningMission(unittest.TestCase):
    """Находка 2: итог затирал отмену, если миссия уже выполнялась."""

    def test_control_running_mission_still_gets_its_result(self):
        with temp_live():
            rec = missions.add("note", {"text": "x"})
            missions.take_next()
            out = missions.mark_running(rec["id"], "done", "итог")
            self.assertIsNotNone(out, "контроль: обычный путь обязан записать итог")
            self.assertEqual(missions.status_of(rec["id"]), "done")

    def test_refused_is_never_overwritten(self):
        with temp_live():
            rec = missions.add("note", {"text": "x"})
            missions.take_next()
            missions.mark(rec["id"], "refused", "снята оператором")
            self.assertIsNone(missions.mark_running(rec["id"], "done", "итог"))
            self.assertEqual(missions.status_of(rec["id"]), "refused")

    def test_agent_keeps_the_cancel_and_says_so(self):
        with temp_live():
            syn = quiet_synapse()
            rec = missions.add("note", {"text": "заметка"})
            missions.take_next()
            missions.mark(rec["id"], "refused", "снята оператором")
            status, text = syn.handle({"id": rec["id"], "kind": "note",
                                       "params": {"text": "заметка"}})
            self.assertEqual(status, "refused")
            self.assertEqual(missions.status_of(rec["id"]), "refused")
            was = i18n.lang()
            try:
                i18n.set_lang("en")
                _, en_text = syn.handle({"id": rec["id"], "kind": "note",
                                         "params": {"text": "заметка"}})
                self.assertIn("cancelled", en_text)
                i18n.set_lang("ru")
                _, ru_text = syn.handle({"id": rec["id"], "kind": "note",
                                         "params": {"text": "заметка"}})
                self.assertIn("снята", ru_text)
            finally:
                i18n.set_lang(was)

    def test_drop_mission_really_deletes(self):
        """Кнопка «Удалить» — удаляет, а идущую работу ещё и ОСТАНАВЛИВАЕТ.

        Здесь стояло прежнее поведение (статус refused, запись остаётся в очереди),
        и это была правда до 26.09.2026. Слово Стаса: «останавливать и удалять нужно —
        если на кнопке написано УДАЛИТЬ!» — поэтому проверка переписана осознанно,
        а не подогнана: она закрепляет НОВЫЙ договор.
        """
        from synapse import cancel
        from synapse.panel_core import PanelCore
        with temp_live():
            core = PanelCore()
            queued = missions.add("note", {"text": "x"})
            res = core.drop_mission(queued["id"])
            self.assertTrue(res["ok"])
            self.assertFalse(res["running"])
            self.assertIsNone(missions.status_of(queued["id"]), "запись осталась в очереди")

            running = missions.add("note", {"text": "y"})
            missions.take_next()
            res2 = core.drop_mission(running["id"])
            self.assertTrue(res2["ok"])
            self.assertTrue(res2["stopped"], "идущую работу обязаны остановить")
            self.assertTrue(cancel.requested(running["id"]), "сигнал остановки не поставлен")
            self.assertIsNone(missions.status_of(running["id"]), "запись осталась в очереди")
            cancel.clear(running["id"])

    def test_mission_ids_do_not_repeat(self):
        """Старое «len(items) + 1» давало ДВЕ записи с одним id в одном файле.

        Так и было: пока в очереди лежали A и B, счётчик шёл на -3; стоило убрать
        A, как следующая миссия в ту же секунду получала id записи B — и mark()
        по id попадал в первую попавшуюся.
        """
        with temp_live():
            first = missions.add("note", {"text": "1"})
            missions.add("note", {"text": "2"})
            items = [m for m in missions.all_missions() if m["id"] != first["id"]]
            missions._write(items)               # убрали ПЕРВУЮ, хвост остался
            missions.add("note", {"text": "3"})
            ids = [m["id"] for m in missions.all_missions()]
            self.assertEqual(len(ids), len(set(ids)), f"id повторился: {ids}")

    def test_mission_id_avoids_one_already_used_by_a_report(self):
        """id не повторяет тот, по которому уже лежит файл отчёта (панель ищет по id)."""
        with temp_live() as tmp:
            saved_data = config.DATA
            try:
                data = Path(tmp) / "livedata"
                (data / "reports").mkdir(parents=True)
                config.DATA = data
                busy = "m-20200101-000000-1"
                (data / "reports" / f"20200101-000000-{busy}.json").write_text("{}",
                                                                             encoding="utf-8")
                self.assertIn(busy, missions._ids_used_in_reports())
                rec = missions.add("note", {"text": "x"})
                self.assertNotEqual(rec["id"], busy)
            finally:
                config.DATA = saved_data


class TestWindowCloseStopsBrain(unittest.TestCase):
    """Находка 3: закрытие окна оставляло свой llama-server сиротой."""

    class _Brain:
        def __init__(self):
            self.calls = []

        def mode(self):
            return "server"

        def stop_server(self):
            self.calls.append("stop")

    def test_control_stop_server_is_really_called(self):
        """КОНТРОЛЬ: подставной мозг обязан увидеть stop_server — иначе тест слепой.

        И песочница обязательна: в первый заход контроль писал panel.close в ЖИВОЙ
        журнал оператора (6 строк в двух журналах за три прогона) — ровно та же
        болезнь, что «такого_нет» в очереди. Нашла сверкой sha256 до и после.
        """
        with temp_live():
            stub = SimpleNamespace(core=SimpleNamespace(brain=self._Brain()))
            self.assertEqual(stub.core.brain.mode(), "server")
            result = (importlib.import_module("synapse.qt_panel")
                      .SynapseWindow.stop_own_brain(stub))
            self.assertEqual(result, "stopped")
            self.assertIn("stop", stub.core.brain.calls)

    def test_both_panels_have_the_close_hook(self):
        with temp_live():
            for mod_name, cls_name, engine in (("synapse.qt_panel", "SynapseWindow", "qt-ogl"),
                                               ("synapse.panel", "Panel", "tk")):
                mod = importlib.import_module(mod_name)
                cls = getattr(mod, cls_name)
                self.assertTrue(hasattr(cls, "stop_own_brain"), f"{engine}: нет хука закрытия")
                stub = SimpleNamespace(core=SimpleNamespace(brain=self._Brain()))
                self.assertEqual(cls.stop_own_brain(stub), "stopped")
                self.assertIn("stop", stub.core.brain.calls)

    def test_tk_on_close_stops_the_server(self):
        """У tkinter-панели условие было ПЕРЕВЁРНУТО — проверяем по коду и хуком."""
        src = (ROOT / "synapse" / "panel.py").read_text(encoding="utf-8")
        self.assertIn("def on_close", src)
        self.assertIn("self.stop_own_brain()", src)
        self.assertNotIn('if self.core.brain.mode() != "server"', src)


class TestBodyLawNumbers(unittest.TestCase):
    """Находка 6: числа в README не воспроизводились, а sustain работает не как тормоз."""

    TEXT = "alpha"

    def test_control_the_body_actually_dies_at_the_working_point(self):
        """КОНТРОЛЬ: рабочая пара обязана гаснуть — иначе остальные числа бессмысленны."""
        peak, gone = measure(0.70, 0.30, 3, self.TEXT)
        self.assertIsNotNone(gone, "рабочий закон не гаснет — это уже не рабочий закон")
        self.assertGreater(peak, 0)

    def test_working_point_numbers_are_reproducible(self):
        """Числа рабочего закона — ДВОЙНЫЕ, и контроль идёт первым.

        enabled=False обязан совпасть с прежним законом ТОЧНО (иначе физиология
        «переписала историю»), а включённая физиология даёт свои числа: торможение
        GABA срезает и пик, и хвост волны.
        """
        with nt_mode(enabled=False):
            self.assertEqual(measure(0.70, 0.30, 3, self.TEXT), (16, 29))
            self.assertEqual(measure(0.70, 0.30, 8, self.TEXT), (46, 48))
        with nt_mode(enabled=True):
            self.assertEqual(measure(0.70, 0.30, 3, self.TEXT), (16, 28))
            self.assertEqual(measure(0.70, 0.30, 8, self.TEXT), (45, 45))

    def test_runaway_point_never_dies(self):
        for enabled, second in ((False, (208, None)), (True, (198, None))):
            with nt_mode(enabled=enabled):
                self.assertEqual(measure(0.75, 0.35, 3, self.TEXT), (19, None))
                self.assertEqual(measure(0.72, 0.55, 3, self.TEXT), second)

    def test_sustain_prolongs_the_wave_instead_of_damping_it(self):
        """Честный факт из замера: sustain УВЕЛИЧИВАЕТ сохранение (eff), а не тормозит.

        Значит «тонус торможения» — неверное имя, и настоящая страховка это decay и
        порог. Поведение не меняла: это решение Стаса. Тест держит факт, чтобы
        README не стал снова рассказывать про «страховку».
        """
        with nt_mode(enabled=False):
            self.assertEqual(measure(0.70, 0.30, 8, self.TEXT, sustain=0.0), (45, 43))
            self.assertEqual(measure(0.70, 0.30, 8, self.TEXT, sustain=4.0), (219, None))
        with nt_mode(enabled=True):
            self.assertEqual(measure(0.70, 0.30, 8, self.TEXT, sustain=0.0), (44, 40))
            self.assertEqual(measure(0.70, 0.30, 8, self.TEXT, sustain=4.0), (206, None))

    def test_big_stimulus_breaks_even_the_working_law(self):
        """Порог честности: «рабочее» держится не при любом входе, а до k~12."""
        self.assertIsNotNone(measure(0.70, 0.30, 6, self.TEXT)[1], "потолок окна (k=6)")
        self.assertIsNone(measure(0.70, 0.30, 30, self.TEXT)[1], "при k=30 срывается и рабочее")


class TestVersionAndDocs(unittest.TestCase):
    """Находка 4: __version__ застрял на 0.6.0 при живой 0.10.3."""

    def test_version_matches_changelog(self):
        import synapse
        head = "\n".join((ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()[:6])
        m = re.search(r"\(v(\d+\.\d+\.\d+)\)", head)
        self.assertIsNotNone(m, f"в заголовке чейнджлога нет версии: {head!r}")
        self.assertEqual(synapse.__version__, m.group(1),
                         "версия в коде и в чейнджлоге разошлись — так и родился 0.6.0")

    def test_readme_names_sustain_honestly(self):
        rd = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("sustain", rd)                        # новое честное имя
        self.assertIn("САМОПОДДЕРЖКА", rd)                  # и русское объяснение
        self.assertNotIn("\"brake\":", rd)                  # старого ключа в примере нет


class TestI18nHasNoOrphans(unittest.TestCase):
    """Находка 7: 10 ключей лежали в словаре и никем не звались — тест смотрит ОБРАТНО."""

    KEY_RE = re.compile(r"[\"'`]([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)[\"'`]")

    @classmethod
    def _used_keys(cls) -> set:
        """Ключи, которые в коде НАЗВАНЫ строкой.

        Словарь (synapse/i18n.py) из скана исключён: там лежат все ключи, и по нему
        проверка ищет СИРОТ — иначе она находила бы всё и не находила ничего.
        Литерал, а не вызов t(): в панелях часть ключей выбирается в ветке
        (`t("tk.engine.picture" if ... else "tk.engine.canvas")`) или кладётся в
        `_say_key("act.live")` — по вызову такие не видны, а мёртвыми не являются.
        """
        used = set()
        files = [p for p in (ROOT / "synapse").rglob("*.py")
                 if p.name != "i18n.py"] + [ROOT / "run.py"]
        blob = ""
        for p in files:
            src = p.read_text(encoding="utf-8")
            blob += src
            used.update(cls.KEY_RE.findall(src))
        # Ключи БЕЗ точки (их в словаре единицы) ищем просто как строку: у них нет
        # формы a.b, и шаблон выше их не поймает, а мёртвыми они не являются.
        used.update(k for k in i18n.CATALOG if "." not in k and f'"{k}"' in blob)
        return used

    @staticmethod
    def _built_keys() -> set:
        """Ключи, которые проект собирает из частей (f-строка) — их скан не видит."""
        built = set()
        for k in missions.KINDS:
            built.add(f"tool.{k}.label")
            built.add(f"tool.{k}.hint")
        for p in reports.KNOWN_PORTS:
            built.add(f"svc.{p}")
            built.add(f"mean.{p}")        # t(f'mean.{port}') в отчёте
        for s in ("writable", "writable_unverified", "read_only", "denied", "missing",
                  "refused", "off"):
            built.add(f"rep.wp.state.{s}")
        for c in ("role", "nt", "zone"):
            built.add(f"coloring.{c}")
        # ОБЛАЧНЫЙ МОЗГ (v0.30.0): и режим, и причина отказа собираются из кода —
        # `t(f"set.cloud.{mode}")` и `t(f"cloud.why.{why}")`. Списки лежат в
        # synapse/online.py (MODES, WHY_CODES), чтобы ключи строились ЗДЕСЬ из них,
        # а не перечислялись вторым списком, который разъедется с первым.
        for m in online.MODES:
            built.add(f"set.cloud.{m}")
        for w in online.WHY_CODES:
            built.add(f"cloud.why.{w}")
        for th in theme.available():
            built.add(f"set.theme.{th}")
        built.update(i18n.ROLE_KEYS.values())
        built.update(i18n.ZONE_KEYS.values())
        built.update(scope.REASON_CODES)
        return built

    def test_control_the_scan_sees_real_keys(self):
        """КОНТРОЛЬ: скан обязан видеть живые ключи и НЕ видеть мёртвый.

        Иначе «сирот нет» ничего не значит: сломанный скан находил бы пустоту.
        """
        used = self._used_keys()
        self.assertGreater(len(used), 150, "скан словаря сломан")
        self.assertIn("btn.live", used)
        self.assertIn("act.pulse", used)
        self.assertNotIn("set.saved.nothing", used)

    def test_no_orphan_keys(self):
        used = self._used_keys() | self._built_keys()
        orphans = sorted(k for k in i18n.CATALOG if k not in used)
        self.assertEqual(orphans, [], f"ключи в словаре, но никем не званы: {orphans}")

    def test_door_reasons_all_have_words(self):
        missing = [c for c in scope.REASON_CODES if not i18n.has(c)]
        self.assertEqual(missing, [], f"дверь отказала бы ключом: {missing}")

    def test_write_probe_states_all_have_words(self):
        """Состояние writable_unverified рождается в коде — у него должна быть строка."""
        for s in ("writable", "writable_unverified", "read_only", "denied", "missing",
                  "refused", "off"):
            self.assertTrue(i18n.has(f"rep.wp.state.{s}"), s)


class TestMinorFixes(unittest.TestCase):
    """Находка 10: мелочи, найденные чтением (дубль в регулярке, статусы двух панелей)."""

    def test_net_scan_regex_has_no_duplicate(self):
        src = (ROOT / "synapse" / "tools" / "net_scan.py").read_text(encoding="utf-8")
        self.assertNotIn("Disk|Disk", src, "в регулярке остался дубль")
        self.assertIn("Диск|Disk", src)

    def test_panels_show_the_same_status_word(self):
        core_src = (ROOT / "synapse" / "panel_core.py").read_text(encoding="utf-8")
        qt_src = (ROOT / "synapse" / "qt_panel.py").read_text(encoding="utf-8")
        self.assertNotIn("STATUS_EN", core_src)
        self.assertNotIn("status_en", qt_src)
        self.assertIn("missions.STATUSES", core_src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
