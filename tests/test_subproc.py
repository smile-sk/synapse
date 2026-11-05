"""Тесты v0.6: сторож консольных окон — дочерние процессы Synapse не мигают.

    python tests/test_subproc.py        (запускать из корня проекта)

Болячка (та же, что была в AI_UI): панель живёт под pythonw.exe, своей консоли у
неё нет, поэтому ЛЮБОЙ консольный ребёнок (ipconfig, ping, net view, tasklist)
получает своё чёрное окно. Замер 24.09.2026: creationflags уходил 0x0 — и сканер
мигал 254 раза за аудит /24.

ПРАВКА 25.09.2026 (жалоба «опять мигания терминальных окон при вызове run_python»).
Замер счётчиком видимых окон (EnumWindows, шаг 5 мс, по файлам отдельно) показал:
мигали НЕ рабочие запуски и НЕ run_python, а ЭТИ САМИ ТЕСТЫ —

    ai_ui  tests/test_child_guard.py    консольных окон 0
    Synapse tests/test_subproc.py       КОНСОЛЬНЫХ ОКОН 6   (3 CASCADIA + 3 PseudoConsoleWindow)
    Synapse tests/test_launcher.py      консольных окон 0

контроль запускал незакрытые процессы намеренно (это была его суть), а проверка
«просьба дать окно» открывала настоящее окно явным CREATE_NEW_CONSOLE. Правило,
выведенное из этого и теперь соблюдаемое здесь: ТЕСТ НЕ ИМЕЕТ ПРАВА ПОКАЗЫВАТЬ
ОКНО. Отсюда в файле:

  * проба-ребёнок запускается pythonw.exe — своей консоли нет ВООБЩЕ, то есть
    ровно как у панели (заодно это самый честный случай для сторожа);
  * каждый запуск внутри пробы идёт со startupinfo SW_HIDE — даже незакрытый
    (контрольный) запуск не появляется на экране оператора;
  * PYTHONPATH у пробы очищается: проверяем ИМЕННО сторож Synapse, а не сторожа
    соседнего проекта, случайно оказавшегося в окружении (раньше тесты молча на
    него опирались — и «0 миганий» получалось чужими руками).

ТЕСТЫ СМОТРЯТ, ЧТО КОД ДЕЛАЕТ, А НЕ ЧТО НАПИСАНО: датчик ставится ПЕРВЫМ, сторож
садится поверх него, и мы читаем флаги, которые реально ушли в запуск процесса.
ПЕРВЫЙ ТЕСТ — КОНТРОЛЬ: без сторожа флаги нулевые. Без контроля «стало 0x8000000»
ничего не значило бы.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import subproc                       # noqa: E402  (импорт ставит сторожа)


def consoleless_python() -> str:
    """pythonw рядом с текущим интерпретатором: процесс БЕЗ своей консоли.

    Именно так живёт панель — и именно поэтому ей нужен сторож. Если pythonw
    рядом нет (не Windows), берём текущий интерпретатор.
    """
    exe = Path(sys.executable)
    for name in ("pythonw.exe", "pythonw"):
        cand = exe.with_name(name)
        if cand.exists():
            return str(cand)
    return sys.executable


#: Общий кусок проб: спрятать окно СВОИМИ руками, независимо от сторожа.
#: Контроль обязан остаться контролем (флаги 0x0) и при этом не мигнуть оператору.
HIDDEN = '''
def hidden():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return si


def run_hidden(cmd, **kw):
    kw.setdefault("startupinfo", hidden())
    kw.setdefault("capture_output", True)
    return subprocess.run(cmd, **kw)
'''

PROBE = textwrap.dedent("""
    import sys, os, json, subprocess
    sys.path.insert(0, r"__SYNAPSE_ROOT__")
    __HIDDEN__

    # ДАТЧИК первым: сторож, поставленный позже, сядет поверх и флаги будут видны.
    real = subprocess.Popen.__init__
    seen = []
    def recorder(self, *a, **kw):
        seen.append(int(kw.get("creationflags", 0) or 0))
        real(self, *a, **kw)
    subprocess.Popen.__init__ = recorder

    import synapse                      # здесь пакет ставит сторожа
    from synapse import subproc

    run_hidden(["cmd", "/c", "echo", "hi"])                                # как ipconfig
    run_hidden([sys.executable, "-c", "pass"])                             # как ping
    print(json.dumps({
        "seen": [hex(x) for x in seen],
        "last": hex(subproc.last_flags or 0),
        "installed": subproc.is_installed(),
        "popen_name": subprocess.Popen.__init__.__name__,
    }))
""")

PROBE_REAL = textwrap.dedent("""
    import sys, os, json, subprocess
    sys.path.insert(0, r"__SYNAPSE_ROOT__")
    __HIDDEN__

    import synapse
    from synapse import subproc
    from synapse.tools import net_scan as ns

    ns.ping("127.0.0.1")                       # НАСТОЯЩИЙ путь сканера
    ping_flags = subproc.last_flags
    run_hidden(["cmd", "/c", "exit"], creationflags=subproc.DETACHED)      # просьба без консоли
    detached = subproc.last_flags
    # Просьба ДАТЬ окно проверяется живьём, но окно прячем сами (SW_HIDE):
    # сторож обязан не трогать явные флаги — а экран оператора не наше дело.
    run_hidden(["cmd", "/c", "exit"], creationflags=subproc.NEW_CONSOLE)
    asked = subproc.last_flags
    print(json.dumps({"ping": hex(ping_flags or 0),
                      "detached": hex(detached or 0),
                      "asked": hex(asked or 0)}))
""")


def _run_probe(text: str, env_extra: dict | None = None) -> dict:
    """Запустить пробу отдельным процессом БЕЗ консоли и вернуть её JSON.

    Проба идёт pythonw.exe: у неё своей консоли нет — как у панели, — поэтому
    любой её незакрытый ребёнок был бы виден оператору. Это и есть настоящая
    проверка сторожа, а не «в тепличных условиях».
    """
    work = Path(tempfile.mkdtemp(prefix="syn_probe_"))
    script = work / "probe.py"
    source = (text.replace("__SYNAPSE_ROOT__", str(ROOT))
                  .replace("__HIDDEN__", HIDDEN.strip("\n")))
    script.write_text(source, encoding="utf-8", newline="\r\n")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)      # чужой сторож в зачёт не идёт
    env.update(env_extra or {})
    r = subprocess.run([consoleless_python(), str(script)], capture_output=True,
                       text=True, errors="replace", env=env, cwd=str(ROOT))
    out = (r.stdout or "").strip().splitlines()
    for line in reversed(out):
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"проба не ответила: rc={r.returncode} {r.stdout!r} {r.stderr[-400:]!r}")


class TestWindowGuard(unittest.TestCase):
    # --- КОНТРОЛЬ: сначала доказываем, что болячка настоящая -------------------
    def test_control_without_guard_flags_are_zero(self):
        """Выключатель SYNAPSE_SHOW_CONSOLE=1 — как было: флаги 0x0, окно каждому вызову."""
        got = _run_probe(PROBE, {"SYNAPSE_SHOW_CONSOLE": "1"})
        self.assertEqual(got["seen"], ["0x0", "0x0"],
                         "контроль сломан: без сторожа окна не должно прятаться")
        self.assertFalse(got["installed"], "выключатель не выключил сторожа")

    # --- Сторож ---------------------------------------------------------------
    def test_guard_hides_console_for_real_children(self):
        got = _run_probe(PROBE)
        self.assertTrue(got["installed"])
        self.assertEqual(got["popen_name"], "_synapse_popen_init",
                         "сторож не встал в единственной точке запуска")
        for flag in got["seen"]:
            value = int(flag, 16)
            self.assertTrue(value & subproc.NO_WINDOW,
                            f"окно не спрятано: creationflags={flag}")

    def test_real_scanner_ping_goes_through_guard(self):
        """Не синтетика: настоящий ping из инструмента №1 (то, что мигало)."""
        got = _run_probe(PROBE_REAL)
        self.assertTrue(int(got["ping"], 16) & subproc.NO_WINDOW,
                        f"ping сканера не спрятан: {got['ping']}")

    def test_explicit_console_request_respected(self):
        """Просили окно явно (CREATE_NEW_CONSOLE) — сторож не спорит и не подменяет флаги."""
        got = _run_probe(PROBE_REAL)
        self.assertEqual(int(got["detached"], 16), subproc.DETACHED)
        self.assertEqual(int(got["asked"], 16), subproc.NEW_CONSOLE)

    def test_package_import_installs_guard(self):
        self.assertTrue(subproc.is_installed(),
                        "импорт пакета обязан ставить сторожа до первого запуска")

    def test_install_is_idempotent(self):
        self.assertTrue(subproc.install())
        self.assertTrue(subproc.install())
        self.assertTrue(subproc.is_installed())

    def test_adjust_flags_keeps_other_flags(self):
        self.assertEqual(subproc.adjust_flags(0), subproc.NO_WINDOW)
        self.assertEqual(subproc.adjust_flags(None), subproc.NO_WINDOW)
        self.assertEqual(subproc.adjust_flags(0x00000200), 0x00000200 | subproc.NO_WINDOW)
        self.assertEqual(subproc.adjust_flags(subproc.NEW_CONSOLE), subproc.NEW_CONSOLE)
        self.assertEqual(subproc.adjust_flags(subproc.DETACHED), subproc.DETACHED)

    def test_os_system_and_popen_are_covered(self):
        """os.system/os.popen идут мимо Popen — сторож обязан закрыть и их."""
        self.assertEqual(os.system.__name__, "_patched_system")
        self.assertEqual(os.popen.__name__, "_patched_popen")
        subproc.last_flags = None
        os.system("cmd /c exit 0")
        self.assertTrue(int(subproc.last_flags or 0) & subproc.NO_WINDOW,
                        "os.system всё ещё может мигнуть")


if __name__ == "__main__":
    unittest.main(verbosity=2)
