"""Помощник тестов: жить во ВРЕМЕННОЙ папке, а не в живых данных оператора.

Зачем (найдено 25.09.2026 по жалобе «"такого_нет" ещё глаз мозолит»). Оказалось,
что дело было не в языке, а в МУСОРЕ В ЖИВЫХ ДАННЫХ: тесты писали прямо в рабочие
data/ — мусорные миссии в очередь, «test.event» и настоящие тики в журнал, и даже
крутили state.json живого организма (он «жил» чужими тактами, счётчик рос).
Оператор видел это в окне Journal и в таблице миссий.

Здесь ОДИН контекст, который разом уводит во временную папку всё, что тест может
испортить: очередь миссий, оба журнала (JSONL и человеческий) и файл состояния.

    with temp_live():
        syn.tick(with_mission=False)      # журнал и state уезжают в temp
"""
from __future__ import annotations

import copy
import tempfile
from contextlib import contextmanager
from pathlib import Path

from synapse import (agent as agent_mod, audit, brain as brain_mod, config, creds as creds_mod,
                     leads as leads_mod, life as life_mod, missions, online as online_mod,
                     propose as propose_mod, reports, world as world_mod)
from synapse.tools import diff, net_scan, report_io


#: Текущая песочница (одна на процесс): вложенный вход её не подменяет.
_ACTIVE: Path | None = None


@contextmanager
def nt_mode(**settings):
    """Настройки ФИЗИОЛОГИИ передачи на время теста — живой config.json не трогаем.

    Зачем (25.09.2026): числа закона тела стали двойными — со знаком медиатора и
    без него. `enabled=False` обязан воспроизводить прежний закон ТОЧНО, и это
    единственный способ доказать, что физиология действительно добавлена, а не
    переписала задним числом прежний результат. Патчим config.cached (его зовёт
    каждый такт), а не файл настроек оператора.

        with nt_mode(enabled=False):
            ...            # прежнее тело
    """
    real = config.cached

    def fake():
        cfg = copy.deepcopy(real())
        cfg.setdefault("body", {}).setdefault("nt", {}).update(settings)
        return cfg

    config.cached = fake
    try:
        yield
    finally:
        config.cached = real


@contextmanager
def temp_live():
    """Очередь, журналы, ОТЧЁТЫ и state.json — во временной папке. Отдаёт путь.

    ВЛОЖЕННЫЙ вход ничего не подменяет (v0.15.0). Песочница, открытая классом в
    setUpClass, обязана переживать автофикстуру tests/conftest.py: иначе каждый
    тест получал бы ПУСТУЮ папку, данные, посеянные классом, терялись — именно так
    и упали три проверки test_panel_refresh в первом прогоне с conftest.

    Отчёты уводим вместе с журналами, потому что их путь живёт в ТРЁХ местах:
    reports.REPORTS_DIR (читает панель) и по копии у net_scan и diff, взятых из
    модуля в момент импорта. Подменяем все три, иначе тест с отчётом оставил бы
    файл в живых data/reports оператора.
    """
    global _ACTIVE
    if _ACTIVE is not None:                    # уже в песочнице — не спорим с ней
        yield _ACTIVE
        return
    saved = (missions.QUEUE, audit.LOG_DIR, audit.JSONL, audit.PLAIN, agent_mod.STATE,
             brain_mod.PID_FILE, life_mod.LIFE, leads_mod.LEADS, creds_mod.CREDS,
             world_mod.WORLD, world_mod.DIARY, propose_mod.PROPOSALS,
             online_mod.BUDGET_FILE)
    keep_reports = reports.REPORTS_DIR
    aliases = [(mod, getattr(mod, "REPORTS")) for mod in (net_scan, diff, report_io)]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reports_dir = root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        _ACTIVE = root
        missions.QUEUE = root / "queue.json"
        audit.LOG_DIR = root
        audit.JSONL = root / "audit.jsonl"
        audit.PLAIN = root / "synapse.log"
        agent_mod.STATE = root / "state.json"
        # Номер сервера — тоже в песочницу (находка живой проверки 26.09.2026):
        # иначе тест или проверка пишут свой pid в ЖИВОЙ data/brain.pid.
        brain_mod.PID_FILE = root / "brain.pid"
        # ЖИЗНЬ (v0.27.0): запас сил — тоже файл, и он тоже уезжает в песочницу.
        life_mod.LIFE = root / "life.json"
        # ЦЕЛИ (v0.28.0) и СЕКРЕТЫ: и память о зацепках, и доступы уезжают во временную
        # папку. Иначе тест читал бы настоящий data/creds.json оператора и писал бы
        # мусорные цели в живую память существа.
        leads_mod.LEADS = root / "leads.json"
        creds_mod.CREDS = root / "creds.json"
        # ПАМЯТЬ О ДЕЛАХ и ПРЕДЛОЖЕНИЯ МОЗГА (v0.29.0, этапы M5 и M6): свой мир,
        # его дневник и счётчик предложений — тоже файлы, и они тоже уезжают.
        # Иначе тест сравнивал бы свой снимок с ЖИВЫМ миром оператора и писал бы
        # строки в его дневник.
        world_mod.WORLD = root / "world.json"
        world_mod.DIARY = root / "diary.md"
        propose_mod.PROPOSALS = root / "propose.json"
        # БЮДЖЕТ ОБЛАКА (v0.30.0): расход токенов — тоже файл, и тест обязан считать
        # свой. Иначе прогон съедал бы ЖИВОЙ суточный потолок оператора, а облачный
        # тест мог бы уйти в настоящий запрос из-за чужого остатка.
        online_mod.BUDGET_FILE = root / "budget.json"
        reports.REPORTS_DIR = reports_dir
        # report_io — ОБЩАЯ запись отчётов: через неё пишут write_probe и remote.
        # Нашлось 26.09.2026, когда я писала тесты для remote: путь отчётов живёт
        # в ЧЕТВЁРТОМ месте (константа модуля, взятая при импорте), и отчёты
        # инструментов ложились в ЖИВЫЕ data/reports оператора.
        for mod in (net_scan, diff, report_io):
            mod.REPORTS = reports_dir
        try:
            yield root
        finally:
            (missions.QUEUE, audit.LOG_DIR, audit.JSONL, audit.PLAIN, agent_mod.STATE,
             brain_mod.PID_FILE, life_mod.LIFE, leads_mod.LEADS, creds_mod.CREDS,
             world_mod.WORLD, world_mod.DIARY, propose_mod.PROPOSALS,
             online_mod.BUDGET_FILE) = saved
            reports.REPORTS_DIR = keep_reports
            for mod, old in zip((net_scan, diff, report_io), aliases):
                mod.REPORTS = old
            _ACTIVE = None
