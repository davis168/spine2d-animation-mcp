"""Build a Spine skeleton from a PSD's Import PSD tags, as the Spine editor does.

Bones, slots, skins and attachments come from the resolved tags (psd/tags.py)
and the images from psd/render.py. Following the editor and Esoteric's
PhotoshopToSpine script:

- A bone sits at the centre of the top visible layer on it (else the top
  visible layer on its child bones); bones are not rotated.
- Slots are created bottom-to-top, so draw order follows the layer panel; a
  slot's setup attachment is its bottom visible layer. A slot folder that is
  split by another slot moves next to its last slot.
- A skin attachment is keyed by its placeholder, with `name` and `path` only
  when they differ from it.
- A region sits at its image centre; [scale:k] makes its image k times larger
  and the attachment 1/k; [rotate:deg] turns its image deg counter-clockwise
  and the attachment back. A [mesh] is a quad over its image; a [mesh:name]
  linked mesh shares its source mesh's image frame.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from ..psd.render import Rendered, center, content_box, render, rotate
from ..psd.tags import PsdTagError, TaggedLayer, TaggedPsd
from . import document as d


@dataclass
class TaggedBuild:
    doc: dict
    images: dict[str, Image.Image]  # image path (the atlas region name) -> image
    warnings: list[str]


def build(tagged: TaggedPsd, *, spine: str, scale: float = 1.0) -> TaggedBuild:
    """The skeleton in format `spine` (the skeleton.spine header) and its images."""
    canvas = (tagged.width, tagged.height)
    warnings = list(tagged.warnings)
    errors: list[str] = []
    doc: dict = {"skeleton": {"spine": spine}}

    origin = tagged.origin
    if not isinstance(origin, tuple):
        box = content_box(origin, canvas, clip=False)
        if box is None:
            warnings.append(f"the [origin] layer has no pixels, so the canvas centre is the origin: {origin.path}")
            origin = (tagged.width / 2, tagged.height / 2)
        else:
            origin = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

    rendered: dict[TaggedLayer, Rendered] = {}
    empty = []
    for layer in tagged.layers:
        if not isinstance(layer.mesh, str):
            found = render(layer, canvas, scale)
            if found is None:
                empty.append(layer.item.path)
            else:
                rendered[layer] = found
    sources: dict[TaggedLayer, TaggedLayer] = {}
    meshes = [layer for layer in rendered if layer.mesh is True]
    for layer in tagged.layers:
        if isinstance(layer.mesh, str):
            source = _source(layer, meshes, errors)
            if source is not None:
                sources[layer] = source
                rendered[layer] = render(layer, canvas, scale, frame=rendered[source].frame)
                if rendered[layer] is None:
                    empty.append(layer.item.path)
    if empty:
        warnings.append(f"{len(empty)} layer(s) have no pixels on the canvas and were left out: " + "; ".join(empty))
    layers = [layer for layer in tagged.layers if rendered.get(layer) is not None]

    world = _bone_positions(tagged, layers, rendered, origin, scale)
    bones = [{"name": "root"}]
    for name, bone in tagged.bones.items():
        if name not in world:
            continue
        parent = bone.parent or "root"
        px, py = world[bone.parent] if bone.parent else (0.0, 0.0)
        entry = {"name": name, "parent": parent}
        x, y = _r(world[name][0] - px), _r(world[name][1] - py)
        if x:
            entry["x"] = x
        if y:
            entry["y"] = y
        bones.append(entry)

    slots: dict[str, dict] = {}
    skins: dict[str, dict[str, dict[str, dict]]] = {"default": {}}
    images: dict[str, Image.Image] = {}
    image_from: dict[str, TaggedLayer] = {}
    key = d.linked_mesh_key(doc)
    for layer in layers:
        bone = layer.bone or "root"
        slot = slots.setdefault(layer.slot, {"name": layer.slot, "bone": bone})
        if slot["bone"] != bone:
            warnings.append(f"slot {layer.slot!r} is on bone {slot['bone']!r}, so {layer.item.path} (on {bone!r}) "
                            f"is drawn on {slot['bone']!r} too")
        if layer.visible:
            slot.setdefault("attachment", layer.placeholder)
        if layer.blend:
            slot.setdefault("blend", layer.blend)

        image = rendered[layer].image
        if layer.path in images and images[layer.path].tobytes() != image.tobytes():
            errors.append(f"{image_from[layer.path].item.path} and {layer.item.path} write different images to "
                          f"{layer.path}.png (give one a [path])")
        images.setdefault(layer.path, image)
        image_from.setdefault(layer.path, layer)

        bone_at = world.get(slot["bone"], (0.0, 0.0))
        attachment = _attachment(layer, rendered[layer], sources.get(layer), bone_at, origin, scale, key)
        skins.setdefault(layer.skin, {}).setdefault(layer.slot, {})[layer.placeholder] = attachment
    if errors:
        raise PsdTagError(errors)

    order = _gather_folders(list(slots))
    doc["skeleton"].update(_bounds(layers, rendered, origin, scale))
    doc["skeleton"]["images"] = "./images/"
    doc["bones"] = bones
    doc["slots"] = [_slot(slots[name]) for name in order]
    doc["skins"] = [{"name": name, "attachments": attachments} for name, attachments in skins.items() if attachments]
    return TaggedBuild(doc, images, warnings)


def _source(layer: TaggedLayer, meshes: list[TaggedLayer], errors: list[str]) -> TaggedLayer | None:
    """The [mesh] a [mesh:name] links to: names compared by /-segments from the
    right, in the same slot; the shortest full match wins."""
    want = [part for part in layer.mesh.split("/") if part]
    found = [m for m in meshes if m.attachment.split("/")[-len(want):] == want]
    here = [m for m in found if m.slot == layer.slot]
    if not here:
        message = f"{layer.item.path}: [mesh:{layer.mesh}] names no [mesh] layer in slot {layer.slot!r}"
        if found:
            message += f" ({found[0].attachment!r} is in slot {found[0].slot!r}; a linked mesh and its source share a slot)"
        errors.append(message)
        return None
    shortest = min(len(m.attachment.split("/")) for m in here)
    best = [m for m in here if len(m.attachment.split("/")) == shortest]
    if len(best) > 1:
        errors.append(f"{layer.item.path}: [mesh:{layer.mesh}] matches " + ", ".join(repr(m.attachment) for m in best)
                      + "; name more of the source's path")
        return None
    return best[0]


def _bone_positions(tagged: TaggedPsd, layers: list[TaggedLayer], rendered: dict, origin, scale: float) -> dict:
    """World position of every bone that holds a layer: the centre of its top visible layer."""
    under: dict[str, list[TaggedLayer]] = {name: [] for name in tagged.bones}
    for layer in layers:
        name = layer.bone
        while name is not None:
            under[name].append(layer)
            name = tagged.bones[name].parent
    world: dict[str, tuple[float, float]] = {}
    for name, bone in tagged.bones.items():
        if not under[name]:
            continue
        direct = [layer for layer in bone.layers if layer in rendered]
        pick = _top(direct) or _top(under[name])
        l, t, r, b = rendered[pick].content
        world[name] = (((l + r) / 2 - origin[0]) * scale, (origin[1] - (t + b) / 2) * scale)
    return world


def _top(layers: list[TaggedLayer]) -> TaggedLayer | None:
    visible = [layer for layer in layers if layer.visible]
    return (visible or layers or [None])[-1]


def _attachment(layer: TaggedLayer, rendered: Rendered, source: TaggedLayer | None,
                bone_at: tuple[float, float], origin, scale: float, key: str) -> dict:
    att: dict = {}
    if layer.attachment != layer.placeholder:
        att["name"] = layer.attachment
    if layer.path != layer.attachment:
        att["path"] = layer.path
    width, height = rendered.image.size
    frame = rendered.frame
    if source is not None:
        att["type"] = "linkedmesh"
        if source.skin != "default":
            att["skin"] = source.skin
        att[key] = source.placeholder
        att.update(width=width, height=height)
        return att
    cx, cy = center(frame, origin, scale)
    if layer.mesh:
        hw, hh = width / 2 / frame.scale, height / 2 / frame.scale
        vertices = []
        for corner in ((hw, -hh), (-hw, -hh), (-hw, hh), (hw, hh)):
            dx, dy = rotate(corner, -frame.rotate)
            vertices += [_r(cx + dx - bone_at[0]), _r(cy + dy - bone_at[1])]
        att.update(type="mesh", uvs=[1, 1, 0, 1, 0, 0, 1, 0], triangles=[1, 2, 3, 1, 3, 0], vertices=vertices,
                   hull=4, edges=[0, 2, 2, 4, 4, 6, 0, 6], width=width, height=height)
        return att
    x, y = _r(cx - bone_at[0]), _r(cy - bone_at[1])
    if x:
        att["x"] = x
    if y:
        att["y"] = y
    if frame.scale != 1:
        att.update(scaleX=round(1 / frame.scale, 6), scaleY=round(1 / frame.scale, 6))
    if frame.rotate % 360:
        att["rotation"] = _r(-frame.rotate)
    att.update(width=width, height=height)
    return att


def _slot(slot: dict) -> dict:
    return {k: slot[k] for k in ("name", "bone", "attachment", "blend") if k in slot}


def _gather_folders(names: list[str]) -> list[str]:
    """Move each split slot folder's slots next to its last slot, innermost folders first."""
    for folder in sorted(d.draw_order_folders(names), key=lambda f: -f.count("/")):
        held = set(d.draw_order_folders(names)[folder])
        last = max(i for i, name in enumerate(names) if name in held)
        names = ([n for n in names[:last + 1] if n not in held] + [n for n in names if n in held]
                 + names[last + 1:])
    return names


def _bounds(layers: list[TaggedLayer], rendered: dict, origin, scale: float) -> dict:
    """The setup pose's bounds: every visible layer's pixels."""
    boxes = [rendered[layer].content for layer in layers if layer.visible] or [rendered[layer].content for layer in layers]
    if not boxes:
        return {}
    l, t = min(box[0] for box in boxes), min(box[1] for box in boxes)
    r, b = max(box[2] for box in boxes), max(box[3] for box in boxes)
    return {"x": _r((l - origin[0]) * scale), "y": _r((origin[1] - b) * scale),
            "width": _r((r - l) * scale), "height": _r((b - t) * scale)}


def _r(value: float) -> float:
    value = round(value, 2)
    return 0.0 if value == 0 else value
