"""A compact, JSON-ready picture of a skeleton: what an agent needs to read
before editing it (names, hierarchy, setup positions, what each animation keys).
"""

from __future__ import annotations

from . import document as d
from .check import summary


def _r(value: float) -> float:
    return round(float(value), 3)


def inspect(doc: dict) -> dict:
    world = d.world_transforms(doc)
    bones = []
    for b in d.bones(doc):
        w = world[b["name"]]
        entry = {"name": b["name"], "parent": b.get("parent")}
        for key, default in (("x", 0), ("y", 0), ("rotation", 0), ("scaleX", 1), ("scaleY", 1),
                             ("shearX", 0), ("shearY", 0), ("length", 0)):
            if b.get(key, default) != default:
                entry[key] = _r(b[key])
        if b.get("inherit", "normal") != "normal":
            entry["inherit"] = b["inherit"]
        entry["world"] = [_r(w.x), _r(w.y)]
        bones.append(entry)

    slots = []
    for i, s in enumerate(d.slots(doc)):
        entry = {"name": s["name"], "bone": s.get("bone"), "draw_index": i, "attachment": s.get("attachment")}
        for key in ("color", "dark", "blend"):
            if key in s:
                entry[key] = s[key]
        slots.append(entry)

    skins = []
    for skin in d.skins(doc):
        atts = {}
        for slot, named in (skin.get("attachments") or {}).items():
            atts[slot] = {}
            for name, att in (named or {}).items():
                info = {"type": att.get("type", "region")}
                for key in ("path", "width", "height", "x", "y", "rotation", "parent", "end"):
                    if key in att:
                        info[key] = _r(att[key]) if isinstance(att[key], (int, float)) else att[key]
                if "uvs" in att:
                    info["vertices"] = len(att["uvs"]) // 2
                    info["weighted"] = len(att.get("vertices") or []) != len(att["uvs"])
                atts[slot][name] = info
        skins.append({"name": skin.get("name"), "attachments": atts})

    constraints = {}
    for kind, c in d.all_constraints(doc):
        info = {k: c[k] for k in ("name", "order", "bones", "bone", "target") if k in c}
        constraints.setdefault(kind, []).append(info)

    animations = []
    for name, anim in (doc.get("animations") or {}).items():
        anim = anim or {}
        timelines = sum(len(t or {}) for t in (anim.get("bones") or {}).values())
        timelines += sum(len(t or {}) for t in (anim.get("slots") or {}).values())
        animations.append({
            "name": name,
            "duration": _r(d.animation_duration(anim)),
            "bones": sorted(anim.get("bones") or {}),
            "slots": sorted(anim.get("slots") or {}),
            "constraints": sorted(
                f"{kind}:{c}" for kind in d.CONSTRAINT_KINDS for c in (anim.get(kind) or {})
            ),
            "timelines": timelines,
            "events": len(anim.get("events") or []),
            "draw_order_keys": len(anim.get("drawOrder") or []),
        })

    return {
        "summary": summary(doc),
        "skeleton": doc.get("skeleton") or {},
        "bones": bones,
        "slots": slots,
        "skins": skins,
        "constraints": constraints,
        "events": sorted(doc.get("events") or {}),
        "animations": animations,
    }
