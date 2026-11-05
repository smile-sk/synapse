"""Регрессия: прогон Synapse не открывает оператору НИ ОДНОГО консольного окна.

Запуск из корня проекта:  python tests/test_no_flash.py

ЗАЧЕМ ЭТОТ ФАЙЛ (замер 25.09.2026, жалоба Стаса: «опять мигания терминальных окон
в последней твоей работе при вызове run_python — почему никак побороть не можем?»).
Я дважды «лечила» сторож и оба раза говорила «починено» — потому что мерила не то.
Замер по файлам отдельно, счётчиком ВИДИМЫХ окон (EnumWindows, шаг 5 мс):

    ai_ui  tests/test_child_guard.py    консольных окон 0
    Synapse tests/test_subproc.py       КОНСОЛЬНЫХ ОКОН 6   (3 CASCADIA + 3 PseudoConsoleWindow)
    Synapse tests/test_launcher.py      консольных окон 0

То есть мигали НЕ run_python, НЕ панель и НЕ рабочий код, а МОИ ЖЕ ТЕСТЫ:
контроль запускал незакрытые процессы намеренно, а проверка «просьба дать окно»
открывала настоящее окно явным CREATE_NEW_CONSOLE. Рабочие запуски в это же время
были чистыми (проверено: cmd / ping / python-ребёнок при стороже — 0 окон).

Правило, которое здесь охраняется: ТЕСТ И РАБОЧИЙ ЗАПУСК НЕ ПОКАЗЫВАЮТ ОКНО.
Сторож есть — значит это должно быть ПРОВЕРЯЕМО, а не «я проверила, честно».
Для этого нужен свидетель, который ВИДИТ окна: он и живёт в этом файле.

КОНТРОЛЬ ПЕРВЫМ — но без вспышки на экране. Чувствительность свидетеля доказывает
окно системного класса `Static` размером 0x0: система считает его видимым
(IsWindowVisible = True), а глазом его нет — нулевая площадь, ни рамки, ни заголовка.
Если свидетель перестанет видеть даже его — «0 окон» перестанет что-либо значить,
и тест обязан упасть, а не молча зеленеть.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import subproc                       # noqa: E402  (импорт ставит сторожа)

WINDOWS = os.name == "nt"
_POLL = 0.005
WS_VISIBLE = 0x10000000
WS_POPUP = 0x80000000


def consoleless_python() -> str:
    """pythonw рядом с текущим интерпретатором — процесс БЕЗ своей консоли (как панель)."""
    exe = Path(sys.executable)
    for name in ("pythonw.exe", "pythonw"):
        cand = exe.with_name(name)
        if cand.exists():
            return str(cand)
    return sys.executable


def visible_windows() -> set:
    """Все ВИДИМЫЕ окна верхнего уровня: (класс, pid, hwnd)."""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = set()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, name, 256)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.add((name.value, int(pid.value), int(hwnd)))
        return True

    user32.EnumWindows(cb, 0)
    return found


def is_console(entry) -> bool:
    cls = entry[0]
    return ("Console" in cls) or ("CASCADIA" in cls) or ("Pseudo" in cls)


class Watcher:
    """Свидетель: пока мы работаем, собирает всё, что хоть раз мелькнуло на экране."""

    def __init__(self, interval: float = _POLL) -> None:
        self.interval = interval
        self.before: set = set()
        self.seen: set = set()
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self) -> "Watcher":
        self.before = visible_windows()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        time.sleep(0.15)                      # дать свидетелю добить последний кадр
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.seen.update(visible_windows())    # .update, а не |= в замыкании
            time.sleep(self.interval)

    def settle(self, seconds: float = 0.8) -> None:
        """Дать чужим окнам дойти и забыть их: считаем только то, что будет ПОТОМ.

        Замер 25.09.2026: окно теста, отработавшего РАНЬШЕ, всплывает с задержкой
        (Windows Terminal открывается не мгновенно) — и свидетель писал его в вину
        следующему тесту. Ложная тревога защитой не считается.
        """
        time.sleep(seconds)
        self.seen = set()
        self.before = visible_windows()

    def new(self, console_only: bool = False) -> list:
        fresh = self.seen - self.before
        if console_only:
            fresh = {w for w in fresh if is_console(w)}
        return sorted(fresh, key=lambda w: (w[0], w[1]))


PROBE_PING = textwrap.dedent("""
    import sys, os, json, subprocess, ctypes
    sys.path.insert(0, r"__SYNAPSE_ROOT__")
    import synapse
    from synapse import subproc

    # Незакрытый запуск из процесса БЕЗ консоли — ровно то, что мигало у панели.
    subprocess.run(["ping", "-n", "2", "127.0.0.1"], capture_output=True)
    print(json.dumps({
        "flags": hex(subproc.last_flags or 0),
        "installed": subproc.is_installed(),
        "console": ctypes.windll.kernel32.GetConsoleWindow(),
        "pythonpath": os.environ.get("PYTHONPATH"),
    }))
""")


@unittest.skipUnless(WINDOWS, "окна консолей — только Windows")
class TestNoFlash(unittest.TestCase):

    # --- КОНТРОЛЬ: свидетель обязан УМЕТЬ видеть окно ------------------------
    def test_control_watcher_sees_a_window(self):
        """Окно 0x0 системного класса Static: видимо системе, невидимо глазу."""
        user32 = ctypes.windll.user32
        with Watcher() as watcher:
            hwnd = user32.CreateWindowExW(0, "STATIC", None, WS_VISIBLE | WS_POPUP,
                                          0, 0, 0, 0, None, None, None, None)
            self.assertTrue(hwnd, "не удалось создать контрольное окно")
            time.sleep(0.3)
            user32.DestroyWindow(hwnd)
        self.assertTrue(watcher.new(),
                        "свидетель не увидел даже контрольного окна — проверка слепа")

    # --- Настоящая проверка: прогон тестов не показывает окна ----------------
    def test_synapse_tests_show_no_console_window(self):
        """Те два теста, что мигали (контроль + просьба дать окно), обязаны молчать."""
        try:
            import pytest                                   # noqa: F401
        except Exception as exc:                            # noqa: BLE001
            self.skipTest(f"нет pytest: {exc}")
        cmd = [sys.executable, "-m", "pytest", "tests/test_subproc.py", "-q",
               "-k", "control_without_guard or explicit_console"]
        with Watcher() as watcher:
            watcher.settle()
            r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=300)
        self.assertEqual(r.returncode, 0, ((r.stdout or "")[-600:] + (r.stderr or "")[-600:]))
        self.assertIn("2 passed", r.stdout or "")
        self.assertEqual(watcher.new(console_only=True), [],
                         "тесты снова показали оператору консольное окно")

    def test_probe_without_console_hides_ping(self):
        """Проба pythonw (своей консоли нет) зовёт ping без флагов — окна быть не должно."""
        work = Path(tempfile.mkdtemp(prefix="syn_noflash_"))
        script = work / "probe.py"
        script.write_text(PROBE_PING.replace("__SYNAPSE_ROOT__", str(ROOT)),
                          encoding="utf-8", newline="\r\n")
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)              # чужой сторож в зачёт не идёт
        with Watcher() as watcher:
            watcher.settle()
            r = subprocess.run([consoleless_python(), str(script)], capture_output=True,
                               text=True, errors="replace", env=env, cwd=str(ROOT),
                               timeout=120)
        line = [l for l in (r.stdout or "").splitlines() if l.startswith("{")]
        self.assertTrue(line, f"проба не ответила: rc={r.returncode} {r.stdout!r} {r.stderr[-300:]!r}")
        got = json.loads(line[-1])
        self.assertTrue(got["installed"], "сторож не встал у пробы без консоли")
        self.assertTrue(int(got["flags"], 16) & subproc.NO_WINDOW,
                        f"ping ушёл без флага: {got['flags']}")
        self.assertEqual(got["console"], 0, "проба не должна иметь своей консоли")
        self.assertIsNone(got["pythonpath"], "проба обязана быть без чужого сторожа")
        self.assertEqual(watcher.new(console_only=True), [],
                         "проба без консоли всё-таки показала окно")


if __name__ == "__main__":
    unittest.main(verbosity=2)
