"""Spine Import PSD tags: synthetic tagged PSDs through import_psd_to_spine.

Placement is checked by redrawing the setup pose from the written JSON and
images and comparing it with the PSD's own composite.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from psd_tools import PSDImage
from psd_tools.api.layers import Group, PixelLayer
from psd_tools.constants import BlendMode, Resource
from psd_tools.psd.image_resources import ImageResource

from spine2d_mcp import server, spine_cli
from spine2d_mcp.pipeline import BODY_PARTS, SPINE_TAGS, import_psd_to_spine
from spine2d_mcp.psd.tags import PsdTagError, tag_names
from spine2d_mcp.spine import document as d
from spine2d_mcp.spine.check import check

needs_spine = pytest.mark.skipif(spine_cli.find_spine() is None, reason="Spine editor not installed")
SIZE = (200, 200)


def _gradient(w: int, h: int, color) -> Image.Image:
    """A tile that shows any flip or turn: colour fades left to right, alpha top to bottom."""
    x = np.linspace(0.4, 1, w)[None, :, None]
    y = np.linspace(1, 0.6, h)[:, None]
    rgb = (np.array(color[:3])[None, None, :] * x).repeat(h, axis=0)
    alpha = (255 * y).repeat(w, axis=1)[..., None]
    return Image.fromarray(np.concatenate([rgb, alpha], axis=-1).astype(np.uint8), "RGBA")


def layer(parent, name, box, color=(200, 60, 60), visible=True, blend=None, flat=False):
    left, top, w, h = box
    tile = Image.new("RGBA", (w, h), (*color, 255)) if flat else _gradient(w, h, color)
    px = PixelLayer.frompil(tile, parent, name, top=top, left=left)
    px.visible = visible
    if blend is not None:
        px.blend_mode = blend
    return px


def group(parent, name, visible=True):
    g = Group.new(parent, name)
    g.visible = visible
    return g


def save(psd: PSDImage, tmp_path: Path, name: str = "tagged.psd") -> str:
    path = tmp_path / name
    psd.save(path)
    return str(path)


def new_psd(guides: list[tuple[float, int]] | None = None) -> PSDImage:
    psd = PSDImage.new("RGBA", SIZE, color=(0, 0, 0, 0))
    if guides:
        data = struct.pack(">IIII", 1, 576, 576, len(guides))
        data += b"".join(struct.pack(">IB", round(at * 32), direction) for at, direction in guides)
        psd.image_resources[Resource.GRID_AND_GUIDES_INFO] = ImageResource(
            key=Resource.GRID_AND_GUIDES_INFO, name="", data=data)
    return psd


def run(path: str, tmp_path: Path, version: str = "4.3", **options):
    result = import_psd_to_spine(path, str(tmp_path / f"out{version}"), format_version=version, **options)
    return result, json.loads(result.project_path.read_text())


def world_bones(doc: dict) -> dict[str, tuple[float, float]]:
    world = {}
    for bone in doc["bones"]:
        px, py = world.get(bone.get("parent"), (0.0, 0.0))
        world[bone["name"]] = (px + bone.get("x", 0), py + bone.get("y", 0))
    return world


def attachments(doc: dict) -> dict[tuple[str, str, str], dict]:
    return {(skin["name"], slot, key): att for skin in doc["skins"]
            for slot, atts in skin["attachments"].items() for key, att in atts.items()}


def redraw(doc: dict, images: Path, skins: list[str], origin: tuple[float, float]) -> Image.Image:
    """The setup pose, drawn from the JSON and images like a runtime would (no bone rotation)."""
    world = world_bones(doc)
    by_skin = {s["name"]: s["attachments"] for s in doc["skins"]}
    pose = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    for slot in doc["slots"]:
        key = slot.get("attachment")
        att = None
        for skin in ["default", *skins]:
            att = (by_skin.get(skin, {}).get(slot["name"]) or {}).get(key) or att
        if att is None:
            continue
        source = att
        if att.get("type") == "linkedmesh":
            source = by_skin[att.get("skin", "default")][slot["name"]][att.get("source") or att.get("parent")]
        img = Image.open(images / f"{att.get('path', att.get('name', key))}.png").convert("RGBA")
        bx, by = world[slot["bone"]]
        if source.get("type") == "mesh":
            xs, ys = source["vertices"][0::2], source["vertices"][1::2]
            cx, cy = bx + (min(xs) + max(xs)) / 2, by + (min(ys) + max(ys)) / 2
        else:
            cx, cy = bx + att.get("x", 0), by + att.get("y", 0)
        if att.get("rotation"):
            img = img.rotate(att["rotation"], Image.BICUBIC, expand=True)
        if att.get("scaleX", 1) != 1:
            img = img.resize((round(img.width * att["scaleX"]), round(img.height * att["scaleY"])), Image.LANCZOS)
        placed = Image.new("RGBA", SIZE, (0, 0, 0, 0))
        placed.paste(img, (round(origin[0] + cx - img.width / 2), round(origin[1] - cy - img.height / 2)))
        pose = Image.alpha_composite(pose, placed)
    return pose


def psd_view(path: str) -> Image.Image:
    """What the PSD shows, without [ignore] and [origin] layers."""
    psd = PSDImage.open(path)

    def shown(node) -> bool:
        while node is not None and not isinstance(node, PSDImage):
            if not node.visible or {"ignore", "origin"} & set(tag_names(node.name)):
                return False
            node = node.parent
        return True

    return psd.composite(viewport=(0, 0, *SIZE), layer_filter=shown).convert("RGBA")


def difference(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute difference of the premultiplied pixels (0-255)."""
    def premultiplied(im):
        x = np.asarray(im, dtype=float)
        return np.concatenate([x[..., :3] * x[..., 3:] / 255, x[..., 3:]], axis=-1)
    return float(np.abs(premultiplied(a) - premultiplied(b)).mean())


def test_bones_slots_and_placement(tmp_path):
    psd = new_psd()
    layer(psd, "shadow", (50, 170, 100, 20), (40, 40, 40), blend=BlendMode.MULTIPLY)
    body = group(psd, "body [bone]")
    layer(body, "torso", (60, 80, 80, 60))
    arm = group(body, "arm [bone]")
    slot = group(arm, "arm [slot]")
    layer(slot, "arm-down", (140, 90, 20, 50), (60, 160, 60), visible=False)
    layer(slot, "arm-up", (140, 60, 20, 40), (60, 60, 160))
    path = save(psd, tmp_path)

    result, doc = run(path, tmp_path)
    assert result.importer == SPINE_TAGS
    assert [(b["name"], b.get("parent")) for b in doc["bones"]] == [("root", None), ("body", "root"), ("arm", "body")]
    world = world_bones(doc)
    assert world["body"] == (0, -10)  # the centre of its top visible layer, torso (y-up, origin at the canvas centre)
    assert world["arm"] == (50, 20)  # arm-up: arm-down is hidden
    assert doc["slots"] == [
        {"name": "shadow", "bone": "root", "attachment": "shadow", "blend": "multiply"},
        {"name": "torso", "bone": "body", "attachment": "torso"},
        {"name": "arm", "bone": "arm", "attachment": "arm-up"},
    ]
    atts = attachments(doc)
    assert set(atts) == {("default", "shadow", "shadow"), ("default", "torso", "torso"),
                         ("default", "arm", "arm-down"), ("default", "arm", "arm-up")}
    assert atts[("default", "arm", "arm-down")] == {"y": -35, "width": 22, "height": 52}  # padding 1
    assert atts[("default", "torso", "torso")] == {"width": 82, "height": 62}
    assert redraw(doc, result.images_dir, [], (100, 100)).getbbox() is not None
    assert difference(redraw(doc, result.images_dir, [], (100, 100)), psd_view(path)) < 0.05
    assert not [f for f in check(doc) if f.level == "error"]


@pytest.mark.parametrize("version,key", [("4.2", "parent"), ("4.3", "source")])
def test_skins_meshes_and_linked_meshes(tmp_path, version, key):
    psd = new_psd()
    head = group(psd, "head [slot]")
    blue = group(head, "blue [skin]")
    layer(blue, "face [mesh]", (70, 20, 60, 70), (60, 60, 200))
    layer(blue, "hat", (70, 5, 60, 20), (200, 200, 60), visible=False)  # a slot shows one attachment
    red = group(head, "red [skin]")
    layer(red, "face [mesh:blue/face]", (75, 25, 50, 60), (200, 60, 60), visible=False)
    path = save(psd, tmp_path)

    result, doc = run(path, tmp_path, version)
    atts = attachments(doc)
    face = atts[("blue", "head", "face")]
    assert face["name"] == "blue/face" and face["type"] == "mesh" and "path" not in face
    assert face["uvs"] == [1, 1, 0, 1, 0, 0, 1, 0] and face["triangles"] == [1, 2, 3, 1, 3, 0] and face["hull"] == 4
    assert (face["width"], face["height"]) == (62, 72)
    assert face["vertices"] == [31, 9, -31, 9, -31, 81, 31, 81]  # the head slot is on the root bone
    linked = atts[("red", "head", "face")]
    assert linked == {"name": "red/face", "type": "linkedmesh", "skin": "blue", key: "face", "width": 62, "height": 72}
    assert Image.open(result.images_dir / "red/face.png").size == (62, 72)  # cut with the source's frame
    assert doc["slots"] == [{"name": "head", "bone": "root", "attachment": "face"}]
    assert d.default_skin_clashes(doc) == []
    assert [s["name"] for s in doc["skins"]] == ["blue", "red"]
    assert difference(redraw(doc, result.images_dir, ["blue"], (100, 100)), psd_view(path)) < 0.05


def test_folders_paths_and_patterns(tmp_path):
    psd = new_psd()
    fx = group(psd, "fx [folder]")
    layer(fx, "spark [path:shared/spark]", (10, 10, 10, 10))
    layer(fx, "/glow", (30, 10, 10, 10))
    eyes = group(psd, "eyes [slots] [name:eye-*] [path:img/*]")
    layer(eyes, "left", (60, 60, 10, 10))
    layer(eyes, "right [!name] [!slots]", (90, 60, 10, 10))
    limbs = group(psd, "limbs [bones] [bone:b-*]")
    layer(limbs, "foot", (60, 150, 20, 10))
    path = save(psd, tmp_path)

    result, doc = run(path, tmp_path)
    atts = attachments(doc)
    assert atts[("default", "spark", "fx/spark")] == {"path": "fx/shared/spark", "x": -85, "y": 85,
                                                      "width": 12, "height": 12}
    assert ("default", "glow", "glow") in atts  # a leading / ignores the folder
    assert atts[("default", "eye-left", "eye-left")]["path"] == "img/left"  # path pattern uses the raw layer name
    assert ("default", "right", "right") in atts and atts[("default", "right", "right")]["path"] == "img/right"
    assert [b["name"] for b in doc["bones"]] == ["root", "b-foot"]
    assert {s["name"]: s["bone"] for s in doc["slots"]}["foot"] == "b-foot"
    for image in ("fx/shared/spark", "glow", "img/left", "img/right", "foot"):
        assert (result.images_dir / f"{image}.png").exists()
    atlas = result.atlas_path.read_text()
    assert "fx/shared/spark\n" in atlas and "img/left\n" in atlas


def test_scale_rotate_and_options(tmp_path):
    psd = new_psd()
    layer(psd, "big [scale:2]", (20, 20, 30, 40), (200, 80, 80))
    layer(psd, "turned [rotate:90]", (100, 20, 30, 50), (80, 200, 80))
    layer(psd, "tilted [rotate:30]", (60, 110, 40, 60), (80, 80, 200))
    layer(psd, "full [trim:false] [pad:0]", (150, 150, 10, 10), (220, 220, 60))
    path = save(psd, tmp_path)

    result, doc = run(path, tmp_path)
    atts = attachments(doc)
    big = atts[("default", "big", "big")]
    assert (big["width"], big["height"], big["scaleX"], big["scaleY"]) == (62, 82, 0.5, 0.5)
    turned = atts[("default", "turned", "turned")]
    assert (turned["width"], turned["height"], turned["rotation"]) == (52, 32, -90)
    assert (turned["x"], turned["y"]) == (15, 55)  # still at the layer's centre
    assert atts[("default", "tilted", "tilted")]["rotation"] == -30
    full = atts[("default", "full", "full")]
    assert (full["width"], full["height"]) == SIZE and "x" not in full  # canvas-sized, centred on the origin
    assert difference(redraw(doc, result.images_dir, [], (100, 100)), psd_view(path)) < 0.5

    half, small = run(path, tmp_path / "half", scale=0.5, padding=0, trim=False)
    turned = attachments(small)[("default", "turned", "turned")]
    assert (turned["width"], turned["height"]) == (100, 100) and "x" not in turned  # canvas-sized
    big = attachments(small)[("default", "big", "big")]
    assert (big["width"], big["height"]) == (200, 200) and "x" not in big  # 0.5 x [scale:2]


@pytest.mark.parametrize("degrees", [30, 135, -60])
def test_rotated_odd_shapes_stay_in_place(tmp_path, degrees):
    """Trimming after the turn moves the image centre; the attachment follows it."""
    psd = new_psd()
    ell = np.zeros((60, 50, 4), np.uint8)
    ell[:, :15] = (200, 80, 80, 255)
    ell[45:, :] = (80, 80, 200, 255)
    PixelLayer.frompil(Image.fromarray(ell, "RGBA"), psd, f"ell [rotate:{degrees}]", top=40, left=70)
    path = save(psd, tmp_path)
    result, doc = run(path, tmp_path, padding=0)
    att = attachments(doc)[("default", "ell", "ell")]
    assert att["rotation"] == -degrees and (att["x"], att["y"]) != (-5, 30)  # not the layer's centre
    assert difference(redraw(doc, result.images_dir, [], (100, 100)), psd_view(path)) < 1


def test_origin_from_layer_and_guides(tmp_path):
    psd = new_psd(guides=[(40, 0), (150, 1), (90, 0)])  # vertical x=40 (the first counts), horizontal y=150
    layer(psd, "dot", (60, 60, 20, 20))
    by_guides = save(psd, tmp_path, "guides.psd")
    psd = new_psd(guides=[(40, 0)])
    layer(psd, "dot", (60, 60, 20, 20))
    layer(psd, "origin [origin]", (99, 199, 2, 2))
    by_layer = save(psd, tmp_path, "layer.psd")

    _, doc = run(by_guides, tmp_path / "g")
    assert attachments(doc)[("default", "dot", "dot")] | {} == {"x": 30, "y": 80, "width": 22, "height": 22}
    _, doc = run(by_layer, tmp_path / "l")
    assert {(a.get("x"), a.get("y")) for a in attachments(doc).values()} == {(-30, 130)}  # the layer beats guides
    assert [s["name"] for s in doc["slots"]] == ["dot"]  # [origin] makes no attachment


def test_merge_overlay_clipping_and_ignore(tmp_path):
    psd = new_psd()
    hidden = group(psd, "spare", visible=False)
    layer(hidden, "spare-part", (0, 0, 10, 10))
    layer(psd, "base", (20, 20, 40, 40), (200, 0, 0), flat=True)
    clip = layer(psd, "clip", (0, 0, 40, 40), (0, 0, 200), flat=True)
    clip.clipping = True
    merged = group(psd, "badge [merge]")
    layer(merged, "back [bone]", (100, 20, 30, 30), (0, 200, 0), flat=True)
    layer(merged, "front", (110, 30, 30, 30), (0, 0, 200), flat=True)
    layer(merged, "off", (140, 40, 20, 20), (200, 200, 200), visible=False)
    layer(merged, "skip [ignore]", (90, 10, 5, 5))
    layer(psd, "wash [overlay]", (0, 0, 200, 200), (255, 255, 255), flat=True)
    sources = group(psd, "sources [ignore]")
    layer(sources, "reference", (0, 0, 200, 200))
    path = save(psd, tmp_path)

    result, doc = run(path, tmp_path)
    assert [s["name"] for s in doc["slots"]] == ["spare-part", "base", "badge"]
    assert [b["name"] for b in doc["bones"]] == ["root"]  # tags inside a [merge] group do nothing
    assert "attachment" not in doc["slots"][0]  # hidden: imported, not shown
    base = Image.open(result.images_dir / "base.png")
    assert base.size == (42, 42) and base.getpixel((5, 5)) == (255, 255, 255, 255)  # clipped blue, then the overlay
    badge = Image.open(result.images_dir / "badge.png")
    assert badge.size == (42, 42) and badge.getpixel((30, 30))[3] == 255

    _, doc = run(path, tmp_path / "visible", ignore_hidden=True)
    assert [s["name"] for s in doc["slots"]] == ["base", "badge"]


def test_split_slot_folders_move_to_their_last_slot(tmp_path):
    psd = new_psd()
    layer(psd, "a [slot:f/a]", (10, 10, 10, 10))
    layer(psd, "b [slot:g/b]", (30, 10, 10, 10))
    layer(psd, "c [slot:f/c]", (50, 10, 10, 10))
    _, doc = run(save(psd, tmp_path), tmp_path)
    assert [s["name"] for s in doc["slots"]] == ["g/b", "f/a", "f/c"]


def test_bad_tags_are_refused_with_every_problem(tmp_path):
    psd = new_psd()
    layer(psd, "thing [bogus]", (10, 10, 10, 10))
    pattern = group(psd, "names [name:no-star]")
    layer(pattern, "x", (10, 30, 10, 10))
    one = group(psd, "a [bone:arm]")
    layer(one, "p", (30, 10, 10, 10))
    two = group(psd, "b [bone]")
    other = group(two, "c [bone:arm]")
    layer(other, "q", (30, 30, 10, 10))
    skin = group(psd, "default [skin]")
    layer(skin, "r", (50, 10, 10, 10))
    dupes = group(psd, "s [slot]")
    layer(dupes, "same", (70, 10, 10, 10))
    layer(dupes, "same", (70, 30, 10, 10))
    layer(psd, "lonely [mesh:missing]", (90, 10, 10, 10))
    path = save(psd, tmp_path)

    with pytest.raises(PsdTagError) as refused:
        run(path, tmp_path)
    text = str(refused.value)
    for expected in ("[bogus] is not an Import PSD tag", "needs a * where the layer name goes",
                     "bone 'arm' has two parents", '"default" is the default skin', "two 'same' attachments"):
        assert expected in text, text
    assert not (tmp_path / "out4.3").exists()

    psd = new_psd()
    layer(psd, "lonely [mesh:missing]", (90, 10, 10, 10))
    with pytest.raises(PsdTagError, match=r"\[mesh:missing\] names no \[mesh\] layer"):
        run(save(psd, tmp_path, "linked.psd"), tmp_path)


def test_untagged_psds(synthetic_psd, tmp_path):
    result, _ = run(synthetic_psd, tmp_path / "parts")
    assert result.importer == BODY_PARTS  # body-part names: auto-rigged as before

    psd = new_psd()
    layer(psd, "sky", (0, 0, 200, 100), (90, 140, 220))
    layer(psd, "tree", (80, 60, 40, 100), (40, 120, 40))
    result, doc = run(save(psd, tmp_path), tmp_path / "plain")
    assert result.importer == SPINE_TAGS  # Spine's plain import: a slot per layer on the root bone
    assert doc["slots"] == [{"name": "sky", "bone": "root", "attachment": "sky"},
                            {"name": "tree", "bone": "root", "attachment": "tree"}]


def test_mcp_tool_reports_the_importer(tmp_path):
    psd = new_psd()
    slot = group(psd, "hand [slot]")
    layer(slot, "open", (10, 10, 20, 20), visible=False)
    skin = group(psd, "navy [skin]")
    layer(skin, "coat", (40, 40, 30, 30))
    (tmp_path / ".git").mkdir()
    res = server.import_psd_to_spine(save(psd, tmp_path), str(tmp_path / "out"), format_version="4.3",
                                     padding=2)
    assert res["importer"] == SPINE_TAGS and res["format_version"] == "4.3"
    assert res["summary"].startswith("spine 4.3") and "2 skins" in res["summary"]
    assert res["warnings"] == ["the setup pose's attachments are skin placeholders: show skin 'navy' to see them"]
    assert attachments(d.load(res["project_path"]))[("default", "hand", "open")]["width"] == 24


@needs_spine
@pytest.mark.parametrize("version", ["4.2", "4.3"])
def test_tagged_import_survives_the_editor(tmp_path, version):
    psd = new_psd()
    body = group(psd, "body [bone]")
    arm = group(body, "arm [bone]")
    head = group(arm, "arm [slot]")
    blue = group(head, "blue [skin]")
    layer(blue, "sleeve [mesh]", (70, 20, 40, 70))
    layer(blue, "cuff [rotate:90] [scale:2]", (70, 95, 40, 20), visible=False)
    red = group(head, "red [skin]")
    layer(red, "sleeve [mesh:sleeve]", (72, 22, 36, 66), (200, 60, 60), visible=False)
    layer(body, "torso [slot:parts/torso]", (60, 100, 80, 80), (60, 60, 200))
    result, doc = run(save(psd, tmp_path), tmp_path, version)

    made = server.spine_import(str(result.project_path))
    assert made["ok"], made
    out = server.spine_export(made["spine_project"], str(tmp_path / "back"), "json", format_version=version)
    back = json.loads(Path(next(f for f in out["files"] if f.endswith(".json"))).read_text())
    assert [f for f in check(back) if f.level == "error"] == []
    assert world_bones(back) == world_bones(doc)
    assert back["slots"] == doc["slots"]
    for key, att in attachments(doc).items():
        returned = attachments(back)[key]
        for field, value in att.items():
            if isinstance(value, list):
                assert returned[field] == pytest.approx(value, abs=0.01), (key, field)
            else:
                assert returned[field] == (pytest.approx(value, abs=0.01) if isinstance(value, float) else value)
