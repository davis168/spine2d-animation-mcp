"""Spine skeleton JSON (format 4.2 or 4.3) as a plain dict: load, save, look
up, measure.

The editing tools work on the raw document rather than the Pydantic models in
`schema.py`, so a skeleton exported by the Spine editor keeps every field this
package does not model (meshes, paths, sequences, nonessential data) through a
round trip. `schema.py` stays the strict model the PSD importer builds with.

The two format versions lay constraints out differently: 4.2 has one
top-level array per type (`ik`, `transform`, `path`, `physics`), each entry
with an `order`; 4.3 has one `constraints` array whose entries carry a `type`
and apply in array order. The helpers below read either layout (chosen by
`versions.layout`), so callers name constraint kinds, not arrays.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import versions

# Every constraint kind any supported format version has.
CONSTRAINT_KINDS = ("ik", "transform", "path", "physics", "slider")


def load(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: not a Spine skeleton JSON object")
    return doc


def save(doc: dict, path: str | Path) -> Path:
    """Write atomically, so a failed write never leaves a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def _dicts(value) -> list[dict]:
    """Entries of a list that are objects; malformed ones are the checker's to report."""
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def bones(doc: dict) -> list[dict]:
    return _dicts(doc.get("bones"))


def bone_map(doc: dict) -> dict[str, dict]:
    return {b.get("name"): b for b in bones(doc)}


def slots(doc: dict) -> list[dict]:
    return _dicts(doc.get("slots"))


def slot_map(doc: dict) -> dict[str, dict]:
    return {s.get("name"): s for s in slots(doc)}


def skins(doc: dict) -> list[dict]:
    return _dicts(doc.get("skins"))


def find_skin(doc: dict, name: str) -> dict | None:
    return next((s for s in skins(doc) if s.get("name") == name), None)


def attachment_names(doc: dict, slot: str) -> set[str]:
    """Every attachment name any skin defines for the slot."""
    names: set[str] = set()
    for skin in skins(doc):
        atts = skin.get("attachments")
        named = atts.get(slot) if isinstance(atts, dict) else None
        if isinstance(named, dict):
            names.update(named)
    return names


def kinds(doc: dict) -> tuple[str, ...]:
    """The constraint kinds the document's format version has."""
    return versions.FORMATS[versions.layout(doc)].constraint_kinds


def all_constraints(doc: dict) -> list[tuple[str, dict]]:
    """(kind, entry) for every constraint: 4.2 in type-array order, 4.3 in
    array (apply) order. See `apply_order` for 4.2's apply order."""
    if versions.layout(doc) == "4.2":
        return [(kind, c) for kind in kinds(doc) for c in _dicts(doc.get(kind))]
    return [(c.get("type"), c) for c in _dicts(doc.get("constraints"))]


def constraints(doc: dict, kind: str) -> list[dict]:
    return [c for k, c in all_constraints(doc) if k == kind]


def apply_order(doc: dict) -> list[tuple[str, dict]]:
    """Constraints in the order the runtime applies them. 4.3 applies the
    array in order. 4.2's Skeleton.updateCache, for each i from 0 to n-1
    (n constraints), applies the first constraint (ik, then transform, path,
    physics, each in array order) whose `order` is i; any other constraint
    never runs (see `never_applied`), and is listed last here."""
    entries = all_constraints(doc)
    if versions.layout(doc) != "4.2":
        return entries
    applied = _applied_42(entries)
    return [entries[j] for j in applied] + [e for j, e in enumerate(entries) if j not in applied]


def _applied_42(entries: list[tuple[str, dict]]) -> list[int]:
    """Indexes of the constraints 4.2 applies, in apply order."""
    applied = []
    for i in range(len(entries)):
        first = next((j for j, (_, c) in enumerate(entries) if c.get("order", 0) == i), None)
        if first is not None:
            applied.append(first)
    return applied


def never_applied(doc: dict) -> list[str]:
    """4.2 constraints the runtime never runs: an `order` outside 0..n-1, or
    one an earlier constraint in the ik/transform/path/physics lists has."""
    if versions.layout(doc) != "4.2":
        return []
    entries = all_constraints(doc)
    applied = set(_applied_42(entries))
    return [c.get("name") for j, (_, c) in enumerate(entries) if j not in applied]


def renumber(doc: dict, ordered: list[tuple[str, dict]]) -> None:
    """Give 4.2 constraints the orders 0..n-1 in the given apply order."""
    for i, (_, c) in enumerate(ordered):
        c["order"] = i


def target_bone_key(doc: dict, kind: str) -> str | None:
    """The field naming the bone an IK or transform constraint follows: IK
    `target`; transform `target` in 4.2, `source` in 4.3."""
    if kind == "ik":
        return "target"
    if kind == "transform":
        return "target" if versions.layout(doc) == "4.2" else "source"
    return None


def path_slot_key(doc: dict) -> str:
    """The field naming a path constraint's slot: `target` in 4.2, `slot` in 4.3."""
    return "target" if versions.layout(doc) == "4.2" else "slot"


def linked_mesh_key(doc: dict) -> str:
    """The field naming a linked mesh's source mesh: `parent` in 4.2, `source` in 4.3."""
    return "parent" if versions.layout(doc) == "4.2" else "source"


def animation_duration(animation: dict) -> float:
    """The reader's duration: the latest key time across every timeline."""
    latest = 0.0

    def keys_time(keys) -> None:
        nonlocal latest
        if isinstance(keys, list):
            for k in keys:
                if isinstance(k, dict):
                    t = k.get("time", 0)
                    if isinstance(t, (int, float)):
                        latest = max(latest, float(t))

    def values(obj) -> list:
        return list(obj.values()) if isinstance(obj, dict) else []

    # bones/slots/path/physics/slider: name -> {timeline: keys}; ik/transform: name -> keys.
    for group in ("bones", "slots", "path", "physics", "slider"):
        for timelines in values(animation.get(group)):
            for keys in values(timelines):
                keys_time(keys)
    for group in ("ik", "transform"):
        for keys in values(animation.get(group)):
            keys_time(keys)
    for skin in values(animation.get("attachments")):
        for slot in values(skin):
            for att in values(slot):
                for keys in values(att):
                    keys_time(keys)
    keys_time(animation.get("drawOrder"))
    folders = animation.get("drawOrderFolder")  # 4.3: [{slots, keys}]
    for folder in folders if isinstance(folders, list) else []:
        keys_time(folder.get("keys") if isinstance(folder, dict) else None)
    keys_time(animation.get("events"))
    return latest


# The editor keeps draw order folders in slot names: a slot named "face/eyes/left"
# is in folder "face" and its subfolder "face/eyes". A folder's slots sit next
# to each other in the draw order; a 4.3 draw order folder timeline keys all
# of them (subfolders included).

def draw_order_folders(names: list[str]) -> dict[str, list[str]]:
    """Every draw order folder, outer folders first, with its slots in the order given."""
    folders: dict[str, list[str]] = {}
    for name in names:
        parts = name.split("/")
        for depth in range(1, len(parts)):
            folders.setdefault("/".join(parts[:depth]), []).append(name)
    return folders


def folder_of(names: list[str], slots) -> str | None:
    """The folder holding exactly `slots` (the outermost, if nested folders hold the same ones)."""
    if not isinstance(slots, list) or not all(isinstance(s, str) for s in slots):
        return None
    wanted = set(slots)
    return next((f for f, held in draw_order_folders(names).items() if set(held) == wanted), None)


def offsets_for(base: list[str], order: list[str]) -> list[dict]:
    """The draw order key offsets that turn `base` (setup order) into `order`
    (the same names, reordered)."""
    position = {name: i for i, name in enumerate(order)}
    return [{"slot": name, "offset": position[name] - i} for i, name in enumerate(base) if position[name] != i]


def order_from(base: list[str], offsets: list[dict]) -> list[str]:
    """The order a draw order key's offsets give, as the reader computes it."""
    index = {name: i for i, name in enumerate(base)}
    order: list[str | None] = [None] * len(base)
    unchanged, original = [], 0
    for o in offsets:
        while original != index[o["slot"]]:
            unchanged.append(base[original])
            original += 1
        order[original + o.get("offset", 0)] = base[original]
        original += 1
    unchanged += base[original:]
    for i in range(len(order) - 1, -1, -1):
        if order[i] is None:
            order[i] = unchanged.pop()
    return order


def split_folders(names: list[str]) -> list[str]:
    """Folders whose slots are not next to each other in `names`."""
    index = {name: i for i, name in enumerate(names)}
    return [f for f, held in draw_order_folders(names).items() if index[held[-1]] - index[held[0]] + 1 != len(held)]


def is_convex(points: list[tuple[float, float]]) -> bool:
    """Whether a polygon turns the same way at every corner."""
    turns = set()
    for i in range(len(points)):
        (ax, ay), (bx, by), (cx, cy) = points[i - 2], points[i - 1], points[i]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) > 1e-9:
            turns.add(cross > 0)
    return len(turns) <= 1


@dataclass(frozen=True)
class Affine:
    """A bone's world transform: x' = a*x + b*y + x0, y' = c*x + d*y + y0."""

    a: float
    b: float
    c: float
    d: float
    x: float
    y: float

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return self.a * x + self.b * y + self.x, self.c * x + self.d * y + self.y

    def inverse_vector(self, x: float, y: float) -> tuple[float, float]:
        """The local vector that maps to world vector (x, y)."""
        det = self.a * self.d - self.b * self.c
        if abs(det) < 1e-12:
            raise ValueError("bone has a zero scale: its transform cannot be inverted")
        return (self.d * x - self.b * y) / det, (-self.c * x + self.a * y) / det


def local_matrix(bone: dict) -> tuple[float, float, float, float]:
    """Spine's local rotation/scale/shear matrix (Bone.updateWorldTransform)."""
    rotation = bone.get("rotation", 0.0)
    sx, sy = bone.get("scaleX", 1.0), bone.get("scaleY", 1.0)
    shx, shy = bone.get("shearX", 0.0), bone.get("shearY", 0.0)
    rx = math.radians(rotation + shx)
    ry = math.radians(rotation + 90 + shy)
    return math.cos(rx) * sx, math.cos(ry) * sy, math.sin(rx) * sx, math.sin(ry) * sy


Matrix = tuple[float, float, float, float]  # (a, b, c, d): x' = a*x + b*y, y' = c*x + d*y


def mat_mul(p: Matrix, q: Matrix) -> Matrix:
    a, b, c, dd = p
    e, f, g, h = q
    return a * e + b * g, a * f + b * h, c * e + dd * g, c * f + dd * h


def mat_inv(m: Matrix) -> Matrix:
    a, b, c, dd = m
    det = a * dd - b * c
    if abs(det) < 1e-12:
        raise ValueError("a bone has zero scale: its transform cannot be inverted")
    return dd / det, -b / det, -c / det, a / det


def decompose(m: Matrix) -> dict[str, float]:
    """Spine local fields (rotation, scaleX, scaleY, shearY; shearX = 0) whose
    `local_matrix` equals `m`, including reflections (negative scaleY)."""
    a, b, c, dd = m
    rotation = math.degrees(math.atan2(c, a))
    sign = 1.0 if a * dd - b * c >= 0 else -1.0
    y_angle = math.degrees(math.atan2(sign * dd, sign * b))
    return {
        "rotation": rotation,
        "scaleX": math.hypot(a, c),
        "scaleY": sign * math.hypot(b, dd),
        "shearY": (y_angle - rotation - 90 + 180) % 360 - 180,
    }


def matrix(t: Affine) -> Matrix:
    return t.a, t.b, t.c, t.d


def world_transforms(doc: dict) -> dict[str, Affine]:
    """Setup-pose world transforms, as the runtime computes them for bones with
    `inherit: normal` (the default). Bones with another inherit mode are
    computed as if normal; callers that need exact results must refuse them."""
    world: dict[str, Affine] = {}
    for bone in bones(doc):
        la, lb, lc, ld = local_matrix(bone)
        x, y = bone.get("x", 0.0), bone.get("y", 0.0)
        parent = world.get(bone.get("parent"))
        if parent is None:
            world[bone.get("name")] = Affine(la, lb, lc, ld, x, y)
            continue
        wx, wy = parent.apply(x, y)
        world[bone.get("name")] = Affine(
            parent.a * la + parent.b * lc,
            parent.a * lb + parent.b * ld,
            parent.c * la + parent.d * lc,
            parent.c * lb + parent.d * ld,
            wx,
            wy,
        )
    return world


def ancestors(doc: dict, name: str) -> list[str]:
    """Parent chain of a bone, nearest first."""
    by_name = bone_map(doc)
    chain, cur = [], by_name.get(name, {}).get("parent")
    while cur is not None and cur in by_name and cur not in chain:
        chain.append(cur)
        cur = by_name[cur].get("parent")
    return chain
