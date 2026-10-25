"""The image Spine's Import PSD writes for a tagged layer, and where it sits.

In canvas coordinates (y-down): render the layer with its clipping layers (a
[merge] group flattened), draw the [overlay] layers above it clipped to its
pixels, cut it to the canvas, take its frame (the whitespace-trimmed bounds,
the whole canvas, or its layer mask), scale by Scale x [scale], rotate by
[rotate] (counter-clockwise, then trimmed again), and pad.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps
from psd_tools import PSDImage

from .tags import BLENDS, Item, TaggedLayer, tag_names

Box = tuple[int, int, int, int]  # left, top, right, bottom


@dataclass
class Frame:
    """The part of the canvas an image shows and how it is transformed; a linked
    mesh is cut with its source's frame so the two images line up."""

    box: Box  # canvas bounds before scaling
    scale: float  # [scale]
    rotate: float  # [rotate], degrees counter-clockwise
    crop: Box | None  # trim after rotating, in the rotated image
    shift: tuple[float, float]  # the trimmed image's centre from the rotated image's, in image pixels (y-down)
    pad: int


@dataclass
class Rendered:
    image: Image.Image
    frame: Frame
    content: Box  # canvas bounds of the layer's pixels


def pixels(item: Item, canvas: tuple[int, int]) -> tuple[Image.Image, int, int] | None:
    """The item's pixels and their canvas offset: a layer with its visible
    clipping layers, or a group flattened from its visible, unignored layers."""
    layer = item.layer
    if not layer.is_group():
        image = layer.composite(layer_filter=lambda node: node is layer or node.visible)
        return (image.convert("RGBA"), layer.bbox[0], layer.bbox[1]) if image is not None else None
    hidden = []
    node = layer
    while node is not None and not isinstance(node, PSDImage):
        if not node.visible:
            hidden.append(node)
            node.visible = True
        node = node.parent
    try:
        image = layer.composite(viewport=(0, 0, *canvas),
                                layer_filter=lambda n: n.visible and "ignore" not in tag_names(n.name))
    finally:
        for node in hidden:
            node.visible = False
    return (image.convert("RGBA"), 0, 0) if image is not None else None


def content_box(item: Item, canvas: tuple[int, int], clip: bool = True) -> Box | None:
    """Canvas bounds of the item's pixels, cut to the canvas unless `clip` is False."""
    found = pixels(item, canvas)
    if found is None:
        return None
    return _bounds(*found, canvas if clip else None)


def _bounds(image: Image.Image, left: int, top: int, canvas: tuple[int, int] | None) -> Box | None:
    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        return None
    box = (bbox[0] + left, bbox[1] + top, bbox[2] + left, bbox[3] + top)
    if canvas is not None:
        box = (max(box[0], 0), max(box[1], 0), min(box[2], canvas[0]), min(box[3], canvas[1]))
        if box[0] >= box[2] or box[1] >= box[3]:
            return None
    return box


def render(layer: TaggedLayer, canvas: tuple[int, int], scale: float = 1.0, frame: Frame | None = None) -> Rendered | None:
    """The layer's image, or None when it has no pixels on the canvas. With
    `frame`, the image is cut exactly like the frame's (a linked mesh's source)."""
    found = pixels(layer.item, canvas)
    if found is None:
        return None
    image, left, top = found
    for over in reversed(layer.overlays):  # the overlay closest to the layer first
        drawn = pixels(over, canvas)
        if drawn is not None:
            image = _overlay(image, left, top, *drawn, BLENDS.get(over.layer.blend_mode))
    content = _bounds(image, left, top, canvas)
    if content is None:
        return None

    if frame is None:
        box = content if layer.trim == "ws" else (0, 0, *canvas)
        if layer.trim == "mask":
            mask = layer.item.layer.mask
            box = (max(mask.left, 0), max(mask.top, 0), min(mask.right, canvas[0]), min(mask.bottom, canvas[1]))
            if box[0] >= box[2] or box[1] >= box[3]:
                box = content
        frame = Frame(box, layer.scale, layer.rotate, None, (0.0, 0.0), layer.pad)
        trim_rotated = layer.trim == "ws"
    else:
        trim_rotated = False

    l, t, r, b = frame.box
    image = image.crop((l - left, t - top, r - left, b - top))
    factor = scale * frame.scale
    if factor != 1:
        size = (max(1, round(image.width * factor)), max(1, round(image.height * factor)))
        image = image.convert("RGBa").resize(size, Image.LANCZOS).convert("RGBA")
    if frame.rotate % 360:
        image = image.convert("RGBa").rotate(frame.rotate, Image.BICUBIC, expand=True).convert("RGBA")
        crop = frame.crop
        if crop is None and trim_rotated:
            crop = image.getchannel("A").getbbox()
        if crop is not None:
            shift = ((crop[0] + crop[2] - image.width) / 2, (crop[1] + crop[3] - image.height) / 2)
            image = image.crop(crop)
            frame = Frame(frame.box, frame.scale, frame.rotate, crop, shift, frame.pad)
    if frame.pad:
        image = ImageOps.expand(image, border=frame.pad, fill=(0, 0, 0, 0))
    return Rendered(image, frame, content)


def center(frame: Frame, origin: tuple[float, float], scale: float) -> tuple[float, float]:
    """The image centre in skeleton coordinates (y-up, origin at `origin`)."""
    l, t, r, b = frame.box
    x, y = ((l + r) / 2 - origin[0]) * scale, (origin[1] - (t + b) / 2) * scale
    if frame.crop is not None:
        dx, dy = rotate((frame.shift[0], -frame.shift[1]), -frame.rotate)
        x, y = x + dx / frame.scale, y + dy / frame.scale
    return x, y


def rotate(point: tuple[float, float], degrees: float) -> tuple[float, float]:
    a = math.radians(degrees)
    return point[0] * math.cos(a) - point[1] * math.sin(a), point[0] * math.sin(a) + point[1] * math.cos(a)


def _overlay(base: Image.Image, bx: int, by: int, over: Image.Image, ox: int, oy: int, blend: str | None) -> Image.Image:
    """Draw `over` on `base` clipped to base's pixels, like a Photoshop clipping mask."""
    placed = Image.new("RGBA", base.size, (0, 0, 0, 0))
    placed.paste(over, (ox - bx, oy - by))
    b = np.asarray(base, dtype=np.float32) / 255
    o = np.asarray(placed, dtype=np.float32) / 255
    bc, oc, ba = b[..., :3], o[..., :3], b[..., 3:]
    oa = o[..., 3:] * ba
    if blend == "multiply":
        oc = bc * oc
    elif blend == "screen":
        oc = 1 - (1 - bc) * (1 - oc)
    elif blend == "additive":
        oc = np.minimum(bc + oc, 1)
    out = np.concatenate([bc * (1 - oa) + oc * oa, ba], axis=-1)
    return Image.fromarray(np.round(out * 255).astype(np.uint8), "RGBA")
