import pytest

from spine2d_mcp import server
from spine2d_mcp.edits import EditError
from spine2d_mcp.ops.animation import add_animation, remove_animation
from spine2d_mcp.spine import document as d
from spine2d_mcp.spine.animation import AnimationError, ease_handles, to_wire
from spine2d_mcp.spine.check import check
from spine2d_mcp.spine.presets import NAMES, preset


@pytest.fixture
def doc(imported):
    return d.load(imported.project_path)


def test_ease_handles_are_absolute():
    # CSS ease-in-out over 0 s..2 s, value 10..30.
    assert ease_handles("inout", 0, 2, 10, 30) == [0.84, 10, 1.16, 30]
    assert ease_handles("linear", 0, 1, 0, 1) is None
    assert ease_handles([0.25, 0.1, 0.25, 1], 1, 2, 0, 100) == [1.25, 10, 1.25, 100]


def test_to_wire_moves_ease_onto_curves():
    anim = to_wire({"bones": {
        "head": {
            "rotate": [{"time": 0, "value": 0, "ease": "inout"}, {"time": 1, "value": 10, "ease": "out"}],
            "translate": [{"time": 0, "x": 0, "y": 0, "ease": "in"}, {"time": 0.5, "x": 4, "y": -8}],
            "scale": [{"time": 0, "ease": "stepped"}, {"time": 1, "x": 2}],
        }
    }})
    rotate = anim["bones"]["head"]["rotate"]
    assert rotate[0]["curve"] == [0.42, 0, 0.58, 10]
    assert "curve" not in rotate[1] and "ease" not in rotate[1]  # nothing follows the last key
    translate = anim["bones"]["head"]["translate"][0]["curve"]
    assert translate == [0.21, 0, 0.5, 4, 0.21, 0, 0.5, -8]  # x channel, then y
    assert anim["bones"]["head"]["scale"][0]["curve"] == "stepped"


def test_to_wire_colours_use_one_channel_per_component():
    anim = to_wire({"slots": {"head": {"rgba": [
        {"time": 0, "color": "ffffffff", "ease": "inout"}, {"time": 1, "color": "ff000000"}]}}})
    curve = anim["slots"]["head"]["rgba"][0]["curve"]
    assert len(curve) == 16
    assert curve[4:8] == [0.42, 1, 0.58, 0]  # green 1 -> 0


def test_to_wire_rejects_angle_and_unknown_eases():
    with pytest.raises(AnimationError, match="use 'value'"):
        to_wire({"bones": {"head": {"rotate": [{"angle": 5}]}}})
    with pytest.raises(AnimationError, match="unknown ease"):
        to_wire({"bones": {"head": {"rotate": [{"value": 0, "ease": "bouncy"}, {"time": 1}]}}})


@pytest.mark.parametrize("name", NAMES)
def test_presets_build_clean_animations(doc, name):
    detail = add_animation(doc, name, preset=name)
    loops = [name] if name in ("idle", "walk") else None
    assert check(doc, loops) == [], [str(f) for f in check(doc, loops)]
    assert detail["skipped_bones"] == [] or name == "wave"
    for timelines in doc["animations"][name]["bones"].values():
        for keys in timelines.values():
            assert all("ease" not in k for k in keys)


def test_walk_keeps_the_hip_still_so_feet_do_not_slide(doc):
    add_animation(doc, "walk", preset="walk")
    assert "hip" not in doc["animations"]["walk"]["bones"]
    assert doc["animations"]["walk"]["bones"]["leg_left"]["translate"][2]["y"] > 0


def test_left_wave_mirrors_the_right(doc):
    add_animation(doc, "wave_l", preset="wave", side="left")
    left = [k["value"] for k in doc["animations"]["wave_l"]["bones"]["arm_left"]["rotate"]]
    right = [k["value"] for k in preset("wave")["bones"]["arm_right"]["rotate"]]
    assert left == [-v for v in right]  # the dip (-8) becomes +8, the raise 160 becomes -160
    assert min(left) == -160


def test_speed_amount_and_height_scaling():
    base = preset("walk")
    fast = preset("walk", speed=2.0, amount=0.5, height=992)
    lift = lambda p: max(k["y"] for k in p["bones"]["leg_left"]["translate"])  # noqa: E731
    assert lift(base) == 16 and lift(fast) == 16  # twice the height, half the amount
    assert fast["bones"]["leg_left"]["translate"][-1]["time"] == 0.4
    sy = min(k["y"] for k in preset("walk", amount=0.5)["bones"]["arm_left"]["scale"])
    assert sy == 0.95  # squash moves halfway back to 1


def test_bone_map_and_missing_bones(doc):
    for b in doc["bones"]:
        if b["name"] == "head":
            b["name"] = "Head"
    for s in doc["slots"]:
        if s["bone"] == "head":
            s["bone"] = "Head"
    detail = add_animation(doc, "idle", preset="idle")
    assert detail["skipped_bones"] == ["head"]
    detail = add_animation(doc, "idle", preset="idle", bone_map={"head": "Head"}, replace=True)
    assert "Head" in detail["bones"] and detail["skipped_bones"] == []
    with pytest.raises(EditError, match="bone_map"):
        add_animation({"bones": [{"name": "root2"}]}, "idle", preset="idle")


def test_custom_timelines_events_and_replace(doc):
    detail = add_animation(doc, "nod", timelines={
        "bones": {"head": {"rotate": [{"time": 0, "value": 0, "ease": "out"}, {"time": 0.3, "value": -8, "ease": "inout"},
                                       {"time": 0.6, "value": 0}]}},
        "events": [{"time": 0.3, "name": "nod_down"}],
    })
    assert detail["defined_events"] == ["nod_down"] and doc["events"] == {"nod_down": {}}
    assert check(doc) == []
    with pytest.raises(EditError, match="already exists"):
        add_animation(doc, "nod", preset="idle")
    remove_animation(doc, "nod")
    assert "animations" not in doc


def test_preset_with_override(doc):
    add_animation(doc, "idle_tilt", preset="idle", timelines={
        "bones": {"head": {"rotate": [{"time": 0, "value": 0, "ease": "smooth"}, {"time": 1, "value": 10, "ease": "smooth"},
                                       {"time": 2, "value": 0}]}}})
    bones = doc["animations"]["idle_tilt"]["bones"]
    assert bones["head"]["rotate"][1]["value"] == 10 and "chest" in bones


def test_tool_writes_validated_file(imported):
    path = str(imported.project_path)
    result = server.add_animation(path, "walk", preset="walk")
    assert result["errors"] == [] and result["warnings"] == []
    assert result["changed"]["duration"] == 0.8
    assert server.validate_skeleton(path, loops=["walk"])["ok"]
    assert server.remove_animation(path, "walk")["changed"] == {"removed": "walk"}
