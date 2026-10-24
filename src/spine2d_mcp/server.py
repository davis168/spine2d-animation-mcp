"""FastMCP server exposing the PSD -> Spine 4.2 conversion as a tool."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .pipeline import import_psd_to_spine as _import

mcp = FastMCP("spine2d-mcp")


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


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
