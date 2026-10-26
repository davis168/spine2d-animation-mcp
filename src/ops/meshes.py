"""Meshes: a grid mesh over an image (or in place of a region attachment),
weights binding its vertices to bones, and linked meshes.

A mesh lists its hull (border) vertices first, clockwise on screen, then the
inner ones, as the editor does; `edges` (vertex index pairs, each index
doubled) are the grid lines the editor shows. Unweighted vertices are points
relative to the slot's bone; weighted ones are, per vertex, the bones it
follows with the vertex's position in each bone's space and a weight.
"""

from __future__ import annotations

import math

from ..edits import EditError
from ..spine import document as d
from ..spine import versions
from . import rig
from .attachments import slot_attachments, to_bone_space
from .rig import require_normal_chain, weighted_influences

MAX_INFLUENCES = 4  # bones per vertex for automatic weights
MIN_WEIGHT = 0.01  # smaller automatic weights are dropped
CELL = 48  # target grid cell size in pixels when no grid is given


def auto_grid(width: float, height: float) -> tuple[int, int]:
    """Columns and rows giving cells of about CELL pixels (1..8 each way)."""
    return (max(1, min(8, round(width / CELL))), max(1, min(8, round(height / CELL))))


def grid(cols: int, rows: int) -> tuple[list[float], int, list[int], list[int]]:
    """uvs (u right, v down; hull first), hull length, triangles and edges of
    a cols x rows grid over the image."""
    if not (isinstance(cols, int) and isinstance(rows, int) and 1 <= cols <= 64 and 1 <= rows <= 64):
        raise EditError("grid must be [columns, rows], each 1..64")
    order = ([(i, 0) for i in range(cols + 1)] + [(cols, j) for j in range(1, rows + 1)]
             + [(i, rows) for i in range(cols - 1, -1, -1)] + [(0, j) for j in range(rows - 1, 0, -1)])
    hull = len(order)
    order += [(i, j) for j in range(1, rows) for i in range(1, cols)]
    index = {p: k for k, p in enumerate(order)}
    uvs = [round(c, 6) for i, j in order for c in (i / cols, j / rows)]
    triangles = []
    for j in range(rows):
        for i in range(cols):
            a, b, c, e = index[(i, j)], index[(i + 1, j)], index[(i + 1, j + 1)], index[(i, j + 1)]
            triangles += [a, b, c, a, c, e]
    pairs = [(k, (k + 1) % hull) for k in range(hull)]
    pairs += [(index[(i, j)], index[(i + 1, j)]) for j in range(1, rows) for i in range(cols)]
    pairs += [(index[(i, j)], index[(i, j + 1)]) for i in range(1, cols) for j in range(rows)]
    return uvs, hull, triangles, [2 * k for pair in pairs for k in pair]


def _placed(uvs: list[float], width: float, height: float, x: float, y: float, rotation: float,
            scale_x: float, scale_y: float) -> list[tuple[float, float]]:
    """Each uv's point where a region with these fields shows that part of the image."""
    cos, sin = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))
    points = []
    for i in range(0, len(uvs), 2):
        px, py = (uvs[i] - 0.5) * width * scale_x, (0.5 - uvs[i + 1]) * height * scale_y
        points.append((x + px * cos - py * sin, y + px * sin + py * cos))
    return points


def world_points(doc: dict, slot: str, att: dict) -> list[tuple[float, float]]:
    """A vertex attachment's vertices in world space, in the setup pose."""
    world = d.world_transforms(doc)
    vertices = att["vertices"]
    count = len(att["uvs"]) // 2 if "uvs" in att else att["vertexCount"]
    if len(vertices) == 2 * count:
        require_normal_chain(doc, d.slot_map(doc)[slot]["bone"])
        t = world[d.slot_map(doc)[slot]["bone"]]
        return [t.apply(vertices[i], vertices[i + 1]) for i in range(0, len(vertices), 2)]
    bones = d.bones(doc)
    points, i = [], 0
    while i < len(vertices):
        n, wx, wy = int(vertices[i]), 0.0, 0.0  # counts and indices may be written as floats
        for k in range(i + 1, i + 1 + 4 * n, 4):
            require_normal_chain(doc, bones[int(vertices[k])]["name"])
            t = world[bones[int(vertices[k])]["name"]]
            px, py = t.apply(vertices[k + 1], vertices[k + 2])
            wx, wy = wx + px * vertices[k + 3], wy + py * vertices[k + 3]
        points.append((wx, wy))
        i += 1 + 4 * n
    return points


def _segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _automatic(doc: dict, bones: list[str], point: tuple[float, float]) -> dict[str, float]:
    """Weights by inverse squared distance to each bone (origin to tip), the
    closest MAX_INFLUENCES kept."""
    world, by_name = d.world_transforms(doc), d.bone_map(doc)
    raw = {}
    for name in bones:
        t, length = world[name], by_name[name].get("length", 0)
        dist = _segment_distance(*point, t.x, t.y, t.x + t.a * length, t.y + t.c * length)
        raw[name] = 1 / (dist + 1) ** 2
    kept = dict(sorted(raw.items(), key=lambda kv: -kv[1])[:MAX_INFLUENCES])
    total = sum(kept.values())
    kept = {b: w / total for b, w in kept.items() if w / total >= MIN_WEIGHT}
    total = sum(kept.values())
    return {b: w / total for b, w in kept.items()}


def _weighted(doc: dict, points: list[tuple[float, float]], weights: list[dict[str, float]]) -> list:
    """The weighted vertices array for world points and per-vertex weights."""
    world = d.world_transforms(doc)
    index = {b["name"]: i for i, b in enumerate(d.bones(doc))}
    for bone in {b for w in weights for b in w}:
        require_normal_chain(doc, bone)
    out: list = []
    for (px, py), influences in zip(points, weights):
        items = sorted(influences.items(), key=lambda kv: index[kv[0]])
        rounded = [round(w, 4) for _, w in items]
        rounded[rounded.index(max(rounded))] += round(1 - sum(rounded), 4)  # sum exactly 1
        out.append(len(items))
        for (bone, _), w in zip(items, rounded):
            t = world[bone]
            bx, by = t.inverse_vector(px - t.x, py - t.y)
            out += [index[bone], round(bx, 4), round(by, 4), round(w, 4)]
    return out


def _weights_for(doc: dict, points: list[tuple[float, float]], bones: list[str] | None,
                 weights: list[dict] | None) -> list[dict[str, float]]:
    by_name = d.bone_map(doc)
    if weights is not None:
        if not isinstance(weights, list) or len(weights) != len(points):
            raise EditError(f"weights must give one {{bone: weight}} per vertex ({len(points)} vertices)")
        out = []
        for i, w in enumerate(weights):
            if not isinstance(w, dict) or not w or any(b not in by_name for b in w):
                raise EditError(f"weights[{i}]: give {{bone: weight}} with this skeleton's bones")
            if any(not isinstance(v, (int, float)) or v < 0 for v in w.values()) or sum(w.values()) <= 0:
                raise EditError(f"weights[{i}]: weights must be 0 or more, not all 0")
            total = sum(w.values())
            out.append({b: v / total for b, v in w.items() if v > 0})
        return out
    unknown = [b for b in bones or [] if b not in by_name]
    if not bones or unknown:
        raise EditError(f"give bones to bind to (no bone {', '.join(map(repr, unknown))})" if unknown
                        else "give bones to bind to")
    return [_automatic(doc, bones, p) for p in points]


def _deform_users(doc: dict, skin: str, slot: str, name: str) -> list[str]:
    """Animations with deform keys laid out by this mesh's vertices: its own,
    and those of linked meshes that link to it."""
    owners = [(skin, slot, name)]
    for s in d.skins(doc):
        for other_slot, atts in (s.get("attachments") or {}).items():
            for other_name, att in (atts or {}).items():
                if (isinstance(att, dict) and att.get("type") == "linkedmesh"
                        and att.get("parent", att.get("source")) == name and att.get("skin", "default") == skin
                        and att.get("slot", other_slot) == slot):
                    owners.append((s.get("name"), other_slot, other_name))
    return [f"{anim_name} ({'/'.join(owner)})" for anim_name, anim in (doc.get("animations") or {}).items()
            for owner in owners
            if (((((anim or {}).get("attachments") or {}).get(owner[0]) or {}).get(owner[1]) or {})
                .get(owner[2]) or {}).get("deform")]


def order_influences(doc: dict) -> list[str]:
    """List each weighted vertex's bones in bone order, moving every deform
    key's deltas (one pair per influence) along with them, including keys on
    linked meshes that share those vertices. The runtimes read any order; the
    Spine editor refuses a mesh whose vertices list two bones in both orders.
    Returns "skin/slot/attachment" for each attachment reordered."""
    moved: dict[tuple[str, str, str], list[int]] = {}  # new influence position -> old position
    for skin in d.skins(doc):
        for slot, atts in (skin.get("attachments") or {}).items():
            for name, att in (atts or {}).items():
                if not (isinstance(att, dict) and att.get("type", "region") in rig._VERTEX_TYPES
                        and "vertices" in att and rig._is_weighted(att)):
                    continue
                vertices, out, order, i, first = att["vertices"], [], [], 0, 0
                while i < len(vertices):
                    n = int(vertices[i])
                    records = [vertices[i + 1 + 4 * j:i + 5 + 4 * j] for j in range(n)]
                    ranked = sorted(range(n), key=lambda j: records[j][0])
                    out += [n] + [v for j in ranked for v in records[j]]
                    order += [first + j for j in ranked]
                    first += n
                    i += 1 + 4 * n
                if order != sorted(order):
                    att["vertices"] = out
                    moved[(skin.get("name"), slot, name)] = order
    if not moved:
        return []
    owners = dict(moved)
    for skin in d.skins(doc):  # linked meshes key deforms in their source's influence order
        for slot, atts in (skin.get("attachments") or {}).items():
            for name, att in (atts or {}).items():
                if isinstance(att, dict) and att.get("type") == "linkedmesh":
                    source = (att.get("skin", "default"), att.get("slot", slot), att.get("source", att.get("parent")))
                    if source in moved:
                        owners[(skin.get("name"), slot, name)] = moved[source]
    for anim in (doc.get("animations") or {}).values():
        for (skin, slot, name), order in owners.items():
            timelines = ((((anim or {}).get("attachments") or {}).get(skin) or {}).get(slot) or {}).get(name) or {}
            for key in timelines.get("deform") or []:
                deltas = key.get("vertices")
                if not deltas:
                    continue
                full = [0.0] * (2 * len(order))
                offset = key.get("offset", 0)
                full[offset:offset + len(deltas)] = deltas
                full = [c for old in order for c in full[2 * old:2 * old + 2]]
                start = next((k for k, v in enumerate(full) if v), None)
                key.pop("offset", None)
                if start is None:
                    key.pop("vertices")  # all zero: the setup pose
                    continue
                end = len(full) - next(k for k, v in enumerate(reversed(full)) if v)
                key["vertices"] = full[start:end]
                if start:
                    key["offset"] = start
    return ["/".join(owner) for owner in moved]


def add_mesh(
    doc: dict,
    slot: str,
    name: str,
    size: tuple[float, float] | None = None,
    skin: str = "default",
    path: str | None = None,
    x: float = 0.0,
    y: float = 0.0,
    rotation: float = 0.0,
    scaleX: float = 1.0,
    scaleY: float = 1.0,
    world: bool = False,
    grid_size: tuple[int, int] | None = None,
    bones: list[str] | None = None,
    weights: list[dict] | None = None,
    set_as_setup: bool = False,
    replace: bool = False,
    sequence: dict | None = None,
    color: str | None = None,
) -> dict:
    """Add a grid mesh showing an image of `size` (w, h) placed like a region
    (x, y, rotation, scale relative to the slot's bone, or world x, y with
    `world`). Without size, the region attachment `name` in that slot and
    skin becomes a mesh in place (same image and placement, its keys kept).
    `bones` binds the vertices with automatic weights; `weights` gives them
    per vertex."""
    slot_entry = d.slot_map(doc).get(slot)
    if slot_entry is None:
        raise EditError(f"no slot {slot!r}")
    converted, image_name = None, None
    if size is None:
        region = ((((d.find_skin(doc, skin) or {}).get("attachments") or {}).get(slot)) or {}).get(name)
        if not isinstance(region, dict) or region.get("type", "region") != "region":
            raise EditError(f"no region attachment {name!r} in slot {slot!r}, skin {skin!r} to turn into a mesh "
                            "(or give an image)")
        size = (region["width"], region["height"])
        x, y, rotation = region.get("x", 0), region.get("y", 0), region.get("rotation", 0)
        scaleX, scaleY = region.get("scaleX", 1), region.get("scaleY", 1)
        path = path or region.get("path")
        image_name = region.get("name")  # the image, when the attachment names it (the PSD importer does)
        sequence = sequence or region.get("sequence")
        color = color or region.get("color")
        converted, replace = name, True
    elif world:
        (x, y), = to_bone_space(doc, slot, [(x, y)])
    atts = slot_attachments(doc, skin, slot, name, replace)
    width, height = float(size[0]), float(size[1])
    cols, rows = grid_size or auto_grid(width, height)
    uvs, hull, triangles, edges = grid(cols, rows)
    points = _placed(uvs, width, height, x, y, rotation, scaleX, scaleY)
    att: dict = {"type": "mesh"}
    if image_name:
        att["name"] = image_name
    if path and path != (image_name or name):
        att["path"] = path
    if color:
        att["color"] = color
    att["uvs"] = uvs
    att["triangles"] = triangles
    if bones is not None or weights is not None:
        require_normal_chain(doc, slot_entry["bone"])
        t = d.world_transforms(doc)[slot_entry["bone"]]
        world_pts = [t.apply(px, py) for px, py in points]
        att["vertices"] = _weighted(doc, world_pts, _weights_for(doc, world_pts, bones, weights))
    else:
        att["vertices"] = [round(v, 4) for p in points for v in p]
    att["hull"] = hull
    att["edges"] = edges
    att["width"], att["height"] = width, height
    if sequence is not None:
        att["sequence"] = sequence
    atts[name] = att
    if set_as_setup:
        slot_entry["attachment"] = name
    return {"slot": slot, "mesh": name, "skin": skin, "vertices": len(uvs) // 2, "grid": [cols, rows],
            "weighted": bones is not None or weights is not None,
            **({"from_region": converted} if converted else {})}


def set_weights(doc: dict, slot: str, name: str, skin: str = "default", bones: list[str] | None = None,
                weights: list[dict] | None = None) -> dict:
    """Bind a mesh's vertices to bones (automatic weights from `bones`, or
    `weights` per vertex), keeping them where they are in the setup pose;
    bones=[] unbinds them back to the slot's bone."""
    att = ((((d.find_skin(doc, skin) or {}).get("attachments") or {}).get(slot)) or {}).get(name)
    if not isinstance(att, dict) or att.get("type") not in ("mesh", "boundingbox", "clipping", "path"):
        raise EditError(f"no mesh (or other vertex attachment) {name!r} in slot {slot!r}, skin {skin!r}")
    keyed = _deform_users(doc, skin, slot, name)
    if keyed:
        raise EditError(f"deform keys in {', '.join(keyed)} are stored per weight: remove them before "
                        "re-weighting, then key the deform again")
    points = world_points(doc, slot, att)
    if bones == [] and weights is None:
        att["vertices"] = [round(v, 4) for p in to_bone_space(doc, slot, points) for v in p]
        return {"slot": slot, "attachment": name, "weighted": False}
    att["vertices"] = _weighted(doc, points, _weights_for(doc, points, bones, weights))
    used = sorted({d.bones(doc)[att["vertices"][k]]["name"] for k in weighted_influences(att)})
    return {"slot": slot, "attachment": name, "weighted": True, "bones": used}


def add_linked_mesh(
    doc: dict,
    slot: str,
    name: str,
    source: str,
    size: tuple[float, float] | None = None,
    skin: str = "default",
    path: str | None = None,
    source_skin: str | None = None,
    source_slot: str | None = None,
    timelines: bool = True,
    set_as_setup: bool = False,
    replace: bool = False,
    sequence: dict | None = None,
) -> dict:
    """Add a linked mesh: another image on `source`'s mesh (its vertices,
    triangles and weights). With `timelines` it plays the source's deform
    keys too. (4.3) The source may be in another slot (`source_slot`)."""
    if slot not in d.slot_map(doc):
        raise EditError(f"no slot {slot!r}")
    version = versions.layout(doc)
    source_slot = source_slot or slot
    if source_slot != slot and version == "4.2":
        raise EditError("a source mesh in another slot needs format version 4.3; in 4.2 the source must be in "
                        "the same slot")
    owner = d.find_skin(doc, source_skin or "default")
    if owner is None:
        raise EditError(f"no skin {source_skin or 'default'!r} for the source mesh")
    mesh = ((owner.get("attachments") or {}).get(source_slot) or {}).get(source)
    if not isinstance(mesh, dict) or mesh.get("type") != "mesh":
        raise EditError(f"no mesh {source!r} in slot {source_slot!r}, skin {owner['name']!r} "
                        "(a linked mesh links to a mesh, not to another linked mesh)")
    if (source_skin or "default") == skin and source_slot == slot and source == name:
        raise EditError("a linked mesh cannot link to itself")
    atts = slot_attachments(doc, skin, slot, name, replace)
    att: dict = {"type": "linkedmesh"}
    if path and path != name:
        att["path"] = path
    att[d.linked_mesh_key(doc)] = source
    if source_skin and source_skin != "default":
        att["skin"] = source_skin
    if source_slot != slot:
        att["slot"] = source_slot
    if not timelines:
        att["timelines"] = False
    width, height = size or (mesh.get("width", 0), mesh.get("height", 0))
    att["width"], att["height"] = float(width), float(height)
    if sequence is not None:
        att["sequence"] = sequence
    atts[name] = att
    if set_as_setup:
        d.slot_map(doc)[slot]["attachment"] = name
    return {"slot": slot, "linked_mesh": name, "skin": skin, "source": source, "source_slot": source_slot,
            "source_skin": owner["name"], "timelines": timelines}
