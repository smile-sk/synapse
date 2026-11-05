"""Тесты Synapse. Запуск из корня проекта:

    python tests/test_synapse.py

Первым в сетевом блоке идёт КОНТРОЛЬ (закрытый порт — не находится),
иначе зелёный тест ничего не доказывает.
"""
from __future__ import annotations

import json
import socket
import os
import time
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import audit, config, missions, scope          # noqa: E402
from live_data import temp_live                              # noqa: E402
from synapse.agent import Synapse                            # noqa: E402
from synapse.body import Body                                # noqa: E402
from synapse.tools import net_scan                           # noqa: E402


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


def quiet_synapse():
    """Организм для тестов БЕЗ живого мозга.

    Тесты не должны поднимать llama-server и жечь GPU, а тем более оставлять его сиротой.
    """
    cfg = config.load()
    cfg["brain"] = dict(cfg.get("brain", {}), mode="off")
    return Synapse(cfg=cfg)


class TestConfig(unittest.TestCase):
    def test_memory_wins_unknown_kept(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"tick_seconds": 7, "мой_ключ": "жив"}), encoding="utf-8")
            cfg = config.load(p)
            self.assertEqual(cfg["tick_seconds"], 7)          # память главнее умолчания
            self.assertEqual(cfg["мой_ключ"], "жив")          # незнакомый ключ не трогаем
            self.assertEqual(cfg["brain"]["mode"], "server")  # вложенное умолчание на месте

    def test_list_taken_whole(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"net": {"cert_ports": [1, 2]}}), encoding="utf-8")
            self.assertEqual(config.load(p)["net"]["cert_ports"], [1, 2])   # список целиком


class TestBody(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = Body.load()

    def test_connectome_loaded(self):
        self.assertEqual(self.body.size, 302)                  # C. elegans: 302 нейрона
        self.assertGreater(len(self.body.edges), 3000)
        roles = self.body.roles_summary()
        self.assertEqual(sum(roles.values()), self.body.size)   # каждая клетка названа
        for r in ("sensory", "motor", "inter"):
            self.assertGreater(roles.get(r, 0), 0, f"нет роли {r}")

    def test_impulse_travels_then_dies(self):
        """Тело живое и смертное: импульс расходится и гаснет сам (слой рефлексов)."""
        b = Body.load()
        b.stimulus("импульс", amp=1.0, k=5)
        peak = 0
        for _ in range(10):
            b.step()
            peak = max(peak, b.lit())
        self.assertGreater(peak, 8, "импульс не расходится по связям")
        for _ in range(200):
            b.step()
            if b.lit() == 0:
                break
        self.assertEqual(b.lit(), 0, "тело осталось возбуждённым — это уже припадок, не жизнь")

    def test_stimulus_is_deterministic(self):
        a = Body.load().stimulus("одно и то же", amp=1.0, k=3)
        b = Body.load().stimulus("одно и то же", amp=1.0, k=3)
        self.assertEqual(a, b, "один текст — один участок тела")

    def test_intent_reads_motor(self):
        b = Body.load()
        self.assertEqual(b.intent()["action"], "idle")
        b.poke(b.pick("motor", k=3, seed=1), 0.9)
        self.assertEqual(b.intent()["action"], "act")          # тело хочет действовать


class TestScope(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()

    def test_public_allowed_by_the_operators_list(self):
        """Слово Стаса 30.09.2026: «любые сети должны быть» — конфиг решает, не устройство.

        Причина двери — КОДОМ, а слова берёт словарь (одно окно — один язык).
        """
        ok, code, params = scope.check_code("8.8.8.8", self.cfg)
        self.assertTrue(ok, "с allowed_cidrs 0.0.0.0/0 публичный адрес обязан проходить")
        self.assertEqual(code, "door.any")
        was = i18n.lang()
        try:
            i18n.set_lang("en")
            self.assertIn("any network is allowed", scope.say(code, **params))
            i18n.set_lang("ru")
            self.assertIn("любые сети разрешены", scope.say(code, **params))
        finally:
            i18n.set_lang(was)

    def test_control_a_private_only_list_still_refuses(self):
        """КОНТРОЛЬ: дверь не сломана — свой список из приватных сетей по-прежнему отбивает."""
        own = {"net": {"allowed_cidrs": ["127.0.0.0/8", "10.0.0.0/8",
                                         "172.16.0.0/12", "192.168.0.0/16"]}}
        ok, code, _ = scope.check_code("8.8.8.8", own)
        self.assertFalse(ok, "с чужим списком публичный адрес обязан быть отбит")
        self.assertEqual(code, "door.outside_nets")
        self.assertFalse(scope.check_cidr("8.8.8.0/24", own)[0], "и подсеть — тоже")

    def test_private_allowed(self):
        ok, _ = scope.check("192.168.1.10", self.cfg)
        self.assertTrue(ok)

    def test_cidr_limits(self):
        self.assertTrue(scope.check_cidr("192.168.1.0/24", self.cfg)[0])
        self.assertTrue(scope.check_cidr("8.8.8.0/24", self.cfg)[0],
                        "чужая сеть разрешена: список оператора — 0.0.0.0/0")
        ok, why = scope.check_cidr("10.0.0.0/8", self.cfg)
        self.assertFalse(ok, "подсеть больше потолка адресов должна отсекаться")


class TestMissions(unittest.TestCase):
    def test_queue_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            q = Path(d) / "queue.json"
            rec = missions.add("net_audit", {"cidr": "127.0.0.0/30"}, note="тест", path=q)
            self.assertEqual(len(missions.queued(q)), 1)
            taken = missions.take_next(q)
            self.assertEqual(taken["id"], rec["id"])
            self.assertEqual(taken["status"], "running")
            self.assertEqual(missions.queued(q), [], "взятая миссия не ждёт дважды")
            done = missions.mark(rec["id"], "done", "ок", path=q)
            self.assertEqual(done["status"], "done")
            self.assertEqual(json.loads(q.read_text(encoding="utf-8"))[0]["result"], "ок")


class TestNetScan(unittest.TestCase):
    def test_port_check_control_then_real(self):
        """КОНТРОЛЬ: закрытый порт не должен находиться. Потом — настоящий."""
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        open_port = srv.getsockname()[1]
        closed = socket.socket()                      # заведомо закрытый порт
        closed.bind(("127.0.0.1", 0))
        closed_port = closed.getsockname()[1]
        closed.close()
        try:
            found = net_scan.scan_ports_fast("127.0.0.1", [open_port, closed_port], timeout=1.0)
            self.assertFalse(found[closed_port], "контроль: закрытый порт найден — проверка врёт")
            self.assertTrue(found[open_port], "открытый порт не найден")
        finally:
            srv.close()

    def test_tcp_alive_control_then_real(self):
        """КОНТРОЛЬ: закрытый порт — не живость. Потом настоящий слушатель."""
        srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
        port = srv.getsockname()[1]
        dead = socket.socket(); dead.bind(("127.0.0.1", 0))
        dead_port = dead.getsockname()[1]; dead.close()
        try:
            self.assertFalse(net_scan.tcp_alive("127.0.0.1", ports=[dead_port], timeout=1.0))
            self.assertTrue(net_scan.tcp_alive("127.0.0.1", ports=[port], timeout=1.0))
        finally:
            srv.close()

    def test_local_cidr_is_private(self):
        cidr = net_scan.local_cidr()
        if cidr:                                       # машина без сети — не вина кода
            self.assertTrue(scope.check_cidr(cidr, config.load())[0], f"своя подсеть {cidr} должна быть разрешена")


class TestAuditAndAgent(unittest.TestCase):
    def test_audit_tail(self):
        """Журнал — во ВРЕМЕННОЙ папке: тест не пишет в живой журнал оператора.

        Раньше писал: «test.event» ложился в data/logs рядом с настоящими событиями
        организма, и оператор видел их в окне Journal (та же болезнь, что мусор в
        очереди — тесты пачкали живые данные).
        """
        with tempfile.TemporaryDirectory() as tmp:
            saved = (audit.LOG_DIR, audit.JSONL, audit.PLAIN)
            audit.LOG_DIR = Path(tmp)
            audit.JSONL = Path(tmp) / "audit.jsonl"
            audit.PLAIN = Path(tmp) / "synapse.log"
            try:
                rec = audit.log("test.event", note="проверка")
                tail = audit.tail(20)
            finally:
                audit.LOG_DIR, audit.JSONL, audit.PLAIN = saved
        self.assertTrue(any(r.get("note") == "проверка" for r in tail))
        self.assertIn("at", rec)

    def test_tick_offline_and_unknown_mission(self):
        """Организм не падает: мусорное имя в очереди — отказ с записью, не крах.

        Строку с посторонним именем кладём в очередь РУКАМИ (как если бы файл правили
        в блокноте или он остался от прежней версии): дверь add() такое имя теперь не
        пропускает, а путь отказа у организма обязан остаться рабочим. Очередь берём
        во ВРЕМЕННОЙ папке — тесты не имеют права пачкать рабочую очередь оператора
        (ровно так 39 записей с «такого_нет» и попали в живые данные).
        """
        syn = quiet_synapse()
        with temp_live():                            # очередь, журнал и state — в temp
            missions.QUEUE.write_text(json.dumps([{
                "id": "m-manual-1", "kind": "такого_нет", "params": {},
                "note": "тест отказа", "created": "2026-09-25T00:00:00",
                "status": "queued", "result": None, "finished": None}],
                ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):      # дверь: такое имя не пропускаем
                missions.add("такого_нет", {})
            status, text = syn.handle({"id": "m-manual-1", "kind": "такого_нет", "params": {}})
            row = json.loads(missions.QUEUE.read_text(encoding="utf-8"))[0]
        self.assertEqual(status, "refused")
        self.assertEqual(row["status"], "refused", "отказ не записан в очередь")
        # фраза берётся ИЗ СЛОВАРЯ — значит идёт за языком интерфейса, а не зашита в код
        self.assertEqual(text, i18n.t("ag.no_tool", kind="такого_нет",
                                      kinds=", ".join(missions.KINDS)))
        with temp_live():               # и этот такт: журнал и state — во временные
            out = syn.tick(with_mission=False)
        # «nobrain» добавлен v0.25.0: молчащий мозг теперь НАЗВАН, а не спрятан за
        # «idle». Прежний список закреплял молчание — тот самый случай, когда оператор
        # читал журнал и не мог понять, почему существо ничего не делает.
        self.assertIn(out["status"], ("idle", "asleep", "nobrain"))
        self.assertGreater(out["tick"], 0)

    def test_status_shape(self):
        s = quiet_synapse().status()
        for key in ("name", "body", "brain", "state", "queued"):
            self.assertIn(key, s)


class TestBrainWithBody(unittest.TestCase):
    """Мозг и тело вместе: такт сам поднимает свой мозг, если он включён.

    КОНТРОЛЬ: при mode=off сервер не трогаем — иначе тест зелёный на любом коде.
    """

    class _Stub:
        def __init__(self, mode="server", calls=None):
            self._mode, self.calls = mode, (calls if calls is not None else [])

        def mode(self):
            return self._mode

        def ensure(self, on_note=None):
            self.calls.append("ensure")
            return True

        def available(self, timeout=3.0, ttl=2.0):   # мозг не отвечает — такт не падает
            return False

        def think(self, prompt, system="", history=None):
            return None

    def _tick_with(self, mode):
        calls = []
        with temp_live():        # такт пишет в журнал и state — только во временные
            syn = Synapse(cfg=config.load(), brain=self._Stub(mode, calls))
            syn.tick(with_mission=False)
        return calls

    def test_server_mode_raises_brain(self):
        self.assertEqual(self._tick_with("server"), ["ensure"])

    def test_off_mode_leaves_brain_alone(self):
        self.assertEqual(self._tick_with("off"), [])
class TestExtraListeners(unittest.TestCase):
    """Два сервера на одном порту — это двойная память и ответы «вперемешку».

    Живой прогон 24.09 подтвердил: Windows пускает двоих на один порт
    (SO_REUSEADDR), и на 11435 стояли ДВА llama-server сразу, каждый со своей
    копией модели (~600 МБ лишних). Поэтому сервер обязан видеть слушателей порта.
    """

    def _brain(self, port):
        cfg = {"brain": {"server_port": port, "mode": "off", "local_dir": "models/brain",
                         "endpoint": f"http://127.0.0.1:{port}/v1/chat/completions"}}
        from synapse.brain import Brain
        return Brain(cfg)

    def test_sees_who_listens_on_our_port(self):
        import socket
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        try:
            port = srv.getsockname()[1]
            # Таблица TCP у Windows заполняется не мгновенно: замер 30.09.2026 — один
            # прогон из трёх видел пусто СРАЗУ после listen(), а через доли секунды
            # слушателя находил. Ждём появления, а не гадаем (иначе тест врёт).
            deadline = time.time() + 3.0
            pids = []
            while time.time() < deadline:
                pids = self._brain(port).listeners()
                if os.getpid() in pids:
                    break
                time.sleep(0.1)
            self.assertIn(os.getpid(), pids,
                          f"свой слушатель на {port} не найден: {pids}")
            self.assertEqual(self._brain(port + 1).listeners()[:1], [],
                             "на пустом порту слушателей быть не должно")
        finally:
            srv.close()

    def test_does_not_kill_a_stranger(self):
        """КОНТРОЛЬ: на нашем порту чужой процесс (это питон, не llama-server) — не убиваем."""
        import socket
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        try:
            b = self._brain(srv.getsockname()[1])
            self.assertEqual(b.stop_extra_listeners(), [],
                             "по чужому pid гасить процесс нельзя — сверяем имя процесса")
            self.assertTrue(srv.fileno() > 0, "слушатель должен остаться жив")
        finally:
            srv.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
