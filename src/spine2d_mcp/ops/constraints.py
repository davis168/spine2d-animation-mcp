"""Add and remove IK, transform, path, physics and (4.3) slider constraints,
in the skeleton's own format version.

4.2 keeps one top-level array per type, each entry with an `order`; 4.3 keeps
one `constraints` array whose position is the apply order. The options are
one API for both: where the versions name the same thing differently it is
translated (`uniform` <-> `scaleY: "uniform"`, `local` <-> `localSource` +
`localTarget`, `relative` <-> `additive`, the transform's `target` <->
`source`), and an option only 4.3 has is refused on a 4.2 skeleton.
"""

from __future__ import annotations

import math

from ..edits import EditError
from ..spine import document as d
from ..spine import versions
from ..spine.check import SCALE_Y_MODES, TRANSFORM_PROPERTIES
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

# Field -> the reader's default (None: "same as another mix", see _transform).
_IK = {"mix": 1, "softness": 0, "bendPositive": True, "compress": False, "stretch": False}
_TRANSFORM = {"rotation": 0, "x": 0, "y": 0, "scaleX": 0, "scaleY": 0, "shearY": 0,
              "mixRotate": 1, "mixX": 1, "mixY": None, "mixScaleX": 1, "mixScaleY": None, "mixShearY": 1}
_PHYSICS = {"x": 0, "y": 0, "rotate": 0, "scaleX": 0, "shearX": 0, "limit": 5000, "fps": 60,
            "strength": 100, "mass": 1, "wind": 0, "gravity": 0, "mix": 1,
            "inertiaGlobal": False, "strengthGlobal": False, "dampingGlobal": False, "massGlobal": False,
            "windGlobal": False, "gravityGlobal": False, "mixGlobal": False}
_PHYSICS_DEFAULTS = {"4.2": {"inertia": 1, "damping": 1}, "4.3": {"inertia": 0.5, "damping": 0.85}}
_SLIDER = {"additive": False, "loop": False, "mix": 1, "time": 0, "property": None, "from": 0, "to": 0,
           "scale": 1, "max": 0, "local": False}
_PATH = {"positionMode": "percent", "spacingMode": "length", "rotateMode": "tangent", "rotation": 0,
         "position": 0, "spacing": 0, "mixRotate": 1, "mixX": 1, "mixY": None}
PATH_MODES = {"positionMode": ("fixed", "percent"), "spacingMode": ("length", "fixed", "percent", "proportional"),
              "rotateMode": ("tangent", "chain", "chainScale")}
_MIX = {"rotate": "mixRotate", "x": "mixX", "y": "mixY", "scaleX": "mixScaleX", "scaleY": "mixScaleY",
        "shearY": "mixShearY"}


def _only_43(version: str, what: str) -> None:
    if version != "4.3":
        raise EditError(f"{what} needs format version 4.3; this skeleton is {version} "
                        "(convert_skeleton converts it)")


def _apply(entry: dict, params: dict, fields: dict, kind: str) -> None:
    for key, value in params.items():
        if key not in fields:
            raise EditError(f"{kind} constraints have no {key!r} (have {', '.join(fields)})")
        if value != fields[key]:
            entry[key] = value


def _ik(version: str, params: dict) -> tuple[dict, dict]:
    params = dict(params)
    if version == "4.2":
        mode = params.pop("scaleY", None)
        if mode not in (None, "none", "uniform"):
            _only_43(version, f"IK scaleY {mode!r}")
        if mode is not None:
            params["uniform"] = mode == "uniform"
        return params, {**_IK, "uniform": False}
    if "uniform" in params:
        if "scaleY" in params:
            raise EditError("give scaleY or uniform, not both")
        params["scaleY"] = "uniform" if params.pop("uniform") else "none"
    _check_scale_y(params)
    return params, {**_IK, "scaleY": "none"}


def _check_scale_y(params: dict) -> None:
    """4.3 IK and physics scale modes."""
    if params.get("scaleY", "none") not in SCALE_Y_MODES:
        raise EditError(f"scaleY must be none, uniform or volume, not {params['scaleY']!r}")


def _transform(version: str, params: dict, entry: dict) -> None:
    params = dict(params)
    if version == "4.2":
        if "additive" in params:
            params["relative"] = params.pop("additive")
        if "localSource" in params or "localTarget" in params:
            local_source, local_target = params.pop("localSource", False), params.pop("localTarget", False)
            if local_source != local_target:
                _only_43(version, "a local source with a world target (or the reverse)")
            params["local"] = local_source
        for key in ("clamp", "properties"):
            if params.get(key):
                _only_43(version, f"transform {key}")
            params.pop(key, None)
        _apply(entry, params, {**_TRANSFORM, "local": False, "relative": False}, "transform")
        return
    if "local" in params:
        params["localSource"] = params["localTarget"] = params.pop("local")
    if "relative" in params:
        params["additive"] = params.pop("relative")
    properties = params.pop("properties", None)
    if properties is None:  # each property drives itself, as the editor sets up a new constraint
        properties = {p: {"to": {p: {} if p in ("scaleX", "scaleY") else {"max": 100}}} for p in TRANSFORM_PROPERTIES}
    if not isinstance(properties, dict) or not properties:
        raise EditError("properties must map source properties to {offset, to: {target: {offset, max, scale}}}")
    mapped = set()
    for source, spec in properties.items():
        if source not in TRANSFORM_PROPERTIES or not isinstance(spec, dict) or not isinstance(spec.get("to"), dict):
            raise EditError(f"properties.{source}: need a source property ({', '.join(TRANSFORM_PROPERTIES)}) "
                            "with a 'to' object")
        for target in spec["to"]:
            if target not in TRANSFORM_PROPERTIES:
                raise EditError(f"properties.{source}.to.{target}: not one of {', '.join(TRANSFORM_PROPERTIES)}")
            mapped.add(target)
    fields = {**_TRANSFORM, "localSource": False, "localTarget": False, "additive": False, "clamp": False}
    _apply(entry, params, fields, "transform")
    entry["properties"] = properties
    # 4.3 reads mixY as mixX and mixScaleY as mixScaleX only when x / scaleX is
    # mapped; otherwise a missing value is 0, so write the intended default.
    for prop, base in (("y", "x"), ("scaleY", "scaleX")):
        if prop in mapped and base not in mapped and _MIX[prop] not in entry:
            entry[_MIX[prop]] = 1
    for prop, mix in _MIX.items():
        if prop not in mapped and mix in entry:
            raise EditError(f"{mix} has no effect: no property maps to {prop}")


def _slider(doc: dict, entry: dict, bone: str | None, params: dict) -> None:
    params = dict(params)
    animation = params.pop("animation", None)
    if animation not in (doc.get("animations") or {}):
        have = ", ".join(doc.get("animations") or {}) or "none"
        raise EditError(f"a slider plays an animation: give animation, one of this skeleton's ({have})")
    entry["animation"] = animation
    if bone is not None:
        if bone not in d.bone_map(doc):
            raise EditError(f"no bone {bone!r}")
        if params.get("property") not in TRANSFORM_PROPERTIES:
            raise EditError(f"a bone-driven slider needs property, one of {', '.join(TRANSFORM_PROPERTIES)}")
        if "time" in params:
            raise EditError("a slider is driven by a bone (bone, property, from, to) or by time, not both")
        entry["bone"] = bone
        entry["property"] = params.pop("property")
    else:
        for key in ("property", "from", "to", "scale", "max", "local"):
            if key in params:
                raise EditError(f"{key} needs bone (a bone-driven slider)")
    _apply(entry, params, _SLIDER, "slider")


def _path(doc: dict, version: str, entry: dict, bones: list[str] | None, slot: str | None, params: dict) -> None:
    """Bones follow the path attachment the slot shows. mixY follows mixX
    unless given (4.3 reads it so; 4.2 reads 1, so it is written out)."""
    if not bones or any(b not in d.bone_map(doc) for b in bones):
        raise EditError("path constraints need `bones` (this skeleton's) to move along the path")
    if slot not in d.slot_map(doc):
        raise EditError("path constraints need target: the slot showing the path (add_shape type=path adds one)")
    paths = [n for s in d.skins(doc) for n, a in (((s.get("attachments") or {}).get(slot)) or {}).items()
             if isinstance(a, dict) and a.get("type") == "path"]
    if not paths:
        raise EditError(f"slot {slot!r} has no path attachment (add_shape type=path adds one)")
    for key, allowed in PATH_MODES.items():
        if key in params and params[key] not in allowed:
            raise EditError(f"{key} must be one of {', '.join(allowed)}")
    entry["bones"] = list(bones)
    entry[d.path_slot_key(doc)] = slot
    params = dict(params)
    mix_x = params.get("mixX", 1)
    mix_y = params.pop("mixY", mix_x)
    if mix_y != (mix_x if version == "4.3" else 1):
        params["mixY"] = mix_y
    _apply(entry, params, _PATH, "path")


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
    version = versions.layout(doc)
    allowed = ("ik", "transform", "path", "physics") + (("slider",) if version == "4.3" else ())
    if type == "slider":
        _only_43(version, "a slider")
    if type not in allowed:
        raise EditError(f"type must be {', '.join(allowed)}")
    existing = {c.get("name"): (k, c) for k, c in d.all_constraints(doc)}
    before = d.never_applied(doc)
    in_skins: list[dict] = []
    skin_only = False
    if name in existing:
        if not replace:
            raise EditError(f"a {existing[name][0]} constraint named {name!r} already exists (pass replace=true)")
        old_kind = existing[name][0]
        skin_only = bool(existing[name][1].get("skin"))
        in_skins = [s for s in d.skins(doc) if name in (s.get(old_kind) or [])]
        removed = remove_constraint(doc, name, keep_timelines=True)
        if order is None:
            order = removed["order"]  # a replaced constraint keeps its place
    entry: dict = {"type": type, "name": name} if version == "4.3" else {"name": name, "order": 0}
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
        fields = {**_PHYSICS, **_PHYSICS_DEFAULTS[version]}
        if version == "4.3":
            fields["scaleY"] = "none"
            _check_scale_y(params)
        elif params.get("scaleY", "none") != "none":
            _only_43(version, "physics scaleY")
        else:
            params.pop("scaleY", None)
        _apply(entry, params, fields, type)
        if not any(entry.get(k) for k in ("x", "y", "rotate", "scaleX", "shearX")):
            raise EditError("physics moves nothing: set at least one of x, y, rotate, scaleX, shearX (0..1), or a preset")
        tip = [k for k in d.PHYSICS_TIP if entry.get(k)]
        if tip and not d.bone_map(doc)[bone].get("length"):
            raise EditError(f"bone {bone!r} has no length, which physics {', '.join(tip)} need (they swing the bone's "
                            "tip): give it a length first (edit_rig set_bone length=..., about the art's size along the "
                            "bone); the Spine editor drops them on a bone without one")
    elif type == "slider":
        _slider(doc, entry, bone, params)
    elif type == "path":
        _path(doc, version, entry, bones, target, params)
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
        entry[d.target_bone_key(doc, type)] = target
        if type == "ik":
            params, fields = _ik(version, params)
            _apply(entry, params, fields, type)
            turn = _ik_setup_turn(doc, bones, target)
            if turn is not None and abs(turn) > 5:
                detail["warning"] = (
                    f"IK aims {bones[0]!r}'s x axis at the target, which is {turn:.0f} deg away from it at "
                    "setup, so the pose will swing by that much. IK suits rigs whose bones point along "
                    "their limbs (x axis toward the child); rigs from import_psd_to_spine have unrotated "
                    "bones, so aim limbs with rotate keys instead, or orient the bones first."
                )
        else:
            _transform(version, params, entry)

    if skin_only:
        entry["skin"] = True
    position = _insert(doc, version, type, entry, order)
    for skin in in_skins:  # a replaced constraint stays in the skins that had it
        skin.setdefault(type, []).append(name)
    return {**detail, "order": position, **_revived(before, doc)}


def _insert(doc: dict, version: str, kind: str, entry: dict, order: int | None) -> int:
    """Add the entry at apply position `order` (default last); returns it.
    4.2 orders are renumbered 0..n-1, the only orders its runtime runs."""
    ordered = d.apply_order(doc)
    index = len(ordered) if order is None else max(0, min(order, len(ordered)))
    if version == "4.2":
        doc.setdefault(kind, []).append(entry)
        ordered.insert(index, (kind, entry))
        d.renumber(doc, ordered)
    else:
        doc.setdefault("constraints", []).insert(index, entry)
    return index


def _revived(before: list[str], doc: dict) -> dict:
    """A note when renumbering made 4.2 constraints run that never did."""
    revived = [n for n in before if n not in d.never_applied(doc)]
    if not revived:
        return {}
    return {"note": f"constraint orders renumbered 0..n-1: {', '.join(map(str, revived))} never ran before "
                    "(order out of range or shared) and now runs"}


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
    """Remove a constraint with its skin entries (and timelines unless kept).
    Returns its type and apply position; 4.2 orders are renumbered 0..n-1."""
    version = versions.layout(doc)
    before = d.never_applied(doc)
    ordered = d.apply_order(doc)
    for position, (kind, c) in enumerate(ordered):
        if c.get("name") != name:
            continue
        key = kind if version == "4.2" else "constraints"
        doc[key] = [x for x in doc[key] if x is not c]
        if not doc[key]:
            del doc[key]
        if version == "4.2":
            d.renumber(doc, [e for e in ordered if e[1] is not c])
        for skin in d.skins(doc):
            if name in (skin.get(kind) or []):
                skin[kind].remove(name)
        if not keep_timelines:
            for anim in (doc.get("animations") or {}).values():
                ((anim or {}).get(kind) or {}).pop(name, None)
        return {"removed": name, "type": kind, "order": position, **_revived(before, doc)}
    raise EditError(f"no constraint {name!r}")
