"""Region attachments from PNGs, and repacking the atlas from the images folder.

The skeleton's `images` path (relative to the JSON; the importer writes
"./images/") holds one PNG per attachment path, which is what the Spine editor
displays; the atlas the runtime loads is packed from that folder.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

from ..atlas import default_atlas
from ..atlas.packer import Region, pack
from ..atlas.writer import compose_pages, write_atlas_text
from ..edits import EditError
from ..spine import document as d
from ..spine import versions
from .rig import require_normal_chain

# Seen with the Spine 4.3.26 editor: its JSON import ignores both clipping flags.
EDITOR_CLIP_NOTE = ("the Spine 4.3 editor (4.3.26) ignores convex and inverse when it imports JSON: every clip "
                    "comes in convex (a concave one clips to its hull) and not inverse; spine_import records "
                    "them and spine_export puts them back in the project's JSON and binary exports")


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
    sequence: dict | None = None,
    path: str | None = None,
) -> dict:
    """Add a region attachment of `size` (w, h) to a slot. x, y, rotation are
    relative to the slot's bone, or world-space with `world`. `sequence`
    (see make_sequence) shows numbered images in turn."""
    slot_entry = d.slot_map(doc).get(slot)
    if slot_entry is None:
        raise EditError(f"no slot {slot!r}")
    if world:
        # Local = bone⁻¹ · wanted world matrix, so a mirrored or unevenly scaled
        # bone still shows the image at the given world angle and scale.
        t = d.world_transforms(doc)[slot_entry["bone"]]
        x, y = t.inverse_vector(x - t.x, y - t.y)
        wanted = d.local_matrix({"rotation": rotation, "scaleX": scaleX, "scaleY": scaleY})
        fields = d.decompose(d.mat_mul(d.mat_inv(d.matrix(t)), wanted))
        if abs(fields["shearY"]) > 0.01:
            raise EditError(f"bone {slot_entry['bone']!r} is scaled unevenly: the image would need a shear "
                            "at that angle; give x, y, rotation relative to the bone instead")
        rotation, scaleX, scaleY = fields["rotation"], fields["scaleX"], fields["scaleY"]
    atts = slot_attachments(doc, skin, slot, name, replace)
    att: dict = {"path": path} if path and path != name else {}
    att.update({"width": float(size[0]), "height": float(size[1])})
    for key, value, default in (("x", x, 0), ("y", y, 0), ("rotation", rotation, 0),
                                ("scaleX", scaleX, 1), ("scaleY", scaleY, 1)):
        if round(value, 4) != default:
            att[key] = round(value, 4)
    if sequence is not None:
        att["sequence"] = sequence
    atts[name] = att
    if set_as_setup:
        slot_entry["attachment"] = name
    return {"slot": slot, "attachment": name, "skin": skin, **att}


def slot_attachments(doc: dict, skin: str, slot: str, name: str, replace: bool) -> dict:
    """The skin's attachments for `slot` (the skin is created if missing),
    refusing to overwrite `name` unless `replace`."""
    skin_entry = d.find_skin(doc, skin)
    if skin_entry is None:
        skin_entry = {"name": skin, "attachments": {}}
        doc.setdefault("skins", []).append(skin_entry)
    atts = skin_entry.setdefault("attachments", {}).setdefault(slot, {})
    if name in atts and not replace:
        raise EditError(f"slot {slot!r} already has an attachment {name!r} in skin {skin!r} (pass replace=true)")
    return atts


def require_own_slot(doc: dict, slot: str, name: str, kind: str, what: str) -> None:
    """A slot shows one attachment at a time: refuse to show `name` at setup
    where the slot already shows an attachment of another type."""
    shown = d.slot_map(doc)[slot].get("attachment")
    if shown in (None, name):
        return
    shown_atts = [((s.get("attachments") or {}).get(slot) or {}).get(shown) for s in d.skins(doc)]
    if any(isinstance(a, dict) and a.get("type", "region") != kind for a in shown_atts):
        raise EditError(f"slot {slot!r} shows {shown!r} at setup, and a slot shows one attachment at a time: give "
                        f"{what} its own slot (edit_rig add_slot), or pass set_as_setup=false and key it on")


def to_bone_space(doc: dict, slot: str, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """World points as points relative to the slot's bone."""
    bone = d.slot_map(doc)[slot]["bone"]
    require_normal_chain(doc, bone)
    t = d.world_transforms(doc)[bone]
    return [t.inverse_vector(x - t.x, y - t.y) for x, y in points]


SEQUENCE_DIGITS = 2


def make_sequence(count: int, start: int = 1, digits: int = SEQUENCE_DIGITS, setup: int = 0) -> dict:
    """A region or mesh sequence: images <path><start>, <path><start+1>, ...
    (numbers padded to `digits`), showing image `setup` in the setup pose."""
    if not isinstance(count, int) or count < 1:
        raise EditError("a sequence needs at least one image")
    if not isinstance(start, int) or start < 0 or not isinstance(digits, int) or digits < 0:
        raise EditError("sequence start and digits must be whole numbers, 0 or more")
    if not isinstance(setup, int) or not 0 <= setup < count:
        raise EditError(f"sequence setup must be 0..{count - 1} (the image shown in the setup pose)")
    seq = {"count": count}
    for key, value, default in (("start", start, 1), ("digits", digits, 0), ("setup", setup, 0)):
        if value != default:
            seq[key] = value
    return seq


def sequence_names(path: str, sequence: dict) -> list[str]:
    """The atlas region names a sequence shows, in order."""
    start, digits = sequence.get("start", 1), sequence.get("digits", 0)
    return [f"{path}{str(start + i).zfill(digits)}" for i in range(sequence.get("count", 0))]


def points_of(vertices) -> list[tuple[float, float]]:
    """[[x, y], ...] or a flat [x, y, x, y, ...] list as points."""
    if isinstance(vertices, list) and all(isinstance(v, (list, tuple)) and len(v) == 2 for v in vertices):
        pairs = vertices
    elif isinstance(vertices, list) and len(vertices) % 2 == 0:
        pairs = [vertices[i:i + 2] for i in range(0, len(vertices), 2)]
    else:
        raise EditError("vertices must be [x, y] points")
    try:
        return [(float(x), float(y)) for x, y in pairs]
    except (TypeError, ValueError):
        raise EditError("vertices must be numbers") from None


def add_clipping(
    doc: dict,
    slot: str,
    vertices,
    name: str = "clip",
    skin: str = "default",
    end: str | None = None,
    world: bool = False,
    convex: bool = False,
    inverse: bool = False,
    set_as_setup: bool = True,
    replace: bool = False,
    color: str | None = None,
) -> dict:
    """Add a clipping attachment: a polygon on `slot` that clips the slots
    drawn after it, up to and including `end` (default: the last slot).
    Vertices are points relative to the slot's bone, or world points with
    `world`. convex (a faster clip for convex shapes) and inverse (hide
    what is inside) are 4.3 options."""
    slot_entry = d.slot_map(doc).get(slot)
    if slot_entry is None:
        raise EditError(f"no slot {slot!r}: add one with edit_rig add_slot, drawn just before the slots to clip")
    if versions.layout(doc) == "4.2" and (convex or inverse):
        raise EditError("convex and inverse clipping need format version 4.3; this skeleton is 4.2 "
                        "(convert_skeleton converts it)")
    points = points_of(vertices)
    if len(points) < 3:
        raise EditError("a clipping polygon needs at least 3 points")
    if world:
        points = to_bone_space(doc, slot, points)
    if convex and not d.is_convex(points):
        raise EditError("the polygon is not convex: leave convex off (it only speeds up convex shapes)")
    names = [s["name"] for s in d.slots(doc)]
    start = names.index(slot)
    if end is not None:
        if end not in names:
            raise EditError(f"no slot {end!r} to end the clip at")
        if names.index(end) <= start:
            raise EditError(f"end {end!r} is drawn before or at {slot!r}, but a clip covers the slots drawn after "
                            "its own, up to end")
    clipped = names[start + 1:names.index(end) + 1 if end else len(names)]
    if not clipped:
        raise EditError(f"{slot!r} is drawn last, so its clip covers nothing: draw it before the slots to clip "
                        "(edit_rig set_draw_order)")
    if set_as_setup:
        require_own_slot(doc, slot, name, "clipping", "the clip")
    atts = slot_attachments(doc, skin, slot, name, replace)
    att: dict = {"type": "clipping"}
    if end is not None:
        att["end"] = end
    att["vertexCount"] = len(points)
    att["vertices"] = [round(v, 4) for point in points for v in point]
    if color is not None:
        att["color"] = color
    if convex:
        att["convex"] = True
    if inverse:
        att["inverse"] = True
    atts[name] = att
    if set_as_setup:
        slot_entry["attachment"] = name
    notes = []
    if inverse and not d.is_convex(points):
        notes.append("inverse clipping always clips to the polygon's convex hull")
    if versions.layout(doc) != "4.2" and (inverse or not d.is_convex(points)):
        notes.append(EDITOR_CLIP_NOTE)
    return {"slot": slot, "clipping": name, "skin": skin, "clips": clipped, "points": len(points),
            **{k: True for k in ("convex", "inverse") if att.get(k)}, **({"notes": notes} if notes else {})}


def attachment_paths(doc: dict) -> set[str]:
    """Atlas region names the skins need (region and mesh paths, every
    image of a sequence)."""
    paths = set()
    for skin in d.skins(doc):
        for atts in (skin.get("attachments") or {}).values():
            for name, att in (atts or {}).items():
                if not isinstance(att, dict) or att.get("type", "region") not in ("region", "mesh", "linkedmesh"):
                    continue
                path = att.get("path", att.get("name", name))
                if isinstance(att.get("sequence"), dict):
                    paths.update(sequence_names(path, att["sequence"]))
                else:
                    paths.add(path)
    return paths


def image_files(folder: Path) -> dict[str, Path]:
    """Region name (path relative to the folder, no extension) -> PNG."""
    if not folder.is_dir():
        return {}
    return {p.relative_to(folder).with_suffix("").as_posix(): p for p in sorted(folder.rglob("*.png"))}


def image_users(doc: dict, region: str) -> list[tuple[str, str, str]]:
    """(skin, slot, attachment) of every attachment showing atlas region `region`."""
    users = []
    for skin in d.skins(doc):
        for slot, atts in (skin.get("attachments") or {}).items():
            for name, att in (atts or {}).items():
                if not isinstance(att, dict) or att.get("type", "region") not in ("region", "mesh", "linkedmesh"):
                    continue
                path = att.get("path", att.get("name", name))
                names = sequence_names(path, att["sequence"]) if isinstance(att.get("sequence"), dict) else [path]
                if region in names:
                    users.append((skin.get("name"), slot, name))
    return users


def check_new_images(doc: dict, project_path: str | Path, images: dict[str, str | Path], replace: bool,
                     owner: tuple[str, str, str] | None = None) -> None:
    """Before any write: the images (region name -> PNG) can be copied in and
    the atlas repacked. An image another attachment shows is never
    overwritten; `replace` overwrites only the image of `owner` (skin,
    slot, attachment) itself."""
    folder = images_dir(project_path, doc)
    for name, image_path in images.items():
        image_size(image_path)
        target = folder / f"{name}.png"
        if not target.exists() or target.resolve() == Path(image_path).resolve():
            continue
        others = [u for u in image_users(doc, name) if u != owner]
        if others:
            shown = ", ".join("/".join(u) for u in others)
            raise EditError(f"{target} is the image of {shown}: give this attachment another name (or skin), "
                            "so both keep their own image")
        if not replace:
            raise EditError(f"{target} already exists and is a different image (pass replace=true to overwrite it)")
    missing = sorted(attachment_paths(doc) - set(image_files(folder)) - set(images))
    if missing:
        raise EditError(f"cannot repack the atlas afterwards: no image in {folder} for {', '.join(missing)}")


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
    files = image_files(folder)
    missing = sorted(attachment_paths(doc) - set(files))
    if missing:
        raise EditError(f"cannot repack: no image in {folder} for {', '.join(missing)}")
    images = {}
    for name, path in files.items():
        with Image.open(path) as im:
            images[name] = im.convert("RGBA")
    layout = pack([Region(name, im.width, im.height) for name, im in images.items()])
    atlas_path = Path(atlas_path) if atlas_path else default_atlas(project_path)
    stem = atlas_path.stem
    write_atlas_text(layout, stem, atlas_path)
    pages = compose_pages(layout, images, stem, atlas_path.parent)
    return {
        "atlas_path": str(atlas_path),
        "png_paths": [str(p) for p in pages],
        "regions": len(images),
        "pages": [list(p) for p in layout.pages],
    }
