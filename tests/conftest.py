"""Песочница для ВСЕХ тестов: ни один тест не имеет права писать в живые data/.

Зачем (25.09.2026). Правило «тесты не пишут в живые данные» я нарушала трижды, и
каждый раз по одной причине: помощник temp_live() есть, но НОВЫЙ тест о нём просто
не знает. Последний случай нашёлся разбором жалобы Стаса «он ничего не делает,
постоянно idle»: test_tools_v07::test_idle_thought_is_spaced звал idle_thought() с
мозгом-заглушкой и писал «brain.thought text=мысль» прямо в ЖИВОЙ журнал — шесть
таких записей (15:31–15:38) лежали в data/logs/audit.jsonl.

Лечение — не «быть внимательнее» (это я обещала уже дважды), а механизм:
автофикстура уводит во временную папку очередь миссий, оба журнала и файл
состояния для КАЖДОГО теста. Тесту не нужно помнить про песочницу — он в ней
живёт по умолчанию.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_data import temp_live          # noqa: E402


@pytest.fixture(autouse=True)
def _sandbox_live_data():
    """Каждый тест живёт во ВРЕМЕННОЙ папке: живые data/ остаются нетронутыми."""
    with temp_live():
        yield
