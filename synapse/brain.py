"""Мозг Synapse: свой llama-server внутри проекта, без оглядки на AI_UI.

Закон Стаса (24.09.2026): «модель должна быть с телом вместе — сама себе сервер,
существо автономное, ai_ui не при чём». Поэтому:

  * Synapse держит СВОЮ копию рантайма и модели в models/brain/ (provision);
  * Synapse сам поднимает llama-server на СВОЁМ порту и сам его гасит;
  * если локальной копии ещё нет — честно говорит, чего не хватает и откуда
    копировать (source_*), а не падает и не ищет чужие пути по всему диску.

Режимы, и все честные (v0.30.0 — облако добавлено, локальный остался):
  off    — мозга нет, живёт слой рефлексов;
  server — мозг включён, OpenAI-совместимый endpoint (llama.cpp server).

ОТКУДА БЕРЁТСЯ МЫСЛЬ — отдельная ось brain.online.mode (слово Стаса 30.09.2026):
  local  — только свой сервер;   online — только облако (свой сервер не нужен);
  hybrid — облако, когда готово и бюджет есть, иначе свой сервер.
Разведены намеренно: «включён ли мозг» и «где он живёт» — разные вопросы. Выбор
пути делает route(), сам запрос — synapse/online.py, а бюджет и маска адресов —
там же (наружу публичные адреса не уезжают).

Почему 0.5b — «дежурный», а не «решающий»: он быстрый и всегда на месте,
его роль — заметить, назвать, напомнить, а не рассуждать за старшего.
Старший мозг (12b) придёт позже — место под него уже то же самое.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from synapse import audit, config, i18n, online

IS_WIN = platform.system().lower().startswith("win")

#: Сколько ждать ответа сервера при проверке «жив ли мозг».
#: ЗАМЕР 26.09.2026 (нашёл разбор жалобы «очень тормозит и ничего не отвечает»):
#: на этой машине соединение с ЗАКРЫТЫМ портом не отказывает, а молчит до конца
#: таймаута — при прежних 3 секундах проба стоила 2.05 с и вызывалась из ГЛАВНОГО
#: потока окна (панель замирала), а прогон тестов test_window_fits растягивался с 3
#: секунд до минут. На живом локальном сервере ответ приходит за миллисекунды,
#: поэтому 0.25 с хватает с запасом, а цена ошибки падает в восемь раз.
PROBE_TIMEOUT = 0.25

#: Путь к файлу с номером сервера. Модульная переменная, а не вычисление на месте:
#: тесты подменяют её (temp_live), чтобы номер чужого сервера не попал в живые data/.
PID_FILE = config.DATA / "brain.pid"
PROGRESS_STEP = 25 * 1024 * 1024          # о прогрессе копирования — каждые 25 МБ

#: ЗАМОК ПОДЪЁМА СЕРВЕРА (v0.26.0). Живая поломка 26.09.2026: два вызова start_server
#: подряд (кнопка «включить мозг» и такт, каждый в своём потоке) поднимали ДВА сервера
#: на один порт. Второй не вставал, умирал с кодом 1 — и именно его панель считала
#: своим, поэтому в чате появлялось «сервер не стартовал», пока настоящий грузился.
#: Загрузка модели на этой машине 20–30 с, то есть окно для гонки очень широкое.
_START_LOCK = threading.Lock()


class Brain:
    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = (cfg or config.load()).get("brain", {})
        self._proc: subprocess.Popen | None = None
        self._log_fh = None                       # вывод своего сервера — в файл, не в пустоту
        self._last_try = 0.0                      # когда в последний раз пробовали поднять свой сервер
        self._avail: tuple[float, bool] | None = None   # память пробы: (когда, отвечает ли)
        self._log_mark = 0                        # с какого байта начался ЭТОТ запуск сервера

    def reload(self, cfg: dict) -> None:
        """Перечитать настройки после правки в окне (v0.20.0).

        Поймал мой же тест: `self.cfg` берётся ОДИН РАЗ при рождении, поэтому после
        «Сохранить» мозг продолжал видеть прежнюю модель, порт и слои — в окне выбор
        менялся, а сервер поднимался бы на старом. Память пробы тоже сбрасываем: порт
        мог смениться, и прежнее «отвечает» стало бы ложью.
        """
        self.cfg = (cfg or {}).get("brain", {}) or {}
        self._avail = None

    # ---------- где что лежит ----------
    @property
    def local_dir(self) -> Path:
        p = Path(str(self.cfg.get("local_dir", "models/brain")))
        return p if p.is_absolute() else config.ROOT / p

    @property
    def exe(self) -> Path:
        name = "llama-server.exe" if IS_WIN else "llama-server"
        local = self.local_dir / name
        if local.exists():
            return local
        src = Path(str(self.cfg.get("source_runtime", ""))) / name
        return src if src.exists() else local

    @property
    def model_path(self) -> Path:
        name = Path(str(self.cfg.get("model", "model.gguf"))).name
        local = self.local_dir / name
        if local.exists():
            return local
        src = Path(str(self.cfg.get("source_model", "")))
        return src if src.exists() else local

    def model_short(self) -> str:
        """Короткое имя ЖИВОЙ модели: «1.5b», «0.5b» — для подписей в окне.

        Зачем (v0.23.0, жалоба 26.09.2026): в поле ввода было жёстко зашито
        «спросить мозг (0.5b)», а в настройках стояла 1.5b — подпись врала
        оператору о том, кого он спрашивает.
        """
        name = Path(str(self.cfg.get("model", ""))).stem            # без .gguf
        m = re.search(r"(\d+(?:\.\d+)?)\s*b", name, re.I)         # 1.5b / 0.5b
        return m.group(0).lower() if m else (name or "?")

    def mode(self) -> str:
        return str(self.cfg.get("mode", "off"))

    def set_mode(self, mode: str) -> Path:
        """Переключить режим в СВОЁМ конфиге (панель делает это кнопкой)."""
        if mode not in ("off", "server"):
            raise ValueError(f"нет такого режима: {mode}")
        cfg = config.load()
        cfg.setdefault("brain", {})["mode"] = mode
        path = config.save(cfg)
        self.cfg = cfg["brain"]
        audit.log("brain.mode", mode=mode)
        return path

    def cloud_mode(self) -> str:
        """Откуда берём мысль: local | online | hybrid (brain.online.mode)."""
        return online.mode(self.cfg)

    def set_cloud_mode(self, mode: str) -> Path:
        """Переключить источник мысли в СВОЁМ конфиге (то же, что галка в настройках)."""
        if mode not in online.MODES:
            raise ValueError(f"нет такого источника мысли: {mode}")
        cfg = config.load()
        cfg.setdefault("brain", {}).setdefault("online", {})["mode"] = mode
        path = config.save(cfg)
        config.forget_cache()
        self.cfg = config.load().get("brain") or {}
        audit.log("brain.cloud_mode", mode=mode)
        return path

    def route(self) -> str:
        """ОТКУДА мысль ПРЯМО СЕЙЧАС: local | online | none.

        Решение принимается кодом, а не моделью: мозг не выбирает, где он живёт.
        online — только облако: если облако не готово, существо молчит (тихой
        подмены на локальное нет, это и была просьба «только облако»).
        hybrid — облако, когда готово; иначе локальный сервер.
        """
        if self.mode() != "server":
            return "none"
        cm = online.mode(self.cfg)
        if cm == "online":
            return "online" if online.ready(self.cfg) else "none"
        if cm == "hybrid":
            return "online" if online.ready(self.cfg) else "local"
        return "local"

    def state(self, ttl: float = 2.0) -> dict:
        """Всё, что панель должна показать про мозг, одним словарём."""
        alive = self.available(ttl=ttl)
        return {
            "mode": self.mode(),
            "available": alive,
            "endpoint": self.cfg.get("endpoint"),
            "port": int(self.cfg.get("server_port", 11435)),
            "local_dir": str(self.local_dir),
            "exe": str(self.exe),
            "exe_local": (self.local_dir / self.exe.name).exists(),
            "model": str(self.model_path),
            "running_model": self.running_model() if alive else None,
            "stale_model": self.model_mismatch() if alive else False,
            "model_local": (self.local_dir / Path(str(self.cfg.get("model", "x"))).name).exists(),
            "self_contained": (self.local_dir / self.exe.name).exists()
                              and (self.local_dir / Path(str(self.cfg.get("model", "x"))).name).exists(),
            # ОБЛАКО (v0.30.0): режим, провайдер, модель, ключ есть/нет, бюджет и
            # ПРИЧИНА отказа по коду (nokey/nourl/nomodel/limit/calls) — окно скажет
            # словами, а не оставит оператора гадать, почему облако молчит.
            "cloud": online.state(self.cfg),
            "route": self.route(),
        }

    # ---------- здоровье сервера ----------
    def _base(self) -> str:
        ep = str(self.cfg.get("endpoint", ""))
        return ep.rsplit("/chat/completions", 1)[0]

    def _root_url(self) -> str:
        """КОРЕНЬ сервера: llama.cpp отдаёт /props и /health в корне, НЕ под /v1.

        Поймано ЖИВЫМ прогоном 26.09.2026: я спросила /v1/props, сервер ответил 404,
        running_model() вернул None — и сверка модели МОЛЧА не работала, то есть
        сирота с чужой моделью снова прошёл бы за своего. Тихая ошибка опаснее громкой.
        """
        base = self._base()
        for tail in ("/v1", "/api"):
            if base.endswith(tail):
                base = base[: -len(tail)]
        return base

    def available(self, timeout: float = PROBE_TIMEOUT, ttl: float = 2.0) -> bool:
        """Отвечает ли сервер. Ответ помним ttl секунд.

        Панель спрашивает это каждые 8 секунд в главном потоке окна: без памяти
        каждый вопрос это сетевой запрос (замер 3.4 мс, а при молчащем сервере —
        до timeout), и окно на это время встаёт.

        ОБЛАКО (v0.30.0): если мысль берётся из облака, «жив ли мозг» — это вопрос
        про ключ и бюджет, а НЕ про локальный порт. Пробу по сети здесь не делаем:
        каждый кадр окна дёргать платный endpoint — это деньги и лишние запросы.
        В hybrid готовое облако делает мозг живым даже при погашенном сервере —
        иначе готовность мозга зависела бы от того, чего мы как раз избегаем.
        """
        cm = online.mode(self.cfg)
        if cm in ("online", "hybrid"):
            if online.ready(self.cfg):
                return True
            if cm == "online":
                return False
        now = time.time()
        if ttl > 0 and self._avail is not None and now - self._avail[0] < ttl:
            return self._avail[1]
        ok = self._probe(timeout)
        self._avail = (now, ok)
        return ok

    def _probe(self, timeout: float = PROBE_TIMEOUT) -> bool:
        """Отвечает ли сервер — ДЕШЁВОЙ проверкой, а не полноценным urllib-запросом.

        Почему не urlopen (замер 26.09.2026): urlopen на закрытом порту шёл 2.05 с
        (urllib делает свои попытки поверх молчащего сокета), и эта цена платилась из
        главного потока окна. Здесь один connect с коротким таймаутом и минимальный
        GET: живого локального сервера это касается за миллисекунды.
        """
        try:
            parsed = urllib.parse.urlsplit(self._base())
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or int(self.cfg.get("server_port", 11435))
        except ValueError:
            return False
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                sock.settimeout(timeout)
                sock.sendall(b"GET /models HTTP/1.0\r\nHost: " + host.encode("ascii", "ignore") + b"\r\n\r\n")
                head = sock.recv(64)
            return head.startswith(b"HTTP/1") and b" 200" in head[:12]
        except (OSError, ValueError):
            return False

    # ---------- своя копия (тело + мозг в одном доме) ----------
    def provision(self, on_progress=None) -> dict:
        """Скопировать рантайм и модель в models/brain. Второй раз — ничего не делает."""
        src_rt = Path(str(self.cfg.get("source_runtime", "")))
        src_md = Path(str(self.cfg.get("source_model", "")))
        report = {"copied": [], "skipped": [], "missing": [], "bytes": 0, "errors": []}
        if not src_rt.exists():
            report["missing"].append(str(src_rt))
        if not src_md.exists():
            report["missing"].append(str(src_md))
        if report["missing"]:
            audit.log("brain.provision.missing", missing=report["missing"])
            return report

        self.local_dir.mkdir(parents=True, exist_ok=True)
        done = 0
        for item in sorted(src_rt.rglob("*")):
            if item.is_dir():
                continue
            dst = self.local_dir / item.relative_to(src_rt)
            try:
                if dst.exists() and dst.stat().st_size == item.stat().st_size:
                    report["skipped"].append(dst.name)
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dst)
                size = dst.stat().st_size
                report["bytes"] += size
                done += size
                report["copied"].append(dst.name)
                if on_progress and done >= PROGRESS_STEP:
                    done = 0
                    on_progress(dst.name, report["bytes"])
            except OSError as e:
                report["errors"].append(f"{item.name}: {e}")

        name = Path(str(self.cfg.get("model", "model.gguf"))).name
        dst_model = self.local_dir / name
        try:
            if not (dst_model.exists() and dst_model.stat().st_size == src_md.stat().st_size):
                shutil.copy2(src_md, dst_model)
                report["bytes"] += dst_model.stat().st_size
                report["copied"].append(dst_model.name)
                if on_progress:
                    on_progress(dst_model.name, report["bytes"])
            else:
                report["skipped"].append(dst_model.name)
        except OSError as e:
            report["errors"].append(f"{name}: {e}")

        report["self_contained"] = (self.local_dir / self.exe.name).exists() and dst_model.exists()
        audit.log("brain.provision.done", copied=len(report["copied"]),
                  skipped=len(report["skipped"]), mb=round(report["bytes"] / 1048576, 1),
                  errors=report["errors"][:3], self_contained=report["self_contained"])
        return report

    # ---------- свой сервер ----------
    def ngl(self) -> int:
        """Сколько слоёв отдать видеокарте. device=cpu — ноль слоёв (честный CPU).

        Слово Стаса 26.09.2026: «сделай переключение CPU/GPU… я сам попробую и решу».
        Смысл режимов, замеренный на этой машине: GPU быстрее (0.5b: 25 ток/с против
        16.9 на процессоре) но занимает видеопамять и делит её с телом AI_UI; CPU
        медленнее, зато не трогает видеокарту вовсе — это и есть компактный режим.
        """
        device = str(self.cfg.get("device", "gpu") or "gpu").strip().lower()
        if device == "cpu":
            return 0
        try:
            n = int(self.cfg.get("ngl", 10))
        except (TypeError, ValueError):
            n = 10
        return max(0, min(999, n))

    def argv(self) -> list[str]:
        """Полная команда запуска СВОЕГО сервера — одним местом, чтобы это можно было
        проверить тестом, не поднимая сервер (проверка: -ngl 0 при device=cpu)."""
        exe, model = self.exe, self.model_path
        args = [str(exe), "-m", str(model),
                "--port", str(int(self.cfg.get("server_port", 11435)))]
        args += ["-ngl", str(self.ngl()), "-c", str(int(self.cfg.get("ctx", 4096) or 4096))]
        args += [str(a) for a in (self.cfg.get("server_args") or [])]
        return args

    @property
    def log_file(self) -> Path:
        """Куда пишется вывод своего сервера.

        Зачем (живой случай 26.09.2026): сервер с 1.5b умер с кодом 1, а ПРИЧИНЫ не
        было видно нигде — вывод уходил в DEVNULL, и «умер код 1» ничего не объясняло.
        Диагноз без причины — это гадание, поэтому вывод сервера пишем в файл и при
        смерти кладём последние строки в журнал (brain.server.died reason=...).
        """
        return audit.LOG_DIR / "brain-server.log"

    def server_log_tail(self, n: int = 8, since: int | None = None) -> str:
        """Последние непустые строки вывода сервера — одной строкой для журнала.

        `since` — байтовое смещение, с которого начался ЭТОТ запуск (v0.26.0).
        Файл вывода ОБЩИЙ на все запуски, поэтому без смещения в причине смерти
        оказывался хвост ЧУЖОГО процесса: живой случай 26.09.2026 — в причине стояло
        предупреждение CORS, и настоящую ошибку по журналу видно не было.
        """
        try:
            raw = self.log_file.read_bytes()
        except OSError:
            return ""
        if since and len(raw) >= since:      # файл не укоротился ротацией — берём только своё
            raw = raw[since:]
        lines = [l.strip() for l in raw.decode("utf-8", errors="replace").splitlines() if l.strip()]
        return " ⏎ ".join(lines[-n:])[:400]

    def _open_server_log(self):
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            if self.log_file.exists() and self.log_file.stat().st_size > 1048576:
                keep = self.log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
                self.log_file.write_text("\n".join(keep) + "\n", encoding="utf-8")
            return self.log_file.open("a", encoding="utf-8", errors="replace")
        except OSError:
            return None

    def start_server(self, wait_seconds: float = 90.0, on_note=None) -> bool:
        """Поднять СВОЙ llama-server. Ждём, пока он реально ответит (не «запустился и упал»).

        ОДИН ПОДЪЁМ ЗА РАЗ (v0.26.0). Живой разбор 26.09.2026 («нажимал кнопку включить
        мозг, сервер не включался»): загрузка модели занимает 20–30 с, и за это время
        второй вызов — кнопка и такт идут из РАЗНЫХ потоков — успевал запустить ВТОРОЙ
        процесс на тот же порт. Второй не вставал на занятый порт и умирал с кодом 1,
        панель считала «нашим» именно его и говорила «сервер не стартовал», а настоящий
        в это время грузился и потом гибнул как сирота. В журнале это видно буквально:
        15:24:13 старт pid 13960, 15:24:17 старт pid 1496 — два сервера за 4 секунды.
        """
        exe, model = self.exe, self.model_path
        if not exe.exists() or not model.exists():
            audit.log("brain.server.missing", exe=str(exe), model=str(model),
                      hint=i18n.t("brain.hint.provision"))
            if on_note:
                on_note(i18n.t("brain.note.no_copy"))
            return False
        # ЖИВОЙ СЕРВЕР НА ПОРТУ — НЕ ВСЕГДА НАШ (v0.20.2): сверяем модель по /props,
        # сироту с чужой моделью убираем и поднимаем свою (живой случай 26.09.2026).
        if self.adopt():
            return True
        if not _START_LOCK.acquire(blocking=False):
            return self._wait_ready(wait_seconds)        # уже поднимают — ждём ЕГО, не плодим
        try:
            extra = self.stop_extra_listeners()          # на порт не встаём вдвоём
            if extra and on_note:
                on_note(i18n.t("brain.note.extra", list=", ".join(str(p) for p in extra)))
            args = self.argv()
            flags = 0x08000000 if IS_WIN else 0      # CREATE_NO_WINDOW: без чёрного окна
            self._log_fh = self._open_server_log()
            self._log_mark = self._log_size()            # причина смерти — ТОЛЬКО свои строки
            try:
                self._proc = subprocess.Popen(args, cwd=str(exe.parent), creationflags=flags,
                                              stdout=self._log_fh or subprocess.DEVNULL,
                                              stderr=subprocess.STDOUT if self._log_fh else subprocess.DEVNULL)
            except OSError as e:
                audit.log("brain.server.error", error=str(e)[:200])
                if on_note:
                    on_note(i18n.t("brain.note.start_failed", error=e))
                return False
            audit.log("brain.server.started", port=int(self.cfg.get("server_port", 11435)),
                      exe=str(exe), model=model.name, pid=getattr(self._proc, "pid", None))
            self._remember_pid(getattr(self._proc, "pid", None))
            return self._wait_ready(wait_seconds, proc=self._proc, on_note=on_note)
        finally:
            _START_LOCK.release()

    def _log_size(self) -> int:
        """Размер файла вывода сервера — метка начала ЭТОГО запуска."""
        try:
            return self.log_file.stat().st_size
        except OSError:
            return 0

    def _wait_ready(self, wait_seconds: float = 90.0, proc=None, on_note=None) -> bool:
        """Дождаться ответа сервера; если процесс умер — сказать причину вслух.

        `proc=None` — ждём ЧУЖОЙ подъём (второй вызов start_server): процесс не наш,
        но ответа ждём столько же, чтобы кнопка не говорила «нет» раньше времени.
        """
        deadline = time.time() + max(5.0, wait_seconds)
        while time.time() < deadline:
            if proc is not None and proc.poll() is not None:   # процесс умер — не притворяемся живыми
                audit.log("brain.server.died", code=proc.returncode,
                          reason=self.server_log_tail(since=self._log_mark))
                if on_note:
                    on_note(i18n.t("brain.note.crashed", code=proc.returncode))
                return False
            if self.available(timeout=2.0, ttl=0.0):   # ждём подъёма — тут память вредна
                self._avail = (time.time(), True)
                return True
            time.sleep(1.0)
        if proc is not None and on_note:
            on_note(i18n.t("brain.note.no_answer"))
        return False

    def stop_server(self) -> None:
        """Погасить мозг — свой процесс или тот, что подняла другая команда.

        Без этого сервер, поднятый в консоли, живёт вечно: панель его
        подхватывает как готовый (отвечает на /models), но владельца не знает и убить
        его не может — кнопка «выключить» молчала бы впустую.
        """
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            audit.log("brain.server.stopped", pid=getattr(self._proc, "pid", None))
        else:
            pid = self.foreign_pid()               # чужой сервер — гасим по PID из файла
            if pid:
                self._kill_pid(pid)
                audit.log("brain.server.stopped", pid=pid, adopted=True)
            else:
                # СИРОТА БЕЗ PID-ФАЙЛА (v0.20.2). Живой случай 26.09.2026: сервер
                # прошлой сессии держал порт, «выключить мозг» молчал впустую, а окно
                # на закрытии писало brain=stopped, ничего не гася.
                killed = self.stop_port_listeners()
                if killed:
                    audit.log("brain.server.stopped", pids=killed, adopted=True)
        self._proc = None
        try:
            if self._log_fh:
                self._log_fh.close()
        except OSError:
            pass
        self._log_fh = None
        self._avail = (time.time(), False)         # погасили — помним, что он молчит
        self._forget_pid()

    # ---------- жизнь мозга между запусками (файл PID) ----------
    @property
    def pid_file(self) -> Path:
        """Файл с номером нашего сервера.

        v0.21.0 — ЧЕРЕЗ МОДУЛЬНУЮ ПЕРЕМЕННУЮ, а не напрямую от config.DATA.
        Причина найдена ЖИВОЙ проверкой: `temp_live()` уводит в temp очередь,
        журналы, отчёты и состояние, но НЕ config.DATA (там же лежит живое тело
        data/body/worm.json), поэтому номер сервера, поднятого тестом или
        проверкой, оседал в ЖИВОМ data/brain.pid. Теперь путь можно подменить —
        так же, как уже сделано для очереди и отчётов (tests/live_data.py).
        """
        return PID_FILE

    def _remember_pid(self, pid) -> None:
        if not pid:
            return
        try:
            self.pid_file.parent.mkdir(parents=True, exist_ok=True)
            self.pid_file.write_text(str(int(pid)), encoding="utf-8")
        except (OSError, ValueError):
            pass

    def _forget_pid(self) -> None:
        try:
            self.pid_file.unlink()
        except OSError:
            pass

    def _is_our_server(self, pid: int) -> bool:
        """Жив ли процесс и это ли именно llama-server (не убить чужое по протухшему PID)."""
        if pid <= 0:
            return False
        try:
            if IS_WIN:
                r = subprocess.run(["tasklist", "/fi", f"PID eq {pid}", "/fo", "csv", "/nh"],
                                   capture_output=True, timeout=15)
                return "llama-server" in r.stdout.decode("cp1251", errors="replace").lower()
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                return b"llama-server" in f.read()
        except (OSError, subprocess.SubprocessError):
            return False

    def foreign_pid(self) -> int | None:
        """PID сервера, поднятого ДРУГИМ процессом (консоль или прошлая панель)."""
        try:
            pid = int(self.pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        return pid if self._is_our_server(pid) else None

    def _kill_pid(self, pid: int) -> None:
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=15)
            else:
                os.kill(pid, 15)
        except (OSError, subprocess.SubprocessError):
            pass

    # ---------- лишние серверы на нашем порту ----------
    def listeners(self) -> list[int]:
        """PID-ы, которые СЛУШАЮТ наш порт (не «подключены», а именно слушают).

        Зачем: Windows разрешает двум процессам встать на один порт (SO_REUSEADDR),
        и 24.09 так и вышло — два llama-server слушали 11435 одновременно, каждый
        держал свою копию модели (лишние ~600 МБ) и отвечал «вперемешку».
        """
        port = int(self.cfg.get("server_port", 11435))
        pids: list[int] = []
        try:
            if IS_WIN:
                # ПУСТОЙ ВЫВОД NETSTAT ПЕРЕСПРАШИВАЕМ (v0.28.0). Пустая таблица — это
                # «не спросили», а не «никого нет»: цена ошибки не косметическая, пустой
                # ответ читается как «слушателей нет», и тогда на занятый порт встаёт
                # ВТОРОЙ сервер — ровно та беда, от которой эта проверка и написана.
                # (Три попытки стоят миллисекунды; в моей среде замера 30.09.2026 тест
                # TestExtraListeners падал через раз, но пустым таблица не оказывалась —
                # это подстраховка на будущее, а не доказанная причина того падения.)
                text = ""
                for _ in range(3):
                    out = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                                         capture_output=True, timeout=20).stdout
                    text = out.decode("cp1251", errors="replace")
                    if text.strip():
                        break
                    time.sleep(0.15)
                for line in text.splitlines():
                    parts = line.split()
                    if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
                        if parts[1].endswith(f":{port}"):
                            pids.append(int(parts[4]))
            else:
                out = subprocess.run(["ss", "-ltnp"], capture_output=True, timeout=20).stdout
                for line in out.decode("utf-8", errors="replace").splitlines():
                    if f":{port}" in line:
                        m = re.search(r"pid=(\d+)", line)
                        if m:
                            pids.append(int(m.group(1)))
        except (OSError, subprocess.SubprocessError, ValueError):
            return []
        return sorted({p for p in pids if p > 0})

    def stop_extra_listeners(self) -> list[int]:
        """Убрать ЛИШНИХ слушателей нашего порта (свой процесс и pid-файл не трогаем).

        Убиваем только то, что действительно llama-server (`_is_our_server`):
        по протухшему или чужому pid нельзя гасить произвольный процесс.
        """
        mine = getattr(self._proc, "pid", None)
        keep = self.foreign_pid()
        killed: list[int] = []
        for pid in self.listeners():
            if pid == mine or pid == keep:
                continue
            if self._is_our_server(pid):
                self._kill_pid(pid)
                killed.append(pid)
        if killed:
            audit.log("brain.server.extra_killed", pids=killed, port=int(self.cfg.get("server_port", 11435)))
        return killed

    # ---------- кто на нашем порту (v0.20.2) ----------
    def props(self, timeout: float = max(PROBE_TIMEOUT, 1.0)) -> dict | None:
        """Что о себе говорит сервер на нашем порту (llama.cpp: /props).

        Зачем, живой случай 26.09.2026: на порту 11435 сидел СИРОТА 0.5b (pid 5412,
        pid-файла нет, прошлая сессия), а в настройках оператор поставил 1.5b.
        Существо отвечало сиротой, и выбор оператора не работал вовсе — выглядело как
        «ничего не изменилось». Теперь спрашиваем сервер, КАКУЮ модель он держит,
        и чужую за свою не принимаем.
        """
        try:
            with urllib.request.urlopen(self._root_url() + "/props", timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
            return data if isinstance(data, dict) else None
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def running_model(self) -> str | None:
        """Имя модели, которую сервер держит СЕЙЧАС (None — если не сказал)."""
        p = (self.props() or {}).get("model_path")
        return Path(str(p)).name if p else None

    def model_mismatch(self) -> bool:
        """На порту живёт сервер с ДРУГОЙ моделью, чем в наших настройках."""
        want = Path(str(self.cfg.get("model", ""))).name.lower()
        have = (self.running_model() or "").lower()
        return bool(want and have and want != have)

    def stop_port_listeners(self) -> list[int]:
        """Погасить ВСЕХ llama-server на нашем порту, даже БЕЗ pid-файла (сирот).

        Сирота — сервер прошлой сессии: pid-файла уже нет, «своим» он не числится, и
        прежний stop_server тронуть его не мог (окно писало brain=stopped, ничего не
        гася). Проверяем имя процесса, чтобы по случайному pid не убить чужое.
        """
        killed: list[int] = []
        mine = getattr(self._proc, "pid", None)
        for pid in self.listeners():
            if pid == mine:
                continue
            if self._is_our_server(pid):
                self._kill_pid(pid)
                killed.append(pid)
        if killed:
            audit.log("brain.server.orphan_killed", pids=killed,
                      port=int(self.cfg.get("server_port", 11435)))
        return killed

    def adopt(self) -> bool:
        """Признать живого сервера на нашем порту СВОИМ. False — его нет или он не тот.

        Три исхода:
          * никто не отвечает — False (пусть поднимается свой start_server);
          * отвечает ЧУЖАЯ модель (сверка по /props) — гасим и False;
          * отвечает наша модель — True; если pid-файла нет (сервер подняла другая
            команда), запоминаем pid, чтобы панель могла его погасить.
        """
        if not self.available(ttl=0.0):
            return False
        have = self.running_model()
        if self.model_mismatch():
            killed = self.stop_port_listeners()
            audit.log("brain.server.stale", pids=killed,
                      want=Path(str(self.cfg.get("model", ""))).name, have=have)
            self._avail = None
            return False
        if not self._proc and not self.foreign_pid():
            pids = self.listeners()
            if pids:
                self._remember_pid(pids[0])
                audit.log("brain.server.adopted", pid=pids[0], model=have)
        return True

    def ensure(self, on_note=None, retry_after: float = 30.0) -> bool:
        """Ничего не делает в режиме «только облако»: свой сервер там не нужен вовсе."""
        if self.mode() == "server" and online.mode(self.cfg) == "online":
            return online.ready(self.cfg)
        """Мозг включён — значит сервер обязан быть жив (поднимем, если надо).

        Попытка стоит до 90 секунд ожидания, поэтому в такте её делаем НЕ ЧАЩЕ раза
        в retry_after секунд: пусть без модели тело живёт дальше, а не стоит колом.

        РАЗРЕЖЕНИЕ 300 -> 30 (v0.25.0). Живая жалоба 26.09.2026: «мозг упал и больше
        не включался, прошло уже почти 20 мин». С прежним числом после ОДНОЙ неудачи
        следующая попытка была только через пять минут, и всё это время в журнале не
        появлялось ни строки — молчание мозга выглядело как мёртвое существо.
        """
        if self.mode() != "server":
            return False
        now = time.time()
        # СНАЧАЛА разрежение, ПОТОМ проба (v0.15.0). Найдено живым замером: проба
        # молчащего сервера стоит до 2 секунд (замер самой available(): 2112 мс), а
        # такт при tick_seconds=0 идёт несколько раз в секунду. Пока порядок был
        # обратный, существо вставало колом ровно тогда, когда модели рядом не было:
        # живой прогон на настройках Стаса дал 2 такта за 3 секунды.
        if now - self._last_try < max(0.0, retry_after):
            return False
        self._last_try = now
        if self.adopt():
            return True
        ok = self.start_server(on_note=on_note)
        if not ok:
            # НЕ ПОДНЯЛСЯ — СКАЗАНО ВСЛУХ (v0.25.0), а не проглочено.
            audit.log("brain.down", mode=self.mode(),
                      port=int(self.cfg.get("server_port", 11435)),
                      retry_in=int(max(0.0, retry_after)))
        return ok

    # ---------- мышление ----------
    def system_prompt(self) -> str:
        """Системный промпт мозга — из modelfile.md (его личность и правила).

        Слово Стаса 25.09.2026: «сделай модфайл отдельный, заполни по нашему
        стандарту». Раньше «кто я» и «как отвечать» были раскиданы по словарю
        (ag.ask_system, ag.think_system) и строке role в конфиге. Теперь один
        файл — modelfile.md в корне проекта. Нет файла — короткий честный
        запасной текст, а не падение.
        """
        rel = str(self.cfg.get("system_prompt_file", "modelfile.md"))
        p = Path(rel)
        if not p.is_absolute():
            p = config.ROOT / p
        try:
            text = p.read_text(encoding="utf-8").strip()
        except OSError:
            return ("You are Synapse, a small organism with the nervous system of a worm. "
                    "Answer to the point, without making things up.")
        # КОРОТКИЙ ПРОМПТ ДЛЯ МАЛОГО МОЗГА (v0.16.0, найдено ЖИВЫМ замером).
        # С полным modelfile.md (правила списком) 0.5b принимала его за текст, который
        # надо ПРОДОЛЖИТЬ, и вместо ответа выдавала его же пункты: «Я сейчас делаю
        # следующее: 1. Отвечать на вопросы пользователя по делу…». С коротким промптом
        # та же модель отвечает по делу («Я здесь, как ваш питомец»). Поэтому по
        # умолчанию берём раздел ## System Prompt; весь файл — если раздела нет или в
        # настройках стоит prompt_mode: full (это для старшего мозга, 12b).
        if str(self.cfg.get("prompt_mode", "section") or "section").lower() == "full":
            return text
        section = self._section(text, str(self.cfg.get("prompt_section", "System Prompt")))
        return section or text

    @staticmethod
    def _section(text: str, title: str) -> str:
        """Кусок modelfile.md между заголовками ## — короткий промпт для 0.5b.

        Раздела нет — вернём пусто, и вызывающий честно возьмёт весь файл: немой
        мозг хуже длинного промпта.
        """
        want = "## " + title.strip().lower()
        out: list[str] = []
        inside = False
        for line in text.splitlines():
            low = line.strip().lower()
            if low.startswith("## "):
                if inside:
                    break
                inside = (low == want)
                continue
            if inside:
                out.append(line)
        return "\n".join(out).strip()

    def think(self, prompt: str, system: str = "", history=None) -> str | None:
        """Ответ модели или None (мозг выключен / сервер молчит) — без исключений.

        ОБЛАКО (v0.30.0): сначала спрашиваем route(). online — ответ только из облака
        (и молчание, если оно не ответило); hybrid — облако, а при отказе свой сервер
        с записью brain.online.fallback; local — прежний путь целиком.

        history — НИТЬ (v0.18.0): список сообщений [{"role","content"}, ...], которые
        встают между системным промптом и текущим вопросом. Раньше поток каждый такт
        задавал мозгу НОВЫЙ вопрос без памяти — это и был луп, а не поток. Теперь мозг
        видит свои прошлые мысли и продолжает ту же, а не начинает с нуля.
        """
        plan = self.route()
        if plan == "online":
            res = online.ask(self.cfg, system or self.system_prompt(), prompt, history)
            if res.get("ok"):
                return str(res.get("text") or "").strip() or None
            if online.mode(self.cfg) == "online":
                # ТОЛЬКО ОБЛАКО — значит молчим вместе с ним, а не подсовываем
                # локальный ответ за ответ облака (это была бы тихая подмена).
                audit.log("brain.online.fail", error=res.get("error"),
                          code=res.get("code"), route=plan)
                return None
            # hybrid: облако не смогло — говорим об этом в журнал и продолжаем своим.
            audit.log("brain.online.fallback", error=res.get("error"),
                      code=res.get("code"), route=plan)
        if self.mode() != "server" or not self.available():
            return None
        msgs = [{"role": "system", "content": system or self.system_prompt()}]
        for m in (history or []):
            if isinstance(m, dict) and m.get("role") in ("user", "assistant", "system") and m.get("content"):
                msgs.append({"role": m["role"], "content": m["content"]})
        msgs.append({"role": "user", "content": prompt})
        body = {
            "model": self.cfg.get("model", "local"),
            "messages": msgs,
            "max_tokens": int(self.cfg.get("max_tokens", 400)),
            "temperature": float(self.cfg.get("temperature", 0.3)),
            # ШТРАФ ЗА ПОВТОР (v0.23.0, живой замер 26.09.2026). 1.5b на этой машине
            # без него уходит в петлю: ответ «**Мышление:** **Текст:**» — тот же оборот
            # двадцать раз подряд. С repeat_penalty 1.15 тот же запрос дал связный
            # ответ вместо петли. Значение — настройкой brain.repeat_penalty.
            "repeat_penalty": float(self.cfg.get("repeat_penalty", 1.15)),
            "repeat_last_n": int(self.cfg.get("repeat_last_n", 96)),
            "stream": False,
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            str(self.cfg.get("endpoint")), data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=float(self.cfg.get("timeout", 120))) as r:
                payload = json.loads(r.read().decode("utf-8", errors="replace"))
            text = (payload.get("choices") or [{}])[0].get("message", {}).get("content", "")
            audit.log("brain.answer", mode=self.mode(), chars=len(text or ""))
            return (text or "").strip() or None
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            # Сервер не ответил — ЗАБЫТЬ, что он «жив» (v0.16.0). Иначе память
            # available() ещё ttl секунд отвечает «да», и поток тактов тратит время
            # на запросы к тому, кого уже нет.
            self._avail = None
            audit.log("brain.error", mode=self.mode(), error=str(e)[:200])
            return None
