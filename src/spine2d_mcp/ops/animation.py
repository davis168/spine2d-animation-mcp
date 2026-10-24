"""Add and remove animations."""

from __future__ import annotations

from ..edits import EditError
from ..spine import document as d
from ..spine.animation import rename_bones, to_wire
from ..spine.presets import preset as make_preset


def _merge(base: dict, extra: dict) -> dict:
    """Overlay `extra` on `base`: per bone/slot timeline for bones and slots,
    whole groups otherwise (events, drawOrder, constraint timelines)."""
    out = dict(base)
    for group, value in extra.items():
        if group in ("bones", "slots") and isinstance(value, dict):
            merged = {name: dict(tls) for name, tls in (out.get(group) or {}).items()}
            for name, tls in value.items():
                merged.setdefault(name, {}).update(tls or {})
            out[group] = merged
        else:
            out[group] = value
    return out


def add_animation(
    doc: dict,
    name: str,
    preset: str | None = None,
    timelines: dict | None = None,
    bone_map: dict[str, str] | None = None,
    side: str = "right",
    speed: float = 1.0,
    amount: float = 1.0,
    replace: bool = False,
) -> dict:
    if not name:
        raise EditError("the animation needs a name")
    animations = doc.setdefault("animations", {})
    if name in animations and not replace:
        raise EditError(f"animation {name!r} already exists (pass replace=true to overwrite it)")
    if preset is None and not timelines:
        raise EditError("give a preset, timelines, or both")

    anim: dict = {}
    skipped: list[str] = []
    if preset is not None:
        height = (doc.get("skeleton") or {}).get("height") or None
        anim = make_preset(preset, height=height, side=side, speed=speed, amount=amount)
        if bone_map:
            anim = rename_bones(anim, bone_map)
        bones = d.bone_map(doc)
        skipped = sorted(b for b in anim["bones"] if b not in bones)
        anim["bones"] = {b: t for b, t in anim["bones"].items() if b in bones}
        if not anim["bones"]:
            raise EditError(
                f"none of the {preset!r} preset's bones exist in this rig; map them with bone_map "
                "(preset name -> rig name)"
            )
    if timelines:
        anim = _merge(anim, timelines)

    wire = to_wire(anim)
    defined = []
    events = doc.setdefault("events", {}) if wire.get("events") else doc.get("events")
    for key in wire.get("events") or []:
        event = key.get("name")
        if isinstance(event, str) and event not in events:
            events[event] = {}
            defined.append(event)
    animations[name] = wire
    return {
        "animation": name,
        "duration": round(d.animation_duration(wire), 4),
        "bones": sorted(wire.get("bones") or {}),
        "slots": sorted(wire.get("slots") or {}),
        "skipped_bones": skipped,
        "defined_events": defined,
    }


def remove_animation(doc: dict, name: str) -> dict:
    animations = doc.get("animations") or {}
    if name not in animations:
        raise EditError(f"no animation {name!r} (have {', '.join(animations) or 'none'})")
    del animations[name]
    if not animations:
        doc.pop("animations", None)
    return {"removed": name}
