"""v0.7: потолок снят, инструменты (write_probe, certs, diff), английские команды.

Проверяем то, что код ДЕЛАЕТ, а не то, что он разобран:
  * потолок пробуждений больше не держит организм;
  * «думание вслух» разрежено, а не запрещено;
  * тест записи реально пишет маркер, читает его и убирает за собой;
  * мёртвый порт в certs — это отказ с причиной, а не ложное «всё хорошо»;
  * diff видит новые/пропавшие хосты и порты, и молчит, когда изменений нет.

Запуск без pytest:  python tests/test_tools_v07.py
"""
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from synapse import agent as agent_mod, config, reports          # noqa: E402
from synapse.tools import certs, diff, write_probe               # noqa: E402
from live_data import temp_live                                  # noqa: E402


import pytest                                          # noqa: E402

from synapse import i18n                               # noqa: E402


@pytest.fixture(autouse=True)
def _russian_language():
    """Эти тесты проверяют РУССКИЙ текст — тот, который читает Стас.

    Так и надо: язык по умолчанию теперь английский (ui.lang в настройках), а
    английский путь отдельно проверяет tests/test_i18n_theme.py. Иначе проверка
    «отчёт читается» зависела бы от того, какой язык случайно стоит в конфиге.
    """
    i18n.set_lang("ru")
    yield
    i18n.set_lang("en")


class StubBrain:
    """Мозг-заглушка: тест не имеет права поднимать llama-server."""

    def __init__(self, answer="мысль"):
        self.answer = answer
        self.calls = 0

    def mode(self):
        return "off"

    def available(self, timeout=3.0, ttl=2.0):
        return True

    def think(self, *a, **k):
        self.calls += 1
        return self.answer


def test_control_old_cap_would_have_held():
    """КОНТРОЛЬ: механизм потолка жив, но по умолчанию выключен.

    Иначе «потолка нет» ничего не значит: надо видеть, что он умеет держать.
    """
    s = agent_mod.Synapse(cfg={"max_wakeups_per_day": 2}, body=_body(), brain=StubBrain())
    ok, why = s.can_wake({"wakeups": 5})
    assert ok is False and "2" in why, why


def test_no_cap_by_default():
    s = agent_mod.Synapse(cfg=config.load(), body=_body(), brain=StubBrain())
    assert int(s.cfg.get("max_wakeups_per_day") or 0) == 0, "потолок должен быть снят"
    ok, why = s.can_wake({"wakeups": 99999})
    assert ok is True, why


def test_idle_thought_is_spaced_not_banned():
    """Разрежение мыслей — ПО СЕКУНДАМ, а не по тактам (v0.15.0).

    ОДИН ТЕМП — ТАКТ (v0.22.0). Слово Стаса 26.09.2026: «время такта и есть количество
    мыслей — ведь если мозг выключен, то не будет мыслей… время такта 0 — поток. всё».
    Раньше здесь стояло сначала «раз в N тактов», потом «раз в N секунд» — оба были
    ШВОМ: просили поток, а получали паузу. Теперь разрежения нет: мысль идёт каждый такт.

    ОБНОВЛЕНО v0.25.0 (26.09.2026). Раньше здесь стояло «первый свободный такт даёт
    РЕФЛЕКС, а не мысль» — и это оказалось НАСТОЯЩЕЙ ПОЛОМКОЙ, а не порядком: тело в
    живом окне стоит мёртвым, голод срабатывал КАЖДЫЙ такт, и мысль не задавалась ни
    разу (живой журнал 15:23–15:33: десять минут, ноль мыслей, пустая вкладка «Мысли»).
    Теперь рефлекс тело делает, но мысль НЕ отменяет.

    ВАЖНО: тест живёт в temp_live(). Раньше он писал «brain.thought text=мысль» и
    «instinct.feed» в ЖИВОЙ журнал оператора — шесть таких записей (15:31–15:38,
    25.09.2026) нашлись в data/logs/audit.jsonl разбором жалобы «постоянно idle».
    """
    brain = StubBrain()
    with temp_live():
        s = agent_mod.Synapse(cfg={"idle_thinking": True, "instinct_min_seconds": 3600},
                              body=_body(), brain=brain)
        first = s.idle_thought()
        assert brain.calls == 1, "голодное тело НЕ отменяет мысль (v0.25.0)"
        assert first == "мысль"
        s.idle_thought()
        s.idle_thought()
        assert brain.calls == 3, "темп задаёт ТАКТ: пропуска между мыслями быть не должно"


def test_write_probe_writes_reads_and_cleans():
    """Пишем, читаем обратно, убираем. Мусора после себя не оставляем."""
    with tempfile.TemporaryDirectory() as tmp:
        row = write_probe.probe_dir(tmp, marker="probe.txt")
        assert row["state"] == "writable", row
        assert list(Path(tmp).iterdir()) == [], "маркер обязан быть убран за собой"


def test_write_probe_missing_dir_is_not_a_lie():
    with tempfile.TemporaryDirectory() as tmp:
        row = write_probe.probe_dir(str(Path(tmp) / "нет-такой-папки"))
        assert row["state"] == "missing", row


def test_certs_dead_port_is_a_refusal_with_reason():
    """Контроль: закрытый порт НЕ должен выглядеть как успех."""
    row = certs.read_cert("127.0.0.1", 9, timeout=1.0)
    assert row["ok"] is False, row
    assert row["error"], "у отказа обязана быть причина"
    assert row["days_left"] is None


def test_diff_sees_changes_and_silence():
    old = {"at": "2026-09-24T16:26:52", "cidr": "192.168.1.0/24", "alive_count": 7,
           "hosts": [{"ip": "192.168.1.1", "open": [22, 80], "shares": []},
                     {"ip": "192.168.1.88", "open": [22, 445], "shares": ["Stas"]}]}
    new = {"at": "2026-09-25T09:00:00", "cidr": "192.168.1.0/24", "alive_count": 8,
           "hosts": [{"ip": "192.168.1.1", "open": [22], "shares": []},
                     {"ip": "192.168.1.88", "open": [22, 445, 8080], "shares": ["Stas", "Backup"]},
                     {"ip": "192.168.1.44", "open": [80], "shares": []}]}
    rep = diff.compare(old, new)
    assert [h["ip"] for h in rep["new_hosts"]] == ["192.168.1.44"], rep["new_hosts"]
    assert rep["gone_hosts"] == [], "все прежние хосты на месте"
    changed = {c["ip"]: c for c in rep["changed"]}
    assert changed["192.168.1.1"]["removed_ports"] == [80]
    assert changed["192.168.1.88"]["added_ports"] == [8080]
    assert changed["192.168.1.88"]["shares_added"] == ["Backup"]
    quiet = diff.compare(old, old)
    assert (quiet["new_hosts"], quiet["gone_hosts"], quiet["changed"]) == ([], [], []), quiet
    assert "не изменилась" in reports.summary_line(dict(quiet, kind="diff"))


def test_new_kinds_are_wired():
    for kind in ("write_probe", "certs", "diff"):
        assert kind in agent_mod.KINDS, kind
        assert kind in reports.KIND_RU, kind
    source = (Path(__file__).resolve().parents[1] / "synapse" / "agent.py").read_text(encoding="utf-8")
    code = "\n".join(l.split("#", 1)[0] for l in source.splitlines())
    assert "idle_think_seconds" not in code, "мысль всё ещё разрежена отдельным ключом"
    assert "tick_seconds" in code, "такт перестал быть темпом"


def test_titles_start_with_the_command():
    """Подпись отчёта начинается с КОМАНДЫ по-английски: оператор зовёт её так же."""
    assert reports.title({"kind": "certs", "targets": [1, 2], "expiring": [1]}).startswith("certs")
    assert reports.title({"kind": "write_probe", "shares": [{"state": "writable"}]}).startswith("write_probe")
    assert reports.title({"kind": "diff", "new_hosts": [{}], "gone_hosts": []}).startswith("diff")
    assert reports.title({"at": "2026-09-24T16:26:52", "cidr": "192.168.1.0/24",
                          "hosts": [], "alive_count": 0}).startswith("net_audit")


def _body():
    from synapse.body import Body
    return Body.load()


if __name__ == "__main__":
    import inspect
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and inspect.isfunction(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as exc:
                failed += 1
                print("FAIL", name, "->", exc)
    print("\nOK" if not failed else "\nПРОВАЛОВ: %d" % failed)
    sys.exit(1 if failed else 0)
