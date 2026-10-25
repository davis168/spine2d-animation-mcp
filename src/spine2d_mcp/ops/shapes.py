"""Bounding box, point and path attachments.

A bounding box is a polygon for hit tests (SkeletonBounds) and a point
marks a position and direction, e.g. where a gun fires. A path is a Bezier
curve that path constraints move bones along: each knot is stored as three
vertices (incoming handle, point, outgoing handle), and `lengths` holds the
length from the path's start to the end of each curve in the setup pose,
which the runtime reads when the path is not constant speed.
"""

from __future__ import annotations

import math

from ..edits import EditError
from ..spine import document as d
from .attachments import points_of, require_own_slot, slot_attachments, to_bone_space


def _slot(doc: dict, slot: str) -> dict:
    entry = d.slot_map(doc).get(slot)
    if entry is None:
        raise EditError(f"no slot {slot!r} (edit_rig add_slot adds one)")
    return entry


def add_bounding_box(doc: dict, slot: str, vertices, name: str = "bounds", skin: str = "default",
                     world: bool = False, set_as_setup: bool = True, replace: bool = False,
                     color: str | None = None) -> dict:
    """A polygon on `slot` for hit tests (the runtime's SkeletonBounds uses
    the bounding boxes slots show)."""
    entry = _slot(doc, slot)
    points = points_of(vertices)
    if len(points) < 3:
        raise EditError("a bounding box needs at least 3 points")
    if world:
        points = to_bone_space(doc, slot, points)
    if set_as_setup:
        require_own_slot(doc, slot, name, "boundingbox", "the bounding box")
    atts = slot_attachments(doc, skin, slot, name, replace)
    att: dict = {"type": "boundingbox", "vertexCount": len(points),
                 "vertices": [round(v, 4) for p in points for v in p]}
    if color:
        att["color"] = color
    atts[name] = att
    if set_as_setup:
        entry["attachment"] = name
    return {"slot": slot, "bounding_box": name, "skin": skin, "points": len(points)}


def add_point(doc: dict, slot: str, name: str, x: float = 0.0, y: float = 0.0, rotation: float = 0.0,
              skin: str = "default", world: bool = False, replace: bool = False, color: str | None = None) -> dict:
    """A point (position and direction) relative to the slot's bone, or a
    world position and angle with `world`."""
    entry = _slot(doc, slot)
    if world:
        (x, y), = to_bone_space(doc, slot, [(x, y)])
        t = d.world_transforms(doc)[entry["bone"]]
        rotation -= math.degrees(math.atan2(t.c, t.a))
    atts = slot_attachments(doc, skin, slot, name, replace)
    att: dict = {"type": "point"}
    for key, value in (("x", x), ("y", y), ("rotation", rotation)):
        if round(value, 4) != 0:
            att[key] = round(value, 4)
    if color:
        att["color"] = color
    atts[name] = att
    return {"slot": slot, "point": name, "skin": skin, **{k: att.get(k, 0) for k in ("x", "y", "rotation")}}


def _knots(points) -> list[dict]:
    """Knots as {x, y, in?, out?}: from [x, y] pairs or objects with optional handles."""
    if not isinstance(points, list):
        raise EditError("points must be a list of [x, y] or {x, y, in: [x, y], out: [x, y]}")
    knots = []
    for i, p in enumerate(points):
        if isinstance(p, dict):
            try:
                knot = {"x": float(p["x"]), "y": float(p["y"])}
                for key in ("in", "out"):
                    if key in p:
                        knot[key] = tuple(map(float, p[key]))
            except (KeyError, TypeError, ValueError):
                raise EditError(f"points[{i}]: give x, y and optionally in/out handles as [x, y]") from None
        else:
            (x, y), = points_of([p])
            knot = {"x": x, "y": y}
        knots.append(knot)
    return knots


def _handles(knots: list[dict], closed: bool) -> list[tuple[float, float]]:
    """Every knot's (in handle, point, out handle); missing handles are
    smooth (Catmull-Rom: a sixth of the neighbours' difference)."""
    n = len(knots)
    out = []
    for i, k in enumerate(knots):
        p = (k["x"], k["y"])
        if closed or 0 < i < n - 1:
            prev, nxt = knots[(i - 1) % n], knots[(i + 1) % n]
            tx, ty = (nxt["x"] - prev["x"]) / 6, (nxt["y"] - prev["y"]) / 6
        else:
            other = knots[1] if i == 0 else knots[-2]
            sign = 1 if i == 0 else -1
            tx, ty = sign * (other["x"] - p[0]) / 3, sign * (other["y"] - p[1]) / 3
        out += [k.get("in", (p[0] - tx, p[1] - ty)), p, k.get("out", (p[0] + tx, p[1] + ty))]
    return out


def _bezier_length(p0, p1, p2, p3, steps: int = 200) -> float:
    total, last = 0.0, p0
    for s in range(1, steps + 1):
        t = s / steps
        u = 1 - t
        point = tuple(u * u * u * a + 3 * u * u * t * b + 3 * u * t * t * c + t * t * t * e
                      for a, b, c, e in zip(p0, p1, p2, p3))
        total += math.hypot(point[0] - last[0], point[1] - last[1])
        last = point
    return total


def path_lengths(world: list[tuple[float, float]], closed: bool) -> list[float]:
    """Length from the start to the end of each curve, one entry per knot
    (an open path's last entry repeats the total)."""
    n = len(world) // 3
    lengths, total = [], 0.0
    for i in range(n if closed else n - 1):
        j = (i + 1) % n
        total += _bezier_length(world[3 * i + 1], world[3 * i + 2], world[3 * j], world[3 * j + 1])
        lengths.append(round(total, 4))
    if not closed:
        lengths.append(round(total, 4))
    return lengths


def add_path(doc: dict, slot: str, points, name: str = "path", skin: str = "default", closed: bool = False,
             constant_speed: bool = True, world: bool = False, set_as_setup: bool = True, replace: bool = False,
             color: str | None = None) -> dict:
    """A Bezier path through `points` (relative to the slot's bone, or world
    points with `world`) for path constraints to move bones along. Points
    without handles get smooth ones."""
    entry = _slot(doc, slot)
    knots = _knots(points)
    if len(knots) < 2:
        raise EditError("a path needs at least 2 points")
    vertices = _handles(knots, closed)
    if world:
        vertices = to_bone_space(doc, slot, vertices)
    if set_as_setup:
        require_own_slot(doc, slot, name, "path", "the path")
    t = d.world_transforms(doc)[entry["bone"]]
    lengths = path_lengths([t.apply(x, y) for x, y in vertices], closed)
    atts = slot_attachments(doc, skin, slot, name, replace)
    att: dict = {"type": "path"}
    if closed:
        att["closed"] = True
    if not constant_speed:
        att["constantSpeed"] = False
    att["lengths"] = lengths
    att["vertexCount"] = len(vertices)
    att["vertices"] = [round(v, 4) for p in vertices for v in p]
    if color:
        att["color"] = color
    atts[name] = att
    if set_as_setup:
        entry["attachment"] = name
    return {"slot": slot, "path": name, "skin": skin, "knots": len(knots), "closed": closed,
            "length": lengths[-1]}
