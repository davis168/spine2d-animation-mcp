"""Add and remove animations."""

from __future__ import annotations

from ..edits import EditError
from ..spine import document as d
from ..spine import versions
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
        expand_draw_order(doc, anim)

    wire = to_wire(anim, versions.layout(doc))
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


# ── draw order keys ─────────────────────────────────────────────────────────
#
# A draw order key stores offsets: for each slot that moves, how far from its
# setup position (in setup order). In 4.3 a draw order folder (see
# document.draw_order_folders) can have its own draw order keys, which reorder only
# its slots among the positions they hold; the timeline lists all the
# folder's slots, in setup order. The `order` shorthand gives the wanted
# back-to-front order instead, and is turned into offsets here.

NESTED_FOLDER_NOTE = ("the Spine 4.3 editor (4.3.26) drops a nested folder's draw order keys when it imports "
                      "JSON; the runtime plays them")

def draw_order_offsets(base: list[str], order: list[str], where: str = "order") -> list[dict]:
    """The offsets that turn `base` (setup order) into `order`."""
    if not isinstance(order, list) or sorted(map(str, order)) != sorted(base) or len(set(order)) != len(order):
        missing = [s for s in base if s not in (order or [])]
        unknown = [s for s in (order or []) if s not in base]
        raise EditError(f"{where}: order must list each of these slots once: {', '.join(base)}"
                        + (f" (missing {', '.join(missing)})" if missing else "")
                        + (f" (not here: {', '.join(map(str, unknown))})" if unknown else ""))
    return d.offsets_for(base, order)


draw_order_from_offsets = d.order_from


def _keys(keys, base: list[str], where: str) -> list[dict]:
    out = []
    for i, key in enumerate(keys or []):
        if not isinstance(key, dict):
            raise EditError(f"{where}[{i}]: a key must be an object ({{time, order}})")
        key = dict(key)
        if "order" in key:
            if "offsets" in key:
                raise EditError(f"{where}[{i}]: give order or offsets, not both")
            offsets = draw_order_offsets(base, key.pop("order"), f"{where}[{i}]")
            if offsets:  # a key without offsets is the setup order
                key["offsets"] = offsets
        out.append(key)
    return out


def _slot_names(doc: dict) -> list[str]:
    return [s["name"] for s in d.slots(doc)]


def _folder_list(folders: dict[str, list[str]]) -> str:
    return ", ".join(folders) or "none; rename_slot puts slots in one, e.g. head -> face/head"


def _folder_keys(doc: dict, spec, where: str) -> tuple[str, dict]:
    """One drawOrderFolder entry, {slots, keys}, from {folder, keys} or the
    folder's {slots, keys}; with the folder's name."""
    if versions.layout(doc) == "4.2":
        raise EditError("draw order folder keys need format version 4.3; this skeleton is 4.2 "
                        "(convert_skeleton converts it, or key the whole draw order instead)")
    if not isinstance(spec, dict) or ("folder" in spec) == ("slots" in spec):
        raise EditError(f"{where}: give {{folder, keys}}")
    names = _slot_names(doc)
    folders = d.draw_order_folders(names)
    keys = spec.get("keys")
    if "folder" in spec:
        name = spec["folder"]
        if name not in folders:
            raise EditError(f"{where}: no draw order folder {name!r} (folders: {_folder_list(folders)})")
    else:
        name = d.folder_of(names, spec["slots"])
        if name is None:
            raise EditError(f"{where}.slots: not the slots of one draw order folder, which the keys must reorder all "
                            f"of (folders: {_folder_list(folders)})")
        if spec["slots"] != folders[name] and any(isinstance(k, dict) and "offsets" in k for k in keys or []):
            raise EditError(f"{where}: raw offsets count from the folder's slots in setup order; list slots as "
                            f"{folders[name]}, or give each key an order instead")
    slots = folders[name]
    if len(slots) < 2:
        raise EditError(f"{where}: folder {name!r} holds one slot, so there is nothing to reorder")
    if not keys:
        raise EditError(f"{where}: give at least one key")
    return name, {"slots": slots, "keys": _keys(keys, slots, f"{where}.keys")}


def expand_draw_order(doc: dict, anim: dict) -> None:
    """Turn the shorthands in drawOrder and drawOrderFolder into the format:
    `order` into offsets, and folder names into the folder's slots."""
    if isinstance(anim.get("drawOrder"), list):
        anim["drawOrder"] = _keys(anim["drawOrder"], _slot_names(doc), "drawOrder")
    folders = anim.get("drawOrderFolder")
    if folders is None:
        return
    if isinstance(folders, dict):  # {folder: keys}
        folders = [{"folder": name, "keys": keys} for name, keys in folders.items()]
    if not isinstance(folders, list):
        raise EditError("drawOrderFolder: give a list of {folder, keys}, or map folder names to keys")
    named = [_folder_keys(doc, spec, f"drawOrderFolder[{i}]") for i, spec in enumerate(folders)]
    repeated = sorted({name for name, _ in named if [n for n, _ in named].count(name) > 1})
    if repeated:
        raise EditError(f"drawOrderFolder: folder {repeated[0]!r} keyed twice; give it one entry")
    anim["drawOrderFolder"] = [entry for _, entry in named]


def set_draw_order_keys(doc: dict, animation: str, keys: list[dict], folder: str | None = None) -> dict:
    """Set an animation's draw order keys: the whole draw order, or (4.3) one
    draw order folder's. No keys removes that timeline."""
    animations = doc.get("animations") or {}
    if animation not in animations:
        raise EditError(f"no animation {animation!r} (have {', '.join(animations) or 'none'}); "
                        "add_animation creates one, with drawOrder keys if wanted")
    anim = animations[animation]
    if folder is None:
        if not keys:
            anim.pop("drawOrder", None)
            return {"animation": animation, "removed": "drawOrder"}
        anim["drawOrder"] = _keys(keys, _slot_names(doc), "keys")
        return {"animation": animation, "timeline": "drawOrder", "keys": len(keys),
                "duration": round(d.animation_duration(anim), 4)}
    entries = anim.get("drawOrderFolder") or []
    held = set(d.draw_order_folders(_slot_names(doc)).get(folder) or [])
    at = next((i for i, e in enumerate(entries) if isinstance(e, dict) and set(e.get("slots") or []) == held), None)
    if not keys:
        if at is None:
            raise EditError(f"animation {animation!r} has no draw order keys for folder {folder!r}")
        del entries[at]
        if not entries:
            anim.pop("drawOrderFolder", None)
        return {"animation": animation, "removed": f"draw order keys of folder {folder!r}"}
    _, entry = _folder_keys(doc, {"folder": folder, "keys": keys}, "keys")
    if at is None:
        entries.append(entry)
    else:
        entries[at] = entry
    anim["drawOrderFolder"] = entries
    nested = "/" in (d.folder_of(_slot_names(doc), entry["slots"]) or "")  # not when the outer folder holds the same
    return {"animation": animation, "timeline": f"draw order folder {folder!r}", "slots": entry["slots"],
            "keys": len(keys), "duration": round(d.animation_duration(anim), 4),
            **({"note": NESTED_FOLDER_NOTE} if nested else {})}


def _rekey(keys: list, old: list[str], new: list[str], renamed: dict[str, str]) -> None:
    """Re-encode keys counted from `old` to count from `new`, showing the same
    order: renamed slots keep their place, removed ones drop out, added ones
    take their setup place."""
    for key in keys:
        if not isinstance(key, dict):
            continue
        try:
            order = draw_order_from_offsets(old, key.get("offsets") or [])
        except (KeyError, IndexError, TypeError):
            continue  # malformed; the checker reports it
        order = [s for s in (renamed.get(s, s) for s in order) if s in new]
        for s in new:
            if s not in order:
                order.insert(min(new.index(s), len(order)), s)
        offsets = draw_order_offsets(new, order)
        if offsets:
            key["offsets"] = offsets
        else:
            key.pop("offsets", None)


def keep_draw_order_keys(doc: dict, before: list[str], renamed: dict[str, str] | None = None,
                         folders_renamed: dict[str, str] | None = None) -> list[str]:
    """After slots were added, removed, renamed or reordered (`before` is the
    old draw order), re-encode every draw order key so it shows the same
    order as before; folder keys follow their folder. Returns a note for each
    folder's keys dropped because the folder is gone."""
    renamed, folders_renamed = renamed or {}, folders_renamed or {}
    after = _slot_names(doc)
    new_folders = d.draw_order_folders(after)
    notes = []
    for anim_name, anim in (doc.get("animations") or {}).items():
        anim = anim if isinstance(anim, dict) else {}
        if isinstance(anim.get("drawOrder"), list):
            _rekey(anim["drawOrder"], before, after, renamed)
        entries = anim.get("drawOrderFolder")
        if not isinstance(entries, list):
            continue
        kept = []
        for entry in entries:
            old = entry.get("slots") if isinstance(entry, dict) else None
            folder = d.folder_of(before, old)
            if folder is None or any(s not in before for s in old):
                kept.append(entry)  # not a folder's keys; the checker reports it
                continue
            new = new_folders.get(folders_renamed.get(folder, folder))
            if not new:
                notes.append(f"dropped the draw order keys of folder {folder!r} in {anim_name!r}: the folder is gone")
                continue
            _rekey(entry.get("keys") or [], old, new, renamed)
            entry["slots"] = new
            kept.append(entry)
        if kept:
            anim["drawOrderFolder"] = kept
        else:
            anim.pop("drawOrderFolder", None)
    return notes


def remove_animation(doc: dict, name: str) -> dict:
    animations = doc.get("animations") or {}
    if name not in animations:
        raise EditError(f"no animation {name!r} (have {', '.join(animations) or 'none'})")
    del animations[name]
    if not animations:
        doc.pop("animations", None)
    return {"removed": name}
