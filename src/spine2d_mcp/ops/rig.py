"""Edit a rig's setup pose: bones, pivots, slots and draw order.

Each operation is a dict with an "op" key, applied in order (see `edit_rig`).
Operations that would move art keep it in place where the docstring says so:
`move_pivot` compensates children, attachments and weighted vertices;
`set_bone` with a new parent and `set_slot` with a new bone keep world
positions. Bone reordering remaps weighted-mesh bone indices.
"""

from __future__ import annotations

from ..edits import EditError
from ..spine import document as d

_BONE_FIELDS = ("x", "y", "rotation", "scaleX", "scaleY", "shearX", "shearY", "length", "inherit", "color")
_BONE_DEFAULTS = {"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1, "shearX": 0, "shearY": 0,
                  "length": 0, "inherit": "normal"}
_SLOT_FIELDS = ("attachment", "color", "dark", "blend")
_VERTEX_TYPES = ("mesh", "boundingbox", "clipping", "path")


def _bone(doc: dict, name: str) -> dict:
    bone = d.bone_map(doc).get(name)
    if bone is None:
        raise EditError(f"no bone {name!r}")
    return bone


def _slot(doc: dict, name: str) -> dict:
    slot = d.slot_map(doc).get(name)
    if slot is None:
        raise EditError(f"no slot {name!r}")
    return slot


def _set_fields(target: dict, fields: dict, allowed, defaults) -> None:
    for key, value in fields.items():
        if key not in allowed:
            raise EditError(f"cannot set {key!r} (settable: {', '.join(allowed)})")
        if value is None or value == defaults.get(key, object()):
            target.pop(key, None)  # keep the file free of default-valued fields
        else:
            target[key] = value


def _require_normal_chain(doc: dict, name: str) -> None:
    _bone(doc, name)
    by_name = d.bone_map(doc)
    for b in [name, *d.ancestors(doc, name)]:
        if by_name[b].get("inherit", "normal") != "normal":
            raise EditError(f"bone {b!r} has inherit {by_name[b]['inherit']!r}; only 'normal' chains can be edited in world space")


def _set_local(target: dict, m: d.Matrix, keys: tuple[str, ...]) -> dict[str, float]:
    """Write the Spine fields of local matrix `m` onto a bone or attachment.
    `keys` lists the fields the target can hold; refuses a needed field it lacks."""
    fields = d.decompose(m)
    if "shearY" not in keys and abs(fields["shearY"]) > 0.01:
        raise EditError("the result would need a shear, which this attachment type cannot hold "
                        "(the two bones are scaled differently)")
    if "scaleX" not in keys and (abs(fields["scaleX"] - 1) > 1e-4 or abs(fields["scaleY"] - 1) > 1e-4):
        raise EditError("the result would need a scale, which this attachment type cannot hold")
    for key in keys:
        value = round(fields.get(key, 0.0), 4)
        default = 1 if key.startswith("scale") else 0
        if key == "shearX" or value == default:
            target.pop(key, None)
        else:
            target[key] = value
    return fields


def _vertex_attachments(doc: dict):
    """(slot name, attachment) for every attachment in every skin."""
    for skin in d.skins(doc):
        for slot, atts in (skin.get("attachments") or {}).items():
            for att in (atts or {}).values():
                yield slot, att


def _is_weighted(att: dict) -> bool:
    count = len(att["uvs"]) // 2 if "uvs" in att else att.get("vertexCount")
    return isinstance(count, int) and len(att.get("vertices") or []) != count * 2


def _weighted_influences(att: dict):
    """Yield the index into `vertices` of each influence's bone index."""
    vertices, i = att["vertices"], 0
    while i < len(vertices):
        n = int(vertices[i])
        for j in range(n):
            yield i + 1 + j * 4
        i += 1 + n * 4


def _reorder_bones(doc: dict, order: list[str]) -> None:
    """Put bones in `order` and remap weighted-mesh bone indices to match."""
    old = [b["name"] for b in d.bones(doc)]
    by_name = d.bone_map(doc)
    new_index = {name: i for i, name in enumerate(order)}
    remap = {i: new_index[name] for i, name in enumerate(old) if name in new_index}
    for _, att in _vertex_attachments(doc):
        if att.get("type") in _VERTEX_TYPES and "vertices" in att and _is_weighted(att):
            for k in _weighted_influences(att):
                index = int(att["vertices"][k])
                if index not in remap:
                    raise EditError(f"a weighted mesh uses bone {old[index]!r}, which this change removes")
                att["vertices"][k] = remap[index]
    doc["bones"] = [by_name[name] for name in order]


def _descendants(doc: dict, name: str) -> list[str]:
    children: dict[str, list[str]] = {}
    for b in d.bones(doc):
        children.setdefault(b.get("parent"), []).append(b["name"])
    out, stack = [], list(children.get(name, []))
    while stack:
        cur = stack.pop(0)
        out.append(cur)
        stack[0:0] = children.get(cur, [])
    return out


# ── operations ───────────────────────────────────────────────────────────────

def add_bone(doc: dict, name: str, parent: str, world: bool = False, **fields) -> dict:
    if name in d.bone_map(doc):
        raise EditError(f"bone {name!r} already exists")
    _bone(doc, parent)
    bone = {"name": name, "parent": parent}
    if world:
        _require_normal_chain(doc, parent)
        wx, wy = fields.pop("x", 0.0), fields.pop("y", 0.0)
        p = d.world_transforms(doc)[parent]
        fields["x"], fields["y"] = (round(v, 4) for v in p.inverse_vector(wx - p.x, wy - p.y))
    _set_fields(bone, fields, _BONE_FIELDS, _BONE_DEFAULTS)
    # Insert after the parent's subtree, so the list still reads as a hierarchy.
    order = [b["name"] for b in d.bones(doc)]
    subtree = [parent, *_descendants(doc, parent)]
    at = max(order.index(b) for b in subtree) + 1
    doc["bones"].append(bone)
    order.insert(at, name)
    _reorder_bones(doc, order)
    return {"op": "add_bone", "bone": name, "parent": parent, "x": bone.get("x", 0), "y": bone.get("y", 0)}


def set_bone(doc: dict, name: str, parent: str | None = None, **fields) -> dict:
    bone = _bone(doc, name)
    if parent is not None and parent != bone.get("parent"):
        _reparent(doc, name, parent)
    _set_fields(bone, fields, _BONE_FIELDS, _BONE_DEFAULTS)
    return {"op": "set_bone", "bone": name, **({"parent": parent} if parent else {}), **fields}


def _reparent(doc: dict, name: str, parent: str) -> None:
    """Move a bone under a new parent, keeping its world transform: the new
    local matrix is parent⁻¹ · world, decomposed into Spine's fields (so
    mirrored and unevenly scaled parents keep the art unchanged)."""
    _bone(doc, parent)
    if parent == name or parent in _descendants(doc, name):
        raise EditError(f"cannot parent {name!r} to its own descendant {parent!r}")
    _require_normal_chain(doc, name)
    _require_normal_chain(doc, parent)
    world = d.world_transforms(doc)
    me, new_parent = world[name], world[parent]
    bone = _bone(doc, name)
    lx, ly = new_parent.inverse_vector(me.x - new_parent.x, me.y - new_parent.y)
    bone["parent"] = parent
    bone["x"], bone["y"] = round(lx, 4), round(ly, 4)
    local = d.mat_mul(d.mat_inv(d.matrix(new_parent)), d.matrix(me))
    _set_local(bone, local, ("rotation", "scaleX", "scaleY", "shearX", "shearY"))
    # The parent must precede the bone: move the bone's subtree after the parent's.
    moving = [name, *_descendants(doc, name)]
    order = [b["name"] for b in d.bones(doc) if b["name"] not in moving]
    order[order.index(parent) + 1:order.index(parent) + 1] = moving
    _reorder_bones(doc, order)


def move_pivot(doc: dict, name: str, x: float = 0.0, y: float = 0.0, to: list[float] | None = None) -> dict:
    """Move a bone's pivot by a world-space delta (x, y), or to world point
    `to`, without moving its art: children, attachments on its slots and
    weighted influences are shifted back by the same amount."""
    _require_normal_chain(doc, name)
    world = d.world_transforms(doc)
    me = world[name]
    dx, dy = (to[0] - me.x, to[1] - me.y) if to is not None else (x, y)
    bone = _bone(doc, name)
    parent = world.get(bone.get("parent"))
    lx, ly = parent.inverse_vector(dx, dy) if parent else (dx, dy)
    bone["x"] = round(bone.get("x", 0) + lx, 4)
    bone["y"] = round(bone.get("y", 0) + ly, 4)
    bx, by = me.inverse_vector(dx, dy)  # the same shift in the bone's own axes
    for child in d.bones(doc):
        if child.get("parent") == name:
            child["x"] = round(child.get("x", 0) - bx, 4)
            child["y"] = round(child.get("y", 0) - by, 4)
    slots_on_bone = {s["name"] for s in d.slots(doc) if s.get("bone") == name}
    bone_index = [b["name"] for b in d.bones(doc)].index(name)
    for slot, att in _vertex_attachments(doc):
        kind = att.get("type", "region")
        if kind in ("region", "point") and slot in slots_on_bone:
            att["x"] = round(att.get("x", 0) - bx, 4)
            att["y"] = round(att.get("y", 0) - by, 4)
        elif kind in _VERTEX_TYPES and "vertices" in att:
            if _is_weighted(att):
                for k in _weighted_influences(att):
                    if int(att["vertices"][k]) == bone_index:
                        att["vertices"][k + 1] = round(att["vertices"][k + 1] - bx, 4)
                        att["vertices"][k + 2] = round(att["vertices"][k + 2] - by, 4)
            elif slot in slots_on_bone:
                v = att["vertices"]
                for i in range(0, len(v), 2):
                    v[i], v[i + 1] = round(v[i] - bx, 4), round(v[i + 1] - by, 4)
    return {"op": "move_pivot", "bone": name, "world_delta": [round(dx, 4), round(dy, 4)],
            "world": [round(me.x + dx, 4), round(me.y + dy, 4)]}


def rename_bone(doc: dict, name: str, to: str) -> dict:
    _bone(doc, name)
    if to in d.bone_map(doc):
        raise EditError(f"bone {to!r} already exists")
    for b in d.bones(doc):
        if b["name"] == name:
            b["name"] = to
        if b.get("parent") == name:
            b["parent"] = to
    for s in d.slots(doc):
        if s.get("bone") == name:
            s["bone"] = to
    for kind, c in d.all_constraints(doc):
        if c.get("bone") == name:  # physics, slider
            c["bone"] = to
        key = d.target_bone_key(doc, kind)  # a path constraint follows a slot, not a bone
        if key and c.get(key) == name:
            c[key] = to
        if "bones" in c:
            c["bones"] = [to if b == name else b for b in c["bones"]]
    for skin in d.skins(doc):
        if "bones" in skin:
            skin["bones"] = [to if b == name else b for b in skin["bones"]]
    for anim in (doc.get("animations") or {}).values():
        bones = (anim or {}).get("bones") or {}
        if name in bones:
            anim["bones"] = {to if b == name else b: t for b, t in bones.items()}
    return {"op": "rename_bone", "from": name, "to": to}


def remove_bone(doc: dict, name: str) -> dict:
    """Remove a bone nothing depends on (no children, slots or constraints);
    its animation timelines go with it."""
    _bone(doc, name)
    users = [f"bone {b['name']}" for b in d.bones(doc) if b.get("parent") == name]
    users += [f"slot {s['name']}" for s in d.slots(doc) if s.get("bone") == name]
    users += [f"{k} {c['name']}" for k, c in d.all_constraints(doc)
              if c.get("bone") == name or name in (c.get("bones") or [])
              or (d.target_bone_key(doc, k) and c.get(d.target_bone_key(doc, k)) == name)]
    if users:
        raise EditError(f"bone {name!r} is used by {', '.join(users)}")
    _reorder_bones(doc, [b["name"] for b in d.bones(doc) if b["name"] != name])
    for skin in d.skins(doc):
        if name in (skin.get("bones") or []):
            skin["bones"].remove(name)
    for anim in (doc.get("animations") or {}).values():
        ((anim or {}).get("bones") or {}).pop(name, None)
    return {"op": "remove_bone", "bone": name}


def add_slot(doc: dict, name: str, bone: str, attachment: str | None = None, index: int | None = None, **fields) -> dict:
    if name in d.slot_map(doc):
        raise EditError(f"slot {name!r} already exists")
    _bone(doc, bone)
    slot = {"name": name, "bone": bone}
    _set_fields(slot, {"attachment": attachment, **fields}, _SLOT_FIELDS, {"blend": "normal"})
    slots = doc.setdefault("slots", [])
    slots.insert(len(slots) if index is None else index, slot)
    return {"op": "add_slot", "slot": name, "bone": bone, "draw_index": slots.index(slot),
            **_draw_order_note(doc)}


def set_slot(doc: dict, name: str, bone: str | None = None, **fields) -> dict:
    slot = _slot(doc, name)
    if bone is not None and bone != slot.get("bone"):
        _rebind_slot(doc, name, bone)
    _set_fields(slot, fields, _SLOT_FIELDS, {"blend": "normal"})
    return {"op": "set_slot", "slot": name, **({"bone": bone} if bone else {}), **fields}


def _rebind_slot(doc: dict, name: str, bone: str) -> None:
    """Move a slot to another bone, keeping its attachments where they are
    (new local = new bone⁻¹ · old bone · old local, for mirrored or unevenly
    scaled bones too)."""
    slot = _slot(doc, name)
    _bone(doc, bone)
    _require_normal_chain(doc, slot["bone"])
    _require_normal_chain(doc, bone)
    world = d.world_transforms(doc)
    old, new = world[slot["bone"]], world[bone]
    to_new = d.mat_mul(d.mat_inv(d.matrix(new)), d.matrix(old))

    def remap(x: float, y: float) -> tuple[float, float]:
        wx, wy = old.apply(x, y)
        return tuple(round(v, 4) for v in new.inverse_vector(wx - new.x, wy - new.y))

    for skin in d.skins(doc):
        for att_name, att in (((skin.get("attachments") or {}).get(name)) or {}).items():
            kind = att.get("type", "region")
            if kind in ("region", "point"):
                att["x"], att["y"] = remap(att.get("x", 0), att.get("y", 0))
                local = d.local_matrix({k: att[k] for k in ("rotation", "scaleX", "scaleY") if k in att})
                keys = ("rotation", "scaleX", "scaleY") if kind == "region" else ("rotation",)
                try:
                    _set_local(att, d.mat_mul(to_new, local), keys)
                except EditError as e:
                    raise EditError(f"attachment {att_name!r}: {e}") from None
            elif kind in _VERTEX_TYPES and "vertices" in att and not _is_weighted(att):
                v = att["vertices"]
                for i in range(0, len(v), 2):
                    v[i], v[i + 1] = remap(v[i], v[i + 1])
    slot["bone"] = bone


def remove_slot(doc: dict, name: str) -> dict:
    _slot(doc, name)
    slot_key = d.path_slot_key(doc)
    users = [f"path constraint {c['name']}" for c in d.constraints(doc, "path") if c.get(slot_key) == name]
    users += [f"clipping {skin.get('name')}/{slot}/{att_name}"
              for skin in d.skins(doc) for slot, atts in (skin.get("attachments") or {}).items()
              for att_name, att in (atts or {}).items() if (att or {}).get("end") == name]
    users += [f"linked mesh {skin.get('name')}/{slot}/{att_name}"  # 4.3: a source mesh in another slot
              for skin in d.skins(doc) for slot, atts in (skin.get("attachments") or {}).items()
              for att_name, att in (atts or {}).items()
              if (att or {}).get("type") == "linkedmesh" and att.get("slot") == name and slot != name]
    users += [f"drawOrder keys of {anim_name}" for anim_name, anim in (doc.get("animations") or {}).items()
              if any(o.get("slot") == name for k in (anim or {}).get("drawOrder") or [] for o in k.get("offsets") or [])]
    users += [f"draw order folder {folder} of {anim_name}" for anim_name, anim in (doc.get("animations") or {}).items()
              for folder, spec in ((anim or {}).get("drawOrderFolder") or {}).items()
              if name in ((spec or {}).get("slots") or [])]
    if users:
        raise EditError(f"slot {name!r} is still used by {', '.join(users)}; change those first")
    doc["slots"] = [s for s in d.slots(doc) if s["name"] != name]
    for skin in d.skins(doc):
        (skin.get("attachments") or {}).pop(name, None)
    for anim in (doc.get("animations") or {}).values():
        ((anim or {}).get("slots") or {}).pop(name, None)
        for skin_atts in ((anim or {}).get("attachments") or {}).values():
            (skin_atts or {}).pop(name, None)
    return {"op": "remove_slot", "slot": name, **_draw_order_note(doc)}


def set_draw_order(doc: dict, order: list[str] | None = None, slot: str | None = None, index: int | None = None) -> dict:
    """Reorder slots (first drawn first, at the back): give the full `order`,
    or move one `slot` to `index`."""
    names = [s["name"] for s in d.slots(doc)]
    if order is not None:
        if sorted(order) != sorted(names):
            missing, extra = set(names) - set(order), set(order) - set(names)
            raise EditError(f"order must list every slot once (missing {sorted(missing)}, unknown {sorted(extra)})")
    elif slot is not None and index is not None:
        _slot(doc, slot)
        order = [n for n in names if n != slot]
        order.insert(max(0, min(index, len(order))), slot)
    else:
        raise EditError("give order, or slot and index")
    by_name = d.slot_map(doc)
    doc["slots"] = [by_name[n] for n in order]
    return {"op": "set_draw_order", "order": order, **_draw_order_note(doc)}


def _draw_order_note(doc: dict) -> dict:
    keyed = [n for n, a in (doc.get("animations") or {}).items()
             if (a or {}).get("drawOrder") or (a or {}).get("drawOrderFolder")]
    if not keyed:
        return {}
    return {"note": f"draw order keys in {', '.join(keyed)} are offsets from the setup order; re-check them"}


_OPS = {
    "add_bone": add_bone, "set_bone": set_bone, "move_pivot": move_pivot, "rename_bone": rename_bone,
    "remove_bone": remove_bone, "add_slot": add_slot, "set_slot": set_slot, "remove_slot": remove_slot,
    "set_draw_order": set_draw_order,
}
OP_NAMES = tuple(_OPS)


def edit_rig(doc: dict, operations: list[dict]) -> list[dict]:
    if not operations:
        raise EditError("no operations given")
    done = []
    for i, op in enumerate(operations):
        op = dict(op)
        kind = op.pop("op", None)
        if kind not in _OPS:
            raise EditError(f"operation {i}: unknown op {kind!r} (have {', '.join(OP_NAMES)})")
        try:
            done.append(_OPS[kind](doc, **op))
        except TypeError as e:
            raise EditError(f"operation {i} ({kind}): {e}") from None
        except EditError as e:
            raise EditError(f"operation {i} ({kind}): {e}") from None
    return done
