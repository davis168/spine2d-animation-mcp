"""End-to-end PSD -> Spine project conversion, in a given format version.

Pure function (no MCP/IO framework coupling) so it's directly testable. The MCP
tool in `server.py` is a thin wrapper over this that first works out which
format version the game project needs.

A PSD whose layer names use Spine's Import PSD tags (`[bone]`, `[slot]`,
`[skin]`, ...) is imported the way the Spine editor's Import PSD does it
(psd/tags.py, spine/psd_import.py). A PSD without tags whose layers are named
after body parts (head, arm_left, ...) is auto-rigged (spine/builder.py); any
other untagged PSD gets Spine's plain import, one slot per layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from psd_tools import PSDImage

from .atlas.packer import Region, pack
from .atlas.writer import compose_pages, write_atlas_text
from .psd import conventions as conv
from .psd import tags
from .psd.parser import parse_psd
from .spine import document as d
from .spine import psd_import, versions
from .spine.builder import build
from .spine.check import check
from .spine.writer import write_json
from .spine_cli import header_release

# The Spine editor displays individual part images, not the atlas; the
# skeleton's `images` path (relative to the JSON) tells it where they are.
IMAGES_DIR = "images"

SPINE_TAGS = "spine-tags"
BODY_PARTS = "body-parts"


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
    importer: str = BODY_PARTS  # SPINE_TAGS or BODY_PARTS
    warnings: list[str] = field(default_factory=list)


def import_psd_to_spine(
    psd_path: str,
    out_dir: str,
    *,
    format_version: str,
    scale: float = 1.0,
    padding: int = 1,
    trim: bool = True,
    ignore_hidden: bool = False,
) -> ImportResult:
    """Convert the PSD. `scale`, `padding`, `trim` and `ignore_hidden` are the
    Spine editor's Import PSD options and apply to the Spine-tag import."""
    psd_path = str(psd_path)
    stem = Path(psd_path).stem
    out = Path(out_dir)

    psd = PSDImage.open(psd_path)
    parsed = None
    if not tags.has_spine_tags(psd):
        parsed = parse_psd(psd)
        if not any(conv.canonical_part(layer.name) for layer in parsed.layers):
            parsed = None
    if parsed is None:
        return _import_tagged(psd, out, stem, format_version, scale, padding, trim, ignore_hidden)

    out.mkdir(parents=True, exist_ok=True)
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


def _import_tagged(psd: PSDImage, out: Path, stem: str, format_version: str, scale: float, padding: int,
                   trim: bool, ignore_hidden: bool) -> ImportResult:
    tagged = tags.read(psd, padding=padding, trim=trim, ignore_hidden=ignore_hidden)
    built = psd_import.build(tagged, spine=header_version(format_version), scale=scale)
    if not built.images:
        raise ValueError("the PSD has no layers with pixels to import")
    errors = [str(f) for f in check(built.doc) if f.level == "error"]
    if errors:
        raise ValueError("the imported skeleton would not load in Spine:\n" + "\n".join(errors))
    warnings = built.warnings
    skin = d.starting_skin(built.doc)
    if skin is not None:
        warnings.append(f"the setup pose's attachments are skin placeholders: show skin {skin!r} to see them")

    out.mkdir(parents=True, exist_ok=True)
    project_path = d.save(built.doc, out / f"{stem}.json")
    layout = pack([Region(path, im.width, im.height) for path, im in built.images.items()])
    atlas_path = write_atlas_text(layout, stem, out / f"{stem}.atlas")
    png_paths = compose_pages(layout, built.images, stem, out)
    images_dir = _write_part_images(built.images, out / IMAGES_DIR)
    return ImportResult(project_path, atlas_path, png_paths, images_dir, SPINE_TAGS, warnings)


def _write_part_images(images: dict, images_dir: Path) -> Path:
    """One PNG per atlas region, at its name (the attachment `path`; may hold folders)."""
    images_dir.mkdir(parents=True, exist_ok=True)
    for name, img in images.items():
        path = images_dir / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGBA").save(path, "PNG")
    return images_dir
