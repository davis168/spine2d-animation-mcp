"""Add and remove IK, transform and physics constraints (Spine 4.2 layout:
one top-level array per type, each entry with an `order`)."""

from __future__ import annotations

import math

from ..edits import EditError
from ..spine import document as d
from .rig import add_bone

# Starting points for common secondary motion; tune per character. Each keys
# which bone properties the simulation moves (0..1) and how it behaves.
PHYSICS_PRESETS = {
    "hair": {"rotate": 1, "inertia": 0.5, "strength": 80, "damping": 0.85, "mass": 1},
    "cloth": {"rotate": 1, "x": 0.3, "inertia": 0.7, "strength": 60, "damping": 0.8, "mass": 1.5},
    "tail": {"rotate": 1, "inertia": 0.4, "strength": 50, "damping": 0.75, "mass": 1},
    "antenna": {"rotate": 1, "inertia": 0.3, "strength": 150, "damping": 0.6, "mass": 0.5},
    "jiggle": {"y": 1, "scaleX": 0.3, "inertia": 0.6, "strength": 120, "damping": 0.7, "mass": 1},
}

_IK_FIELDS = {"mix": 1, "softness": 0, "bendPositive": True, "compress": False, "stretch": False, "uniform": False}
_TRANSFORM_FIELDS = {"rotation": 0, "x": 0, "y": 0, "scaleX": 0, "scaleY": 0, "shearY": 0,
                     "mixRotate": 1, "mixX": 1, "mixY": None, "mixScaleX": 1, "mixScaleY": None, "mixShearY": 1,
                     "local": False, "relative": False}
_PHYSICS_FIELDS = {"x": 0, "y": 0, "rotate": 0, "scaleX": 0, "shearX": 0, "limit": 5000, "fps": 60,
                   "inertia": 1, "strength": 100, "damping": 1, "mass": 1, "wind": 0, "gravity": 0, "mix": 1,
                   "inertiaGlobal": False, "strengthGlobal": False, "dampingGlobal": False, "massGlobal": False,
                   "windGlobal": False, "gravityGlobal": False, "mixGlobal": False}


def _apply(entry: dict, params: dict, fields: dict, kind: str) -> None:
    for key, value in params.items():
        if key not in fields:
            raise EditError(f"{kind} constraints have no {key!r} (have {', '.join(fields)})")
        if value != fields[key]:
            entry[key] = value


def _next_order(doc: dict) -> int:
    orders = [c.get("order", 0) for _, c in d.all_constraints(doc)]
    return max(orders) + 1 if orders else 0


def add_constraint(
    doc: dict,
    type: str,
    name: str,
    bones: list[str] | None = None,
    bone: str | None = None,
    target: str | None = None,
    target_at: str | list[float] | None = None,
    target_parent: str | None = None,
    preset: str | None = None,
    order: int | None = None,
    replace: bool = False,
    **params,
) -> dict:
    if type not in ("ik", "transform", "physics"):
        raise EditError("type must be ik, transform or physics (path constraints need path attachments)")
    existing = {c.get("name"): (k, c) for k, c in d.all_constraints(doc)}
    if name in existing:
        if not replace:
            raise EditError(f"a {existing[name][0]} constraint named {name!r} already exists (pass replace=true)")
        remove_constraint(doc, name, keep_timelines=True)
    entry: dict = {"name": name, "order": _next_order(doc) if order is None else order}
    detail: dict = {"type": type, "constraint": name}

    if type == "physics":
        if not bone:
            raise EditError("physics constraints need `bone`")
        if bone not in d.bone_map(doc):
            raise EditError(f"no bone {bone!r}")
        entry["bone"] = bone
        if preset is not None:
            if preset not in PHYSICS_PRESETS:
                raise EditError(f"unknown physics preset {preset!r} (have {', '.join(PHYSICS_PRESETS)})")
            params = {**PHYSICS_PRESETS[preset], **params}
        _apply(entry, params, _PHYSICS_FIELDS, type)
        if not any(entry.get(k) for k in ("x", "y", "rotate", "scaleX", "shearX")):
            raise EditError("physics moves nothing: set at least one of x, y, rotate, scaleX, shearX (0..1), or a preset")
    else:
        if not bones:
            raise EditError(f"{type} constraints need `bones`")
        for b in bones:
            if b not in d.bone_map(doc):
                raise EditError(f"no bone {b!r}")
        if not target:
            raise EditError(f"{type} constraints need a target bone")
        if target not in d.bone_map(doc):
            if target_at is None:
                raise EditError(f"no bone {target!r}; pass target_at (a bone name or [x, y] world point) to create it")
            if isinstance(target_at, str):
                if target_at not in d.bone_map(doc):
                    raise EditError(f"target_at bone {target_at!r} not found")
                w = d.world_transforms(doc)[target_at]
                at = [w.x, w.y]
            else:
                at = list(target_at)
            parent = target_parent or d.bones(doc)[0]["name"]
            add_bone(doc, target, parent, world=True, x=at[0], y=at[1])
            detail["created_target"] = {"bone": target, "parent": parent, "world": [round(v, 3) for v in at]}
        entry["bones"] = list(bones)
        entry["target"] = target
        _apply(entry, params, _IK_FIELDS if type == "ik" else _TRANSFORM_FIELDS, type)
        if type == "ik":
            turn = _ik_setup_turn(doc, bones, target)
            if turn is not None and abs(turn) > 5:
                detail["warning"] = (
                    f"IK aims {bones[0]!r}'s x axis at the target, which is {turn:.0f} deg away from it at "
                    "setup, so the pose will swing by that much. IK suits rigs whose bones point along "
                    "their limbs (x axis toward the child); rigs from import_psd_to_spine have unrotated "
                    "bones, so aim limbs with rotate keys instead, or orient the bones first."
                )

    doc.setdefault(type, []).append(entry)
    return {**detail, "order": entry["order"]}


def _ik_setup_turn(doc: dict, bones: list[str], target: str) -> float | None:
    """Angle between the chain root's x axis and the direction to the target."""
    world = d.world_transforms(doc)
    root, tgt = world[bones[0]], world[target]
    dx, dy = tgt.x - root.x, tgt.y - root.y
    if math.hypot(dx, dy) < 1e-6:
        return None
    axis = math.degrees(math.atan2(root.c, root.a))
    turn = (math.degrees(math.atan2(dy, dx)) - axis + 180) % 360 - 180
    return turn


def remove_constraint(doc: dict, name: str, keep_timelines: bool = False) -> dict:
    for kind in d.CONSTRAINT_KINDS:
        entries = d.constraints(doc, kind)
        if any(c.get("name") == name for c in entries):
            doc[kind] = [c for c in entries if c.get("name") != name]
            if not doc[kind]:
                del doc[kind]
            for skin in d.skins(doc):
                if name in (skin.get(kind) or []):
                    skin[kind].remove(name)
            if not keep_timelines:
                for anim in (doc.get("animations") or {}).values():
                    ((anim or {}).get(kind) or {}).pop(name, None)
            return {"removed": name, "type": kind}
    raise EditError(f"no constraint {name!r}")
