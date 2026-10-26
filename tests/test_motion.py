"""analyze_motion and onion_skin: animations with known faults, and their clean versions."""

from __future__ import annotations

import shutil

import numpy as np
import pytest
from PIL import Image

from spine2d_mcp import runtime, server
from spine2d_mcp.motion import MotionError, analyze_motion
from spine2d_mcp.onion import OnionError, View, _pages, onion_skin, render_frame
from spine2d_mcp.spine import document as d

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
pytestmark = needs_node

REST = [{"time": 0, "value": 0}]


def rotate(*keys):
    """(time, value[, ease]) -> rotate keys."""
    return {"rotate": [{"time": t, "value": v, **({"ease": e[0]} if e else {})} for t, v, *e in keys]}


def animate(imported, name, bones, **kw):
    path = str(imported.project_path)
    server.add_animation(path, name, timelines={"bones": bones}, **kw)
    return path


def findings(report, animation, kind=None):
    return [f for f in report["findings"] if f["animation"] == animation and (kind is None or f["kind"] == kind)]


def kinds(report, animation):
    return {f["kind"] for f in findings(report, animation)}


def test_linear_spacing_and_its_eased_version(imported43):
    path = animate(imported43, "linear", {"arm_left": rotate((0, 0), (0.3, 0), (0.8, 60), (1.3, 60))})
    animate(imported43, "eased", {"arm_left": rotate((0, 0), (0.3, 0, "inout"), (0.8, 60), (1.3, 60))})
    report = analyze_motion(path, ["linear", "eased"])

    linear, = findings(report, "linear", "linear spacing")
    assert linear["rank"] == 3 and {"arm_left", "hand_left"} <= set(linear["evidence"]["slots"])
    assert linear["evidence"]["time"] == pytest.approx(0.3, abs=0.04) and linear["evidence"]["until"] == pytest.approx(0.8, abs=0.04)
    assert linear["evidence"]["keys"][0].startswith("arm_left rotate @ 0.3")
    assert "frozen hold" in kinds(report, "linear")  # the 0.5 s end hold is dead still
    assert not kinds(report, "eased") & {"linear spacing", "abrupt start", "abrupt stop", "hitch"}
    assert report["animations"]["eased"]["poses"][-1]["time"] == pytest.approx(0.8, abs=0.1)


def test_a_reversal_at_full_speed_is_a_hitch(imported43):
    path = animate(imported43, "snap", {"arm_left": rotate((0, 0), (0.2, 0), (0.6, 60), (1.0, 0), (1.4, 0))})
    animate(imported43, "swing", {"arm_left": rotate((0, 0), (0.2, 0, "inout"), (0.6, 60, "inout"), (1.0, 0), (1.4, 0))})
    report = analyze_motion(path, ["snap", "swing"])
    hitch, = findings(report, "snap", "hitch")
    assert hitch["rank"] == 2 and hitch["evidence"]["time"] == pytest.approx(0.6, abs=0.04)
    assert "hand_left" in hitch["evidence"]["slots"]
    assert not findings(report, "swing", "hitch")


def test_a_jerk_on_a_drawing_swap_above_is_listed_apart(imported43):
    """Per-pose drawings: the hand follows each forearm drawing, so it jerks when one is swapped in."""
    path = str(imported43.project_path)
    doc = d.load(path)
    arm = d.find_skin(doc, "default")["attachments"]["arm_left"]
    arm["arm_left_bent"] = {**arm["arm_left"], "path": arm["arm_left"].get("path", "arm_left")}
    d.save(doc, path)
    server.add_animation(path, "snap", timelines={
        "bones": {"arm_left": rotate((0, 0), (0.2, 0), (0.6, 60), (1.0, 0), (1.4, 0))},
        "slots": {"arm_left": {"attachment": [{"time": 0, "name": "arm_left"}, {"time": 0.6, "name": "arm_left_bent"}]}}})
    report = analyze_motion(path, ["snap"])
    assert not findings(report, "snap", "hitch")
    swap, = findings(report, "snap", "hitch at a swap")
    assert swap["rank"] == 4 and swap["evidence"]["swaps"] == ["arm_left"]
    assert "hand_left" in swap["evidence"]["slots"] and swap["evidence"]["time"] == pytest.approx(0.6, abs=0.04)
    assert "onion_skin" in swap["what"]


def test_straight_paths_and_arcs(imported43):
    path = animate(imported43, "slide", {"hand_left": {"translate": [
        {"time": 0, "x": 0, "y": 0}, {"time": 0.2, "x": 0, "y": 0, "ease": "inout"},
        {"time": 0.8, "x": 80, "y": 0}, {"time": 1.2, "x": 80, "y": 0}]}})
    animate(imported43, "swing", {"arm_left": rotate((0, 0), (0.2, 0, "inout"), (0.8, 50), (1.2, 50))})
    report = analyze_motion(path, ["slide", "swing"])
    straight, = findings(report, "slide", "straight path")
    assert straight["evidence"]["slots"] == ["hand_left"] and straight["principle"] == "7 arcs"
    assert not findings(report, "swing", "straight path")  # a turn about the shoulder draws an arc


def test_parts_moving_in_lockstep_have_no_overlap(imported43):
    same = rotate((0, 0), (0.1, 0, "inout"), (0.6, 20), (1.2, 20))
    late = rotate((0, 0), (0.2, 0, "inout"), (0.7, 20), (1.2, 20))
    later = rotate((0, 0), (0.27, 0, "inout"), (0.77, 20), (1.2, 20))
    path = animate(imported43, "stiff", {b: same for b in ("torso", "arm_left", "arm_right", "head")})
    animate(imported43, "loose", {"torso": same, "arm_left": late, "arm_right": later, "head": late})
    report = analyze_motion(path, ["stiff", "loose"])
    stiff, = findings(report, "stiff", "no overlap")
    assert set(stiff["evidence"]["bones"]) >= {"torso", "arm_left", "arm_right", "head"}
    assert not findings(report, "loose", "no overlap")
    chains = report["animations"]["loose"]["overlap"][0]["chains"]
    assert {"parent": "torso", "child": "arm_left", "start_ms": 100, "stop_ms": 100} in chains


def test_mirrored_arms_are_twins(imported43):
    path = animate(imported43, "twins", {"arm_left": rotate((0, 0, "inout"), (0.5, 40), (1, 40)),
                                         "arm_right": rotate((0, 0, "inout"), (0.5, -40), (1, -40))})
    animate(imported43, "solo", {"arm_left": rotate((0, 0, "inout"), (0.5, 40), (1, 40)),
                                 "arm_right": rotate((0, 0), (0.2, 0, "out"), (0.8, -25), (1, -25))})
    report = analyze_motion(path, ["twins", "solo"])
    twins, = findings(report, "twins", "twinning")
    assert twins["evidence"]["bones"] == ["arm_left", "arm_right"]
    assert not findings(report, "solo", "twinning")


def test_a_widened_limb_is_a_stretch(imported43):
    path = animate(imported43, "stretch", {"arm_left": {"scale": [
        {"time": 0, "x": 1, "y": 1, "ease": "inout"}, {"time": 0.5, "x": 1.3, "y": 1}, {"time": 1, "x": 1.3, "y": 1}]}})
    animate(imported43, "shrink", {"arm_left": {"scale": [
        {"time": 0, "x": 1, "y": 1, "ease": "inout"}, {"time": 0.5, "x": 1, "y": 0.8}, {"time": 1, "x": 1, "y": 0.8}]}})
    report = analyze_motion(path, ["stretch", "shrink"])
    stretch, = findings(report, "stretch", "stretch")  # the hand only inherits the arm's scale
    assert stretch["evidence"]["bone"] == "arm_left" and stretch["evidence"]["ratio"] == pytest.approx(1.3, abs=0.01)
    assert stretch["evidence"]["keys"] == ["arm_left scale @ 0.5"]
    assert not findings(report, "shrink", "stretch")  # foreshortening (scale < 1) is allowed


def test_loops_seam_amplitude_and_lockstep(imported43):
    breathe = rotate((0, 0, "inout"), (1, 3, "inout"), (2, 0))
    path = animate(imported43, "breathe", {b: breathe for b in ("torso", "head", "arm_left")})
    animate(imported43, "drift", {"torso": breathe, "head": rotate((0, 0, "inout"), (0.7, 2, "inout"), (2, 0)),
                                  "arm_left": rotate((0, 1, "inout"), (1.4, -2, "inout"), (2, 1))})
    animate(imported43, "seam", {"head": rotate((0, 0, "inout"), (1, 15))})
    report = analyze_motion(path, ["breathe", "drift", "seam"], loops=["breathe", "drift", "seam"])
    assert "lockstep loop" in kinds(report, "breathe") and "lockstep loop" not in kinds(report, "drift")
    assert report["animations"]["breathe"]["loop_seam_gap"] < 0.01 and report["animations"]["breathe"]["amplitude"] > 1
    seam, = findings(report, "seam", "loop seam")
    assert seam["rank"] == 2 and seam["evidence"]["slot"] == "head"
    assert report["loops"] == ["breathe", "drift", "seam"]


def test_a_hard_mix_jumps(imported43):
    path = animate(imported43, "up", {"arm_left": rotate((0, 60), (0.5, 60))})
    animate(imported43, "down", {"arm_left": rotate((0, 0), (0.5, 0))})
    report = analyze_motion(path, ["up", "down"], mixes=[{"from": "up", "to": "down", "mix": 0},
                                                         {"from": "down", "to": "up", "mix": 0.3}])
    jump, = [f for f in report["findings"] if f["animation"] == "up -> down"]
    assert jump["kind"] == "mix teleport" and "hand_left" in jump["evidence"]["slots"]
    assert jump["evidence"]["time"] == pytest.approx(0, abs=0.04)
    assert not [f for f in report["findings"] if f["animation"] == "down -> up"]


def test_findings_from_the_keys(imported43):
    path = animate(imported43, "blocked", {
        "arm_left": rotate((0, 0, "stepped"), (0.5, 30, "stepped"), (1, 0)),
        "head": rotate((0, 0, "inout"), (0.2, 5, "inout"), (0.4, 0, "inout"), (0.6, 5, "inout"), (0.8, 0, "inout"),
                       (1.0, 5, "inout"), (1.2, 0)),
        "arm_right": rotate((0, 0), (1.5, 30))})
    report = analyze_motion(path, ["blocked"])
    found = {f["kind"]: f for f in findings(report, "blocked")}
    assert found["stepped keys"]["evidence"]["count"] == 2 and found["stepped keys"]["rank"] == 5
    assert found["same ease everywhere"]["rank"] == 5
    assert found["floaty"]["evidence"]["keys"] == ["arm_right rotate @ 0.0-1.5"]
    assert found["linear keys"]["evidence"]["keys"] == ["arm_right rotate @ 0.0-1.5"]


def test_a_4_2_skeleton_too(imported):
    path = animate(imported, "snap", {"arm_left": rotate((0, 0), (0.2, 0), (0.6, 60), (1.0, 0), (1.4, 0))})
    report = analyze_motion(path, ["snap"])
    assert [f["kind"] for f in findings(report, "snap") if f["rank"] == 2] == ["hitch"]
    assert report["runtime"].startswith("spine-core 4.2")
    assert onion_skin(path, "snap", time=0.6)["ghosts"]


def test_refusals(imported43):
    path = str(imported43.project_path)
    with pytest.raises(MotionError, match="no animation 'nope'|no animation nope"):
        analyze_motion(path, ["nope"])
    with pytest.raises(OnionError, match="no animation"):
        onion_skin(path, "nope")


def test_onion_skin_ghosts_and_trails(imported43, tmp_path):
    path = animate(imported43, "swing", {"arm_left": rotate((0, 0), (0.2, 0, "inout"), (0.8, 60), (1.2, 60))})
    out = onion_skin(path, "swing", time=0.5, before=6, after=6, step=3, size=600, out_path=str(tmp_path / "o.png"))
    assert [g["time"] for g in out["ghosts"]] == pytest.approx([0.3, 0.4, 0.6, 0.7], abs=1e-3)
    assert [g["side"] for g in out["ghosts"]] == ["before", "before", "after", "after"]
    assert out["trails"] == ["hand_left"]  # the end of the only moving chain
    image = Image.open(out["path"])
    assert max(image.size) == 600
    pixels = np.asarray(image.convert("RGB"), dtype=int)
    warm = (pixels[..., 0] > pixels[..., 2] + 60).sum()
    cool = (pixels[..., 2] > pixels[..., 0] + 60).sum()
    assert warm > 50 and cool > 50  # ghosts on both sides of the current pose

    keyed = onion_skin(path, "swing", time=0.5, before=12, after=12, keyed=True, trails=[], out_path=str(tmp_path / "k.png"))
    assert [g["time"] for g in keyed["ghosts"]] == [0.2, 0.8] and keyed["trails"] == []
    chosen = onion_skin(path, "swing", time=1.0, times=[0.2, 0.5, 0.8], trails=["head"], out_path=str(tmp_path / "t.png"))
    assert [g["time"] for g in chosen["ghosts"]] == [0.2, 0.5, 0.8] and chosen["trails"] == ["head"]


def test_the_renderer_draws_what_the_psd_shows(tmp_path):
    """A tagged PSD (mesh, linked mesh, rotated and scaled layers), imported and drawn by spine-core +
    render_frame, matches the PSD's own composite."""
    from test_psd_tags import SIZE, difference, group, layer, new_psd, psd_view, run, save

    psd = new_psd()
    head = group(psd, "head [slot]")
    layer(head, "face [mesh]", (60, 20, 70, 80), (60, 60, 200))
    layer(psd, "big [scale:2]", (20, 120, 30, 50), (200, 80, 80))
    layer(psd, "turned [rotate:30]", (120, 110, 50, 70), (80, 200, 80))
    source = save(psd, tmp_path)
    result, _ = run(source, tmp_path)
    path = str(result.project_path)
    server.add_animation(path, "still", timelines={"bones": {"root": rotate((0, 0), (0.5, 0))}})
    frame = runtime.motion(path, {"mode": "render", "skins": [], "animation": "still", "times": [0]})["frames"][0]
    drawn = render_frame(frame["draws"], _pages(result.atlas_path), View(-100, 100, 1.0, 0, SIZE))
    assert difference(drawn, psd_view(source)) < 0.6


@pytest.mark.parametrize("version", ["4.2", "4.3"])
def test_clipping_is_drawn(imported, imported43, version):
    result = imported if version == "4.2" else imported43
    path = animate(result, "still", {"root": rotate((0, 0), (0.5, 0))})

    def drawn_pixels():
        frame = runtime.motion(path, {"mode": "render", "skins": [], "animation": "still", "times": [0]})["frames"][0]
        view = View(-210, 500, 1.0, 0, (420, 520))
        return int((np.asarray(render_frame(frame["draws"], _pages(result.atlas_path), view))[..., 3] > 0).sum())
    whole = drawn_pixels()
    server.edit_rig(path, [{"op": "add_slot", "name": "mask", "bone": "torso", "index": 1}])
    server.add_clipping(path, "mask", [[-20, -20], [20, -20], [20, 20], [-20, 20]], end="torso")
    clipped = drawn_pixels()
    torso = int((np.asarray(Image.open(result.images_dir / "torso.png").convert("RGBA"))[..., 3] > 0).sum())
    assert whole - clipped == pytest.approx(torso - 40 * 40, rel=0.01)  # all of the torso but the 40 x 40 clip


def test_the_mcp_tools(imported43, tmp_path):
    path = animate(imported43, "snap", {"arm_left": rotate((0, 0), (0.2, 0), (0.6, 60), (1.0, 0), (1.4, 0))})
    report = server.analyze_motion(path, animations=["snap"])
    assert report["summary"].startswith(f"{len(report['findings'])} findings in 1 animation")
    assert report["skins"] == [] and report["runtime"].startswith("spine-core 4.3")
    skin = server.onion_skin(path, "snap", time=0.6, out_path=str(tmp_path / "snap.png"))
    assert Image.open(skin["path"]).size == tuple(skin["size"])
    assert d.load(path)["animations"]["snap"]  # read-only: the skeleton is unchanged
