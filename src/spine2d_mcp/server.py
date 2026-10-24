"""FastMCP server: build, inspect, check and edit Spine 4.2 skeletons."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .edits import edit_skeleton, report
from .ops import animation as anim_ops
from .pipeline import import_psd_to_spine as _import
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
def validate_skeleton(project_path: str, loops: list[str] | None = None) -> dict:
    """Check a skeleton JSON against the Spine 4.2 format: references, meshes,
    constraints, and animation keys (rotate `value` not `angle`, curve lengths,
    key order, events). Errors break loading or render wrongly; warnings are
    probably unintended.

    Args:
        project_path: Absolute path to the skeleton .json.
        loops: Animation names meant to loop; each is checked for a pop where
            its last key differs from its first.
    """
    doc = d.load(project_path)
    findings = check(doc, loops)
    result = report(findings)
    return {"ok": not result["errors"], "summary": summary(doc), **result}


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


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
