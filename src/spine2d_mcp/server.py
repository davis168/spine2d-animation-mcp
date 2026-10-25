"""FastMCP server: build, inspect, check, edit and convert Spine skeletons in
format 4.2 or 4.3, each in its own version."""

from __future__ import annotations

import functools
import os
import re
import shutil
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import project as proj
from . import spine_cli
from .edits import EditError, edit_skeleton, editable_version, report
from .ops import animation as anim_ops
from .ops import attachments as att_ops
from .ops import constraints as con_ops
from .ops import rig as rig_ops
from .pipeline import header_version
from .pipeline import import_psd_to_spine as _import
from .preview import _atlas_pages, player_version
from .preview import preview_html as _preview
from .runtime import compare_poses, runtime_check
from .spine import document as d
from .spine import versions
from .spine.check import check, summary
from .spine.convert import LossyConversion, convert
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
        "refuses changes that introduce errors; run validate_skeleton after a series of edits.\n"
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
) -> dict:
    """Convert a PSD character into a Spine project (skeleton JSON + atlas +
    PNG page(s) + per-part images) importable into the Spine editor.

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
    """
    src = Path(psd_path)
    if out_dir is None:
        out_dir = str(src.parent / f"{src.stem}_spine")
    version, game, notes = proj.for_new_file(out_dir, format_version, allow_version_mismatch, save_project_version)
    result = _import(psd_path, out_dir, version)
    if save_project_version:
        notes.append(proj.save_declaration(game, version))
    return {
        "project_path": str(result.project_path),
        "atlas_path": str(result.atlas_path),
        "png_paths": [str(p) for p in result.png_paths],
        "images_dir": str(result.images_dir),
        "format_version": version,
        "version": game.note(version),
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

    With runtime=true it also loads the skeleton and atlas in the spine-core
    release of its format version (4.2.120 or 4.3.13) via Node and steps every
    animation; spine-core is installed into a cache on first use.

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
        result["runtime"] = runtime_check(project_path, atlas_path)
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
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
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
    )


@mcp.tool()
@_asks
def remove_animation(project_path: str, name: str, out_path: str | None = None,
                     allow_version_mismatch: bool = False) -> dict:
    """Delete an animation from the skeleton (refused while a 4.3 slider plays it).

    Args:
        project_path: Absolute path to the skeleton .json.
        name: Animation to remove.
        out_path: Write to this path instead of editing in place.
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
    """
    return edit_skeleton(project_path, lambda doc: anim_ops.remove_animation(doc, name), out_path,
                         allow_version_mismatch=allow_version_mismatch)


@mcp.tool()
@_asks
def edit_rig(project_path: str, operations: list[dict], out_path: str | None = None,
             allow_version_mismatch: bool = False) -> dict:
    """Edit the setup pose: a list of operations applied in order, written
    only if the result is still a valid skeleton of its format version.

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
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
    """
    return edit_skeleton(project_path, lambda doc: rig_ops.edit_rig(doc, operations), out_path,
                         allow_version_mismatch=allow_version_mismatch)


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
) -> dict:
    """Add an IK, transform, physics or (4.3) slider constraint, written in
    the skeleton's format version. Options one version names differently are
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
    - physics: bone; preset hair, cloth, tail, antenna or jiggle, and/or params
      x, y, rotate, scaleX, shearX (0..1: what it moves), inertia, strength,
      damping, mass, wind, gravity, mix, limit, fps (4.3 also scaleY mode).
    - slider (4.3): plays an animation by time, or driven by a bone: params
      animation (required), time, mix, additive, loop; with bone: property
      (rotate, x, y, scaleX, scaleY, shearY), from, to, scale, max, local.

    Args:
        project_path: Absolute path to the skeleton .json.
        type: ik, transform, physics or slider.
        name: Constraint name.
        bones: Constrained bones (ik, transform).
        bone: Simulated bone (physics) or driving bone (slider).
        target: Target bone (ik, transform). Created if missing and target_at is given.
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
    """
    return edit_skeleton(
        project_path,
        lambda doc: con_ops.add_constraint(
            doc, type, name, bones, bone, target, target_at, target_parent, preset, order, replace,
            **(params or {}),
        ),
        out_path,
        allow_version_mismatch=allow_version_mismatch,
    )


@mcp.tool()
@_asks
def remove_constraint(project_path: str, name: str, out_path: str | None = None,
                      allow_version_mismatch: bool = False) -> dict:
    """Remove a constraint by name, with its skin entries and animation timelines.

    Args:
        project_path: Absolute path to the skeleton .json.
        name: Constraint to remove.
        out_path: Write to this path instead of editing in place.
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
    """
    return edit_skeleton(project_path, lambda doc: con_ops.remove_constraint(doc, name), out_path,
                         allow_version_mismatch=allow_version_mismatch)


@mcp.tool()
@_asks
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
    allow_version_mismatch: bool = False,
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
        allow_version_mismatch: Edit even though the file's format version
            differs from the game project's (only when the user said so).
    """
    size = att_ops.image_size(image_path)
    name = name or Path(image_path).stem
    # Everything that could stop the copy or the repack is checked before the JSON is written.
    att_ops.check_new_image(d.load(project_path), project_path, image_path, name, replace)
    result = edit_skeleton(
        project_path,
        lambda doc: att_ops.add_region(doc, slot, name, size, skin, x, y, rotation,
                                       world=world, set_as_setup=set_as_setup, replace=replace),
        allow_version_mismatch=allow_version_mismatch,
    )
    doc = d.load(project_path)
    result["image"] = str(att_ops.copy_image(image_path, att_ops.images_dir(project_path, doc), name))
    result["atlas"] = att_ops.repack(project_path, atlas_path)
    return result


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


_VERSION_SUFFIX = re.compile(r"-(\d+\.\d+(?:\.\d+)?)$")  # "hero-4.2" -> "hero"; a non-Spine "-1.5" stays


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
) -> dict:
    """Convert a skeleton JSON to another format version (4.2 <-> 4.3).

    4.2 -> 4.3 loses nothing. 4.3 -> 4.2 refuses (needs_answer) when 4.2
    cannot express something (sliders, non-identity transform mappings,
    scale modes, clipping convex/inverse, cross-slot linked meshes, draw
    order folders), listing each; allow_lossy drops exactly those.

    Writes a new file next to the original (hero.json -> hero-4.3.json) with
    a copy of its atlas under the new name; the images and atlas pages are
    shared. The original is replaced only when out_path names it.

    With verify (default), both skeletons are played in their own spine-core
    releases and every bone's pose is compared frame by frame (physics
    settings and keys compared too; the simulation itself changed in 4.3).

    Args:
        project_path: Absolute path to the skeleton .json.
        to_version: "4.2" or "4.3"; default the game project's version.
        out_path: Where to write; default <name>-<version>.json beside it.
        allow_lossy: Convert even though some parts are dropped (only when the
            user agreed to lose them).
        allow_version_mismatch: Convert to a version other than the game
            project's (only when the user said so).
        verify: Compare poses in both runtimes after converting.
        atlas_path: The original's atlas; default <skeleton>.atlas.
    """
    src = Path(project_path)
    doc = d.load(src)
    file_version = editable_version(doc, src)
    if to_version is None:
        game = proj.detect(Path(out_path) if out_path else src)
        if game.version and game.version != file_version and versions.is_supported(game.version):
            to_version = game.version
        elif game.version == file_version:
            return {"converted": False, "note": f"{src.name} is already {file_version}, the game project's "
                                                f"version ({game.source}); pass to_version to convert anyway"}
        else:
            others = [v for v in versions.SUPPORTED if v != file_version]
            raise proj.NeedsAnswer(
                f"Convert {src.name} (format {file_version}) to which format version? "
                + (game.note(file_version) if game.version or game.problem else "No game project version found."),
                [{"answer": v, "call_again_with": {"to_version": v}} for v in reversed(others)],
                recommended=others[-1])
    target_version = versions.get(to_version).version
    if target_version == file_version:
        raise EditError(f"{src.name} is already format version {file_version}")
    suffix = _VERSION_SUFFIX.search(src.stem)
    stem = src.stem[:suffix.start()] if suffix and versions.is_supported(versions.major_minor(suffix.group(1))) \
        else src.stem
    out = Path(out_path) if out_path else src.with_name(f"{stem}-{target_version}.json")
    if out_path is None and out.exists():
        raise EditError(f"{out} already exists; pass out_path (that file to overwrite it, or another name)")
    game = proj.detect(out)
    if game.version and game.version != target_version and not allow_version_mismatch:
        if game.version == file_version:
            keep = {"answer": f"Keep {src.name} in {file_version}, the game project's version", "instead": "nothing to do"}
        else:
            keep = {"answer": f"Convert to {game.version}, the game project's version",
                    "call_again_with": {"to_version": game.version}}
        raise proj.NeedsAnswer(
            f"The game project uses Spine {game.version} ({game.source}), but conversion to {target_version} was "
            f"asked for; a {target_version} skeleton will not load in this game.",
            [keep, {"answer": f"Convert to {target_version} anyway", "call_again_with": {"allow_version_mismatch": True}}],
            recommended=keep["answer"])
    try:
        conversion = convert(doc, target_version, allow_lossy)
    except LossyConversion as lossy:
        raise proj.NeedsAnswer(
            f"Converting {src.name} from {file_version} to {target_version} would lose: "
            + "; ".join(lossy.losses) + ". Convert anyway and drop these?",
            [{"answer": f"Keep it in {file_version} (do not convert)", "instead": "nothing to do"},
             {"answer": "Convert and drop them", "call_again_with": {"allow_lossy": True}}],
            recommended=f"Keep it in {file_version} (do not convert)") from None
    atlas = Path(atlas_path) if atlas_path else src.with_suffix(".atlas")
    shared = out.with_suffix(".atlas")
    if (atlas.is_file() and shared.exists() and shared.resolve() != atlas.resolve()
            and shared.read_text(encoding="utf-8") != atlas.read_text(encoding="utf-8")):
        raise EditError(f"{shared} already exists and is a different atlas; pass out_path with another name")
    new_doc = conversion.doc
    new_doc["skeleton"]["spine"] = header_version(target_version)
    images = (new_doc.get("skeleton") or {}).get("images")
    if images and out.parent.resolve() != src.parent.resolve():
        target = (src.parent / images).resolve()
        new_doc["skeleton"]["images"] = os.path.relpath(target, out.parent.resolve()).replace(os.sep, "/") + "/"
    # Converting in place replaces the original, so verify against a copy of it.
    original = src
    if out.resolve() == src.resolve() and verify:
        fd, name = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        original = Path(shutil.copyfile(src, name))
    d.save(new_doc, out)

    findings = check(new_doc)
    result = {"converted": True, "from": file_version, "to": target_version, "project_path": str(out),
              "summary": summary(new_doc), "changed": conversion.changed, **report(findings),
              "version": game.note(target_version)}
    if conversion.lost:
        result["lost"] = conversion.lost
    if conversion.dropped_editor_data:
        result["dropped_editor_data"] = conversion.dropped_editor_data
    try:
        if atlas.is_file():
            result["atlas_path"] = str(_share_atlas(atlas, out))
            if verify:
                result["verify"] = compare_poses(original, out, atlas)
                if conversion.lost:
                    result["verify"]["note"] = "parts were dropped (see lost), so differences there are expected"
        elif verify:
            result["verify"] = {"ran": False, "reason": f"no atlas at {atlas} (pass atlas_path)"}
    except Exception as e:  # the converted file is written; report the rest rather than fail
        result.setdefault("verify", {"ran": False, "reason": f"{type(e).__name__}: {e}"})
    finally:
        if original != src:
            original.unlink(missing_ok=True)
    return result


def _share_atlas(atlas: Path, skeleton: Path) -> Path:
    """Give the converted skeleton an atlas under its own name, so tools that
    look for <skeleton>.atlas find it: a copy of the text beside it (pages stay
    shared), plus the pages when it lives in another folder."""
    target = skeleton.with_suffix(".atlas")
    if target.resolve() == atlas.resolve():
        return target
    text = atlas.read_text(encoding="utf-8")
    if not target.exists() or target.read_text(encoding="utf-8") != text:
        target.write_text(text, encoding="utf-8")
    if target.parent.resolve() != atlas.parent.resolve():
        for page in _atlas_pages(text):
            if (atlas.parent / page).is_file() and not (target.parent / page).exists():
                shutil.copyfile(atlas.parent / page, target.parent / page)
    return target


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
    load.

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
) -> dict:
    """Render or export with the Spine editor's CLI.

    - png: one PNG per frame for each animation, in output_dir/frames.
    - gif: one looping GIF per animation (encoded from the PNG frames).
    - json / binary: skeleton data in a format version: a JSON input keeps its
      own; a .spine project exports in format_version, else the game
      project's (from output_dir), else $SPINE_FORMAT_VERSION, else it asks.

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
    """
    source = Path(input_path)
    game = None
    if source.suffix.lower() == ".json":
        version = spine_cli.json_version(source)
        if format in ("json", "binary"):
            proj.for_existing_file(Path(output_dir), version, allow_version_mismatch, action="export", file=source)
    elif format in ("json", "binary"):
        version, game, _ = proj.for_new_file(output_dir, format_version, allow_version_mismatch, save_project_version)
    else:  # images: any editor that can open the project; prefer the game project's version
        detected = proj.detect(output_dir).version
        version = format_version or (detected if versions.is_supported(detected) else None)
    result = spine_cli.spine_export(input_path, output_dir, format, animations, fps, scale, background,
                                    editor_version, version)
    if game is not None and save_project_version and result.get("ok"):
        result["notes"] = [proj.save_declaration(game, version)]
    return result


@mcp.tool()
def preview_html(
    project_path: str,
    atlas_path: str | None = None,
    out_path: str | None = None,
    animation: str | None = None,
) -> dict:
    """Write a self-contained HTML page that plays the skeleton in the official
    Spine Web Player of its format version (4.2.120 or 4.3.13; skeleton, atlas
    and images embedded; the player script loads from unpkg). Its controls
    switch animations and skins.

    Args:
        project_path: Absolute path to the skeleton .json.
        atlas_path: Atlas; default <skeleton>.atlas next to the JSON.
        out_path: HTML file to write; default <skeleton>_preview.html.
        animation: Animation to start with; default the first.
    """
    path = _preview(project_path, atlas_path, out_path, animation)
    return {"preview_path": str(path), "player": f"@esotericsoftware/spine-player@{player_version(d.load(project_path))}"}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
