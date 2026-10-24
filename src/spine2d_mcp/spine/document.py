"""Spine 4.2 skeleton JSON as a plain dict: load, save, look up, measure.

The editing tools work on the raw document rather than the Pydantic models in
`schema.py`, so a skeleton exported by the Spine editor keeps every field this
package does not model (meshes, paths, sequences, nonessential data) through a
round trip. `schema.py` stays the strict model the PSD importer builds with.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Spine runtimes load only data from their own major.minor line; this project
# ships to spine-phaser-v4 on the 4.2 line.
TARGET_VERSION = "4.2"

CONSTRAINT_KINDS = ("ik", "transform", "path", "physics")


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


def bones(doc: dict) -> list[dict]:
    return doc.get("bones") or []


def bone_map(doc: dict) -> dict[str, dict]:
    return {b.get("name"): b for b in bones(doc)}


def slots(doc: dict) -> list[dict]:
    return doc.get("slots") or []


def slot_map(doc: dict) -> dict[str, dict]:
    return {s.get("name"): s for s in slots(doc)}


def skins(doc: dict) -> list[dict]:
    value = doc.get("skins") or []
    return value if isinstance(value, list) else []


def find_skin(doc: dict, name: str) -> dict | None:
    return next((s for s in skins(doc) if s.get("name") == name), None)


def attachment_names(doc: dict, slot: str) -> set[str]:
    """Every attachment name any skin defines for the slot."""
    names: set[str] = set()
    for skin in skins(doc):
        names.update((skin.get("attachments") or {}).get(slot, {}) or {})
    return names


def constraints(doc: dict, kind: str) -> list[dict]:
    return doc.get(kind) or []


def all_constraints(doc: dict) -> list[tuple[str, dict]]:
    return [(kind, c) for kind in CONSTRAINT_KINDS for c in constraints(doc, kind)]


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

    for group in ("bones", "slots", "ik", "transform", "path", "physics"):
        for timelines in (animation.get(group) or {}).values():
            if isinstance(timelines, dict):
                for keys in timelines.values():
                    keys_time(keys)
    for skin in (animation.get("attachments") or {}).values():
        for slot in (skin or {}).values():
            for att in (slot or {}).values():
                for keys in (att or {}).values():
                    keys_time(keys)
    keys_time(animation.get("drawOrder"))
    keys_time(animation.get("events"))
    return latest


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
