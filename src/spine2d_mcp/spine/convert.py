"""Convert a skeleton document between format versions (4.2 <-> 4.3).

4.2 -> 4.3 loses nothing. It follows what the 4.3 editor writes when it opens
a 4.2 project (checked against Spine 4.3.26), with two deliberate
differences: a two-bone IK's `uniform` is kept as `scaleY: "uniform"` (both
runtimes apply it; the editor drops it), and a transform constraint whose
mixes are all zero is kept rather than deleted.

4.3 -> 4.2 reverses that. What 4.2 cannot express (sliders, non-identity
transform property mappings, scale modes other than uniform IK, clipping
convex/inverse, cross-slot linked meshes, draw order folders) is a loss: the
conversion refuses, listing each one, unless `allow_lossy` is passed, in which
case exactly those parts are dropped. Editor-only display data (bone icon size
and rotation) is dropped without counting as a loss.

Defaults that changed between the versions are written out explicitly, so
every value reads back the same: physics inertia (4.2: 1, 4.3: 0.5) and
damping (1, 0.85), the physics `mix` timeline (0, 1), a path constraint's
`mixY` (1, mixX) and a transform key's `mixScaleY` (mixScaleX, 1).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from . import document as d
from . import versions

PROPERTIES = ("rotate", "x", "y", "scaleX", "scaleY", "shearY")
MIX = {"rotate": "mixRotate", "x": "mixX", "y": "mixY", "scaleX": "mixScaleX", "scaleY": "mixScaleY",
       "shearY": "mixShearY"}
_CHANNEL = {p: i for i, p in enumerate(PROPERTIES)}  # transform timeline curve channel per property
_KINDS_42 = versions.FORMATS["4.2"].constraint_kinds


class LossyConversion(ValueError):
    """The target format version cannot express part of the skeleton."""

    def __init__(self, source: str, target: str, losses: list[str]):
        self.losses = losses
        super().__init__(f"converting {source} -> {target} would lose: " + "; ".join(losses))


@dataclass
class Conversion:
    doc: dict
    from_version: str
    to_version: str
    changed: list[str] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)  # dropped because allow_lossy was passed
    dropped_editor_data: list[str] = field(default_factory=list)


def convert(doc: dict, to_version: str, allow_lossy: bool = False) -> Conversion:
    """A converted copy of `doc` (the input is not modified)."""
    source = versions.of(doc)
    versions.get(source)
    target = versions.get(to_version).version
    if source == target:
        raise ValueError(f"the skeleton is already format version {target}")
    out = Conversion(copy.deepcopy(doc), source, target)
    path = list(versions.SUPPORTED)
    step = 1 if path.index(target) > path.index(source) else -1
    i = path.index(source)
    header = out.doc.setdefault("skeleton", {})
    while path[i] != target:
        _STEPS[(path[i], path[i + step])](out, allow_lossy)
        i += step
        header["spine"] = path[i]
    header.pop("hash", None)  # the editor's hash of the old content
    return out


def _key_default(prop: str, values: dict, version: str) -> float:
    """A transform timeline key's default mix: 1, mixY = mixX, and mixScaleY =
    mixScaleX in 4.2 but 1 in 4.3."""
    if prop == "y":
        return values["x"]
    if prop == "scaleY":
        return values["scaleX"] if version == "4.2" else 1
    return 1


def _read_key(k: dict, version: str) -> dict:
    values: dict = {}
    for p in PROPERTIES:
        values[p] = k.get(MIX[p], _key_default(p, values, version))
    return values


def _write_key(k: dict, values: dict, version: str) -> None:
    """Write each mix the target version would not read as `values` by default."""
    for p in PROPERTIES:
        if values[p] != _key_default(p, values, version):
            k[MIX[p]] = values[p]


# ── 4.2 -> 4.3 ──────────────────────────────────────────────────────────────

def _upgrade_42_43(out: Conversion, allow_lossy: bool) -> None:
    doc = out.doc
    keyed = _keyed(doc)
    idle = d.never_applied(doc)
    if idle:
        out.changed.append(f"{', '.join(map(str, idle))} never ran in 4.2 (order out of range or shared); "
                           "in 4.3 they run, after the others")
    converted = []
    for kind, c in d.apply_order(doc):
        entry = {"type": kind, **{k: v for k, v in c.items() if k != "order"}}
        name = c.get("name")
        if kind == "ik":
            if entry.pop("uniform", False):
                entry["scaleY"] = "uniform"
        elif kind == "transform":
            _transform_up(entry, ("transform", name) in keyed)
        elif kind == "path":
            entry["slot"] = entry.pop("target", None)
            if "mixY" not in entry and entry.get("mixX", 1) != 1:
                entry["mixY"] = 1  # 4.2's default; 4.3 would read mixX
        elif kind == "physics":
            _physics_defaults(entry, old=(1, 1), new=(0.5, 0.85))
        converted.append(entry)
    for kind in _KINDS_42:
        doc.pop(kind, None)
    if converted:
        _put_constraints(doc, converted)
        out.changed.append(f"{len(converted)} constraints moved into one 'constraints' array, in apply order")

    linked = 0
    for _, _, att in _attachments(doc):
        if att.get("type") == "linkedmesh" and "parent" in att:
            att["source"] = att.pop("parent")
            linked += 1
    if linked:
        out.changed.append(f"{linked} linked meshes: parent -> source")

    for anim in _animations(doc):
        for keys in (anim.get("transform") or {}).values():
            for k in _key_list(keys):
                _write_key(k, _read_key(k, "4.2"), "4.3")  # mixScaleY: 4.2 reads mixScaleX, 4.3 reads 1
        for timelines in (anim.get("physics") or {}).values():
            for k in _key_list((timelines or {}).get("mix")):
                k.setdefault("value", 0)  # 4.2's default; 4.3 reads 1


def _transform_up(c: dict, keyed: bool) -> None:
    """4.2 transform -> 4.3: target -> source, local -> localSource +
    localTarget, relative -> additive, and each property mapped to itself.
    Like the editor, an unkeyed constraint maps only properties with a mix."""
    c["source"] = c.pop("target", None)
    local, relative = c.pop("local", False), c.pop("relative", False)
    mix_x = c.get("mixX", 1)
    mix_scale_x = c.get("mixScaleX", 1)
    effective = {"rotate": c.get("mixRotate", 1), "x": mix_x, "y": c.get("mixY", mix_x),
                 "scaleX": mix_scale_x, "scaleY": c.get("mixScaleY", mix_scale_x), "shearY": c.get("mixShearY", 1)}
    for mix in MIX.values():
        c.pop(mix, None)
    mapped = [p for p in PROPERTIES if keyed or effective[p] != 0] or list(PROPERTIES)
    c["properties"] = {p: {"to": {p: {} if p in ("scaleX", "scaleY") else {"max": 100}}} for p in mapped}
    if local:
        c["localSource"] = c["localTarget"] = True
    if relative:
        c["additive"] = True
    for p in mapped:
        if effective[p] != _setup_default_43(p, mapped, effective):
            c[MIX[p]] = effective[p]


def _setup_default_43(prop: str, mapped: list[str], effective: dict) -> float:
    """What the 4.3 reader uses for a mapped property's missing setup mix:
    1, but mixY falls back to mixX and mixScaleY to mixScaleX, which are 0
    (the pose's initial value) when their property is not mapped."""
    if prop == "y":
        return effective["x"] if "x" in mapped else 0
    if prop == "scaleY":
        return effective["scaleX"] if "scaleX" in mapped else 0
    return 1


# ── 4.3 -> 4.2 ──────────────────────────────────────────────────────────────

def _downgrade_43_42(out: Conversion, allow_lossy: bool) -> None:
    doc = out.doc
    losses: list[str] = []
    by_kind: dict[str, list[dict]] = {}
    unmapped: dict[str, list[str]] = {}  # transform name -> properties 4.2 must zero
    sliders: list[str] = []
    kept = 0  # 4.2 runs only orders 0..n-1, so number the kept constraints densely
    for kind, c in d.all_constraints(doc):
        name = c.get("name")
        if kind == "slider":
            sliders.append(name)
            losses.append(f"slider {name!r} (4.2 has no sliders)")
            continue
        entry = {k: v for k, v in c.items() if k != "type"}
        entry = {"name": entry.pop("name", name), "order": kept, **entry}
        kept += 1
        if kind == "ik":
            mode = entry.pop("scaleY", "none")
            if mode == "uniform":
                entry["uniform"] = True
            elif mode != "none":
                losses.append(f"IK {name!r} scaleY {mode!r} (4.2 has only uniform)")
        elif kind == "transform":
            unmapped[name] = _transform_down(entry, name, losses)
        elif kind == "path":
            entry["target"] = entry.pop("slot", None)
            if "mixY" not in entry and entry.get("mixX", 1) != 1:
                entry["mixY"] = entry["mixX"]  # 4.3's default; 4.2 would read 1
        elif kind == "physics":
            mode = entry.pop("scaleY", "none")
            if mode != "none":
                losses.append(f"physics {name!r} scaleY {mode!r} (4.2 has no scale mode)")
            _physics_defaults(entry, old=(0.5, 0.85), new=(1, 1))
        by_kind.setdefault(kind, []).append(entry)

    for skin_name, slot, name, att in _named_attachments(doc):
        where = f"{skin_name}/{slot}/{name}"
        if att.get("type") == "linkedmesh":
            if "source" in att:
                att["parent"] = att.pop("source")
            other = att.pop("slot", None)
            if other is not None and other != slot:
                losses.append(f"linked mesh {where} uses a source mesh in slot {other!r} (4.2 links within a slot)")
        elif att.get("type") == "clipping":
            for key in ("convex", "inverse"):
                if att.pop(key, False):
                    losses.append(f"clipping {where} {key} (4.2 has no {key} clipping)")

    for anim_name, anim in (doc.get("animations") or {}).items():
        anim = anim or {}
        if anim.pop("drawOrderFolder", None):
            losses.append(f"animation {anim_name!r} draw order folders (4.2 has none)")
        if anim.pop("slider", None) and not sliders:
            losses.append(f"animation {anim_name!r} slider timelines")
        for name, keys in (anim.get("transform") or {}).items():
            for k in _key_list(keys):
                values = _read_key(k, "4.3")
                for prop in unmapped.get(name, ()):
                    # 4.3 ignores an unmapped property's mix; 4.2 would apply it.
                    values[prop] = k[MIX[prop]] = 0
                    curve = k.get("curve")
                    if isinstance(curve, list) and len(curve) == 24:
                        ch = _CHANNEL[prop]
                        curve[4 * ch + 1] = curve[4 * ch + 3] = 0
                _write_key(k, values, "4.2")
        for timelines in (anim.get("physics") or {}).values():
            for k in _key_list((timelines or {}).get("mix")):
                k.setdefault("value", 1)  # 4.3's default; 4.2 reads 0

    if losses and not allow_lossy:
        raise LossyConversion(out.from_version, out.to_version, losses)
    out.lost.extend(losses)

    for skin in d.skins(doc):
        skin.pop("slider", None)
    doc.pop("constraints", None)
    _put_constraints(doc, None, by_kind)
    if by_kind:
        total = sum(len(v) for v in by_kind.values())
        out.changed.append(f"{total} constraints split into ik/transform/path/physics arrays with 'order'")
    for bone in d.bones(doc):
        for key in ("iconSize", "iconRotation"):
            if key in bone:
                bone.pop(key)
                out.dropped_editor_data.append(f"bone {bone.get('name')!r} {key}")


def _transform_down(c: dict, name: str, losses: list[str]) -> list[str]:
    """4.3 transform -> 4.2. Returns the properties 4.2 must leave unmoved
    (mix 0) because 4.3 did not map them."""
    c["target"] = c.pop("source", None)
    properties = c.pop("properties", None) or {}
    local_source, local_target = c.pop("localSource", False), c.pop("localTarget", False)
    additive, clamp = c.pop("additive", False), c.pop("clamp", False)
    mapped: set[str] = set()
    for source, spec in properties.items():
        spec = spec or {}
        for target, tspec in (spec.get("to") or {}).items():
            tspec = tspec or {}
            if (source == target and spec.get("offset", 0) == 0 and tspec.get("offset", 0) == 0
                    and tspec.get("scale", 1) == 1):
                mapped.add(target)
            else:
                losses.append(f"transform {name!r} maps {source} to {target}"
                              + (" with an offset or scale" if source == target else "")
                              + " (4.2 maps each property to itself)")
    if clamp:
        losses.append(f"transform {name!r} clamp (4.2 does not clamp)")
    if local_source != local_target:
        losses.append(f"transform {name!r} mixes local and world space (4.2 has one 'local' switch)")
    if local_source and local_target:
        c["local"] = True
    if additive:
        c["relative"] = True
    raw = {p: c.pop(MIX[p], None) for p in PROPERTIES}
    order = [p for p in PROPERTIES if p in mapped]
    effective: dict = {}
    for p in PROPERTIES:
        if p not in mapped:
            effective[p] = 0
        else:
            effective[p] = raw[p] if raw[p] is not None else _setup_default_43(p, order, effective)
    # Write each mix where 4.2's default (1; mixY = mixX; mixScaleY = mixScaleX) differs.
    defaults = {"rotate": 1, "x": 1, "y": effective["x"], "scaleX": 1, "scaleY": effective["scaleX"], "shearY": 1}
    for p in PROPERTIES:
        if effective[p] != defaults[p]:
            c[MIX[p]] = effective[p]
    return [p for p in PROPERTIES if p not in mapped]


# ── shared ──────────────────────────────────────────────────────────────────

def _physics_defaults(c: dict, old: tuple[float, float], new: tuple[float, float]) -> None:
    """Keep inertia and damping reading the same under the other version's defaults."""
    for key, was, now in zip(("inertia", "damping"), old, new):
        value = c.get(key, was)
        if value == now:
            c.pop(key, None)
        else:
            c[key] = value


def _put_constraints(doc: dict, constraints: list[dict] | None, by_kind: dict[str, list[dict]] | None = None) -> None:
    """Insert the constraint array(s) after `slots`, keeping the usual key order."""
    items = list(doc.items())
    doc.clear()
    placed = False
    for key, value in items:
        doc[key] = value
        if key == "slots":
            placed = True
            _add_constraints(doc, constraints, by_kind)
    if not placed:
        _add_constraints(doc, constraints, by_kind)


def _add_constraints(doc: dict, constraints, by_kind) -> None:
    if constraints:
        doc["constraints"] = constraints
    for kind in _KINDS_42:
        if by_kind and by_kind.get(kind):
            doc[kind] = by_kind[kind]


def _keyed(doc: dict) -> set[tuple[str, str]]:
    """(kind, constraint name) for every constraint some animation keys."""
    return {(kind, name) for anim in _animations(doc) for kind in d.CONSTRAINT_KINDS
            for name in (anim.get(kind) or {})}


def _animations(doc: dict) -> list[dict]:
    return [a for a in (doc.get("animations") or {}).values() if isinstance(a, dict)]


def _key_list(keys) -> list[dict]:
    return [k for k in keys if isinstance(k, dict)] if isinstance(keys, list) else []


def _named_attachments(doc: dict):
    for skin in d.skins(doc):
        for slot, atts in (skin.get("attachments") or {}).items():
            for name, att in (atts or {}).items():
                if isinstance(att, dict):
                    yield skin.get("name"), slot, name, att


def _attachments(doc: dict):
    for skin_name, slot, _, att in _named_attachments(doc):
        yield skin_name, slot, att


_STEPS = {("4.2", "4.3"): _upgrade_42_43, ("4.3", "4.2"): _downgrade_43_42}
