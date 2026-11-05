"""v0.20.0: мозг в настройках, легенда там же, ровные строки, клик по узлу, канал мыслей.

    python tests/test_ui_v020.py

Четыре жалобы Стаса 26.09.2026, все проверяются здесь:
  * «сделала вкладку Мысли — но там пусто»;
  * «постоянно в каждый тик что то моргает в статусной строке, смещая все строки»;
  * «легенду цветов вынеси куда то в настройки»;
  * «что за подпись внизу - клик по нейрону - карточка нейрона - это не работает»;
  * «сделай переключение CPU/GPU и выбор модели тоже в настройках».

КОНТРОЛЬ идёт первым в каждом классе: без него «стало ровно» и «мысль появилась»
ничего не доказывают.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from synapse import agent as agent_mod, audit, config, i18n      # noqa: E402
from synapse.panel_core import PanelCore                         # noqa: E402
from live_data import temp_live                                  # noqa: E402

try:                                                             # noqa: E402
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except Exception:                                                # noqa: BLE001
    HAS_QT = False


class Base(unittest.TestCase):
    """Жизнь во временной папке: живые data/ оператора тест не трогает."""

    def setUp(self):
        self._live = temp_live()
        self._live.__enter__()

    def tearDown(self):
        self._live.__exit__(None, None, None)


class CfgSafe(Base):
    """Плюс СВОЙ config.json.

    Найдено при написании этих тестов: temp_live() уводит очередь, журналы, отчёты и
    state, но НЕ config.json — а проверка «настройки сохранились» писала бы в живой
    файл оператора. Вторая опасность: apply_settings при смене модели гасит сервер,
    то есть тест убил бы НАСТОЯЩИЙ llama-server. Поэтому здесь временный путь настроек
    и заглушка stop_server, которая только записывает вызов.
    """

    def setUp(self):
        super().setUp()
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
        super().tearDown()


@unittest.skipUnless(HAS_QT, "нет PySide6 — окно не построить")
class TestLegendPlacement(CfgSafe):
    """Легенда обязана жить в «Настройках», а не под телом (жалоба 26.09.2026)."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _in(self, node, parent) -> bool:
        while node is not None:
            if node is parent:
                return True
            node = node.parent()
        return False

    def test_legend_is_in_the_settings_tab_and_not_under_the_body(self):
        from synapse.qt_panel import SynapseWindow
        win = SynapseWindow()
        win.core.brain.stop_server = lambda: None      # НАСТОЯЩИЙ сервер не гасим
        try:
            page = win.book.widget(win._settings_tab)
            self.assertTrue(self._in(win.lbl_legend, page),
                            "легенда не в «Настройках» — значит всё ещё мозолит глаза под телом")
            self.assertFalse(self._in(win.lbl_legend, win.view.parent()),
                             "легенда осталась в левой колонке под телом")
            self.assertTrue(win.lbl_legend.text(), "легенда пуста — цвета опять не объяснены")
            # строки фиксированной высоты: иначе вид дёргается на каждом такте
            from synapse.qt_panel import FixedLine
            for lbl in (win.lbl_action, win.lbl_body, win.lbl_card):
                self.assertIsInstance(lbl, FixedLine, "подпись снова гуляет по высоте")
        finally:
            win.close()


@unittest.skipUnless(HAS_QT, "нет PySide6 — окно не построить")
class TestControl(Base):
    """Без этих проверок «строки стали ровными» и «клик доходит» ничего не значат."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_control_height_is_the_same_for_short_and_long_text(self):
        from synapse.qt_panel import FixedLine
        line = FixedLine(lines=1)
        was = (line.minimumHeight(), line.maximumHeight())
        line.setText("коротко")
        line.setText("очень длинная строка " * 40)
        self.assertEqual((line.minimumHeight(), line.maximumHeight()), was,
                         "высота подписи поехала — на каждом такте вид будет дёргаться")
        self.assertEqual(line.full_text(), "очень длинная строка " * 40,
                         "полный текст потерян — оператор не увидит, что произошло")
        self.assertTrue(line.toolTip(), "нет подсказки с полным текстом")

    def test_control_pick_reports_the_neuron_it_hit(self):
        """Клик по узлу обязан назвать ИМЕННО тот узел (иначе карточка — чужому)."""
        from synapse.qt_panel import BodyView
        core = PanelCore()
        try:
            view = BodyView(core, fps_ms=33)
        except Exception as e:                        # noqa: BLE001 — среда без GL
            self.skipTest(f"GL-виджет в этой среде не создаётся: {type(e).__name__}: {e}")
        view.resize(700, 420)
        got = []
        view.picked.connect(got.append)
        px, py, _d = core.screen(700, 420)[7]
        view._pick(px, py)
        self.assertEqual(got, [core.body.ids[7]], "клик не доходит до окна — карточки не будет")
        self.assertEqual(view._pick(3.0, 3.0), None, "клик по пустому месту назвал узел")

    def test_control_the_card_is_filled_by_the_click_itself(self):
        """КОНТРОЛЬ ПРОТИВ МОЕЙ ЖЕ СЛЕПОТЫ (v0.21.1).

        Прежний контроль проверял ТОЛЬКО сигнал picked — и «проходил», пока карточка
        ПАДАЛА на ", ".join(3) (receptors это число, а не список). Оператор при этом
        видел: узел вспыхнул, а информации о нём нет. Поэтому здесь проверяется
        НАСТОЯЩИЙ результат — текст карточки после клика по узлу, а не факт сигнала.
        """
        from synapse.qt_panel import SynapseWindow
        try:
            win = SynapseWindow()
        except Exception as e:                        # noqa: BLE001 — среда без GL
            self.skipTest(f"окно в этой среде не создаётся: {type(e).__name__}: {e}")
        try:
            win.resize(760, 520)
            view = win.view
            view._full_rebuild(max(1, view.width()), max(1, view.height()))
            self.assertTrue(view._pts, "вид не построил координаты узлов")
            px, py, _d = view._pts[7]
            win.lbl_card.setText("")
            view._pick(px, py)
            text = win.lbl_card.full_text()
            self.assertTrue(text, "клик по узлу не дал карточки — оператор снова не увидит нейрон")
            self.assertIn(win.core.body.ids[7], text,
                          "карточка говорит не о том узле, по которому кликнули")
        finally:
            win.close()


class TestSettings(CfgSafe):
    """Выбор модели и счёта — в окне, а не правкой файла руками."""

    def test_models_are_read_from_the_creature_folder(self):
        st = PanelCore().settings()
        self.assertIn("qwen2.5-0.5b-instruct-q4_k_m.gguf", st["brain_models"])
        self.assertIn("qwen2.5-1.5b-instruct-q4_k_m.gguf", st["brain_models"],
                      "1.5b не виден в списке — выбрать его в окне нельзя")

    def test_control_defaults_are_still_gpu_and_ten_layers(self):
        core = PanelCore()
        self.assertEqual(core.settings()["brain_device"], "gpu")
        self.assertEqual(core.brain.ngl(), 10, "умолчание поехало — поведение изменилось бы")

    def test_cpu_choice_takes_the_layers_off_the_card(self):
        core = PanelCore()
        core.brain.stop_server = lambda: None          # НАСТОЯЩИЙ сервер не трогаем
        res = core.apply_settings({"brain_device": "cpu", "brain_ngl": "12",
                                   "brain_port": "11500",
                                   "brain_model": "qwen2.5-1.5b-instruct-q4_k_m.gguf"})
        self.assertTrue(res["ok"], res)
        disk = json.loads(self._cfg.read_text(encoding="utf-8"))
        self.assertEqual(disk["brain"]["device"], "cpu")
        self.assertEqual(disk["brain"]["ngl"], 12)
        self.assertEqual(disk["brain"]["model"], "qwen2.5-1.5b-instruct-q4_k_m.gguf")
        self.assertEqual(disk["brain"]["server_port"], 11500)
        self.assertEqual(core.brain.ngl(), 0, "cpu-режим всё ещё грузит видеокарту")
        argv = core.brain.argv()
        self.assertEqual(argv[argv.index("-ngl") + 1], "0", "в команде сервера остались слои на GPU")
        self.assertEqual(core.brain.model_path.name, "qwen2.5-1.5b-instruct-q4_k_m.gguf")

    def test_gpu_choice_puts_the_layers_back(self):
        core = PanelCore()
        core.brain.stop_server = lambda: None
        core.apply_settings({"brain_device": "gpu", "brain_ngl": "12"})
        self.assertEqual(core.brain.ngl(), 12)

    def test_changing_the_model_stops_the_old_server_and_says_so(self):
        core = PanelCore()
        killed = []
        core.brain.stop_server = lambda: killed.append(1)
        res = core.apply_settings({"brain_model": "qwen2.5-1.5b-instruct-q4_k_m.gguf"})
        self.assertEqual(killed, [1], "старая модель осталась бы жить на прежнем порту")
        self.assertIn(i18n.t("set.changed.brain_restart"), res["changed"],
                      "не сказано, что мозг надо поднять заново")

    def test_bad_model_is_refused_and_the_old_one_kept(self):
        core = PanelCore()
        before = core.settings()["brain_model"]
        res = core.apply_settings({"brain_model": "ведро.gguf"})
        self.assertFalse(res["ok"])
        self.assertTrue(res["errors"], "оператору не сказали, что выбор неверен")
        self.assertEqual(core.settings()["brain_model"], before)

    def test_old_style_server_args_are_migrated_not_changed(self):
        """У оператора в файле лежало -ngl/-c: значения обязаны сохраниться, а не сброситься."""
        cfg = config.load()
        cfg.setdefault("brain", {})["server_args"] = ["-ngl", "7", "-c", "2048", "--threads", "4"]
        config.save(cfg, self._cfg)
        config.forget_cache()
        st = PanelCore().settings()
        self.assertEqual(st["brain_ngl"], 7, "старое число слоёв потеряно при обновлении")
        self.assertEqual(st["brain_ctx"], 2048, "старый контекст потерян при обновлении")


class TestThoughts(CfgSafe):
    """Жалоба «вкладка Мысли пуста»: канал наполнялся только метками 💭 от модели."""

    class Stub:
        def __init__(self, answer):
            self._answer = answer

        def available(self, timeout=3.0, ttl=2.0):
            return True

        def mode(self):
            return "server"

        def ensure(self, *a, **kw):
            return True

        def ask(self, prompt, system="", history=None):
            return "ok"

        def think(self, prompt, system="", history=None):
            return self._answer(prompt) if callable(self._answer) else self._answer

    def _syn(self, stub):
        cfg = config.load()
        cfg["idle_thinking"] = True
        cfg["idle_think_seconds"] = 0
        return agent_mod.Synapse(cfg=cfg, brain=stub)

    def test_control_the_channel_is_empty_before_any_thought(self):
        self.assertEqual(audit.thoughts_tail(), [])

    def test_plain_answer_lands_in_the_thoughts_channel(self):
        syn = self._syn(self.Stub("телу тесно, я голоден"))
        for _ in range(5):
            syn.idle_thought()
        text = chr(10).join(audit.thoughts_tail())
        self.assertIn("телу тесно", text, "ответ не попал в канал мыслей — вкладка снова пуста")

    def test_a_copy_of_the_prompt_is_not_a_thought(self):
        """Копия промпта — не мысль: в канал она попадать не должна, иначе вкладка врала бы."""
        syn = self._syn(self.Stub(lambda prompt: prompt))
        for _ in range(5):
            syn.idle_thought()
        self.assertEqual(chr(10).join(audit.thoughts_tail()).strip(), "",
                         "копия промпта записана как мысль")

