"""Запуск окна управления ПРАВИЛЬНЫМ интерпретатором, а не первым попавшимся.

ЗАЧЕМ ЭТО ЗДЕСЬ (замер 25.09.2026, жалоба Стаса «впечатление, что ничего не
изменилось — такие же тормоза, журнал на русском»)
------------------------------------------------------------------------------
Двойной клик по Synapse.pyw запускал окно на C:\\Python314\\pythonw.exe, а в нём НЕТ
ни PySide6, ни Pillow. Панель молча уходила на запасной tkinter-путь — те же русские
кнопки, те же русские статусы — и рисовала тело canvas-ом без Pillow, то есть самым
медленным путём. Вся работа над окном на видеокарте и над гибридным телом в момент
запуска просто не участвовала: неправильный интерпретатор делал её невидимой.

Что делает этот модуль:
  * спрашивает САМ интерпретатор, есть ли в нём PySide6 (subprocess, не догадка);
  * если нет — ищет такой интерпретатор (конфиг -> PATH -> локальные установки)
    и перезапускает окно им, а сам тихо уходит;
  * что выбрал и почему — пишет в журнал (panel.relaunch / panel.fallback);
  * выбор помнит в конфиге (ui.python), чтобы следующий запуск не искал заново.

Чего он НЕ делает: не устанавливает пакеты, не качает ничего и не меняет чужие
интерпретаторы. Нет Qt нигде — окно откроется запасным путём, но об этом будет
сказано в журнале, а не молча.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

from synapse import audit, config

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0        # CREATE_NO_WINDOW: без мигания консоли
DETACHED = 0x00000008 if IS_WIN else 0         # жить после выхода запускающего
PROBE = "import PySide6"


def has_qt(python: str | Path, timeout: float = 40.0) -> bool:
    """Есть ли в этом интерпретаторе PySide6. Спрашиваем его самого, а не гадаем."""
    try:
        r = subprocess.run([str(python), "-c", PROBE], capture_output=True,
                           timeout=timeout, creationflags=NO_WINDOW)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def candidates() -> list[Path]:
    """Кого вообще имеет смысл спросить. Порядок = приоритет."""
    ui = config.load().get("ui", {})
    raw: list[str] = []
    if ui.get("python"):
        raw.append(str(ui["python"]))
    raw += [str(p) for p in (ui.get("python_candidates") or [])]
    for name in ("pythonw", "python"):
        exe = shutil.which(name)
        if exe:
            raw.append(exe)
            if Path(exe).name.lower().startswith("python.exe"):
                raw.append(str(Path(exe).with_name("pythonw.exe")))
    patterns = [
        r"C:\Python*\pythonw.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python*\pythonw.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python*\python.exe"),
    ]
    for pat in patterns:
        raw.extend(glob.glob(pat))
    out: list[Path] = []
    for item in raw:
        p = Path(item)
        if item and p.exists() and p not in out:
            out.append(p)
    return out


def pick(current: str | Path | None = None) -> Path | None:
    """Первый интерпретатор с Qt, кроме текущего (в текущем Qt уже нет — мы это знаем)."""
    me = Path(str(current or sys.executable))
    for p in candidates():
        if p == me:
            continue
        if has_qt(p):
            return p
    return None


def remember(python: Path) -> None:
    """Запомнить выбор в конфиге: следующий запуск не будет искать заново."""
    try:
        cfg = config.load()
        ui = dict(cfg.get("ui") or {})
        if str(python) != str(ui.get("python") or ""):
            ui["python"] = str(python)
            cfg["ui"] = ui
            config.save(cfg)
    except Exception:                       # noqa: BLE001 — не повод не открыть окно
        pass


def relaunch(python: Path, script: Path) -> bool:
    """Открыть окно уже правильным интерпретатором. True — этому процессу делать нечего."""
    try:
        subprocess.Popen([str(python), str(script)], cwd=str(script.parent),
                         creationflags=NO_WINDOW | DETACHED,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        audit.log("panel.relaunch.failed", python=str(python), error=str(e)[:200])
        return False
    audit.log("panel.relaunch", python=str(python), script=script.name)
    return True


def ensure_right_python(script: Path | None = None, prefer: str | None = None) -> bool:
    """True — окно уже открыто другим процессом, этому можно уходить.

    prefer='tk' (или ui.prefer=tk в конфиге) отключает поиск: оператор сам выбрал
    запасной путь, и навязывать ему Qt не наше дело.
    """
    ui = config.load().get("ui", {})
    if (prefer or ui.get("prefer") or "qt").lower() == "tk":
        return False
    if has_qt(sys.executable):
        return False                                # Qt на месте — запускаемся как есть
    script = script or (config.ROOT / "Synapse.pyw")
    remembered = ui.get("python")
    if remembered and Path(str(remembered)).exists() and has_qt(remembered):
        return relaunch(Path(str(remembered)), script)      # быстрый путь: помним выбор
    found = pick(sys.executable)
    if found:
        remember(found)
        return relaunch(found, script)
    audit.log("panel.fallback", why="no interpreter with PySide6 - opening the fallback panel",
              checked=len(candidates()), python=sys.executable)
    return False
