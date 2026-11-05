"""v0.15.0: вкладка настроек читает ФАЙЛ; существо живёт, а не лежит в idle.

Слово Стаса 25.09.2026, две жалобы — и у них ОДНА причина:

  (1) «он теперь просто ничего не делает опять — постоянно idle, смотри сам логи»;
  (2) «язык в окне открывается по умолчанию тот, что сейчас стоит в программе…
      я нажимаю сохранить с русского интерфейса и не замечаю, что там английский
      подставляется, как открываешь вкладку настроек».

Доказательство из живого audit.jsonl (16:28:56): settings.saved changed=2, и среди
изменённых — язык en (при русском окне!) и idle_thinking: false. Панель заполняла
поля вкладки ТОЛЬКО кнопкой «Перечитать» и после сохранения, а при старте в полях
стояли УМОЛЧАНИЯ (язык en, «думать вслух» выключено). Оператор жал «Сохранить» —
и окно записывало умолчания как его выбор. Так существо и лишилось думания.

Здесь проверяется и лечение, и его отсутствие было бы видно:

  * КОНТРОЛЬ: файл настроек и умолчания РАЗНЫЕ — иначе «поля совпали с файлом»
    ничего не доказывает;
  * окно показывает ФАЙЛ при рождении (язык, «думать вслух», такт, темп мыслей);
  * нажатие «Сохранить» БЕЗ правок ничего не меняет (сценарий жалобы);
  * открытие вкладки настроек перечитывает файл;
  * рефлекс и мысль разрежены ПО СЕКУНДАМ, а не по тактам (такт стал непрерывным),
    и журнал больше не плоская линия «status=idle».

Запуск: python tests/test_settings_live.py   (или pytest tests)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")     # окно без экрана

from synapse import audit, config, i18n, missions, propose, theme   # noqa: E402
from synapse.agent import Synapse                               # noqa: E402
from synapse.body import Body                                   # noqa: E402
from synapse.panel_core import PanelCore                        # noqa: E402
from live_data import temp_live                                 # noqa: E402


class StubBrain:
    """Мозг-заглушка: тест не имеет права поднимать llama-server."""

    def __init__(self, answer="мне спокойно"):
        self.answer = answer
        self.calls = 0

    def mode(self):
        return "off"

    def available(self, timeout=3.0, ttl=2.0):
        return True

    def think(self, prompt, system="", history=None):
        self.calls += 1
        return self.answer


class TempConfig(unittest.TestCase):
    """Свой config.json в temp + журналы в temp: живые data/ не трогаем."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "config.json"
        self._keep = config.CONFIG_PATH
        config.CONFIG_PATH = self.path
        config.forget_cache()
        self._live = temp_live()
        self._live.__enter__()
        # Настройки «как у Стаса»: русское окно, существо думает, такт непрерывный,
        # а ДИАПАЗОН ПОРТОВ не совпадает с умолчаниями (контроль).
        config.save({"ui": {"lang": "ru", "theme": "dark"},
                     "tick_seconds": 0, "idle_thinking": True,
                     "net": {"port_from": 500, "port_to": 600}})
        config.forget_cache()

    def syn(self, brain, **cfg):
        """Организм с заглушкой мозга: тело настоящее, журналы — в песочнице."""
        base = {"idle_thinking": True, "instinct_min_seconds": 3600}
        base.update(cfg)
        return Synapse(cfg=base, body=Body.load(), brain=brain)

    def tearDown(self):
        self._live.__exit__(None, None, None)
        config.CONFIG_PATH = self._keep
        config.forget_cache()
        i18n.set_lang("en")
        theme.set_theme("dark")
        self._tmp.cleanup()


class TestControlFileIsNotDefaults(TempConfig):
    """КОНТРОЛЬ первым: файл и умолчания обязаны РАЗЛИЧАТЬСЯ."""

    def test_file_differs_from_defaults(self):
        cfg = config.load()
        self.assertEqual(cfg["ui"]["lang"], "ru")
        self.assertTrue(cfg["idle_thinking"])
        self.assertEqual(float(cfg["tick_seconds"]), 0.0)
        self.assertNotEqual(cfg["ui"]["lang"], config.DEFAULTS["ui"]["lang"],
                            "язык совпал с умолчанием — тест не различит ошибку")
        self.assertNotEqual((cfg["net"]["port_from"], cfg["net"]["port_to"]),
                            (config.DEFAULTS["net"]["port_from"],
                             config.DEFAULTS["net"]["port_to"]),
                            "диапазон портов совпал с умолчанием — тест слепой")

    def test_old_rate_keys_are_retired(self):
        """ОДИН ТЕМП — ТАКТ: и «раз в N тактов», и «пауза до мысли» уходят на чтении."""
        self.path.write_text(json.dumps({"idle_think_every": 5,
                                         "idle_think_seconds": 7,
                                         "net": {"ports": [22, 445]}}), encoding="utf-8")
        config.forget_cache()
        cfg = config.load()
        self.assertNotIn("idle_think_every", cfg)
        self.assertNotIn("idle_think_seconds", cfg)
        self.assertNotIn("ports", cfg["net"],
                         "список портов выведен: он не выражал «все порты»")
        self.assertIn("tick_seconds", cfg)


class TestWindowReadsTheFile(TempConfig):
    """Окно показывает ФАЙЛ, а не умолчания — это и была жалоба."""

    def setUp(self):
        super().setUp()
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as exc:                       # нет Qt — честный пропуск
            self.skipTest(f"PySide6 недоступен: {type(exc).__name__}")
        from synapse.qt_panel import SynapseWindow
        self.app = QApplication.instance() or QApplication([])
        self.win = SynapseWindow()

    def test_language_and_life_are_shown_at_start(self):
        self.assertEqual(self.win.cmb_lang.currentData(), "ru",
                         "в окне не тот язык, что в файле настроек")
        self.assertTrue(self.win.chk_idle.isChecked(),
                        "«думать вслух» показано выключенным, хотя в файле включено")
        self.assertEqual(int(self.win.sp_port_from.value()), 500)
        self.assertEqual(int(self.win.sp_port_to.value()), 600)
        self.assertEqual(float(self.win.ed_tick.text()), 0.0)

    def test_save_without_touching_changes_nothing(self):
        """Сценарий жалобы: нажал «Сохранить», ничего не трогая."""
        self.win._save_settings()
        disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(disk["ui"]["lang"], "ru", "язык переписан на английский")
        self.assertTrue(disk["idle_thinking"], "«Сохранить» выключило думание")
        self.assertEqual(float(disk["tick_seconds"]), 0.0)

    def test_opening_the_tab_reloads_from_file(self):
        config.save({"ui": {"lang": "en"}})
        config.forget_cache()
        self.win.book.setCurrentIndex(self.win._settings_tab)
        self.assertEqual(self.win.cmb_lang.currentData(), "en",
                         "вкладка настроек не перечитала файл")


class TestTheCreatureIsNotAVegetable(TempConfig):
    """Оно живёт: рефлекс разрежен по секундам, мысль идёт каждый такт, журнал не плоский."""

    def test_control_reflex_fires_once_not_every_tick(self):
        """КОНТРОЛЬ: рефлекс срабатывает — но не метрономом."""
        syn = self.syn(StubBrain())
        first = syn.instinct()
        self.assertTrue(first, "голодное тело не получило импульса")
        self.assertEqual(syn.instinct(), "",
                         "рефлекс срабатывает на каждом такте — это метроном")

    def test_thought_goes_on_every_tick(self):
        """ОДИН ТЕМП — ТАКТ (v0.22.0): мысль разрежает только сам такт.

        Слово Стаса 26.09.2026: «время такта и есть количество мыслей». Мозг спрашивают
        на каждом такте, а повтор (модель зациклилась) СКАЗАН и помечен — не спрятан.
        """
        brain = StubBrain()
        syn = self.syn(brain)
        syn.body.stimulus("проснулась", amp=1.0, k=6)     # тело живое: голод не мешает
        self.assertEqual(syn.idle_thought(), "мне спокойно")
        self.assertEqual(brain.calls, 1)
        second = syn.idle_thought()
        self.assertEqual(brain.calls, 2, "мысль пропущена: темп потерян")
        self.assertIn(i18n.t("ag.repeat", text="мне спокойно", streak=1)[:8], second,
                      "повтор не сказан, а спрятан")

    def test_journal_is_not_a_flatline(self):
        """Журнал показывает ЖИЗНЬ, а не пять строк «status=idle» подряд.

        Существо на свободных тактах делает дело (рефлекс, мысль), поэтому строки
        есть — но они РАЗНЫЕ и с делом. Плоская линия — это когда все пять строк
        «status=idle»: именно её оператор и читал, отсюда «он овощ».
        """
        syn = self.syn(StubBrain())
        for _ in range(5):
            syn.tick()
        events = audit.tail(80)
        idle = [r for r in events
                if r["event"] == "tick" and r.get("status") == "idle"]
        self.assertLessEqual(len(idle), 1,
                             f"«status=idle» повторяется {len(idle)} раз на 5 тактов")
        statuses = {r.get("status") for r in events if r["event"] == "tick"}
        self.assertTrue(statuses & {"fed", "thinking"},
                        f"на свободных тактах в журнале нет дела: {statuses}")

    def test_mission_tick_is_always_written(self):
        """КОНТРОЛЬ к предыдущему: случилось дело — строка в журнале есть всегда."""
        missions.add("note", {"text": "проба"})
        syn = self.syn(StubBrain())
        syn.tick()
        ticks = [r for r in audit.tail(80) if r["event"] == "tick"]
        self.assertEqual(len(ticks), 1)
        self.assertIsNotNone(ticks[0]["mission"])


class TestQuietBrainDoesNotStallTheTick(TempConfig):
    """Молчащий мозг не имеет права тормозить такт (найдено ЖИВЫМ замером).

    v0.15.0. Пока «думать вслух» было выключено, такт не спрашивал мозг вовсе. С
    включённым думанием `available()` вызывался на КАЖДОМ такте, а при молчащем
    сервере он стоит до 2 секунд (замер самой функции: 2112 мс). Живой прогон на
    настройках Стаса дал 2 такта за 3 секунды вместо сотен — существо вставало
    колом ровно тогда, когда модели рядом не было.
    """

    class SlowBrain(StubBrain):
        """Заглушка, отвечающая медленно — как молчащий сервер."""

        def __init__(self, delay=0.2):
            super().__init__()
            self.delay = delay
            self.probes = 0

        def available(self, timeout=3.0, ttl=2.0):
            self.probes += 1
            time.sleep(self.delay)
            return False

    def test_control_probe_really_is_slow(self):
        """КОНТРОЛЬ: заглушка ДЕЙСТВИТЕЛЬНО медленная, иначе тест слепой."""
        brain = self.SlowBrain()
        t0 = time.perf_counter()
        brain.available()
        self.assertGreaterEqual(time.perf_counter() - t0, 0.2)

    def test_the_tick_asks_the_brain_each_time_now(self):
        """ОДИН ТЕМП — ТАКТ (v0.22.0): разрежения больше нет, мысль идёт каждый такт.

        Заглушка своей памяти (probe_ttl) не держит и честно отвечает «нет» на каждый
        вопрос — значит проба идёт на каждом такте. Память пробы живёт в НАСТОЯЩЕМ Brain,
        и её проверяет tests/test_stream.py::TestDeafBrainDoesNotStallTheStream.
        """
        brain = self.SlowBrain()
        syn = self.syn(brain)
        syn.body.stimulus("проснулась", amp=1.0, k=6)   # тело живое: рефлекс не мешает
        for _ in range(3):
            syn.tick()
        self.assertEqual(brain.probes, 3,
                         f"мозг спрошен {brain.probes} раз за 3 такта — темп потерян")

    def test_control_with_thinking_off_the_brain_is_not_asked(self):
        """КОНТРОЛЬ: выключили думание — вопроса нет вовсе. Иначе верх слепой.

        Опираться на «мёртвое тело» нельзя: такт сам делает вдох (tick_breath_seconds),
        тело приходит в себя, и рефлекс не срабатывает — проверка была бы про вдох,
        а не про темп мыслей.
        """
        brain = self.SlowBrain()
        syn = self.syn(brain, idle_thinking=False)
        syn.body.stimulus("проснулась", amp=1.0, k=6)
        syn.tick()
        self.assertEqual(brain.probes, 0, "мозг спрошен при выключенном думании")


class TestEnsureIsThrottled(TempConfig):
    """ensure() не щупает сервер на каждом такте (v0.15.0, тот же класс тормоза).

    Проба молчащего сервера стоит до 2 секунд, а «мозг включён — сервер обязан
    быть жив» проверялось на КАЖДОМ такте. Теперь сначала разрежение (retry_after),
    потом проба.
    """

    def _brain(self):
        from synapse.brain import Brain
        b = Brain({"brain": {"mode": "server", "server_port": 9,
                             "local_dir": "нет-такого-каталога"}})
        b.probes = 0

        def fake_available(timeout=3.0, ttl=2.0):
            b.probes += 1
            return False

        b.available = fake_available
        b.start_server = lambda **kw: False          # сервер тест НЕ поднимает
        return b

    def test_control_first_call_checks(self):
        """КОНТРОЛЬ: первая попытка обязана спросить сервер."""
        b = self._brain()
        self.assertFalse(b.ensure())
        self.assertEqual(b.probes, 1)

    def test_repeated_calls_are_throttled(self):
        b = self._brain()
        b.ensure()
        for _ in range(5):
            b.ensure()
        self.assertEqual(b.probes, 1, f"сервер щупается на каждом такте: {b.probes} проб")


class TestGalkasReachTheOrganism(TempConfig):
    """v0.31.0: галки про мозг и запас применяются БЕЗ перезапуска.

    Живая жалоба вечера 30.09.2026 («ничего не происходит, бред какой то»): в
    21:42:45 оператор снял галку «подтверждение действий» (`settings.saved
    changed=1`, в файле `brain.propose_confirm: false`), а в 21:43:48 и 21:44:18
    мозг снова положил работу статусом `proposed` — она ждала кнопки, хотя
    галка была снята. Причина: существо держало СЛЕПОК настроек с момента
    запуска, а панель обновляла только мозг.
    """

    def syn(self, **brain) -> Synapse:                    # noqa: D102
        """Организм с заглушкой мозга и галками про предложения (по умолчанию — обе вкл.)."""
        base = {"idle_thinking": True, "instinct_min_seconds": 3600,
                "brain": dict({"propose": True, "propose_confirm": True}, **brain)}
        return Synapse(cfg=base, body=Body.load(), brain=StubBrain())

    def _offer(self, syn: Synapse, kind: str = "peers") -> dict:
        """Предложение из мозга — тем же путём, что и в жизни (propose.offer)."""
        params = {} if kind == "peers" else {"cidr": "127.0.0.0/29"}
        rec, code = propose.offer({"kind": kind, "params": params}, syn.cfg)
        self.assertIsNotNone(rec, f"предложение не приняли: {code}")
        return rec

    def test_control_before_reload_the_offer_waits_for_the_button(self):
        """КОНТРОЛЬ: со слепком при рождении поведение прежнее — иначе тест слепой."""
        self.assertEqual(self._offer(self.syn())["status"], missions.PROPOSED)

    def test_a_galka_works_right_after_reload(self):
        """Снял галку — organism берёт предложенное сразу, без перезапуска окна."""
        syn = self.syn()
        cfg = config.load()
        cfg["brain"] = dict(cfg.get("brain") or {}, propose=True, propose_confirm=False)
        syn.reload(cfg)
        self.assertEqual(self._offer(syn, kind="net_audit")["status"], "queued",
                         "снятая галка не дошла до организма")

    def test_the_panel_pushes_settings_into_the_organism(self):
        """Дверь настроек одна: сохранил в окне — существо перечитало само."""
        syn = self.syn()
        core = PanelCore.__new__(PanelCore)
        core.cfg = config.load()
        core.syn = syn
        res = core.apply_settings({"brain_propose_confirm": False})
        self.assertTrue(res["changed"], "окно не сказало, что поменяло")
        self.assertFalse(syn.cfg["brain"]["propose_confirm"],
                         "организм остался со слепком настроек")
        self.assertEqual(self._offer(syn, kind="net_audit")["status"], "queued")

    def test_the_life_switch_is_read_by_the_organism_too(self):
        """Рубильник запаса — тот же путь: выключил в окне, существом прочитано."""
        syn = self.syn()
        core = PanelCore.__new__(PanelCore)
        core.cfg = config.load()
        core.syn = syn
        cfg = config.load()
        cfg["life"] = dict(cfg.get("life") or {}, enabled=False)
        syn.reload(cfg)
        self.assertFalse(syn.life._sec("enabled"), "запас остался включённым по слепку")


if __name__ == "__main__":
    unittest.main(verbosity=2)
