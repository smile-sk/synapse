"""v0.23.0: ответ в чат, живой ответ не считается копией, общий лог приложения.

Жалобы Стаса 26.09.2026, каждая проверяется здесь:
  * «НЕТУ МЫСЛЕЙ НА ВКЛАДКЕ МЫСЛИ!» — ответ выбрасывался как копия промпта;
  * «на вкладке мозг существо не отвечает на мои реплики» — ответ уходил в
    однострочную строку статуса и обрезался, в поле чата не попадал;
  * «в строке ввода стоит статичное спросить 0.5b — когда выбрана 1.5b»;
  * «почему хотя бы лога общего нет для себя же у приложения?» — его не было.

КОНТРОЛЬ идёт первым: без него «ответ появился» и «не копия» ничего не доказывают.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from synapse import agent as agent_mod, applog, audit, config, i18n     # noqa: E402
from live_data import temp_live                                        # noqa: E402

#: Настоящий ответ существа из живого журнала 26.09.2026, 14:44:10 (chars=672).
#: Рядом с ним стоял brain.echo — ответ ВЫБРОСИЛИ как копию промпта.
LIVE_ANSWER = ("**Обзор состояния Synapse:** - **Нервная система:** 1220 трассов, "
               "текущий трасс: 14:43:41. - **Тело:** 5 из 302 нейронов зажжено, "
               "энергия: 0.0028.")


class Base(unittest.TestCase):
    """Жизнь во временной папке: живые data/ оператора тест не трогает."""

    def setUp(self):
        self._live = temp_live()
        self._live.__enter__()
        i18n.set_lang("ru")

    def tearDown(self):
        i18n.set_lang("en")
        self._live.__exit__(None, None, None)


class TestEchoRule(Base):
    """Копия — это ИНСТРУКЦИЯ. Данные тела копией не считаются."""

    def setUp(self):
        super().setUp()
        self.s = agent_mod.Synapse()
        self.skeleton = self.s._prompt_skeleton()

    def test_control_the_instruction_copy_is_still_caught(self):
        """КОНТРОЛЬ: копия МОИХ СЛОВ (запрет копирования) обязана ловиться."""
        found = self.s._echo_of(i18n.t("ag.observe.noecho"), self.skeleton, "")
        self.assertIsNotNone(found, "детектор перестал ловить копию инструкции")

    def test_control_the_system_prompt_copy_is_still_caught(self):
        """КОНТРОЛЬ: копия системного промпта — тоже копия."""
        system = self.s._system_text()
        self.assertTrue(system, "нет системного промпта — проверять нечего")
        self.assertIsNotNone(self.s._echo_of(system[:60], self.skeleton, system))

    def test_the_life_answer_from_the_journal_is_not_an_echo(self):
        """ЖИВОЙ СЛУЧАЙ: ответ про тело (числа из задания) — не копия.

        Это и была причина пустой вкладки «Мысли»: в задании лежат живые данные
        тела, модель их называет — а прежнее правило считало это копированием.
        """
        self.assertIsNone(self.s._echo_of(LIVE_ANSWER, self.skeleton, self.s._system_text()))

    def test_a_short_genuine_thought_is_not_an_echo(self):
        """Короткая своя мысль с именем нейрона — не копия."""
        self.assertIsNone(self.s._echo_of("Мне любопытно, что VD11 держится дольше",
                                          self.skeleton, self.s._system_text()))


class TestAppLog(Base):
    """Общий лог приложения: старт и необработанные исключения."""

    def test_control_no_log_before_install(self):
        """КОНТРОЛЬ: до install() файла общего лога нет — значит тест про него."""
        self.assertFalse(applog.app_log().exists(), "лог появился сам, без install()")

    def test_install_writes_the_start_line(self):
        """install() пишет строку старта: видно, ЧЕМ запущено приложение."""
        old_hook = sys.excepthook
        try:
            path = applog.install("тест")
        finally:
            sys.excepthook = old_hook
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        self.assertIn("app.start", text)
        self.assertIn("тест", text)

    def test_unhandled_exception_lands_in_the_log_with_traceback(self):
        """Необработанное исключение НЕ пропадает: оно в логе с полным стеком.

        Это лечение молчания окна: карточка нейрона падала TypeError, а увидеть
        это было негде — оконный процесс консоли не имеет.
        """
        old_hook = sys.excepthook
        try:
            applog.install("тест")
            try:
                raise ValueError("карточка упала")
            except ValueError:
                sys.excepthook(*sys.exc_info())
        finally:
            sys.excepthook = old_hook
        text = applog.app_log().read_text(encoding="utf-8")
        self.assertIn("app.error", text)
        self.assertIn("карточка упала", text)
        self.assertIn("Traceback", text)

    def test_the_log_follows_the_sandbox(self):
        """Лог живёт в LOG_DIR, поэтому песочница уводит и его."""
        self.assertEqual(applog.app_log().parent, audit.LOG_DIR)


class TestPlaceholder(Base):
    """Подпись поля ввода берёт ЖИВУЮ модель, а не зашитое «0.5b»."""

    def test_control_the_old_text_mentioned_0_5b(self):
        """КОНТРОЛЬ: в словаре больше нет жёсткого «0.5b» ни в одном языке."""
        for lang in ("en", "ru"):
            i18n.set_lang(lang)
            self.assertNotIn("0.5b", i18n.t("ph.ask", model="1.5b"))

    def test_the_placeholder_names_the_live_model(self):
        """Когда выбрана 1.5b — в подписи 1.5b."""
        i18n.set_lang("ru")
        self.assertIn("1.5b", i18n.t("ph.ask", model="1.5b"))
        self.assertIn("0.5b", i18n.t("ph.ask", model="0.5b"))


class StubPanel:
    """Мини-окно для проверки ОДНОГО метода: настоящего кода, без всего окна."""

    def __init__(self):
        self.shown = []
        self.chat = []
        self._ticking = True
        self._closing = False

    def _say(self, line):
        self.shown.append(line)

    def _refresh(self):
        pass

    def __getattr__(self, name):            # txt_ask и прочее — только эти два метода
        raise AttributeError(name)


class FakeChat:
    def __init__(self):
        self.lines = []

    def append(self, text):
        self.lines.append(text)


class TestAnswerGoesToChat(Base):
    """Ответ на реплику оператора попадает В ПОЛЕ ЧАТА, а не только в строку."""

    def test_the_answer_is_appended_to_the_chat_box(self):
        from synapse import qt_panel
        panel = StubPanel()
        panel.txt_ask = FakeChat()
        panel.txt_journal = FakeChat()
        panel._journal_last = None
        panel.core = None
        qt_panel.SynapseWindow._on_tick(panel, {"status": "ask", "text": "Мне спокойно."})
        self.assertTrue(panel.txt_ask.lines, "ответ не попал в чат")
        joined = " ".join(panel.txt_ask.lines)
        self.assertIn("Мне спокойно.", joined)
        self.assertIn(i18n.t("ask.brain"), joined)

    def test_control_a_tick_does_not_write_into_the_chat(self):
        """КОНТРОЛЬ: обычный такт в чат не пишет — иначе поле засорится."""
        from synapse import qt_panel
        panel = StubPanel()
        panel.txt_ask = FakeChat()
        qt_panel.SynapseWindow._on_tick(panel, {"tick": 5, "text": "живу"})
        self.assertEqual(panel.txt_ask.lines, [], "такт попал в чат")



class TestDegenerateLoop(Base):
    """Срыв генерации (петля) — это мусор, а не мысль."""

    def test_control_a_normal_answer_is_not_a_loop(self):
        """КОНТРОЛЬ: живой связный ответ петлёй не считается."""
        text = ("Мне любопытно, что нейрон VD11 держится дольше остальных, и я "
                "хочу понять, отчего тело так делает именно сейчас")
        self.assertFalse(agent_mod.Synapse._degenerate(text))

    def test_control_a_short_answer_is_not_judged(self):
        """КОНТРОЛЬ: короткий ответ петлёй быть не может — судить не о чем."""
        self.assertFalse(agent_mod.Synapse._degenerate("мне спокойно"))

    def test_the_live_loop_from_the_tick_is_a_loop(self):
        """ЖИВОЙ СЛУЧАЙ: ответ такта 26.09.2026 — тот же оборот много раз."""
        loop = "**Мышление:** **Текст:** " * 20
        self.assertTrue(agent_mod.Synapse._degenerate(loop))

if __name__ == "__main__":
    unittest.main(verbosity=2)
