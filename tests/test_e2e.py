import json

from PIL import Image

from spine2d_mcp.pipeline import import_psd_to_spine


def test_pipeline_produces_three_artifacts(synthetic_psd, tmp_path):
    out = tmp_path / "spine_out"
    result = import_psd_to_spine(synthetic_psd, str(out), format_version="4.2")

    assert result.project_path.exists()
    assert result.atlas_path.exists()
    assert len(result.png_paths) >= 1
    for p in result.png_paths:
        assert p.exists()


def test_pipeline_uses_atlas_extension_not_txt(synthetic_psd, tmp_path):
    out = tmp_path / "spine_out"
    result = import_psd_to_spine(synthetic_psd, str(out), format_version="4.2")
    assert result.atlas_path.suffix == ".atlas"
    assert list(out.glob("*.txt")) == []


def test_pipeline_json_is_valid_spine(synthetic_psd, tmp_path):
    result = import_psd_to_spine(synthetic_psd, str(tmp_path / "out"), format_version="4.2")
    data = json.loads(result.project_path.read_text())
    # the release of the editor that imports it (4.2.43 here), which runtimes read as 4.2
    assert data["skeleton"]["spine"].startswith("4.2.")
    newest = import_psd_to_spine(synthetic_psd, str(tmp_path / "out43"), format_version="4.3")
    assert json.loads(newest.project_path.read_text())["skeleton"]["spine"].startswith("4.3.")
    bone_names = {b["name"] for b in data["bones"]}
    assert "root" in bone_names and "torso" in bone_names
    # every slot's bone exists; every attachment slot exists
    slot_names = {s["name"] for s in data["slots"]}
    assert "head" in slot_names
    for slot in data["slots"]:
        assert slot["bone"] in bone_names


def test_pipeline_atlas_regions_match_skin(synthetic_psd, tmp_path):
    result = import_psd_to_spine(synthetic_psd, str(tmp_path / "out"), format_version="4.2")
    atlas_text = result.atlas_path.read_text()
    data = json.loads(result.project_path.read_text())
    for slot, atts in data["skins"][0]["attachments"].items():
        for att_name in atts:
            assert att_name in atlas_text


def test_atlas_png_has_content(synthetic_psd, tmp_path):
    result = import_psd_to_spine(synthetic_psd, str(tmp_path / "out"), format_version="4.2")
    page = Image.open(result.png_paths[0]).convert("RGBA")
    # at least some non-transparent pixels were composited
    assert page.getextrema()[3][1] > 0


def test_pipeline_writes_part_images_for_editor(synthetic_psd, tmp_path):
    """The Spine editor displays individual part images (not the atlas), so
    each attachment is also written to images/ and the skeleton points there."""
    result = import_psd_to_spine(synthetic_psd, str(tmp_path / "out"), format_version="4.2")
    data = json.loads(result.project_path.read_text())
    assert data["skeleton"]["images"] == "./images/"
    assert result.images_dir == result.project_path.parent / "images"
    written = {p.stem for p in result.images_dir.glob("*.png")}
    assert written == set(data["skins"][0]["attachments"])
    with Image.open(result.images_dir / "head.png") as head:
        assert head.size == (80, 80)


def test_mcp_tool_wrapper(synthetic_psd, tmp_path):
    from spine2d_mcp.server import import_psd_to_spine as tool

    (tmp_path / ".git").mkdir()  # a repository with no Spine version declared
    res = tool(synthetic_psd, str(tmp_path / "out"), format_version="4.2")
    assert {"project_path", "atlas_path", "png_paths", "images_dir", "format_version"} <= set(res)
    assert res["project_path"].endswith(".json") and res["format_version"] == "4.2"
