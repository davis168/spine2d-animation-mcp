"""FastMCP server: build, inspect, check, edit and convert Spine skeletons in
format 4.2 or 4.3, each in its own version."""

from __future__ import annotations

import functools
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import project as proj
from . import spine_cli
from .conversion import convert_file
from .edits import EditError, edit_skeleton, report
from .motion import analyze_motion as _analyze_motion
from .ops import animation as anim_ops
from .ops import attachments as att_ops
from .ops import constraints as con_ops
from .ops import meshes as mesh_ops
from .ops import rig as rig_ops
from .ops import shapes as shape_ops
from .ops import skins as skin_ops
from .onion import onion_skin as _onion_skin
from .pipeline import import_psd_to_spine as _import
from .preview import phaser_html as _phaser
from .preview import player_version
from .preview import preview_html as _preview
from .runtime import runtime_check
from .spine import document as d
from .spine import versions
from .spine.check import check, summary
from .spine.inspect import inspect
from .spine.presets import LOOPING

mcp = FastMCP(
    "spine2d-mcp",
    instructions=(
        "Builds, edits, checks and converts Spine skeleton JSON in format version 4.2 or 4.3 (the "
        "major.minor in skeleton.spine). Spine runtimes load only their own format version, so every "
        "skeleton is edited in the version it is written in, and a game project's version comes from its "
        "@esotericsoftware/spine-* dependency or a .spine-version file. Call inspect_skeleton before editing "
        "to learn names and versions. Every editing tool checks the result against the file's format and "
        "refuses changes that introduce errors; run validate_skeleton after a series of edits. To judge how an "
        "animation moves (smooth, pose to pose, natural), run analyze_motion and look at onion_skin images of what "
        "it flags before changing keys.\n"
        "When a result has needs_answer, do not guess: ask the user its question with its options in chat "
        "(say which is recommended), wait for the answer, then do what the chosen option says. This happens "
        "when a game project's version is unknown or unclear, when a file's version differs from its game "
        "project's, and when a conversion would lose something. If the user already said which version to "
        "use, that is the answer: pass format_version (and allow_version_mismatch=true if it differs from the "
        "game project's), mention the difference once, and do not ask again. To work in another version than "
        "a file is in, convert_skeleton it first, then edit the converted file."
    ),
)


def _asks(tool):
    """Return a NeedsAnswer as the tool's result: a question for the user."""
    @functools.wraps(tool)
    def wrapper(*args, **kwargs):
        try:
            return tool(*args, **kwargs)
        except proj.NeedsAnswer as question:
            return question.result()
    return wrapper


@mcp.tool()
@_asks
def import_psd_to_spine(
    psd_path: str,
    out_dir: str | None = None,
    format_version: str | None = None,
    allow_version_mismatch: bool = False,
    save_project_version: bool = False,
    scale: float = 1.0,
    padding: int = 1,
    trim: bool = True,
    ignore_hidden: bool = False,
) -> dict:
    """Convert a PSD into a Spine project (skeleton JSON + atlas + PNG page(s)
    + one image per attachment under images/) importable into the Spine editor.

    A PSD whose layer names use Spine's Import PSD tags ([bone], [slot],
    [skin], [folder], [mesh], [mesh:name], [merge], [origin], [overlay],
    [ignore], [scale], [rotate], [pad], [trim], [path], [bones], [slots],
    [name:*] patterns, ...) is imported like the Spine editor's Import PSD:
    nested [bone] groups become bones, layers go in slots in layer order,
    [skin] groups become skins and placeholders, and hidden layers become
    attachments that are not shown (importer "spine-tags"). An untagged PSD
    whose layers are named after body parts (head, torso, arm_left, ...) is
    auto-rigged instead (importer "body-parts"); any other untagged PSD gets
    one slot per layer. Invalid or contradictory tags are refused with every
    problem listed.

    The format version is format_version if given, else the game project's
    (from out_dir: its @esotericsoftware/spine-* dependency or .spine-version),
    else $SPINE_FORMAT_VERSION; otherwise the result asks (needs_answer).

    Args:
        psd_path: Absolute path to the source .psd file.
        out_dir: Output directory. Defaults to "<psd_dir>/<psd_stem>_spine".
        format_version: "4.2" or "4.3".
        allow_version_mismatch: Use format_version even though the game project
            uses another version (only when the user said so).
        save_project_version: Also save format_version as the game project's
            .spine-version (when the project did not say).
        scale: Import PSD's Scale: images and the skeleton are scaled by it.
        padding: Import PSD's Padding: transparent pixels around each image.
        trim: Import PSD's Trim whitespace; false makes every image canvas-sized.
        ignore_hidden: Import PSD's Ignore hidden layers; by default hidden
            layers are imported as attachments the setup pose does not show.
    """
    src = Path(psd_path)
    if out_dir is None:
        out_dir = str(src.parent / f"{src.stem}_spine")
    version, game, notes = proj.for_new_file(out_dir, format_version, allow_version_mismatch, save_project_version)
    result = _import(psd_path, out_dir, format_version=version, scale=scale, padding=padding, trim=trim,
                     ignore_hidden=ignore_hidden)
    if save_project_version:
        notes.append(proj.save_declaration(game, version))
    return {
        "project_path": str(result.project_path),
        "atlas_path": str(result.atlas_path),
        "png_paths": [str(p) for p in result.png_paths],
        "images_dir": str(result.images_dir),
        "importer": result.importer,
        "summary": summary(d.load(result.project_path)),
        "format_version": version,
        "version": game.note(version),
        **({"warnings": result.warnings} if result.warnings else {}),
        **({"notes": notes} if notes else {}),
    }


@mcp.tool()
def inspect_skeleton(project_path: str) -> dict:
    """Describe a Spine skeleton JSON: bones (parent, setup transform, world
    position), slots in draw order, skins and attachments, constraints in apply
    order, events, and for each animation its duration and what it keys. Also
    its format version and the game project's (and where that comes from).

    Args:
        project_path: Absolute path to the skeleton .json.
    """
    doc = d.load(project_path)
    try:
        info = inspect(doc)
    except (AttributeError, TypeError, KeyError, ValueError):
        errors = [str(f) for f in check(doc) if f.level == "error"][:10]
        raise ValueError("cannot inspect a malformed skeleton; validate_skeleton reports:\n"
                         + "\n".join(errors)) from None
    info["version"] = proj.detect(project_path).describe(versions.of(doc))
    if not versions.is_supported(versions.of(doc)):
        info["version"]["note"] = (f"format version {versions.of(doc)!r} is not supported "
                                   f"({versions.supported_text()}); this is a best-effort reading")
    return info


@mcp.tool()
def validate_skeleton(
    project_path: str,
    loops: list[str] | None = None,
    runtime: bool = False,
    atlas_path: str | None = None,
) -> dict:
    """Check a skeleton JSON against its format version's reader (4.2 or 4.3):
    references, meshes, constraints, animation keys, keys only the other
    version reads, and whether its version matches the game project's. Errors
    break loading or render wrongly; warnings are probably unintended.

    With runtime=true it also loads the skeleton and atlas in spine-core via
    Node and steps every animation: the release the game project has
    installed, else the tested release of the file's format version (4.2.120
    or 4.3.13); spine-core is installed into a cache on first use.

    Args:
        project_path: Absolute path to the skeleton .json.
        loops: Animation names meant to loop; each is checked for a pop where
            its last key differs from its first.
        runtime: Also run the spine-core runtime check.
        atlas_path: Atlas for the runtime check; default <skeleton>.atlas.
    """
    doc = d.load(project_path)
    version = versions.of(doc)
    game = proj.detect(project_path)
    result = {"summary": summary(doc), **report(check(doc, loops)), "version": game.describe(version)}
    if versions.is_supported(version) and game.version and game.version != version:
        result["errors"].insert(0, (
            f"ERROR skeleton.spine: a {version} file, but the game project uses {game.version} ({game.source}), "
            f"so the game cannot load it; convert_skeleton(to_version={game.version!r}) converts it"))
    elif game.unclear:
        result["warnings"].append(f"WARNING game project: its format version is unclear: {game.problem}")
    ok = not result["errors"]
    if runtime:
        # The game's own spine-core release when it has one installed for this format version.
        release = game.runtime_release if versions.major_minor(game.runtime_release or "") == version else None
        result["runtime"] = runtime_check(project_path, atlas_path, release)
        if release:
            result["runtime"]["release_from"] = "the game project's installed runtime"
        ok = ok and result["runtime"].get("ok", True)
    return {"ok": ok, **result}


@mcp.tool()
@_asks
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
    allow_version_mismatch: bool = False,
    save_project_version: bool = False,
) -> dict:
    """Add an animation from a preset, from keyframes, or both (keyframes
    override the preset per bone/slot timeline).

    Presets (tuned for rigs from import_psd_to_spine, scaled to the skeleton's
    height): idle (2.0 s loop), walk (0.8 s in-place front-view loop), wave
    (2.5 s), jump (1.3 s).

    `timelines` is Spine animation JSON (in the skeleton's format version) plus
    an easing shorthand:
    {"bones": {"head": {"rotate": [{"time": 0, "value": 0, "ease": "inout"},
                                    {"time": 0.5, "value": 8}],
                        "translate": [{"time": 0, "x": 0, "y": 0, "ease": "out"}, ...],
                        "scale": [...]}},
     "slots": {"mouth": {"attachment": [{"time": 0, "name": "mouth_open"}],
                         "rgba": [{"time": 0, "color": "ffffffff", "ease": "linear"}, ...]}},
     "slider": {"blink": {"time": [{"time": 0, "value": 0}, ...]}},   (4.3)
     "drawOrder": [{"time": 0.2, "order": [every slot, back to front]}],
     "drawOrderFolder": {"face": [{"time": 0.2,                         (4.3)
                                   "order": ["face/mouth", "face/eye"]}]},
     "events": [{"time": 0.3, "name": "footstep"}]}
    drawOrderFolder keys a draw order folder (the slots named "face/..."; see
    edit_rig rename_slot): its order lists all the folder's slots.
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
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
        save_project_version: Save the file's version as the game project's
            .spine-version (when the project's version was unclear).
    """
    loops = [name] if (loop if loop is not None else preset in LOOPING) else None
    return edit_skeleton(
        project_path,
        lambda doc: anim_ops.add_animation(
            doc, name, preset, timelines, bone_map, side, speed, amount, replace
        ),
        out_path,
        loops,
        allow_version_mismatch,
        save_project_version,
    )


@mcp.tool()
@_asks
def remove_animation(project_path: str, name: str, out_path: str | None = None,
                     allow_version_mismatch: bool = False, save_project_version: bool = False) -> dict:
    """Delete an animation from the skeleton (refused while a 4.3 slider plays it).

    Args:
        project_path: Absolute path to the skeleton .json.
        name: Animation to remove.
        out_path: Write to this path instead of editing in place.
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
        save_project_version: Save the file's version as the game project's
            .spine-version (when the project's version was unclear).
    """
    return edit_skeleton(project_path, lambda doc: anim_ops.remove_animation(doc, name), out_path,
                         allow_version_mismatch=allow_version_mismatch,
                         save_project_version=save_project_version)


@mcp.tool()
@_asks
def edit_rig(project_path: str, operations: list[dict], out_path: str | None = None,
             allow_version_mismatch: bool = False, save_project_version: bool = False) -> dict:
    """Edit the setup pose: a list of operations applied in order, written
    only if the result is still a valid skeleton of its format version.

    Operations ("op" plus its fields; x/y in skeleton units, y up, degrees CCW):
    - add_bone: name, parent, x, y [, rotation, scaleX, scaleY, shearX, shearY,
      length, inherit, color, icon, visible, iconSize and iconRotation (4.3),
      world=true to give x/y in world space]
    - set_bone: name [, parent (keeps the world position), x, y, rotation, ...]
    - move_pivot: name, x, y (world-space delta) or to=[x, y] (world point);
      the bone's art, children and weighted vertices stay where they are
    - rename_bone: name, to (updates slots, constraints, animations)
    - remove_bone: name (only if no children, slots or constraints use it)
    - add_slot: name, bone [, attachment, index, color, dark, blend, visible]
    - set_slot: name [, bone (keeps attachments in place), attachment, color,
      dark, blend, visible]
    - rename_slot: name, to (updates skins, constraints, clips, animations);
      a "/" puts the slot in a draw order folder (head -> face/head), whose slots
      stay next to each other in the draw order
    - rename_folder: name, to (renames the folder's slots; to="" takes them
      out of it)
    - remove_slot: name
    - set_draw_order: order=[every slot, back to front] or slot + index
    - set_event: name [, int, float, string, audio (path in the skeleton's
      audio folder), volume, balance]: define an event or its defaults
    - remove_event: name (only if no animation keys it)
    - set_skeleton: referenceScale (physics distances are relative to it),
      fps (the editor's), images, audio (folders relative to the JSON)
    Draw order keys keep showing the same order when slots are added,
    removed, renamed or reordered (a folder whose slots change says so: before
    its first key it shows its new setup order). Renamed slots need renaming in game code
    that looks them up by name.

    Args:
        project_path: Absolute path to the skeleton .json.
        operations: e.g. [{"op": "move_pivot", "name": "arm_left", "x": 0, "y": -20}].
        out_path: Write to this path instead of editing in place.
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
        save_project_version: Save the file's version as the game project's
            .spine-version (when the project's version was unclear).
    """
    return edit_skeleton(project_path, lambda doc: rig_ops.edit_rig(doc, operations), out_path,
                         allow_version_mismatch=allow_version_mismatch,
                         save_project_version=save_project_version)


@mcp.tool()
@_asks
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
    allow_version_mismatch: bool = False,
    save_project_version: bool = False,
) -> dict:
    """Add an IK, transform, path, physics or (4.3) slider constraint, written
    in the skeleton's format version. Options one version names differently are
    translated; an option only 4.3 has is refused on a 4.2 skeleton.

    - ik: bones (1 or 2, parent then child), target bone; params mix,
      softness, bendPositive, compress, stretch, and scaleY "none", "uniform"
      or "volume" (4.3; 4.2 has uniform=true only). IK aims the first bone's x
      axis at the target, so it suits bones that point along their limbs; the
      result warns when the setup pose would swing.
    - transform: bones, target (the bone that drives them; 4.3 writes it as
      `source`); params rotation, x, y, scaleX, scaleY, shearY (offsets),
      mixRotate, mixX, mixY, mixScaleX, mixScaleY, mixShearY, local, relative
      (4.3 also: localSource, localTarget, additive, clamp, and properties
      mapping source properties to target properties, e.g. {"rotate": {"to":
      {"x": {"scale": 2}}}}; default: each property drives itself).
    - path: bones that follow a path, target = the slot showing the path
      (add_shape type=path adds one); params positionMode (percent, fixed),
      spacingMode (length, fixed, percent, proportional), rotateMode
      (tangent, chain, chainScale), position, spacing, rotation (offset),
      mixRotate, mixX, mixY (follows mixX unless given).
    - physics: bone; preset hair, cloth, tail, antenna or jiggle, and/or params
      x, y, rotate, scaleX, shearX (0..1: what it moves), inertia, strength,
      damping, mass, wind, gravity, mix, limit, fps (4.3 also scaleY mode).
    - slider (4.3): plays an animation by time, or driven by a bone: params
      animation (required), time, mix, additive, loop; with bone: property
      (rotate, x, y, scaleX, scaleY, shearY), from, to, scale, max, local.

    Args:
        project_path: Absolute path to the skeleton .json.
        type: ik, transform, path, physics or slider.
        name: Constraint name.
        bones: Constrained bones (ik, transform, path).
        bone: Simulated bone (physics) or driving bone (slider).
        target: Target bone (ik, transform; created if missing and target_at
            is given), or the path's slot (path).
        target_at: Where to create a missing target: a bone name (its world
            position) or [x, y] world point.
        target_parent: Parent for a created target (default: the root bone).
        preset: Physics preset name.
        params: Other fields, as listed above.
        order: Apply position among all constraints (default: last).
        replace: Replace a constraint of the same name, keeping its place and timelines.
        out_path: Write to this path instead of editing in place.
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
        save_project_version: Save the file's version as the game project's
            .spine-version (when the project's version was unclear).
    """
    return edit_skeleton(
        project_path,
        lambda doc: con_ops.add_constraint(
            doc, type, name, bones, bone, target, target_at, target_parent, preset, order, replace,
            **(params or {}),
        ),
        out_path,
        allow_version_mismatch=allow_version_mismatch,
        save_project_version=save_project_version,
    )


@mcp.tool()
@_asks
def remove_constraint(project_path: str, name: str, out_path: str | None = None,
                      allow_version_mismatch: bool = False, save_project_version: bool = False) -> dict:
    """Remove a constraint by name, with its skin entries and animation timelines.

    Args:
        project_path: Absolute path to the skeleton .json.
        name: Constraint to remove.
        out_path: Write to this path instead of editing in place.
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
        save_project_version: Save the file's version as the game project's
            .spine-version (when the project's version was unclear).
    """
    return edit_skeleton(project_path, lambda doc: con_ops.remove_constraint(doc, name), out_path,
                         allow_version_mismatch=allow_version_mismatch,
                         save_project_version=save_project_version)


@mcp.tool()
@_asks
def add_attachment(
    project_path: str,
    slot: str,
    image_path: str | None = None,
    name: str | None = None,
    skin: str = "default",
    x: float = 0.0,
    y: float = 0.0,
    rotation: float = 0.0,
    world: bool = False,
    set_as_setup: bool = False,
    replace: bool = False,
    atlas_path: str | None = None,
    frames: list[str] | None = None,
    setup_frame: int = 0,
    allow_version_mismatch: bool = False,
    save_project_version: bool = False,
) -> dict:
    """Add a PNG as a region attachment on a slot (e.g. a mouth shape or an
    alternate hand to swap with an attachment timeline), copy it into the
    skeleton's images folder and repack the atlas. With `frames` it is an
    image sequence (flames, blinking lights): the images are stored as
    <name>_01, <name>_02, ... and animations pick the frame with sequence
    keys (add_animation: "attachments": {skin: {slot: {name: {"sequence":
    [{"time": 0, "mode": "loop", "delay": 0.1}]}}}}).

    Args:
        project_path: Absolute path to the skeleton .json (edited in place).
        slot: Slot to attach to (add one first with edit_rig add_slot if needed).
        image_path: Absolute path to the PNG (or give frames).
        name: Attachment name; default the PNG's file name without extension.
        skin: Skin to add it to (created if missing); another skin's images
            go in images/<skin>/, so the same name can show other art per skin.
        x, y, rotation: Image centre and angle relative to the slot's bone, or
            world-space with world=true.
        set_as_setup: Show it in the setup pose (sets the slot's attachment).
        replace: Overwrite an attachment of the same name.
        atlas_path: Atlas to rewrite; default <skeleton>.atlas next to the JSON.
        frames: PNGs of an image sequence, in order (instead of image_path).
        setup_frame: The frame (0-based) shown in the setup pose.
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
        save_project_version: Save the file's version as the game project's
            .spine-version (when the project's version was unclear).
    """
    name = name or (Path(image_path).stem if image_path else None)
    if not name:
        raise EditError("give name (or image_path, whose file name it defaults to)")
    images, size, path, sequence = _images(name, image_path, frames, skin, setup_frame)
    if size is None:
        raise EditError("give image_path or frames")

    def edit(doc):
        return att_ops.add_region(doc, slot, name, size, skin, x, y, rotation, world=world,
                                  set_as_setup=set_as_setup, replace=replace, sequence=sequence, path=path)

    return _with_images(project_path, images, replace, atlas_path, edit, allow_version_mismatch,
                        save_project_version, (skin, slot, name))


def _images(name: str, image_path: str | None, frames: list[str] | None, skin: str, setup_frame: int = 0):
    """Images to copy in (region name -> PNG), the attachment's size, its
    region path when that differs from its name, and its sequence. A skin
    other than the default keeps its images in a folder of its name, so an
    attachment named like a default one (the usual way to swap art per
    skin) does not take the default one's image."""
    if image_path and frames:
        raise EditError("give image_path or frames, not both")
    base = name if skin == "default" else f"{skin}/{name}"
    if frames:
        sequence = att_ops.make_sequence(len(frames), setup=setup_frame)
        path = f"{base}_"
        return (dict(zip(att_ops.sequence_names(path, sequence), frames)), att_ops.image_size(frames[0]), path,
                sequence)
    if image_path:
        return {base: image_path}, att_ops.image_size(image_path), (base if base != name else None), None
    return {}, None, None, None


def _with_images(project_path: str, images: dict[str, str], replace: bool, atlas_path: str | None, edit,
                 allow_version_mismatch: bool, save_project_version: bool, owner: tuple[str, str, str]) -> dict:
    """Edit the skeleton, then copy the images in and repack the atlas;
    everything that could stop the copy or the repack is checked first."""
    if images:
        att_ops.check_new_images(d.load(project_path), project_path, images, replace, owner)
    result = edit_skeleton(project_path, edit, allow_version_mismatch=allow_version_mismatch,
                           save_project_version=save_project_version)
    if images:
        folder = att_ops.images_dir(project_path, d.load(project_path))
        copied = [str(att_ops.copy_image(src, folder, region)) for region, src in images.items()]
        result["images" if len(copied) > 1 else "image"] = copied if len(copied) > 1 else copied[0]
        result["atlas"] = att_ops.repack(project_path, atlas_path)
    return result


@mcp.tool()
@_asks
def add_mesh(
    project_path: str,
    slot: str,
    name: str | None = None,
    image_path: str | None = None,
    frames: list[str] | None = None,
    x: float = 0.0,
    y: float = 0.0,
    rotation: float = 0.0,
    world: bool = False,
    grid: list[int] | None = None,
    bones: list[str] | None = None,
    weights: list[dict] | None = None,
    source: str | None = None,
    source_skin: str | None = None,
    source_slot: str | None = None,
    timelines: bool = True,
    skin: str = "default",
    set_as_setup: bool = False,
    replace: bool = False,
    atlas_path: str | None = None,
    allow_version_mismatch: bool = False,
    save_project_version: bool = False,
) -> dict:
    """Add a mesh attachment: an image on a grid of vertices that can bend
    with bones (weights) or be reshaped with deform keys.

    - In place of a region: give the region's name and no image; it becomes
      a mesh with the same image and placement, and its keys keep working.
    - From an image: image_path (or frames for an image sequence), placed
      like a region (x, y, rotation relative to the slot's bone, or world).
    - Linked mesh: source names a mesh whose vertices, triangles and weights
      it reuses with another image (default the source's image), e.g. the
      same arm in another skin. timelines=true plays the source's deform
      keys too. (4.3) source_slot may be another slot.

    bones binds the vertices to those bones with automatic weights (inverse
    distance to each bone, at most 4 per vertex), so the image bends with
    them, e.g. bones=["arm_upper", "arm_lower"] for an elbow; weights gives
    [{bone: weight}, ...] per vertex instead. Without either, the mesh
    follows the slot's bone. Deform keys go in add_animation:
    "attachments": {skin: {slot: {name: {"deform": [{"time": 0,
    "vertices": [dx, dy, ...]}]}}}} (one dx, dy per vertex, or per weight
    for a weighted mesh).

    Args:
        project_path: Absolute path to the skeleton .json.
        slot: The mesh's slot.
        name: Attachment name (default the image's file name).
        image_path: PNG to show (copied into the images folder; atlas repacked).
        frames: PNGs of an image sequence, instead of image_path.
        x, y, rotation: Image centre and angle relative to the slot's bone
            (world x, y with world=true).
        grid: [columns, rows] of the vertex grid; default cells of about 48 px.
        bones: Bind to these bones with automatic weights.
        weights: Per-vertex {bone: weight} instead of bones.
        source: Make a linked mesh of this mesh.
        source_skin: Skin holding the source mesh (default "default").
        source_slot: (4.3) Slot holding the source mesh (default this slot).
        timelines: A linked mesh plays its source's deform keys.
        skin: Skin to add it to (created if missing); another skin's images
            go in images/<skin>/, so the same name can show other art per skin.
        set_as_setup: Show it in the setup pose.
        replace: Overwrite an attachment of the same name.
        atlas_path: Atlas to rewrite; default <skeleton>.atlas next to the JSON.
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
        save_project_version: Save the file's version as the game project's
            .spine-version (when the project's version was unclear).
    """
    name = name or (Path(image_path).stem if image_path else None)
    if not name:
        raise EditError("give name: the region to turn into a mesh, or the new mesh's name")
    images, size, path, sequence = _images(name, image_path, frames, skin)

    def edit(doc):
        if source is not None:
            linked_path, linked_sequence = path, sequence
            if not images:  # the source's image (or image sequence)
                owner = d.find_skin(doc, source_skin or "default") or {}
                src = ((owner.get("attachments") or {}).get(source_slot or slot) or {}).get(source) or {}
                linked_path, linked_sequence = src.get("path", source), src.get("sequence")
            return mesh_ops.add_linked_mesh(doc, slot, name, source, size, skin, linked_path, source_skin,
                                            source_slot, timelines, set_as_setup, replace, linked_sequence)
        return mesh_ops.add_mesh(doc, slot, name, size, skin, path, x, y, rotation, 1.0, 1.0, world,
                                 tuple(grid) if grid else None, bones, weights, set_as_setup, replace, sequence)

    return _with_images(project_path, images, replace, atlas_path, edit, allow_version_mismatch,
                        save_project_version, (skin, slot, name))


@mcp.tool()
@_asks
def set_weights(
    project_path: str,
    slot: str,
    attachment: str,
    bones: list[str] | None = None,
    weights: list[dict] | None = None,
    skin: str = "default",
    out_path: str | None = None,
    allow_version_mismatch: bool = False,
    save_project_version: bool = False,
) -> dict:
    """Bind a mesh's vertices (or a bounding box's, clip's or path's) to
    bones, keeping them where they are in the setup pose: automatic weights
    from `bones`, or `weights` per vertex ([{bone: weight}, ...]). bones=[]
    unbinds them, back to following the slot's bone. Refused while the
    attachment has deform keys (their layout depends on the weights).

    Args:
        project_path: Absolute path to the skeleton .json.
        slot: The attachment's slot.
        attachment: The attachment's name.
        bones: Bones for automatic weights ([] to unbind).
        weights: Per-vertex {bone: weight} instead.
        skin: The attachment's skin.
        out_path: Write to this path instead of editing in place.
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
        save_project_version: Save the file's version as the game project's
            .spine-version (when the project's version was unclear).
    """
    return edit_skeleton(project_path, lambda doc: mesh_ops.set_weights(doc, slot, attachment, skin, bones, weights),
                         out_path, allow_version_mismatch=allow_version_mismatch,
                         save_project_version=save_project_version)


@mcp.tool()
@_asks
def add_shape(
    project_path: str,
    slot: str,
    type: str,
    name: str | None = None,
    vertices: list[list[float]] | None = None,
    points: list | None = None,
    x: float = 0.0,
    y: float = 0.0,
    rotation: float = 0.0,
    closed: bool = False,
    constant_speed: bool = True,
    world: bool = False,
    skin: str = "default",
    set_as_setup: bool | None = None,
    replace: bool = False,
    color: str | None = None,
    out_path: str | None = None,
    allow_version_mismatch: bool = False,
    save_project_version: bool = False,
) -> dict:
    """Add a bounding box, point or path attachment (positions relative to
    the slot's bone, y up, or world with world=true).

    - boundingbox: vertices [[x, y], ...] (3 or more): a polygon for hit
      tests (the runtime's SkeletonBounds uses the boxes slots show).
    - point: x, y, rotation: a position and direction, e.g. a muzzle or
      where a particle effect starts.
    - path: points, each [x, y] (smooth handles are made) or {"x", "y",
      "in": [x, y], "out": [x, y]}; closed, constant_speed. Bones follow it
      with add_constraint type=path.

    A bounding box or path is shown in the setup pose by default (the
    runtime uses the one a slot shows), so give it its own slot (edit_rig
    add_slot).

    Args:
        project_path: Absolute path to the skeleton .json.
        slot: The attachment's slot.
        type: boundingbox, point or path.
        name: Attachment name (default bounds, point or path).
        vertices: The bounding box polygon.
        points: The path's knots.
        x, y, rotation: The point.
        closed: The path loops back to its first point.
        constant_speed: Bones move along the path at an even speed.
        world: Positions are world-space.
        skin: Skin to add it to (created if missing).
        set_as_setup: Show it in the setup pose (default true for a bounding
            box or path, false for a point).
        replace: Overwrite an attachment of the same name.
        color: RRGGBBAA, how the editor draws it.
        out_path: Write to this path instead of editing in place.
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
        save_project_version: Save the file's version as the game project's
            .spine-version (when the project's version was unclear).
    """
    if type == "boundingbox":
        def edit(doc):
            return shape_ops.add_bounding_box(doc, slot, vertices, name or "bounds", skin, world,
                                              True if set_as_setup is None else set_as_setup, replace, color)
    elif type == "point":
        def edit(doc):
            result = shape_ops.add_point(doc, slot, name or "point", x, y, rotation, skin, world, replace, color)
            if set_as_setup:
                d.slot_map(doc)[slot]["attachment"] = name or "point"
            return result
    elif type == "path":
        def edit(doc):
            return shape_ops.add_path(doc, slot, points, name or "path", skin, closed, constant_speed, world,
                                      True if set_as_setup is None else set_as_setup, replace, color)
    else:
        raise EditError("type must be boundingbox, point or path")
    return edit_skeleton(project_path, edit, out_path, allow_version_mismatch=allow_version_mismatch,
                         save_project_version=save_project_version)


@mcp.tool()
@_asks
def edit_skin(
    project_path: str,
    operations: list[dict],
    out_path: str | None = None,
    allow_version_mismatch: bool = False,
    save_project_version: bool = False,
) -> dict:
    """Add, copy, rename or remove skins, and make bones and constraints
    active only with a skin (e.g. a cape's bones and physics only in the
    "caped" skin). Attachments go into a skin with the attachment tools'
    `skin` argument.

    The default skin holds what every skin shows. An attachment that differs
    per skin is a skin placeholder: it lives in skins, never also in the
    default skin (the Spine editor refuses that). So to swap art, move the
    attachment into a base skin, then give the other skin its own under the
    same name:
      edit_skin([{"op": "move_to_skins", "slot": "arm_left",
                  "attachment": "arm_left", "skins": ["base"]},
                 {"op": "add_skin", "name": "armor"}])
      add_mesh(slot="arm_left", name="arm_left", skin="armor",
               source="arm_left", source_skin="base", image_path=...)
    The game then sets "base" or "armor". A skin's images are kept in
    images/<skin>/. copy_from copies a whole skin, for variants that change
    most of it (keys of copied attachments must then be added per skin).

    Operations ("op" plus its fields), applied in order:
    - add_skin: name [, copy_from (another skin to copy), color]
    - move_to_skins: slot, attachment, skins=[...] (out of the default skin
      into those skins, made when missing, with its deform and sequence keys)
    - rename_skin: name, to
    - remove_skin: name (with its deform and sequence keys)
    - add_to_skin: skin, bones=[...], constraints=[...] (they become active
      only while a skin listing them is the current skin)
    - remove_from_skin: skin, bones, constraints (one no skin lists is
      active for every skin again)

    Args:
        project_path: Absolute path to the skeleton .json.
        operations: e.g. [{"op": "add_skin", "name": "armor"}].
        out_path: Write to this path instead of editing in place.
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
        save_project_version: Save the file's version as the game project's
            .spine-version (when the project's version was unclear).
    """
    return edit_skeleton(project_path, lambda doc: skin_ops.edit_skins(doc, operations), out_path,
                         allow_version_mismatch=allow_version_mismatch,
                         save_project_version=save_project_version)


@mcp.tool()
@_asks
def add_clipping(
    project_path: str,
    slot: str,
    vertices: list[list[float]],
    name: str = "clip",
    end: str | None = None,
    skin: str = "default",
    world: bool = False,
    convex: bool = False,
    inverse: bool = False,
    set_as_setup: bool = True,
    replace: bool = False,
    color: str | None = None,
    out_path: str | None = None,
    allow_version_mismatch: bool = False,
    save_project_version: bool = False,
) -> dict:
    """Add a clipping attachment: a polygon on `slot` that clips every slot
    drawn after it, up to and including `end` (default: the last slot), e.g.
    a mask for a window or a mouth. The result lists the slots it clips.

    A slot shows one attachment at a time, so give the clip its own slot
    (edit_rig add_slot, drawn just before the slots to clip). Turn a clip on
    and off with an attachment timeline on that slot. The Spine 4.3 editor
    (4.3.26) ignores convex and inverse when it imports JSON (the result
    notes when that matters); the runtime reads them.

    Args:
        project_path: Absolute path to the skeleton .json.
        slot: The clip's slot.
        vertices: The polygon's points, [[x, y], ...], relative to the slot's
            bone (y up), or world points with world=true; at least 3.
        name: Attachment name.
        end: Last slot clipped; default every slot drawn after `slot`.
        skin: Skin to add it to (created if missing).
        world: vertices are world-space points.
        convex: (4.3) The polygon is convex, which clips faster.
        inverse: (4.3) Hide what is inside the polygon instead of outside.
        set_as_setup: Show the clip in the setup pose (sets the slot's attachment).
        replace: Overwrite an attachment of the same name.
        color: RRGGBBAA, how the editor draws the polygon.
        out_path: Write to this path instead of editing in place.
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
        save_project_version: Save the file's version as the game project's
            .spine-version (when the project's version was unclear).
    """
    return edit_skeleton(
        project_path,
        lambda doc: att_ops.add_clipping(doc, slot, vertices, name, skin, end, world, convex, inverse,
                                         set_as_setup, replace, color),
        out_path,
        allow_version_mismatch=allow_version_mismatch,
        save_project_version=save_project_version,
    )


@mcp.tool()
@_asks
def set_draw_order_keys(
    project_path: str,
    animation: str,
    keys: list[dict],
    folder: str | None = None,
    out_path: str | None = None,
    allow_version_mismatch: bool = False,
    save_project_version: bool = False,
) -> dict:
    """Set an animation's draw order keys, replacing the ones it has: the
    whole skeleton's draw order, or (4.3) one draw order folder's, which reorders
    only that folder's slots, independently of other keys (e.g. a hand
    passing in front of the face while the body's order is keyed elsewhere).

    A draw order folder is the slots named "<folder>/...", next to each other in
    the draw order; edit_rig rename_slot puts slots in one.

    Each key is {"time": seconds, "order": [slot names, back to front]};
    a key without order returns to the setup order. For the whole draw order,
    order lists every slot; for a folder, all its slots. No keys removes the
    timeline.

    Args:
        project_path: Absolute path to the skeleton .json.
        animation: An existing animation (add_animation creates one).
        keys: e.g. [{"time": 0, "order": ["face/hand", "face/eye"]}, {"time": 0.5}].
        folder: (4.3) A draw order folder; default the whole draw order. The Spine
            4.3 editor (4.3.26) drops a nested folder's keys on import.
        out_path: Write to this path instead of editing in place.
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
        save_project_version: Save the file's version as the game project's
            .spine-version (when the project's version was unclear).
    """
    return edit_skeleton(
        project_path,
        lambda doc: anim_ops.set_draw_order_keys(doc, animation, keys, folder),
        out_path,
        allow_version_mismatch=allow_version_mismatch,
        save_project_version=save_project_version,
    )


@mcp.tool()
def pack_atlas(project_path: str, atlas_path: str | None = None) -> dict:
    """Repack the atlas from the skeleton's images folder (one PNG per
    attachment path), e.g. after editing part images. The atlas format is the
    same for 4.2 and 4.3.

    Args:
        project_path: Absolute path to the skeleton .json.
        atlas_path: Atlas to write; default <skeleton>.atlas next to the JSON.
    """
    return att_ops.repack(project_path, atlas_path)


@mcp.tool()
@_asks
def convert_skeleton(
    project_path: str,
    to_version: str | None = None,
    out_path: str | None = None,
    allow_lossy: bool = False,
    allow_version_mismatch: bool = False,
    verify: bool = True,
    atlas_path: str | None = None,
    save_project_version: bool = False,
) -> dict:
    """Convert a skeleton JSON to another format version (4.2 <-> 4.3).

    4.2 -> 4.3 loses nothing. 4.3 -> 4.2 refuses (needs_answer) when 4.2
    cannot express something (sliders, non-identity transform mappings,
    scale modes, clipping convex/inverse, cross-slot linked meshes, draw
    order folders), listing each; allow_lossy drops exactly those.

    Writes a new file next to the original (hero.json -> hero-4.3.json) that
    shares its atlas and images; tools find hero.atlas for hero-4.3.json. The
    original is replaced only when out_path names it.

    With verify (default), both skeletons are played in their own spine-core
    releases and every bone's pose is compared frame by frame (physics
    settings and keys compared too; the simulation itself changed in 4.3).

    Args:
        project_path: Absolute path to the skeleton .json.
        to_version: "4.2" or "4.3"; default the game project's version, else
            the other supported version.
        out_path: Where to write; default <name>-<version>.json beside it.
        allow_lossy: Convert even though some parts are dropped (only when the
            user agreed to lose them).
        allow_version_mismatch: Convert to a version other than the game
            project's (only when the user said so).
        verify: Compare poses in both runtimes after converting.
        atlas_path: The original's atlas; default <skeleton>.atlas.
        save_project_version: Also save to_version as the game project's
            .spine-version (when the project did not say).
    """
    return convert_file(project_path, to_version, out_path, allow_lossy, allow_version_mismatch, verify,
                        atlas_path, save_project_version)


@mcp.tool()
def spine_import(
    project_path: str,
    spine_project: str | None = None,
    skeleton_name: str | None = None,
    editor_version: str | None = None,
) -> dict:
    """Import a skeleton JSON into a .spine project with the Spine editor's
    CLI (needs Spine installed; set SPINE_EXECUTABLE if it is not in the
    standard place). The editor imports only JSON of its own format version,
    so the editor is picked for the file's version (newest installed stable
    release, or $SPINE_EDITOR_VERSION); its reader reports anything it cannot
    load. changed_on_import lists what the editor's import changes: 4.3
    clipping convex/inverse and nested folders' draw order keys, which are
    recorded beside the project and put back by spine_export's JSON and
    binary exports, and physics rotate/scaleX/shearX on a bone without length
    (give the bone a length). A skin attachment under a name the default skin
    also has in that slot is refused before the editor runs (it would not
    import); edit_skin move_to_skins fixes it.

    Args:
        project_path: Absolute path to the skeleton .json.
        spine_project: .spine file to create or import into; default next to the JSON.
        skeleton_name: Name of the imported skeleton; default the JSON's name.
        editor_version: A specific editor release of the file's format version.
    """
    return spine_cli.spine_import(project_path, spine_project, skeleton_name, editor_version)


@mcp.tool()
@_asks
def spine_export(
    input_path: str,
    output_dir: str,
    format: str = "png",
    animations: list[str] | None = None,
    fps: int = 30,
    scale: float = 100,
    background: str | None = "ffffffff",
    editor_version: str | None = None,
    format_version: str | None = None,
    allow_version_mismatch: bool = False,
    save_project_version: bool = False,
    restore: bool = True,
) -> dict:
    """Render or export with the Spine editor's CLI.

    - png: one PNG per frame for each animation, in output_dir/frames.
    - gif: one looping GIF per animation (encoded from the PNG frames).
    - json / binary: skeleton data in a format version: a JSON input keeps its
      own; a .spine project exports in format_version, else the game
      project's (from output_dir), else $SPINE_FORMAT_VERSION, else it asks.
      A JSON or binary export from a project made by spine_import gets back
      what the 4.3 editor's import lost (clip convex/inverse, nested folder
      keys), unless restore=false or the export shows it was changed in the
      editor.

    Args:
        input_path: Skeleton .json or .spine project.
        output_dir: Folder to write into.
        format: png, gif, json or binary.
        animations: Only these animations (png/gif); default all.
        fps: Frames per second (png/gif).
        scale: Percent (png/gif).
        background: RRGGBB or RRGGBBAA, or null for transparent PNGs.
        editor_version: A specific editor release (of the data's format version).
        format_version: Format version to open a .spine project in / export.
        allow_version_mismatch: Export data in a version other than the game
            project's (only when the user said so).
        save_project_version: Save format_version as the game project's
            .spine-version (when the project did not say).
        restore: Put back what the editor's import lost (json and binary).
    """
    source = Path(input_path)
    game = None
    if source.suffix.lower() == ".json":
        version = spine_cli.json_version(source)
        if format in ("json", "binary"):
            game = proj.for_existing_file(Path(output_dir), version, allow_version_mismatch, action="export",
                                          file=source, save_project_version=save_project_version)
    elif format in ("json", "binary"):
        version, game, _ = proj.for_new_file(output_dir, format_version, allow_version_mismatch, save_project_version)
    else:  # images: any editor that can open the project; prefer the game project's version
        detected = proj.detect(output_dir).version
        version = format_version or (detected if versions.is_supported(detected) else None)
    result = spine_cli.spine_export(input_path, output_dir, format, animations, fps, scale, background,
                                    editor_version, version, restore)
    if game is not None and save_project_version and result.get("ok"):
        result["notes"] = [proj.save_declaration(game, version)]
    return result


@mcp.tool()
def preview_html(
    project_path: str,
    atlas_path: str | None = None,
    out_path: str | None = None,
    animation: str | None = None,
    runtime: str = "player",
) -> dict:
    """Write an HTML page that plays the skeleton.

    - runtime="player": the official Spine Web Player of its format version
      (4.2.120 or 4.3.13), self-contained (skeleton, atlas and images
      embedded; the player script loads from unpkg); it opens from disk.
    - runtime="phaser": a Phaser 4 game with spine-phaser-v4, the game
      runtime of its format version (4.2: 4.2.120 on Phaser 4.1.0; 4.3:
      4.3.13 on Phaser 4.2.1), loading the files beside it, so serve the
      folder over HTTP (the result gives a command) and open the URL.

    Both have pickers for animations and skins.

    Args:
        project_path: Absolute path to the skeleton .json.
        atlas_path: Atlas; default <skeleton>.atlas next to the JSON.
        out_path: HTML file to write; default <skeleton>_preview.html or <skeleton>_phaser.html.
        animation: Animation to start with; default the first.
        runtime: player or phaser.
    """
    release = versions.get(versions.of(d.load(project_path)))
    if runtime == "phaser":
        path = _phaser(project_path, atlas_path, out_path, animation)
        return {"preview_path": str(path), "runtime": f"phaser@{release.phaser} + "
                f"@esotericsoftware/spine-phaser-v4@{release.runtime}",
                "serve": f"python3 -m http.server 8000 --directory {path.parent}",
                "url": f"http://localhost:8000/{path.name}"}
    if runtime != "player":
        raise EditError("runtime must be player or phaser")
    path = _preview(project_path, atlas_path, out_path, animation)
    return {"preview_path": str(path), "player": f"@esotericsoftware/spine-player@{player_version(d.load(project_path))}"}


@mcp.tool()
def analyze_motion(
    project_path: str,
    animations: list[str] | None = None,
    loops: list[str] | None = None,
    skins: list[str] | None = None,
    mixes: list[dict] | None = None,
) -> dict:
    """Measure how animations move as the viewer sees them, and find what
    makes motion read robotic, stiff, floaty or jerky. Each animation plays
    in spine-core like a game plays it (AnimationState, IK and other
    constraints, physics) and is sampled 60 times a second; the attachments'
    screen motion is measured.

    Findings are ranked like a critique, each with its evidence (parts,
    times, and the keys that move those parts) and a keyframe-level fix:
    2 breaks the read: hitches (velocity jumps mid-move), teleports, loop
      seams, mixes that jerk, limbs drawn longer than in the setup pose;
    3 weight and timing: abrupt starts and stops (no slow out / slow in),
      constant speed (linear spacing), straight paths (no arcs), parts that
      all start and stop together (no overlap), long single moves (floaty),
      linear keys far apart, nothing that settles (no poses);
    4 life: dead-still holds, loops that barely move or move in lockstep,
      mirrored (twinned) limbs, attachment swaps with nothing moving;
    5 polish: one ease on every key, stepped (blocking) keys left in.
    Per animation it also reports the key poses (holds and slow passing
    poses), peak speed, and parent -> child start/stop offsets (overlap).
    Thresholds are starting points at the skeleton's size: look at the
    flagged moments with onion_skin before changing keys.

    Args:
        project_path: Absolute path to the skeleton .json (its atlas beside it).
        animations: Animations to measure; default all.
        loops: Animations that loop (played twice: seam, amplitude and
            lockstep checks); default the presets' loops present (idle, walk).
        skins: Skins to show (combined); default the skins holding the setup
            pose's attachments.
        mixes: Transitions to check, e.g. [{"from": "idle", "to": "deal",
            "mix": 0.2}] (mix in seconds).
    """
    return _analyze_motion(project_path, animations, loops, skins, mixes)


@mcp.tool()
def onion_skin(
    project_path: str,
    animation: str,
    time: float | None = None,
    before: int = 6,
    after: int = 6,
    step: int = 2,
    keyed: bool = False,
    times: list[float] | None = None,
    skins: list[str] | None = None,
    trails: list[str] | None = None,
    size: int = 1024,
    background: str | None = "#1b1b24",
    out_path: str | None = None,
) -> dict:
    """Draw an onion skin (the Spine editor's Ghosting view) as a PNG: the
    pose at `time`, with tinted ghosts of the poses before (warm) and after
    (cool) it, and spacing trails: a dot per frame along chosen parts' paths,
    so arcs and spacing show at a glance (dots bunch where a part slows down
    and spread where it is fast). Poses play in spine-core (constraints,
    physics, clipping) and are drawn without the Spine editor. Open the PNG
    to look at it.

    Args:
        project_path: Absolute path to the skeleton .json (its atlas and page
            images beside it).
        animation: The animation.
        time: The current pose, in seconds; default the middle.
        before: Frames before `time` to ghost (at the skeleton's fps, 30 by default).
        after: Frames after `time` to ghost.
        step: A ghost every `step` frames.
        keyed: Ghost only frames that have keys, within before/after.
        times: Ghost exactly these times instead (e.g. the key poses
            analyze_motion reports).
        skins: Skins to show (combined); default the skins holding the setup
            pose's attachments.
        trails: Slots whose paths to dot; default up to three parts on
            different chains that travel farthest; [] for none.
        size: The longer side of the image in pixels.
        background: Background colour, or null for transparent.
        out_path: PNG to write; default <skeleton>-<animation>-onion.png.
    """
    return _onion_skin(project_path, animation, time, before, after, step, keyed, times, skins, trails, size,
                       background, out_path)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
