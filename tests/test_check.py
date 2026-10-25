import copy
import math

import pytest

from spine2d_mcp.spine import document as d
from spine2d_mcp.spine.check import check, summary


@pytest.fixture
def doc(imported):
    return d.load(imported.project_path)


def _findings(doc, level=None, loops=None):
    return [f for f in check(doc, loops) if level is None or f.level == level]


def _has(doc, text, level="error", loops=None):
    return any(text in f"{f.path}: {f.message}" for f in _findings(doc, level, loops))


def test_imported_skeleton_is_clean(doc):
    assert _findings(doc) == []
    assert summary(doc).startswith("spine 4.2.") and "| 14 bones, 10 slots" in summary(doc)


@pytest.mark.parametrize(
    "mutate, text",
    [
        (lambda doc: doc["skeleton"].update(spine="4.1.24"), "unsupported format version 4.1.24"),
        (lambda doc: doc.update(constraints=[]), "4.3 constraints array"),
        (lambda doc: doc["bones"].insert(1, doc["bones"].pop(2)), "not a bone declared before it"),
        (lambda doc: doc["bones"][1].update(transform="noScale"), "4.1 key"),
        (lambda doc: doc["bones"][1].update(inherit="sideways"), "unknown inherit"),
        (lambda doc: doc["skins"][0].pop("name"), "requires it, even for 'default'"),
        (lambda doc: doc["slots"][0].update(attachment="ghost"), "renders nothing"),
        (lambda doc: doc["slots"][0].update(bone="ghost"), "bone 'ghost' not found"),
        (lambda doc: doc["slots"][0].update(color="fff"), "8 hex digits"),
        (lambda doc: doc["skins"][0]["attachments"]["head"]["head"].pop("width"), "numeric width"),
    ],
)
def test_structure_errors(doc, mutate, text):
    mutate(doc)
    assert _has(doc, text), [str(f) for f in check(doc)]


def _with_animation(doc, anim, events=None):
    doc = copy.deepcopy(doc)
    doc["animations"] = {"a": anim}
    if events is not None:
        doc["events"] = events
    return doc


@pytest.mark.parametrize(
    "anim, text",
    [
        ({"bones": {"head": {"rotate": [{"angle": 10}]}}}, "'angle' is the 3.x key"),
        ({"bones": {"ghost": {"rotate": [{"value": 1}]}}}, "bone not found"),
        ({"bones": {"head": {"spin": [{"value": 1}]}}}, "unknown bone timeline"),
        ({"bones": {"head": {"rotate": [{"time": 1, "value": 0}, {"time": 0.5, "value": 1}]}}}, "goes back"),
        (
            {"bones": {"head": {"translate": [{"x": 0, "curve": [0, 0, 1, 1]}, {"time": 1, "x": 5}]}}},
            "needs 8 numbers",
        ),
        ({"bones": {"head": {"rotate": [{"value": 0, "curve": "ease"}, {"time": 1}]}}}, "unknown curve"),
        ({"slots": {"head": {"attachment": [{"name": "ghost"}]}}}, "in no skin for this slot"),
        ({"slots": {"head": {"rgba": [{"color": "ffffff"}]}}}, "8 hex digits"),
        ({"slots": {"head": {"glow": [{}]}}}, "unknown slot timeline"),
        ({"events": [{"name": "step"}]}, "not defined in the root 'events'"),
        ({"drawOrder": [{"offsets": [{"slot": "head", "offset": 20}]}]}, "outside the draw order"),
        ({"ik": {"ghost": [{"mix": 1}]}}, "ik constraint not found"),
    ],
)
def test_animation_errors(doc, anim, text):
    assert _has(_with_animation(doc, anim), text), [str(f) for f in check(_with_animation(doc, anim))]


def test_bezier_handles_outside_segment_warn(doc):
    anim = {"bones": {"head": {"rotate": [{"value": 0, "curve": [0.25, 0, 0.75, 10]}, {"time": 2, "value": 10}]}}}
    assert not _has(_with_animation(doc, anim), "handle times", "warning")
    anim["bones"]["head"]["rotate"][0]["curve"] = [0.25, 0, 3, 10]
    assert _has(_with_animation(doc, anim), "handle times outside", "warning")


def test_loop_pop_warns_only_for_named_loops(doc):
    anim = {"bones": {"head": {"rotate": [{"value": 0}, {"time": 1, "value": 5}]}}}
    doc = _with_animation(doc, anim)
    assert not _has(doc, "loop pops", "warning")
    assert _has(doc, "loop pops at the wrap", "warning", loops=["a"])
    assert _has(doc, "named as a loop but does not exist", loops=["missing"])


def test_events_defined_at_the_root_pass(doc):
    doc = _with_animation(doc, {"events": [{"time": 0.2, "name": "step"}]}, events={"step": {}})
    assert _findings(doc) == []


def test_ik_constraint_rules(doc):
    doc["bones"].append({"name": "hand_target", "parent": "root", "x": -80, "y": 260})
    doc["ik"] = [{"name": "arm", "order": 0, "bones": ["arm_left", "hand_left"], "target": "hand_target"}]
    assert _findings(doc) == []
    doc["ik"][0]["bones"] = ["arm_left", "leg_left"]
    assert _has(doc, "must be a child of 'arm_left'")
    doc["ik"][0]["bones"] = ["arm_left"]
    doc["ik"][0]["target"] = "arm_left"
    assert _has(doc, "also one of the constrained bones")


def test_mesh_checks(doc):
    atts = doc["skins"][0]["attachments"]["torso"]
    atts["torso"] = {
        "type": "mesh", "uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2], "hull": 3,
        "vertices": [0, 0, 10, 0, 0, 10], "width": 10, "height": 10,
    }
    assert _findings(doc) == []
    atts["torso"]["triangles"] = [0, 1, 3]
    assert _has(doc, "triangle index outside 0..2")
    atts["torso"]["triangles"] = [0, 1, 2]
    # Weighted: per vertex [boneCount, (boneIndex, x, y, weight) * boneCount].
    atts["torso"]["vertices"] = [1, 2, 0, 0, 1, 1, 2, 10, 0, 1, 1, 2, 0, 10, 0.7]
    assert _has(doc, "weights sum to 0.700", "warning")
    atts["torso"]["vertices"] = [1, 99, 0, 0, 1, 1, 2, 10, 0, 1, 1, 2, 0, 10, 1]
    assert _has(doc, "bone index 99 out of range")


def test_world_transforms_follow_spine_maths():
    doc = {
        "bones": [
            {"name": "root"},
            {"name": "arm", "parent": "root", "x": 10, "rotation": 90},
            {"name": "hand", "parent": "arm", "x": 20},
        ]
    }
    world = d.world_transforms(doc)
    hx, hy = world["hand"].x, world["hand"].y
    assert math.isclose(hx, 10, abs_tol=1e-9) and math.isclose(hy, 20, abs_tol=1e-9)
    lx, ly = world["arm"].inverse_vector(0, 5)  # world up = the arm's local +x
    assert math.isclose(lx, 5, abs_tol=1e-9) and math.isclose(ly, 0, abs_tol=1e-9)


def test_animation_duration_reads_every_timeline():
    anim = {
        "bones": {"a": {"rotate": [{"value": 0}, {"time": 0.5, "value": 1}]}},
        "slots": {"s": {"attachment": [{"time": 1.25, "name": None}]}},
        "events": [{"time": 0.75, "name": "e"}],
    }
    assert d.animation_duration(anim) == 1.25


def test_save_round_trips_unknown_fields(tmp_path, doc):
    doc["skeleton"]["fps"] = 24
    doc["slots"][0]["visible"] = False
    out = d.save(doc, tmp_path / "copy.json")
    assert d.load(out) == doc
