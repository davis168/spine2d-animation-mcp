"""Meshes and weights, linked meshes, bounding box / point / path
attachments with path constraints, image sequences, skins with skin bones
and constraints, bone display fields, and runtime releases across 4.3.x."""

import json
import math
import shutil
from pathlib import Path

import pytest
from PIL import Image

from spine2d_mcp import server, spine_cli
from spine2d_mcp.edits import EditError
from spine2d_mcp.ops import attachments as att
from spine2d_mcp.ops import constraints as con
from spine2d_mcp.ops import meshes, rig, shapes, skins
from spine2d_mcp.ops.animation import add_animation
from spine2d_mcp.runtime import _run, compare_poses
from spine2d_mcp.spine import document as d
from spine2d_mcp.spine.check import check
from spine2d_mcp.spine.convert import LossyConversion, convert

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
needs_spine = pytest.mark.skipif(spine_cli.find_spine() is None, reason="Spine editor not installed")


@pytest.fixture
def doc(imported):
    return d.load(imported.project_path)


@pytest.fixture
def doc43(imported43):
    return d.load(imported43.project_path)


@pytest.fixture
def art(tmp_path):
    folder = tmp_path / "art"
    folder.mkdir()
    for i, color in enumerate(((255, 80, 0, 255), (255, 160, 0, 255), (255, 230, 0, 255))):
        Image.new("RGBA", (30, 50), color).save(folder / f"flame{i}.png")
    Image.new("RGBA", (40, 140), (90, 90, 200, 255)).save(folder / "armor_arm.png")
    return folder


def _errors(doc):
    return [str(f) for f in check(doc) if f.level == "error"]


def _warnings(doc):
    return [str(f) for f in check(doc) if f.level == "warning"]


def _att(doc, slot, name, skin="default"):
    return d.find_skin(doc, skin)["attachments"][slot][name]


def _close(a, b, tol=0.01):
    return all(abs(p - q) <= tol for pa, pb in zip(a, b) for p, q in zip(pa, pb)) and len(a) == len(b)


# ── meshes ──────────────────────────────────────────────────────────────────

def test_grid_layout():
    uvs, hull, triangles, edges = meshes.grid(1, 1)
    assert uvs == [0, 0, 1, 0, 1, 1, 0, 1] and hull == 4 and triangles == [0, 1, 2, 0, 2, 3]
    assert edges == [0, 2, 2, 4, 4, 6, 6, 0]
    uvs, hull, triangles, edges = meshes.grid(3, 2)
    count = len(uvs) // 2
    assert count == 12 and hull == 10 and len(triangles) == 3 * 2 * 6 and max(triangles) == count - 1
    assert all(0 <= u <= 1 for u in uvs) and all(e % 2 == 0 and e // 2 < count for e in edges)
    hull_uv = [(uvs[2 * i], uvs[2 * i + 1]) for i in range(hull)]
    assert sum(hull_uv[i - 1][0] * hull_uv[i][1] - hull_uv[i][0] * hull_uv[i - 1][1] for i in range(hull)) > 0
    with pytest.raises(EditError, match="grid"):
        meshes.grid(0, 2)


def _region_corners(doc, slot, name):
    region = _att(doc, slot, name)
    t = d.world_transforms(doc)[d.slot_map(doc)[slot]["bone"]]
    w, h = region["width"], region["height"]
    local = meshes._placed([0, 0, 1, 0, 1, 1, 0, 1], w, h, region.get("x", 0), region.get("y", 0),
                           region.get("rotation", 0), region.get("scaleX", 1), region.get("scaleY", 1))
    return [t.apply(x, y) for x, y in local]


def test_region_becomes_a_mesh_in_place(doc43):
    corners = _region_corners(doc43, "arm_left", "arm_left")
    result = meshes.add_mesh(doc43, "arm_left", "arm_left", grid_size=(1, 3))
    mesh = _att(doc43, "arm_left", "arm_left")
    assert result["from_region"] == "arm_left" and result["vertices"] == 8 and mesh["type"] == "mesh"
    points = meshes.world_points(doc43, "arm_left", mesh)
    assert _close([points[0], points[1], points[4], points[5]], corners)  # hull corners where the region was
    assert check(doc43) == []
    with pytest.raises(EditError, match="no region attachment"):
        meshes.add_mesh(doc43, "arm_left", "nope")


def test_weighted_mesh_stays_put_and_follows_the_nearest_bone(doc43):
    meshes.add_mesh(doc43, "arm_left", "arm_left", grid_size=(1, 4))
    before = meshes.world_points(doc43, "arm_left", _att(doc43, "arm_left", "arm_left"))
    result = meshes.set_weights(doc43, "arm_left", "arm_left", bones=["arm_left", "hand_left"])
    mesh = _att(doc43, "arm_left", "arm_left")
    assert result["weighted"] and set(result["bones"]) == {"arm_left", "hand_left"}
    assert _close(meshes.world_points(doc43, "arm_left", mesh), before)
    index = {b["name"]: i for i, b in enumerate(d.bones(doc43))}
    weights, i = [], 0
    while i < len(mesh["vertices"]):
        n = mesh["vertices"][i]
        w = {mesh["vertices"][k]: mesh["vertices"][k + 3] for k in range(i + 1, i + 1 + 4 * n, 4)}
        assert abs(sum(w.values()) - 1) < 1e-6
        weights.append(w)
        i += 1 + 4 * n
    hand = d.world_transforms(doc43)["hand_left"]
    nearest = min(range(len(before)), key=lambda k: math.hypot(before[k][0] - hand.x, before[k][1] - hand.y))
    assert weights[nearest].get(index["hand_left"], 0) > 0.5
    assert check(doc43) == []
    meshes.set_weights(doc43, "arm_left", "arm_left", bones=[])  # unbind
    assert not rig._is_weighted(mesh) and _close(meshes.world_points(doc43, "arm_left", mesh), before)


def test_weights_refusals(doc43):
    meshes.add_mesh(doc43, "arm_left", "arm_left", grid_size=(1, 1))
    with pytest.raises(EditError, match="one {bone: weight} per vertex"):
        meshes.set_weights(doc43, "arm_left", "arm_left", weights=[{"arm_left": 1}])
    with pytest.raises(EditError, match="no bone 'ghost'"):
        meshes.set_weights(doc43, "arm_left", "arm_left", bones=["ghost"])
    meshes.set_weights(doc43, "arm_left", "arm_left", weights=[{"arm_left": 1}, {"arm_left": 3, "hand_left": 1},
                                                               {"hand_left": 1}, {"arm_left": 1}])
    add_animation(doc43, "a", timelines={"attachments": {"default": {"arm_left": {"arm_left": {
        "deform": [{"vertices": [1, 1]}]}}}}})
    with pytest.raises(EditError, match="deform keys in a"):
        meshes.set_weights(doc43, "arm_left", "arm_left", bones=["arm_left"])


def test_linked_meshes(doc, doc43):
    for version_doc in (doc, doc43):
        meshes.add_mesh(version_doc, "arm_left", "arm_left")
        skins.add_skin(version_doc, "armor")
        meshes.add_linked_mesh(version_doc, "arm_left", "arm_left", "arm_left", skin="armor", path="armor_arm",
                               timelines=False)
        linked = _att(version_doc, "arm_left", "arm_left", "armor")
        assert linked[d.linked_mesh_key(version_doc)] == "arm_left" and linked["timelines"] is False
        assert "skin" not in linked and check(version_doc) == []
    with pytest.raises(EditError, match="needs format version 4.3"):
        meshes.add_linked_mesh(doc, "arm_right", "copy", "arm_left", source_slot="arm_left")
    meshes.add_linked_mesh(doc43, "arm_right", "copy", "arm_left", source_slot="arm_left")
    assert _att(doc43, "arm_right", "copy")["slot"] == "arm_left" and check(doc43) == []
    with pytest.raises(EditError, match="links to a mesh, not to another linked mesh"):
        meshes.add_linked_mesh(doc43, "arm_left", "x", "arm_left", source_skin="armor", skin="armor")


# ── shapes and path constraints ─────────────────────────────────────────────

def test_path_lengths_and_handles():
    straight = shapes._handles([{"x": 0, "y": 0}, {"x": 100, "y": 0}], closed=False)
    assert straight[1] == (0, 0) and straight[2] == pytest.approx((100 / 3, 0))
    assert shapes.path_lengths(straight, closed=False) == [pytest.approx(100, abs=0.01)] * 2
    square = [{"x": x, "y": y} for x, y in ((0, 0), (100, 0), (100, 100), (0, 100))]
    lengths = shapes.path_lengths(shapes._handles(square, closed=True), closed=True)
    assert len(lengths) == 4 and lengths == sorted(lengths) and 350 < lengths[-1] < 450


def test_shapes(doc43):
    rig.add_slot(doc43, "hitbox", "torso")
    rig.add_slot(doc43, "trail", "root")
    torso = d.world_transforms(doc43)["torso"]
    shapes.add_bounding_box(doc43, "hitbox", [[torso.x, torso.y], [torso.x + 10, torso.y], [torso.x, torso.y + 10]],
                            world=True)
    assert _att(doc43, "hitbox", "bounds")["vertices"][:4] == [0, 0, 10, 0]
    shapes.add_point(doc43, "head", "mouth", x=0, y=10, rotation=-90)
    result = shapes.add_path(doc43, "trail", [[-100, 200], [0, 300], [100, 200]], closed=False)
    path = _att(doc43, "trail", "path")
    assert path["vertexCount"] == 9 and len(path["lengths"]) == 3 and result["length"] > 200
    assert d.slot_map(doc43)["trail"]["attachment"] == "path" and check(doc43) == []
    with pytest.raises(EditError, match="at least 2 points"):
        shapes.add_path(doc43, "trail", [[0, 0]], name="short")
    with pytest.raises(EditError, match="one attachment at a time"):
        shapes.add_bounding_box(doc43, "head", [[0, 0], [1, 0], [0, 1]])


def test_path_constraints(doc, doc43):
    for version_doc in (doc, doc43):
        rig.add_slot(version_doc, "trail", "root")
        rig.add_bone(version_doc, "orb", "root", y=300)
        with pytest.raises(EditError, match="no path attachment"):
            con.add_constraint(version_doc, "path", "follow", bones=["orb"], target="trail")
        shapes.add_path(version_doc, "trail", [[-100, 200], [0, 300], [100, 200]])
        with pytest.raises(EditError, match="rotateMode must be"):
            con.add_constraint(version_doc, "path", "follow", bones=["orb"], target="trail", rotateMode="spin")
        con.add_constraint(version_doc, "path", "follow", bones=["orb"], target="trail", mixX=0.5, position=0.3)
        assert _errors(version_doc) == []
    (c42,) = d.constraints(doc, "path")
    (c43,) = d.constraints(doc43, "path")
    assert c42["target"] == "trail" and c42["mixY"] == 0.5  # 4.2 would read 1
    assert c43["slot"] == "trail" and "mixY" not in c43  # 4.3 reads mixX


# ── sequences ───────────────────────────────────────────────────────────────

def test_sequences(doc43):
    seq = att.make_sequence(3, setup=1)
    assert seq == {"count": 3, "digits": 2, "setup": 1}
    assert att.sequence_names("flame_", seq) == ["flame_01", "flame_02", "flame_03"]
    with pytest.raises(EditError, match="setup must be 0..2"):
        att.make_sequence(3, setup=3)
    rig.add_slot(doc43, "fire", "head")
    att.add_region(doc43, "fire", "flame", (30, 50), sequence=seq, path="flame_", set_as_setup=True)
    assert {"flame_01", "flame_03"} <= att.attachment_paths(doc43) and "flame_" not in att.attachment_paths(doc43)
    add_animation(doc43, "burn", timelines={"attachments": {"default": {"fire": {"flame": {"sequence": [
        {"time": 0, "mode": "loop", "delay": 0.1}, {"time": 1, "mode": "hold", "index": 2}]}}}}})
    assert check(doc43) == []
    doc43["animations"]["burn"]["attachments"]["default"]["fire"]["flame"]["sequence"][1]["index"] = 5
    doc43["animations"]["burn"]["attachments"]["default"]["fire"]["flame"]["sequence"][0]["mode"] = "spin"
    errors = _errors(doc43)
    assert any("sequence[1].index" in e for e in errors) and any("unknown mode 'spin'" in e for e in errors)
    _att(doc43, "fire", "flame")["sequence"] = {"count": 2, "setup": 2}
    assert any("sequence.setup: must be below count" in e for e in _errors(doc43))


# ── skins, skin bones and constraints, display fields ───────────────────────

def test_skins(doc43):
    rig.add_bone(doc43, "cape", "torso", y=20, length=40)
    rig.add_bone(doc43, "cape_tip", "cape", x=40)
    con.add_constraint(doc43, "physics", "cape_sway", bone="cape", preset="cloth")
    skins.add_skin(doc43, "hero", copy_from="default")
    assert _att(doc43, "head", "head", "hero") == _att(doc43, "head", "head")
    done = skins.add_to_skin(doc43, "hero", bones=["cape"], constraints=["cape_sway"])
    hero = d.find_skin(doc43, "hero")
    assert hero["bones"] == ["cape"] and hero["physics"] == ["cape_sway"] and "cape_tip" in done["note"]
    assert d.bone_map(doc43)["cape"]["skin"] is True and check(doc43) == []
    skins.rename_skin(doc43, "hero", "caped")
    assert d.find_skin(doc43, "caped") is not None
    freed = skins.remove_from_skin(doc43, "caped", bones=["cape"])
    assert freed["active_for_every_skin"] == ["cape"] and "skin" not in d.bone_map(doc43)["cape"]
    d.bone_map(doc43)["cape"]["skin"] = True  # hand-made: a skin bone no skin lists
    assert any("a skin bone no skin lists" in w for w in _warnings(doc43))
    del d.bone_map(doc43)["cape"]["skin"]
    meshes.add_mesh(doc43, "arm_left", "arm_left")
    meshes.add_mesh(doc43, "arm_left", "arm_left", skin="caped")  # its copy of the region too
    meshes.add_linked_mesh(doc43, "arm_left", "alt", "arm_left", skin="caped")
    with pytest.raises(EditError, match="the default skin is what slots show"):
        skins.rename_skin(doc43, "default", "base")
    skins.add_skin(doc43, "spare")
    meshes.add_linked_mesh(doc43, "arm_right", "alt", "arm_left", skin="spare", source_skin="caped",
                           source_slot="arm_left")
    with pytest.raises(EditError, match="linked meshes use source meshes in skin 'caped'"):
        skins.remove_skin(doc43, "caped")
    skins.rename_skin(doc43, "caped", "hero")
    assert _att(doc43, "arm_right", "alt", "spare")["skin"] == "hero" and check(doc43) == []


def test_bone_display_fields(doc, doc43):
    rig.set_bone(doc43, "head", icon="star", iconSize=2, iconRotation=45, visible=False)
    head = d.bone_map(doc43)["head"]
    assert (head["icon"], head["iconSize"], head["iconRotation"], head["visible"]) == ("star", 2, 45, False)
    rig.set_bone(doc43, "head", iconSize=1, visible=True)
    assert "iconSize" not in head and "visible" not in head
    rig.set_slot(doc43, "head", visible=False)
    assert d.slot_map(doc43)["head"]["visible"] is False and check(doc43) == []
    with pytest.raises(EditError, match="iconSize needs format version 4.3"):
        rig.set_bone(doc, "head", iconSize=2)
    rig.set_bone(doc, "head", icon="star", visible=False)
    assert check(doc) == []
    down = convert(doc43, "4.2")
    assert "iconRotation" not in d.bone_map(down.doc)["head"]


def test_skin_art_keeps_its_own_image(imported43, art):
    path = str(imported43.project_path)
    images = imported43.project_path.parent / "images"
    original = (images / "arm_left.png").read_bytes()
    server.add_mesh(path, "arm_left", "arm_left")
    server.edit_skin(path, [{"op": "add_skin", "name": "armor"}])
    result = server.add_mesh(path, "arm_left", "arm_left", source="arm_left", skin="armor",
                             image_path=str(art / "armor_arm.png"))
    assert result["image"].endswith("images/armor/arm_left.png")
    assert (images / "arm_left.png").read_bytes() == original  # the default art is untouched
    doc = d.load(path)
    assert _att(doc, "arm_left", "arm_left", "armor")["path"] == "armor/arm_left"
    assert "armor/arm_left" in imported43.atlas_path.read_text() and check(doc) == []
    with pytest.raises(EditError, match="is the image of default/arm_left/arm_left"):
        server.add_attachment(path, "arm_right", str(art / "armor_arm.png"), name="arm_left", replace=True)
    assert (images / "arm_left.png").read_bytes() == original


def test_header_and_animation_colour(doc, doc43):
    rig.set_skeleton(doc43, referenceScale=50, fps=24, audio="./audio/")
    header = doc43["skeleton"]
    assert (header["referenceScale"], header["fps"], header["audio"]) == (50, 24, "./audio/")
    rig.set_skeleton(doc43, fps=30)  # the default is left out
    assert "fps" not in header
    with pytest.raises(EditError, match="referenceScale must be a number above 0"):
        rig.set_skeleton(doc43, referenceScale=0)
    with pytest.raises(EditError, match="no settable 'hash'"):
        rig.set_skeleton(doc43, hash="x")
    add_animation(doc43, "a", timelines={"color": "ff8800ff", "bones": {"head": {"rotate": [{"value": 0}]}}})
    assert check(doc43) == []
    doc43["animations"]["a"]["color"] = "nope"
    assert any("animations.a.color" in e for e in _errors(doc43))
    add_animation(doc, "a", timelines={"color": "ff8800ff", "bones": {"head": {"rotate": [{"value": 0}]}}})
    assert any("4.3 editor data" in w for w in _warnings(doc))
    doc43["animations"]["a"]["color"] = "ff8800ff"
    assert convert(doc43, "4.2").dropped_editor_data == ["animation 'a' color"]


def test_event_definitions(doc43):
    done = rig.edit_rig(doc43, [{"op": "set_event", "name": "step", "int": 2, "string": "left",
                                 "audio": "sfx/step.wav", "volume": 0.5}])
    assert doc43["events"]["step"] == {"int": 2, "string": "left", "audio": "sfx/step.wav", "volume": 0.5}
    assert done[0]["event"] == "step" and check(doc43) == []
    rig.set_event(doc43, "step", volume=1, string="")  # defaults are left out
    assert doc43["events"]["step"] == {"int": 2, "audio": "sfx/step.wav"}
    with pytest.raises(EditError, match="volume needs audio"):
        rig.set_event(doc43, "ping", volume=0.2)
    with pytest.raises(EditError, match="events have no 'pitch'"):
        rig.set_event(doc43, "ping", pitch=2)
    add_animation(doc43, "walk", timelines={"events": [{"time": 0.2, "name": "step"}]})
    with pytest.raises(EditError, match="keyed in walk"):
        rig.remove_event(doc43, "step")
    doc43["events"]["step"]["int"] = 1.5
    assert any("events.step.int: must be a whole number" in e for e in _errors(doc43))


# ── runtimes, conversion and the editor ─────────────────────────────────────

def _rich(path: str, art: Path, version: str) -> None:
    """Every new kind of content, through the MCP tools."""
    server.add_animation(path, "wave", preset="wave")
    server.add_mesh(path, "arm_left", "arm_left", bones=["arm_left", "hand_left"], grid=[1, 4])
    server.add_mesh(path, "arm_right", image_path=str(art / "armor_arm.png"), x=0, y=-70)
    server.edit_skin(path, [{"op": "add_skin", "name": "armor"}])
    server.add_mesh(path, "arm_left", "arm_left_armor", source="arm_left", image_path=str(art / "armor_arm.png"),
                    skin="armor")
    server.edit_rig(path, [{"op": "add_slot", "name": "trail", "bone": "root", "index": 0},
                           {"op": "add_slot", "name": "hitbox", "bone": "torso"},
                           {"op": "add_bone", "name": "orb", "parent": "root", "x": 0, "y": 300},
                           {"op": "add_slot", "name": "fire", "bone": "head"}])
    server.add_shape(path, "trail", "path", points=[[-150, 250], [0, 350], [150, 250]], world=True)
    server.add_constraint(path, "path", "follow", bones=["orb"], target="trail", params={"rotateMode": "chain"})
    server.add_animation(path, "orbit", timelines={"path": {"follow": {"position": [{"value": 0}, {"time": 1, "value": 1}]}}})
    server.add_shape(path, "hitbox", "boundingbox", vertices=[[-50, 0], [50, 0], [50, 180], [-50, 180]])
    server.add_shape(path, "head", "point", name="mouth", x=0, y=10, rotation=-90)
    server.add_attachment(path, "fire", name="flame", frames=[str(art / f"flame{i}.png") for i in range(3)],
                          set_as_setup=True)
    server.add_animation(path, "burn", timelines={"attachments": {"default": {"fire": {"flame": {"sequence": [
        {"time": 0, "mode": "loop", "delay": 0.1}, {"time": 0.5, "mode": "hold", "index": 2}]}}}}})
    server.add_animation(path, "bend", timelines={"bones": {"hand_left": {"rotate": [{"value": 0}, {"time": 0.5, "value": 60}]}}})
    server.edit_skin(path, [{"op": "add_to_skin", "skin": "armor", "bones": ["orb"], "constraints": ["follow"]}])
    if version == "4.3":
        server.edit_rig(path, [{"op": "set_bone", "name": "orb", "iconSize": 2, "icon": "star"}])


@needs_node
@pytest.mark.parametrize("version", ["4.2", "4.3"])
def test_the_runtime_plays_meshes_paths_and_sequences(version, imported, imported43, art):
    made = imported if version == "4.2" else imported43
    path = str(made.project_path)
    _rich(path, art, version)
    result = server.validate_skeleton(path, runtime=True)
    assert result["ok"], result
    by_name = {a["name"]: a for a in result["runtime"]["animations"]}
    assert by_name["burn"]["timelines"] == 1 and by_name["bend"]["finite"]
    orbit = next(a for a in result["runtime"]["animations"] if a["name"] == "orbit")
    assert orbit["max_bone_travel"] == 0  # orb and follow are armor-only and no skin is set
    per_skin = {s["name"]: s for s in result["runtime"]["skins"]}
    assert per_skin["armor"]["max_bone_travel"] > per_skin["default"]["max_bone_travel"]  # the orb moves with armor
    assert not per_skin["armor"]["missing"]
    server.edit_skin(path, [{"op": "remove_from_skin", "skin": "armor", "bones": ["orb"], "constraints": ["follow"]}])
    run = _run(Path(path), made.atlas_path, poses=False)
    orbit = next(a for a in run["animations"] if a["name"] == "orbit")
    assert run["ok"] and orbit["max_bone_travel"] > 100, run  # the path moves the orb


@needs_node
def test_converting_meshes_paths_and_skins_keeps_poses(imported43, art, tmp_path):
    path = str(imported43.project_path)
    _rich(path, art, "4.3")
    down = server.convert_skeleton(path, "4.2", out_path=str(tmp_path / "rich-4.2.json"),
                                   atlas_path=str(imported43.atlas_path))
    assert down["converted"] and down["verify"]["ok"], down
    assert "bone 'orb' iconSize" in down["dropped_editor_data"]
    server.add_mesh(path, "arm_right", "copy", source="arm_left", source_slot="arm_left")
    with pytest.raises(LossyConversion, match="uses a source mesh in slot 'arm_left'"):
        convert(d.load(path), "4.2")


@needs_node
def test_the_harness_plays_every_43_release(imported43, art):
    path = str(imported43.project_path)
    _rich(path, art, "4.3")
    for release in ("4.3.0", "4.3.13"):  # 4.3.6 changed Animation.apply; both sides of it
        run = _run(Path(path), imported43.atlas_path, poses=True, release=release)
        assert run["ran"] and run["ok"] and run["runtime"] == f"spine-core {release}", run
    assert _run(Path(path), imported43.atlas_path, poses=False, release="4.2.120")["reason"].startswith(
        "spine-core 4.2.120 cannot load a 4.3 file")


@needs_node
def test_validate_uses_the_games_installed_runtime(game, synthetic_psd):
    root = game(package={"@esotericsoftware/spine-phaser-v4": "~4.3.0"},
                installed={"@esotericsoftware/spine-phaser-v4": "4.3.0"})
    made = server.import_psd_to_spine(synthetic_psd, str(root / "assets"))
    result = server.validate_skeleton(made["project_path"], runtime=True)
    assert result["ok"] and result["runtime"]["runtime"] == "spine-core 4.3.0", result["runtime"]
    assert result["runtime"]["release_from"] == "the game project's installed runtime"


@needs_spine
@pytest.mark.parametrize("version", ["4.2", "4.3"])
def test_meshes_paths_sequences_and_skins_through_the_editor(version, imported, imported43, art, tmp_path):
    made = imported if version == "4.2" else imported43
    path = str(made.project_path)
    _rich(path, art, version)
    imported_project = server.spine_import(path)
    assert imported_project["ok"] and "changed_on_import" not in imported_project, imported_project
    out = server.spine_export(imported_project["spine_project"], str(tmp_path / "back"), "json",
                              format_version=version)
    back = json.loads(Path(next(f for f in out["files"] if f.endswith(".json"))).read_text())
    assert _errors(back) == []
    kinds = {a.get("type", "region") for s in back["skins"] for atts in s["attachments"].values() for a in atts.values()}
    assert {"mesh", "linkedmesh", "path", "boundingbox", "point", "region"} <= kinds
    armor = next(s for s in back["skins"] if s["name"] == "armor")
    assert armor["bones"] == ["orb"] and armor["path"] == ["follow"]
    flame = back["skins"][0]["attachments"]["fire"]["flame"]
    assert flame["sequence"]["count"] == 3 and flame["path"] == "flame_"
    arm = back["skins"][0]["attachments"]["arm_left"]["arm_left"]
    assert len(arm["vertices"]) > 2 * len(arm["uvs"]) // 2 and arm["hull"] == 10  # weighted, our hull kept
    ours = d.load(path)["skins"][0]["attachments"]["arm_left"]["arm_left"]
    assert compare_poses is not None and len(arm["uvs"]) == len(ours["uvs"])
