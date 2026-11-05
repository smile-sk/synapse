"""Один сторож на весь организм: дочерние процессы Synapse больше не мигают окнами.

ЗАЧЕМ ЭТО ЗДЕСЬ (история, не догадка)
------------------------------------
В AI_UI эта болячка уже была вылечена и снова всплыла здесь. Причина одна и та
же, и она в правилах Windows: наша панель стартует под pythonw.exe (Synapse.pyw),
то есть У ПРОЦЕССА НЕТ СВОЕЙ КОНСОЛИ. Консольный дочерний процесс, запущенный
процессом без консоли, получает СВОЮ чёрную консоль — она и моргает оператору.

Замер 24.09.2026 (не на глаз): при запуске `ipconfig`/`ping` из Synapse в
creationflags уходило 0x0 — то есть окно открывалось каждому вызову. А сканер
зовёт `ping` НА КАЖДЫЙ адрес подсети: /24 — это 254 мигания за один аудит.

Раньше такое лечили «точками» — флагом в каждом месте запуска. Точки кончаются:
следующее новое место (инструмент, миссия, чужая библиотека) снова мигает.
ЗДЕСЬ — ОДИН СТОРОЖ: после install() флаг добавляется сам, в единственной точке,
через которую проходят ВСЕ запуски, — в subprocess.Popen. Явную просьбу дать
консоль уважаем (CREATE_NEW_CONSOLE / DETACHED_PROCESS): попросил окно — не мешаем.

ГРАНИЦЫ
-------
* Только Windows; на других ОС install() просто помечает себя.
* GUI-приложениям не мешает: CREATE_NO_WINDOW подавляет консоль, настоящее окно
  программы (браузер, редактор) остаётся.
* Выключатель для отладки: SYNAPSE_SHOW_CONSOLE=1 — сторож не ставится, окна видны.

ДИАГНОСТИКА
-----------
last_flags — что реально ушло в последний запуск (этим проверяют тесты).
is_installed() отвечает, РАБОТАЕТ ли сторож сейчас (при выключенном выключателе — нет).
install() идемпотентен: второй вызов ничего не портит.
"""
from __future__ import annotations

import os
import subprocess

NO_WINDOW = 0x08000000        # CREATE_NO_WINDOW — консоли не будет
NEW_CONSOLE = 0x00000010      # CREATE_NEW_CONSOLE — вызывающий сам попросил окно
DETACHED = 0x00000008         # DETACHED_PROCESS — консоли и так не будет

EXPLICIT_CONSOLE = NEW_CONSOLE | DETACHED

_installed = False
_active = False               # сторож реально поставлен (а не «install вызван»)
last_flags: int | None = None          # что ушло в последний Popen (для тестов)
_keepalive: list = []                  # держим Popen для os.popen: иначе трубу закроет GC


def adjust_flags(flags) -> int:
    """Добавить CREATE_NO_WINDOW, если вызывающий НЕ просил консоль явно."""
    flags = int(flags or 0)
    if flags & EXPLICIT_CONSOLE:
        return flags
    return flags | NO_WINDOW


def is_installed() -> bool:
    """Сторож РАБОТАЕТ сейчас (не просто «install() звали»)."""
    return _active


def install() -> bool:
    """Поставить сторожа (единожды). True — поставлен, False — не ставился."""
    global _installed, _active
    if _installed:
        return _active
    _installed = True
    if os.name != "nt":
        return False
    if str(os.environ.get("SYNAPSE_SHOW_CONSOLE", "")).lower() in ("1", "true", "yes"):
        return False

    original_init = subprocess.Popen.__init__

    def _patched_init(self, *args, **kwargs):
        global last_flags
        kwargs["creationflags"] = adjust_flags(kwargs.get("creationflags"))
        last_flags = kwargs["creationflags"]
        original_init(self, *args, **kwargs)

    _patched_init.__name__ = "_synapse_popen_init"
    subprocess.Popen.__init__ = _patched_init

    # os.system и os.popen идут мимо Popen — они тоже открывают окно.
    original_system = os.system

    def _patched_system(cmd):
        try:
            return subprocess.call(cmd, shell=True)
        except Exception:                                    # noqa: BLE001
            return original_system(cmd)

    os.system = _patched_system

    original_popen = os.popen

    def _patched_popen(cmd, mode="r", buffering=-1):
        try:
            if "r" in mode:
                proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                                        text=True, bufsize=buffering)
                _keepalive.append(proc)
                return proc.stdout
            proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE,
                                    text=True, bufsize=buffering)
            _keepalive.append(proc)
            return proc.stdin
        except Exception:                                    # noqa: BLE001
            return original_popen(cmd, mode, buffering)

    os.popen = _patched_popen
    _active = True
    return True
