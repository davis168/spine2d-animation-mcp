"""FastMCP server: build, inspect, check and edit Spine 4.2 skeletons."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .edits import report
from .pipeline import import_psd_to_spine as _import
from .spine import document as d
from .spine.check import check, summary
from .spine.inspect import inspect

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


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
