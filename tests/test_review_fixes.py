"""Regression tests for the independent review's findings."""

import math
import random

import pytest
from PIL import Image

from spine2d_mcp import server
from spine2d_mcp.edits import EditError
from spine2d_mcp.ops import attachments as att
from spine2d_mcp.ops import constraints as con
from spine2d_mcp.ops import rig
from spine2d_mcp.spine import document as d
from spine2d_mcp.spine.check import check


def _close(p, q, tol=1e-3):
    return all(math.isclose(a, b, abs_tol=tol) for a, b in zip(p, q))


def _errors(doc):
    return [f"{f.path}: {f.message}" for f in check(doc) if f.level == "error"]


def _doc(bones, slots=(), skins=()):
    return {"skeleton": {"spine": "4.2.43"}, "bones": list(bones), "slots": list(slots), "skins": list(skins)}


# 1. Mirrored and unevenly scaled bones -------------------------------------------------

def test_decompose_round_trips_any_matrix():
    rnd = random.Random(7)
    for _ in range(200):
        bone = {"rotation": rnd.uniform(-180, 180), "scaleX": rnd.choice([-1, 1]) * rnd.uniform(0.2, 3),
                "scaleY": rnd.choice([-1, 1]) * rnd.uniform(0.2, 3), "shearY": rnd.uniform(-40, 40)}
        m = d.local_matrix(bone)
        assert _close(d.local_matrix(d.decompose(m)), m, 1e-9)


@pytest.mark.parametrize("parent", [
    {"name": "flip", "parent": "root", "x": 50, "scaleX": -1},
    {"name": "flip", "parent": "root", "x": 50, "scaleX": 2, "scaleY": 1, "rotation": 15},
])
def test_reparent_keeps_the_whole_world_transform(parent):
    doc = _doc([{"name": "root"}, parent, {"name": "arm", "parent": "root", "x": 100, "rotation": 30, "scaleY": 1.5}])
    before = d.world_transforms(doc)["arm"]
    rig.set_bone(doc, "arm", parent="flip")
    after = d.world_transforms(doc)["arm"]
    assert _close((after.a, after.b, after.c, after.d, after.x, after.y),
                  (before.a, before.b, before.c, before.d, before.x, before.y))


def _region_world(doc, slot, name):
    s = d.slot_map(doc)[slot]
    a = d.find_skin(doc, "default")["attachments"][slot][name]
    t = d.world_transforms(doc)[s["bone"]]
    m = d.mat_mul(d.matrix(t), d.local_matrix(a))
    return (*m, *t.apply(a.get("x", 0), a.get("y", 0)))


def test_slot_rebind_onto_a_mirrored_bone_keeps_the_region():
    doc = _doc([{"name": "root"}, {"name": "a", "parent": "root", "rotation": 20},
                {"name": "b", "parent": "root", "x": 30, "scaleX": -1}],
               [{"name": "s", "bone": "a", "attachment": "r"}],
               [{"name": "default", "attachments": {"s": {"r": {"width": 10, "height": 10, "x": 5, "y": 2}}}}])
    before = _region_world(doc, "s", "r")
    rig.set_slot(doc, "s", bone="b")
    assert _close(_region_world(doc, "s", "r"), before)


def test_slot_rebind_refuses_a_shear_a_region_cannot_hold():
    doc = _doc([{"name": "root"}, {"name": "a", "parent": "root", "rotation": 30},
                {"name": "b", "parent": "root", "scaleX": 2}],
               [{"name": "s", "bone": "a", "attachment": "r"}],
               [{"name": "default", "attachments": {"s": {"r": {"width": 10, "height": 10}}}}])
    with pytest.raises(EditError, match="shear"):
        rig.set_slot(doc, "s", bone="b")


def test_world_placement_on_a_mirrored_bone():
    doc = _doc([{"name": "root"}, {"name": "b", "parent": "root", "x": 10, "scaleX": -1}],
               [{"name": "s", "bone": "b"}], [{"name": "default", "attachments": {}}])
    att.add_region(doc, "s", "r", (8, 8), x=20, y=5, rotation=30, world=True)
    world = _region_world(doc, "s", "r")
    assert _close(world, (*d.local_matrix({"rotation": 30}), 20, 5))


# 2. A path constraint's target is a slot ----------------------------------------------

def _rope_doc():
    return _doc(
        [{"name": "root"}, {"name": "rope", "parent": "root"}, {"name": "b2", "parent": "root"}],
        [{"name": "rope", "bone": "root", "attachment": "p"}],
        [{"name": "default", "attachments": {"rope": {"p": {
            "type": "path", "vertexCount": 3, "vertices": [0, 0, 1, 1, 2, 2], "lengths": [10]}}}}],
    ) | {"path": [{"name": "pc", "order": 0, "bones": ["b2"], "target": "rope"}]}


def test_rename_bone_leaves_path_targets_alone():
    doc = _rope_doc()
    rig.rename_bone(doc, "rope", "cord")
    assert doc["path"][0]["target"] == "rope" and _errors(doc) == []


def test_remove_bone_ignores_a_same_named_path_target():
    doc = _rope_doc()
    rig.remove_bone(doc, "rope")
    assert "rope" not in d.bone_map(doc) and _errors(doc) == []


def test_remove_slot_ignores_a_same_named_ik_target():
    doc = _doc([{"name": "root"}, {"name": "arm", "parent": "root"}, {"name": "hat", "parent": "root"}],
               [{"name": "hat", "bone": "root"}])
    doc["ik"] = [{"name": "k", "order": 0, "bones": ["arm"], "target": "hat"}]
    rig.remove_slot(doc, "hat")
    assert "hat" not in d.slot_map(doc)


# 3. Draw-order offsets ---------------------------------------------------------------

@pytest.mark.parametrize("offsets, text", [
    ([{"slot": "u", "offset": -1}, {"slot": "s", "offset": 1}], "setup order"),
    ([{"slot": "s", "offset": 1}, {"slot": "t", "offset": 0}], "two slots moved to draw position 1"),
])
def test_draw_order_offsets_the_reader_cannot_follow(offsets, text):
    doc = _doc([{"name": "root"}], [{"name": n, "bone": "root"} for n in "stu"])
    doc["animations"] = {"a": {"drawOrder": [{"time": 0, "offsets": offsets}]}}
    assert any(text in e for e in _errors(doc)), _errors(doc)
    doc["animations"]["a"]["drawOrder"][0]["offsets"] = [{"slot": "s", "offset": 2}, {"slot": "u", "offset": -2}]
    assert _errors(doc) == []


# 4. Duration counts ik and transform timelines -----------------------------------------

def test_duration_includes_constraint_timelines():
    anim = {"ik": {"c": [{"time": 0}, {"time": 3}]}, "transform": {"t": [{"time": 0}, {"time": 2}]},
            "bones": {"a": {"rotate": [{"time": 1, "value": 1}]}}}
    assert d.animation_duration(anim) == 3.0


# 5. Malformed input is reported, not raised -------------------------------------------

@pytest.mark.parametrize("mutate", [
    lambda doc: doc.update(animations={"a": {"bones": {"head": {"rotate": 5}}}}),
    lambda doc: doc.update(animations={"a": {"bones": {"head": {"rotate": [1]}}}}),
    lambda doc: doc.update(animations={"a": []}),
    lambda doc: doc.update(animations={"a": {"bones": []}}),
    lambda doc: doc.update(animations={"a": {"slots": {"head": 5}}}),
    lambda doc: doc.update(animations={"a": {"attachments": {"default": {"head": {"head": 5}}}}}),
    lambda doc: doc.update(animations={"a": {"ik": {"x": 3}}}),
    lambda doc: doc["bones"].append(5),
    lambda doc: doc["slots"].append("x"),
    lambda doc: doc["skins"].append(None),
    lambda doc: doc["skins"][0]["attachments"]["head"].update(head=None),
    lambda doc: doc["skins"][0].update(attachments=[]),
    lambda doc: doc.update(ik=[3]),
    lambda doc: doc.update(events={"e": 1}),
])
def test_malformed_input_is_a_finding(imported, mutate):
    doc = d.load(imported.project_path)
    mutate(doc)
    assert _errors(doc), "expected an error finding"
    d.save(doc, imported.project_path)
    assert not server.validate_skeleton(str(imported.project_path))["ok"]
    try:
        server.inspect_skeleton(str(imported.project_path))
    except ValueError as e:  # a clear refusal, never a raw TypeError/AttributeError
        assert "malformed" in str(e)


# 6. Checker accuracy against the reader ------------------------------------------------

def test_reader_accepted_values_pass_and_path_lengths_are_required():
    doc = _doc([{"name": "root"}], [{"name": "s", "bone": "root", "color": "ff0000", "dark": "00ff00"}])
    doc["physics"] = [{"name": "p", "order": 0, "bone": "root", "rotate": 1, "fps": 60.0}]
    assert _errors(doc) == []
    doc["skins"] = [{"name": "default", "attachments": {"s": {"p": {"type": "path", "vertexCount": 3,
                                                                    "vertices": [0, 0, 1, 1, 2, 2]}}}}]
    assert any("lengths" in e for e in _errors(doc))


# 7. Smaller defects ---------------------------------------------------------------------

def test_add_attachment_checks_the_repack_before_writing(imported, tmp_path):
    png = tmp_path / "mouth.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(png)
    (imported.images_dir / "head.png").unlink()
    before = imported.project_path.read_text()
    with pytest.raises(EditError, match="cannot repack"):
        server.add_attachment(str(imported.project_path), "torso", str(png))
    assert imported.project_path.read_text() == before


def test_add_attachment_will_not_overwrite_a_different_image(imported, tmp_path):
    png = tmp_path / "torso.png"
    Image.new("RGBA", (8, 8), (0, 0, 255, 255)).save(png)
    with pytest.raises(EditError, match="already exists"):
        server.add_attachment(str(imported.project_path), "head", str(png))


def test_replacing_a_constraint_keeps_its_skins(imported):
    doc = d.load(imported.project_path)
    con.add_constraint(doc, "physics", "sway", bone="head", preset="hair")
    doc["skins"][0]["physics"] = ["sway"]
    con.add_constraint(doc, "physics", "sway", bone="head", preset="tail", replace=True)
    assert doc["skins"][0]["physics"] == ["sway"] and _errors(doc) == []


def test_remove_slot_names_what_still_uses_it(imported):
    doc = d.load(imported.project_path)
    doc["animations"] = {"a": {"drawOrder": [{"time": 0, "offsets": [{"slot": "head", "offset": 1}]}]}}
    with pytest.raises(EditError, match="drawOrder keys of a"):
        rig.remove_slot(doc, "head")
