"""Meshes and weights, linked meshes, bounding box / point / path
attachments with path constraints, image sequences, skins with skin bones
and constraints, bone display fields, and runtime releases across 4.3.x."""

import copy
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
        meshes.add_linked_mesh(version_doc, "arm_left", "armored", "arm_left", skin="armor", path="armor_arm",
                               timelines=False)
        linked = _att(version_doc, "arm_left", "armored", "armor")
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
    with pytest.raises(EditError, match="show with every skin already"):
        skins.add_skin(doc43, "hero", copy_from="default")
    skins.add_skin(doc43, "hero")
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
    skins.move_to_skins(doc43, "arm_left", "arm_left", ["caped"])
    meshes.add_linked_mesh(doc43, "arm_left", "alt", "arm_left", skin="caped", source_skin="caped")
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
    server.edit_skin(path, [{"op": "move_to_skins", "slot": "arm_left", "attachment": "arm_left", "skins": ["base"]},
                            {"op": "add_skin", "name": "armor"}])
    result = server.add_mesh(path, "arm_left", "arm_left", source="arm_left", source_skin="base", skin="armor",
                             image_path=str(art / "armor_arm.png"))
    assert result["image"].endswith("images/armor/arm_left.png")
    assert (images / "arm_left.png").read_bytes() == original  # the default art is untouched
    doc = d.load(path)
    assert _att(doc, "arm_left", "arm_left", "armor")["path"] == "armor/arm_left"
    assert "armor/arm_left" in imported43.atlas_path.read_text() and check(doc) == []
    with pytest.raises(EditError, match="is the image of base/arm_left/arm_left"):
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


# ── review findings ─────────────────────────────────────────────────────────

def test_copying_a_skin_keeps_what_linked_meshes_link_to(doc43):
    meshes.add_mesh(doc43, "arm_left", "arm_left")
    skins.add_skin(doc43, "red")
    meshes.add_linked_mesh(doc43, "arm_left", "red_arm", "arm_left", skin="red")  # links to the default mesh
    skins.add_skin(doc43, "blue", copy_from="red")
    assert "skin" not in _att(doc43, "arm_left", "red_arm", "blue") and check(doc43) == []


def test_art_that_differs_per_skin_lives_only_in_skins(doc, doc43):
    for version_doc in (doc, doc43):  # the 4.2.43 and 4.3.26 editors both refuse a name in both
        clash = copy.deepcopy(version_doc)
        skins.add_skin(clash, "armor")
        clash["skins"][-1]["attachments"] = {"head": {"head": dict(_att(clash, "head", "head"))}}
        errors = [str(f) for f in check(clash) if f.level == "error"]
        assert len(errors) == 1 and "Spine editor refuses to import it" in errors[0] and "move_to_skins" in errors[0]
    meshes.add_mesh(doc43, "arm_left", "arm_left")
    add_animation(doc43, "flex", timelines={"attachments": {"default": {"arm_left": {"arm_left": {
        "deform": [{"vertices": [1, 1]}]}}}}})
    skins.add_skin(doc43, "red")
    meshes.add_linked_mesh(doc43, "arm_left", "red_arm", "arm_left", skin="red")  # links to the default mesh
    done = skins.move_to_skins(doc43, "arm_left", "arm_left", ["base", "armor"])
    assert done["made"] == ["base", "armor"] and done["relinked"] == ["red/arm_left/red_arm"]
    assert done["moved_keys_in"] == ["flex"] and "setSkinByName" in done["note"]
    assert "arm_left" not in d.find_skin(doc43, "default")["attachments"]
    assert _att(doc43, "arm_left", "arm_left", "base") == _att(doc43, "arm_left", "arm_left", "armor")
    assert _att(doc43, "arm_left", "red_arm", "red")["skin"] == "base"
    assert set(doc43["animations"]["flex"]["attachments"]) == {"base", "armor"} and check(doc43) == []
    assert d.starting_skin(doc43) == "base" and d.starting_skin(doc) is None
    with pytest.raises(EditError, match="the default skin has no 'arm_left' in slot 'arm_left'"):
        skins.move_to_skins(doc43, "arm_left", "arm_left", ["other"])
    d.find_skin(doc43, "default")["attachments"]["arm_left"] = {"red_arm": {"path": "arm_left", "width": 9, "height": 9}}
    with pytest.raises(EditError, match="skin 'red' already has its own 'red_arm'"):
        skins.move_to_skins(doc43, "arm_left", "red_arm", ["red"])


def test_spine_import_explains_the_editors_skin_rule(imported43):
    path = str(imported43.project_path)
    doc = d.load(path)
    doc["skins"].append({"name": "armor", "attachments": {"head": {"head": dict(_att(doc, "head", "head"))}}})
    d.save(doc, path)
    with pytest.raises(spine_cli.SpineCliError, match="armor/head/head.*move_to_skins"):
        server.spine_import(path)  # before any editor runs


def test_previews_start_in_the_skin_that_holds_the_art(imported43):
    path = str(imported43.project_path)
    server.edit_skin(path, [{"op": "move_to_skins", "slot": "head", "attachment": "head", "skins": ["base"]}])
    player = Path(server.preview_html(path)["preview_path"]).read_text()
    phaser = Path(server.preview_html(path, runtime="phaser")["preview_path"]).read_text()
    assert '"skin": "base"' in player and '"skin": "base"' in phaser


@pytest.mark.parametrize("fields", [{"scaleX": 2}, {"scaleY": -1}, {"rotation": 30, "scaleX": 0.5}])
def test_world_point_rotation_uses_the_whole_bone_matrix(doc43, fields):
    rig.set_bone(doc43, "head", **fields)
    shapes.add_point(doc43, "head", "aim", x=0, y=0, rotation=45, world=True)
    r = math.radians(_att(doc43, "head", "aim").get("rotation", 0))
    t = d.world_transforms(doc43)["head"]
    # PointAttachment.computeWorldRotation
    shown = math.degrees(math.atan2(math.cos(r) * t.c + math.sin(r) * t.d, math.cos(r) * t.a + math.sin(r) * t.b))
    assert shown == pytest.approx(45, abs=1e-3)


def test_a_mesh_cannot_link_to_itself(doc43):
    meshes.add_mesh(doc43, "arm_left", "arm_left")
    with pytest.raises(EditError, match="cannot link to itself"):
        meshes.add_linked_mesh(doc43, "arm_left", "arm_left", "arm_left", replace=True)


def test_reweighting_respects_linked_meshes_deform_keys(doc43):
    meshes.add_mesh(doc43, "arm_left", "arm_left", grid_size=(1, 1))
    meshes.add_linked_mesh(doc43, "arm_left", "arm2", "arm_left", timelines=False)
    add_animation(doc43, "a", timelines={"attachments": {"default": {"arm_left": {"arm2": {
        "deform": [{"vertices": [5, 5, 5, 5, 5, 5, 5, 5]}]}}}}})
    with pytest.raises(EditError, match="deform keys in a \\(default/arm_left/arm2\\)"):
        meshes.set_weights(doc43, "arm_left", "arm_left", bones=["arm_left", "hand_left"])


def _two_bone_arm(doc):
    """arm_left as a weighted mesh whose vertices follow arm_left and hand_left."""
    meshes.add_mesh(doc, "arm_left", "arm_left", grid_size=(1, 4))
    meshes.set_weights(doc, "arm_left", "arm_left", bones=["arm_left", "hand_left"])
    mesh = _att(doc, "arm_left", "arm_left")
    records, i = [], 0
    while i < len(mesh["vertices"]):
        n = int(mesh["vertices"][i])
        records.append(n)
        i += 1 + 4 * n
    return mesh, records


def _deformed(doc, slot, att, deltas):
    """World points with a deform key's deltas added, one pair per influence."""
    moved, vertices, i, k = dict(att), list(att["vertices"]), 0, 0
    while i < len(vertices):
        n = int(vertices[i])
        for j in range(n):
            vertices[i + 2 + 4 * j] += deltas[2 * k]
            vertices[i + 3 + 4 * j] += deltas[2 * k + 1]
            k += 1
        i += 1 + 4 * n
    moved["vertices"] = vertices
    return meshes.world_points(doc, slot, moved)


def test_mix_and_match_rigs_and_events_at_one_time_are_clean(doc43):
    """A rig whose parts all live in named skins, and two events firing at one moment (logic and
    sound), are both valid; the same event twice at one time is not."""
    skin = d.find_skin(doc43, "default")
    skin["name"] = "dealer/one"
    doc43["events"] = {"landed": {}, "sfx/land": {}}
    doc43["animations"] = {"deal": {"events": [{"time": 1.3, "name": "landed"}, {"time": 1.3, "name": "sfx/land"}]}}
    assert check(doc43) == []
    doc43["animations"]["deal"]["events"].append({"time": 1.3, "name": "landed"})
    twice, = check(doc43)
    assert twice.level == "warning" and "two keys at time 1.3 for 'landed'" in twice.message


def test_a_region_turned_into_a_mesh_keeps_its_image(doc43):
    head = _att(doc43, "head", "head")
    head["name"] = "dealer/navy/head"  # the PSD importer names the image this way
    meshes.add_mesh(doc43, "head", "head", grid_size=(2, 2))
    mesh = _att(doc43, "head", "head")
    assert mesh["type"] == "mesh" and mesh["name"] == "dealer/navy/head" and "path" not in mesh
    region = _att(doc43, "arm_left", "arm_left")
    region["path"] = "arms/left"
    meshes.add_mesh(doc43, "arm_left", "arm_left", grid_size=(1, 2))
    assert _att(doc43, "arm_left", "arm_left")["path"] == "arms/left"


def test_a_weighted_deform_key_holds_a_delta_per_influence(doc43):
    mesh, counts = _two_bone_arm(doc43)
    influences = sum(counts)
    assert influences > len(mesh["uvs"]) // 2  # some vertices follow both bones
    add_animation(doc43, "bend", timelines={"attachments": {"default": {"arm_left": {"arm_left": {
        "deform": [{"time": 0}, {"time": 0.5, "vertices": [1.0] * (2 * influences)}]}}}}})
    assert [f for f in check(doc43) if "deform" in f.path] == []
    keys = doc43["animations"]["bend"]["attachments"]["default"]["arm_left"]["arm_left"]["deform"]
    keys[1]["vertices"].append(1.0)
    assert any("exceeds" in f.message for f in check(doc43) if "deform" in f.path)


def test_mixed_bone_order_is_reported_and_reordering_keeps_every_vertex(doc43):
    mesh, counts = _two_bone_arm(doc43)
    meshes.add_linked_mesh(doc43, "arm_left", "arm2", "arm_left", timelines=False)
    influences = sum(counts)
    deltas = [float(k % 7) for k in range(2 * influences)]
    add_animation(doc43, "bend", timelines={"attachments": {"default": {"arm_left": {
        "arm_left": {"deform": [{"time": 0, "offset": 2, "vertices": deltas[2:]}]},
        "arm2": {"deform": [{"time": 0, "vertices": deltas}]}}}}})
    assert check(doc43) == []
    first = next(v for v, n in enumerate(counts) if n == 2)  # swap one vertex's two bones
    i = sum(1 + 4 * n for n in counts[:first])
    v = mesh["vertices"]
    v[i + 1:i + 9] = v[i + 5:i + 9] + v[i + 1:i + 5]
    k = 2 * sum(counts[:first])
    swapped = deltas[:k] + deltas[k + 2:k + 4] + deltas[k:k + 2] + deltas[k + 4:]
    for name, d_ in (("arm_left", swapped[2:]), ("arm2", swapped)):
        doc43["animations"]["bend"]["attachments"]["default"]["arm_left"][name]["deform"][0]["vertices"] = d_
    before_setup = meshes.world_points(doc43, "arm_left", mesh)
    before_bent = _deformed(doc43, "arm_left", mesh, swapped)
    found = [f for f in check(doc43) if "both orders" in f.message]
    assert found and found[0].level == "error" and "spine_import" in found[0].message

    assert meshes.order_influences(doc43) == ["default/arm_left/arm_left"]
    assert check(doc43) == []
    assert _close(meshes.world_points(doc43, "arm_left", mesh), before_setup)
    keys = doc43["animations"]["bend"]["attachments"]["default"]["arm_left"]

    def full(key):
        out = [0.0] * (2 * influences)
        out[key.get("offset", 0):key.get("offset", 0) + len(key["vertices"])] = key["vertices"]
        return out

    assert full(keys["arm2"]["deform"][0]) == deltas  # the linked mesh's key moved too
    own = [0.0, 0.0] + swapped[2:]  # its key as written, in the swapped order
    assert full(keys["arm_left"]["deform"][0]) == own[:k] + own[k + 2:k + 4] + own[k:k + 2] + own[k + 4:]
    assert _close(_deformed(doc43, "arm_left", mesh, deltas), before_bent)
    assert meshes.order_influences(doc43) == []


@needs_spine
def test_spine_import_replaces_a_project_only_when_asked(imported43):
    path = str(imported43.project_path)
    first = server.spine_import(path)
    assert first["ok"], first
    with pytest.raises(spine_cli.SpineCliError, match="already holds skeleton .* replace=true"):
        server.spine_import(path)
    again = server.spine_import(path, replace=True)
    assert again["ok"] and again["replaced"], again
    held = spine_cli.project_info(first["spine_project"]).get("skeletons")
    assert held == [imported43.project_path.stem]  # replaced, not a second skeleton beside it
    assert not list(imported43.project_path.parent.glob(".*.replacing.spine"))


@needs_spine
def test_spine_import_gives_the_editor_bone_order(imported43):
    path = str(imported43.project_path)
    doc = d.load(path)
    mesh, counts = _two_bone_arm(doc)
    first = next(v for v, n in enumerate(counts) if n == 2)
    i = sum(1 + 4 * n for n in counts[:first])
    v = mesh["vertices"]
    v[i + 1:i + 9] = v[i + 5:i + 9] + v[i + 1:i + 5]
    d.save(doc, path)
    result = server.spine_import(path)
    assert result["ok"], result
    assert any("bone order" in w for w in result.get("warnings", []))
    assert d.load(path)["skins"] == doc["skins"]  # the JSON itself is unchanged


def test_weights_need_normal_inherit(doc43):
    meshes.add_mesh(doc43, "arm_left", "arm_left", grid_size=(1, 1))
    rig.set_bone(doc43, "arm_left", inherit="noScale")
    with pytest.raises(EditError, match="only 'normal' chains"):
        meshes.set_weights(doc43, "arm_left", "arm_left", bones=["hand_left"])
    with pytest.raises(EditError, match="only 'normal' chains"):
        meshes.add_mesh(doc43, "arm_left", "arm_left2", size=(10, 10), bones=["hand_left"])
    with pytest.raises(EditError, match="only 'normal' chains"):  # explicit weights on a noScale bone
        meshes.add_mesh(doc43, "arm_right", "m", size=(10, 10), grid_size=(1, 1), weights=[{"arm_left": 1}] * 4)


def test_replacing_a_skin_constraint_keeps_it_skin_only(doc, doc43):
    for version_doc in (doc, doc43):
        rig.set_bone(version_doc, "head", length=60)
        con.add_constraint(version_doc, "physics", "sway", bone="head", preset="hair")
        skins.add_skin(version_doc, "hero")
        skins.add_to_skin(version_doc, "hero", constraints=["sway"])
        con.add_constraint(version_doc, "physics", "sway", bone="head", preset="tail", replace=True)
        (c,) = d.constraints(version_doc, "physics")
        assert c["skin"] is True and d.find_skin(version_doc, "hero")["physics"] == ["sway"]


def test_folder_and_slot_names_stay_apart(doc43):
    rig.edit_rig(doc43, [{"op": "rename_slot", "name": n, "to": f"arms/{n}"} for n in ("arm_left", "arm_right")]
                 + [{"op": "rename_slot", "name": n, "to": f"hands/{n}"} for n in ("hand_left", "hand_right")])
    with pytest.raises(EditError, match="'hands' is already a folder"):
        rig.rename_folder(doc43, "arms", "hands")
    with pytest.raises(EditError, match="a draw order folder's name"):
        rig.rename_slot(doc43, "head", "arms")
    with pytest.raises(EditError, match="slot 'torso' has the name the folder"):
        rig.rename_slot(doc43, "head", "torso/head")
    add_animation(doc43, "a", timelines={"drawOrderFolder": {"arms": [{"time": 0.2, "order": ["arms/arm_right",
                                                                                              "arms/arm_left"]}]}})
    done = rig.set_draw_order(doc43, slot="arms/arm_right", index=2)  # reorders the folder's setup order
    assert "before its first key it now shows the new setup order" in done["notes"][0]


@needs_node
def test_a_setup_attachment_only_some_skins_hold_is_valid(imported43):
    path = str(imported43.project_path)
    server.edit_rig(path, [{"op": "add_slot", "name": "hat", "bone": "head"}])
    doc = d.load(path)
    doc["skins"].append({"name": "hero", "attachments": {"hat": {"hat": {"path": "head", "width": 80, "height": 80}}}})
    d.slot_map(doc)["hat"]["attachment"] = "hat"  # shown only with the hero skin
    d.save(doc, path)
    result = server.validate_skeleton(path, runtime=True)
    assert result["ok"], result
    checks = {s["name"]: s for s in result["runtime"]["skin_checks"]}
    assert checks["default"]["not_shown"] == ["hat: hat"] and checks["hero"]["not_shown"] == []


@pytest.mark.parametrize("version", ["4.2", "4.3"])
def test_phaser_preview_pins_the_game_runtime(version, imported, imported43, tmp_path):
    made = imported if version == "4.2" else imported43
    result = server.preview_html(str(made.project_path), runtime="phaser")
    page = Path(result["preview_path"]).read_text()
    phaser, runtime = ("4.1.0", "4.2.120") if version == "4.2" else ("4.2.1", "4.3.13")
    assert f"phaser@{phaser}/dist" in page and f"spine-phaser-v4@{runtime}/dist" in page
    assert result["runtime"] == f"phaser@{phaser} + @esotericsoftware/spine-phaser-v4@{runtime}"
    assert '"skeleton": "test_character.json"' in page and '"atlas": "test_character.atlas"' in page
    assert "spineJson" in page and "spineSkeleton" in page  # 4.2 and 4.3 loader names
    elsewhere = server.preview_html(str(made.project_path), runtime="phaser", out_path=str(tmp_path / "web" / "p.html"))
    assert '"skeleton": "../' in Path(elsewhere["preview_path"]).read_text()  # paths relative to the page
    with pytest.raises(EditError, match="runtime must be player or phaser"):
        server.preview_html(str(made.project_path), runtime="unity")


def test_older_43_editors_get_the_changelogs_caveats(doc, doc43):
    from spine2d_mcp.editor_quirks import editor_caveats
    add_animation(doc43, "idle", preset="idle")
    con.add_constraint(doc43, "slider", "nod", bone="torso", animation="idle", property="rotate", to=1)
    old = editor_caveats("4.3.05", doc43)
    assert any("4.3.08 fix for importing bone-driven sliders" in c for c in old)
    assert any("4.3.14 fix for CLI data import" in c for c in old) and old[-1] == "use Spine 4.3.26 or newer"
    assert editor_caveats("4.3.10", doc43) == ["Spine 4.3.10 predates the 4.3.14 fix for CLI data import (it could "
                                               "crash)", "use Spine 4.3.26 or newer"]
    assert editor_caveats("4.3.26", doc43) == [] and editor_caveats("4.3.27", doc43) == []
    assert "4.3.05 fix for keeping a Bezier curve's handle" in editor_caveats("4.3.04", doc43)[0]  # idle eases
    add_animation(doc43, "snap", timelines={"bones": {"head": {"rotate": [{"value": 0}, {"time": 1, "value": 9}]}}})
    del doc43["animations"]["idle"]
    assert not any("4.3.05" in c for c in editor_caveats("4.3.04", doc43))  # linear keys only
    assert "beta" in editor_caveats("4.3.39-beta", doc43)[0]
    assert editor_caveats("4.2.43", doc) == []


@needs_spine
def test_import_matches_the_header_to_the_editor(imported43):
    path = imported43.project_path
    doc = d.load(path)
    doc["skeleton"]["spine"] = "4.3.20"  # written for another 4.3 release
    d.save(doc, path)
    made = server.spine_import(str(path))
    assert made["ok"] and not any("version" in w.lower() for w in made.get("warnings", [])), made
    assert d.load(path)["skeleton"]["spine"] == "4.3.20"  # the file itself is untouched
    assert not list(path.parent.glob(".*.import-*.json"))  # the copy is gone


def test_physics_on_a_bone_without_length_is_flagged(doc, doc43):
    doc43["constraints"] = [{"type": "physics", "name": "hair", "bone": "head", "rotate": 1, "x": 0.5}]
    doc["physics"] = [{"name": "hair", "order": 0, "bone": "head", "shearX": 1}]
    for version_doc, used in ((doc43, "rotate"), (doc, "shearX")):  # both editors drop them
        assert any(f"{used} on bone 'head', which has no length" in w for w in _warnings(version_doc))
        assert spine_cli.import_changes(version_doc)[0] == f"physics 'hair': {used} dropped (bone 'head' has no length)"


@needs_spine
def test_sliders_keep_their_animations_through_the_editor(imported43, tmp_path):
    path = str(imported43.project_path)
    for name in ("wave", "idle"):
        server.add_animation(path, name, preset=name)
    server.add_animation(path, "bend", timelines={"bones": {"hand_left": {"rotate": [{"value": 0}, {"time": 1, "value": 60}]}}})
    server.add_constraint(path, "slider", "breath", params={"animation": "idle", "time": 0.5})
    server.add_constraint(path, "slider", "nod", bone="torso",
                          params={"animation": "bend", "property": "rotate", "to": 1, "scale": 0.05})
    editor = spine_cli.editor_for("4.3")
    assert spine_cli.editor_animation_order(["wave", "idle", "bend", "b10", "b2"], editor) == \
        ["b2", "b10", "bend", "idle", "wave"]
    made = server.spine_import(path)
    out = server.spine_export(made["spine_project"], str(tmp_path / "back"), "json", format_version="4.3")
    back = json.loads(Path(next(f for f in out["files"] if f.endswith(".json"))).read_text())
    assert {c["name"]: c["animation"] for c in back["constraints"] if c["type"] == "slider"} == \
        {"breath": "idle", "nod": "bend"}  # by index into its sorted list, nod would play "wave"


@needs_spine
def test_skin_placeholders_go_through_the_editor(imported43, art, tmp_path):
    path = str(imported43.project_path)
    server.add_mesh(path, "arm_left", "arm_left", bones=["arm_left", "hand_left"], grid=[1, 4])
    server.edit_skin(path, [{"op": "move_to_skins", "slot": "arm_left", "attachment": "arm_left", "skins": ["base"]},
                            {"op": "add_skin", "name": "armor"}])
    server.add_mesh(path, "arm_left", "arm_left", source="arm_left", source_skin="base", skin="armor",
                    image_path=str(art / "armor_arm.png"))
    made = server.spine_import(path)
    assert made["ok"], made
    out = server.spine_export(made["spine_project"], str(tmp_path / "back"), "json", format_version="4.3")
    back = json.loads(Path(next(f for f in out["files"] if f.endswith(".json"))).read_text())
    held = {s["name"]: s.get("attachments", {}) for s in back["skins"]}
    assert "arm_left" not in held.get("default", {}) and held["base"]["arm_left"]["arm_left"]["type"] == "mesh"
    armor = held["armor"]["arm_left"]["arm_left"]
    assert (armor["type"], armor["skin"], armor["source"]) == ("linkedmesh", "base", "arm_left")
    assert _errors(back) == []


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
    per_skin = {s["name"]: s for s in result["runtime"]["skin_checks"]}
    assert per_skin["armor"]["max_bone_travel"] > per_skin["default"]["max_bone_travel"]  # the orb moves with armor
    assert not per_skin["armor"]["not_shown"]
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
