"""Один подъём мозга за раз + причина смерти — только из строк ЭТОГО запуска.

Живая поломка 26.09.2026, слова Стаса: «Я нажимал кнопку включить мозг и сервер
не включался». В журнале видно буквально: 15:24:13 старт pid 13960, а в 15:24:17 —
ВТОРОЙ старт pid 1496 на тот же порт. Загрузка модели занимает 20–30 с, поэтому
кнопка и такт (каждый в своём потоке) успевали поднять два сервера: второй не
вставал на занятый порт, умирал с кодом 1 — и именно его панель считала «нашим»,
отсюда «сервер не стартовал», пока настоящий грузился и потом гибнул как сирота.

Второй урок того же разбора: причина смерти бралась из ОБЩЕГО файла вывода, поэтому
в ней оказывался хвост чужого процесса (предупреждение CORS) — по журналу причину
видно не было.
"""
from __future__ import annotations

import threading
import time

import pytest

from synapse import brain as brain_mod
from synapse.brain import Brain


class _FakeProc:
    def __init__(self, pid=4242):
        self.pid = pid
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 1

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.terminated = True


class _AlwaysFree:
    """Замок, который «всегда свободен» — им включаем СТАРОЕ (сломанное) поведение."""

    def acquire(self, blocking=False):
        return True

    def release(self):
        pass


def _make_brain(tmp_path, monkeypatch, holder):
    """Мозг с поддельным сервером: настоящий процесс не поднимаем."""
    d = tmp_path / "models"
    d.mkdir(parents=True, exist_ok=True)
    (d / "m.gguf").write_bytes(b"x")
    (d / "llama-server.exe").write_bytes(b"x")
    b = Brain({"mode": "server", "server_port": 11999, "local_dir": str(d),
               "model": "m.gguf", "ngl": 0, "ctx": 512})
    b.adopt = lambda: False
    b.stop_extra_listeners = lambda: []
    b.argv = lambda: ["llama-server-fake"]
    b._open_server_log = lambda: None
    b.available = lambda timeout=2.0, ttl=0.0: bool(holder["ready"])
    monkeypatch.setattr(Brain, "log_file", property(lambda self: holder["log"]))
    monkeypatch.setattr(brain_mod, "PID_FILE", tmp_path / "brain.pid")
    monkeypatch.setattr(brain_mod.audit, "log",
                        lambda ev, **kw: holder["events"].append((ev, kw)))
    return b


def _spawner(holder, twins_expected):
    """Поддельный Popen: считает запуски и держит поток внутри start_server."""
    def fake_popen(args, **kw):
        holder["spawns"] += 1
        if holder["spawns"] >= twins_expected:
            holder["both_in"].set()
        holder["both_in"].wait(5)          # ждём, пока сюда войдут все ожидаемые
        return _FakeProc(pid=4200 + holder["spawns"])
    return fake_popen


def test_control_two_starts_without_gate_spawn_two(tmp_path, monkeypatch):
    """КОНТРОЛЬ: без замка два одновременных подъёма рождают ДВА сервера (та самая поломка)."""
    log = tmp_path / "srv.log"
    log.write_text("", encoding="utf-8")
    holder = {"ready": False, "spawns": 0, "events": [], "log": log, "both_in": threading.Event()}
    b = _make_brain(tmp_path, monkeypatch, holder)
    monkeypatch.setattr(brain_mod, "_START_LOCK", _AlwaysFree())
    monkeypatch.setattr(brain_mod.subprocess, "Popen", _spawner(holder, twins_expected=2))

    results = []

    def run():
        results.append(b.start_server(wait_seconds=8))

    t1 = threading.Thread(target=run)
    t2 = threading.Thread(target=run)
    t1.start()
    t2.start()
    holder["both_in"].wait(3)
    holder["ready"] = True
    t1.join(10)
    t2.join(10)
    assert holder["spawns"] == 2, "контроль обязан показать двух серверов — иначе тест слепой"
    assert results == [True, True]


def test_gate_spawns_exactly_one_server(tmp_path, monkeypatch):
    """С замком: два одновременных подъёма дают ОДИН процесс, оба ждут его и видят готовность."""
    log = tmp_path / "srv.log"
    log.write_text("", encoding="utf-8")
    holder = {"ready": False, "spawns": 0, "events": [], "log": log, "both_in": threading.Event()}
    b = _make_brain(tmp_path, monkeypatch, holder)
    monkeypatch.setattr(brain_mod.subprocess, "Popen", _spawner(holder, twins_expected=1))

    results = []

    def run():
        results.append(b.start_server(wait_seconds=8))

    t1 = threading.Thread(target=run)
    t2 = threading.Thread(target=run)
    t1.start()
    time.sleep(0.2)                 # первый уже внутри подъёма
    t2.start()
    time.sleep(0.5)
    holder["ready"] = True
    t1.join(10)
    t2.join(10)
    assert holder["spawns"] == 1, "второй сервер на тот же порт поднимать нельзя"
    assert results == [True, True], "оба вызова обязаны дождаться ответа сервера"


def test_reason_comes_only_from_this_start(tmp_path, monkeypatch):
    """Причина смерти — только строки этого запуска, а не хвост чужого процесса."""
    log = tmp_path / "srv.log"
    log.write_text("чужая строка: CORS is set to allow all origins\nещё чужая\n", encoding="utf-8")
    holder = {"ready": False, "spawns": 0, "events": [], "log": log, "both_in": threading.Event()}
    b = _make_brain(tmp_path, monkeypatch, holder)

    mark = b._log_size()                       # как в start_server: метка ПЕРЕД запуском
    with log.open("a", encoding="utf-8") as f:
        f.write("этого запуска: failed to load model\ncode 1\n")
    tail = b.server_log_tail(since=mark)
    assert "failed to load model" in tail
    assert "CORS" not in tail, "чужие строки в причину попадать не должны"


def test_death_is_named_with_its_own_reason(tmp_path, monkeypatch):
    """Умерший сервер: событие brain.server.died с кодом и СВОЕЙ причиной; on_note молчит."""
    log = tmp_path / "srv.log"
    log.write_text("старое: CORS warning\n", encoding="utf-8")
    holder = {"ready": False, "spawns": 0, "events": [], "log": log, "both_in": threading.Event()}
    holder["both_in"].set()
    b = _make_brain(tmp_path, monkeypatch, holder)

    dead = _FakeProc(pid=777)
    dead.returncode = 1
    b._log_mark = b._log_size()
    with log.open("a", encoding="utf-8") as f:
        f.write("этого запуска: abort\n")
    notes = []
    assert b._wait_ready(wait_seconds=3, proc=dead, on_note=notes.append) is False
    ev = [e for e in holder["events"] if e[0] == "brain.server.died"]
    assert ev, "смерть сервера обязана быть в журнале"
    assert ev[0][1]["code"] == 1
    assert "abort" in ev[0][1]["reason"]
    assert "CORS" not in ev[0][1]["reason"]
    assert notes and isinstance(notes[0], str)   # заметка отдана СТРОКОЙ, не словарём


def test_no_twin_when_second_caller_times_out(tmp_path, monkeypatch):
    """Если подъём не удался, второй вызов честно говорит «нет» и НЕ плодит свой процесс."""
    log = tmp_path / "srv.log"
    log.write_text("", encoding="utf-8")
    holder = {"ready": False, "spawns": 0, "events": [], "log": log, "both_in": threading.Event()}
    b = _make_brain(tmp_path, monkeypatch, holder)
    monkeypatch.setattr(brain_mod, "_START_LOCK", _AlwaysFree())
    b.available = lambda timeout=2.0, ttl=0.0: False

    lock_held = threading.Lock()
    lock_held.acquire()                      # имитируем «кто-то уже поднимает»
    monkeypatch.setattr(brain_mod, "_START_LOCK", lock_held)
    monkeypatch.setattr(brain_mod.subprocess, "Popen",
                        lambda *a, **kw: pytest.fail("второй сервер поднимать нельзя"))
    assert b.start_server(wait_seconds=5) is False
    assert holder["spawns"] == 0
