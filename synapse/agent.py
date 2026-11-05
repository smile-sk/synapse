"""Synapse — сам организм: такт жизни, миссии, память о себе.

Такт (tick) — это одно «пробуждение»:
  1) тело живёт само (импульс по коннектому, модель не нужна) — слой рефлексов;
  2) если есть миссия от оператора — берём ОДНУ и исполняем;
  3) если миссии нет — организм живёт: ГОЛОДНОЕ существо ставит себе работу САМО
     (только чтение, с потолками — synapse/work.py), сытое думает вслух;
  4) всё записано в аудит, счётчик пробуждений — в state.json.

Потолок пробуждений снят словом Стаса (24.09.2026): организм живёт без счётчика.

МЕРА ВРЕМЕНИ — СЕКУНДЫ (v0.15.0). Пока такт шёл раз в минуту, счёт тактов был мерой
времени; с непрерывным тактом (tick_seconds=0) это стало неправдой.

ПОТОК БЕЗ ПАУЗ (v0.16.0, слово Стаса: «я же сказал поток сознания, причём тут 30с?»).
отдельного ключа разрежения больше нет — темп задаёт такт: мысль идёт за мыслью, а
темп задаёт сама модель. Число больше нуля — редкие мысли, для бережливых.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import time
from datetime import date
from pathlib import Path

from synapse import (audit, cancel, config, i18n, leads, life, missions, propose,
                     reports, work, world)
from synapse.body import Body
from synapse.brain import Brain
from synapse.tools import certs, diff, net_scan, remote, write_probe
from synapse.tools import peers as peers_tool

STATE = config.DATA / "state.json"
#: Список инструментов берём ИЗ ОЧЕРЕДИ (missions.KINDS) — один источник правды:
#: пока их было два, консоль могла положить в очередь имя, которого агент не знает.
KINDS = missions.KINDS


class Synapse:
    #: Копия промпта вместо ответа (v0.17.0): столько слов ПОДРЯД — уже не совпадение
    #: Сколько слов ПОДРЯД из инструкции = копия (было 3 — и этого хватило,
    #: чтобы объявлять копией ЛЮБОЙ честный ответ про тело: см. _NUM ниже).
    _ECHO_RUN = 4
    #: …или два слова подряд, если это половина всего ответа (короткая копия целиком)
    _ECHO_SHARE = 0.6

    #: ЦИФРА В СОВПАДЕНИИ — ЭТО ДАННЫЕ, А НЕ ИНСТРУКЦИЯ (v0.23.0).
    #: Живой случай 26.09.2026, 13:03–14:44: вкладка «Мысли» была пуста, а в журнале
    #: рядом стояли brain.answer chars=672 и brain.echo — ответ ВЫБРОШЕН как копия.
    #: Причина: и в задании, и в modelfile есть живые данные тела («302 нейрона»,
    #: «зажжено 5 из 302, энергия 0.0028»), а модель именно их и НАЗЫВАЕТ — это её
    #: ответ, а не пересказ моих слов. Копия — это когда она возвращает МОИ СЛОВА:
    #: «Не копируй мои слова», «ракурс — своё тело», «Ты — Synapse…». Там цифр нет.
    _NUM = re.compile(r"\d")

    def __init__(self, cfg: dict | None = None, body: Body | None = None,
                 brain: Brain | None = None) -> None:
        self.cfg = cfg or config.load()
        self.body = body or Body.load()
        self.brain = brain or Brain(self.cfg)
        # ЖИЗНЬ — запас сил (v0.27.0): расход временем, приход пользой.
        self.life = life.Life(cfg=self.cfg)
        # ПОСЛЕДНЕЕ ИЗМЕНЕНИЕ МИРА (v0.29.0, этап M5): с чем сравнивал прошлый
        # снимок. Нужно речью и промптом мысли — «стало иначе» вместо «как было».
        self._last_change: dict | None = None
        self._last_thought: str | None = None     # прошлая мысль: не повторяться подряд
        self._thread: list[str] = []              # НИТЬ: последние мысли (v0.18.0), самая свежая в конце
        self._repeat_streak = 0                   # сколько раз подряд мозг сказал то же самое
        # ОТМЕТКИ ВРЕМЕНИ рефлексов и мыслей (v0.15.0). -inf значит «пора прямо
        # сейчас»: с ним первый же свободный такт даёт и мысль, и импульс, и это
        # не зависит от аптайма машины (time.monotonic() на Windows считается
        # от загрузки системы, поэтому 0.0 дал бы разное поведение).
        self._last_think_at = float("-inf")
        self._last_instinct_at = float("-inf")
        self._last_overload_log_at = float("-inf")
        self._last_breath_at = float("-inf")
        self._last_hunger_log_at = float("-inf")
        self._last_tick_log_at = float("-inf")
        self._last_idle_status = "idle"           # чем занят был свободный такт
        self._last_selfwork_log_at = float("-inf")  # когда сказали, почему не взялась за работу
        # ЭХО ВМЕСТО МЫСЛИ (v0.17.0). Живой журнал 25.09.2026: три записи brain.thought
        # подряд «Заметь, сколько прошло времени.» — это МОЯ ЖЕ строка промпта (ракурс
        # наблюдения), модель её СКОПИРОВАЛА. Копию за мысль не выдаём.
        self._echo_streak = 0                     # копий промпта подряд
        # МУСОР ВМЕСТО МЫСЛИ (v0.20.2). Живой журнал 26.09.2026: мозг ответил 2000
        # знаков одних «---», и этот мусор УШЁЛ В НИТЬ — следующий запрос модель
        # продолжала своими же «---», поток зацикливался навсегда. Проверено пробой
        # на живом сервере: с мусорной нитью ответ «---\n\n---», без нити — внятная
        # фраза. Считаем мусорные ответы подряд и в нить их не пускаем.
        self._junk_streak = 0
        self._muted_until = float("-inf")         # до этого времени мозг не спрашиваем

    def reload(self, cfg: dict | None = None) -> None:
        """Настройки перечитаны СУЩЕСТВОМ, а не только окном (v0.31.0).

        Живой случай 30.09.2026, 21:42:45: оператор снял галку «подтверждение
        действий» и ждал, что существо начнёт брать предложенное само. Оно не
        начало — здесь лежал СЛЕПОК `self.cfg`, снятый при рождении, и
        `propose.offer` читал галку из слепка: два предложения (21:43:48 и
        21:44:18) вышли статусом `proposed` и ждали кнопки, которой оператор уже
        не хотел. Панель обновляла мозг (`brain.reload`), а организм — нет.
        """
        self.cfg = cfg if isinstance(cfg, dict) else config.load()
        self.life.reload(self.cfg)

    # ---------- память о себе ----------
    def load_state(self) -> dict:
        if STATE.exists():
            try:
                return json.loads(STATE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return {"day": date.today().isoformat(), "wakeups": 0, "total_ticks": 0,
                "last_tick": None, "last_mission": None, "born": date.today().isoformat()}

    def save_state(self, st: dict) -> None:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, STATE)

    def can_wake(self, st: dict) -> tuple[bool, str]:
        cap = int(self.cfg.get("max_wakeups_per_day", 0) or 0)
        if cap <= 0:
            return True, i18n.t("ag.cap_none")
        if st.get("wakeups", 0) >= cap:
            return False, i18n.t("ag.cap_used", cap=cap)
        return True, f"{st.get('wakeups', 0)}/{cap}"

    # ---------- один такт ----------
    def tick(self, with_mission: bool = True, step_body: bool = True) -> dict:
        st = self.load_state()
        today = date.today().isoformat()
        if st.get("day") != today:                  # новый день — счётчик обнулился
            st["day"], st["wakeups"] = today, 0
            audit.log("day.new", day=today)

        if step_body:                             # 1) тело живёт само
            self.body.step()
            # Вдох на такте — РЕДКО и по секундам (v0.15.0): при непрерывном такте
            # бросок «в четверти случаев» давал ~1.25 вдоха в секунду, и тело жило
            # метрономом — оператор видел вечное «status=idle».
            if self._due("_last_breath_at", "tick_breath_seconds", 15):
                self.body.stimulus(f"дыхание-{st.get('total_ticks', 0)}", amp=0.5, k=3)
        if self.brain.mode() == "server":        # мозг с телом: свой сервер поднимаем сами
            self.brain.ensure()                 # иначе в консоли мозг молчит: сервер поднимала только панель
        # Панель ведёт тело сама (20 кадров в секунду) — тогда такт только думает
        # и исполняет миссию, а тело не «дёргается дважды» из двух мест.
        energy = self.body.energy()
        self._life_step()                         # 1a) жизнь тратит запас временем
        st["total_ticks"] = int(st.get("total_ticks", 0)) + 1
        st["last_tick"] = time.strftime("%Y-%m-%d %H:%M:%S")

        out = {"tick": st["total_ticks"], "energy": round(energy, 4),
               "reserve": round(self.life.reserve(), 4),
               "lit": self.body.lit(), "mission": None, "status": "idle",
               "body": self.body.describe(), "brain": self.brain.mode()}

        ok, why = self.can_wake(st)
        m = missions.take_next() if with_mission else None
        if m:
            # миссия важнее потолка: сказанное оператором исполняется всегда
            status, text = self.handle(m)
            out["mission"], out["status"], out["text"] = m["id"], status, text
            st["last_mission"] = m["id"]
            out["reaction"] = self.react(text)
        elif not ok:
            out["status"], out["text"] = "asleep", why
            audit.log("tick.sleep", why=why)
            self.save_state(st)
            return out
        else:
            made = self._self_work()
            if made:
                # ГОЛОД СТАЛ РАБОТОЙ (v0.28.0). Пока запаса нет, существо не «живёт
                # тихо»: оно берёт себе дело само. Миссия при этом кладётся в ТУ ЖЕ
                # очередь, что и операторская, — её видно в таблице, и её можно снять
                # кнопкой «Удалить», как любую другую.
                out["self_work"] = made["id"]
                out["status"] = "selfwork"
                out["text"] = i18n.t("ag.self_work", kind=made["kind"],
                                     because=made["because"])
            else:
                out["text"] = self.idle_thought()
                # Свободный такт не обязан называться «idle»: подкормила себя или
                # подумала — так и пишем, иначе в журнале вместо жизни плоская линия.
                out["status"] = self._last_idle_status

        st["wakeups"] = int(st.get("wakeups", 0)) + 1
        self.save_state(st)
        # СЕРДЦЕБИЕНИЕ В ЖУРНАЛЕ — РЕДКО (v0.15.0). При непрерывном такте строка
        # "status=idle" писалась ~5 раз в секунду и хоронила под собой настоящие
        # события — оператор читал журнал и видел «постоянно idle». Случилось
        # что-то (миссия, мысль, рефлекс) — пишем ВСЕГДА; «просто жив» — раз в N с.
        # «muted» — состояние, а не событие (v0.17.0): начало молчания уже записано
        # как brain.muted, и писать его каждый такт значило бы вернуть плоскую линию.
        if out["mission"] or out["status"] not in ("idle", "muted") or self._due(
                "_last_tick_log_at", "tick_log_seconds", 30):
            audit.log("tick", tick=st["total_ticks"], energy=round(energy, 5),
                      reserve=round(self.life.reserve(), 5),
                      mission=out["mission"], status=out["status"])
        return out

    # ---------- миссии ----------
    def handle(self, m: dict) -> tuple[str, str]:
        kind = str(m.get("kind", ""))
        params = m.get("params") or {}
        mid = str(m.get("id") or "")
        # СВЕЖИЙ СТАРТ (v0.24.0): след чужой отмены не должен гасить эту миссию.
        # Просьба остановиться ставится кнопкой «Удалить» (synapse/cancel.py) и
        # снимается в _finish — иначе следующая миссия с тем же id встала бы сама.
        cancel.clear(mid)
        if kind not in KINDS:
            text = i18n.t("ag.no_tool", kind=kind, kinds=", ".join(KINDS))
            audit.log("mission.refused", mission=m["id"], kind=kind)
            return self._finish(m, "refused", text)
        try:
            if kind == "net_audit":
                rep = net_scan.audit_network(params.get("cidr"), mission=m["id"], cfg=self.cfg)
                if rep.get("error"):
                    return self._finish(m, "failed", rep["error"])
                # итог миссии — человеческой строкой (то же, что в отчёте .txt)
                if rep.get("stopped") or cancel.requested(mid):
                    return self._finish(m, "refused", i18n.t("ag.stopped_mission"))
                text = reports.summary_line(rep)
                if rep.get("report_text"):
                    text += i18n.t("ag.report_tail", path=rep["report_text"])
                elif rep.get("report"):
                    text += i18n.t("ag.report_tail", path=rep["report"])
                text += self._digest("net_audit", rep, params)
                return self._finish(m, "done", text)
            if kind == "stimulus":
                self.body.stimulus(str(params.get("text", "")), amp=float(params.get("amp", 1.0)))
                text = i18n.t("ag.stimulus_done", body=self.body.describe())
                return self._finish(m, "done", text)
            if kind == "ask":
                question = str(params.get("text", "")) or m.get("note", "")
                # Существо говорит на языке интерфейса — и вопрос, и системная роль.
                answer = self.brain.think(
                    i18n.t("ag.ask_prompt", question=question, body=self.body.describe()))
                text = answer or i18n.t("ag.brain_silent")
                return self._finish(m, "done", text)
            if kind == "remote":
                # УДАЛЁННОЕ УПРАВЛЕНИЕ (v0.24.0). Слово Стаса 26.09.2026: «новая
                # миссия — удалённое управление указанным IP… на указанном адресе
                # должен найтись способ это сделать». Инструмент сам решает, каким
                # путём (WinRM / WMI-DCOM / SSH) и честно говорит, если не пустило.
                rep = remote.control(params, mission=m["id"], cfg=self.cfg)
                if rep.get("state") == "stopped" or cancel.requested(mid):
                    return self._finish(m, "refused", i18n.t("ag.stopped_mission"))
                if rep.get("error"):
                    return self._finish(m, "failed", rep["error"])
                text = reports.summary_line(rep)
                text += self._digest("remote", rep, params)
                if rep.get("report_text"):
                    text += i18n.t("ag.report_tail", path=rep["report_text"])
                return self._finish(m, "done", text)
            if kind == "peers":
                # СОЕДИНЕНИЯ СВОИХ УЗЛОВ (v0.28.0, идея Стаса 30.09.2026): «пускай
                # смотрит и анализирует соединения на моем роутере… потом тоже самое
                # по соединениям на нашем VPS — только все конечно для чтения».
                # Только чтение — по устройству инструмента (команды белым списком в
                # коде), а не по обещанию в отчёте.
                rep = peers_tool.collect(params, mission=m["id"], cfg=self.cfg)
                if cancel.requested(mid):
                    return self._finish(m, "refused", i18n.t("ag.stopped_mission"))
                text = reports.summary_line(rep)
                text += self._digest("peers", rep, params)
                if rep.get("report_text"):
                    text += i18n.t("ag.report_tail", path=rep["report_text"])
                return self._finish(m, "failed" if rep.get("error") else "done", text)
            if kind in ("write_probe", "certs", "diff"):
                tool = {"write_probe": write_probe.probe,
                        "certs": certs.check,
                        "diff": diff.compare_latest}[kind]
                rep = tool(params, mission=m["id"], cfg=self.cfg)
                text = reports.summary_line(rep)
                if rep.get("report_text"):
                    text += i18n.t("ag.report_tail", path=rep["report_text"])
                if not rep.get("error"):
                    text += self._digest(kind, rep, params)
                status = "done" if not rep.get("error") else "failed"
                return self._finish(m, status, text)
            text = str(params.get("text", "")) or m.get("note", "")
            return self._finish(m, "done", text, spoken=i18n.t("ag.note", text=text))
        except Exception as e:                       # организм не падает от миссии
            text = f"{type(e).__name__}: {e}"
            audit.log("mission.crash", mission=m["id"], error=text[:200])
            return self._finish(m, "failed", text)

    def _finish(self, m: dict, status: str, text: str,
                spoken: str | None = None) -> tuple[str, str]:
        """Записать итог миссии — но НЕ перезаписать отмену оператора.

        Находка QA Ореста 25.09.2026: снятая на ходу миссия всё равно помечалась
        done/failed, и оператор видел «снята», хотя задание дошло до конца.
        Правило: refused НЕ перезаписывается никогда (missions.mark_running),
        а работающий итог при этом не теряется — оператор узнаёт о нём строкой.
        """
        if status == "done":
            # КОГДА ЧТО БЫЛО (v0.28.0). Нужно выбору самозадания: «снимок соединений
            # был пять минут назад» — причина не бегать по сети каждую минуту.
            try:
                self.life.note_run(str(m.get("kind") or ""))
            except Exception:                    # жизнь не роняет работу
                pass
        applied = missions.mark_running(m.get("id"), status, text)
        # Просьба остановиться — на одну миссию: снимаем её здесь, в единственном
        # месте, где миссия заканчивается ЛЮБЫМ исходом.
        cancel.clear(str(m.get("id") or ""))
        if applied is None:
            current = missions.status_of(m.get("id"))
            if current == "refused":
                audit.log("mission.cancelled_while_running", mission=m.get("id"),
                          kind=m.get("kind"))
                return "refused", i18n.t("ag.cancelled_running")
            if current is None:
                # Такт позвали напрямую, такой миссии в очереди нет (тесты, чужой
                # вызов): итог отдаём словами, но в очередь не пишем — писать некуда.
                audit.log("mission.not_in_queue", mission=m.get("id"), kind=m.get("kind"))
        return status, spoken or text

    # ---------- реакция тела и мысли ----------
    def react(self, text: str) -> str:
        """Тело отзывается на своё дело (как impulse по графу в AI_UI)."""
        self.body.stimulus(text[:200] or i18n.t("ag.empty"), amp=0.7, k=3)
        self.body.poke(self.body.pick("motor", k=2, seed=len(text)), 0.9)
        self.body.step()
        return self.body.describe()

    def _due(self, attr: str, cfg_key: str, default: float) -> bool:
        """Пора ли снова — по СЕКУНДАМ, а не по числу тактов.

        Зачем (v0.15.0). Слово Стаса 25.09.2026: «бесконечное время жизни в 0
        секунд — чтобы швов между тиками не было». Пока такт шёл раз в минуту,
        счёт тактов был мерой времени. С непрерывным тактом это перестало быть
        правдой: пять тактов в секунду — это не «раз в пять тактов», а «пять раз
        в секунду», и разница видна и на GPU, и в журнале. Секунды от темпа
        такта не зависят. every <= 0 значит «пора всегда» — это и есть поток.
        """
        try:
            every = float(self.cfg.get(cfg_key, default) or 0)
        except (TypeError, ValueError):
            every = float(default)
        now = time.monotonic()
        if every <= 0 or now - float(getattr(self, attr, float("-inf"))) >= every:
            setattr(self, attr, now)
            return True
        return False

    def _life_step(self) -> None:
        """Жизнь идёт: запас тратится временем, а смена уровня — событие в журнале.

        Каждый такт писать расход нельзя: такт бывает и пять раз в секунду — журнал
        стал бы плоской линией (тот же урок, что с tick_log_seconds). Поэтому в
        журнал идёт только ПЕРЕХОД уровня: ok → hungry → starving → ok.
        """
        was = self.life.level()
        was_bored = bool(self.life.bored())
        self.life.metabolize()
        now = self.life.level()
        if now != was:
            audit.log("life.level", was=was, level=now,
                      reserve=round(self.life.reserve(), 4))
        # СКУКА — ПЕРЕХОД, А НЕ МЕТРОНОМ (v0.32.0): в журнал идёт смена состояния
        # («нашла новое» → «давно ничего нет»). Иначе при такте 30 с журнал тонул бы
        # в одинаковых строках — тот же урок, что у tick_log_seconds.
        bored = bool(self.life.bored())
        if bored != was_bored:
            audit.log("life.bored", bored=bored, level=now,
                      idle=round(self.life.idle_seconds() or 0.0),
                      reserve=round(self.life.reserve(), 4))

    def _digest(self, kind: str, rep: dict, params: dict) -> str:
        """Отработать отчёт: покормиться находками И запомнить цели на будущее.

        Это две разные вещи, и раньше была только первая. «Чем наелась» — про жизнь
        (запас), «что взяла на будущее» — про память о зацепках (synapse/leads.py).
        Слово Стаса 30.09.2026: отчёт должен давать ЦЕЛЬ, иначе существо смотрит и
        забывает, и исследования не продолжаются.
        """
        parts = [p for p in (self._eat(kind, rep, params),
                             self._learn(kind, rep, params),
                             self._world(kind, rep, params)) if p]
        # Строки итога разделяем, как и хвост отчёта ("; отчёт: …"): иначе в колонке
        # «итог» слипалось «…целей 3нашла нового: 212».
        return "; " + "; ".join(parts) if parts else ""

    def _world(self, kind: str, rep: dict, params: dict) -> str:
        """Сравнить с прошлым снимком этой области: стало иначе или как было (M5).

        Слово Стаса 30.09.2026: «я хочу сотворить живой киберорганизм ещё и с пользой».
        Польза видна на СРАВНЕНИИ, а не на взгляде: порт, открывшийся ночью, выглядит
        как «как всегда», пока не с чем сравнить. Первый взгляд не шумит — сравнивать
        не с чем; повтор без изменений говорит «как было»; изменение называет числами
        и попадает в дневник (`data/diary.md`).
        """
        try:
            change = world.record(kind, rep, params)
        except Exception as e:                   # память о мире не роняет работу
            audit.log("world.error", kind=kind, error=f"{type(e).__name__}: {e}"[:160])
            return ""
        self._last_change = change
        if change["verdict"] == "first":
            return ""                            # первый взгляд — не с чем сравнивать
        if change["verdict"] == "same":
            return i18n.t("ag.world_same")
        audit.log("world.changed", scope=change["scope"], verdict=change["verdict"],
                  plus=change["plus"], minus=change["minus"], facts=change["facts"])
        return i18n.t("ag.world_change", plus=change["plus"], minus=change["minus"],
                      items=world.items_text(change, limit=2))

    def _learn(self, kind: str, rep: dict, params: dict) -> str:
        """Запомнить цели из отчёта и закрыть те, что эта миссия уже покрыла."""
        try:
            self._cover_leads(kind, rep, params)
            found = leads.from_report(kind, rep, self.cfg)
            got = leads.remember(found) if found else {"new": 0}
            st = leads.stats()
        except Exception as e:                   # память не роняет работу
            audit.log("leads.error", kind=kind, error=f"{type(e).__name__}: {e}"[:160])
            return ""
        if not got.get("new"):
            return ""                            # нового нет — не шумим
        audit.log("leads.learned", kind=kind, new=got["new"], open=st["open"], mine=st["mine"])
        return i18n.t("ag.leads_learned", new=got["new"], open=st["open"], mine=st["mine"])

    def _cover_leads(self, kind: str, rep: dict, params: dict) -> None:
        """Отметить отработанные цели: миссия УЖЕ посмотрела это место.

        Зачем: без закрытия существо бегало бы по одному кругу (одна и та же зацепка
        вечно «открыта»). Новый отчёт по тому же месту откроет цель снова — мир ведь
        мог измениться, и это честно.
        """
        mid = str(rep.get("mission") or "")
        if kind == "remote":
            host = str(params.get("host") or rep.get("host") or "")
            leads.mark(host, "ways", mission=mid)
        elif kind == "certs":
            for t in rep.get("targets") or []:
                leads.mark(str(t.get("host")), "cert", mission=mid)
        elif kind == "net_audit":
            cidr = str(params.get("cidr") or rep.get("cidr") or "")
            if not cidr:
                return
            try:
                net = ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                return
            for item in leads.all_leads():
                if item.get("what") != "host" or item.get("state") != "open":
                    continue
                try:
                    addr = ipaddress.ip_address(str(item.get("target")))
                except ValueError:
                    continue
                if addr.version == net.version and addr in net:
                    leads.mark(str(item.get("target")), "host", mission=mid)

    def _self_work(self) -> dict | None:
        """Голод ставит себе работу — сам, без оператора (этап M3 плана).

        Слово Стаса 30.09.2026: «зачем по твоему мы ему давали инстинкт
        самовыживания?» — чтобы нужда вела к делу, а не к жалобе в журнале.

        Границы жёсткие и проверяются тестом:
          * только ЧТЕНИЕ (список родов работы — в synapse/work.py);
          * очередь оператора впереди: есть его миссия — своя не ставится;
          * потолки по времени и на сутки (life.self_work_*);
          * чужое не берём: цель с признаком mine=False — это «спросить оператора».

        Миссия кладётся в ОБЩУЮ очередь: оператор видит её в таблице и может снять.
        """
        why = work.why_not(self.cfg, self.life)
        if why:
            # Голод есть, а взяться не за что (или уже поработала) — сказать честно,
            # но НЕ каждым тактом: при потоке это была бы плоская линия в журнале.
            if why in ("interval", "cap") and self._due("_last_selfwork_log_at",
                                                        "tick_log_seconds", 30):
                audit.log("self.blocked", why=why, reserve=round(self.life.reserve(), 4),
                          left=self.life.self_work_left(self.cfg))
            return None
        plan = work.choose(self.cfg, self.life)
        if not plan:
            return None
        note = i18n.t("ag.self_work_note", because=plan["because"])
        rec = missions.add(plan["kind"], plan.get("params") or {}, note=note)
        self.life.self_work_taken(plan["kind"], rec["id"])
        audit.log("self.work", mission=rec["id"], kind=plan["kind"], why=plan["why"],
                  because=plan["because"], reserve=round(self.life.reserve(), 4),
                  left=self.life.self_work_left(self.cfg))
        # Причина едет вместе с записью: такт называет её словами, и в журнале рядом
        # с записью миссии видно, ПОЧЕМУ существо взялось за неё.
        rec["because"] = plan["because"]
        return rec

    def _eat(self, kind: str, rep: dict, params: dict) -> str:
        """Покормиться находками и назвать это строкой итога миссии.

        Строка нужна оператору: видно, ЧЕМ существо наелось и что еды не было.
        Кормит только НОВОЕ: пустой повторный обход даёт ноль (иначе еда росла бы
        от безделья). Ошибка жизни миссию не роняет — она просто остаётся без еды.
        """
        try:
            got = self.life.observe(kind, rep, params)
        except Exception as e:                   # жизнь не роняет работу
            audit.log("life.error", kind=kind, error=f"{type(e).__name__}: {e}"[:160])
            return ""
        if not got.get("new"):
            return i18n.t("ag.life_quiet", reserve=self.life.describe())
        return i18n.t("ag.life_feed", new=got["new"], fed=f"{got['fed']:.3f}",
                      reserve=self.life.describe())

    def instinct(self) -> str:
        """Инстинкт самосохранения — рефлекторный слой, работает БЕЗ модели.

        Слово Стаса 25.09.2026: «он ничего не делает, даже не думает». Значит
        существу нужен повод ЖИТЬ между миссиями, а не только брать задания.
        Инстинкт — этот повод и есть: голод (тело затихло совсем — само даёт
        себе импульс, чтобы не умереть) и перегруз (тело не гаснет — само себя
        гасит, страховка от припадка). Возврат — строка для строки ответа;
        пустая значит «ничего особенного, просто живу».

        ЧТО ИЗМЕНИЛОСЬ в v0.15.0 и почему. Рефлекс срабатывал КАЖДЫЙ такт, а
        такт стал непрерывным: замер живого журнала 25.09.2026 — 40 записей
        instinct.feed за 70 секунд, все с energy=0.0, lit=0. Это метроном, а не
        жизнь. Теперь голод даёт импульс не чаще, чем раз в instinct_min_seconds,
        и импульс заметный (amp 0.9 против 0.4), чтобы его было видно на теле.
        Перегруз — СТРАХОВКА, её не разрежаем: гасим всегда, редко пишем в журнал.
        """
        # ГОЛОД — ПЕРВЫЙ РЕФЛЕКС (v0.27.0). Пока запаса нет, существо не «живёт
        # тихо», а ищет: голод — это нужда, из которой растёт поведение, а не
        # украшение. Истощение видно телом (оно не бодрится, а гаснет), голод —
        # беспокойным импульсом. Работает без модели и без токенов.
        if self.life.starving():
            if self._due("_last_hunger_log_at", "instinct_min_seconds", 20):
                audit.log("instinct.hungry", level="starving",
                          reserve=round(self.life.reserve(), 4))
            self.body.soothe(0.8)
            self._last_idle_status = "starving"
            return i18n.t("ag.life_starving", reserve=self.life.describe(),
                          body=self.body.describe())
        if self.life.hungry():
            if self._due("_last_hunger_log_at", "instinct_min_seconds", 20):
                self.body.stimulus("hunger", amp=0.3, k=2)
                audit.log("instinct.hungry", level="hungry",
                          reserve=round(self.life.reserve(), 4))
            self._last_idle_status = "hungry"
            return i18n.t("ag.life_hungry", reserve=self.life.describe(),
                          body=self.body.describe())
        energy = self.body.energy()
        lit = self.body.lit()
        if lit == 0 and energy < 0.0005:
            if self._due("_last_instinct_at", "instinct_min_seconds", 20):
                self.body.stimulus("self-care", amp=0.9, k=4)
                audit.log("instinct.feed", energy=round(energy, 5), lit=lit, amp=0.9)
                self._last_idle_status = "fed"
                return i18n.t("ag.instinct_fed", body=self.body.describe())
            return ""
        if lit > 60:
            self.body.soothe(0.5)
            if self._due("_last_overload_log_at", "instinct_min_seconds", 20):
                audit.log("instinct.overload", lit=lit, energy=round(energy, 5))
            self._last_idle_status = "overload"
            return i18n.t("ag.instinct_overload", lit=lit, body=self.body.describe())
        return ""

    @staticmethod
    def _params_text(params: dict | None) -> str:
        """Поля предложения строкой для речи: «host=203.0.113.10»."""
        params = params or {}
        return ", ".join(f"{k}={v}" for k, v in sorted(params.items())) or i18n.t("dash")

    def _offer_work(self, offers: list[dict]) -> str:
        """Предложить работу от мозга — в очередь, но НЕ на исполнение (M6).

        Слово Стаса 30.09.2026: «сначала посмотрим что он будет предлагать… а потом,
        если его этим не тормозить, он будет что-то делать сам, просто давая отчёты».
        Поэтому здесь нет ни одного права: список инструментов — только чтение, дверь
        спрашивается до записи, потолки на сутки, и всё это в `synapse/propose.py`.
        Всё, что мозг предложил, видно в таблице миссий: со галкой подтверждения —
        как `proposed` (ждёт кнопки «Подтвердить»), без неё — уже `queued`.
        """
        told: list[str] = []
        for prop in offers:
            try:
                rec, code = propose.offer(prop, self.cfg, self.life)
            except Exception as e:               # предложение не роняет мысль
                audit.log("brain.propose_error", kind=prop.get("kind"),
                          error=f"{type(e).__name__}: {e}"[:160])
                continue
            if rec is None:
                audit.log("brain.propose_blocked", kind=prop.get("kind"), why=code,
                          params=prop.get("params") or {})
                if code in ("brain.bad_tool", "brain.bad_param", "brain.propose_alien",
                            "door"):
                    told.append(i18n.t("ag.propose_blocked", why=code))
                continue
            audit.log("brain.proposed", mission=rec["id"], kind=rec["kind"],
                      params=rec.get("params") or {}, status=rec.get("status"))
            if rec.get("status") == "proposed":
                told.append(i18n.t("ag.propose_offered", kind=rec["kind"],
                                   params=self._params_text(rec.get("params"))))
            else:
                told.append(i18n.t("ag.propose_taken", kind=rec["kind"],
                                   params=self._params_text(rec.get("params"))))
        if told:
            self._last_idle_status = "proposed"
        return "; ".join(told)

    def _thought_prompt(self) -> str:
        """Живой контекст для «думания вслух» — каждый такт РАЗНЫЙ.

        Слово Стаса 25.09.2026: «почему он как будто всегда отвечает на вопрос
        "как себя чувствует"… дергать одним и тем же вопросом, провоцируя один и
        тот же ответ». Раньше промпт был ОДИН («что ты чувствуешь»), а тело почти
        не менялось — модель и отвечала одинаково, как заевшая пластинка. Теперь в
        промпт попадает ЖИВОЙ меняющийся контекст: номер такта, время, энергия,
        сколько зажжено, громче всех, намерение тела, ракурс наблюдения (чередуется)
        и прошлая мысль (чтобы не повторяться). Один и тот же вопрос дважды подряд
        больше не задаётся.
        """
        st = self.load_state()
        body = self.body
        loud = ", ".join(f"{n}({v})" for n, v in body.active(top=4)) or i18n.t("body.quiet")
        intent = body.intent().get("why", "")
        ticks = int(st.get("total_ticks", 0))
        # ЖИВОСТЬ ЗА ТАКТ, А НЕ СНИМОК (v0.32.0). Снимок мгновения врал о жизни: тело
        # дышит, а в промпт уходило «энергия 0.0000, громче всех тихо» — и модель
        # говорила «энергия на исходе, ползу искать еду» (16 раз за вечер 30.09.2026).
        live = body.liveliness(reset=True)
        live_text = i18n.t("body.live", n=live["impulses"], peak=live["peak"],
                           size=live["size"], sec=live["seconds"])
        body_text = i18n.t("body.now", lit=body.lit(), size=body.size)
        # СВОЙ МИР — ЖИВОЙ МАТЕРИАЛ (v0.29.0, «роль C»). До этого мысль кормилась
        # состоянием нейронов, и 0.5b возвращала мою же строку: «распределено 6
        # нейронов из 302». Теперь рядом с телом идёт итог СВОИХ дел: когда смотрела,
        # что изменилось, сколько целей открыто — мысли есть о чём говорить.
        diary = world.brief(self.cfg)
        if self._propose_on():
            # МАТЕРИАЛ, А НЕ ПУСТОЕ ПОЛЕ (v0.32.0). Живой вечер 30.09.2026: без своих
            # сетей и целей мозг предлагал выдумку — `net_audit cidr=192.168.1.0/24`
            # (не наша сеть) и `remote host=127.0.0.1` (петля), а дверь такое формально
            # пропускает (net.allowed_cidrs = 0.0.0.0/0). Теперь в правиле лежат СВОИ
            # сети и ОДНА живая цель из зацепок.
            diary += " " + i18n.t("ag.propose_rule", mine=self._own_nets(),
                                  target=self._one_target())
        angle = i18n.t(("ag.observe.body", "ag.observe.time", "ag.observe.memory")[ticks % 3])
        # ЗАПРЕТ КОПИРОВАНИЯ — РЯДОМ С РАКУРСОМ (v0.17.0): сам ракурс стал короткой
        # меткой, а про копирование сказано прямо, в том же месте промпта.
        tail = angle + " " + i18n.t("ag.observe.noecho")
        if self._last_thought:
            tail += " " + i18n.t("ag.observe.last", last=self._last_thought[:160])
        if self._repeat_streak:
            # Повтор уже случился — просим сильнее и КОНКРЕТНЕЕ (v0.16.0).
            tail += " " + i18n.t("ag.observe.repeat", n=self._repeat_streak)
        return i18n.t("ag.think_prompt", body=body_text, live=live_text, loud=loud,
                      reserve=self.life.describe(), diary=diary, intent=intent, tail=tail,
                      bored=(i18n.t("ag.bored") if self.life.bored() else ""),
                      ticks=ticks, now=time.strftime("%H:%M:%S"))

    def _own_nets(self) -> str:
        """Свои сети из настроек — материал для предложения, а не выдумка модели (v0.32.0).

        Живой прогон 30.09.2026 (21:43-22:17) показал, зачем: без этой строки мозг
        предлагал `net_audit cidr=192.168.1.0/24` — сеть, которой у нас нет, и
        `remote host=127.0.0.1` — петлю. Дверь такие адреса формально разрешает
        (net.allowed_cidrs = 0.0.0.0/0, «любые сети по списку оператора»), поэтому
        материал обязан быть настоящим — иначе предложение выглядит работой, а ведёт
        в пустоту.
        """
        try:
            cidrs = [str(c) for c in ((self.cfg.get("peers") or {}).get("mine_cidrs") or [])
                     if c]
        except Exception:                        # настройки без раздела peers
            cidrs = []
        return ", ".join(cidrs[:3]) or i18n.t("ag.none")

    def _one_target(self) -> str:
        """ОДНА открытая цель из памяти о зацепках — конкретный адрес (v0.32.0).

        Цели собирает synapse/leads.py из СВОИХ же отчётов (незнакомый слушатель,
        открытый путь, срок сертификата): это то, что организм действительно видел.
        Своя цель — можно работать самой; чужая — только спросить оператора.
        """
        try:
            items = [i for i in (leads.open_leads(mine=True) or []) if i.get("target")]
        except Exception:                        # памяти о зацепках нет — не беда
            items = []
        if not items:
            return i18n.t("ag.none")
        top = items[0]
        port = top.get("port")
        return f"{top.get('target')}{(':' + str(port)) if port else ''} " \
               f"({top.get('what') or '?'})"

    def _propose_on(self) -> bool:
        """Разрешено ли мозгу предлагать работу (рубильник в настройках)."""
        try:
            return bool(self._brain_opt("propose", True))
        except Exception:                        # настройки без раздела brain
            return True

    def _brain_opt(self, key: str, default):
        """Настройка из раздела brain — с честным умолчанием, если её нет."""
        return (self.cfg.get("brain") or {}).get(key, default)

    def _system_text(self) -> str:
        """Системный промпт (modelfile.md) — второй источник для проверки на копию."""
        try:
            return str(self.brain.system_prompt() or "")
        except Exception:                        # заглушка в тестах или нет файла
            return ""

    @staticmethod
    def _words(text: str) -> list[str]:
        """Слова в нижнем регистре: буквы и цифры, знаки и пунктуация не считаются."""
        return re.findall(r"[0-9a-zа-яё]+", (text or "").lower())

    @classmethod
    def _echo_of(cls, answer: str, *sources: str) -> str | None:
        """Что модель СКОПИРОВАЛА из промпта, — или None, если это её собственные слова.

        Зачем (v0.17.0). Живой журнал 25.09.2026: три записи brain.thought подряд
        «Заметь, сколько прошло времени.» — это строка ракурса из МОЕГО ЖЕ промпта.
        Малая модель (0.5b; в q2_k это было особенно заметно, теперь q4_k_m) склонна ПРОДОЛЖАТЬ поданный текст вместо ответа,
        и такое нельзя выдавать за мысль существа.

        Правило простое и объяснимое: копия — это ДЛИННЫЙ КУСОК СЛОВ ПОДРЯД из
        промпта (три слова и больше), либо ответ целиком внутри промпта, либо
        короткий ответ, который наполовину состоит из промпта. Одно-два случайно
        совпавших слова (имена нейронов, «тело») копией не считаются — иначе мы
        задушили бы живую речь и приняли за эхо настоящую мысль.
        """
        words = cls._words(answer)
        if len(words) < 2:
            return None                          # одно слово судить не о чем
        joined = " ".join(words)
        banks = [cls._words(s) for s in sources if s]
        for bank in banks:
            if joined and joined in " ".join(bank):
                return answer.strip()
        best, best_text = 0, ""
        for bank in banks:
            if not bank:
                continue
            flat, seen = " ".join(bank), set(bank)
            for i, w in enumerate(words):
                if w not in seen:
                    continue
                for j in range(len(words), i + 1, -1):
                    chunk = " ".join(words[i:j])
                    if cls._NUM.search(chunk):
                        continue          # числа — данные тела, не инструкция
                    if chunk in flat:
                        if j - i > best:
                            best, best_text = j - i, chunk
                        break
        if best >= cls._ECHO_RUN or (best >= 3 and best >= cls._ECHO_SHARE * len(words)):
            return best_text or answer.strip()
        return None

    def _mute_after_echoes(self) -> bool:
        """Хватит копий — замолчать на cooldown. True значит молчание началось сейчас.

        Зачем (v0.17.0). Без этого лечение эха только меняло шум: вместо «Заметь,
        сколько прошло времени.» в журнал шло бы «модель скопировала мои слова» —
        столько же строк и столько же запросов к видеокарте. Молчание ограничено
        по времени (brain.echo_cooldown), состояние видно строкой ответа, а начало
        молчания записано как brain.muted — существо не молчит тайком.
        """
        limit = int(self._brain_opt("echo_limit", 3) or 0)
        cooldown = float(self._brain_opt("echo_cooldown", 60) or 0)
        if limit <= 0 or cooldown <= 0 or self._echo_streak < limit:
            return False
        self._muted_until = time.monotonic() + cooldown
        audit.log("brain.muted", seconds=cooldown, echoes=self._echo_streak,
                  reason="the model copies the prompt instead of answering")
        self._echo_streak = 0
        return True

    @staticmethod
    def _thought_of_line(ln: str) -> str | None:
        """Одна строка -> текст 💭-мысли ('' или None). Маркеры: 💭 / [мысль] / (мысль).

        Слово Стаса 25.09.2026: «дай ему отдельный канал внутренней речи — как у
        Миланы». Модель сама пишет строки с 💭 (наказ в промпте), а мы вынимаем их
        из ответа: они идут в отдельный лог, а не в речь для оператора.
        """
        s = " ".join((ln or "").split())
        # МЕТКА ИЩЕТСЯ В НАЧАЛЕ СТРОКИ, РАЗМЕТКА ПЕРЕД НЕЙ ДОПУСТИМА (v0.21.0).
        #
        # Почему именно так, а не «где угодно». Живой журнал 26.09.2026, 13:33 и 13:34:
        # модель отвечала «**💭** Внутреннее рассуждение: - **Энергия** …» — мысль БЫЛА, но
        # начиналась со звёздочек markdown, и прежняя проверка «строка начинается с 💭» её
        # не узнавала. Ответ уходил в «сказанное», там ловился как копия промпта и пропадал:
        # вкладка «Мысли» оставалась пустой.
        #
        # А «метка где угодно в строке» — НЕВЕРНОЕ правило, и это поймал тест
        # (test_ui_v020::test_a_copy_of_the_prompt_is_not_a_thought): в МОЁМ ЖЕ промпте стоит
        # «…строками, начинающимися с 💭 (отдельный канал…)» — метка В СЕРЕДИНЕ строки, поэтому
        # копия промпта становилась «мыслью» и мой текст утекал в канал мыслей существа.
        # Значит порядок такой: снять украшения слева, и только потом спросить начало строки.
        left = s.lstrip("*_`#>~•·+-–— 	")
        low = left.lower()
        mark = ""
        for m in ("💭", "[мысль]", "(мысль)", "[thought]", "(thought)"):
            if low.startswith(m):
                mark = m
                break
        if not mark:
            return None
        # Закрывающие украшения метки («**💭** текст») снимаем тоже — но НЕ тире:
        # «💭 - энергия мала» остаётся с тире, это речь, а не разметка.
        return left[len(mark):].strip(" *_`#>~•·+")

    #: Строка-разделитель или пустая — не мысль.
    _SEP_LINE = re.compile(r"^[\s\-=_*#~`·•—]+$")

    @classmethod
    def _clean_answer(cls, text: str) -> str:
        """Убрать из ответа строки-разделители и пустые, собрать в ОДНУ строку.

        Зачем (v0.20.2, живой журнал 26.09.2026). Мозг ответил 2000 знаков «---»
        через пустую строку — и это ушло и в журнал (см. audit._flat), и в НИТЬ.
        Разделители — разметка, а не мысль: выкидываем их ДО всех проверок.
        """
        keep = [l.strip() for l in (text or "").splitlines() if l.strip()]
        keep = [l for l in keep if not cls._SEP_LINE.match(l)]
        return " ".join(keep).strip()

    @staticmethod
    def _meaningful(text: str) -> bool:
        """Есть ли в ответе СЛОВА. «---», «===», «```» — не ответ.

        Порог нарочно маленький: «да» и «нет» — законные ответы, а строка из одних
        знаков препинания — нет. Считаем буквы, а не длину, чтобы мусор ловился,
        а короткая живая речь не страдала.
        """
        return sum(1 for c in (text or "") if c.isalpha()) >= 2

    def _prompt_skeleton(self) -> str:
        """ИНСТРУКЦИИ промпта без живых данных — эталон для проверки на копию.

        Зачем (v0.23.0). Жалоба 26.09.2026: «на вкладке мозг существо не отвечает,
        вкладка Мысли пуста». Живой журнал 14:44:10 показал, чем это было:
        ответ «Обзор состояния Synapse: … 5 из 302 нейронов зажжено, энергия 0.0028»
        объявлялся КОПИЕЙ и выбрасывался, а вместе с ним не попадала в канал мыслей
        ни одна запись с 13:03.

        Причина была в эталоне сравнения: я сверяла ответ с ЦЕЛЫМ промптом, а в
        промпте лежат живые данные тела (число зажжённых, энергия, имена нейронов).
        Модель их НАЗЫВАЕТ — это не копирование, это и есть ответ про своё тело.
        Копия — это когда модель возвращает МОИ СЛОВА, то есть инструкцию.
        Поэтому эталон — сам шаблон задания (с подстановками, но без значений):
        совпадёт с ним — копия, назовёт свои числа — живая речь.
        """
        return " ".join(i18n.t(k) for k in ("ag.think_prompt", "ag.observe.noecho"))

    @classmethod
    def _degenerate(cls, text: str) -> bool:
        """Модель зациклилась: один оборот повторяется подряд десятки раз.

        Живой случай 26.09.2026 (1.5b, штрафа за повтор ещё не было): ответ
        «**Мышление:** **Текст:**» — тот же кусок больше двадцати раз, 32 секунды
        генерации. Это срыв, а не мысль: в канал мыслей и в нить такое пускать
        нельзя — следующий запрос модель продолжает своим же мусором, и поток
        залипает навсегда (проверено живьём тогда же).
        """
        from collections import Counter
        w = cls._words(text)
        if len(w) < 12:
            return False
        tri = Counter(" ".join(w[i:i + 3]) for i in range(len(w) - 2))
        top = tri.most_common(1)[0][1]
        return top >= max(4, len(w) // 6)

    def _mute_now(self, reason: str, **fields) -> bool:
        """Замолчать на cooldown и записать причину — одним местом для эха и мусора."""
        limit = int(self._brain_opt("echo_limit", 3) or 0)
        cooldown = float(self._brain_opt("echo_cooldown", 60) or 0)
        if limit <= 0 or cooldown <= 0:
            return False
        self._muted_until = time.monotonic() + cooldown
        audit.log("brain.muted", seconds=cooldown, reason=reason, **fields)
        return True

    def _mute_after_junk(self) -> bool:
        """Хватит мусора — замолчать. True значит молчание началось сейчас."""
        limit = int(self._brain_opt("echo_limit", 3) or 0)
        if limit <= 0 or self._junk_streak < limit:
            return False
        started = self._mute_now("the model answers with separators instead of words",
                                 junk=self._junk_streak)
        if started:
            self._junk_streak = 0
        return started

    @classmethod
    def _split_thoughts(cls, reply: str) -> tuple[list[str], str]:
        """Вынуть 💭-строки из ответа. Возвращает (мысли, чистый ответ без них)."""
        thoughts, keep = [], []
        for ln in (reply or "").splitlines():
            t = cls._thought_of_line(ln)
            if t is None:
                keep.append(ln)
            elif t:
                thoughts.append(t)
        clean = "\n".join(keep).strip()
        # «СКАЗАННОГО НЕТ» — НАСТОЯЩЕЕ СОСТОЯНИЕ (v0.21.0). Здесь стояло `clean or reply`:
        # если модель выдала ТОЛЬКО 💭-строки, «сказанным» объявлялся весь ответ целиком.
        # Из-за этого ветка «мозг выдал только мысли» ниже была недостижима, а мысль шла
        # дальше по проверкам и ловилась как копия промпта (живой журнал 13:33/13:34:
        # brain.echo). Теперь сказанное — ровно то, что осталось без 💭.
        return thoughts, clean

    def _thread_messages(self) -> list[dict]:
        """Нить: последние мысли как сообщения для мозга (v0.18.0).

        Раньше think() слал system + один вопрос — истории не было вовсе, поэтому
        «поток» был лупом без паузы (каждый такт — новый вопрос без памяти). Теперь
        мозг видит свои прошлые мысли и продолжает ту же, а не начинает с нуля.
        """
        _thread_raw = (self.cfg.get("flow") or {}).get("thread", 6)
        cap = int(_thread_raw) if _thread_raw is not None else 6
        if cap <= 0:
            return []
        return [{"role": "assistant", "content": t} for t in self._thread[-cap:]]

    def _thread_append(self, spoken: str) -> None:
        """Записать сказанную мысль в нить (и обрезать до потолка)."""
        _thread_raw = (self.cfg.get("flow") or {}).get("thread", 6)
        cap = int(_thread_raw) if _thread_raw is not None else 6
        if cap <= 0:
            return
        self._thread.append(spoken)
        self._thread = self._thread[-cap:]

    def idle_thought(self) -> str:
        # ИНСТИНКТ — РЕФЛЕКС, А НЕ ЗАМЕНА МЫСЛИ (v0.25.0). Найдено ЖИВЫМ журналом
        # 26.09.2026 (15:23-15:33): тело стояло мёртвым (energy 0.0, lit 0), голод
        # срабатывал КАЖДЫЙ такт (разрежение 20 с, а такт 30 с) — и `return instinct`
        # выходил ДО вопроса мозгу. Итог: за десять минут ни одной мысли, а оператор
        # видел «мозг упал и больше не включался». Теперь рефлекс тело делает всегда,
        # но строкой ответа становится, только если мысль не сказалась.
        reflex = self.instinct()
        reflex_status = self._last_idle_status if reflex else ""
        self._last_idle_status = "idle"
        fallback = reflex or i18n.t("ag.alive", body=self.body.describe())

        def without_thought() -> str:
            """Мысль не сказалась. Рефлекс был — пусть он и будет ответом."""
            if reflex:
                self._last_idle_status = reflex_status
            return fallback

        if not self.cfg.get("idle_thinking"):
            return without_thought()
        # МОЗГ ЗАМОЛЧАЛ ПОСЛЕ КОПИЙ (v0.17.0): не спрашиваем, но и не прячем это —
        # состояние видно строкой ответа, а начало молчания записано как brain.muted.
        if time.monotonic() < self._muted_until:
            self._last_idle_status = "muted"
            return i18n.t("ag.muted")
        # Сначала ТЕМП, потом вопрос мозгу (v0.15.0). Порядок важен по ЗАМЕРУ:
        # available() при молчащем сервере стоит ~2 секунды, а такт при
        # tick_seconds=0 идёт несколько раз в секунду. Спрашивая готовность на
        # КАЖДОМ такте, существо вставало колом: замер живьём дал 2 такта за
        # 3 секунды вместо сотен. Теперь готовность спрашиваем только тогда,
        # когда мысль и вправду пора.
        # ОДИН ТЕМП — ТАКТ (v0.22.0). Слово Стаса 26.09.2026: «время такта и есть
        # количество мыслей… время такта 0 — поток. всё». Отдельного разрежения здесь
        # больше НЕТ: сколько тактов в секунду, столько и мыслей. Пока такт держит окно
        # (или run_forever), мысль идёт каждый такт, а при такте 0 такт длится ровно
        # столько, сколько модель отвечает, — это и есть поток.
        # Готовность спрашиваем ЗДЕСЬ. При молчащем сервере проба стоит до 2 секунд
        # (замер), а при потоке мысль нужна КАЖДЫЙ такт — поэтому ответ помним
        # дольше обычного (brain.probe_ttl). Иначе существо стояло бы колом через
        # такт просто потому, что модели рядом нет.
        ready_ttl = float((self.cfg.get("brain") or {}).get("probe_ttl", 20) or 20)
        if not self.brain.available(ttl=ready_ttl):
            # МОЗГ НЕ ОТВЕЧАЕТ — ЭТО ВИДНО (v0.25.0). Ветка молчала и отдавала «живу»:
            # отличить «мозга нет» от «существо ничего не делает» было НЕЛЬЗЯ, и
            # оператор читал журнал, не понимая, почему пусто. Теперь молчание мозга
            # названо событием (не чаще, чем раз в brain_down_seconds).
            if self._due("_last_down_log_at", "brain_down_seconds", 60):
                audit.log("brain.down", mode=self.brain.mode(), ttl=ready_ttl)
            if reflex:
                return without_thought()
            self._last_idle_status = "nobrain"
            return i18n.t("ag.nobrain", body=self.body.describe())
        prompt = self._thought_prompt()
        answer = self.brain.think(prompt, history=self._thread_messages())
        if answer:
            answer = answer.strip()
            # ПРЕДЛОЖЕНИЕ РАБОТЫ ВЫНИМАЕМ ПЕРВЫМ (v0.29.0, этап M6). Строка 🎯 — не
            # речь и не мысль, а предложение миссии: она уходит в очередь (и стоит там
            # как «proposed», если стоит галка «подтверждение действий»), а ответ
            # остаётся чистым. Порядок важен: после вынимания строка предложения не
            # может быть принята ни за копию промпта, ни за мусор.
            offers, answer = propose.split(answer)
            offer_note = self._offer_work(offers) if offers else ""
            answer = answer.strip()
            # ВНУТРЕННЯЯ РЕЧЬ — ОТДЕЛЬНЫЙ КАНАЛ (v0.18.0). Строки с 💭 вынимаем из
            # ответа и пишем в свой лог (brain.inner), а в речь/журнал идёт только
            # сказанное. Так «думает» и «говорит» — РАЗНОЕ, как у Миланы в AgentWorld.
            inner, spoken = self._split_thoughts(answer)
            for th in inner:
                audit.thought(th)
            spoken = (spoken or "").strip()
            if not spoken:
                # Мозг выдал только внутренние мысли — сказанного нет, живём дальше.
                # А вот предложение работы — это уже ДЕЛО: о нём оператор узнаёт строкой.
                if offer_note:
                    self._last_idle_status = "proposed"
                    return offer_note
                self._last_idle_status = "thinking"
                return without_thought()
            # КОПИЮ НЕ ВЫДАЁМ ЗА МЫСЛЬ (v0.17.0). Из сравнения убираем СВОЮ прошлую
            # мысль: она стоит в промпте строкой «твоя прошлая заметка была …», и
            # без этого повтор собственной мысли выглядел бы копией моих слов.
            # Поймал тест v0.16.0 («одна и та же мысль опять») сразу после правки.
            # МУСОР — НЕ МЫСЛЬ (v0.20.2). Проверено ЖИВОЙ пробой на настоящем сервере
            # 26.09.2026: ответ из одних «---» уходил в нить, и следующий запрос модель
            # продолжала «---» бесконечно — поток зацикливался, а оператор видел
            # «ничего не отвечает и не пишет в логи». Чистим разметку и, если слов не
            # осталось, в нить и в канал мыслей НЕ пускаем.
            spoken = self._clean_answer(spoken)
            if not self._meaningful(spoken) or self._degenerate(spoken):
                self._junk_streak += 1
                self._last_idle_status = "junk"
                audit.log("brain.junk", text=spoken[:120], streak=self._junk_streak)
                if self._mute_after_junk():
                    return i18n.t("ag.muted")
                return i18n.t("ag.junk", streak=self._junk_streak)
            self._junk_streak = 0
            copied = self._echo_of(spoken, self._prompt_skeleton(), self._system_text())
            if copied:
                self._echo_streak += 1
                self._last_idle_status = "echo"
                audit.log("brain.echo", text=spoken[:200], copied_from=copied[:120],
                          streak=self._echo_streak)
                if self._mute_after_echoes():
                    return i18n.t("ag.muted")
                return i18n.t("ag.echo", text=copied[:80], streak=self._echo_streak)
            # ПОВТОР НЕ ПРЯЧЕМ (v0.16.0). Найдено живым замером потока: за 6 тактов
            # мозг спросили 6 раз, каждый раз он ответил — а в журнал попала ОДНА
            # мысль. Прежняя защита от повтора гасила повтор в тишину, и для оператора
            # это выглядело как «ничего не делает». Теперь повтор СКАЗАН и помечен
            # (repeat=True): видно и то, что существо живёт, и то, что модель
            # зациклилась. Второе — её потолок (0.5b), а не молчание существа.
            # ВКЛАДКА «МЫСЛИ» БЫЛА ПУСТА (v0.20.0, жалоба 26.09.2026: «сделала вкладку
            # Мысли — но там пусто»). Причина: канал 💭 наполнялся ТОЛЬКО метками самой
            # модели, а 0.5b их ставить не умеет — файла thoughts.log не было вовсе.
            # Но в потоке ответ модели И ЕСТЬ её размышление вслух: отдельного
            # «сказанного» там нет. Поэтому ответ идёт в канал мыслей (копии-эхо —
            # нет: они возвращаются выше и до этой строки не доходят).
            if not inner:
                audit.thought(spoken)
            if spoken == (self._last_thought or ""):
                self._repeat_streak += 1
                self._last_idle_status = "repeating"
                audit.log("brain.thought", text=spoken[:400], repeat=True,
                          streak=self._repeat_streak)
                return i18n.t("ag.repeat", text=spoken, streak=self._repeat_streak)
            self._echo_streak = 0
            self._repeat_streak = 0
            self._junk_streak = 0
            self._last_thought = spoken[:400]        # в промпт и нить — с потолком
            self._thread_append(spoken)
            self._last_idle_status = "thinking"
            # Мысль пишем в журнал: раньше существо думало МОЛЧА, и это выглядело
            # как «он овощ» — думал, а в журнале не было ни одной строки.
            audit.log("brain.thought", text=spoken[:400])
            # Предложение едет хвостом к мысли: оператор видит и то, что существо
            # подумало, и то, что оно хочет сделать дальше.
            return spoken if not offer_note else f"{spoken} · {offer_note}"
        return without_thought()

    # ---------- жизнь ----------
    def run_forever(self, max_ticks: int | None = None, interval: float | None = None) -> list[dict]:
        pause = float(interval if interval is not None else self.cfg.get("tick_seconds", 60))
        seen: list[dict] = []
        n = 0
        while max_ticks is None or n < max_ticks:
            seen.append(self.tick())
            n += 1
            print(json.dumps(seen[-1], ensure_ascii=False))
            if max_ticks is not None and n >= max_ticks:
                break
            try:
                # Слово Стаса 25.09.2026: «бесконечное время жизни в 0 секунд —
                # чтобы швов между тиками не было». 0 значит непрерывный поток:
                # такт перетекает в такт без спячки. Пустой холостой момент дышит
                # 50 мс (не крутим процессор впустую), а не спит минуту.
                time.sleep(pause if pause > 0 else 0.05)
            except KeyboardInterrupt:
                break
        return seen

    def status(self) -> dict:
        st = self.load_state()
        return {"name": self.cfg.get("name", "Synapse"), "roles": self.body.roles_summary(),
                "body": self.body.describe(), "energy": round(self.body.energy(), 5),
                "intent": self.body.intent(), "brain": self.brain.mode(),
                "state": st, "queued": [m["id"] for m in missions.queued()],
                "last_audit": audit.tail(3)}
