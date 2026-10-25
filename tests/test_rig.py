import math

import pytest
from PIL import Image

from spine2d_mcp import server
from spine2d_mcp.edits import EditError
from spine2d_mcp.ops import constraints as con
from spine2d_mcp.ops import rig
from spine2d_mcp.ops.animation import add_animation
from spine2d_mcp.spine import document as d
from spine2d_mcp.spine.check import check


@pytest.fixture
def doc(imported):
    return d.load(imported.project_path)


def _close(a, b, tol=1e-3):
    return all(math.isclose(x, y, abs_tol=tol) for x, y in zip(a, b))


def _region_world(doc, slot):
    s = d.slot_map(doc)[slot]
    att = next(iter(d.find_skin(doc, "default")["attachments"][slot].values()))
    return d.world_transforms(doc)[s["bone"]].apply(att.get("x", 0), att.get("y", 0))


def _bone_world(doc, name):
    w = d.world_transforms(doc)[name]
    return w.x, w.y


def _weighted_world(doc, att):
    world = d.world_transforms(doc)
    names = [b["name"] for b in d.bones(doc)]
    out, v, i = [], att["vertices"], 0
    while i < len(v):
        n, x, y = int(v[i]), 0.0, 0.0
        for j in range(n):
            b, vx, vy, w = v[i + 1 + j * 4: i + 5 + j * 4]
            px, py = world[names[int(b)]].apply(vx, vy)
            x, y = x + px * w, y + py * w
        out.append((x, y))
        i += 1 + n * 4
    return out


def test_move_pivot_keeps_art_and_children_in_place(doc):
    before = {s: _region_world(doc, s) for s in ("arm_left", "hand_left")}
    hand = _bone_world(doc, "hand_left")
    arm = _bone_world(doc, "arm_left")
    detail = rig.move_pivot(doc, "arm_left", x=3, y=-12)
    assert _close(_bone_world(doc, "arm_left"), (arm[0] + 3, arm[1] - 12))
    assert _close(_bone_world(doc, "hand_left"), hand)
    for slot, pos in before.items():
        assert _close(_region_world(doc, slot), pos)
    assert detail["world_delta"] == [3, -12]
    assert check(doc) == []


def test_move_pivot_on_rotated_bones_and_weighted_meshes():
    doc = {
        "skeleton": {"spine": "4.2.43"},
        "bones": [{"name": "root"}, {"name": "a", "parent": "root", "rotation": 90, "scaleX": 2, "x": 5},
                  {"name": "b", "parent": "a", "x": 10}],
        "slots": [{"name": "s", "bone": "a", "attachment": "m"}],
        "skins": [{"name": "default", "attachments": {"s": {"m": {
            "type": "mesh", "uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2], "hull": 3,
            # [count, (bone, x, y, weight) * count] per vertex; bones: root 0, a 1, b 2.
            "vertices": [1, 1, 0, 0, 1, 2, 1, 3, 4, 0.5, 2, 6, 0, 0.5, 1, 2, 0, 5, 1],
        }}}}],
    }
    assert check(doc) == []
    mesh = doc["skins"][0]["attachments"]["s"]["m"]
    before_mesh = _weighted_world(doc, mesh)
    child = _bone_world(doc, "b")
    rig.move_pivot(doc, "a", to=[0, 0])
    assert _close(_bone_world(doc, "a"), (0, 0))
    assert _close(_bone_world(doc, "b"), child)
    for p, q in zip(_weighted_world(doc, mesh), before_mesh):
        assert _close(p, q)


def test_add_bone_world_position_and_order(doc):
    rig.add_bone(doc, "prop", "hand_left", world=True, x=-80, y=250)
    names = [b["name"] for b in d.bones(doc)]
    assert names.index("prop") == names.index("hand_left") + 1
    assert _close(_bone_world(doc, "prop"), (-80, 250))
    assert check(doc) == []


def test_reparent_keeps_world_transform_and_remaps_weights():
    doc = {
        "skeleton": {"spine": "4.2.43"},
        "bones": [{"name": "root"}, {"name": "a", "parent": "root", "x": 10, "rotation": 30},
                  {"name": "b", "parent": "root", "x": -5, "y": 7}, {"name": "c", "parent": "a", "x": 4}],
        "slots": [{"name": "s", "bone": "a", "attachment": "m"}],
        "skins": [{"name": "default", "attachments": {"s": {"m": {
            "type": "mesh", "uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2],
            "vertices": [1, 1, 0, 0, 1, 1, 3, 1, 0, 1, 1, 2, 0, 1, 1],
        }}}}],
    }
    mesh = doc["skins"][0]["attachments"]["s"]["m"]
    before_mesh = _weighted_world(doc, mesh)
    world_c, angle_c = _bone_world(doc, "c"), 30
    rig.set_bone(doc, "c", parent="b")
    names = [b["name"] for b in d.bones(doc)]
    assert names.index("c") > names.index("b")
    assert _close(_bone_world(doc, "c"), world_c)
    t = d.world_transforms(doc)["c"]
    assert math.isclose(math.degrees(math.atan2(t.c, t.a)), angle_c, abs_tol=1e-6)
    for p, q in zip(_weighted_world(doc, mesh), before_mesh):
        assert _close(p, q)
    with pytest.raises(EditError, match="own descendant"):
        rig.set_bone(doc, "b", parent="c")


def test_rename_bone_updates_every_reference(doc):
    add_animation(doc, "idle", preset="idle")
    rig.set_bone(doc, "head", length=60)
    con.add_constraint(doc, "physics", "sway", bone="head", preset="hair")
    rig.rename_bone(doc, "head", "Head")
    assert d.slot_map(doc)["head"]["bone"] == "Head"
    assert doc["physics"][0]["bone"] == "Head"
    assert "Head" in doc["animations"]["idle"]["bones"]
    assert check(doc) == []


def test_remove_bone_only_when_unused(doc):
    with pytest.raises(EditError, match="used by"):
        rig.remove_bone(doc, "hand_left")
    rig.add_bone(doc, "spare", "root")
    add_animation(doc, "a", timelines={"bones": {"spare": {"rotate": [{"value": 0}, {"time": 1, "value": 5}]}}})
    rig.remove_bone(doc, "spare")
    assert "spare" not in d.bone_map(doc) and "spare" not in doc["animations"]["a"]["bones"]


def test_slots_rebind_reorder_and_remove(doc):
    pos = _region_world(doc, "hand_left")
    rig.set_slot(doc, "hand_left", bone="arm_left")
    assert d.slot_map(doc)["hand_left"]["bone"] == "arm_left"
    assert _close(_region_world(doc, "hand_left"), pos)
    rig.add_slot(doc, "hat", "head")
    assert [s["name"] for s in d.slots(doc)][-1] == "hat"
    rig.set_draw_order(doc, slot="hat", index=0)
    assert [s["name"] for s in d.slots(doc)][0] == "hat"
    with pytest.raises(EditError, match="every slot once"):
        rig.set_draw_order(doc, order=["hat"])
    rig.remove_slot(doc, "hat")
    assert "hat" not in d.slot_map(doc)
    assert check(doc) == []


def test_edit_rig_reports_which_operation_failed(doc):
    with pytest.raises(EditError, match="operation 1 \\(move_pivot\\): no bone 'ghost'"):
        rig.edit_rig(doc, [{"op": "add_bone", "name": "t", "parent": "root"}, {"op": "move_pivot", "name": "ghost"}])
    with pytest.raises(EditError, match="unknown op"):
        rig.edit_rig(doc, [{"op": "explode"}])


def test_ik_creates_target_and_warns_on_unrotated_rigs(doc):
    detail = con.add_constraint(doc, "ik", "arm_ik", bones=["arm_left"], target="arm_target", target_at="hand_left")
    assert detail["created_target"]["bone"] == "arm_target"
    assert "swing by" in detail["warning"]
    assert check(doc) == []


def test_ik_on_bones_along_their_limbs_does_not_warn():
    doc = {"skeleton": {"spine": "4.2.43"},
           "bones": [{"name": "root"}, {"name": "upper", "parent": "root", "rotation": -90, "length": 50},
                     {"name": "lower", "parent": "upper", "x": 50, "length": 50}]}
    detail = con.add_constraint(doc, "ik", "leg", bones=["upper", "lower"], target="foot", target_at=[0, -95])
    assert "warning" not in detail
    assert check(doc) == []


def test_physics_transform_and_remove(doc):
    with pytest.raises(EditError, match="bone 'head' has no length, which physics rotate need"):
        con.add_constraint(doc, "physics", "hair", bone="head", preset="hair")
    con.add_constraint(doc, "physics", "drift", bone="head", x=0.5)  # moving the bone itself needs no length
    con.remove_constraint(doc, "drift")
    rig.set_bone(doc, "head", length=60)
    con.add_constraint(doc, "physics", "hair", bone="head", preset="hair", damping=0.9)
    assert doc["physics"][0]["rotate"] == 1 and doc["physics"][0]["damping"] == 0.9
    with pytest.raises(EditError, match="moves nothing"):
        con.add_constraint(doc, "physics", "dead", bone="head")
    con.add_constraint(doc, "transform", "follow", bones=["hand_right"], target="hand_left", mixRotate=0.5)
    assert [c["order"] for _, c in d.all_constraints(doc)] == [1, 0]
    add_animation(doc, "a", timelines={"transform": {"follow": [{"mixRotate": 0, "ease": "inout"}, {"time": 1, "mixRotate": 1}]}})
    assert len(doc["animations"]["a"]["transform"]["follow"][0]["curve"]) == 24
    assert check(doc) == []
    con.remove_constraint(doc, "follow")
    assert "transform" not in doc and "follow" not in doc["animations"]["a"].get("transform", {})


def test_add_attachment_tool_copies_image_and_repacks(imported, tmp_path):
    png = tmp_path / "mouth_open.png"
    Image.new("RGBA", (20, 12), (200, 40, 40, 255)).save(png)
    path = str(imported.project_path)
    result = server.add_attachment(path, slot="head", image_path=str(png), x=0, y=10)
    assert result["errors"] == []
    assert (imported.images_dir / "mouth_open.png").exists()
    assert "mouth_open" in imported.atlas_path.read_text()
    doc = d.load(path)
    assert doc["skins"][0]["attachments"]["head"]["mouth_open"]["width"] == 20.0
    swap = server.add_animation(path, "talk", timelines={"slots": {"head": {"attachment": [
        {"time": 0, "name": "mouth_open"}, {"time": 0.2, "name": "head"}]}}})
    assert swap["errors"] == []


def test_pack_atlas_refuses_when_an_image_is_missing(imported):
    (imported.images_dir / "head.png").unlink()
    with pytest.raises(EditError, match="no image .* for head"):
        server.pack_atlas(str(imported.project_path))
