"""Секреты доступа — ОТДЕЛЬНЫМ файлом, а не в коде и не в настройках.

Правило проекта (PLAN.md, раздел «Границы», п. 4): секреты не лежат ни в коде, ни
в журналах, ни в отчётах. Логин и пароль нужны инструменту `peers`, чтобы просто
ПОСМОТРЕТЬ соединения на своём роутере и на нашем VPS, — значит они живут в
`data/creds.json`, который:

  * читается на каждое обращение (правку видно сразу, перезапуск не нужен);
  * никуда не пишется: ни в audit, ни в отчёт, ни в human-версию отчёта;
  * если файла нет — это НЕ ошибка, а честное «доступа нет»: инструмент скажет
    об этом строкой, а не упадёт.

Формат — по ИМЕНИ узла, как он назван в `peers.targets` (`router`, `vps`, …), а с
v0.30.0 ещё и по ИМЕНИ ПРОВАЙДЕРА облачного мозга (`gemini`, `groq`, …) — ключ там
лежит под именем `key` (принимаем также `api_key` и `token`):

    {
      "router": {"user": "cody", "password": "…"},
      "vps":    {"user": "deploy", "password": "…"},
      "gemini": {"key": "…"}
    }

Пустое или битое значение считается отсутствием: молча подставлять чужой пароль
хуже, чем честно не войти.
"""
from __future__ import annotations

import json
from pathlib import Path

from synapse import config

CREDS = config.DATA / "creds.json"


def _file() -> Path:
    return CREDS


def load() -> dict:
    """Весь файл секретов. Нет файла или он битый — пустой словарь."""
    p = _file()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def for_target(name: str) -> dict:
    """Логин и пароль узла по имени. Пустые поля не возвращаем вовсе."""
    block = (load().get(str(name or "")) or {})
    if not isinstance(block, dict):
        return {}
    out = {}
    for key in ("user", "username", "login"):
        if str(block.get(key) or "").strip():
            out["user"] = str(block[key]).strip()
            break
    for key in ("password", "pass", "pwd"):
        if str(block.get(key) or ""):
            out["password"] = str(block[key])
            break
    # ОБЛАЧНЫЙ МОЗГ (v0.30.0): ключ провайдера лежит в ТОМ ЖЕ файле, что логины узлов
    # (правило проекта: секреты не в коде и не в настройках). Разные провайдеры зовут
    # его по-разному — api_key у Google, key у нас, token у части шлюзов; принимаем все
    # три написания и отдаём под одним именем, чтобы код запроса не разрастался ветками.
    for key_name in ("key", "api_key", "apikey", "token", "bearer"):
        if block.get(key_name):
            out["key"] = str(block[key_name])
            break
    return out


def key(name: str) -> str:
    """Только ключ доступа сервиса (облачный мозг): "" если ключа нет.

    Пустой ключ — это НЕ поломка, а честное «доступа нет»: спросить облако нельзя,
    и об этом скажет интерфейс, а не выдуманный запрос без заголовка.
    """
    return str(for_target(name).get("key") or "")


def has(name: str) -> bool:
    """Есть ли чем войти (пароль или ключ) — без показа самих секретов."""
    c = for_target(name)
    return bool(c.get("password") or c.get("key"))
