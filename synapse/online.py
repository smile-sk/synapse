"""Облачный мозг — сменный источник мысли, OpenAI-совместимый одним кодом.

Слово Стаса 30.09.2026: «давай делать подключение облачной модели, начнём с gemini —
причём давай сделаем выбор режимов — только облако, только локальный и локальный +
облако… сделать нужно чтоб OpenAI совместимые в будущем можно было тот же groq к
примеру подключить».

ПОЧЕМУ ОДИН КОД НА ВСЕХ. У Gemini есть OpenAI-совместимый endpoint
(https://generativelanguage.googleapis.com/v1beta/openai), у Groq — свой, и у обоих
протокол один и тот же: POST {base_url}/chat/completions с Authorization: Bearer.
ЖИВОЙ ЗАМЕР 30.09.2026 ключом Стаса из ai_ui: /v1beta/openai/models отдал 61 модель
(в т.ч. gemini-3.5-flash-lite), а chat/completions ответил за 0.85 с и вернул usage
{prompt 46, completion 21} — то есть ключа действительно ДОСТАТОЧНО, адрес известен
по имени провайдера. Поэтому провайдер — это строчка в настройках, а не ветка кода:
gemini, groq, openrouter, nvidia, deepseek, zai и «custom» (свой адрес руками).
Для gemini-2.5-flash-lite живой ответ был 404 «no longer available to new users» —
поэтому в каталоге стоят те имена, которые СЕЙЧАС отвечают, а поле модели в окне
редактируемое: имя можно вписать любое, не трогая код.

ГРАНИЦЫ ЭТОГО СЛОЯ (за что он отвечает и за что нет):
  * ПРОВАЙДЕР — адрес и модель по имени; ключ берётся из data/creds.json по тому же
    правилу, что логины узлов: пусто/бито = «доступа нет», а не выдуманный ключ;
  * БЮДЖЕТ — НАШ потолок, а не тариф провайдера (тарифа мы не знаем и не выдумываем):
    data/budget.json, сутки, вход+выход, плюс потолок числа обращений. Исчерпали —
    честно говорим «лимит» и НЕ отправляем запрос;
  * МАСКА — публичные адреса наружу не уезжают («внешний-IP», второй — «внешний-IP-2»);
    своя сеть остаётся как есть, иначе разбор «что это, откуда, вредное ли» теряет смысл;
  * ЧЕСТНОСТЬ — ошибка, код и причина отказа возвращаются наружу словарём. Решение
    «откатиться на локальный или молчать» принимает brain (brain.route), а не этот слой.
"""
from __future__ import annotations

import ipaddress
import json
import os
import platform
import re
import time
import urllib.error
import urllib.request

from synapse import audit, config, creds

#: Файл бюджета. Модульная переменная, а не вычисление на месте: тесты подменяют её
#: (песочница temp_live), чтобы прогон не тратил живой бюджет оператора.
BUDGET_FILE = config.DATA / "budget.json"

#: КОДЫ ПРИЧИН, по которым облако сейчас не может ответить. Отдельным списком, а не
#: россыпью литералов: по нему и окно выбирает слова (фраза собирается из кода), и
#: проверка словаря требует, чтобы у КАЖДОГО кода были слова на обоих языках, и
#: тесты строят ключи вида `cloud.why.<код>`. Один список — одно место правды.
WHY_CODES = ("nokey", "nourl", "nomodel", "limit", "calls")

#: Режимы мозга. Это ОДНА ось: откуда существо берёт мысль.
#:   local  — только свой сервер (как было до 30.09.2026, и это умолчание);
#:   online — только облако: локальный сервер не нужен вовсе, и если облако молчит,
#:            существо честно молчит тоже (НЕ подменяем ответ тихой подменой);
#:   hybrid — облако, когда оно готово и бюджет есть; иначе свой сервер.
MODES = ("local", "online", "hybrid")

#: Каталог провайдеров. Адрес и модель — по имени; models — подсказка в окне.
PROVIDERS = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-3.5-flash-lite",
        "models": ["gemini-3.5-flash-lite", "gemini-flash-lite-latest",
                   "gemini-3.7-flash", "gemini-3.8-flash"],
        "note": "free: Google учится на том, что ему послали (страница тарифов)",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        "models": ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3-27b"],
        "note": "free: 8000 токенов/мин, 200K/сутки, 1000 запросов/сутки",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "",
        "models": [],
        "note": "адрес OpenAI-совместимый, имя модели — из каталога OpenRouter",
    },
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "",
        "models": [],
        "note": "40 запросов/мин (лимит из ai_ui config/providers.json)",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "note": "",
    },
    "zai": {
        "base_url": "https://api.z.ai/api/paas/v4",
        "model": "",
        "models": [],
        "note": "",
    },
    # СВОЙ АДРЕС: любой OpenAI-совместимый (локальный сервер в другой папке, свой шлюз).
    "custom": {"base_url": "", "model": "", "models": [], "note": "адрес и модель вписывает оператор"},
}

#: Замер 30.09.2026 (llama-tokenize.exe, qwen2.5-1.5b): 638 знаков ru = 221 токен,
#: 1777 знаков = 675. То есть ~2.9 знака на токен. Этим числом оцениваем запрос
#: ДО отправки — чтобы потолок бюджета решался честно, а не «по факту».
CHARS_PER_TOKEN = 2.9

#: Сколько знаков ответа просить по умолчанию (настройкой brain.online.max_tokens).
DEFAULT_MAX_TOKENS = 500


def section(brain_cfg: dict | None) -> dict:
    """Раздел brain.online с умолчаниями. Читается на каждое обращение — правку
    настроек видно сразу, перезапуск не нужен."""
    cfg = brain_cfg if isinstance(brain_cfg, dict) else {}
    out = dict(cfg.get("online") or {})
    out.setdefault("mode", "local")
    out.setdefault("provider", "gemini")
    out.setdefault("base_url", "")
    out.setdefault("model", "")
    out.setdefault("daily_tokens", 200000)
    out.setdefault("max_calls", 400)
    out.setdefault("max_tokens", DEFAULT_MAX_TOKENS)
    out.setdefault("temperature", 0.3)
    out.setdefault("timeout", 60)
    out.setdefault("mask", True)
    return out


def mode(brain_cfg: dict | None) -> str:
    """Режим мысли. Мусор в настройках читаем как «только локальный» — честнее, чем
    гадать, чего хотел оператор (и ничего никуда не уезжает, пока не сказано прямо)."""
    m = str(section(brain_cfg).get("mode") or "local").strip().lower()
    return m if m in MODES else "local"


def provider(brain_cfg: dict | None) -> str:
    p = str(section(brain_cfg).get("provider") or "gemini").strip().lower()
    return p if p in PROVIDERS else "custom"


def provider_names() -> list[str]:
    return list(PROVIDERS.keys())


def catalog(prov: str) -> dict:
    return dict(PROVIDERS.get(str(prov or "").strip().lower()) or PROVIDERS["custom"])


def base_url(brain_cfg: dict | None) -> str:
    """Адрес endpoint'а БЕЗ /chat/completions: своё значение из настроек важнее каталога."""
    sec = section(brain_cfg)
    own = str(sec.get("base_url") or "").strip()
    return (own or str(catalog(provider(brain_cfg)).get("base_url") or "")).rstrip("/")


def model(brain_cfg: dict | None) -> str:
    sec = section(brain_cfg)
    own = str(sec.get("model") or "").strip()
    return own or str(catalog(provider(brain_cfg)).get("model") or "")


def key(brain_cfg: dict | None) -> str:
    """Ключ провайдера из data/creds.json (ключ: key | api_key | token)."""
    return creds.key(provider(brain_cfg))


def limit(brain_cfg: dict | None) -> int:
    try:
        return max(0, int(section(brain_cfg).get("daily_tokens") or 0))
    except (TypeError, ValueError):
        return 0


def max_calls(brain_cfg: dict | None) -> int:
    try:
        return max(0, int(section(brain_cfg).get("max_calls") or 0))
    except (TypeError, ValueError):
        return 0


# ---------- бюджет (наш собственный потолок, не тариф провайдера) ----------

def _today() -> str:
    return time.strftime("%Y-%m-%d")


def budget() -> dict:
    """Сегодняшний расход. День сменился — счёт начинается заново."""
    try:
        raw = json.loads(BUDGET_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict) or str(raw.get("day") or "") != _today():
        return {"day": _today(), "tokens_in": 0, "tokens_out": 0, "calls": 0}
    out = {"day": _today()}
    for k in ("tokens_in", "tokens_out", "calls"):
        try:
            out[k] = max(0, int(raw.get(k) or 0))
        except (TypeError, ValueError):
            out[k] = 0
    return out


def _save_budget(b: dict) -> None:
    try:
        BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = BUDGET_FILE.with_suffix(BUDGET_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, BUDGET_FILE)          # читатель не застанет половину файла
    except OSError:
        pass                                   # бюджет не повод падать: мозг важнее счёта


def spend(tokens_in: int, tokens_out: int) -> dict:
    """Записать расход и вернуть новое состояние счёта."""
    b = budget()
    b["tokens_in"] += max(0, int(tokens_in or 0))
    b["tokens_out"] += max(0, int(tokens_out or 0))
    b["calls"] += 1
    _save_budget(b)
    return b


def used(brain_cfg: dict | None = None) -> int:
    b = budget()
    return int(b["tokens_in"]) + int(b["tokens_out"])


def left(brain_cfg: dict | None) -> int:
    cap = limit(brain_cfg)
    return -1 if cap <= 0 else max(0, cap - used(brain_cfg))


# ---------- оценка и маска ----------

def estimate(texts) -> int:
    """Оценка токенов по нашим ЗАМЕРАМ (2.9 знака на токен). Не хрустальный шар,
    а тот же порядок величины: ошибка оценки видна в журнале рядом с настоящим usage."""
    if isinstance(texts, str):
        texts = [texts]
    n = sum(len(str(t or "")) for t in (texts or []))
    return max(1, int(round(n / CHARS_PER_TOKEN)))


_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def mask(text: str) -> str:
    """Публичные адреса — не наружу. Своя сеть (10/8, 172.16/12, 192.168/16, петля)
    остаётся как есть: без неё разбор соединений теряет смысл. Отображение
    устойчивое В ПРЕДЕЛАХ строки: один и тот же адрес — одно и то же имя,
    разные — разные, иначе модель перестала бы различать собеседников.
    """
    names: dict[str, str] = {}

    def sub(m: re.Match) -> str:
        raw = m.group(0)
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return raw                      # мусор вида 999.1.1.1 не трогаем
        if not isinstance(ip, ipaddress.IPv4Address):
            return raw
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
            return raw
        if raw not in names:
            names[raw] = "внешний-IP" if not names else f"внешний-IP-{len(names) + 1}"
        return names[raw]

    return _IPV4.sub(sub, str(text or ""))


# ---------- готовность ----------

def why_not(brain_cfg: dict | None) -> str:
    """Пусто — готов; иначе КОД причины: nokey | nourl | nomodel | limit | calls.
    Код, а не фраза: фразу подставляет интерфейс, а журнал должен быть машинным."""
    if not key(brain_cfg):
        return "nokey"
    if not base_url(brain_cfg):
        return "nourl"
    if not model(brain_cfg):
        return "nomodel"
    cap = limit(brain_cfg)
    if cap and used(brain_cfg) >= cap:
        return "limit"
    mc = max_calls(brain_cfg)
    if mc and int(budget()["calls"]) >= mc:
        return "calls"
    return ""


def ready(brain_cfg: dict | None) -> bool:
    return not why_not(brain_cfg)


def state(brain_cfg: dict | None) -> dict:
    """Всё, что окно должно показать про облако, одним словарём. Без сети: проба
    платного endpoint'а каждый кадр — это деньги и лишние запросы."""
    b = budget()
    cap = limit(brain_cfg)
    spent = used(brain_cfg)
    sec = section(brain_cfg)
    return {
        "mode": mode(brain_cfg),
        "provider": provider(brain_cfg),
        "providers": provider_names(),
        "model": model(brain_cfg),
        "models": list(catalog(provider(brain_cfg)).get("models") or []),
        "url": base_url(brain_cfg),
        "key": bool(key(brain_cfg)),
        "mask": bool(sec.get("mask", True)),
        "cap": cap,
        "spent": spent,
        "left": left(brain_cfg),
        "calls": int(b["calls"]),
        "ready": ready(brain_cfg),
        "why": why_not(brain_cfg),
    }


# ---------- сам запрос ----------

def _messages(system: str, prompt: str, history=None) -> list[dict]:
    msgs = [{"role": "system", "content": str(system or "")}]
    for m in (history or []):
        if (isinstance(m, dict) and m.get("role") in ("user", "assistant", "system")
                and m.get("content")):
            msgs.append({"role": m["role"], "content": str(m["content"])})
    msgs.append({"role": "user", "content": str(prompt or "")})
    return msgs


def _err(reason: str, brain_cfg, **extra) -> dict:
    """Отказ словарём. Первое поле — КОД причины (nokey/limit/http/net/empty), а
    подробности (код HTTP, текст ошибки) идут рядом, не подменяя причину."""
    out = {"ok": False, "text": "", "error": reason, "provider": provider(brain_cfg),
           "model": model(brain_cfg), "tokens_in": 0, "tokens_out": 0, "ms": 0}
    out.update(extra)
    return out


def ask(brain_cfg: dict | None, system: str, prompt: str, history=None,
        timeout: float | None = None) -> dict:
    """Спросить облако. Возврат: {"ok", "text", "error", "tokens_in", "tokens_out",
    "ms", "provider", "model", "masked"}. Исключений не бросаем: мозг, который
    падает, — это существо, которое падает вместе с ним.
    """
    why = why_not(brain_cfg)
    if why:
        return _err(why, brain_cfg)
    sec = section(brain_cfg)
    prov, mdl, url, k = provider(brain_cfg), model(brain_cfg), base_url(brain_cfg), key(brain_cfg)
    msgs = _messages(system, prompt, history)
    masked = 0
    if bool(sec.get("mask", True)):
        masked = len(msgs)
        msgs = [{"role": m["role"], "content": mask(m["content"])} for m in msgs]
    try:
        mt = max(1, int(sec.get("max_tokens") or DEFAULT_MAX_TOKENS))
        temp = float(sec.get("temperature", 0.3))
        to = float(timeout if timeout is not None else (sec.get("timeout") or 60))
    except (TypeError, ValueError):
        return _err("bad_cfg", brain_cfg)
    body = {"model": mdl, "messages": msgs, "max_tokens": mt, "temperature": temp,
            "stream": False}
    # ОЦЕНКА ДО ОТПРАВКИ: потолок бюджета решает, уйдёт запрос или нет — а не счёт
    # после него. Иначе «лимит» узнавали бы, уже заплатив за него.
    est_in = estimate([m["content"] for m in msgs])
    cap = limit(brain_cfg)
    if cap and used(brain_cfg) + est_in + mt > cap:
        audit.log("brain.online.limit", spent=used(brain_cfg), need=est_in + mt, cap=cap)
        return _err("limit", brain_cfg, need=est_in + mt)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url + "/chat/completions", data=data, method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + k,
                 "Accept": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=to) as r:
            payload = json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:                       # noqa: BLE001 — тело ошибки не главное
            pass
        audit.log("brain.online.error", provider=prov, model=mdl, code=int(e.code),
                  error=detail[:200])
        return _err("http", brain_cfg, code=int(e.code), detail=detail,
                    ms=int((time.time() - t0) * 1000))
    except (urllib.error.URLError, OSError, ValueError) as e:
        audit.log("brain.online.error", provider=prov, model=mdl, code=0,
                  error=f"{type(e).__name__}: {e}"[:200])
        return _err("net", brain_cfg, detail=f"{type(e).__name__}: {e}"[:200],
                    ms=int((time.time() - t0) * 1000))
    choices = payload.get("choices") or [{}]
    text = str((choices[0].get("message") or {}).get("content") or "").strip()
    usage = payload.get("usage") or {}
    try:
        tin = int(usage.get("prompt_tokens") or est_in)
        tout = int(usage.get("completion_tokens") or estimate(text))
    except (TypeError, ValueError):
        tin, tout = est_in, estimate(text)
    b = spend(tin, tout)
    ms = int((time.time() - t0) * 1000)
    audit.log("brain.online.ask", provider=prov, model=mdl, ms=ms, tokens_in=tin,
              tokens_out=tout, spent=int(b["tokens_in"]) + int(b["tokens_out"]),
              masked=masked, empty=not bool(text))
    if not text:
        return {"ok": False, "text": "", "error": "empty", "provider": prov, "model": mdl,
                "tokens_in": tin, "tokens_out": tout, "ms": ms,
                "masked": masked, "spent": int(b["tokens_in"]) + int(b["tokens_out"])}
    return {"ok": True, "text": text, "error": "", "provider": prov, "model": mdl,
            "tokens_in": tin, "tokens_out": tout, "ms": ms, "masked": masked,
            "spent": int(b["tokens_in"]) + int(b["tokens_out"])}


def probe(brain_cfg: dict | None, timeout: float | None = None) -> dict:
    """ЖИВАЯ проба облака: короткий запрос и правда по факту. Нужна оператору
    (кнопка «Проверить») и мне для замера — «готов» по настройкам ещё не значит
    «отвечает»: ключ может быть отозван, модель снята с обслуживания."""
    return ask(brain_cfg, "Отвечай одним словом.", "Ты на связи?", timeout=timeout)


def platform_note() -> str:
    """Строка для отчёта: где мы считаем и каким клиентом (для разбора жалоб)."""
    return f"{platform.system()} {platform.release()} · urllib · OpenAI-compatible"
