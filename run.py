"""Панель управления Synapse: графическое окно (по умолчанию) и командная строка.

    python run.py                           открыть ГРАФИЧЕСКУЮ панель (окно)
    python run.py panel                     то же самое явно
    python run.py init                      первая сборка: конфиг + тело + карта ролей
    python run.py status                    что организм чувствует сейчас
    python run.py roles                     точная карта: сколько кого, кто хабы
    python run.py tick [--n 3]              прожить N тактов (без ожидания)
    python run.py run [--seconds 60]        жить автономно (Ctrl+C — стоп)
    python run.py mission add --kind net_audit [--cidr 192.168.1.0/24]
    python run.py mission add --kind write_probe [--host 192.168.1.88]   тест записи
    python run.py mission add --kind certs [--host 192.168.1.88]         сроки сертификатов
    python run.py mission add --kind diff                                что изменилось
    python run.py mission list
    python run.py scan [--cidr ...]         разовый аудит сети мимо очереди
    python run.py reports [--show 1]        отчёты человеческим языком (и --rebuild)
    python run.py brain                     состояние мозга (свой сервер внутри проекта)
    python run.py brain --action provision  скопировать рантайм и модель к себе (один раз)
    python run.py brain --action on|off     включить/выключить мозг (поднимет свой сервер)
    python run.py brain --action cloud      живая проба облака: короткий запрос по факту
    python run.py brain --action tidy       убрать лишний сервер, вставший на тот же порт

Существо автономно: рантайм и модель лежат в models/brain рядом с телом,
свой llama-server на своём порту, к AI_UI не обращается.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from synapse import (audit, config, i18n, missions, online, reports,   # noqa: E402
                     roles as roles_mod)
from synapse.agent import Synapse                                 # noqa: E402
from synapse.body import Body                                     # noqa: E402
from synapse.brain import Brain                                   # noqa: E402
from synapse.panel_core import PanelCore                          # noqa: E402
from synapse.tools import net_scan                                # noqa: E402

DEFAULT_BODY_SRC = Path("D:/Documents/Projects/ai_ui/data/bodies/worm.json")
DEFAULT_CACHE = Path("D:/Documents/Projects/ConnectomeLab/data/owmeta_cache.json")


def _build_roles() -> dict:
    """Карта ролей: без неё тело знает только эвристику, а мозг — не знает, куда стучать."""
    out = config.ROOT / (config.load()["body"].get("roles_file") or "data/body/neuron_roles.json")
    if out.exists():
        return {"roles": str(out), "built": False, "summary": roles_mod.summary(roles_mod.load(out))}
    from tools import build_roles
    rc = build_roles.main(["--cache", str(DEFAULT_CACHE), "--out", str(out)])
    return {"roles": str(out), "built": rc == 0, "summary": roles_mod.summary(roles_mod.load(out))}


def cmd_init(_args) -> int:
    p = config.ensure_files()
    dst = config.DATA / "body" / "worm.json"
    if not dst.exists() and DEFAULT_BODY_SRC.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DEFAULT_BODY_SRC, dst)          # своё тело — в своём доме
    for sub in ("logs", "missions", "reports"):
        (config.DATA / sub).mkdir(parents=True, exist_ok=True)
    rmap = _build_roles()
    body = Body.load()
    audit.log("init", config=str(p), body=str(body.path), nodes=body.size, edges=len(body.edges))
    print(json.dumps({"config": str(p), "body": str(body.path), "nodes": body.size,
                      "edges": len(body.edges), "roles": body.roles_summary(),
                      "zones": body.zone_summary(), "map": rmap},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_status(_args) -> int:
    print(json.dumps(PanelCore().status(), ensure_ascii=False, indent=2))
    return 0


def cmd_roles(args) -> int:
    if args.build:
        print(json.dumps(_build_roles(), ensure_ascii=False, indent=2))
        return 0
    path = config.ROOT / (config.load()["body"].get("roles_file") or "data/body/neuron_roles.json")
    data = roles_mod.load(path)
    if not data:
        print(i18n.t("cli.no_roles_map"))
        return 2
    body = Body.load()
    print(json.dumps({"summary": roles_mod.summary(data), "zones": body.zone_summary(),
                      "hubs": body.hubs(12), "file": str(path)},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_tick(args) -> int:
    out = Synapse().run_forever(max_ticks=args.n, interval=0)
    return 0 if out else 1


def cmd_run(args) -> int:
    interval = None if args.seconds is None else float(args.seconds)
    Synapse().run_forever(interval=interval)
    return 0


def cmd_panel(args) -> int:
    """Панель управления. Основная — на видеокарте (Qt+OpenGL), запасная — tkinter.

    Запасной путь нужен честно: если Qt/PySide6 в этом интерпретаторе нет, окно
    всё равно должно открыться, пусть и медленнее.
    """
    extra = ["--selftest", "--seconds", str(args.seconds)] if args.selftest else []
    if not args.selftest:                                 # в selftest вывод нужен ЗДЕСЬ
        from synapse.launcher import ensure_right_python
        if ensure_right_python():
            return 0                                      # окно откроется питоном с Qt
    try:
        from synapse.qt_panel import main as qt_main      # Qt тянем только здесь
    except Exception as e:                                # noqa: BLE001
        audit.log("panel.fallback", why=f"{type(e).__name__}: {e}")
        from synapse.panel import main as panel_main
        return panel_main(extra)
    return qt_main(extra)


def cmd_mission(args) -> int:
    if args.action == "list":
        print(json.dumps(missions.all_missions(), ensure_ascii=False, indent=2))
        return 0
    params = {}
    if args.cidr:
        params["cidr"] = args.cidr
    if args.text:
        params["text"] = args.text
    if getattr(args, "host", None):
        params["host"] = args.host
    if getattr(args, "ports", None):
        params["ports"] = [int(x) for x in str(args.ports).replace(",", " ").split() if x.strip()]
    for key in ("command", "user", "password"):          # миссия remote
        if getattr(args, key, None):
            params[key] = getattr(args, key)
    try:
        rec = missions.add(args.kind, params, note=args.note or "")
    except ValueError:            # незнакомое имя — в очередь не кладём (missions.KINDS)
        print(i18n.t("cli.no_tool", kind=args.kind, kinds=", ".join(missions.KINDS)))
        return 2
    audit.log("mission.added", mission=rec["id"], kind=args.kind, params=params)
    print(json.dumps(rec, ensure_ascii=False, indent=2))
    return 0


def cmd_scan(args) -> int:
    rep = net_scan.audit_network(args.cidr, mission="manual")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if not rep.get("error") else 2


def cmd_reports(args) -> int:
    """Отчёты — человеческим языком: список, пересборка .txt, показ последнего в консоли."""
    folder = config.DATA / "reports"
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        print(i18n.t("cli.no_reports"))
        return 1
    if args.rebuild:
        # .txt пересобираем на ТЕКУЩЕМ языке интерфейса: это снимок для глаз.
        for p in files:
            reports.save_text(reports.load(p), p)
        print(i18n.t("cli.rebuilt", count=len(files)))
    for n, p in enumerate(files[:args.limit], start=1):
        rep = reports.load(p)
        mark = "  " if reports.text_path(p).exists() else "! "
        print(f"{mark}{n:2}. {reports.title(rep, p.stem)}")
    if args.show:
        idx = max(1, int(args.show)) - 1
        p = files[min(idx, len(files) - 1)]
        print("\n" + reports.read_text(p))
    return 0


def cmd_brain(args) -> int:
    b = Brain()
    action = args.action or "status"
    if action == "provision":
        def note(name, total):
            print(i18n.t("cli.copied", name=name, mb=round(total / 1048576, 1)), flush=True)
        rep = b.provision(on_progress=note)
        print(json.dumps({k: (v if k != "copied" else v[:5]) for k, v in rep.items()},
                         ensure_ascii=False, indent=2))
        return 0 if rep.get("self_contained") else 2
    if action == "on":
        core = PanelCore()
        res = core.brain_on(on_note=lambda s: print(f"  {s}", flush=True))
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 2
    if action == "off":
        print(json.dumps(PanelCore().brain_off(), ensure_ascii=False, indent=2))
        return 0
    if action == "cloud":
        # ЖИВАЯ ПРОБА ОБЛАКА (v0.30.0, M7): короткий настоящий запрос. «Готов по
        # настройкам» ещё не значит «отвечает» — ключ могут отозвать, модель снять
        # с обслуживания, потолок выбраться. Здесь это видно сразу и без окна.
        res = online.probe(b.cfg)
        out = {k: res.get(k) for k in ("ok", "text", "error", "code", "detail",
                                       "provider", "model", "ms", "tokens_in",
                                       "tokens_out", "spent")}
        out["budget"] = online.budget()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 2
    if action == "tidy":
        killed = b.stop_extra_listeners()
        print(json.dumps({i18n.t("cli.extra_servers", killed=killed):
                              i18n.t("cli.listening", ports=b.listeners())},
                         ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(b.state(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    # ОБЩИЙ ЛОГ ПРИЛОЖЕНИЯ (v0.23.0): старт + любые необработанные исключения.
    # Раньше окно падало молча — оператор не имел куда посмотреть.
    try:
        from synapse import applog
        applog.install("run.py")
    except Exception:                                  # noqa: BLE001
        pass
    # Язык интерфейса берём из настроек ДО сборки подсказок: тогда и help
    # выходит на выбранном языке, а не на том, что случайно зашит в код.
    i18n.sync_from_config(config.load())
    ap = argparse.ArgumentParser(prog="synapse", description=i18n.t("cli.desc"))
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("init", help=i18n.t("cli.help.init")).set_defaults(fn=cmd_init)
    sub.add_parser("status", help=i18n.t("cli.help.status")).set_defaults(fn=cmd_status)

    p = sub.add_parser("roles", help=i18n.t("cli.help.roles"))
    p.add_argument("--build", action="store_true", help=i18n.t("cli.help.roles_build"))
    p.set_defaults(fn=cmd_roles)

    p = sub.add_parser("panel", help=i18n.t("cli.help.panel"))
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--seconds", type=float, default=3.0, help=i18n.t("cli.help.seconds"))
    p.set_defaults(fn=cmd_panel)

    p = sub.add_parser("tick", help=i18n.t("cli.help.tick"))
    p.add_argument("--n", type=int, default=1)
    p.set_defaults(fn=cmd_tick)

    p = sub.add_parser("run", help=i18n.t("cli.help.run"))
    p.add_argument("--seconds", type=float, default=None)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("mission", help=i18n.t("cli.help.mission"))
    p.add_argument("action", choices=("add", "list"))
    p.add_argument("--kind", default="net_audit", help=i18n.t("cli.help.kind"))
    p.add_argument("--cidr", default=None)
    p.add_argument("--text", default=None)
    p.add_argument("--host", default=None, help=i18n.t("cli.help.host"))
    p.add_argument("--ports", default=None, help=i18n.t("cli.help.ports"))
    p.add_argument("--note", default=None, help=i18n.t("cli.help.note"))
    p.add_argument("--command", default=None, help=i18n.t("cli.help.command"))
    p.add_argument("--user", default=None, help=i18n.t("cli.help.user"))
    p.add_argument("--password", default=None, help=i18n.t("cli.help.password"))
    p.set_defaults(fn=cmd_mission)

    p = sub.add_parser("scan", help=i18n.t("cli.help.scan"))
    p.add_argument("--cidr", default=None)
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("reports", help=i18n.t("cli.help.reports"))
    p.add_argument("--show", type=int, default=0, help=i18n.t("cli.help.show"))
    p.add_argument("--limit", type=int, default=10, help=i18n.t("cli.help.limit"))
    p.add_argument("--rebuild", action="store_true", help=i18n.t("cli.help.rebuild"))
    p.set_defaults(fn=cmd_reports)

    p = sub.add_parser("brain", help=i18n.t("cli.help.brain"))
    p.add_argument("--action", choices=("status", "provision", "on", "off", "tidy", "cloud"),
                   default="status", help=i18n.t("cli.help.action"))
    p.set_defaults(fn=cmd_brain)

    args = ap.parse_args(argv)
    if not getattr(args, "cmd", None):
        return cmd_panel(argparse.Namespace(selftest=False))   # без аргументов — окно
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
