"""Convert parsed PSD layers into a SpineProject plus an atlas layout.

Coordinate handling (the parts the old prototype got wrong):
- PSD is y-down, top-left origin. Spine is y-up. Convert any PSD point with
  `spine_y = psd_height - psd_y`.
- A bone's x/y in JSON are LOCAL to its parent. With inherit="normal" and no
  rotation in the setup pose, local offset = child_world - parent_world.
  (The old code wrote absolute coords for child bones — a format bug.)
- A bone's pivot is the joint that connects it to its parent, NOT the layer
  center: the edge of the bbox facing the parent (see conventions.joint_side),
  or an explicit `[pivot]` marker. This makes rotation anatomically correct.
- The region attachment is offset from the bone so the image stays put:
  attachment offset = image_center_world - bone_pivot_world.
- The skeleton origin (root) is the bottom-center of the character's bounds,
  not the PSD canvas corner, so it is placed by its feet in Spine and Unity.
  Pivots are computed in canvas space; only root-level bones are offset.

Draw order (A1): slots are emitted in PSD z-order (bottom-to-top), which is the
Spine draw order — independent of bone creation order.

Virtual bones (A2): intermediate spine bones (hip/torso/chest/neck) with no
layer are created as positioned parents with no slot/attachment, so multi-
segment animations transfer onto skeletons built from sparse PSDs.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..atlas.packer import AtlasLayout, Region, pack
from ..psd import conventions as conv
from ..psd.parser import ParsedPsd, PsdLayer
from .schema import Bone, RegionAttachment, Skin, Slot, SpineProject
from .validator import validate


@dataclass
class BuildResult:
    project: SpineProject
    atlas: AtlasLayout
    images: dict[str, PsdLayer]  # region name -> source layer (for atlas paste)


def build(parsed: ParsedPsd, max_atlas_size: int = 2048) -> BuildResult:
    # Map canonical part -> layer, keeping each layer's z-index for draw order.
    parts: dict[str, PsdLayer] = {}
    part_z: dict[str, int] = {}
    for z, layer in enumerate(parsed.layers):
        part = conv.canonical_part(layer.name)
        if part is not None and part not in parts:
            parts[part] = layer
            part_z[part] = z

    if not parts:
        raise ValueError(
            "No recognized body-part layers found. Expected names like "
            "head, body, arm_left, leg_right, etc."
        )

    present = set(parts)
    chain = conv.resolve_chain(present)  # includes virtual intermediates

    # World pivots for every bone in the chain (present + virtual).
    world = _compute_pivots(chain, present, parts, parsed)

    # Origin: bottom-center of the character (y-up canvas space).
    left, top, right, bottom = _parts_bounds(parts.values())
    ox, oy = (left + right) / 2.0, float(parsed.height - bottom)

    bones: list[Bone] = [Bone(name=conv.ROOT_BONE)]
    for part in chain:
        parent = conv.parent_of(part)
        px, py = world[part]
        if parent == conv.ROOT_BONE:
            lx, ly = px - ox, py - oy
        else:
            ppx, ppy = world[parent]
            lx, ly = px - ppx, py - ppy
        bones.append(Bone(name=part, parent=parent, x=lx, y=ly))

    # Slots in PSD z-order (draw order); only present parts get a slot.
    slots: list[Slot] = []
    attachments: dict[str, dict[str, RegionAttachment]] = {}
    for part in sorted(present, key=lambda p: part_z[p]):
        layer = parts[part]
        slots.append(Slot(name=part, bone=part, attachment=part))
        # Attachment offset so the image stays where it was in the PSD.
        icx = layer.left + layer.width / 2.0
        icy = parsed.height - (layer.top + layer.height / 2.0)
        bx, by = world[part]
        attachments[part] = {
            part: RegionAttachment(
                width=float(layer.width),
                height=float(layer.height),
                x=icx - bx,
                y=icy - by,
            )
        }

    project = SpineProject(
        bones=bones,
        slots=slots,
        skins=[Skin(name="default", attachments=attachments)],
    )
    # Setup-pose bounds relative to the origin (the feet are at y=0).
    project.skeleton.x = left - ox
    project.skeleton.y = 0.0
    project.skeleton.width = float(right - left)
    project.skeleton.height = float(bottom - top)

    validate(project)

    regions = [
        Region(name=part, width=parts[part].width, height=parts[part].height)
        for part in present
    ]
    atlas = pack(regions, max_size=max_atlas_size)
    return BuildResult(
        project=project,
        atlas=atlas,
        images={part: parts[part] for part in present},
    )


def _parts_bounds(layers) -> tuple[int, int, int, int]:
    """Union bbox (left, top, right, bottom) of the layers, PSD y-down."""
    layers = list(layers)
    return (
        min(l.left for l in layers),
        min(l.top for l in layers),
        max(l.left + l.width for l in layers),
        max(l.top + l.height for l in layers),
    )


def _compute_pivots(
    chain: list[str],
    present: set[str],
    parts: dict[str, PsdLayer],
    parsed: ParsedPsd,
) -> dict[str, tuple[float, float]]:
    """World-space (Spine y-up) pivot for each bone in the chain."""
    world: dict[str, tuple[float, float]] = {}
    for part in chain:
        if part in present:
            world[part] = _part_pivot(part, parts[part], parsed)
    # Virtual intermediates: place between nearest present ancestor and the
    # average of present direct descendants (or ancestor if none).
    for part in chain:
        if part in world:
            continue
        world[part] = _virtual_pivot(part, chain, world, parsed)
    return world


def _part_pivot(
    part: str, layer: PsdLayer, parsed: ParsedPsd
) -> tuple[float, float]:
    # Explicit marker wins.
    if part in parsed.pivot_markers:
        mx, my = parsed.pivot_markers[part]
        return mx, parsed.height - my

    side = conv.joint_side(part)
    cx = layer.left + layer.width / 2.0
    if side is None:
        cy = layer.top + layer.height / 2.0
        return cx, parsed.height - cy
    # PSD y-down: "top" edge is layer.top, "bottom" edge is layer.top+height.
    if side == "top":
        cy = layer.top
    elif side == "bottom":
        cy = layer.top + layer.height
    elif side == "left":
        cx, cy = layer.left, layer.top + layer.height / 2.0
    elif side == "right":
        cx, cy = layer.left + layer.width, layer.top + layer.height / 2.0
    else:
        cy = layer.top + layer.height / 2.0
    return cx, parsed.height - cy


def _virtual_pivot(
    part: str,
    chain: list[str],
    world: dict[str, tuple[float, float]],
    parsed: ParsedPsd,
) -> tuple[float, float]:
    # Nearest present ancestor.
    anc = conv.parent_of(part)
    while anc != conv.ROOT_BONE and anc not in world:
        anc = conv.parent_of(anc)
    anc_pt = world.get(anc)

    # Present direct children of this part.
    children = [
        c for c in chain if conv.parent_of(c) == part and c in world
    ]
    if children:
        cx = sum(world[c][0] for c in children) / len(children)
        cy = sum(world[c][1] for c in children) / len(children)
        if anc_pt is not None:
            return (anc_pt[0] + cx) / 2.0, (anc_pt[1] + cy) / 2.0
        return cx, cy
    if anc_pt is not None:
        return anc_pt
    return parsed.width / 2.0, parsed.height / 2.0
