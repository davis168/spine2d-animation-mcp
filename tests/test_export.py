"""spine_export frames: animation names with '/', skinned skeletons, missing images."""

from __future__ import annotations

import json

import pytest
from PIL import Image

from spine2d_mcp import server, spine_cli
from spine2d_mcp.spine_cli import SHOWN_SKIN, SpineCliError, _group_frames, _missing_images, _shown_copy

needs_spine = pytest.mark.skipif(spine_cli.find_spine() is None, reason="Spine editor not installed")


def _frames(folder, *names):
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        Image.new("RGB", (4, 4)).save(folder / name)
    return folder


def test_frames_are_keyed_by_animation_name(tmp_path):
    folder = _frames(tmp_path / "f", "hero-shown-outcome-dragon_01.png", "hero-shown-outcome-dragon_00.png",
                     "hero-shown-idle_00.png", "hero-shown-no-deal_00.png", "hero-shown-deal_00.png")
    groups = _group_frames(folder, "hero", ["outcome/dragon", "idle", "deal", "no-deal"], SHOWN_SKIN)
    assert sorted(groups) == ["deal", "idle", "no-deal", "outcome/dragon"]
    assert [p.name for p in groups["outcome/dragon"]] == ["hero-shown-outcome-dragon_00.png",
                                                          "hero-shown-outcome-dragon_01.png"]
    # A .spine project's frames carry whatever skin the editor had current.
    other = _frames(tmp_path / "g", "hero-cards-classic-outcome-dragon_00.png", "hero-walk_00.png")
    assert sorted(_group_frames(other, "hero", ["outcome/dragon", "walk"])) == ["outcome/dragon", "walk"]


def _skinned():
    mesh = {"type": "mesh", "uvs": [0, 0, 1, 0, 1, 1], "triangles": [0, 1, 2], "vertices": [0, 0, 1, 0, 1, 1],
            "hull": 3, "width": 1, "height": 1}
    return {"skeleton": {"spine": "4.3.26", "images": "./images/", "audio": "./audio/"},
            "bones": [{"name": "root"}, {"name": "hat", "parent": "root", "skin": True}],
            "slots": [{"name": "head", "bone": "root", "attachment": "face"}, {"name": "top", "bone": "hat"}],
            "skins": [{"name": "default", "attachments": {}},
                      {"name": "blue", "bones": ["hat"], "attachments": {"head": {"face": {"name": "blue/face", **mesh}}}},
                      {"name": "red", "attachments": {"head": {"face": {"name": "red/face", "type": "linkedmesh",
                                                                         "skin": "blue", "source": "face",
                                                                         "width": 1, "height": 1}}}},
                      {"name": "hats", "attachments": {"top": {"cap": {"width": 4, "height": 4}}}}],
            "animations": {}}


def test_the_shown_copy_combines_the_skins(tmp_path):
    copy = _shown_copy(_skinned(), tmp_path, ["blue", "red", "hats"])
    assert [s["name"] for s in copy["skins"]] == ["default", SHOWN_SKIN]
    shown = copy["skins"][1]
    assert shown["attachments"]["head"]["face"]["name"] == "red/face"  # later skins win a placeholder
    assert shown["attachments"]["head"]["face"]["skin"] == SHOWN_SKIN  # its source mesh came along
    assert shown["attachments"]["top"] == {"cap": {"width": 4, "height": 4}} and shown["bones"] == ["hat"]
    assert copy["skeleton"]["images"] == str((tmp_path / "images").resolve()) + "/"
    with pytest.raises(SpineCliError, match="no skin green"):
        _shown_copy(_skinned(), tmp_path, ["green"])
    with pytest.raises(SpineCliError, match="uses a mesh in skin 'blue': add it to skins"):
        _shown_copy(_skinned(), tmp_path, ["red"])


def test_missing_images_are_named(tmp_path):
    doc = _skinned()
    (tmp_path / "images" / "blue").mkdir(parents=True)
    Image.new("RGBA", (2, 2)).save(tmp_path / "images" / "blue" / "face.png")
    assert _missing_images(_shown_copy(doc, tmp_path, ["blue"]), tmp_path) is None
    warning = _missing_images(_shown_copy(doc, tmp_path, ["blue", "hats"]), tmp_path)
    assert warning.startswith("1 attachment image(s) are not in") and "cap" in warning


@needs_spine
def test_frames_of_a_skinned_skeleton(tmp_path):
    from test_psd_tags import group, layer, new_psd, run, save

    psd = new_psd()
    head = group(psd, "head [slot]")
    blue = group(head, "blue [skin]")
    layer(blue, "face", (70, 20, 60, 70), (60, 60, 200))
    red = group(head, "red [skin]")
    layer(red, "face", (75, 25, 50, 60), (200, 60, 60), visible=False)
    result, _ = run(save(psd, tmp_path), tmp_path)
    path = str(result.project_path)
    server.add_animation(path, "outcome/win", timelines={"bones": {"root": {"rotate": [
        {"time": 0, "value": 0, "ease": "inout"}, {"time": 0.5, "value": 20}]}}})
    server.add_animation(path, "rest", timelines={"bones": {"root": {"rotate": [{"time": 0, "value": 0}]}}})

    png = server.spine_export(path, str(tmp_path / "png"), "png", animations=["outcome/win"], fps=10, scale=50)
    assert png["ok"] and png["animations"] == {"outcome/win": 5} and png["skins"] == ["blue"], png
    frame = sorted((tmp_path / "png" / "frames").glob("*.png"))[3]
    assert len(Image.open(frame).convert("RGB").getcolors(1 << 20)) > 3  # the blue face, not a blank frame

    red_png = server.spine_export(path, str(tmp_path / "red"), "png", animations=["rest"], fps=10, skins=["red"])
    pixel = Image.open(next((tmp_path / "red" / "frames").glob("*.png"))).convert("RGB")
    assert red_png["skins"] == ["red"] and (200, 60, 60) in {c for _, c in pixel.getcolors(1 << 20)}

    bare = server.spine_export(path, str(tmp_path / "bare"), "png", animations=["rest"], fps=10, skins=[])
    pixel = Image.open(next((tmp_path / "bare" / "frames").glob("*.png"))).convert("RGB")
    assert bare["skins"] == [] and max(pixel.size) <= 2 and "the editor drew nothing for rest" in bare["warnings"][-1]

    gif = server.spine_export(path, str(tmp_path / "gif"), "gif", animations=["outcome/win"], fps=10, scale=50)
    assert list(gif["gifs"]) == ["outcome/win"] and gif["gifs"]["outcome/win"]["path"].endswith("-outcome-win.gif")

    doc = json.loads(result.project_path.read_text())
    doc["skeleton"]["images"] = "./nowhere/"
    broken = result.project_path.with_name("broken.json")
    broken.write_text(json.dumps(doc))
    broken.with_suffix(".atlas").write_text(result.atlas_path.read_text())
    out = server.spine_export(str(broken), str(tmp_path / "broken"), "png", animations=["rest"], fps=10)
    missing = [w for w in out.get("warnings", []) if w.startswith("1 attachment image(s) are not in")]
    assert missing and "(blue/face)" in missing[0], out  # only the shown skin's images are needed
