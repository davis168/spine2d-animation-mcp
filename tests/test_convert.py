"""Converting skeletons between format 4.2 and 4.3.

The fixture `constraints-4.2.json` uses every constraint kind, with motion,
so the runtime tests prove poses survive: both skeletons are played in their
own spine-core releases and compared frame by frame.
"""

import copy
import shutil
from pathlib import Path

import pytest

from spine2d_mcp.runtime import compare_poses
from spine2d_mcp.spine import document as d
from spine2d_mcp.spine.check import check
from spine2d_mcp.spine.convert import LossyConversion, convert

FIXTURES = Path(__file__).parent / "fixtures"
needs_node = pytest.mark.skipif(shutil.which("node") is None or shutil.which("npm") is None,
                                reason="node/npm not installed")


@pytest.fixture
def rig():
    return d.load(FIXTURES / "constraints-4.2.json")


def _named(doc):
    return {c["name"]: c for c in doc["constraints"]}


def test_upgrade_matches_what_the_editor_writes(rig):
    up = convert(rig, "4.3")
    doc, cons = up.doc, _named(up.doc)
    assert doc["skeleton"]["spine"] == "4.3" and check(doc) == []
    assert not any(k in doc for k in ("ik", "transform", "path", "physics"))
    # in 4.2's apply order: order, then type
    assert [c["name"] for c in doc["constraints"]] == [
        "t_abs", "t_rel", "leg", "reach", "t_loc", "t_locrel", "t_partial", "pc", "tail_phys", "tail2_phys"]
    # what Spine 4.3.26 writes for these when it opens the 4.2 project
    every = {"rotate": {"to": {"rotate": {"max": 100}}}, "x": {"to": {"x": {"max": 100}}},
             "y": {"to": {"y": {"max": 100}}}, "scaleX": {"to": {"scaleX": {}}},
             "scaleY": {"to": {"scaleY": {}}}, "shearY": {"to": {"shearY": {"max": 100}}}}
    assert cons["t_abs"] == {"type": "transform", "name": "t_abs", "bones": ["t_abs_b"], "source": "driver",
                             "rotation": 10, "x": 5, "properties": every, "mixScaleX": 0.5}
    assert cons["t_rel"]["additive"] is True and cons["t_rel"]["mixRotate"] == 0.3
    assert cons["t_loc"]["localSource"] is cons["t_loc"]["localTarget"] is True
    assert cons["pc"]["slot"] == "path" and cons["pc"]["mixY"] == 1  # 4.2's default, not 4.3's mixX
    assert cons["tail_phys"]["inertia"] == 1 and cons["tail_phys"]["damping"] == 1
    assert "inertia" not in cons["tail2_phys"]  # 0.5 is now the default
    # unkeyed: only properties with a mix are mapped
    # (mixY falls back to mixX, 0, so y stays unmapped like x and shearY)
    assert set(cons["t_partial"]["properties"]) == {"rotate", "scaleX", "scaleY"}
    assert cons["t_partial"]["mixRotate"] == 0.5 and cons["t_partial"]["mixScaleX"] == 0.3
    assert not {"mixX", "mixY", "mixScaleY", "mixShearY"} & set(cons["t_partial"])
    # deliberate: both runtimes apply a two-bone IK's uniform; the editor drops it
    assert cons["leg"]["scaleY"] == "uniform" and "uniform" not in cons["leg"]
    anim = doc["animations"]["move"]
    assert anim["transform"]["t_abs"][0]["mixScaleY"] == 0.5  # 4.2 read mixScaleX, 4.3 reads 1
    assert anim["physics"]["tail_phys"]["mix"][0]["value"] == 0  # 4.2 read 0, 4.3 reads 1
    linked = doc["skins"][0]["attachments"]["body"]["skin_alt"]
    assert linked["source"] == "skin" and "parent" not in linked
    assert up.lost == [] and rig["skeleton"]["spine"] == "4.2.43"  # the input is untouched


def test_round_trip_restores_42(rig):
    back = convert(convert(rig, "4.3").doc, "4.2").doc
    assert check(back) == []
    for kind in ("ik", "transform", "path", "physics"):
        assert [c["name"] for c in back[kind]] == [c["name"] for c in rig[kind]]
    assert back["path"][0]["target"] == "path" and back["ik"][0]["uniform"] is True
    assert back["transform"][4]["mixX"] == 0 and back["transform"][4]["mixShearY"] == 0


def _with_43_features(rig):
    doc = copy.deepcopy(convert(rig, "4.3").doc)
    doc["constraints"].append({"type": "slider", "name": "sl", "animation": "move", "time": 0.5})
    _named(doc)["t_rel"]["properties"] = {"rotate": {"to": {"x": {"scale": 2}}}}
    _named(doc)["leg"]["scaleY"] = "volume"
    doc["skins"][0]["attachments"]["path"]["clip"] = {"type": "clipping", "vertexCount": 3,
                                                       "vertices": [0, 0, 1, 0, 0, 1], "convex": True}
    doc["animations"]["move"]["drawOrderFolder"] = [{"slots": ["body", "body_alt"], "keys": [
        {"time": 0.5, "offsets": [{"slot": "body", "offset": 1}]}]}]
    doc["bones"][1]["iconSize"] = 2
    return doc


def test_downgrade_refuses_what_42_cannot_hold(rig):
    doc = _with_43_features(rig)
    assert [f for f in check(doc) if f.level == "error"] == []
    with pytest.raises(LossyConversion) as lossy:
        convert(doc, "4.2")
    text = " | ".join(lossy.value.losses)
    for expected in ("slider 'sl'", "transform 't_rel' maps rotate to x", "IK 'leg' scaleY 'volume'",
                     "clipping default/path/clip convex"):
        assert expected in text
    assert "draw order" not in text  # folded, not lost
    doc["constraints"].insert(0, doc["constraints"].pop())  # the slider first: orders must still be 0..n-1
    down = convert(doc, "4.2", allow_lossy=True)
    assert len(down.lost) == 4 and down.dropped_editor_data == ["bone 'driver' iconSize"]
    assert any("folded into whole draw order keys" in c for c in down.changed)
    assert down.doc["animations"]["move"]["drawOrder"] == [  # body_alt, body, path
        {"time": 0.5, "offsets": [{"slot": "body", "offset": 1}, {"slot": "body_alt", "offset": -1}]}]
    orders = sorted(c["order"] for _, c in d.all_constraints(down.doc))
    assert orders == list(range(len(orders))) and d.never_applied(down.doc) == []
    assert check(down.doc) == []
    assert "slider" not in str(down.doc.get("constraints")) and "drawOrderFolder" not in down.doc["animations"]["move"]


def test_downgrade_zeroes_what_43_left_unmapped():
    doc = {"skeleton": {"spine": "4.3"}, "bones": [{"name": "root"}, {"name": "a", "parent": "root"},
                                                   {"name": "s", "parent": "root"}],
           "constraints": [{"type": "transform", "name": "t", "bones": ["a"], "source": "s",
                            "properties": {"y": {"to": {"y": {}}}}}],
           "animations": {"x": {"transform": {"t": [
               {"mixX": 0.5, "curve": [0, 1, 1, 1, 0, 0.5, 1, 0.5, 0, 0.5, 1, 0.5, 0, 1, 1, 1, 0, 1, 1, 1, 0, 1, 1, 1]},
               {"time": 1}]}}}}
    down = convert(doc, "4.2").doc
    t = down["transform"][0]
    # y alone is mapped and its setup mix falls back to the (unmapped, so 0) mixX
    assert t["mixRotate"] == 0 and t["mixX"] == 0 and t["mixShearY"] == 0
    assert "mixY" not in t  # 4.2 reads it as mixX, 0
    key = down["animations"]["x"]["transform"]["t"][0]
    assert key["mixX"] == 0 and key["mixY"] == 0.5  # 4.3 read mixY as the key's mixX
    assert key["curve"][1] == key["curve"][3] == 0  # rotate channel flattened to 0
    assert key["mixScaleY"] == 0 and check(down) == []  # unmapped too
    # A mapped scaleY key without mixScaleY: 4.3 reads 1, 4.2 would read mixScaleX.
    doc["constraints"][0]["properties"] = {"scaleX": {"to": {"scaleX": {}}}, "scaleY": {"to": {"scaleY": {}}}}
    doc["animations"]["x"]["transform"]["t"] = [{"mixScaleX": 0.5}, {"time": 1}]
    key = convert(doc, "4.2").doc["animations"]["x"]["transform"]["t"][0]
    assert key["mixScaleX"] == 0.5 and key["mixScaleY"] == 1


def test_unsupported_and_same_version(rig):
    with pytest.raises(ValueError, match="already format version 4.2"):
        convert(rig, "4.2")
    with pytest.raises(ValueError, match="not supported"):
        convert(rig, "4.4")


@needs_node
def test_upgrade_keeps_every_pose(rig, tmp_path):
    up = d.save(convert(rig, "4.3").doc, tmp_path / "rig-4.3.json")
    result = compare_poses(FIXTURES / "constraints-4.2.json", up, FIXTURES / "constraints-4.2.atlas")
    assert result["ran"] and result["ok"], result
    assert result["runtimes"] == ["spine-core 4.2.120", "spine-core 4.3.13"]


@needs_node
def test_pose_check_catches_a_dropped_uniform(rig, tmp_path):
    """Spine 4.3.26's own upgrade drops a two-bone IK's uniform; it shows."""
    doc = convert(rig, "4.3").doc
    _named(doc)["leg"].pop("scaleY")
    result = compare_poses(FIXTURES / "constraints-4.2.json", d.save(doc, tmp_path / "bad.json"),
                           FIXTURES / "constraints-4.2.atlas")
    assert result["ran"] and not result["ok"] and result["largest_difference"]["bone"] == "thigh"


@needs_node
def test_downgrade_keeps_every_pose(rig, tmp_path):
    doc43 = convert(rig, "4.3").doc
    _named(doc43)["t_partial"]["properties"] = {"rotate": {"to": {"rotate": {}}}, "y": {"to": {"y": {}}}}
    _named(doc43)["t_partial"]["mixY"] = 0.7
    path43 = d.save(doc43, tmp_path / "rig.json")
    path42 = d.save(convert(doc43, "4.2").doc, tmp_path / "rig-4.2.json")
    result = compare_poses(path43, path42, FIXTURES / "constraints-4.2.atlas")
    assert result["ran"] and result["ok"], result
