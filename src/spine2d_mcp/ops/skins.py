"""Skins: sets of attachments, plus the bones and constraints that are
active only while the skin is (skin bones and skin constraints).

A skin bone or constraint carries `skin: true` and is listed by the skins
that use it; the runtime leaves it inactive unless the current skin lists
it. Each operation is a dict with an "op" key, applied in order.
"""

from __future__ import annotations

import copy

from ..edits import EditError
from ..spine import document as d


def _skin(doc: dict, name: str) -> dict:
    skin = d.find_skin(doc, name)
    if skin is None:
        raise EditError(f"no skin {name!r} (have {', '.join(s.get('name') for s in d.skins(doc)) or 'none'})")
    return skin


def _linked_users(doc: dict, skin: str) -> list[str]:
    """Linked meshes whose source mesh is in `skin`."""
    return [f"{s.get('name')}/{slot}/{name}" for s in d.skins(doc)
            for slot, atts in (s.get("attachments") or {}).items() for name, att in (atts or {}).items()
            if isinstance(att, dict) and att.get("type") == "linkedmesh"
            and att.get("skin", "default") == skin and s.get("name") != skin]


def add_skin(doc: dict, name: str, copy_from: str | None = None, color: str | None = None) -> dict:
    """A new skin, empty or a copy of another (its attachments, bones and
    constraints); linked meshes copied from it keep their source skin."""
    if not isinstance(name, str) or not name:
        raise EditError("a skin needs a name")
    if d.find_skin(doc, name) is not None:
        raise EditError(f"skin {name!r} already exists")
    skin: dict = copy.deepcopy(_skin(doc, copy_from)) if copy_from else {"attachments": {}}
    skin["name"] = name
    if color:
        skin["color"] = color
    for atts in (skin.get("attachments") or {}).values():
        for att in (atts or {}).values():
            if isinstance(att, dict) and att.get("type") == "linkedmesh" and "skin" not in att and copy_from:
                att["skin"] = copy_from  # it linked to its own skin's mesh, which now lives in copy_from
    doc.setdefault("skins", []).append({"name": skin.pop("name"), **skin})
    return {"op": "add_skin", "skin": name, **({"copied_from": copy_from} if copy_from else {})}


def rename_skin(doc: dict, name: str, to: str) -> dict:
    skin = _skin(doc, name)
    if d.find_skin(doc, to) is not None:
        raise EditError(f"skin {to!r} already exists")
    if name == "default":
        raise EditError("the default skin is what slots show without a skin set; add a new skin instead "
                        "(add_skin copy_from='default')")
    skin["name"] = to
    for s in d.skins(doc):
        for atts in (s.get("attachments") or {}).values():
            for att in (atts or {}).values():
                if isinstance(att, dict) and att.get("type") == "linkedmesh" and att.get("skin") == name:
                    att["skin"] = to
    for anim in (doc.get("animations") or {}).values():
        timelines = (anim or {}).get("attachments")
        if isinstance(timelines, dict) and name in timelines:
            anim["attachments"] = {to if k == name else k: v for k, v in timelines.items()}
    return {"op": "rename_skin", "skin": name, "to": to, "note": "game code that sets the skin by name needs the new name"}


def remove_skin(doc: dict, name: str) -> dict:
    """Remove a skin and its deform and sequence keys."""
    _skin(doc, name)
    users = _linked_users(doc, name)
    if users:
        raise EditError(f"linked meshes use source meshes in skin {name!r}: {', '.join(users)}; change those first")
    doc["skins"] = [s for s in d.skins(doc) if s.get("name") != name]
    keyed = []
    for anim_name, anim in (doc.get("animations") or {}).items():
        if ((anim or {}).get("attachments") or {}).pop(name, None) is not None:
            keyed.append(anim_name)
            if not anim["attachments"]:
                del anim["attachments"]
    freed = _unflag(doc)
    return {"op": "remove_skin", "skin": name, **({"removed_keys_in": keyed} if keyed else {}),
            **({"active_for_every_skin": freed} if freed else {})}


def _constraint_kind(doc: dict, name: str) -> tuple[str, dict]:
    for kind, c in d.all_constraints(doc):
        if c.get("name") == name:
            return kind, c
    raise EditError(f"no constraint {name!r}")


def add_to_skin(doc: dict, skin: str, bones: list[str] | None = None, constraints: list[str] | None = None) -> dict:
    """Make bones and constraints skin-only: active while `skin` (or another
    skin listing them) is the current skin."""
    entry = _skin(doc, skin)
    by_name = d.bone_map(doc)
    for b in bones or []:
        if b not in by_name:
            raise EditError(f"no bone {b!r}")
    kinds = [_constraint_kind(doc, c) for c in constraints or []]
    for b in bones or []:
        by_name[b]["skin"] = True
        if b not in entry.setdefault("bones", []):
            entry["bones"].append(b)
    for (kind, c), name in zip(kinds, constraints or []):
        c["skin"] = True
        if name not in entry.setdefault(kind, []):
            entry[kind].append(name)
    for key in ["bones", *{k for k, _ in kinds}]:
        if not entry.get(key):
            entry.pop(key, None)
    loose = sorted({c for b in bones or [] for c in _children(doc, b)} - set(entry.get("bones") or []))
    return {"op": "add_to_skin", "skin": skin, "bones": bones or [], "constraints": constraints or [],
            **({"note": f"child bones {', '.join(loose)} are not in the skin, so they stay active without "
                        "their parent; add them too if they belong to it"} if loose else {})}


def _children(doc: dict, bone: str) -> list[str]:
    return [b["name"] for b in d.bones(doc) if b.get("parent") == bone]


def remove_from_skin(doc: dict, skin: str, bones: list[str] | None = None,
                     constraints: list[str] | None = None) -> dict:
    """Take bones and constraints out of a skin; one no skin lists any more
    is active again for every skin."""
    entry = _skin(doc, skin)
    for b in bones or []:
        if b not in (entry.get("bones") or []):
            raise EditError(f"bone {b!r} is not in skin {skin!r}")
        entry["bones"].remove(b)
    for name in constraints or []:
        kind, _ = _constraint_kind(doc, name)
        if name not in (entry.get(kind) or []):
            raise EditError(f"constraint {name!r} is not in skin {skin!r}")
        entry[kind].remove(name)
    for key in ("bones", *d.CONSTRAINT_KINDS):
        if key in entry and not entry[key]:
            del entry[key]
    freed = _unflag(doc)
    return {"op": "remove_from_skin", "skin": skin, **({"active_for_every_skin": freed} if freed else {})}


def _unflag(doc: dict) -> list[str]:
    """Clear `skin` on bones and constraints no skin lists."""
    listed = {n for s in d.skins(doc) for key in ("bones", *d.CONSTRAINT_KINDS) for n in s.get(key) or []}
    freed = []
    for item in [*d.bones(doc), *(c for _, c in d.all_constraints(doc))]:
        if item.get("skin") and item.get("name") not in listed:
            del item["skin"]
            freed.append(item.get("name"))
    return freed


_OPS = {"add_skin": add_skin, "rename_skin": rename_skin, "remove_skin": remove_skin,
        "add_to_skin": add_to_skin, "remove_from_skin": remove_from_skin}
OP_NAMES = tuple(_OPS)


def edit_skins(doc: dict, operations: list[dict]) -> list[dict]:
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
