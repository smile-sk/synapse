"""Двойной клик по этому файлу открывает панель Synapse (без чёрного окна консоли).

Запускать лучше через pythonw.exe — тогда окна консоли нет вовсе. Если что-то
падает ДО отрисовки окна, здесь же пишем причину в data/logs/panel_error.log:
молчаливый отказ хуже громкого.
"""
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    try:
        # Панель по умолчанию — на ВИДЕОКАРТЕ (Qt+OpenGL); tkinter остаётся запасным
        # путём, если Qt в этом интерпретаторе нет. Тот же выбор, что в run.py panel.
        # Правильный интерпретатор. Замер 25.09.2026: двойной клик брал питон БЕЗ
        # PySide6, окно молча падало на tkinter — то есть Qt-панель не запускалась вовсе.
        # ОБЩИЙ ЛОГ приложения (v0.24.0). Слово Стаса 26.09.2026: «почему хотя бы
        # лога общего нет для себя же у приложения?» — а его и не было: ловушки
        # взводились только в run.py, а .pyw (то, чем оператор запускает окно) шёл
        # мимо них, и любые исключения уходили в пустоту (у pythonw нет консоли).
        from synapse import applog
        applog.install("Synapse.pyw")
        from synapse.launcher import ensure_right_python
        if ensure_right_python(ROOT / "Synapse.pyw"):
            raise SystemExit(0)          # окно уже открыто интерпретатором с Qt
        try:
            from synapse.qt_panel import main
        except Exception as e:           # noqa: BLE001 — запасной путь, но не молча
            from synapse import audit
            audit.log("panel.fallback", why=f"{type(e).__name__}: {e}", python=sys.executable)
            from synapse.panel import main
        raise SystemExit(main([]))
    except SystemExit:
        raise
    except Exception:
        log = ROOT / "data" / "logs" / "panel_error.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
        raise
