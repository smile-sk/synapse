"""Общий лог ПРИЛОЖЕНИЯ (не организма): старт, интерпретатор и что упало.

Зачем (слово Стаса 26.09.2026: «почему хотя бы лога общего нет для себя же
у приложения?»). До этого файла окно было НЕМЫМ к своим ошибкам: исключение в
обработчике кнопки уходило в stderr оконного процесса, а его не видит никто
(pythonw.exe консоли не имеет). Ровно так молча падала карточка нейрона —
узел вспыхивал, а TypeError «receptors это число» никто не показывал, и
оператор трижды слышал «починила» при неработающей карточке.

Что пишем в data/logs/app.log:
  * app.start — версия, интерпретатор, корень проекта (видно, ЧЕМ запущено);
  * app.error — ЛЮБОЕ необработанное исключение с полным traceback, в главном
    потоке и в фоновых (threading.excepthook);
  * app.note — пометки самого приложения, когда хочется оставить след.

Путь берём из audit.LOG_DIR (функцией, а не константой): песочница тестов
(temp_live) переназначает его, и тесты не пишут в живые данные оператора.
"""
from __future__ import annotations

import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

from . import audit                                        # noqa: F401 — нужен LOG_DIR


def app_log() -> Path:
    """Файл общего лога. Функция, а не константа: LOG_DIR меняется песочницей."""
    return audit.LOG_DIR / "app.log"


def note(text: str) -> None:
    """Строка в общий лог. Молчать нельзя, падать из-за лога — тоже."""
    try:
        app_log().parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        with app_log().open("a", encoding="utf-8") as f:
            f.write(f"{stamp} app.note {str(text)[:400]}\n")
    except Exception:                                      # noqa: BLE001
        pass


def _write(kind: str, head: str, tb: str = "") -> None:
    try:
        app_log().parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        with app_log().open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {kind} {head}\n")
            if tb:
                f.write(tb if tb.endswith("\n") else tb + "\n")
    except Exception:                                      # noqa: BLE001
        pass


def _hook(exc_type, exc, tb) -> None:
    """Необработанное исключение главного потока — в лог и в stderr, как обычно."""
    _write("app.error", f"{exc_type.__name__}: {exc}",
           "".join(traceback.format_exception(exc_type, exc, tb)))
    sys.__excepthook__(exc_type, exc, tb)


def _thread_hook(args) -> None:
    """То же для фоновых потоков: такт и запросы к мозгу живут именно там."""
    if args.exc_type is SystemExit:
        return
    _write("app.error", f"thread {args.thread.name if args.thread else '?'}: "
                        f"{args.exc_type.__name__}: {args.exc_value}",
           "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))


def install(where: str = "") -> Path:
    """Взвести ловушки и записать факт старта. Возврат — путь к логу."""
    sys.excepthook = _hook
    try:
        threading.excepthook = _thread_hook            # Python 3.8+
    except Exception:                                  # noqa: BLE001
        pass
    _write("app.start", f"{where or 'app'} python={sys.version.split()[0]} "
                        f"exe={sys.executable} root={Path(__file__).resolve().parent.parent}")
    return app_log()
