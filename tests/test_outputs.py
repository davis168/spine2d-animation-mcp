"""Runtime check, Spine CLI wrapper and HTML preview."""

import base64
import json
import shutil

import pytest

from spine2d_mcp import server, spine_cli
from spine2d_mcp.preview import _atlas_pages, preview_html
from spine2d_mcp.runtime import runtime_check

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
needs_spine = pytest.mark.skipif(spine_cli.find_spine() is None, reason="Spine editor not installed")


@pytest.fixture
def animated(imported):
    path = str(imported.project_path)
    for name in ("idle", "walk", "wave", "jump"):
        server.add_animation(path, name, preset=name)
    return imported


def test_gif_durations_average_the_frame_rate():
    assert spine_cli.gif_durations(6, 30) == [30, 40, 30, 30, 40, 30]
    assert sum(spine_cli.gif_durations(60, 30)) == 2000
    assert spine_cli.gif_durations(4, 25) == [40, 40, 40, 40]


def test_png_settings_and_frame_grouping(tmp_path):
    settings = spine_cli._png_settings(tmp_path / "a.json", tmp_path / "f", "hero", 30, 100, "ff000080")
    assert settings["class"] == "export-png" and settings["skeleton"] == "hero"
    assert settings["background"] == {"r": 1.0, "g": 0.0, "b": 0.0, "a": 128 / 255}
    assert "background" not in spine_cli._png_settings(tmp_path / "a.json", tmp_path, "h", 30, 100, None)
    for name in ("hero-run-fast_00.png", "hero-run-fast_01.png", "hero-idle_00.png", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    groups = spine_cli._group_frames(tmp_path, "hero")
    assert {k: len(v) for k, v in groups.items()} == {"run-fast": 2, "idle": 1}


def test_missing_spine_is_reported(monkeypatch):
    monkeypatch.setenv("SPINE_EXECUTABLE", "/nonexistent/Spine")
    assert spine_cli.find_spine() is None
    with pytest.raises(spine_cli.SpineCliError, match="SPINE_EXECUTABLE"):
        spine_cli.run_spine(["-v"])


def test_preview_embeds_everything(animated):
    out = preview_html(animated.project_path)
    page = out.read_text()
    config = json.loads(page.split("const config = ", 1)[1].split(";\n", 1)[0])
    assert config["skeleton"] == "test_character.json" and config["animation"] == "idle"
    assert set(config["rawDataURIs"]) == {"test_character.json", "test_character.atlas", "test_character.png"}
    embedded = base64.b64decode(config["rawDataURIs"]["test_character.json"].split(",", 1)[1])
    assert json.loads(embedded)["animations"].keys() == {"idle", "walk", "wave", "jump"}
    assert "spine-player@4.2.120" in page
    with pytest.raises(ValueError, match="no animation"):
        preview_html(animated.project_path, animation="dance")


def test_atlas_pages_handle_multiple_pages():
    text = "\nchar.png\nsize: 64,64\na\n  bounds: 0,0,1,1\n\nchar_2.png\nsize: 64,64\nb\n  bounds: 0,0,1,1\n"
    assert _atlas_pages(text) == ["char.png", "char_2.png"]


@needs_node
def test_runtime_check_loads_and_steps_animations(animated):
    result = runtime_check(animated.project_path)
    assert result["ran"] and result["ok"], result
    assert result["runtime"] == "spine-core 4.2.120"
    by_name = {a["name"]: a for a in result["animations"]}
    assert by_name["walk"]["duration"] == 0.8 and by_name["walk"]["finite"]
    assert by_name["jump"]["max_bone_travel"] > 50


@needs_node
def test_runtime_check_catches_atlas_mismatch(animated):
    atlas = animated.atlas_path
    atlas.write_text(atlas.read_text().replace("head\n", "hat\n"))
    result = runtime_check(animated.project_path)
    assert result["ran"] and not result["ok"]
    assert any("head" in e for e in result["errors"])


@needs_node
def test_validate_tool_with_runtime(animated):
    result = server.validate_skeleton(str(animated.project_path), loops=["idle", "walk"], runtime=True)
    assert result["ok"] and result["runtime"]["ran"], result


@needs_spine
def test_spine_cli_import_and_gif_export(animated, tmp_path):
    imported = spine_cli.spine_import(animated.project_path)
    assert imported["ok"], imported
    out = spine_cli.spine_export(animated.project_path, tmp_path / "gifs", "gif", animations=["walk"])
    assert out["ok"], out
    assert list(out["gifs"]) == ["walk"] and out["gifs"]["walk"]["frames"] == 24
    from PIL import Image

    with Image.open(out["gifs"]["walk"]["path"]) as gif:
        gif.seek(12)
        # The fixture's flat-coloured parts on white, not a blank (one-colour) frame.
        assert len(gif.convert("RGB").getcolors(1 << 20)) > 5
