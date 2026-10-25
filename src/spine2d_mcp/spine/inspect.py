"""A compact, JSON-ready picture of a skeleton: what an agent needs to read
before editing it (names, hierarchy, setup positions, what each animation keys).
"""

from __future__ import annotations

from . import document as d
from . import versions
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
                for key in ("path", "width", "height", "x", "y", "rotation", "parent", "source", "slot", "end",
                            "convex", "inverse"):
                    if key in att:
                        number = isinstance(att[key], (int, float)) and not isinstance(att[key], bool)
                        info[key] = _r(att[key]) if number else att[key]
                if "uvs" in att:
                    info["vertices"] = len(att["uvs"]) // 2
                    info["weighted"] = len(att.get("vertices") or []) != len(att["uvs"])
                atts[slot][name] = info
        skins.append({"name": skin.get("name"), "attachments": atts})

    # In apply order; `target` is the bone an IK or transform constraint
    # follows, whatever the version calls it (4.3 transform: `source`).
    constraints = {}
    for applies, (kind, c) in enumerate(d.apply_order(doc)):
        info = {"name": c.get("name"), "applies": applies}
        for k in ("order", "bones", "bone", "animation", "property", "scaleY", "uniform", "local", "relative",
                  "localSource", "localTarget", "additive", "skin"):
            if k in c:
                info[k] = c[k]
        key = d.target_bone_key(doc, kind)
        if key:
            info["target"] = c.get(key)
        if kind == "path":
            info["slot"] = c.get(d.path_slot_key(doc))
        if isinstance(c.get("properties"), dict):
            info["properties"] = {src: sorted((spec or {}).get("to") or {}) for src, spec in c["properties"].items()}
        constraints.setdefault(kind, []).append(info)

    names = [s["name"] for s in d.slots(doc)]
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
            **({"folder_keys": [d.folder_of(names, (e or {}).get("slots")) or (e or {}).get("slots")
                                       for e in anim["drawOrderFolder"]]}
               if isinstance(anim.get("drawOrderFolder"), list) else {}),
        })

    folders = d.draw_order_folders(names)
    return {
        "summary": summary(doc),
        "format_version": versions.of(doc),
        "skeleton": doc.get("skeleton") or {},
        "bones": bones,
        "slots": slots,
        **({"draw_order_folders": folders} if folders else {}),
        "skins": skins,
        "constraints": constraints,
        "events": sorted(doc.get("events") or {}),
        "animations": animations,
    }
