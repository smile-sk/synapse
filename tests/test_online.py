"""v0.30.0: облачный мозг — режимы, бюджет, маска адресов, честный отказ.

Слово Стаса 30.09.2026: «давай делать подключение облачной модели, начнём с gemini —
причём давай сделаем выбор режимов — только облако, только локальный и локальный +
облако… сделать нужно чтоб OpenAI совместимые в будущем можно было тот же groq к
примеру подключить». И отдельно про ключ: «у гугла достаточно ключа (у нас в AI_UI
почему то так — урл как в OpenAI совместимых не указан в окне провайдерах в настройках)?»

ЧТО ЗДЕСЬ ДОКАЗЫВАЕТСЯ:

  БЛОК A. Каталог и готовность — адрес и модель по имени провайдера; «нет ключа»,
    «нет адреса», «нет модели» и «лимит» — это РАЗНЫЕ причины, и они названы кодом.
  БЛОК B. Маска: публичный адрес уезжает как «внешний-IP», своя сеть остаётся, и
    повтор того же адреса даёт то же имя (иначе модель перестала бы различать узлы).
  БЛОК C. Бюджет: расход считается по факту (usage провайдера), сутки сменяются,
    а на исчерпанном потолке запрос НЕ УХОДИТ вовсе (сеть в тесте падает с ошибкой).
  БЛОК D. Сам запрос: ответ, счёт токенов, ошибка http, ошибка сети, пустой ответ.
  БЛОК E. Развод путей в brain: offline/none, только облако (молчит вместе с ним),
    hybrid (облако, при отказе — свой сервер), local (облако не зовём вообще).
  БЛОК F. Настройки: мусор не принимается, правка доезжает до config.json, окно
    показывает источник мысли и НЕ показывает сам ключ.

Живой ключ в тестах НЕ используется: сеть подменяется, поэтому прогон бесплатный и
работает без интернета.

Запуск: python tests/test_online.py   (или pytest tests)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from synapse import audit, brain as brain_mod, config, creds, i18n, online   # noqa: E402
from synapse.brain import Brain                                             # noqa: E402
from synapse.panel_core import PanelCore                                    # noqa: E402
from live_data import temp_live                                            # noqa: E402


# ---------- стенд ----------

class FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_open(payload: dict, seen: dict | None = None):
    """Подмена urlopen: НИ ОДНОГО настоящего запроса, но тело видно целиком."""
    def _open(req, timeout=None):
        if seen is not None:
            seen["url"] = getattr(req, "full_url", str(req))
            seen["body"] = json.loads(req.data.decode("utf-8"))
            seen["headers"] = dict(getattr(req, "headers", {}) or {})
        return FakeResp(payload)
    return _open


def boom(*_a, **_k):
    raise AssertionError("сеть не должна вызываться вовсе")


def _http_err(code: int, text: str):
    import io as _io
    return urllib.error.HTTPError("http://x", code, "err", {}, _io.BytesIO(text.encode()))


def boom_http(code: int = 404, text: str = '{"error": {"message": "no longer available"}}'):
    """Ответ провайдера с ошибкой — как живой 404 на снятую с обслуживания модель."""
    def _open(req, timeout=None):
        raise _http_err(code, text)
    return _open


def router(cloud_payload=None, seen: list | None = None, net_error: bool = False):
    """Развод по АДРЕСУ: облако получает ответ, свой сервер — отказ.

    Нужен там, где надо доказать, КУДА ушёл запрос. Патчим urllib.request.urlopen —
    это ТОТ ЖЕ объект, что видит brain (модуль urllib.request один на процесс),
    поэтому различаем не по вызывающему, а по адресу в самом запросе.
    """
    payload = cloud_payload or {"choices": [{"message": {"content": "ок"}}], "usage": {}}

    def _open(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if seen is not None:
            seen.append(url)
        if "127.0.0.1" in url or "localhost" in url:
            raise urllib.error.URLError("свой сервер молчит")
        if net_error:
            raise urllib.error.URLError("no route to host")
        return FakeResp(payload)
    return _open


def boom_net():
    def _open(req, timeout=None):
        raise urllib.error.URLError("no route to host")
    return _open


def audit_events(name: str = "") -> list[dict]:
    out = []
    try:
        for line in Path(audit.JSONL).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not name or str(rec.get("event")) == name:
                out.append(rec)
    except OSError:
        pass
    return out


class Stand(unittest.TestCase):
    """Песочница: и файлы данных, и файл ключей — во временной папке."""

    def setUp(self):
        self._live = temp_live()
        self._live.__enter__()
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg_path = Path(self._tmp.name) / "config.json"
        self._keep_cfg = config.CONFIG_PATH
        config.CONFIG_PATH = self.cfg_path
        config.forget_cache()

    def tearDown(self):
        config.CONFIG_PATH = self._keep_cfg
        config.forget_cache()
        self._live.__exit__(None, None, None)
        self._tmp.cleanup()

    # --- помощники ---

    def put_key(self, provider: str, key: str = "test-key-123456", field: str = "key"):
        creds.CREDS.parent.mkdir(parents=True, exist_ok=True)
        data = creds.load()
        data[provider] = {field: key}
        creds.CREDS.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def brain_cfg(self, mode="local", provider="gemini", **extra):
        """РАЗДЕЛ brain: его ждут функции online.* (они читают настройки, не весь файл)."""
        return {"mode": "server",
                "endpoint": "http://127.0.0.1:11435/v1/chat/completions",
                "model": "qwen2.5-0.5b.gguf",
                "online": {"mode": mode, "provider": provider,
                           "daily_tokens": 200000, **extra}}

    def full_cfg(self, mode="local", provider="gemini", **extra):
        """ЦЕЛЫЙ config.json: Brain берёт раздел brain из него сам — как приложение."""
        return {"brain": self.brain_cfg(mode=mode, provider=provider, **extra)}

    def make_brain(self, mode="local", **extra) -> Brain:
        """Мозг БЕЗ своего сервера: проба локального порта всегда «молчит»."""
        b = Brain(self.full_cfg(mode=mode, **extra))
        b._probe = lambda *a, **k: False
        b._avail = None
        return b

# ---------- БЛОК A: каталог и готовность ----------

class TestCatalogAndReadiness(Stand):
    def test_openai_compatible_catalog(self):
        """У всех один протокол — провайдер это строчка, а не ветка кода."""
        self.assertIn("gemini", online.PROVIDERS)
        self.assertIn("groq", online.PROVIDERS)
        self.assertIn("custom", online.PROVIDERS)
        gem = online.PROVIDERS["gemini"]
        self.assertTrue(gem["base_url"].startswith("https://generativelanguage.googleapis.com"),
                        "gemini должен ходить через OpenAI-совместимый endpoint")
        self.assertTrue(gem["base_url"].endswith("/openai"))
        self.assertIn("gemini-3.5-flash-lite", gem["models"],
                      "имена моделей — из живого замера 30.09.2026 (/openai/models)")

    def test_cloud_is_off_and_masked_by_default(self):
        """Умолчание — ЛОКАЛЬНЫЙ мозг: пока оператор не переключил, наружу не уходит ничего."""
        d = config.DEFAULTS["brain"]["online"]
        self.assertEqual(d["mode"], "local")
        self.assertTrue(d["mask"], "маска адресов должна стоять включённой по умолчанию")
        self.assertGreater(int(d["daily_tokens"]), 0)

    def test_garbage_mode_reads_as_local(self):
        self.assertEqual(online.mode({"online": {"mode": "nonsense"}}), "local")
        self.assertEqual(online.mode({}), "local")
        self.assertEqual(online.mode({"online": {"mode": "Hybrid"}}), "hybrid")

    def test_reasons_are_different_codes(self):
        """«Нет ключа», «нет адреса», «нет модели» — РАЗНЫЕ причины, а не одно «не работает»."""
        block = {"online": {"mode": "online", "provider": "gemini"}}
        self.assertEqual(online.why_not(block), "nokey")
        self.put_key("gemini")
        self.put_key("custom")                       # ключ есть, а адреса у custom нет
        block = {"online": {"mode": "online", "provider": "custom"}}
        self.assertEqual(online.why_not(block), "nourl")
        block = {"online": {"mode": "online", "provider": "custom",
                            "base_url": "http://127.0.0.1:9/v1", "model": ""}}
        self.assertEqual(online.why_not(block), "nomodel")
        block["online"]["model"] = "whatever"
        self.assertEqual(online.why_not(block), "")
        self.assertTrue(online.ready(block))

    def test_key_is_read_under_any_spelling(self):
        """Google зовёт его api_key, часть шлюзов — token: принимаем все написания."""
        for field in ("key", "api_key", "token"):
            self.put_key("gemini", key=f"secret-{field}", field=field)
            self.assertEqual(online.key({"online": {"provider": "gemini"}}), f"secret-{field}")
        creds.CREDS.write_text("{}", encoding="utf-8")
        self.assertEqual(online.key({"online": {"provider": "gemini"}}), "")

    def test_left_is_unknown_without_ceiling(self):
        self.assertEqual(online.left({"online": {"daily_tokens": 0}}), -1)


# ---------- БЛОК B: маска ----------

class TestMask(Stand):
    def test_public_goes_out_named_and_private_stays(self):
        text = ("соединения: внешний узел 203.0.113.10:22, свой роутер 192.168.1.1:80, "
                "петля 127.0.0.1:11435, второй внешний 8.8.8.8:443")
        out = online.mask(text)
        self.assertNotIn("203.0.113.10", out)
        self.assertNotIn("8.8.8.8", out)
        self.assertIn("внешний-IP", out)
        self.assertIn("внешний-IP-2", out)
        self.assertIn("192.168.1.1", out, "своя сеть обязана остаться: иначе разбор слепнет")
        self.assertIn("127.0.0.1", out)
        self.assertIn(":22", out, "порт — не адрес, его терять нельзя")

    def test_same_address_same_name(self):
        """Повтор адреса — ТО ЖЕ имя: иначе модель не смогла бы сопоставить строки отчёта."""
        self.assertEqual(online.mask("1.2.3.4 и 5.6.7.8 и снова 1.2.3.4"),
                         "внешний-IP и внешний-IP-2 и снова внешний-IP")

    def test_garbage_address_is_left_alone(self):
        self.assertEqual(online.mask("999.1.1.1 и 88.1"), "999.1.1.1 и 88.1")


# ---------- БЛОК C: бюджет ----------

class TestBudget(Stand):
    def test_spend_counts_and_persists(self):
        online.spend(100, 40)
        online.spend(10, 5)
        self.assertEqual(online.used(), 155)
        self.assertEqual(online.budget()["calls"], 2)
        self.assertTrue(Path(online.BUDGET_FILE).exists(), "бюджет должен лежать файлом")

    def test_yesterday_does_not_count(self):
        Path(online.BUDGET_FILE).write_text(
            json.dumps({"day": "2020-01-01", "tokens_in": 999999, "tokens_out": 0, "calls": 9}),
            encoding="utf-8")
        self.assertEqual(online.used(), 0, "сутки сменились — счёт начинается заново")
        self.assertEqual(online.budget()["calls"], 0)

    def test_spent_ceiling_stops_the_request(self):
        """Потолок уже выбран — запрос не уходит: сеть в тесте падает, если её тронули."""
        self.put_key("gemini")
        block = self.brain_cfg(mode="online", daily_tokens=50)
        online.spend(40, 20)
        real = online.urllib.request.urlopen
        online.urllib.request.urlopen = boom
        try:
            res = online.ask(block, "sys", "prompt")
        finally:
            online.urllib.request.urlopen = real
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "limit")

    def test_request_bigger_than_the_ceiling_is_not_sent(self):
        """ОЦЕНКА ДО ОТПРАВКИ: если сам запрос больше потолка — не шлём и говорим вслух."""
        self.put_key("gemini")
        block = self.brain_cfg(mode="online", daily_tokens=100)   # потолок меньше запроса
        real = online.urllib.request.urlopen
        online.urllib.request.urlopen = boom
        try:
            res = online.ask(block, "система", "вопрос")
        finally:
            online.urllib.request.urlopen = real
        self.assertEqual(res["error"], "limit")
        ev = audit_events("brain.online.limit")
        self.assertTrue(ev, "о лимите должна быть запись в журнале")
        self.assertGreater(int(ev[-1].get("need") or 0), 100)

    def test_calls_ceiling_is_honest(self):
        self.put_key("gemini")
        block = self.brain_cfg(mode="online", max_calls=2)
        online.spend(1, 1)
        online.spend(1, 1)
        self.assertEqual(online.why_not(block), "calls")
        self.assertEqual(online.left(block), 200000 - 4)


# ---------- БЛОК D: сам запрос ----------

class TestAsk(Stand):
    def test_answer_and_usage_are_counted(self):
        self.put_key("gemini")
        seen: dict = {}
        payload = {"choices": [{"message": {"content": "  держусь, хозяин  "}}],
                   "usage": {"prompt_tokens": 111, "completion_tokens": 22}}
        real = online.urllib.request.urlopen
        online.urllib.request.urlopen = fake_open(payload, seen)
        try:
            res = online.ask(self.brain_cfg(mode="online"), "система", "вопрос")
        finally:
            online.urllib.request.urlopen = real
        self.assertTrue(res["ok"])
        self.assertEqual(res["text"], "держусь, хозяин", "хвосты пробелов срезаем")
        self.assertEqual((res["tokens_in"], res["tokens_out"]), (111, 22))
        self.assertEqual(online.used(), 133, "счёт — по usage провайдера, а не по нашим догадкам")
        # ключ уходит заголовком, а не в теле запроса
        self.assertIn("/chat/completions", seen["url"])
        self.assertTrue(any(k.lower() == "authorization" for k in seen["headers"]),
                        "ключ должен идти заголовком Authorization")
        ev = audit_events("brain.online.ask")
        self.assertTrue(ev and ev[-1].get("tokens_in") == 111)

    def test_public_addresses_do_not_leave_the_house(self):
        """Тело запроса смотрим ЦЕЛИКОМ: публичного адреса в нём быть не должно."""
        self.put_key("gemini")
        seen: dict = {}
        payload = {"choices": [{"message": {"content": "ок"}}], "usage": {}}
        real = online.urllib.request.urlopen
        online.urllib.request.urlopen = fake_open(payload, seen)
        try:
            online.ask(self.brain_cfg(mode="online"), "наша сеть 192.168.1.0/24",
                       "на VPS 203.0.113.10 открыт порт 22")
        finally:
            online.urllib.request.urlopen = real
        sent = json.dumps(seen["body"], ensure_ascii=False)
        self.assertNotIn("203.0.113.10", sent, "публичный адрес уехал в облако")
        self.assertIn("внешний-IP", sent, "вместо адреса должно быть его имя")
        self.assertIn("192.168.1.0/24", sent, "своя сеть нужна модели для разбора")

    def test_mask_off_sends_as_is(self):
        self.put_key("gemini")
        seen: dict = {}
        payload = {"choices": [{"message": {"content": "ок"}}], "usage": {}}
        real = online.urllib.request.urlopen
        online.urllib.request.urlopen = fake_open(payload, seen)
        try:
            online.ask(self.brain_cfg(mode="online", mask=False), "s", "хост 203.0.113.10")
        finally:
            online.urllib.request.urlopen = real
        self.assertIn("203.0.113.10", json.dumps(seen["body"], ensure_ascii=False),
                      "с выключенной маской адрес уходит как есть — это выбор оператора")

    def test_http_error_is_named_with_code(self):
        self.put_key("gemini")
        real = online.urllib.request.urlopen
        online.urllib.request.urlopen = boom_http(404)
        try:
            res = online.ask(self.brain_cfg(mode="online"), "s", "p")
        finally:
            online.urllib.request.urlopen = real
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "http")
        self.assertEqual(res["code"], 404)
        self.assertEqual(online.used(), 0, "неудавшийся запрос бюджет не тратит")
        self.assertTrue(audit_events("brain.online.error"))

    def test_network_error_is_not_a_crash(self):
        self.put_key("gemini")
        real = online.urllib.request.urlopen
        online.urllib.request.urlopen = boom_net()
        try:
            res = online.ask(self.brain_cfg(mode="online"), "s", "p")
        finally:
            online.urllib.request.urlopen = real
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "net")

    def test_empty_answer_is_not_an_answer(self):
        self.put_key("gemini")
        real = online.urllib.request.urlopen
        online.urllib.request.urlopen = fake_open(
            {"choices": [{"message": {"content": "  "}}], "usage": {"prompt_tokens": 7}})
        try:
            res = online.ask(self.brain_cfg(mode="online"), "s", "p")
        finally:
            online.urllib.request.urlopen = real
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "empty")
        self.assertEqual(online.used(), 8,
                         "за пустой ответ провайдер тоже считает: 7 входа + 1 минимум выхода")

    def test_no_key_means_no_request_at_all(self):
        real = online.urllib.request.urlopen
        online.urllib.request.urlopen = boom
        try:
            res = online.ask(self.brain_cfg(mode="online"), "s", "p")
        finally:
            online.urllib.request.urlopen = real
        self.assertEqual(res["error"], "nokey")


# ---------- БЛОК E: развод путей в brain ----------

class TestRoute(Stand):
    def test_local_never_calls_the_cloud(self):
        b = self.make_brain("local")
        self.assertEqual(b.route(), "local")
        real = online.urllib.request.urlopen
        online.urllib.request.urlopen = boom
        try:
            self.assertIsNone(b.think("есть кто?"), "локального сервера нет — ответа нет")
        finally:
            online.urllib.request.urlopen = real

    def test_off_means_off_for_every_source(self):
        b = Brain({"brain": {"mode": "off", "online": {"mode": "online"}}})
        self.put_key("gemini")
        self.assertEqual(b.route(), "none")
        self.assertIsNone(b.think("есть кто?"))

    def test_online_without_key_is_none_not_local(self):
        b = self.make_brain("online")
        self.assertEqual(b.route(), "none")
        self.assertFalse(b.available(), "без ключа мозг не «жив» — и это честно")

    def test_online_uses_only_cloud(self):
        self.put_key("gemini")
        b = self.make_brain("online")
        self.assertEqual(b.route(), "online")
        self.assertTrue(b.available(), "готовое облако делает мозг живым без своего сервера")
        real = online.urllib.request.urlopen
        online.urllib.request.urlopen = fake_open(
            {"choices": [{"message": {"content": "мысли из облака"}}],
             "usage": {"prompt_tokens": 5, "completion_tokens": 3}})
        try:
            self.assertEqual(b.think("что видишь?"), "мысли из облака")
        finally:
            online.urllib.request.urlopen = real
        self.assertFalse(audit_events("brain.answer"),
                         "в режиме «только облако» локальный путь звать нечем")

    def test_online_failure_is_silence_not_a_substitution(self):
        """Просьба «только облако»: облако молчит — существо молчит ТОЖЕ."""
        self.put_key("gemini")
        b = self.make_brain("online")
        real = online.urllib.request.urlopen
        online.urllib.request.urlopen = boom_net()
        try:
            self.assertIsNone(b.think("что видишь?"))
        finally:
            online.urllib.request.urlopen = real
        self.assertTrue(audit_events("brain.online.fail"))
        self.assertFalse(audit_events("brain.online.fallback"),
                         "в режиме «только облако» подмены локальным быть не должно")

    def test_hybrid_falls_back_to_local_and_says_so(self):
        self.put_key("gemini")
        b = self.make_brain("hybrid")
        self.assertEqual(b.route(), "online")
        real = online.urllib.request.urlopen
        online.urllib.request.urlopen = boom_net()
        try:
            self.assertIsNone(b.think("что видишь?"))     # локальный сервер тоже молчит
        finally:
            online.urllib.request.urlopen = real
        self.assertTrue(audit_events("brain.online.fallback"),
                        "откат должен быть записан в журнал, а не сделан молча")

    def test_hybrid_without_key_is_plain_local(self):
        b = self.make_brain("hybrid")
        self.assertEqual(b.route(), "local")
        self.assertIsNone(b.think("есть кто?"))

    def test_local_mode_goes_to_own_server_not_to_the_cloud(self):
        """Контроль: в локальном режиме запрос уходит на СВОЙ порт, а не в облако."""
        b = Brain(self.full_cfg(mode="local"))
        b._probe = lambda *a, **k: True                # «сервер жив» без сети
        b._avail = None
        seen: list = []
        real = online.urllib.request.urlopen
        online.urllib.request.urlopen = router(seen=seen)
        try:
            self.assertIsNone(b.think("привет"), "свой сервер молчит — ответа нет")
        finally:
            online.urllib.request.urlopen = real
        self.assertTrue(seen, "запрос вообще не ушёл")
        self.assertIn("127.0.0.1", seen[0])
        self.assertFalse(any("generativelanguage" in u for u in seen),
                         "в локальном режиме облако звать нечем")
        self.assertFalse(audit_events("brain.online.ask"))


# ---------- БЛОК F: настройки ----------

class TestSettings(Stand):
    def core(self) -> PanelCore:
        cfg = config.load()
        core = PanelCore.__new__(PanelCore)
        core.cfg = cfg
        core.brain = Brain(cfg)
        return core

    def test_switch_reaches_the_file(self):
        core = self.core()
        res = core.apply_settings({"brain_cloud": "online", "brain_provider": "groq",
                                   "brain_cloud_model": "openai/gpt-oss-120b",
                                   "brain_cloud_budget": "5000",
                                   "brain_cloud_mask": False})
        disk = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(disk["brain"]["online"]["mode"], "online")
        self.assertEqual(disk["brain"]["online"]["provider"], "groq")
        self.assertEqual(disk["brain"]["online"]["model"], "openai/gpt-oss-120b")
        self.assertEqual(int(disk["brain"]["online"]["daily_tokens"]), 5000)
        self.assertFalse(disk["brain"]["online"]["mask"])
        self.assertTrue(any(i18n.t("set.cloud.online") in c for c in res["changed"]),
                        "оператор должен видеть, ЧТО он поменял")
        self.assertEqual(core.brain.cloud_mode(), "online", "мозг перечитывает настройки сразу")

    def test_garbage_is_refused_and_old_value_stays(self):
        core = self.core()
        core.apply_settings({"brain_cloud": "online", "brain_provider": "groq"})
        res = core.apply_settings({"brain_cloud": "телепорт", "brain_provider": "не-провайдер"})
        disk = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(disk["brain"]["online"]["mode"], "online", "прежнее значение потеряно")
        self.assertEqual(disk["brain"]["online"]["provider"], "groq")
        self.assertEqual(len(res["errors"]), 2, "о мусоре надо сказать, а не проглотить")

    def test_settings_show_source_and_hide_the_key(self):
        core = self.core()
        st = core.settings()
        for k in ("brain_cloud", "brain_provider", "brain_providers", "brain_cloud_model",
                  "brain_cloud_budget", "brain_cloud_mask", "brain_cloud_key"):
            self.assertIn(k, st)
        self.assertFalse(st["brain_cloud_key"], "ключа ещё нет")
        self.put_key("gemini", key="top-secret-value")
        self.assertTrue(core.settings()["brain_cloud_key"], "ключ есть — окно должно это знать")
        info = core.cloud_key_info()
        self.assertTrue(info["has"])
        self.assertEqual(info["n"], len("top-secret-value"))
        self.assertNotIn("top-secret-value", json.dumps(info), "сам ключ в окно не попадает")

    def test_key_has_its_own_line_in_settings(self):
        """Окно говорит, ГДЕ ключ лежит — иначе оператор ищет его наугад."""
        self.put_key("gemini", key="abc12345")
        text = i18n.t("set.cloud.key_yes", path=str(creds.CREDS), provider="gemini", n=8)
        self.assertIn(str(creds.CREDS), text)
        self.assertIn("gemini", text)
        text_ru = i18n.t("set.cloud.key_no", path=str(creds.CREDS), provider="groq")
        self.assertIn("groq", text_ru)


class TestWindowShowsTheChoice(Stand):
    """Окно: выбор источника мысли есть, и он уезжает в файл тем же путём, что и всё."""

    def setUp(self):
        super().setUp()
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as exc:                       # нет Qt — честный пропуск
            self.skipTest(f"PySide6 недоступен: {type(exc).__name__}")
        from synapse.qt_panel import SynapseWindow
        self.app = QApplication.instance() or QApplication([])
        self.win = SynapseWindow()

    def test_three_modes_are_offered(self):
        codes = [self.win.cmb_cloud_mode.itemData(i)
                 for i in range(self.win.cmb_cloud_mode.count())]
        self.assertEqual(codes, ["local", "online", "hybrid"])
        self.assertTrue(self.win.chk_cloud_mask.isChecked(),
                        "маска должна быть включена по умолчанию и видна галкой")
        self.assertTrue(self.win.cmb_cloud_model.isEditable(),
                        "имя модели вписывается руками: у провайдера они меняются")

    def test_pick_cloud_and_save(self):
        idx = self.win.cmb_cloud_mode.findData("hybrid")
        self.win.cmb_cloud_mode.setCurrentIndex(idx)
        self.win._save_settings()
        disk = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(disk["brain"]["online"]["mode"], "hybrid")
        # Контроль: язык и «думать вслух» от нашей правки не поехали.
        self.assertIn(disk["ui"]["lang"], i18n.available())

    def test_brain_line_says_where_the_thought_comes_from(self):
        self.win.core.brain.set_cloud_mode("hybrid")
        self.win._brain_at = 0.0
        self.win._refresh_brain()
        line = self.win.lbl_brain.text()
        self.assertIn(i18n.t("set.cloud.hybrid"), line,
                      "на вкладке «Мозг» должно быть видно, что мысль из облака")


if __name__ == "__main__":
    unittest.main(verbosity=2)
