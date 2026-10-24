"""FastMCP server: build, inspect, check and edit Spine 4.2 skeletons."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .edits import edit_skeleton, report
from .ops import animation as anim_ops
from .ops import attachments as att_ops
from .ops import constraints as con_ops
from .ops import rig as rig_ops
from . import spine_cli
from .pipeline import import_psd_to_spine as _import
from .preview import preview_html as _preview
from .runtime import runtime_check
from .spine import document as d
from .spine.check import check, summary
from .spine.inspect import inspect
from .spine.presets import LOOPING, NAMES as PRESET_NAMES

mcp = FastMCP(
    "spine2d-mcp",
    instructions=(
        "Builds and edits Spine 4.2 skeleton JSON (spine-phaser-v4 4.2.x runtime). "
        "Start from a layered PSD with import_psd_to_spine, or from an existing 4.2 JSON. "
        "Call inspect_skeleton before editing to learn bone, slot and animation names. "
        "Every editing tool checks the result against the 4.2 format and refuses to write "
        "a change that introduces errors; run validate_skeleton after a series of edits."
    ),
)


@mcp.tool()
def import_psd_to_spine(psd_path: str, out_dir: str | None = None) -> dict:
    """Convert a PSD character into a Spine 4.2 project (skeleton JSON + atlas
    + PNG page(s) + per-part images) importable into the Spine editor and Unity.

    Args:
        psd_path: Absolute path to the source .psd file.
        out_dir: Output directory. Defaults to "<psd_dir>/<psd_stem>_spine".

    Returns:
        Paths to the generated project.json, .atlas file, PNG page(s), and the
        images/ folder the Spine editor reads part images from.
    """
    src = Path(psd_path)
    if out_dir is None:
        out_dir = str(src.parent / f"{src.stem}_spine")

    result = _import(psd_path, out_dir)
    return {
        "project_path": str(result.project_path),
        "atlas_path": str(result.atlas_path),
        "png_paths": [str(p) for p in result.png_paths],
        "images_dir": str(result.images_dir),
    }


@mcp.tool()
def inspect_skeleton(project_path: str) -> dict:
    """Describe a Spine skeleton JSON: bones (parent, setup transform, world
    position), slots in draw order, skins and attachments, constraints, events,
    and for each animation its duration and the bones/slots it keys.

    Args:
        project_path: Absolute path to the skeleton .json.
    """
    return inspect(d.load(project_path))


@mcp.tool()
def validate_skeleton(
    project_path: str,
    loops: list[str] | None = None,
    runtime: bool = False,
    atlas_path: str | None = None,
) -> dict:
    """Check a skeleton JSON against the Spine 4.2 format: references, meshes,
    constraints, and animation keys (rotate `value` not `angle`, curve lengths,
    key order, events). Errors break loading or render wrongly; warnings are
    probably unintended.

    With runtime=true it also loads the skeleton and atlas in spine-core
    4.2.120 (the runtime spine-phaser-v4 4.2.x uses) via Node and steps every
    animation; spine-core is installed into a cache on first use.

    Args:
        project_path: Absolute path to the skeleton .json.
        loops: Animation names meant to loop; each is checked for a pop where
            its last key differs from its first.
        runtime: Also run the spine-core runtime check.
        atlas_path: Atlas for the runtime check; default <skeleton>.atlas.
    """
    doc = d.load(project_path)
    findings = check(doc, loops)
    result = {"summary": summary(doc), **report(findings)}
    ok = not result["errors"]
    if runtime:
        result["runtime"] = runtime_check(project_path, atlas_path)
        ok = ok and result["runtime"].get("ok", True)
    return {"ok": ok, **result}


@mcp.tool()
def add_animation(
    project_path: str,
    name: str,
    preset: str | None = None,
    timelines: dict | None = None,
    bone_map: dict[str, str] | None = None,
    side: str = "right",
    speed: float = 1.0,
    amount: float = 1.0,
    loop: bool | None = None,
    replace: bool = False,
    out_path: str | None = None,
) -> dict:
    """Add an animation from a preset, from keyframes, or both (keyframes
    override the preset per bone/slot timeline).

    Presets (tuned for rigs from import_psd_to_spine, scaled to the skeleton's
    height): idle (2.0 s loop), walk (0.8 s in-place front-view loop), wave
    (2.5 s), jump (1.3 s).

    `timelines` is Spine 4.2 animation JSON plus an easing shorthand:
    {"bones": {"head": {"rotate": [{"time": 0, "value": 0, "ease": "inout"},
                                    {"time": 0.5, "value": 8}],
                        "translate": [{"time": 0, "x": 0, "y": 0, "ease": "out"}, ...],
                        "scale": [...]}},
     "slots": {"mouth": {"attachment": [{"time": 0, "name": "mouth_open"}],
                         "rgba": [{"time": 0, "color": "ffffffff", "ease": "linear"}, ...]}},
     "events": [{"time": 0.3, "name": "footstep"}]}
    A key's "ease" shapes the segment from that key to the next: linear,
    stepped, in, out, inout, smooth, back, or [x1, y1, x2, y2] (CSS
    cubic-bezier). Rotations use "value" (degrees, CCW); times are seconds.
    Event names are defined automatically.

    Args:
        project_path: Absolute path to the skeleton .json.
        name: Animation name.
        preset: idle, walk, wave or jump.
        timelines: Keyframes as above.
        bone_map: Preset bone name -> this rig's bone name, for rigs not built
            by import_psd_to_spine (e.g. {"arm_right": "R_arm"}).
        side: Which arm waves: "right" or "left" (wave only).
        speed: >1 plays the preset faster.
        amount: Scales the preset's motion (0.5 = half as big).
        loop: Check the animation for a pop at the wrap (default: true for
            idle and walk).
        replace: Overwrite an existing animation of the same name.
        out_path: Write to this path instead of editing in place.
    """
    loops = [name] if (loop if loop is not None else preset in LOOPING) else None
    return edit_skeleton(
        project_path,
        lambda doc: anim_ops.add_animation(
            doc, name, preset, timelines, bone_map, side, speed, amount, replace
        ),
        out_path,
        loops,
    )


@mcp.tool()
def remove_animation(project_path: str, name: str, out_path: str | None = None) -> dict:
    """Delete an animation from the skeleton.

    Args:
        project_path: Absolute path to the skeleton .json.
        name: Animation to remove.
        out_path: Write to this path instead of editing in place.
    """
    return edit_skeleton(project_path, lambda doc: anim_ops.remove_animation(doc, name), out_path)


@mcp.tool()
def edit_rig(project_path: str, operations: list[dict], out_path: str | None = None) -> dict:
    """Edit the setup pose: a list of operations applied in order, written
    only if the result is still a valid 4.2 skeleton.

    Operations ("op" plus its fields; x/y in skeleton units, y up, degrees CCW):
    - add_bone: name, parent, x, y [, rotation, scaleX, scaleY, shearX, shearY,
      length, inherit, color, world=true to give x/y in world space]
    - set_bone: name [, parent (keeps the world position), x, y, rotation, ...]
    - move_pivot: name, x, y (world-space delta) or to=[x, y] (world point);
      the bone's art, children and weighted vertices stay where they are
    - rename_bone: name, to (updates slots, constraints, animations)
    - remove_bone: name (only if no children, slots or constraints use it)
    - add_slot: name, bone [, attachment, index, color, dark, blend]
    - set_slot: name [, bone (keeps attachments in place), attachment, color,
      dark, blend]
    - remove_slot: name
    - set_draw_order: order=[every slot, back to front] or slot + index

    Args:
        project_path: Absolute path to the skeleton .json.
        operations: e.g. [{"op": "move_pivot", "name": "arm_left", "x": 0, "y": -20}].
        out_path: Write to this path instead of editing in place.
    """
    return edit_skeleton(project_path, lambda doc: rig_ops.edit_rig(doc, operations), out_path)


@mcp.tool()
def add_constraint(
    project_path: str,
    type: str,
    name: str,
    bones: list[str] | None = None,
    bone: str | None = None,
    target: str | None = None,
    target_at: str | list[float] | None = None,
    target_parent: str | None = None,
    preset: str | None = None,
    params: dict | None = None,
    order: int | None = None,
    replace: bool = False,
    out_path: str | None = None,
) -> dict:
    """Add an IK, transform or physics constraint (Spine 4.2).

    - ik: bones (1 or 2, parent then child), target bone; params mix,
      softness, bendPositive, compress, stretch, uniform. IK aims the first
      bone's x axis at the target, so it suits bones that point along their
      limbs; the result warns when the setup pose would swing.
    - transform: bones, target; params rotation, x, y, scaleX, scaleY, shearY
      (offsets), mixRotate, mixX, mixY, mixScaleX, mixScaleY, mixShearY, local,
      relative.
    - physics: bone; preset hair, cloth, tail, antenna or jiggle, and/or params
      x, y, rotate, scaleX, shearX (0..1: what it moves), inertia, strength,
      damping, mass, wind, gravity, mix, limit, fps.

    Args:
        project_path: Absolute path to the skeleton .json.
        type: ik, transform or physics.
        name: Constraint name.
        bones: Constrained bones (ik, transform).
        bone: Simulated bone (physics).
        target: Target bone (ik, transform). Created if missing and target_at is given.
        target_at: Where to create a missing target: a bone name (its world
            position) or [x, y] world point.
        target_parent: Parent for a created target (default: the root bone).
        preset: Physics preset name.
        params: Other fields, as listed above.
        order: Apply order among all constraints (default: last).
        replace: Replace a constraint of the same name, keeping its timelines.
        out_path: Write to this path instead of editing in place.
    """
    return edit_skeleton(
        project_path,
        lambda doc: con_ops.add_constraint(
            doc, type, name, bones, bone, target, target_at, target_parent, preset, order, replace,
            **(params or {}),
        ),
        out_path,
    )


@mcp.tool()
def remove_constraint(project_path: str, name: str, out_path: str | None = None) -> dict:
    """Remove a constraint by name, with its skin entries and animation timelines.

    Args:
        project_path: Absolute path to the skeleton .json.
        name: Constraint to remove.
        out_path: Write to this path instead of editing in place.
    """
    return edit_skeleton(project_path, lambda doc: con_ops.remove_constraint(doc, name), out_path)


@mcp.tool()
def add_attachment(
    project_path: str,
    slot: str,
    image_path: str,
    name: str | None = None,
    skin: str = "default",
    x: float = 0.0,
    y: float = 0.0,
    rotation: float = 0.0,
    world: bool = False,
    set_as_setup: bool = False,
    replace: bool = False,
    atlas_path: str | None = None,
) -> dict:
    """Add a PNG as a region attachment on a slot (e.g. a mouth shape or an
    alternate hand to swap with an attachment timeline), copy it into the
    skeleton's images folder and repack the atlas.

    Args:
        project_path: Absolute path to the skeleton .json (edited in place).
        slot: Slot to attach to (add one first with edit_rig add_slot if needed).
        image_path: Absolute path to the PNG.
        name: Attachment name; default the PNG's file name without extension.
        skin: Skin to add it to (created if missing).
        x, y, rotation: Image centre and angle relative to the slot's bone, or
            world-space with world=true.
        set_as_setup: Show it in the setup pose (sets the slot's attachment).
        replace: Overwrite an attachment of the same name.
        atlas_path: Atlas to rewrite; default <skeleton>.atlas next to the JSON.
    """
    size = att_ops.image_size(image_path)
    name = name or Path(image_path).stem
    result = edit_skeleton(
        project_path,
        lambda doc: att_ops.add_region(doc, slot, name, size, skin, x, y, rotation,
                                       world=world, set_as_setup=set_as_setup, replace=replace),
    )
    doc = d.load(project_path)
    result["image"] = str(att_ops.copy_image(image_path, att_ops.images_dir(project_path, doc), name))
    result["atlas"] = att_ops.repack(project_path, atlas_path)
    return result


@mcp.tool()
def pack_atlas(project_path: str, atlas_path: str | None = None) -> dict:
    """Repack the atlas from the skeleton's images folder (one PNG per
    attachment path), e.g. after editing part images.

    Args:
        project_path: Absolute path to the skeleton .json.
        atlas_path: Atlas to write; default <skeleton>.atlas next to the JSON.
    """
    return att_ops.repack(project_path, atlas_path)


@mcp.tool()
def spine_import(
    project_path: str,
    spine_project: str | None = None,
    skeleton_name: str | None = None,
    editor_version: str | None = None,
) -> dict:
    """Import a skeleton JSON into a .spine project with the Spine editor's
    CLI (needs Spine installed; set SPINE_EXECUTABLE if it is not in the
    standard place). The editor's reader reports anything it cannot load.

    Args:
        project_path: Absolute path to the skeleton .json.
        spine_project: .spine file to create or import into; default next to the JSON.
        skeleton_name: Name of the imported skeleton; default the JSON's name.
        editor_version: Spine editor version to run (e.g. a 4.2.x release);
            default $SPINE_EDITOR_VERSION or the launcher's default. Saving a
            project in a newer editor upgrades it.
    """
    return spine_cli.spine_import(project_path, spine_project, skeleton_name, editor_version)


@mcp.tool()
def spine_export(
    input_path: str,
    output_dir: str,
    format: str = "png",
    animations: list[str] | None = None,
    fps: int = 30,
    scale: float = 100,
    background: str | None = "ffffffff",
    editor_version: str | None = None,
) -> dict:
    """Render or export with the Spine editor's CLI.

    - png: one PNG per frame for each animation, in output_dir/frames.
    - gif: one looping GIF per animation (encoded from the PNG frames).
    - json / binary: skeleton data as that editor version writes it; for
      spine-phaser-v4 4.2.x run a 4.2 editor (editor_version), or the data
      will be a newer version the runtime cannot load.

    Args:
        input_path: Skeleton .json or .spine project.
        output_dir: Folder to write into.
        format: png, gif, json or binary.
        animations: Only these animations (png/gif); default all.
        fps: Frames per second (png/gif).
        scale: Percent (png/gif).
        background: RRGGBB or RRGGBBAA, or null for transparent PNGs.
        editor_version: Spine editor version to run; default
            $SPINE_EDITOR_VERSION or the launcher's default.
    """
    return spine_cli.spine_export(input_path, output_dir, format, animations, fps, scale, background, editor_version)


@mcp.tool()
def preview_html(
    project_path: str,
    atlas_path: str | None = None,
    out_path: str | None = None,
    animation: str | None = None,
) -> dict:
    """Write a self-contained HTML page that plays the skeleton in the official
    Spine Web Player 4.2 (skeleton, atlas and images embedded; the player
    script loads from unpkg). Its controls switch animations and skins.

    Args:
        project_path: Absolute path to the skeleton .json.
        atlas_path: Atlas; default <skeleton>.atlas next to the JSON.
        out_path: HTML file to write; default <skeleton>_preview.html.
        animation: Animation to start with; default the first.
    """
    path = _preview(project_path, atlas_path, out_path, animation)
    return {"preview_path": str(path), "player": "@esotericsoftware/spine-player@4.2.120"}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
