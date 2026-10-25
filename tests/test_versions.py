"""Format versions, game project detection, and the 4.3 checker rules."""

import pytest

from spine2d_mcp import project as proj
from spine2d_mcp.spine import document as d
from spine2d_mcp.spine import versions
from spine2d_mcp.spine.check import check


def _errors(doc):
    return [f"{f.path}: {f.message}" for f in check(doc) if f.level == "error"]


def _warnings(doc):
    return [f"{f.path}: {f.message}" for f in check(doc) if f.level == "warning"]


# ── versions ────────────────────────────────────────────────────────────────

def test_major_minor_and_layout():
    assert versions.major_minor("4.3.26") == "4.3" and versions.major_minor("4.2") == "4.2"
    assert versions.major_minor("4.3.39-beta") == "4.3" and versions.major_minor("nope") is None
    assert versions.of({"skeleton": {"spine": "4.2.43"}}) == "4.2"
    assert versions.layout({"skeleton": {"spine": "4.4.0"}, "constraints": []}) == "4.3"
    assert versions.layout({"skeleton": {"spine": "3.8.99"}}) == "4.2"
    with pytest.raises(versions.UnsupportedVersion, match="supported: 4.2, 4.3"):
        versions.get("4.1")


def test_apply_order_follows_the_42_runtime():
    doc = {"skeleton": {"spine": "4.2"},
           "ik": [{"name": "a", "order": 1}, {"name": "b", "order": 0}],
           "transform": [{"name": "c", "order": 0}], "physics": [{"name": "e"}]}
    # For each i in 0..n-1 the runtime runs the first constraint (ik, transform,
    # path, physics) with order i; c and e share order 0 with b, so never run.
    assert [c["name"] for _, c in d.apply_order(doc)] == ["b", "a", "c", "e"]
    assert d.never_applied(doc) == ["c", "e"]
    assert d.never_applied({"skeleton": {"spine": "4.2"}, "ik": [{"name": "x", "order": 5}]}) == ["x"]
    doc43 = {"skeleton": {"spine": "4.3"}, "constraints": [{"type": "physics", "name": "e"},
                                                          {"type": "ik", "name": "a"}]}
    assert [(k, c["name"]) for k, c in d.apply_order(doc43)] == [("physics", "e"), ("ik", "a")]


# ── game project detection ─────────────────────────────────────────────────

def test_installed_runtime_decides(game):
    root = game(package={"@esotericsoftware/spine-phaser-v4": "^4.2.0"},
                installed={"@esotericsoftware/spine-phaser-v4": "4.2.95"})
    found = proj.detect(root / "assets" / "hero.json")
    assert found.version == "4.2" and "4.2.95 installed" in found.source


def test_a_pinned_range_decides_and_an_open_one_is_unclear(game):
    assert proj.detect(game("a", package={"@esotericsoftware/spine-phaser-v4": "~4.3.0"})).version == "4.3"
    open_range = proj.detect(game("b", package={"@esotericsoftware/spine-player": "^4.2.0"}))
    assert open_range.version is None and open_range.unclear
    assert "pin it with ~" in open_range.problem


def test_npm_ranges():
    assert proj.format_lines("~4.2.0") == ["4.2"]
    assert proj.format_lines("4.2.x") == ["4.2"]
    assert proj.format_lines(">=4.2.0 <4.3.0") == ["4.2"]
    assert proj.format_lines("4.2.x || 4.3.x") == ["4.2", "4.3"]
    for open_ended in ("^4.2.0", "*", "latest", ">=4.2", "file:../spine", ""):
        assert proj.format_lines(open_ended) is None, open_ended


def test_declaration_file_and_conflicts(game):
    assert proj.detect(game("cdn", declared="4.3")).version == "4.3"
    settled = proj.detect(game("settled", package={"@esotericsoftware/spine-core": "^4.2.0"}, declared="4.3"))
    assert settled.version == "4.3"  # the declaration settles an open range
    clash = proj.detect(game("clash", package={"@esotericsoftware/spine-core": "~4.2.0"}, declared="4.3"))
    assert clash.version is None and "says 4.2 but" in clash.problem and clash.candidates == ["4.2", "4.3"]
    mixed = proj.detect(game("mixed", installed={}, package={"@esotericsoftware/spine-core": "~4.2.0",
                                                            "@esotericsoftware/spine-player": "~4.3.0"}))
    assert mixed.unclear and "different format versions" in mixed.problem
    still = proj.detect(game("mixed2", package={"@esotericsoftware/spine-core": "~4.2.0",
                                               "@esotericsoftware/spine-player": "~4.3.0"}, declared="4.3"))
    assert still.version is None  # a declaration does not paper over contradicting packages
    assert proj.detect(game("bad", declared="latest")).problem.endswith("not a format version like 4.3")


def test_no_project_version(game):
    found = proj.detect(game("empty") / "out")
    assert found.version is None and not found.unclear
    assert "no game project version found" in found.note(None)


def test_new_files_ask_when_nothing_says(game):
    root = game("empty")
    with pytest.raises(proj.NeedsAnswer) as asked:
        proj.for_new_file(root / "out")
    result = asked.value.result()
    assert result["needs_answer"] and result["recommended"] == "4.3" and not result["written"]
    assert result["options"][0]["call_again_with"] == {"format_version": "4.3", "save_project_version": True}
    version, found, _ = proj.for_new_file(root / "out", "4.3", save_project_version=True)
    assert version == "4.3" and not (root / ".spine-version").exists()  # saved once the work succeeds
    assert "saved" in proj.save_declaration(found, version)
    assert (root / ".spine-version").read_text() == "4.3\n"
    assert proj.for_new_file(root / "out")[0] == "4.3"  # not asked again


def test_new_files_follow_the_project_and_ask_on_mismatch(game, monkeypatch):
    root = game(installed={"@esotericsoftware/spine-phaser-v4": "4.2.95"},
                package={"@esotericsoftware/spine-phaser-v4": "~4.2.0"})
    assert proj.for_new_file(root)[0] == "4.2"
    with pytest.raises(proj.NeedsAnswer, match="uses Spine 4.2"):
        proj.for_new_file(root, "4.3")
    version, _, notes = proj.for_new_file(root, "4.3", allow_version_mismatch=True)
    assert version == "4.3" and "cannot load" in notes[0]
    with pytest.raises(ValueError, match="contradicts"):
        proj.save_declaration(proj.detect(root), "4.3")
    with pytest.raises(ValueError, match="contradicts"):  # refused before any work is done
        proj.for_new_file(root, "4.3", allow_version_mismatch=True, save_project_version=True)
    monkeypatch.setenv("SPINE_FORMAT_VERSION", "4.2")
    assert proj.for_new_file(game("env"))[0] == "4.2"


def test_unclear_projects_ask_with_the_versions_in_question(game):
    root = game(package={"@esotericsoftware/spine-core": "~4.2.0"}, declared="4.3")
    with pytest.raises(proj.NeedsAnswer) as asked:
        proj.for_new_file(root)
    result = asked.value.result()
    assert [o["call_again_with"]["format_version"] for o in result["options"]] == ["4.3", "4.2"]
    assert result["recommended"] == "4.2"  # what the installed runtime loads
    # saving 4.3 would not settle it (package.json still says 4.2), so it is not offered
    assert "save_project_version" not in result["options"][0]["call_again_with"]
    assert "package.json still says 4.2" in result["options"][0]["answer"]
    assert result["options"][1]["call_again_with"]["save_project_version"] is True


def test_existing_files_ask_on_mismatch(game):
    root = game(declared="4.2")
    with pytest.raises(proj.NeedsAnswer) as asked:
        proj.for_existing_file(root / "hero.json", "4.3")
    options = asked.value.result()["options"]
    assert "convert_skeleton" in options[0]["instead"] and options[1]["call_again_with"] == {"allow_version_mismatch": True}
    assert proj.for_existing_file(root / "hero.json", "4.3", allow_version_mismatch=True).version == "4.2"


# ── 4.3 checker ─────────────────────────────────────────────────────────────

def _doc43(**extra):
    doc = {"skeleton": {"spine": "4.3.26"},
           "bones": [{"name": "root"}, {"name": "a", "parent": "root", "length": 10},
                     {"name": "b", "parent": "a", "x": 10, "length": 10}, {"name": "t", "parent": "root"}],
           "slots": [{"name": "s1", "bone": "root"}, {"name": "s2", "bone": "root"}],
           "animations": {"blink": {}}}
    doc.update(extra)
    return doc


def test_clean_43_constraints():
    doc = _doc43(constraints=[
        {"type": "ik", "name": "ik", "bones": ["a", "b"], "target": "t", "scaleY": "volume"},
        {"type": "transform", "name": "tr", "bones": ["b"], "source": "t",
         "properties": {"rotate": {"to": {"x": {"scale": 2}}}}, "mixX": 0.5},
        {"type": "physics", "name": "ph", "bone": "b", "rotate": 1, "scaleY": "uniform"},
        {"type": "slider", "name": "sl", "animation": "blink", "bone": "t", "property": "x", "to": 1},
        {"type": "slider", "name": "timed", "animation": "blink", "time": 0.5},
    ])
    assert _errors(doc) == [] and _warnings(doc) == []


def test_43_rejects_42_layout_and_keys():
    doc = _doc43(ik=[{"name": "old", "bones": ["a"], "target": "t"}])
    assert any("a 4.2 'ik' array" in e for e in _errors(doc))
    doc = _doc43(constraints=[
        {"type": "transform", "name": "tr", "bones": ["b"], "target": "t", "local": True},
        {"type": "ik", "name": "ik", "bones": ["a"], "target": "t", "uniform": True, "order": 3},
        {"type": "path", "name": "p", "bones": ["a"], "target": "s1"},
    ])
    errors = _errors(doc)
    assert any("[tr].target: a 4.2 key" in e and "reads source" in e for e in errors)
    assert any("source bone None not found" in e for e in errors)
    assert any("[ik].uniform" in e and 'scaleY: "uniform"' in e for e in errors)
    assert any("[p]: slot None not found" in e for e in errors)
    assert any("ignores 'order'" in w for w in _warnings(doc))


def test_43_transform_properties_and_sliders():
    doc = _doc43(constraints=[
        {"type": "transform", "name": "tr", "bones": ["b"], "source": "t",
         "properties": {"spin": {"to": {"rotate": {}}}, "x": {"to": {"tilt": {}}}}},
        {"type": "transform", "name": "idle", "bones": ["b"], "source": "t", "mixRotate": 0.5},
        {"type": "slider", "name": "sl", "animation": "missing", "bone": "t"},
        {"type": "ik", "name": "ik", "bones": ["a"], "target": "t", "scaleY": "stretchy"},
    ])
    errors, warnings = _errors(doc), _warnings(doc)
    assert any("properties.spin: unknown source property" in e for e in errors)
    assert any("properties.x.to.tilt: unknown target property" in e for e in errors)
    assert any("animation 'missing' not found" in e for e in errors)
    assert any("needs property" in e for e in errors)
    assert any("unknown scaleY 'stretchy'" in e for e in errors)
    assert any("[idle]: maps no properties" in w for w in warnings)
    assert any("mixRotate: ignored" in w for w in warnings)


def test_43_linked_meshes_folders_and_slider_timelines():
    mesh = {"type": "mesh", "uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2], "vertices": [0, 0, 1, 0, 0, 1]}
    doc = _doc43(
        constraints=[{"type": "slider", "name": "sl", "animation": "blink"}],
        skins=[{"name": "default", "attachments": {
            "s1": {"m": mesh, "old": {"type": "linkedmesh", "parent": "m"}},
            "s2": {"cross": {"type": "linkedmesh", "source": "m", "slot": "s1"},
                   "lost": {"type": "linkedmesh", "source": "m"}}}}],
        animations={"blink": {}, "a": {
            "slider": {"sl": {"time": [{"value": 0}, {"time": 1}]}, "nope": {"time": [{}]}},
            "drawOrderFolder": [{"slots": ["s1", "s2"], "keys": [{"offsets": [{"slot": "s1", "offset": 1}]}]},
                                {"slots": ["s1"], "keys": [{"offsets": [{"slot": "s2", "offset": 0}]}]}]}})
    errors = _errors(doc)
    assert any("old: 'parent' is the 4.2 key" in e for e in errors)
    assert any("lost: source mesh 'm' not found" in e for e in errors)
    assert not any("cross" in e for e in errors)
    assert any("slider.nope: slider not found" in e for e in errors)
    assert any("drawOrderFolder[1].keys[0]: slot 's2' is not in the folder" in e for e in errors)
    assert not any("drawOrderFolder[0]" in e for e in errors)


def test_42_rejects_43_features(imported):
    doc = d.load(imported.project_path)
    doc["animations"] = {"a": {"drawOrderFolder": [], "slider": {"x": {}}}}
    doc["skins"][0]["attachments"]["head"]["clip"] = {"type": "clipping", "vertexCount": 3,
                                                       "vertices": [0, 0, 1, 0, 0, 1], "inverse": True}
    doc["ik"] = [{"name": "ik", "bones": ["arm_left"], "target": "hand_left", "scaleY": "volume"}]
    errors = _errors(doc)
    assert any("draw order folders: 4.2 ignores them" in e for e in errors)
    assert any("slider timelines: 4.2 has no sliders" in e for e in errors)
    assert any("clip.inverse: a 4.3 key" in e for e in errors)
    assert any("ik[ik].scaleY: a 4.3 key" in e for e in errors)


def test_imported_43_is_clean(imported43):
    doc = d.load(imported43.project_path)
    assert versions.of(doc) == "4.3" and check(doc) == []


def test_42_orders_must_be_0_to_n_minus_1(imported):
    doc = d.load(imported.project_path)
    doc["ik"] = [{"name": "a", "order": 0, "bones": ["arm_left"], "target": "hand_left"},
                 {"name": "b", "order": 0, "bones": ["arm_right"], "target": "hand_right"}]
    doc["physics"] = [{"name": "p", "order": 3, "bone": "head", "rotate": 1}]
    errors = _errors(doc)
    assert any("ik[b].order: same order 0" in e and "never runs" in e for e in errors)
    assert any("physics[p].order: 3 is outside 0..2" in e for e in errors)
