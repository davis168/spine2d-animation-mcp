"""Region attachments from PNGs, and repacking the atlas from the images folder.

The skeleton's `images` path (relative to the JSON; the importer writes
"./images/") holds one PNG per attachment path, which is what the Spine editor
displays; the atlas the runtime loads is packed from that folder.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

from PIL import Image

from ..atlas.packer import Region, pack
from ..atlas.writer import compose_pages, write_atlas_text
from ..edits import EditError
from ..spine import document as d


def images_dir(project_path: str | Path, doc: dict) -> Path:
    images = (doc.get("skeleton") or {}).get("images") or "./images/"
    return (Path(project_path).parent / images).resolve()


def image_size(image_path: str | Path) -> tuple[int, int]:
    path = Path(image_path)
    if path.suffix.lower() != ".png" or not path.is_file():
        raise EditError(f"{path}: not a PNG file")
    with Image.open(path) as im:
        return im.size


def add_region(
    doc: dict,
    slot: str,
    name: str,
    size: tuple[int, int],
    skin: str = "default",
    x: float = 0.0,
    y: float = 0.0,
    rotation: float = 0.0,
    scaleX: float = 1.0,
    scaleY: float = 1.0,
    world: bool = False,
    set_as_setup: bool = False,
    replace: bool = False,
) -> dict:
    """Add a region attachment of `size` (w, h) to a slot. x, y, rotation are
    relative to the slot's bone, or world-space with `world`."""
    slot_entry = d.slot_map(doc).get(slot)
    if slot_entry is None:
        raise EditError(f"no slot {slot!r}")
    if world:
        t = d.world_transforms(doc)[slot_entry["bone"]]
        x, y = t.inverse_vector(x - t.x, y - t.y)
        rotation -= math.degrees(math.atan2(t.c, t.a))
    skin_entry = d.find_skin(doc, skin)
    if skin_entry is None:
        skin_entry = {"name": skin, "attachments": {}}
        doc.setdefault("skins", []).append(skin_entry)
    atts = skin_entry.setdefault("attachments", {}).setdefault(slot, {})
    if name in atts and not replace:
        raise EditError(f"slot {slot!r} already has an attachment {name!r} in skin {skin!r} (pass replace=true)")
    att: dict = {"width": float(size[0]), "height": float(size[1])}
    for key, value, default in (("x", x, 0), ("y", y, 0), ("rotation", rotation, 0),
                                ("scaleX", scaleX, 1), ("scaleY", scaleY, 1)):
        if round(value, 4) != default:
            att[key] = round(value, 4)
    atts[name] = att
    if set_as_setup:
        slot_entry["attachment"] = name
    return {"slot": slot, "attachment": name, "skin": skin, **att}


def attachment_paths(doc: dict) -> set[str]:
    """Atlas region names the skins need (region and mesh paths)."""
    paths = set()
    for skin in d.skins(doc):
        for atts in (skin.get("attachments") or {}).values():
            for name, att in (atts or {}).items():
                if att.get("type", "region") in ("region", "mesh") and "sequence" not in att:
                    paths.add(att.get("path", att.get("name", name)))
    return paths


def copy_image(image_path: str | Path, folder: Path, name: str) -> Path:
    target = folder / f"{name}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    if Path(image_path).resolve() != target.resolve():
        shutil.copyfile(image_path, target)
    return target


def repack(project_path: str | Path, atlas_path: str | Path | None = None) -> dict:
    """Pack every PNG in the images folder into the atlas next to the JSON."""
    project_path = Path(project_path)
    doc = d.load(project_path)
    folder = images_dir(project_path, doc)
    if not folder.is_dir():
        raise EditError(f"no images folder at {folder} (set skeleton.images or create it)")
    files = {p.relative_to(folder).with_suffix("").as_posix(): p for p in sorted(folder.rglob("*.png"))}
    missing = sorted(attachment_paths(doc) - set(files))
    if missing:
        raise EditError(f"cannot repack: no image in {folder} for {', '.join(missing)}")
    images = {}
    for name, path in files.items():
        with Image.open(path) as im:
            images[name] = im.convert("RGBA")
    layout = pack([Region(name, im.width, im.height) for name, im in images.items()])
    atlas_path = Path(atlas_path) if atlas_path else project_path.with_suffix(".atlas")
    stem = atlas_path.stem
    write_atlas_text(layout, stem, atlas_path)
    pages = compose_pages(layout, images, stem, atlas_path.parent)
    return {
        "atlas_path": str(atlas_path),
        "png_paths": [str(p) for p in pages],
        "regions": len(images),
        "pages": [list(p) for p in layout.pages],
    }
