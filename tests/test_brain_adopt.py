"""v0.20.2: СЕРВЕР НА НАШЕМ ПОРТУ — НЕ ВСЕГДА НАШ.

Живой случай 26.09.2026, найден замером (/props живого сервера): на порту 11435
сидел СИРОТА 0.5b (pid 5412, прошлая сессия, pid-файла нет), а оператор в настройках
поставил 1.5b. Существо отвечало сиротой — выбор оператора не работал ВООБЩЕ, и это
выглядело как «ничего не изменилось, ничего не отвечает».

  * adopt() — признать живого сервера своим; чужую модель не принимать за свою;
  * сироту без pid-файла stop_server() теперь тоже может погасить;
  * модель сервера видна в состоянии (оператор видел ровно это и не понимал).

КОНТРОЛЬ первым: без знания модели мы НЕ гадаем (props молчит → mismatch False).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import audit, config                       # noqa: E402
from synapse import brain as brain_mod                  # noqa: E402
from synapse.brain import Brain                         # noqa: E402
from live_data import temp_live                         # noqa: E402

WANT = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
HAVE = "qwen2.5-0.5b-instruct-q4_k_m.gguf"


def _stub_server(status: str = "200 OK", body: bytes = b"{}"):
    """Крошечный HTTP-сервер в отдельном потоке: проверяем ПРОБУ, а не мечты."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):                                  # noqa: N802 — имя из stdlib
            self.send_response(int(status.split()[0]))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass          # проба закрывает сокет сразу после строки статуса

        def log_message(self, *a):                         # тишина в выводе тестов
            return

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return port


def _cfg(model=WANT):
    return {"brain": {"model": model, "server_port": 11435,
                      "endpoint": "http://127.0.0.1:11435/v1/chat/completions",
                      "local_dir": "models/brain"}}


class StubBrain(Brain):
    """Мозг без сети: сам решает, что отвечает порт и кто его слушает."""

    def __init__(self, props=None, listeners=(), ours=True, model=WANT):
        super().__init__(_cfg(model))
        self._props_data = props
        self._listeners = list(listeners)
        self._ours = ours
        self.killed = []

    def props(self, timeout=3.0):
        return self._props_data

    def listeners(self):
        return list(self._listeners)

    def _is_our_server(self, pid):
        return self._ours

    def _kill_pid(self, pid):
        self.killed.append(pid)

    def available(self, timeout=3.0, ttl=2.0):
        return self._props_data is not None


class TestMismatch(unittest.TestCase):
    def setUp(self):
        config.forget_cache()

    def test_control_silent_server_means_no_guess(self):
        """КОНТРОЛЬ: сервер промолчал — «модель другая» НЕ выдумываем."""
        b = StubBrain(props=None)
        self.assertFalse(b.model_mismatch(), "без знания модели догадка недопустима")
        self.assertIsNone(b.running_model())

    def test_other_model_is_seen_as_stale(self):
        b = StubBrain(props={"model_path": "D:/m/" + HAVE})
        self.assertEqual(b.running_model(), HAVE)
        self.assertTrue(b.model_mismatch())

    def test_same_model_is_ours(self):
        b = StubBrain(props={"model_path": "D:/m/" + WANT})
        self.assertFalse(b.model_mismatch())


class TestPropsUrl(unittest.TestCase):
    """КОНТРОЛЬ/ФАКТ: /props живёт в КОРНЕ сервера, а не под /v1.

    Это поймал живой прогон 26.09.2026: я запросила /v1/props, получила 404,
    running_model() вернул None — и сверка модели МОЛЧА не срабатывала (сирота с
    чужой моделью прошёл бы за своего). Здесь адрес проверяется по факту запроса.
    """

    def test_props_is_requested_at_server_root(self):
        import json as _json
        import urllib.request as _u

        from synapse import brain as brain_mod

        seen = []

        class FakeResp:
            status = 200

            def read(self):
                return _json.dumps({"model_path": "D:/m/" + WANT}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        real = _u.urlopen

        def fake(req, timeout=None):
            seen.append(req.full_url if hasattr(req, "full_url") else str(req))
            return FakeResp()

        _u.urlopen = fake
        try:
            b = StubBrain(props={"model_path": "D:/m/" + WANT})
            b.props = brain_mod.Brain.props.__get__(b, type(b))
            got = b.props()
        finally:
            _u.urlopen = real
        self.assertEqual(seen, ["http://127.0.0.1:11435/props"],
                         "спрашивать надо КОРЕНЬ сервера, а не /v1/props")
        self.assertEqual(got.get("model_path"), "D:/m/" + WANT)


class TestProbeIsCheap(unittest.TestCase):
    """ПРОБА МОЗГА ИЗ ОКНА ОБЯЗАНА БЫТЬ ДЕШЁВОЙ (v0.20.2).

    Найдено разбором жалобы «очень тормозит и ничего не отвечает»: на этой машине
    соединение с ЗАКРЫТЫМ портом не отказывает, а молчит до конца таймаута — проба
    из главного потока окна стоила 2.05 с (urllib с таймаутом 3 с), и панель замирала.
    Теперь проба — один connect с коротким таймаутом и минимальный GET.
    """

    def test_control_default_probe_timeout_is_small(self):
        self.assertLessEqual(brain_mod.PROBE_TIMEOUT, 0.5,
                             "таймаут пробы вырос — окно снова начнёт замирать")

    def test_closed_port_costs_almost_nothing(self):
        """ЗАМЕР: проба закрытого порта дешевле секунды (было 2.05 с).

        Порт берём СВОБОДНЫЙ (bind на 0 и сразу закрываем), а не «порт существа»:
        26.09.2026 прогон стал красным только потому, что у оператора был включён
        мозг на 11435 — тест мерил живой сервер и называл это закрытым портом.
        Урок тот же, что с чтением живого конфига: тест создаёт своё состояние сам.
        """
        import socket as _s
        import time as _t
        probe_sock = _s.socket()
        probe_sock.bind(("127.0.0.1", 0))
        free_port = probe_sock.getsockname()[1]
        probe_sock.close()
        b = Brain({"brain": {"model": WANT, "server_port": free_port,
                             "endpoint": f"http://127.0.0.1:{free_port}/v1/chat/completions",
                             "local_dir": "models/brain"}})
        t0 = _t.time()
        ok = b._probe()
        dt = _t.time() - t0
        self.assertFalse(ok, "на закрытом порту сервер не может отвечать")
        self.assertLess(dt, 1.0, f"проба закрытого порта стоит {dt:.2f} с — окно замирает")

    def test_live_stub_server_answers_true(self):
        port = _stub_server(status="200 OK")
        b = Brain({"brain": {"model": WANT, "server_port": port,
                             "endpoint": f"http://127.0.0.1:{port}/v1/chat/completions"}})
        self.assertTrue(b.available(ttl=0.0), "живой сервер обязан опознаваться")

    def test_control_server_that_is_not_200_is_not_alive(self):
        port = _stub_server(status="404 Not Found")
        b = Brain({"brain": {"model": WANT, "server_port": port,
                             "endpoint": f"http://127.0.0.1:{port}/v1/chat/completions"}})
        self.assertFalse(b.available(ttl=0.0), "ответ не 200 — сервер не наш")


class TestAdopt(unittest.TestCase):
    def test_control_no_server_no_kill(self):
        with temp_live():
            b = StubBrain(props=None, listeners=(5412,))
            self.assertFalse(b.adopt())
            events = [r.get("event") for r in audit.tail(20)]
        self.assertEqual(b.killed, [], "гасить некого — сервера нет")
        self.assertNotIn("brain.server.orphan_killed", events)

    def test_stale_orphan_is_killed_and_not_adopted(self):
        with temp_live():
            b = StubBrain(props={"model_path": "D:/m/" + HAVE}, listeners=(5412,))
            ok = b.adopt()
            events = [r.get("event") for r in audit.tail(20)]
        self.assertFalse(ok, "чужую модель нельзя принять за свою")
        self.assertEqual(b.killed, [5412], "сирота с чужой моделью должен быть убран")
        self.assertIn("brain.server.stale", events)

    def test_matching_foreign_server_is_adopted_and_remembered(self):
        with temp_live():
            b = StubBrain(props={"model_path": "D:/m/" + WANT}, listeners=(777,))
            ok = b.adopt()
            pid_file = b.pid_file.read_text(encoding="utf-8").strip()
            events = [r.get("event") for r in audit.tail(20)]
        self.assertTrue(ok)
        self.assertEqual(b.killed, [], "свой сервер гасить нельзя")
        self.assertEqual(pid_file, "777", "pid чужого (но подходящего) сервера надо запомнить")
        self.assertIn("brain.server.adopted", events)


class TestStopWithoutPidFile(unittest.TestCase):
    def test_orphan_without_pid_file_is_stopped(self):
        with temp_live():
            b = StubBrain(props=None, listeners=(5412,))
            b.stop_server()
            events = [r.get("event") for r in audit.tail(20)]
        self.assertEqual(b.killed, [5412], "«выключить мозг» обязан гасить сироту без pid-файла")
        self.assertIn("brain.server.stopped", events)

    def test_control_stop_does_not_touch_foreign_processes(self):
        with temp_live():
            b = StubBrain(props=None, listeners=(111, 222), ours=False)
            b.stop_server()
        self.assertEqual(b.killed, [], "по чужому pid нельзя гасить произвольный процесс")


if __name__ == "__main__":
    unittest.main()
