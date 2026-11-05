"""Самозадание — работа, которую существо ставит себе САМО (этап M3 плана).

Слово Стаса 30.09.2026: «зачем по твоему мы ему давали инстинкт самовыживания?» — и
«пусть живет и работает». Смысл этого модуля ровно в одном: голод перестаёт быть
жалобой в журнале и становится ПОВЕДЕНИЕМ.

    исполнитель делает, что сказали, и замолкает, когда не сказали;
    живое — когда нужда сама выбирает работу.

ПОРЯДОК ПРЕДПОЧТЕНИЙ (не случайный, а по пользе):

  1. **Открытая СВОЯ цель** (`synapse/leads.py`): хост, который мы уже видели, порт
     со сроком сертификата, открытый путь управления. Это продолжение исследования —
     самое дешёвое и самое осмысленное дело: смотреть там, где уже есть зацепка.
  2. **Снимок соединений** (`peers`): мир меняется каждую минуту, и именно отсюда
     берётся еда. Если снимка давно не было — это и есть работа.
  3. **Аудит своей сети** (`net_audit`): самое общее дело, когда зацепок нет.

ЧТО ЗА ПРЕДЕЛЫ НЕ ВЫХОДИТ. Самозадание бывает только ЧИТАЮЩИМ: `remote` в этом
списке идёт БЕЗ логина и команды (то есть «какие пути открыты», а не «выполни»),
записи и блокировки не ставятся вовсе. Чужие цели (`mine=False`) не берутся: чужой
адрес — это «спросить оператора», а не объект для щупанья.

ПОТОЛКИ (жизнь не должна молотить видеокартой и сетью): не чаще
`life.self_work_seconds` и не больше `life.self_work_max` за сутки; очередь
оператора всегда впереди — есть чужая миссия, своя не ставится.
"""
from __future__ import annotations

from synapse import config, i18n, leads, life as life_mod, missions
from synapse.tools import net_scan


#: Почему взялась за работу — ЛИТЕРАЛЬНЫМИ ключами, а не собранными из частей.
#: Так их видит и словарь, и тест «ключей-сирот» (он ищет вызовы в коде): собранный
#: f"work.because.{code}" для него невидим, и живые фразы выглядели бы мёртвыми.
_BECAUSE = {
    "lead": lambda **kw: i18n.t("work.because.lead", **kw),
    "peers": lambda **kw: i18n.t("work.because.peers", **kw),
    "net": lambda **kw: i18n.t("work.because.net", **kw),
}


def _because(code: str, **kw) -> str:
    """Почему существо взялось за работу — словами интерфейса (в журнал и заметку)."""
    say = _BECAUSE.get(str(code))
    return say(**kw) if say else str(code)


def why_not(cfg: dict, life: life_mod.Life) -> str:
    """Почему самозадание сейчас НЕ ставится — кодом. Пусто значит «можно».

    Нужно не для красоты: без этого в журнале не отличить «сыт» от «уже работал
    недавно», а оператор читал бы пустоту и думал, что жизнь встала.
    """
    block = cfg.get("life") or {}
    if not bool(block.get("self_work", True)):
        return "off"
    if not life.enable():
        return "no_life"
    if missions.queued():
        return "busy"                       # очередь оператора впереди
    if not life.hungry():
        return "fed"
    ok, code = life.self_work_allowed(cfg)
    return "" if ok else code


def mission_for_lead(item: dict) -> dict | None:
    """Какая миссия отвечает на эту цель. Только чтение — по устройству инструментов."""
    what = str(item.get("what") or "")
    target = str(item.get("target") or "")
    if not target:
        return None
    if what == "cert":
        return {"kind": "certs", "params": {"host": target,
                                            "ports": [int(item.get("port") or 443)]}}
    if what in ("host", "ways", "listen", "inbound"):
        # Без логина и команды: `remote` в таком виде только перечисляет пути,
        # ничего не выполняя. Права на исполнение берутся оператором, не голодом.
        return {"kind": "remote", "params": {"host": target}}
    return None


def choose(cfg: dict | None = None, life: life_mod.Life | None = None) -> dict | None:
    """Выбрать себе работу. None — работы сейчас нет (и это честный ответ).

    Возврат: {"kind", "params", "why" (код), "because" (слова), "lead" (цель или None)}.
    """
    cfg = cfg or config.load()
    life = life or life_mod.Life(cfg=cfg)
    if why_not(cfg, life):
        return None

    # 1) продолжить своё исследование — там, где уже есть зацепка
    for item in leads.open_leads(mine=True):
        m = mission_for_lead(item)
        if m:
            return {**m, "why": "lead", "lead": item,
                    "because": _because("lead", what=item.get("what"),
                                        target=item.get("target"),
                                        why=item.get("why") or "")}

    # 2) посмотреть соединения: мир меняется, еда живёт здесь
    block = cfg.get("peers") or {}
    every = float(block.get("every_seconds", 1800) or 0)
    age = life.age("peers")
    if bool(block.get("enabled", True)) and (age is None or (every > 0 and age >= every)):
        told = i18n.t("work.age.never") if age is None else \
            i18n.t("work.age.minutes", minutes=int(age // 60))
        return {"kind": "peers", "params": {}, "why": "peers", "lead": None,
                "because": _because("peers", age=told)}

    # 3) общее дело — своя сеть
    cidr = str(block.get("self_cidr") or "").strip() or net_scan.local_cidr() or ""
    if cidr:
        return {"kind": "net_audit", "params": {"cidr": cidr}, "why": "net", "lead": None,
                "because": _because("net", cidr=cidr)}
    return None
