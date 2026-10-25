"""Edit a rig's setup pose: bones, pivots, slots, draw order and event
definitions.

Each operation is a dict with an "op" key, applied in order (see `edit_rig`).
Operations that would move art keep it in place where the docstring says so:
`move_pivot` compensates children, attachments and weighted vertices;
`set_bone` with a new parent and `set_slot` with a new bone keep world
positions. Bone reordering remaps weighted-mesh bone indices.
"""

from __future__ import annotations

from ..edits import EditError
from ..spine import document as d
from ..spine import versions
from .animation import keep_draw_order_keys

# icon, iconSize, iconRotation and visible are how the editor shows a bone
# (nonessential data); iconSize and iconRotation are 4.3 only.
_BONE_FIELDS = ("x", "y", "rotation", "scaleX", "scaleY", "shearX", "shearY", "length", "inherit", "color",
                "icon", "iconSize", "iconRotation", "visible")
_BONE_DEFAULTS = {"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1, "shearX": 0, "shearY": 0,
                  "length": 0, "inherit": "normal", "iconSize": 1, "iconRotation": 0, "visible": True}
_ONLY_43 = ("iconSize", "iconRotation")
_SLOT_FIELDS = ("attachment", "color", "dark", "blend", "visible")
_SLOT_DEFAULTS = {"blend": "normal", "visible": True}
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


def _set_fields(target: dict, fields: dict, allowed, defaults, doc: dict | None = None) -> None:
    if doc is not None and versions.layout(doc) == "4.2":
        for key in _ONLY_43:
            if fields.get(key, _BONE_DEFAULTS[key]) != _BONE_DEFAULTS[key]:
                raise EditError(f"{key} needs format version 4.3; this skeleton is 4.2")
    for key, value in fields.items():
        if key not in allowed:
            raise EditError(f"cannot set {key!r} (settable: {', '.join(allowed)})")
        if value is None or value == defaults.get(key, object()):
            target.pop(key, None)  # keep the file free of default-valued fields
        else:
            target[key] = value


def require_normal_chain(doc: dict, name: str) -> None:
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


def weighted_influences(att: dict):
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
            for k in weighted_influences(att):
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
        require_normal_chain(doc, parent)
        wx, wy = fields.pop("x", 0.0), fields.pop("y", 0.0)
        p = d.world_transforms(doc)[parent]
        fields["x"], fields["y"] = (round(v, 4) for v in p.inverse_vector(wx - p.x, wy - p.y))
    _set_fields(bone, fields, _BONE_FIELDS, _BONE_DEFAULTS, doc)
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
    _set_fields(bone, fields, _BONE_FIELDS, _BONE_DEFAULTS, doc)
    return {"op": "set_bone", "bone": name, **({"parent": parent} if parent else {}), **fields}


def _reparent(doc: dict, name: str, parent: str) -> None:
    """Move a bone under a new parent, keeping its world transform: the new
    local matrix is parent⁻¹ · world, decomposed into Spine's fields (so
    mirrored and unevenly scaled parents keep the art unchanged)."""
    _bone(doc, parent)
    if parent == name or parent in _descendants(doc, name):
        raise EditError(f"cannot parent {name!r} to its own descendant {parent!r}")
    require_normal_chain(doc, name)
    require_normal_chain(doc, parent)
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
    require_normal_chain(doc, name)
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
                for k in weighted_influences(att):
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


def _slot_names(doc: dict) -> list[str]:
    return [s["name"] for s in d.slots(doc)]


def _check_slot_name(doc: dict, name) -> None:
    if not isinstance(name, str) or not name or any(not part for part in name.split("/")):
        raise EditError(f"bad slot name {name!r} (a '/' puts it in a draw order folder: folder/name)")
    if name in d.slot_map(doc):
        raise EditError(f"slot {name!r} already exists")


def _keep_folders_together(before: list[str], after: list[str]) -> None:
    """The editor pulls a folder's slots together when it imports JSON, which
    changes the draw order; refuse a change that splits a folder."""
    split = [f for f in d.split_folders(after) if f not in d.split_folders(before)]
    if split:
        held = d.draw_order_folders(after)[split[0]]
        raise EditError(f"draw order folder {split[0]!r} ({', '.join(held)}) would no longer be together in the draw "
                        "order; keep a folder's slots next to each other (rename_slot moves a slot in or out "
                        "of a folder)")


def _kept_keys(doc: dict, before: list[str], renamed: dict | None = None, folders_renamed: dict | None = None) -> dict:
    notes = keep_draw_order_keys(doc, before, renamed, folders_renamed)
    return {"notes": notes} if notes else {}


def add_slot(doc: dict, name: str, bone: str, attachment: str | None = None, index: int | None = None, **fields) -> dict:
    _check_slot_name(doc, name)
    _bone(doc, bone)
    before = _slot_names(doc)
    slot = {"name": name, "bone": bone}
    _set_fields(slot, {"attachment": attachment, **fields}, _SLOT_FIELDS, _SLOT_DEFAULTS)
    at = len(before) if index is None else index
    _keep_folders_together(before, before[:at] + [name] + before[at:])
    slots = doc.setdefault("slots", [])
    slots.insert(at, slot)
    return {"op": "add_slot", "slot": name, "bone": bone, "draw_index": slots.index(slot),
            **_kept_keys(doc, before)}


def set_slot(doc: dict, name: str, bone: str | None = None, **fields) -> dict:
    slot = _slot(doc, name)
    if bone is not None and bone != slot.get("bone"):
        _rebind_slot(doc, name, bone)
    _set_fields(slot, fields, _SLOT_FIELDS, _SLOT_DEFAULTS)
    return {"op": "set_slot", "slot": name, **({"bone": bone} if bone else {}), **fields}


def _rebind_slot(doc: dict, name: str, bone: str) -> None:
    """Move a slot to another bone, keeping its attachments where they are
    (new local = new bone⁻¹ · old bone · old local, for mirrored or unevenly
    scaled bones too)."""
    slot = _slot(doc, name)
    _bone(doc, bone)
    require_normal_chain(doc, slot["bone"])
    require_normal_chain(doc, bone)
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
    if users:
        raise EditError(f"slot {name!r} is still used by {', '.join(users)}; change those first")
    before = _slot_names(doc)
    doc["slots"] = [s for s in d.slots(doc) if s["name"] != name]
    for skin in d.skins(doc):
        (skin.get("attachments") or {}).pop(name, None)
    for anim in (doc.get("animations") or {}).values():
        ((anim or {}).get("slots") or {}).pop(name, None)
        for skin_atts in ((anim or {}).get("attachments") or {}).values():
            (skin_atts or {}).pop(name, None)
    return {"op": "remove_slot", "slot": name, **_kept_keys(doc, before)}


def _renamed_keys(mapping, renamed: dict[str, str]):
    """`mapping` with its keys renamed at once, each keeping its place."""
    if not isinstance(mapping, dict):
        return mapping
    return {renamed.get(k, k): v for k, v in mapping.items()}


def _rename_slots(doc: dict, renamed: dict[str, str]) -> None:
    """Rename slots everywhere the format names them."""
    slot_key, source_key = d.path_slot_key(doc), d.linked_mesh_key(doc)
    for s in d.slots(doc):
        s["name"] = renamed.get(s["name"], s["name"])
    for c in d.constraints(doc, "path"):
        if c.get(slot_key) in renamed:
            c[slot_key] = renamed[c[slot_key]]
    for skin in d.skins(doc):
        if "attachments" in skin:
            skin["attachments"] = _renamed_keys(skin["attachments"], renamed)
        for atts in (skin.get("attachments") or {}).values():
            for att in (atts or {}).values():
                if not isinstance(att, dict):
                    continue
                if att.get("type") == "clipping" and att.get("end") in renamed:
                    att["end"] = renamed[att["end"]]
                if att.get("type") == "linkedmesh" and source_key == "source" and att.get("slot") in renamed:
                    att["slot"] = renamed[att["slot"]]  # 4.3: the source mesh's slot
    for anim in (doc.get("animations") or {}).values():
        if not isinstance(anim, dict):
            continue
        if "slots" in anim:
            anim["slots"] = _renamed_keys(anim["slots"], renamed)
        for skin_name, slots in list((anim.get("attachments") or {}).items()):
            anim["attachments"][skin_name] = _renamed_keys(slots, renamed)
    # draw order keys are renamed by keep_draw_order_keys


def rename_slot(doc: dict, name: str, to: str) -> dict:
    """Rename a slot everywhere. A '/' puts it in a draw order folder (head ->
    face/head); without one it leaves its folder."""
    _slot(doc, name)
    _check_slot_name(doc, to)
    before = _slot_names(doc)
    _keep_folders_together(before, [to if n == name else n for n in before])
    _rename_slots(doc, {name: to})
    return {"op": "rename_slot", "slot": name, "to": to, **_kept_keys(doc, before, {name: to}),
            "note": "game code that looks the slot up by name needs the new name"}


def rename_folder(doc: dict, name: str, to: str) -> dict:
    """Rename a draw order folder (renaming its slots), moving its draw order keys
    along; to='' takes its slots out of it, into its parent folder."""
    names = _slot_names(doc)
    folders = d.draw_order_folders(names)
    if name not in folders:
        raise EditError(f"no draw order folder {name!r} (have {', '.join(folders) or 'none'})")
    if to and any(not part for part in to.split("/")):
        raise EditError(f"bad folder name {to!r}")

    def moved(path: str) -> str:
        rest = path[len(name) + 1:]
        return f"{to}/{rest}" if to else rest

    renamed = {s: moved(s) for s in folders[name]}
    taken = set(names) - set(renamed)
    clash = [n for n in renamed.values() if n in taken]
    if clash:
        raise EditError(f"slot {clash[0]!r} already exists")
    if not to and any(n in folders for n in renamed.values()):
        raise EditError(f"a slot of {name!r} would get the name of a folder")
    folders_renamed = {f: (f"{to}{f[len(name):]}" if to else f[len(name) + 1:]) for f in folders
                       if f == name or f.startswith(name + "/")}
    _keep_folders_together(names, [renamed.get(n, n) for n in names])
    _rename_slots(doc, renamed)
    return {"op": "rename_folder", "folder": name, "to": to, "slots": renamed,
            **_kept_keys(doc, names, renamed, folders_renamed),
            "note": "game code that looks these slots up by name needs the new names"}


def set_draw_order(doc: dict, order: list[str] | None = None, slot: str | None = None, index: int | None = None) -> dict:
    """Reorder slots (first drawn first, at the back): give the full `order`,
    or move one `slot` to `index`. Draw order keys keep showing what they did."""
    names = _slot_names(doc)
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
    _keep_folders_together(names, order)
    by_name = d.slot_map(doc)
    doc["slots"] = [by_name[n] for n in order]
    return {"op": "set_draw_order", "order": order, **_kept_keys(doc, names)}


# The skeleton header's settable fields: referenceScale (physics distances
# are relative to it), the editor's fps and its images and audio folders.
_HEADER_DEFAULTS = {"referenceScale": 100, "fps": 30, "images": None, "audio": None}


def set_skeleton(doc: dict, **fields) -> dict:
    """Set skeleton header fields (referenceScale, fps, images, audio)."""
    unknown = [k for k in fields if k not in _HEADER_DEFAULTS]
    if unknown:
        raise EditError(f"the skeleton header has no settable {', '.join(map(repr, unknown))} "
                        f"(have {', '.join(_HEADER_DEFAULTS)})")
    for key in ("referenceScale", "fps"):
        if key in fields and (not isinstance(fields[key], (int, float)) or fields[key] <= 0):
            raise EditError(f"{key} must be a number above 0")
    header = doc.setdefault("skeleton", {})
    _set_fields(header, fields, tuple(_HEADER_DEFAULTS), _HEADER_DEFAULTS)
    return {"op": "set_skeleton", **{k: header[k] for k in _HEADER_DEFAULTS if k in header}}


_EVENT_DEFAULTS = {"int": 0, "float": 0, "string": "", "audio": None, "volume": 1, "balance": 0}


def set_event(doc: dict, name: str, **fields) -> dict:
    """Define an event or change its defaults (int, float, string) and its
    sound (audio: a path in the skeleton's audio folder, volume 0..1,
    balance -1..1); keys of the event carry these unless they override them."""
    if not isinstance(name, str) or not name:
        raise EditError("an event needs a name")
    unknown = [k for k in fields if k not in _EVENT_DEFAULTS]
    if unknown:
        raise EditError(f"events have no {', '.join(map(repr, unknown))} (have {', '.join(_EVENT_DEFAULTS)})")
    event = doc.setdefault("events", {}).setdefault(name, {})
    _set_fields(event, fields, tuple(_EVENT_DEFAULTS), _EVENT_DEFAULTS)
    if "audio" not in event:
        for key in ("volume", "balance"):
            if key in event:
                raise EditError(f"{key} needs audio (the runtime reads it only for events with a sound)")
    return {"op": "set_event", "event": name, **event}


def remove_event(doc: dict, name: str) -> dict:
    if name not in (doc.get("events") or {}):
        raise EditError(f"no event {name!r}")
    keyed = [a for a, anim in (doc.get("animations") or {}).items()
             if any(k.get("name") == name for k in (anim or {}).get("events") or [])]
    if keyed:
        raise EditError(f"event {name!r} is keyed in {', '.join(keyed)}; remove those keys first")
    del doc["events"][name]
    if not doc["events"]:
        del doc["events"]
    return {"op": "remove_event", "event": name}


_OPS = {
    "set_skeleton": set_skeleton, "set_event": set_event, "remove_event": remove_event,
    "add_bone": add_bone, "set_bone": set_bone, "move_pivot": move_pivot, "rename_bone": rename_bone,
    "remove_bone": remove_bone, "add_slot": add_slot, "set_slot": set_slot, "rename_slot": rename_slot,
    "rename_folder": rename_folder, "remove_slot": remove_slot, "set_draw_order": set_draw_order,
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
