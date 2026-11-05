"""Отчёты Synapse — человеческим языком и на языке интерфейса.

Отчёт — это РАЗГОВОР С ОПЕРАТОРОМ, а не дамп машины. Поэтому:

  * JSON остаётся для кода (data/reports/*.json) — в него смотрит панель и тесты;
  * рядом ложится .txt, который читается глазами (data/reports/*.txt);
  * в панели показывается тот же человеческий текст, а не сырой JSON.

ЯЗЫК (слово Стаса 25.09.2026: «отчёты в зависимости от языка интерфейса»).
Текст собирается на языке интерфейса — язык приходит параметром `lang`, а если
его не дали, берётся текущий (synapse/i18n.py). Правило одно и то же везде:

    JSON — ИСТИНА, .txt — снимок для глаз, собранный в тот день и на том языке,
    на котором существо тогда говорило.

Поэтому read_text() рисует отчёт заново из JSON на НУЖНОМ языке, а .txt
открывает только если JSON уже нет. Иначе переключение языка оставляло бы старые
отчёты на прежнем языке, и это был бы тот самый «хаус с языком».

Честность, зашитая в текст (не украшение, а правило):
  * порт не переводим в «дыру» — переводим в СЛУЖБУ («445 SMB — общие папки Windows»);
  * «права не проверяли» говорим прямо, а не подразумеваем;
  * придуманных вердиктов нет: пишем то, что служба сказала сама (banner), и молчим,
    если она не назвалась;
  * технические имена (net_audit, write_probe, certs, diff, queued…) не переводятся —
    оператор видит ровно то слово, которое лежит в очереди миссий и в журнале.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from synapse import config, i18n

#: Порты, у которых есть человеческое имя службы (ключи svc.<порт> в словаре).
KNOWN_PORTS = (21, 22, 23, 25, 53, 80, 110, 123, 135, 139, 143, 443, 445, 500, 631,
               1433, 1701, 2121, 3306, 3389, 4500, 5000, 5001, 5222, 5228, 5353, 5432,
               5514, 5900, 8080, 8291, 8728, 8883, 27015)

#: За сколько дней до истечения сертификат считается тревожным.
WARN_DAYS = 30

#: Сколько строк соединений рисуем в человеческом отчёте (дальше — «и ещё N»).
RENDER_ROWS = 40

#: Где лежат отчёты. Путь строим здесь, а не берём из tools/report_io: тот модуль
#: ПИШЕТ отчёты, и тащить его сюда ради чтения не нужно.
REPORTS_DIR = config.DATA / "reports"


def _tx(lang: str | None = None):
    """Собрать t() для конкретного языка: отчёт можно нарисовать НА ЛЮБОМ.

    Так тест рисует один и тот же отчёт по-русски и по-английски, не трогая
    текущий язык приложения.
    """
    code = lang or i18n.lang()

    def t(key: str, **kw) -> str:
        return i18n.t_in(code, key, **kw)

    return t


def port_label(port: int, lang: str | None = None) -> str:
    """Порт, названный службой: '445 SMB — общие папки Windows'."""
    t = _tx(lang)
    port = int(port)
    if port in KNOWN_PORTS:
        return f"{port} {t(f'svc.{port}')}"
    return t("rep.port_unknown", port=port)


def banner(port: int, text: str, lang: str | None = None) -> str:
    """Служба назвалась сама — переводим её слова на человеческий.

    Ничего не выдумываем: если баннера нет — так и говорим.
    """
    t = _tx(lang)
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    if int(port) in (80, 443, 8080, 8000, 5000, 5001):
        # код ответа = часть строки ДО первого заголовка (иначе в «ответ» лезут хвосты)
        head = re.split(r"\s+(?=[A-Z][A-Za-z-]{2,}:)", text)[0]
        m = re.match(r"HTTP/[0-9.]+\s+(.{0,60})", head)
        code = " ".join(m.group(1).split()) if m else ""
        ms = re.search(r"Server:\s*([^;]+?)(?=\s+[A-Za-z-]{3,}:|$)", text)
        server = " ".join(ms.group(1).split())[:32] if ms else ""
        bits = []
        if code:
            bits.append(t("rep.banner.http_answer", code=code))
        if server:
            bits.append(t("rep.banner.http_server", server=server))
        return "; ".join(bits) if bits else t("rep.banner.http_silent")
    if int(port) == 22 and text.upper().startswith("SSH"):
        name = text.split("-", 2)[-1] if text.count("-") >= 2 else text
        return t("rep.banner.ssh", name=name)
    if int(port) in (445, 139):
        return t("rep.banner.smb")
    return t("rep.banner.other", text=text[:60])


def hosts_summary(hosts: list[dict], lang: str | None = None) -> list[str]:
    """Сводка по службам: какие порты у скольких хостов и у кого именно."""
    t = _tx(lang)
    by_port: dict[int, list[str]] = {}
    for h in hosts:
        for p in h.get("open") or []:
            by_port.setdefault(int(p), []).append(str(h.get("ip")))
    rows = []
    for port in sorted(by_port):
        ips = by_port[port]
        where = " ".join(_addr(ip) for ip in sorted(ips, key=lambda s: [int(x) for x in s.split(".")]))
        rows.append("  " + t("rep.hosts_line", port=port_label(port, lang).ljust(38),
                             count=f"{len(ips):2}", where=where))
    return rows


def shares_lines(hosts: list[dict], lang: str | None = None) -> list[str]:
    """У кого видны общие папки — то самое место, где может лежать копия."""
    t = _tx(lang)
    rows = []
    for h in hosts:
        sh = h.get("shares") or []
        if sh:
            rows.append(f"  {h.get('ip')}: " + ", ".join(sh))
    return rows


def what_it_means(hosts: list[dict], lang: str | None = None) -> list[str]:
    """Что из увиденного следует практически. Без выдумок и без «вердиктов»."""
    t = _tx(lang)
    ports = sorted({int(p) for h in hosts for p in (h.get("open") or [])})
    notes = []
    for p in ports:
        if p not in (22, 80, 443, 139, 445, 3389, 8080, 21, 23):
            continue
        where = " ".join(_addr(h["ip"]) for h in hosts if p in (h.get("open") or []))
        notes.append(f"  {p}: {t(f'mean.{p}')}  ({where.strip()})")
    return notes


def title(rep: dict, name: str = "", lang: str | None = None) -> str:
    """Подпись отчёта для списка в панели.

    Первым словом — КОМАНДА (по-английски): оператор видит то же имя, что пишет
    в очереди миссий. Человеческое пояснение живёт рядом, а не вместо.
    """
    t = _tx(lang)
    kind = str(rep.get("kind") or ("net_audit" if rep.get("hosts") is not None else "")) \
        or (name or "report")
    tail = _short_when(rep.get("at"))
    if rep.get("hosts") is not None:
        alive = rep.get("alive_count")
        if alive is None:
            alive = len(rep.get("hosts") or [])
        return (f"{kind} {rep.get('cidr') or ''}".strip()
                + t("rep.title.alive", count=alive) + tail)
    if kind == "certs":
        rows = rep.get("targets") or []
        bad = rep.get("expiring") or []
        return "certs" + t("rep.title.certs", checked=len(rows), bad=len(bad)) + tail
    if kind == "write_probe":
        shares = rep.get("shares") or []
        writ = [s for s in shares if s.get("state") == "writable"]
        return "write_probe" + t("rep.title.wp", count=len(shares), writable=len(writ)) + tail
    if kind == "diff":
        return "diff" + t("rep.title.diff", new=len(rep.get("new_hosts") or []),
                          gone=len(rep.get("gone_hosts") or [])) + tail
    if kind == "remote":
        ways = [w for w in (rep.get("ways") or []) if w.get("open")]
        return ("remote " + str(rep.get("host") or "")
                + t("rep.title.remote", ways=len(ways), worked=str(rep.get("method") or ""))
                + tail)
    if rep.get("error"):
        return t("rep.error_line", kind=kind, error=str(rep["error"])[:50])
    return f"{kind or 'report'}"


def _short_when(iso) -> str:
    """' · 24.09 16:26' — короткая приписка ко времени, если оно разбирается."""
    try:
        return " · " + datetime.fromisoformat(str(iso)).strftime("%d.%m %H:%M")
    except (ValueError, TypeError):
        return ""


def _when(iso: str | None, lang: str | None = None) -> str:
    """'2026-09-24T16:26:52' -> '24.09.2026 16:26' (как это звучит в разговоре)."""
    if not iso:
        return i18n.t_in(lang or i18n.lang(), "dash")
    try:
        dt = datetime.fromisoformat(str(iso))
    except ValueError:
        return str(iso)
    return dt.strftime("%d.%m.%Y %H:%M")


def _addr(ip: str) -> str:
    """'192.168.1.88' -> '.88' — так короче внутри одной сети."""
    parts = str(ip).split(".")
    return "." + parts[-1] if len(parts) == 4 else str(ip)


def _head(kind_key: str, rep: dict, t) -> list[str]:
    out = [t(kind_key)]
    when = t("rep.when", when=_when(rep.get("at")))
    out.append(when + (t("rep.mission_id", id=rep["mission"]) if rep.get("mission") else ""))
    out.append(t("rep.rule"))
    if rep.get("error"):
        out.append(t("rep.failed", error=rep["error"]))
    return out


def summary_line(rep: dict, lang: str | None = None) -> str:
    """Одна строка для итога миссии и журнала."""
    kind = str(rep.get("kind") or "")
    if kind == "certs":
        return summary_certs(rep, lang)
    if kind == "write_probe":
        return summary_write_probe(rep, lang)
    if kind == "diff":
        return summary_diff(rep, lang)
    if kind == "remote":
        return summary_remote(rep, lang)
    if kind == "peers":
        return summary_peers(rep, lang)
    t = _tx(lang)
    if rep.get("error"):
        return t("rep.sum.audit_failed", error=rep["error"])
    if rep.get("stopped"):
        return t("rep.sum.audit_stopped", cidr=rep.get("cidr") or "",
                 done=len(rep.get("hosts") or []))
    hosts = rep.get("hosts") or []
    ports: dict[int, int] = {}
    for h in hosts:
        for p in h.get("open") or []:
            ports[int(p)] = ports.get(int(p), 0) + 1
    bits = [t("rep.sum.live", count=rep.get("alive_count", len(hosts)))]
    for p in sorted(ports, key=lambda x: -ports[x]):
        svc = t(f"svc.{p}").split(" ")[0] if p in KNOWN_PORTS else str(p)
        bits.append(f"{svc}: {ports[p]}")
    sh = shares_lines(hosts, lang)
    if sh:
        bits.append(t("rep.sum.shares_at",
                      hosts=", ".join(s.split(":")[0].strip() for s in sh)))
    return t("rep.sum.audit", cidr=rep.get("cidr"), bits="; ".join(bits))


def render_net_audit(rep: dict, lang: str | None = None) -> str:
    """Полный человеческий отчёт об аудите сети."""
    t = _tx(lang)
    hosts = rep.get("hosts") or []
    out: list[str] = []
    out.append(t("rep.audit.head", cidr=rep.get("cidr") or t("dash")))
    out.append(t("rep.when", when=_when(rep.get("at")))
               + (t("rep.mission_id", id=rep["mission"]) if rep.get("mission") else ""))
    out.append(t("rep.rule"))
    if rep.get("error"):
        out.append(t("rep.failed", error=rep["error"]))
        return "\n".join(out)
    alive = rep.get("alive_count", len(hosts))
    out.append(t("rep.audit.alive_own", count=alive) if hosts
               else t("rep.audit.alive", count=alive))
    out.append("")
    if not hosts:
        out.append(t("rep.audit.nobody"))
        return "\n".join(out)

    out.append(t("rep.section.found"))
    out.extend(hosts_summary(hosts, lang) or [t("rep.audit.nothing_open")])
    sh = shares_lines(hosts, lang)
    if sh:
        out.append("")
        out.append(t("rep.section.shares"))
        out.extend(sh)
    out.append("")
    out.append(t("rep.section.hosts"))
    for h in sorted(hosts, key=lambda x: [int(v) for v in str(x.get("ip", "0")).split(".") if v.isdigit()] or [0]):
        opened = h.get("open") or []
        out.append("")
        out.append(t("rep.host_open", ip=h.get("ip"),
                     ports=", ".join(port_label(int(p), lang) for p in opened) if opened
                     else t("rep.nothing")))
        for p in opened:
            line = banner(int(p), (h.get("banners") or {}).get(str(p), ""), lang)
            if line:
                out.append(f"      {int(p)}: {line}")
        if h.get("shares"):
            out.append(t("rep.shares_line", shares=", ".join(h["shares"])))
    out.append("")
    out.append(t("rep.rule"))
    out.append(t("rep.section.meaning"))
    out.extend(what_it_means(hosts, lang) or [t("rep.nothing_noteworthy")])
    out.append("")
    out.append(t("rep.no_write_probe"))
    return "\n".join(out)


def render_any(obj, lang: str | None = None) -> str:
    """Запасной путь: незнакомый отчёт показываем списком «поле: значение»."""
    if not isinstance(obj, dict):
        return json.dumps(obj, ensure_ascii=False, indent=2)
    out = []
    for k, v in obj.items():
        if isinstance(v, (list, dict)):
            out.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        else:
            out.append(f"{k}: {v}")
    return "\n".join(out)


def render(rep, lang: str | None = None) -> str:
    """Любой отчёт -> человеческий текст (по умолчанию — на текущем языке)."""
    t = _tx(lang)
    if not isinstance(rep, dict):
        return render_any(rep, lang)
    kind = str(rep.get("kind") or "")
    if kind == "certs":
        return render_certs(rep, lang)
    if kind == "write_probe":
        return render_write_probe(rep, lang)
    if kind == "diff":
        return render_diff(rep, lang)
    if kind == "remote":
        return render_remote(rep, lang)
    if kind == "peers":
        return render_peers(rep, lang)
    if "hosts" in rep:
        return render_net_audit(rep, lang)
    if rep.get("kind") and rep.get("hosts") is None and rep.get("error"):
        label = t(f"tool.{kind}.label") if i18n.has(f"tool.{kind}.label") else t("tool.note.label")
        return t("rep.error_line", kind=label, error=rep["error"])
    return render_any(rep, lang)


def render_certs(rep: dict, lang: str | None = None) -> str:
    """Сроки сертификатов: ближние первыми (истекает молча — вот что важно)."""
    t = _tx(lang)
    out = _head("rep.certs.head", rep, t)
    if rep.get("error"):
        return "\n".join(out)
    rows = rep.get("targets") or []
    if not rows:
        out.append(t("rep.certs.nothing"))
        return "\n".join(out)
    bad = rep.get("expiring") or []
    out.append(t("rep.certs.summary", checked=len(rows), days=WARN_DAYS, bad=len(bad)))
    out.append("")
    out.append(t("rep.certs.by_date"))
    for row in sorted(rows, key=lambda x: (x.get("days_left") is None, x.get("days_left") or 0)):
        where = f"{row.get('host')}:{row.get('port')}"
        if not row.get("ok"):
            out.append(t("rep.certs.noreply", where=where, error=row.get("error")))
            continue
        days = row.get("days_left")
        flag = t("rep.certs.flag") if (days is not None and days <= WARN_DAYS) else ""
        out.append(t("rep.certs.line", where=where, flag=flag, days=days,
                     until=row.get("not_after") or t("dash")))
        out.append(t("rep.certs.subject", value=row.get("subject") or t("dash")))
        out.append(t("rep.certs.issuer", value=row.get("issuer") or t("dash")))
        if row.get("names"):
            out.append(t("rep.certs.names", value=", ".join(row["names"][:6])))
    out.append("")
    out.append(t("rep.rule"))
    out.append(t("rep.section.meaning"))
    out.extend(t("rep.certs.meaning").split("\n"))
    return "\n".join(out)


def render_write_probe(rep: dict, lang: str | None = None) -> str:
    """Тест записи: не «дыра», а факт — пускает писать, пускает только читать, не пускает."""
    t = _tx(lang)
    out = _head("rep.wp.head", rep, t)
    if rep.get("error"):
        return "\n".join(out)
    shares = rep.get("shares") or []
    if not shares:
        out.append(t("rep.wp.nothing"))
        return "\n".join(out)
    writ = [s for s in shares if s.get("state") == "writable"]
    out.append(t("rep.wp.summary", checked=len(shares), writable=len(writ)))
    out.append("")
    out.append(t("rep.wp.by_folder"))
    for s in shares:
        state = str(s.get("state") or "")
        mark = t(f"rep.wp.state.{state}") if i18n.has(f"rep.wp.state.{state}") else state
        out.append(t("rep.wp.line", path=s.get("path"), mark=mark))
        if s.get("detail"):
            out.append(t("rep.wp.why", detail=s["detail"]))
    out.append("")
    out.append(t("rep.rule"))
    out.append(t("rep.section.meaning"))
    out.extend(t("rep.wp.meaning").split("\n"))
    return "\n".join(out)


def render_diff(rep: dict, lang: str | None = None) -> str:
    """Что изменилось между двумя аудитами — то, ради чего существо и живёт рядом."""
    t = _tx(lang)
    out = _head("rep.diff.head", rep, t)
    if rep.get("error"):
        return "\n".join(out)
    out.append(t("rep.diff.was", cidr=rep.get("older_cidr") or t("dash"),
                 when=_when(rep.get("older_at")), alive=rep.get("older_alive", "?")))
    out.append(t("rep.diff.now", cidr=rep.get("newer_cidr") or t("dash"),
                 when=_when(rep.get("newer_at")), alive=rep.get("newer_alive", "?")))
    out.append("")
    new_hosts = rep.get("new_hosts") or []
    gone = rep.get("gone_hosts") or []
    changed = rep.get("changed") or []
    if not (new_hosts or gone or changed):
        out.append(t("rep.diff.none"))
        return "\n".join(out)
    if new_hosts:
        out.append(t("rep.diff.appeared"))
        for h in new_hosts:
            out.append(t("rep.diff.open", ip=h.get("ip"),
                         ports=", ".join(str(p) for p in h.get("open") or []) or t("rep.nothing")))
    if gone:
        out.append(t("rep.diff.gone"))
        for h in gone:
            out.append(t("rep.diff.was_open", ip=h.get("ip"),
                         ports=", ".join(str(p) for p in h.get("open") or []) or t("rep.nothing")))
    if changed:
        out.append(t("rep.diff.changed"))
        for h in changed:
            bits = []
            if h.get("added_ports"):
                bits.append(t("rep.diff.opened", ports=", ".join(str(p) for p in h["added_ports"])))
            if h.get("removed_ports"):
                bits.append(t("rep.diff.closed", ports=", ".join(str(p) for p in h["removed_ports"])))
            if h.get("shares_added"):
                bits.append(t("rep.diff.shares_new", shares=", ".join(h["shares_added"])))
            if h.get("shares_removed"):
                bits.append(t("rep.diff.shares_gone", shares=", ".join(h["shares_removed"])))
            out.append(f"  {h.get('ip')}  " + "; ".join(bits))
    out.append("")
    out.append(t("rep.rule"))
    out.append(t("rep.section.meaning"))
    out.extend(t("rep.diff.meaning").split("\n"))
    return "\n".join(out)


def summary_certs(rep: dict, lang: str | None = None) -> str:
    t = _tx(lang)
    if rep.get("error"):
        return t("rep.sum.certs_failed", error=rep["error"])
    rows = rep.get("targets") or []
    bad = [r for r in rows if r.get("days_left") is not None and r["days_left"] <= WARN_DAYS]
    ok = [r for r in rows if r.get("ok")]
    if not rows:
        return t("rep.sum.certs_none")
    tail = t("rep.sum.certs", ok=len(ok), total=len(rows))
    if bad:
        tail += t("rep.sum.certs_soon", list=", ".join(
            t("rep.sum.certs_days", host=r["host"], days=r["days_left"])
            for r in sorted(bad, key=lambda x: x["days_left"])[:4]))
    else:
        tail += t("rep.sum.certs_far")
    return tail


def summary_write_probe(rep: dict, lang: str | None = None) -> str:
    t = _tx(lang)
    if rep.get("error"):
        return t("rep.sum.wp_failed", error=rep["error"])
    shares = rep.get("shares") or []
    writ = [s for s in shares if s.get("state") == "writable"]
    if not shares:
        return t("rep.sum.wp_none")
    if not writ:
        return t("rep.sum.wp_nowhere", count=len(shares))
    return t("rep.sum.wp_ok", count=len(shares),
             list=", ".join(str(s.get("path")) for s in writ[:3]))


def summary_diff(rep: dict, lang: str | None = None) -> str:
    t = _tx(lang)
    if rep.get("error"):
        return t("rep.sum.diff_failed", error=rep["error"])
    new_hosts = rep.get("new_hosts") or []
    gone = rep.get("gone_hosts") or []
    changed = rep.get("changed") or []
    if not (new_hosts or gone or changed):
        return t("rep.sum.diff_same", cidr=rep.get("newer_cidr") or "")
    return (t("rep.sum.diff", new=len(new_hosts), gone=len(gone), changed=len(changed))
            + (t("rep.sum.diff_new", list=", ".join(str(h.get("ip")) for h in new_hosts[:4]))
               if new_hosts else ""))


def render_remote(rep: dict, lang: str | None = None) -> str:
    """Отчёт удалённого управления: какие пути открыты и что сработало.

    Состояние метода печатаем ИДЕНТИФИКАТОРОМ (ok / denied / unreachable /
    skipped) — как статусы миссий: это машинное слово, а не подпись, и оно
    одинаково и в файле, и на экране.
    """
    t = _tx(lang)
    out = _head("tool.remote.label", rep, t)
    out.append(t("rep.remote.target", host=rep.get("host") or "",
                 command=rep.get("command") or t("rep.remote.no_command")))
    ways = rep.get("ways") or []
    if ways:
        out.append(t("rep.remote.ways"))
        for w in ways:
            out.append(t("rep.remote.row", port=w.get("port"), method=w.get("method"),
                         state=t("rep.remote.open" if w.get("open") else "rep.remote.closed")))
    runs = rep.get("attempts") or []
    if runs:
        out.append(t("rep.remote.attempts"))
        for a in runs:
            out.append(t("rep.remote.attempt_row", method=a.get("method"),
                         state=a.get("state") or "skipped",
                         output=(a.get("output") or a.get("detail") or "")[:160]))
    if rep.get("hint"):
        out.append(t("rep.remote.next", hint=rep["hint"]))
    return "\n".join(out)


def _svc_tail(port, lang: str | None = None) -> str:
    """' — HTTPS — веб с шифром' для знакомого порта; пусто, если порт не опознан.

    Порт без имени НЕ подписываем словами «служба не опознана» в каждой строке:
    это шум. Незнакомое называется там, где оно важно, — в разделе «стоит внимания».
    """
    t = _tx(lang)
    try:
        p = int(port)
    except (TypeError, ValueError):
        return ""
    return " — " + t(f"svc.{p}") if p in KNOWN_PORTS else ""


def _lead_why(code: str, lang: str | None = None) -> str:
    """Почему эта цель появилась — словами. Ключи тут ЛИТЕРАЛАМИ (см. synapse/work.py):

    собранный ключ не видит тест «ключей-сирот», и живые фразы выглядели бы мёртвыми.
    Незнакомый код возвращаем как есть: выдумывать перевод хуже, чем показать код.
    """
    t = _tx(lang)
    if code == "listen_unusual":
        return t("lead.why.listen_unusual")
    if code == "in_unusual":
        return t("lead.why.in_unusual")
    if code == "both_sides":
        return t("lead.why.both_sides")
    if code == "frequent":
        return t("lead.why.frequent")
    return code


def _where(g: dict) -> str:
    """'142.251.98.100:443' или просто адрес, если порт не назван (IPsec, ICMP)."""
    host = str(g.get("host") or "")
    port = g.get("port")
    return f"{host}:{port}" if port else host


def _group_connections(rows: list[dict], direction: str) -> list[dict]:
    """Свернуть соединения в «сторону» — по адресу и СЛУЖБЕ, а не по каждой строке.

    Зачем: 40 соединений к одному сайту — это одно отношение, а не сорок новостей.
    Счётчик показывает, кто у нас частый партнёр, и не заваливает отчёт.
    Направление, наш адрес и порт службы уже посчитаны в synapse/tools/peers.py —
    здесь только сборка.
    """
    groups: dict[tuple, dict] = {}
    for r in rows:
        if r.get("direction") != direction:
            continue
        key = (r.get("target"), r.get("other"), r.get("service_port"),
               r.get("proto") if direction == "listen" else "")
        rec = groups.setdefault(key, {"target": r.get("target"), "host": r.get("other"),
                                      "local": r.get("local"),
                                      "from": r.get("our"), "port": r.get("service_port"),
                                      "count": 0, "states": set(), "ours": set()})
        rec["count"] += 1
        if r.get("state"):
            rec["states"].add(str(r["state"]).lower())
        if r.get("our"):
            rec["ours"].add(str(r["our"]))
    return sorted(groups.values(), key=lambda g: -g["count"])


def render_peers(rep: dict, lang: str | None = None) -> str:
    """Полный человеческий отчёт по соединениям: что это, откуда и что насторожило."""
    t = _tx(lang)
    out = _head("rep.peers.head", rep, t)
    if rep.get("error"):
        return "\n".join(out)
    out.append(t("rep.peers.rule"))
    out.append("")
    out.append(t("rep.peers.nodes"))
    for row in rep.get("targets") or []:
        if row.get("state") == "ok":
            out.append(t("rep.peers.node_ok", name=row.get("name"), host=row.get("host"),
                         n=row.get("connections"), skipped=row.get("skipped") or 0,
                         who=row.get("who") or ""))
        else:
            out.append(t("rep.peers.node_bad", name=row.get("name"), host=row.get("host"),
                         state=row.get("state"), error=(row.get("error") or "")[:160]))
    if rep.get("mine_cidrs"):
        cidrs = list(rep["mine_cidrs"])
        shown = ", ".join(cidrs[:8])
        if len(cidrs) > 8:                      # список сетей длинный — не заваливаем
            shown += " " + t("rep.peers.more", n=len(cidrs) - 8)
        out.append(t("rep.peers.mine", cidrs=shown))
    rows = rep.get("rows") or []

    groups = _group_connections(rows, "out")
    out.append("")
    out.append(t("rep.section.out"))
    for g in groups[:RENDER_ROWS]:
        out.append(t("rep.peers.out_row", target=g.get("target") or "",
                     where=_where(g), port=g["port"],
                     svc=_svc_tail(g["port"], lang), n=g["count"],
                     state=", ".join(sorted(g["states"])) or t("dash"),
                     clients=len(g["ours"])))
    if not groups:
        out.append(t("rep.peers.nothing"))
    elif len(groups) > RENDER_ROWS:
        out.append(t("rep.peers.more", n=len(groups) - RENDER_ROWS))

    ins = _group_connections(rows, "in")
    out.append("")
    out.append(t("rep.section.in"))
    for g in ins[:RENDER_ROWS]:
        out.append(t("rep.peers.in_row", target=g.get("target") or "",
                     where=_where(g), port=g["port"],
                     svc=_svc_tail(g["port"], lang), n=g["count"],
                     state=", ".join(sorted(g["states"])) or t("dash")))
    if not ins:
        out.append(t("rep.peers.nothing"))

    inner = _group_connections(rows, "inner")
    if inner:
        out.append("")
        out.append(t("rep.section.inner"))
        for g in inner[:RENDER_ROWS]:
            out.append(t("rep.peers.inner_row", target=g.get("target") or "",
                         frm=g["from"], where=_where(g), port=g["port"],
                         svc=_svc_tail(g["port"], lang), n=g["count"],
                         state=", ".join(sorted(g["states"])) or t("dash")))

    listeners = [r for r in rows if r.get("direction") == "listen"]
    out.append("")
    out.append(t("rep.section.listen"))
    shown = [r for r in listeners if r.get("known") or r.get("verdict") == "listen_unusual"]
    for r in shown[:RENDER_ROWS]:
        if r.get("known"):
            out.append(t("rep.peers.listen_known", target=r.get("target") or "",
                         proto=r.get("proto"), local=r.get("local"),
                         svc=r.get("service")))
        else:
            out.append(t("rep.peers.listen_unknown", target=r.get("target") or "",
                         proto=r.get("proto"), local=r.get("local"),
                         port=r.get("server_port")))
    if not shown:
        out.append(t("rep.peers.nothing"))
    quiet = len(listeners) - len(shown)
    if quiet > 0:
        out.append(t("rep.peers.listen_quiet", n=quiet))

    watch = rep.get("watch") or []
    if watch:
        out.append("")
        out.append(t("rep.section.watch"))
        for a in watch:
            what = str(a.get("what"))
            if what == "both_sides":
                out.append(t("rep.peers.watch_both", peer=a.get("peer"),
                             targets=", ".join(a.get("targets") or [])))
            elif what == "frequent":
                out.append(t("rep.peers.watch_freq", peer=a.get("peer"), n=a.get("times")))
            else:
                out.append(t("rep.peers.watch_port", target=a.get("target") or "",
                             port=a.get("port"), svc=_svc_tail(a.get("port"), lang), n=0))

    mine = [i for i in (rep.get("leads") or []) if i.get("mine")]
    alien = [i for i in (rep.get("leads") or []) if not i.get("mine")]
    out.append("")
    out.append(t("rep.section.leads"))
    seen: set = set()
    for item in mine + alien:
        key = (item.get("target"), item.get("what"), item.get("port"))
        if key in seen:
            continue
        seen.add(key)
        why = str(item.get("why") or "")
        # Причина цели — КОД (`listen_unusual`, `frequent`, …): в файле целей лежит
        # она, а слова берутся словарём в момент показа (иначе смена языка оставляла
        # бы старые цели на прежнем языке — тот же принцип, что у отчётов).
        why_text = _lead_why(why, lang)
        out.append(t("rep.peers.lead_row", target=item.get("target"),
                     port=f":{item['port']}" if item.get("port") else "",
                     what=item.get("what"), why=why_text,
                     mark="" if item.get("mine") else t("rep.peers.lead_alien")))
    if not mine and not alien:
        out.append(t("rep.peers.nothing"))
    out.append("")
    out.append(t("rep.rule"))
    return "\n".join(out)


def summary_peers(rep: dict, lang: str | None = None) -> str:
    """Одна строка для итога миссии peers и журнала."""
    t = _tx(lang)
    if rep.get("error"):
        return t("rep.sum.peers_failed", error=rep["error"])
    s = rep.get("summary") or {}
    bits = [t("rep.sum.peers_nodes", ok=s.get("ok", 0), all=len(rep.get("targets") or []))]
    bits.append(t("rep.sum.peers_conn", n=s.get("connections", 0),
                  listeners=s.get("listeners", 0)))
    if s.get("unusual"):
        bits.append(t("rep.sum.peers_unusual", n=s.get("unusual")))
    leads = rep.get("leads") or []
    if leads:
        bits.append(t("rep.sum.peers_leads", n=len(leads)))
    return t("rep.sum.peers", bits="; ".join(bits))


def summary_remote(rep: dict, lang: str | None = None) -> str:
    """Одна строка для итога миссии remote."""
    t = _tx(lang)
    host = rep.get("host") or ""
    if rep.get("error"):
        return t("rep.sum.remote_failed", error=rep["error"])
    if rep.get("state") == "stopped":
        return t("rep.sum.remote_stopped", host=host)
    ways = [w["method"] for w in (rep.get("ways") or []) if w.get("open")
            and w.get("method") in ("winrm", "winrm_tls", "wmi_dcom", "ssh", "smb")]
    if rep.get("state") == "ok":
        return t("rep.sum.remote_ok", host=host, method=rep.get("method") or "",
                 out=(rep.get("output") or "")[:120])
    if rep.get("attempts"):
        return t("rep.sum.remote_denied", host=host, method=rep.get("method") or "")
    if ways:
        return t("rep.sum.remote_probe", host=host, ways=", ".join(ways))
    return t("rep.sum.remote_none", host=host)


def text_path(json_path: str | Path) -> Path:
    p = Path(json_path)
    return p.with_suffix(".txt")


def save_text(rep: dict, json_path: str | Path, lang: str | None = None) -> Path:
    """Положить человеческую версию рядом с JSON (её и открывают блокнотом).

    Язык снимка — текущий язык интерфейса: так отчёт читается тем, кто его
    тогда смотрел. Настоящий источник всё равно JSON, поэтому панель при смене
    языка рисует отчёт заново (см. read_text).
    """
    p = text_path(json_path)
    tmp = p.with_suffix(".txt.tmp")
    tmp.write_text(render(rep, lang), encoding="utf-8")
    tmp.replace(p)
    return p


def find_for_mission(mid: str) -> dict:
    """Отчёт миссии по её id: id стоит в ИМЕНИ файла `<штамп>-<id>.json`.

    Зачем: строка итога миссии хранится в очереди на языке ТОГО дня. Если отчёт
    на диске есть, итог собирается заново на текущем языке (JSON — истина,
    строка — снимок) — иначе в таблице оставался бы русский текст при английском
    интерфейсе. Нет отчёта — вернём пустой словарь, и панель покажет что записано.
    """
    if not mid:
        return {}
    try:
        found = sorted(REPORTS_DIR.glob(f"*{mid}*.json"), reverse=True)
    except OSError:
        return {}
    for p in found:
        rep = load(p)
        if rep:
            rep.setdefault("path", str(p))
            return rep
    return {}


#: Проза, записанная в СТАРЫЕ отчёты жёстким текстом (до v0.10.1: тогда фразы инструментов
#: лежали в коде, а не в словаре). Отчёт остаётся снимком своего дня, но ПОКАЗ идёт за языком
#: интерфейса — поэтому известные старые фразы переводим при чтении. Незнакомая кириллица
#: проходит как есть: придумывать перевод мы не имеем права.
LEGACY_PHRASES = {
    "ни одна служба не ответила рукопожатием": "err.certs.no_tls",
    "служба не отдала сертификат": "err.certs.no_cert",
    "дверь не пустила": "err.door",
    "нет аудита сети — сначала net_audit": "err.no_audit",
    "не нашла свою подсеть": "err.no_subnet",
    "нет двух аудитов для сравнения — сначала net_audit": "err.diff.need_two",
    "в последнем аудите общих папок не видно": "err.wp.no_shares",
    "тест записи выключен в настройках": "wp.off",
    "служебная папка — не трогаем": "wp.reserved",
    "запись прошла, но прочиталось иначе": "wp.unverified",
}
#: Фразы с хвостом: «сертификат не разобрать: PermissionError», «из последнего аудита X».
LEGACY_PREFIXES = (
    ("сертификат не разобрать: ", "err.certs.decode", "reason"),
    ("из последнего аудита ", "src.from_audit", "cidr"),
    ("из миссии: ", "src.from_mission_host", "host"),
    (" | маркер не убрать: ", "wp.not_removed", "error"),
)
_CYRILLIC = re.compile("[А-Яа-яЁё]")
#: Поля отчёта, где лежит проза (её и переводим при чтении).
PROSE_FIELDS = ("error", "source", "detail")


def translate_legacy(text) -> str:
    """Перевести фразу из старого отчёта. Незнакомую кириллицу отдаём как есть."""
    text = str(text or "")
    if not text or not _CYRILLIC.search(text):
        return text
    key = LEGACY_PHRASES.get(text)
    if key:
        return i18n.t(key)
    for prefix, key, field in LEGACY_PREFIXES:
        if text.startswith(prefix):
            return i18n.t(key, **{field: text[len(prefix):]})
    return text


def soft_legacy(rep: dict) -> dict:
    """Перевести старую прозу в отчёте ПРИ ЧТЕНИИ: файл не переписываем, только показ."""
    if not isinstance(rep, dict):
        return rep
    for field in PROSE_FIELDS:
        if rep.get(field):
            rep[field] = translate_legacy(rep[field])
    for key in ("targets", "shares", "hosts"):
        for row in rep.get(key) or []:
            if isinstance(row, dict):
                for field in PROSE_FIELDS:
                    if row.get(field):
                        row[field] = translate_legacy(row[field])
    return rep


def load(path: str | Path) -> dict:
    """Прочитать отчёт с диска: JSON, а если его нет — пустой словарь."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    # Отчёт с диска показываем на ТЕКУЩЕМ языке: старая проза переводится здесь,
    # а сам файл не переписывается — он остаётся записью того дня.
    return soft_legacy(data if isinstance(data, dict) else {"value": data})


def read_text(path: str | Path, limit: int = 20000, lang: str | None = None) -> str:
    """Человеческий текст отчёта НА НУЖНОМ ЯЗЫКЕ.

    Рисуем из JSON каждый раз: иначе смена языка оставляла бы старые отчёты
    на прежнем языке. Готовый .txt берём только когда JSON уже нет — он был
    снимком на тот день и на тот язык, и это лучше, чем ничего.
    """
    p = Path(path)
    rep = load(p)
    if rep:
        return render(rep, lang)[:limit]
    txt = text_path(p)
    if txt.exists():
        try:
            return txt.read_text(encoding="utf-8")[:limit]
        except OSError:
            pass
    return i18n.t_in(lang or i18n.lang(), "rep.read_failed", name=p.name)


# --- совместимость с прежними вызовами (v0.4–v0.9) -------------------------
#: Было словарём порт -> русская служба. Оставлено, чтобы старые вызовы не падали.
SERVICES: dict[int, str] = {p: i18n.t_in("ru", f"svc.{p}") for p in KNOWN_PORTS}
#: Было словарём команда -> русское пояснение.
KIND_RU: dict[str, str] = {k: i18n.t_in("ru", f"tool.{k}.label") for k in
                           ("net_audit", "write_probe", "certs", "diff", "stimulus", "note", "ask")}


def port_ru(port: int) -> str:
    """Прежнее имя port_label(): русская подпись порта (v0.4–v0.9)."""
    return port_label(port, "ru")


def banner_ru(port: int, text: str) -> str:
    """Прежнее имя banner(): русский разбор баннера (v0.4–v0.9)."""
    return banner(port, text, "ru")
