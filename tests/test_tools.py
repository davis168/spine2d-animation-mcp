"""The MCP tool functions, called directly."""

import json

import pytest

from spine2d_mcp import server
from spine2d_mcp.edits import EditError, edit_skeleton
from spine2d_mcp.spine import document as d


def test_inspect_lists_rig(imported):
    info = server.inspect_skeleton(str(imported.project_path))
    names = [b["name"] for b in info["bones"]]
    assert names[0] == "root" and "hand_left" in names
    arm = next(b for b in info["bones"] if b["name"] == "arm_left")
    assert arm["world"] == [-80.0, 370.0]
    assert [s["name"] for s in info["slots"]][0] == "head"
    assert info["skins"][0]["attachments"]["head"]["head"]["type"] == "region"
    assert info["animations"] == []


def test_validate_reports_ok_and_errors(imported, tmp_path):
    ok = server.validate_skeleton(str(imported.project_path))
    assert ok["ok"] and ok["errors"] == [] and ok["summary"].startswith("spine 4.2")
    doc = d.load(imported.project_path)
    doc["skeleton"]["spine"] = "4.3.26"
    bad = d.save(doc, tmp_path / "bad.json")
    result = server.validate_skeleton(str(bad))
    assert not result["ok"] and "targets Spine 4.2" in result["errors"][0]


def test_edit_refuses_changes_that_add_errors(imported):
    path = str(imported.project_path)
    original = imported.project_path.read_text()
    with pytest.raises(EditError, match="bone 'ghost' not found"):
        edit_skeleton(path, lambda doc: doc["slots"][0].update(bone="ghost"))
    assert imported.project_path.read_text() == original


def test_edit_allows_fixes_on_files_with_old_errors(imported):
    path = str(imported.project_path)
    doc = d.load(path)
    doc["skeleton"]["spine"] = "4.3.26"  # a pre-existing error
    d.save(doc, path)
    result = edit_skeleton(path, lambda doc: doc["slots"][0].update(color="ff0000ff"))
    assert any("targets Spine 4.2" in e for e in result["errors"])
    assert json.loads(imported.project_path.read_text())["slots"][0]["color"] == "ff0000ff"
