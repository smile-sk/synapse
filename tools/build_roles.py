"""Сборка точной карты нейронов для Synapse (разовый запуск, потом по обновлению).

    python tools/build_roles.py                     взять пути из config.json
    python tools/build_roles.py --cache <путь>      явно указать owmeta_cache.json
    python tools/build_roles.py --dry               показать, что получится, не писать

Читает выгрузку OpenWorm (ConnectomeLab/data/owmeta_cache.json) и своё тело
(data/body/worm.json), пишет data/body/neuron_roles.json — ту самую карту,
из которой тело берёт роли, а мозг понимает, КУДА стучать и ОТКУДА читать.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from synapse import config, roles as roles_mod            # noqa: E402

DEFAULT_CACHE = Path("D:/Documents/Projects/ConnectomeLab/data/owmeta_cache.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="build_roles")
    ap.add_argument("--cache", default=str(DEFAULT_CACHE))
    ap.add_argument("--body", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args(argv)

    cfg = config.load()
    cache_p = Path(args.cache)
    body_p = Path(args.body or cfg["body"]["file"])
    if not body_p.is_absolute():
        body_p = config.ROOT / body_p
    out_p = Path(args.out or cfg["body"].get("roles_file") or "data/body/neuron_roles.json")
    if not out_p.is_absolute():
        out_p = config.ROOT / out_p

    if not cache_p.exists():
        print(f"нет выгрузки атласа: {cache_p}", file=sys.stderr)
        return 2
    if not body_p.exists():
        print(f"нет тела: {body_p}", file=sys.stderr)
        return 2

    cache = json.loads(cache_p.read_text(encoding="utf-8"))
    body = json.loads(body_p.read_text(encoding="utf-8"))
    built = roles_mod.build(cache, body)
    stat = roles_mod.summary(built)

    # каждый узел тела обязан получить роль — иначе карта не «точная», а дырявая
    missing = [n["id"] for n in body.get("nodes", []) if str(n.get("id")) not in built]
    stat["missing"] = missing
    stat["unmapped_in_atlas"] = [k for k, v in built.items() if not v.get("in_atlas")]
    stat["source"] = roles_mod.SOURCE

    print(json.dumps(stat, ensure_ascii=False, indent=2))
    if missing:
        print("ОТКАЗ: не всем узлам досталось роль", file=sys.stderr)
        return 3
    if args.dry:
        return 0
    out_p.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_p.with_suffix(out_p.suffix + ".tmp")
    tmp.write_text(json.dumps(built, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out_p)
    print(f"карта записана: {out_p} ({out_p.stat().st_size} байт)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
