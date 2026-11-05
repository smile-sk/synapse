"""Точная карта нейронов Synapse — из атласа, а не из догадок.

Откуда берём (единственный источник, никакой отсебятины):
  * ConnectomeLab/data/owmeta_cache.json — выгрузка OpenWorm (owmeta v0.12.3):
    для каждого из 302 нейронов ТИП (sensory / interneuron / motor), медиаторы
    и список рецепторов. Это и есть «точная карта», о которой просил Стас.
  * тело (data/body/worm.json, Cook2019) — вес узла, координаты, медиатор.

Что делаем из этого:
  role      — главная роль для мозга: sensory | inter | motor | polymodal | other
  types     — КАК СКАЗАЛ АТЛАС, сырыми словами (не переписываем атлас)
  nt        — нейромедиатор (из тела, если пусто — из атласа)
  deg/in/out — степень узла (сколько связей) и веса; hub — топ-5% по связности
  zone      — вдоль тела: голова / середина / хвост (в этой выгрузке ГОЛОВА —
              там, где отрицательный y: замер на живом теле — амфидные нейроны
              ADLL/ASEL/AWAL/AFDL лежат при y≈-0.44, а хвостовые PHCL/PLML при
              y≈+1.43; в первой версии я взяла ось наоборот и получила
              «210 хвостовых» — это была ошибка знака, а не биология)
  receptors — сколько рецепторов названо (чем нейрон слушает мир)

Правило разрешения составных типов (проверяемо, без тайн):
  sensory+motor  -> polymodal   (IL1, M3, MC, NSM, URA, VB — так у атласа)
  только motor   -> motor
  только sensory -> sensory
  inter+...      -> по более специальному (DVA -> sensory, AVL -> motor)
  unknown/пусто  -> other       (CANL/CANR — клетки без синапсов, честно)
"""
from __future__ import annotations

import json
from pathlib import Path

ROLES = ("sensory", "inter", "motor", "polymodal", "other")
ROLE_RU = {"sensory": "сенсорный", "inter": "вставочный", "motor": "мотонейрон",
           "polymodal": "полимодальный", "other": "прочее", "unknown": "неизвестно"}
# какие роли отзываются на стимул и какие считаются «руками» тела
STIMULUS_POOL = ("sensory", "polymodal")
ACTION_POOL = ("motor", "polymodal")

SOURCE = "OpenWorm owmeta v0.12.3 (owmeta_cache.json) + Cook2019 herm_full_edgelist"


def resolve_role(types) -> str:
    """Типы атласа -> одна главная роль. Функция чистая: её и проверяем тестом."""
    t = {str(x).strip().lower() for x in (types or []) if str(x).strip()}
    sensory, motor, inter = "sensory" in t, "motor" in t, "interneuron" in t
    if sensory and motor:
        return "polymodal"
    if sensory:
        return "sensory"
    if motor:
        return "motor"
    if inter:
        return "inter"
    return "other"


def build(cache: dict, body: dict) -> dict:
    """Собрать карту на все узлы тела. cache — owmeta, body — worm.json."""
    info = (cache or {}).get("neuron_info", {}) or {}
    nodes = list((body or {}).get("nodes", []))
    ids = [str(n.get("id", "?")) for n in nodes]

    deg = [0] * len(ids)
    w_in = [0.0] * len(ids)
    w_out = [0.0] * len(ids)
    for e in (body or {}).get("edges", []):
        a, b = int(e.get("a", -1)), int(e.get("b", -1))
        w = float(e.get("w", 1.0))
        if 0 <= a < len(ids):
            deg[a] += 1
            w_out[a] += w
        if 0 <= b < len(ids):
            deg[b] += 1
            w_in[b] += w

    ys = [float(n.get("y", 0.0)) for n in nodes]
    lo, hi = (min(ys), max(ys)) if ys else (-1.0, 1.0)
    span = (hi - lo) or 1.0

    def zone_of(i: int) -> str:
        # rel=1 — голова (минимальный y в этой выгрузке), rel=0 — хвост
        rel = (hi - ys[i]) / span
        if rel >= 0.66:
            return "голова"
        if rel <= 0.33:
            return "хвост"
        return "середина"

    hubs = sorted(range(len(ids)), key=lambda i: -deg[i])
    hub_cut = deg[hubs[min(len(hubs) - 1, max(0, len(hubs) // 20))]] if hubs else 0

    out: dict[str, dict] = {}
    for i, name in enumerate(ids):
        rec = info.get(name) or info.get(name.upper()) or []
        types = list(rec[1]) if len(rec) > 1 and isinstance(rec[1], list) else []
        receptors = list(rec[2]) if len(rec) > 2 and isinstance(rec[2], list) else []
        atlas_nt = list(rec[3]) if len(rec) > 3 and isinstance(rec[3], list) else []
        marker = str(rec[4]) if len(rec) > 4 else ""
        nt = str(nodes[i].get("nt") or "") or (atlas_nt[0] if atlas_nt else "")
        out[name] = {
            "role": resolve_role(types),
            "types": types,
            "nt": nt,
            "atlas_nt": atlas_nt,
            "marker": marker,                    # "+ (MoSe) VB1" — как атлас себя называет
            "receptors": len(receptors),
            "deg": deg[i], "w_in": round(w_in[i], 1), "w_out": round(w_out[i], 1),
            "hub": bool(deg[i] >= hub_cut and deg[i] > 0),
            "zone": zone_of(i),
            "source": SOURCE,
            "in_atlas": bool(rec),
        }
    return out


def load(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def summary(roles: dict) -> dict:
    """Сколько кого — для отчёта в панели."""
    counts: dict[str, int] = {}
    nts: dict[str, int] = {}
    zones: dict[str, int] = {}
    hubs = 0
    for r in roles.values():
        counts[r["role"]] = counts.get(r["role"], 0) + 1
        if r.get("nt"):
            nts[r["nt"]] = nts.get(r["nt"], 0) + 1
        zones[r.get("zone", "?")] = zones.get(r.get("zone", "?"), 0) + 1
        hubs += 1 if r.get("hub") else 0
    return {"total": len(roles), "roles": counts, "nt": nts, "zones": zones, "hubs": hubs}
