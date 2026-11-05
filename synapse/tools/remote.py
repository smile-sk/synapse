"""Инструмент: удалённое управление указанным адресом (миссия remote).

Слово Стаса 26.09.2026: «новая миссия — удалённое управление указанным IP
устройством, например по psexec, или свои предложение, т.е. на указанном адресе
должен найтись способ это сделать».

Шаги:
  1) ДВЕРЬ (scope) — до касания хоста: чужие сети не трогаем;
  2) ВЕКТОРЫ УПРАВЛЕНИЯ ищутся на самом хосте: WinRM (5985/5986), WMI/DCOM
     (135+445 — ровно тот путь, которым работает psexec), SMB (445), SSH (22),
     и те, что только сообщают о себе: telnet (23), RDP (3389), VNC (5900);
  3) ЛУЧШИЙ доступный способ действительно пробуется, если дали логин, пароль и
     команду: WinRM через Invoke-Command, WMI через DCOM-сессию CIM (создание
     процесса — аналог psexec), SSH по ключу;
  4) ОТЧЁТ: какие пути открыты, что сработало, что ответило отказом. Отчёт
     читает человек, он же остаётся следом в журнале.

Чего здесь нарочно НЕТ: подбора паролей. Без учётных данных инструмент только
отвечает, какие пути открыты, и говорит, что нужно, чтобы им воспользоваться.

Оговорка, которую обязан сказать: пароль передаётся в командной строке
PowerShell. Мгновенно видеть его может только тот, кто в этот момент смотрит
список процессов на этой машине, — но это надо знать, а не обнаружить.
"""
from __future__ import annotations

import platform
import re
import shutil
import subprocess
from datetime import datetime

from synapse import audit, cancel, config, i18n, scope
from synapse.tools import net_scan, report_io

IS_WIN = platform.system().lower().startswith("win")

#: Векторы управления: порт -> метод. Порядок — по разумности, а не по номеру.
VECTORS = ((5985, "winrm"), (5986, "winrm_tls"), (135, "wmi_dcom"), (445, "smb"),
           (22, "ssh"), (23, "telnet"), (3389, "rdp"), (5900, "vnc"))

#: Чем МОЖНО выполнить команду (остальные векторы только сообщают о себе).
RUNNERS = ("winrm", "winrm_tls", "wmi_dcom", "ssh")

#: Как состояние метода звучит в отчёте. Словами из словаря, а не кодами.
STATE_KEYS = {"found": "rm.state.found", "ok": "rm.state.ok", "denied": "rm.state.denied",
              "unreachable": "rm.state.unreachable", "skipped": "rm.state.skipped"}


def _safe_host(host: str) -> str:
    """Хост в команду — только буквы, цифры, точка, дефис.

    Строка команды собирается из адреса, и подставлять туда что угодно нельзя:
    незакрытая кавычка превратила бы запуск в чужую команду.
    """
    return host if re.fullmatch(r"[A-Za-z0-9._-]+", host or "") else ""


def _q(text: str) -> str:
    """Одинарные кавычки в PowerShell удваиваются — иначе строка рвётся."""
    return str(text or "").replace("'", "''")


def _cred_block(user: str, password: str) -> str:
    """PSCredential из явных логина и пароля (для Invoke-Command и CIM)."""
    return ("$p=ConvertTo-SecureString '" + _q(password) + "' -AsPlainText -Force;"
            "$c=New-Object System.Management.Automation.PSCredential('" + _q(user) + "',$p);")


def _ps(script: str, timeout: float) -> tuple[int, str]:
    """PowerShell одним вызовом. Возврат — (код возврата, вывод+ошибки)."""
    exe = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
    r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, timeout=timeout)
    return r.returncode, net_scan._decode((r.stdout or b"") + (r.stderr or b""))


def probe_ways(host: str, cfg: dict, mission: str = "") -> list[dict]:
    """Какие векторы управления открыты на хосте. Ничего не выполняет."""
    timeout = float((cfg.get("net") or {}).get("connect_timeout", 0.35))
    ways = []
    for port, method in VECTORS:
        if mission and cancel.requested(mission):      # «Удалить» — и обход прекращается
            break
        ways.append({"port": port, "method": method,
                     "open": net_scan.is_open(host, port, timeout)})
    audit.log("remote.probe", mission=mission or None, host=host,
              open=[w["port"] for w in ways if w["open"]])
    return ways


def run_method(method: str, host: str, user: str, password: str, command: str,
               timeout: float, port: int | None = None) -> dict:
    """Попробовать ОДИН способ выполнить команду. Возвращает строку отчёта."""
    row = {"method": method, "port": port, "state": "skipped", "detail": "",
           "exit": None, "output": ""}
    host_q = _safe_host(host)
    if not host_q:
        row["detail"] = i18n.t("rm.bad_host")
        return row
    try:
        if method in ("winrm", "winrm_tls"):
            if not IS_WIN:
                row["detail"] = i18n.t("rm.need_windows")
                return row
            script = (_cred_block(user, password) +
                      "Invoke-Command -ComputerName " + host_q + " -Credential $c "
                      "-ScriptBlock { " + command + " }")
        elif method == "wmi_dcom":
            if not IS_WIN:
                row["detail"] = i18n.t("rm.need_windows")
                return row
            # Это и есть путь psexec: создание процесса на чужой машине через DCOM.
            script = (_cred_block(user, password) +
                      "$o=New-CimSessionOption -Protocol Dcom;"
                      "$s=New-CimSession -ComputerName " + host_q + " -Credential $c "
                      "-SessionOption $o;"
                      "Invoke-CimMethod -CimSession $s -ClassName Win32_Process "
                      "-MethodName Create -Arguments @{CommandLine='" + _q(command) + "'}")
        elif method == "ssh":
            exe = shutil.which("ssh")
            if not exe:
                row["detail"] = i18n.t("rm.no_ssh")
                return row
            target = f"{user}@{host_q}" if user else host_q
            r = subprocess.run([exe, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                                "-o", "StrictHostKeyChecking=accept-new", target, command],
                               capture_output=True, timeout=timeout)
            out = net_scan._decode((r.stdout or b"") + (r.stderr or b""))
            row["exit"], row["output"] = r.returncode, out.strip()[:400]
            # 255 у ssh — это «не пустило/не дозвонились», а не ответ команды.
            row["state"] = "ok" if r.returncode == 0 else (
                "unreachable" if r.returncode == 255 else "denied")
            if row["state"] == "denied":
                row["detail"] = i18n.t("rm.ssh_password")
            return row
        else:
            row["detail"] = i18n.t("rm.not_a_runner")
            return row
        code, out = _ps(script, timeout)
        row["exit"], row["output"] = code, out.strip()[:400]
        row["state"] = "ok" if code == 0 else "denied"
        if code == 0 and method == "wmi_dcom":
            row["detail"] = i18n.t("rm.wmi_started")
    except subprocess.TimeoutExpired:
        row["state"], row["detail"] = "unreachable", i18n.t("rm.timed_out")
    except (OSError, subprocess.SubprocessError) as e:
        row["state"], row["detail"] = "unreachable", f"{type(e).__name__}: {e}"
    return row


def control(params: dict | None = None, mission: str = "", cfg: dict | None = None) -> dict:
    """Миссия remote: найти способ управления хостом и (если дали) выполнить команду."""
    params = params or {}
    cfg = cfg or config.load()
    host = str(params.get("host") or "").strip()
    command = str(params.get("command") or params.get("text") or "").strip()
    user = str(params.get("user") or "").strip()
    password = str(params.get("password") or "")
    rep = {"at": datetime.now().isoformat(timespec="seconds"), "kind": "remote",
           "mission": mission, "host": host, "command": command, "user": user,
           "ways": [], "attempts": [], "method": None, "output": "",
           "state": "probe", "error": None}
    if not host:
        rep["error"] = i18n.t("err.no_host")
        audit.log("remote.failed", mission=mission or None, why=rep["error"])
        return rep
    if not bool((cfg.get("net") or {}).get("allow_remote", True)):
        rep["error"] = i18n.t("err.remote_off")
        audit.log("remote.off", mission=mission or None, host=host)
        return rep
    if not scope.guard(host, cfg):                      # дверь — ДО касания
        rep["error"] = i18n.t("err.door")
        audit.log("remote.refused", mission=mission or None, host=host)
        return rep

    rep["ways"] = probe_ways(host, cfg, mission)
    for w in rep["ways"]:
        w["state_text"] = i18n.t(STATE_KEYS["found"] if w["open"] else STATE_KEYS["skipped"])
    runnable = [w for w in rep["ways"] if w["open"] and w["method"] in RUNNERS]
    if not command or not (user or password):
        # Без доступа и команды мы НЕ гадаем: только говорим, куда можно войти.
        rep["state"] = "found" if runnable else "none"
        rep["hint"] = i18n.t("rm.hint")
        report_io.save(rep, mission=mission, kind="remote")
        audit.log("remote.ways", mission=mission or None, host=host,
                  runnable=[w["method"] for w in runnable], state=rep["state"])
        return rep

    timeout = float((cfg.get("net") or {}).get("remote_timeout", 25))
    for w in runnable:
        if cancel.requested(mission):
            rep["state"] = "stopped"
            break
        row = run_method(w["method"], host, user, password, command, timeout, w["port"])
        row["state_text"] = i18n.t(STATE_KEYS.get(row["state"], "rm.state.skipped"))
        rep["attempts"].append(row)
        audit.log("remote.attempt", mission=mission or None, host=host,
                  method=w["method"], state=row["state"], exit=row["exit"])
        if row["state"] == "ok":
            rep["method"], rep["output"], rep["state"] = w["method"], row["output"], "ok"
            break
        rep["state"] = "denied"
    report_io.save(rep, mission=mission, kind="remote")
    audit.log("remote.done", mission=mission or None, host=host, state=rep["state"],
              method=rep["method"], tried=len(rep["attempts"]))
    return rep
