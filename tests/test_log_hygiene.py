"""v0.20.2: ЖУРНАЛ НЕ РВЁТСЯ и МУСОР НЕ ЗАРАЖАЕТ ПОТОК.

Жалоба Стаса 26.09.2026: «абсолютно ничерта не работает, ничего не пишет в логи и
не отвечает». Разбор нашёл две настоящие поломки, обе мои:

  1) МОЗГ ОТВЕТИЛ 2000 ЗНАКОВ РАЗДЕЛИТЕЛЕЙ «---», и поле text=... разорвало
     человеческий журнал: из 1264 строк 641 оказалась мусором (посчитано). Запись
     выглядела состоящей из одних «---», вкладки выглядят пустыми;
  2) ЭТОТ ЖЕ МУСОР УШЁЛ В НИТЬ — и следующий запрос модель продолжала своими же
     «---». Проверено живой пробой на настоящем сервере: с мусорной нитью ответ
     «---\n\n---», без нити — внятная фраза. Поток зацикливался навсегда.

КОНТРОЛЬ первым, как везде в проекте.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import audit, i18n                      # noqa: E402
from synapse.agent import Synapse                    # noqa: E402
from synapse.body import Body                        # noqa: E402
from live_data import temp_live                      # noqa: E402

JUNK = "---\n\n---\n\n---"


class StubBrain:
    """Мозг-заглушка: отвечает чем велено, помнит историю."""

    def __init__(self, answer="живая мысль"):
        self.answer = answer
        self.calls = 0
        self.histories = []

    def mode(self):
        return "off"

    def available(self, timeout=3.0, ttl=2.0):
        return True

    def think(self, prompt, system="", history=None):
        self.calls += 1
        self.histories.append(list(history or []))
        return self.answer


def _syn(br, **cfg):
    base = {"idle_thinking": True, "idle_think_seconds": 0,
            "instinct_min_seconds": 3600, "tick_log_seconds": 3600}
    base.update(cfg)
    s = Synapse(cfg=base, body=Body.load(), brain=br)
    s.body.stimulus("проснулась", amp=1.0, k=6)
    s.save_state({"day": date.today().isoformat(), "wakeups": 0, "total_ticks": 7})
    return s


def _lines() -> list:
    return audit.PLAIN.read_text(encoding="utf-8").splitlines()


class TestControlNaiveJoinBreaksTheLog(unittest.TestCase):
    """КОНТРОЛЬ: наивная склейка поля РВЁТ запись — иначе лечить нечего."""

    def test_naive_join_of_a_multiline_value_makes_many_lines(self):
        naive = "2026-01-01 00:00:00 event text=" + JUNK
        self.assertGreater(len(naive.splitlines()), 1,
                           "контроль не работает: многострочное значение не рвёт строку")

    def test_control_real_log_stays_one_line_per_record(self):
        with temp_live():
            audit.log("control", text=JUNK)
            lines = _lines()
            empty_or_sep = [l for l in lines if l.strip() in ("", "---")]
        self.assertEqual(len(lines), 1, "запись аудита обязана быть ОДНОЙ строкой")
        self.assertEqual(empty_or_sep, [], "журнал засорён строками-разделителями")


class TestLogHygiene(unittest.TestCase):
    def setUp(self):
        i18n.set_lang("ru")

    def test_newlines_become_one_line_and_are_visible(self):
        with temp_live():
            audit.log("t", text="первая\n\nвторая")
            lines = _lines()
        self.assertEqual(len(lines), 1, "перевод строки разорвал запись журнала")
        self.assertIn("первая", lines[0])
        self.assertIn("вторая", lines[0])

    def test_long_value_is_capped(self):
        with temp_live():
            audit.log("t", text="я" * 500)
            line = _lines()[0]
        self.assertLess(len(line), 400, "длинное поле не обрезано")

    def test_thought_is_written_as_one_line(self):
        with temp_live():
            audit.thought(JUNK + "\nмысль живая")
            tl = audit.thoughts_log().read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(tl), 1, "одна мысль — одна строка")
        self.assertIn("мысль живая", tl[0])

    def test_thoughts_tail_hides_separators_and_blanks(self):
        with temp_live():
            with audit.thoughts_log().open("a", encoding="utf-8") as f:
                f.write("---\n\n\n===\nнастоящая мысль\n```\n")
            got = audit.thoughts_tail(50)
        self.assertEqual(got, ["настоящая мысль"])


class TestJunkDoesNotPoisonTheFlow(unittest.TestCase):
    """Мусорный ответ: в нить НЕ попадает, в журнал — честной строкой."""

    def test_control_good_answer_reaches_the_thread(self):
        br = StubBrain("живая мысль про ASGR")
        with temp_live():
            s = _syn(br)
            out = s.idle_thought()
        self.assertEqual(s._thread, ["живая мысль про ASGR"], "живая мысль должна остаться в нити")
        self.assertNotIn("ag.junk", out)

    def test_junk_answer_is_not_threaded(self):
        br = StubBrain(JUNK)
        with temp_live():
            s = _syn(br)
            out = s.idle_thought()
            bodies = [r for r in audit.tail(20) if r.get("event") in ("brain.junk", "brain.thought")]
        self.assertEqual(s._thread, [], "МУСОР ПОПАЛ В НИТЬ — поток заразится")
        self.assertEqual(s._last_idle_status, "junk")
        self.assertEqual([r["event"] for r in bodies], ["brain.junk"])
        self.assertIn("разделителями", out)

    def test_junk_never_becomes_a_thought_record(self):
        br = StubBrain(JUNK)
        with temp_live():
            s = _syn(br)
            s.idle_thought()
            tl = audit.thoughts_log().read_text(encoding="utf-8").splitlines() if audit.thoughts_log().exists() else []
        self.assertEqual(tl, [], "мусор записан как мысль существа")

    def test_junk_streak_mutes_the_brain(self):
        br = StubBrain(JUNK)
        with temp_live():
            s = _syn(br, brain={"echo_limit": 2, "echo_cooldown": 60})
            s.idle_thought()
            s.idle_thought()
            out = s.idle_thought()              # третий мусор — уже молчание
            events = [r.get("event") for r in audit.tail(30)]
        self.assertIn("brain.muted", events, "после лимита мусора мозг обязан замолчать")
        self.assertEqual(out, i18n.t("ag.muted"))
        # Вопросов РОВНО два: второй мусорный ответ уже включил молчание, и третий
        # такт мозг не спрашивает вовсе — в этом и смысл (молотить впустую нечего).
        self.assertEqual(br.calls, 2, "пока мозг молчит, спрашивать его нельзя")


class TestSandboxCoversThePidFile(unittest.TestCase):
    """Номер сервера не должен попадать в ЖИВЫЕ data/ (находка живой проверки 26.09.2026).

    Что было: `temp_live()` уводит очередь, журналы, отчёты и состояние, но НЕ config.DATA
    (там же живое тело data/body/worm.json). Путь pid-файла считался напрямую от config.DATA,
    поэтому сервер, поднятый тестом или проверкой, писал свой номер в живой data/brain.pid —
    и панель потом могла счесть чужим свой же сервер.

    Проверяем ДВА РАЗНЫХ утверждения, и первое — контроль: без него «pid-файл в песочнице»
    ничего не значит (первый вариант этих тестов читал «живой» путь ВНУТРИ песочницы и падал
    на собственном сравнении — conftest держит песочницу всё время тестов).
    """

    def test_control_brain_takes_the_path_from_the_module_variable(self):
        src = (ROOT / "synapse" / "brain.py").read_text(encoding="utf-8")
        self.assertIn('PID_FILE = config.DATA / "brain.pid"', src,
                      "переменной пути pid-файла нет — подменять нечего")
        self.assertIn("return PID_FILE", src,
                      "pid_file() обязан брать путь из переменной, а не из config.DATA")

    def test_pid_file_lives_in_the_sandbox_not_in_live_data(self):
        from synapse import brain as brain_mod
        live = (ROOT / "data").resolve()
        self.assertEqual(brain_mod.PID_FILE.name, "brain.pid")
        self.assertNotEqual(Path(brain_mod.PID_FILE).resolve().parent, live,
                            "во время тестов pid-файл обязан быть в песочнице, а не в живых data/")
        # и запись правда идёт туда, а не в живой файл
        brain_mod.PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        brain_mod.PID_FILE.write_text("12345", encoding="utf-8")
        self.assertEqual(brain_mod.PID_FILE.read_text(encoding="utf-8"), "12345")
        brain_mod.PID_FILE.unlink()
        self.assertFalse((live / "brain.pid").exists()
                         and (live / "brain.pid").read_text(encoding="utf-8") == "12345",
                         "номер утёк в живой data/brain.pid")

if __name__ == "__main__":
    unittest.main()
