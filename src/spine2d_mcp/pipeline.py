"""End-to-end PSD -> Spine project conversion, in a given format version.

Pure function (no MCP/IO framework coupling) so it's directly testable. The MCP
tool in `server.py` is a thin wrapper over this that first works out which
format version the game project needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .atlas.writer import compose_pages, write_atlas_text
from .psd.parser import parse_psd
from .spine import versions
from .spine.builder import build
from .spine.writer import write_json
from .spine_cli import header_release

# The Spine editor displays individual part images, not the atlas; the
# skeleton's `images` path (relative to the JSON) tells it where they are.
IMAGES_DIR = "images"


def header_version(format_version: str) -> str:
    """The `skeleton.spine` value for a new file: the editor release that would
    import it (runtimes read only the major.minor; the 4.3 editor warns on any
    other release)."""
    return header_release(versions.get(format_version).version)


@dataclass
class ImportResult:
    project_path: Path
    atlas_path: Path
    png_paths: list[Path]
    images_dir: Path


def import_psd_to_spine(psd_path: str, out_dir: str, *, format_version: str) -> ImportResult:
    psd_path = str(psd_path)
    stem = Path(psd_path).stem
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    parsed = parse_psd(psd_path)
    result = build(parsed)
    result.project.skeleton.spine = header_version(format_version)
    result.project.skeleton.images = f"./{IMAGES_DIR}/"

    project_path = write_json(result.project, out / f"{stem}.json")
    atlas_path = write_atlas_text(result.atlas, stem, out / f"{stem}.atlas")
    images = {name: layer.image for name, layer in result.images.items()}
    png_paths = compose_pages(result.atlas, images, stem, out)
    images_dir = _write_part_images(images, out / IMAGES_DIR)

    return ImportResult(
        project_path=project_path,
        atlas_path=atlas_path,
        png_paths=png_paths,
        images_dir=images_dir,
    )


def _write_part_images(images: dict, images_dir: Path) -> Path:
    """One PNG per attachment, named after it (the attachment `path` default)."""
    images_dir.mkdir(parents=True, exist_ok=True)
    for name, img in images.items():
        img.convert("RGBA").save(images_dir / f"{name}.png", "PNG")
    return images_dir
