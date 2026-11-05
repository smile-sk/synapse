"""v0.16.0: ПОТОК СОЗНАНИЯ — у мыслей нет пауз по умолчанию.

Слово Стаса 25.09.2026: «не понял опять. откуда раз в 30с? я же сказал поток
сознания причём тут 30с?»

Так и было: тридцать секунд — МОЯ выдумка. Я поставила разрежение, чтобы существо
не жгло видеокарту, а просили ровно обратного: «просто бытие потока, не выключаться
на тик». Разрежение и есть шов, только измеренный в секундах вместо тактов.

Что проверяется — КОНТРОЛЬ первым, как везде в проекте:

  * КОНТРОЛЬ: ненулевая пауза ДЕЙСТВИТЕЛЬНО разрежает мысли. Без этого «0 = поток»
    ничего не доказывает: тест прошёл бы и на сломанном механизме;
  * умолчание равно нулю — поток это состояние ПО УМОЛЧАНИЮ, а не то, что надо
    выпрашивать у настроек;
  * при нуле модель спрашивается КАЖДЫЙ такт: мысль идёт за мыслью;
  * настройки ПРИНИМАЮТ ноль (раньше снизу стоял зажим 1 с), и поле окна его
    показывает словом «поток — без пауз», а не цифрой;
  * молчащий мозг не превращает поток в стояние колом: у пробы готовности есть
    память (brain.probe_ttl), и агент её спрашивает.

Запуск: pytest tests   (не pythonw.exe — там нет консольных дескрипторов)
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
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")      # окно без экрана

from synapse import audit, config, i18n                      # noqa: E402
from synapse.agent import Synapse                             # noqa: E402
from synapse.body import Body                                 # noqa: E402
from synapse.panel_core import PanelCore                      # noqa: E402
from live_data import temp_live                               # noqa: E402


class StubBrain:
    """Мозг-заглушка: считает, сколько раз его спросили, и с каким ttl."""

    def __init__(self, answer="живая мысль", ready=True):
        self.answer = answer
        self.calls = 0
        self.probes = 0
        self.ttls: list[float] = []
        self.ready = ready

    def mode(self):
        return "off"                       # такт не зовёт ensure()

    def available(self, timeout=3.0, ttl=2.0):
        self.probes += 1
        self.ttls.append(float(ttl))
        return self.ready

    def think(self, prompt, system="", history=None):
        self.calls += 1
        return self.answer


def _syn(br, **cfg):
    """Организм с заглушкой мозга. Тело ЖИВОЕ: иначе первым сработает инстинкт.

    Первый параметр назван br, а не brain: иначе вызов _syn(b, brain={...}) падает
    с «multiple values for argument» — на этом и упали два теста в первом прогоне.
    """
    base = {"idle_thinking": True,
            "instinct_min_seconds": 3600, "tick_log_seconds": 3600}
    base.update(cfg)
    s = Synapse(cfg=base, body=Body.load(), brain=br)
    s.body.stimulus("проснулась", amp=1.0, k=6)
    return s


class TestStreamIsTheDefault(unittest.TestCase):
    def test_the_rate_is_the_tick_alone(self):
        """ОДИН ТЕМП — ТАКТ (v0.22.0, слово Стаса: «время такта 0 — поток. всё»)."""
        self.assertNotIn("idle_think_seconds", config.DEFAULTS)
        src = (Path(__file__).resolve().parents[1] / "synapse" / "agent.py").read_text(encoding="utf-8")
        code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
        self.assertNotIn("idle_think_seconds", code,
                         "мысль всё ещё разрежена отдельным ключом")

    def test_zero_asks_every_tick(self):
        """0 = поток: мысль идёт за мыслью, пауз между ними нет."""
        brain = StubBrain()
        syn = _syn(brain)
        with temp_live():
            for _ in range(3):
                syn.tick()
        self.assertEqual(brain.calls, 3,
                         f"поток не сплошной: мозг спрошен {brain.calls} раз за 3 такта")


class TestSettingsAcceptZero(unittest.TestCase):
    """Ноль обязан ДОЖИВАТЬ до файла: раньше его зажимали снизу в одну секунду."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "config.json"
        self._keep = config.CONFIG_PATH
        config.CONFIG_PATH = self.path
        config.forget_cache()
        self._live = temp_live()
        self._live.__enter__()

    def tearDown(self):
        self._live.__exit__(None, None, None)
        config.CONFIG_PATH = self._keep
        config.forget_cache()
        i18n.set_lang("en")
        self._tmp.cleanup()

    def _core(self):
        core = PanelCore.__new__(PanelCore)
        core.cfg = config.load()
        return core

    def test_tick_zero_is_kept_not_clamped(self):
        """Ноль обязан ДОЖИВАТЬ до файла: раньше его зажимали снизу в одну секунду."""
        core = self._core()
        res = core.apply_settings({"tick_seconds": "0"})
        self.assertTrue(res["ok"], res)
        self.assertEqual(float(res["settings"]["tick_seconds"]), 0.0,
                         "ноль зажат в единицу: поток нельзя поставить в окне")
        disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(float(disk["tick_seconds"]), 0.0)

    def test_control_a_number_is_kept_too(self):
        """КОНТРОЛЬ: число тоже принимается — иначе проверка выше слепая."""
        core = self._core()
        res = core.apply_settings({"tick_seconds": "45"})
        self.assertEqual(float(res["settings"]["tick_seconds"]), 45.0)


class TestDeafBrainDoesNotStallTheStream(unittest.TestCase):
    """Молчащий сервер не имеет права превращать поток в стояние колом."""

    class SlowDeafBrain(StubBrain):
        def __init__(self, delay=0.15):
            super().__init__(ready=False)
            self.delay = delay

        def available(self, timeout=3.0, ttl=2.0):
            self.probes += 1
            self.ttls.append(float(ttl))
            time.sleep(self.delay)
            return False

    def test_control_probe_is_really_slow(self):
        """КОНТРОЛЬ: проба ДЕЙСТВИТЕЛЬНО медленная, иначе замер ничего не значит."""
        brain = self.SlowDeafBrain()
        t0 = time.perf_counter()
        brain.available()
        self.assertGreaterEqual(time.perf_counter() - t0, 0.15)

    def test_agent_asks_with_memory(self):
        """Агент спрашивает готовность С ПАМЯТЬЮ — иначе проба идёт каждый такт."""
        brain = self.SlowDeafBrain()
        syn = _syn(brain, brain={"probe_ttl": 33})
        with temp_live():
            syn.tick()
        self.assertEqual(brain.ttls, [33.0],
                         f"агент спросил готовность с ttl={brain.ttls} — без памяти поток встанет")

    def _real_deaf_brain(self, delay=0.15):
        """НАСТОЯЩИЙ мозг с медленной пробой: память живёт в нём, а не в заглушке.

        Заглушка ttl игнорирует — мерить ею память нельзя (на этом мой первый
        заход и упал: 3 пробы при любом ttl). Поэтому здесь Brain, у которого
        подменена только проба; сервер тест не поднимает.
        """
        from synapse.brain import Brain
        b = Brain({"brain": {"mode": "server", "server_port": 9,
                             "endpoint": "http://127.0.0.1:9/v1/chat/completions"}})
        b.probes = 0

        def probe(timeout=3.0):
            b.probes += 1
            time.sleep(delay)
            return False

        b._probe = probe
        b.ensure = lambda on_note=None, retry_after=300.0: False
        return b

    def test_control_without_memory_probes_every_tick(self):
        """КОНТРОЛЬ: с почти нулевой памятью проба идёт каждый такт — вот от чего лечим."""
        b = self._real_deaf_brain()
        syn = _syn(b, brain={"probe_ttl": 0.0001})
        with temp_live():
            for _ in range(3):
                syn.tick()
        self.assertEqual(b.probes, 3,
                         f"контроль слепой: проба случилась {b.probes} раз, а не каждый такт")

    def test_stream_flows_while_the_brain_is_deaf(self):
        """Три такта с молчащим мозгом обязаны стоить ОДНУ пробу, а не три."""
        b = self._real_deaf_brain()
        syn = _syn(b, brain={"probe_ttl": 20})
        t0 = time.perf_counter()
        with temp_live():
            for _ in range(3):
                syn.tick()
        spent = time.perf_counter() - t0
        self.assertEqual(b.probes, 1,
                         f"молчащий мозг щупается {b.probes} раз за 3 такта — поток стоит")
        self.assertLess(spent, 3 * 0.15,
                        "три такта стоили как три пробы: память не работает")


class TestBrainProbeMemory(unittest.TestCase):
    """Память пробы живёт в самом Brain — проверяем её, а не заглушку."""

    def _brain(self):
        from synapse.brain import Brain
        b = Brain({"brain": {"mode": "server",     # без этого think() выходит досрочно
                             "endpoint": "http://127.0.0.1:9/v1/chat/completions",
                             "server_port": 9}})
        b.probes = 0

        def probe(timeout=3.0):
            b.probes += 1
            return False

        b._probe = probe
        return b

    def test_control_without_memory_probes_every_time(self):
        """КОНТРОЛЬ: ttl=0 — памяти нет, проба обязана идти каждый раз."""
        b = self._brain()
        for _ in range(4):
            b.available(ttl=0.0)
        self.assertEqual(b.probes, 4)

    def test_memory_spares_the_silent_server(self):
        b = self._brain()
        for _ in range(4):
            b.available(ttl=20.0)
        self.assertEqual(b.probes, 1,
                         f"молчащий сервер щупается {b.probes} раз — поток встанет колом")

    def test_control_cache_is_really_read(self):
        """КОНТРОЛЬ: память обязана ЧИТАТЬСЯ, иначе «забыла» ничего не значит."""
        b = self._brain()
        b._avail = (time.time(), True)          # память говорит «жив»
        self.assertTrue(b.available(ttl=2.0))
        self.assertEqual(b.probes, 0, "память не читается — проба пошла в сеть")

    def test_failure_forgets_the_cache(self):
        """Сервер не ответил — память обязана перестать утверждать, что он жив."""
        b = self._brain()
        b._avail = (time.time(), True)          # память говорит «жив»
        self.assertIsNone(b.think("привет"))    # сервера нет
        self.assertEqual(b.probes, 0, "память не читалась — контроль выше слепой")
        self.assertIsNone(b._avail,
                          "после ошибки память продолжает утверждать, что сервер жив")


class TestWindowShowsTheStream(unittest.TestCase):
    """В окне при нуле написано слово, а не цифра: иначе оператор не поймёт."""

    def setUp(self):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as exc:                          # нет Qt — честный пропуск
            self.skipTest(f"PySide6 недоступен: {type(exc).__name__}")
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "config.json"
        self._keep = config.CONFIG_PATH
        config.CONFIG_PATH = self.path
        config.forget_cache()
        self._live = temp_live()
        self._live.__enter__()
        # Настройки «как у Стаса»: русское окно, поток (пауза 0), мозг выключен.
        config.save({"ui": {"lang": "ru", "theme": "dark"}, "tick_seconds": 0,
                     "idle_thinking": True, "brain": {"mode": "off"}})
        config.forget_cache()
        from synapse.qt_panel import SynapseWindow
        self.app = QApplication.instance() or QApplication([])
        self.win = SynapseWindow()

    def tearDown(self):
        self.win.close()
        self._live.__exit__(None, None, None)
        config.CONFIG_PATH = self._keep
        config.forget_cache()
        i18n.set_lang("en")
        self._tmp.cleanup()

    def test_the_second_rate_field_is_gone(self):
        """«Второе число 0» с простынёй — убрано из окна (слово Стаса 26.09.2026)."""
        self.assertFalse(hasattr(self.win, "ed_every"),
                         "второе поле темпа вернулось в окно")

    def test_tick_zero_reads_as_the_flow(self):
        self.win.ed_tick.setText("0")
        self.win._update_life_now()
        self.assertEqual(self.win.lbl_life_now.text(), i18n.t("set.life.stream"))

    def test_control_a_number_reads_as_a_tick(self):
        self.win.ed_tick.setText("30")
        self.win._update_life_now()
        self.assertIn("30", self.win.lbl_life_now.text())

    def test_ports_are_two_numbers_sixteen_bits_wide(self):
        """Диапазон портов в окне — два числа, и он полный (v0.22.0)."""
        self.assertEqual((self.win.sp_port_from.minimum(), self.win.sp_port_to.maximum()),
                         (1, 65535), "диапазон портов не полный")


class TestRepeatIsSaidNotHidden(unittest.TestCase):
    """Мозг зациклился — существо об этом СКАЖЕТ, а не замолчит.

    Найдено ЖИВЫМ замером потока 25.09.2026: за 6 тактов мозг спросили 6 раз (поток
    работает), а в журнал попала ОДНА мысль. 0.5b повторяла ту же фразу, а прежняя
    защита от повтора гасила повтор в тишину — и оператор читал «он ничего не делает».
    """

    def test_control_the_brain_really_repeats(self):
        """КОНТРОЛЬ: заглушка ДЕЙСТВИТЕЛЬНО повторяется — иначе тест слепой."""
        brain = StubBrain(answer="одно и то же")
        syn = _syn(brain)
        with temp_live():
            first = syn.idle_thought()
            second = syn.idle_thought()
        self.assertEqual(brain.calls, 2, "мозг спрошен не дважды — это не про повтор")
        self.assertEqual(first, "одно и то же")
        self.assertNotEqual(second, first, "повтор выдан как новая мысль")

    def test_every_thought_of_the_stream_is_visible(self):
        brain = StubBrain(answer="одно и то же")
        syn = _syn(brain)
        with temp_live():
            syn.idle_thought()
            syn.idle_thought()
            syn.idle_thought()
            rows = [r for r in audit.tail(80) if r.get("event") == "brain.thought"]
        self.assertEqual(len(rows), 3,
                         f"в журнале {len(rows)} записей вместо трёх: поток прячется")
        self.assertTrue(rows[-1].get("repeat"), "повтор не помечен как повтор")
        self.assertGreaterEqual(int(rows[-1].get("streak", 0)), 2)

    def test_repeat_text_shows_what_is_repeated(self):
        """Повтор — не «живу»: оператор должен видеть, ЧТО именно заело."""
        brain = StubBrain(answer="одно и то же")
        syn = _syn(brain)
        with temp_live():
            syn.idle_thought()
            again = syn.idle_thought()
        self.assertIn("одно и то же", again, "в повторе не видно, что именно повторяется")

    def test_streak_pushes_the_prompt_harder(self):
        """Зациклился — просим конкретнее, а не задаём тот же вопрос третий раз."""
        brain = StubBrain(answer="одно и то же")
        syn = _syn(brain)
        with temp_live():
            syn.idle_thought()
            syn.idle_thought()
            prompt = syn._thought_prompt()
        self.assertIn(str(syn._repeat_streak), prompt,
                      "в промпте нет счётчика повторов — модель не узнает, что заела")
        self.assertIn(i18n.t("ag.observe.repeat", n=syn._repeat_streak)[:20], prompt)


class TestSmallBrainGetsAShortPrompt(unittest.TestCase):
    """0.5b ПРОДОЛЖАЕТ длинный список правил вместо ответа — значит промпт короткий.

    Живой замер 25.09.2026. С полным modelfile.md мозг отвечал его же пунктами:
    «Я сейчас делаю следующее: 1. Отвечать на вопросы пользователя по делу, коротко
    и без выдумок. 2. Говорить на языке, на котором к тебе обратились…». С коротким
    промптом та же модель отвечает по делу. Живой файл: 1879 знаков против 188.
    """

    FILE = ("# ModelFile: проба\n\n## System Prompt\n\nТы — маленький организм. "
            "Говори коротко.\n\n## Behavior\n\n- Отвечай по делу, коротко, без выдумок.\n"
            "- Не выдумывай результаты своей работы.\n")

    def _brain(self, tmp, mode=None):
        from synapse.brain import Brain
        f = Path(tmp) / "modelfile.md"
        f.write_text(self.FILE, encoding="utf-8")
        cfg = {"brain": {"system_prompt_file": str(f)}}
        if mode is not None:
            cfg["brain"]["prompt_mode"] = mode
        return Brain(cfg)

    def test_control_full_file_is_the_long_one(self):
        """КОНТРОЛЬ: с prompt_mode=full мозг получает правила списком — вот что ломало."""
        with tempfile.TemporaryDirectory() as tmp:
            sp = self._brain(tmp, "full").system_prompt()
        self.assertIn("Не выдумывай результаты", sp)

    def test_default_takes_only_the_short_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = self._brain(tmp).system_prompt()
        self.assertEqual(sp, "Ты — маленький организм. Говори коротко.")
        self.assertNotIn("Не выдумывай результаты", sp,
                         "в короткий промпт ушёл весь список правил")

    def test_it_is_really_shorter(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._brain(tmp)
            short = len(b.system_prompt())
        self.assertLess(short * 2, len(self.FILE),
                        f"короткий промпт не короче: {short} против {len(self.FILE)}")

    def test_no_section_falls_back_to_the_whole_file(self):
        """Раздела нет — лучше длинный промпт, чем немой мозг."""
        from synapse.brain import Brain
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "modelfile.md"
            f.write_text("# ModelFile: без разделов\n\nТы — просто организм.\n",
                         encoding="utf-8")
            sp = Brain({"brain": {"system_prompt_file": str(f)}}).system_prompt()
        self.assertIn("просто организм", sp)

    def test_the_real_modelfile_has_the_section(self):
        """Живой модфайл обязан иметь раздел — иначе правка ничего не даёт."""
        from synapse.brain import Brain
        text = (ROOT / "modelfile.md").read_text(encoding="utf-8")
        self.assertIn("## System Prompt", text)
        sp = Brain().system_prompt()
        self.assertNotIn("Не выдавай того, чего не было", sp,
                         "в мозг по-прежнему уходит весь список правил")


if __name__ == "__main__":
    unittest.main(verbosity=2)
