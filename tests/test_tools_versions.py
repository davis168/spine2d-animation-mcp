"""The MCP tools across format versions: asking, mismatches, converting,
4.3 editing, and per-version editor, runtime and player releases."""

import json
import shutil
from pathlib import Path

import pytest

from spine2d_mcp import server, spine_cli
from spine2d_mcp.edits import EditError
from spine2d_mcp.ops import constraints as con
from spine2d_mcp.ops import rig
from spine2d_mcp.ops.animation import add_animation
from spine2d_mcp.preview import preview_html
from spine2d_mcp.runtime import runtime_check
from spine2d_mcp.spine import document as d
from spine2d_mcp.spine import versions
from spine2d_mcp.spine.check import check

FIXTURES = Path(__file__).parent / "fixtures"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
needs_spine = pytest.mark.skipif(spine_cli.find_spine() is None, reason="Spine editor not installed")
PHASER_42 = {"@esotericsoftware/spine-phaser-v4": "~4.2.0"}


def _copy_rig(folder: Path, name="rig") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / "constraints-4.2.atlas", folder / f"{name}.atlas")
    return Path(shutil.copyfile(FIXTURES / "constraints-4.2.json", folder / f"{name}.json"))


# ── creating ────────────────────────────────────────────────────────────────

def test_import_asks_then_saves_the_answer(synthetic_psd, game):
    root = game("cdn-game")
    asked = server.import_psd_to_spine(synthetic_psd, str(root / "hero"))
    assert asked["needs_answer"] and asked["recommended"] == "4.3" and not (root / "hero").exists()
    done = server.import_psd_to_spine(synthetic_psd, str(root / "hero"), **asked["options"][0]["call_again_with"])
    assert done["format_version"] == "4.3" and (root / ".spine-version").read_text() == "4.3\n"
    assert versions.of(d.load(done["project_path"])) == "4.3"
    again = server.import_psd_to_spine(synthetic_psd, str(root / "hero2"))
    assert again["format_version"] == "4.3" and "the game project uses 4.3" in again["version"]


def test_import_follows_the_game_project(synthetic_psd, game):
    root = game(package=PHASER_42)
    result = server.import_psd_to_spine(synthetic_psd, str(root / "assets"))
    assert result["format_version"] == "4.2"
    asked = server.import_psd_to_spine(synthetic_psd, str(root / "assets"), format_version="4.3")
    assert asked["needs_answer"] and "uses Spine 4.2" in asked["question"]
    told = server.import_psd_to_spine(synthetic_psd, str(root / "new"), format_version="4.3",
                                      allow_version_mismatch=True)
    assert told["format_version"] == "4.3" and "cannot load" in told["notes"][0]


# ── editing and validating ──────────────────────────────────────────────────

def test_editing_a_mismatched_file_asks_first(game):
    path = _copy_rig(game(declared="4.3") / "spine")
    original = path.read_text()
    asked = server.add_animation(str(path), "nod", preset="idle", bone_map={"head": "driver"})
    assert asked["needs_answer"] and "rig.json is a Spine 4.2 file" in asked["question"]
    assert "convert_skeleton" in asked["options"][0]["instead"] and path.read_text() == original
    done = server.add_animation(str(path), "nod", timelines={"bones": {"driver": {"rotate": [{"value": 0}]}}},
                                allow_version_mismatch=True)
    assert done["errors"] == [] and "the game cannot load it" in done["version"]


def test_validate_reports_the_mismatch(game):
    path = _copy_rig(game(package=PHASER_42) / "a")
    assert server.validate_skeleton(str(path))["ok"]
    path43 = _copy_rig(game("next", declared="4.3") / "a")
    result = server.validate_skeleton(str(path43))
    assert not result["ok"] and "game project uses 4.3" in result["errors"][0]
    assert result["version"]["file"] == "4.2" and result["version"]["game_project"] == "4.3"
    info = server.inspect_skeleton(str(path43))
    assert info["format_version"] == "4.2" and info["version"]["from"].endswith(".spine-version: 4.3")


def test_editing_refuses_unsupported_versions(imported):
    doc = d.load(imported.project_path)
    doc["skeleton"]["spine"] = "4.1.24"
    d.save(doc, imported.project_path)
    with pytest.raises(EditError, match="support 4.2, 4.3"):
        server.remove_animation(str(imported.project_path), "idle")


# ── converting ──────────────────────────────────────────────────────────────

def test_convert_tool_writes_a_new_file_and_verifies(game):
    path = _copy_rig(game(declared="4.3") / "spine")
    result = server.convert_skeleton(str(path))  # the game project's version
    assert result["converted"] and result["to"] == "4.3" and result["errors"] == []
    out = Path(result["project_path"])
    # one atlas for both: no copy that could drift when either skeleton repacks it
    assert out.name == "rig-4.3.json" and Path(result["atlas_path"]).name == "rig.atlas"
    assert not (path.parent / "rig-4.3.atlas").exists() and "atlas_note" not in result
    assert d.load(path)["skeleton"]["spine"] == "4.2.43"  # the original is kept
    if shutil.which("node"):
        assert result["verify"]["ok"], result["verify"]
    assert server.validate_skeleton(str(out))["ok"]
    with pytest.raises(EditError, match="already exists"):
        server.convert_skeleton(str(path), "4.3")
    assert server.convert_skeleton(str(out))["converted"] is False  # already the project's version


def test_convert_tool_asks_before_losing_anything(game, tmp_path):
    path = _copy_rig(game(declared="4.3") / "spine")
    up = Path(server.convert_skeleton(str(path), verify=False)["project_path"])
    doc = d.load(up)
    doc["constraints"].append({"type": "slider", "name": "sl", "animation": "move"})
    d.save(doc, up)
    asked = server.convert_skeleton(str(up), "4.2", out_path=str(tmp_path / "down.json"),
                                    allow_version_mismatch=True, verify=False)
    assert asked["needs_answer"] and "slider 'sl'" in asked["question"] and not (tmp_path / "down.json").exists()
    done = server.convert_skeleton(str(up), "4.2", out_path=str(tmp_path / "down.json"),
                                   allow_version_mismatch=True, allow_lossy=True, verify=False)
    assert done["lost"] == ["slider 'sl' (4.2 has no sliders)"]
    assert d.load(done["project_path"])["skeleton"]["images"].endswith("/")


def test_convert_into_another_folder_keeps_the_images(game, tmp_path):
    path = _copy_rig(game(declared="4.3") / "spine")
    elsewhere = str(tmp_path / "elsewhere" / "rig.json")
    # no game project there, and 4.3 is the only other version: nothing to ask
    result = server.convert_skeleton(str(path), out_path=elsewhere, verify=False)
    assert result["to"] == "4.3"
    images = d.load(result["project_path"])["skeleton"]["images"]
    assert (tmp_path / "elsewhere" / images).resolve() == (path.parent / "images").resolve()
    assert result["atlas_path"] == str(path.with_suffix(".atlas")) and "atlas_path=" in result["atlas_note"]


# ── 4.3 editing ─────────────────────────────────────────────────────────────

@pytest.fixture
def doc43(imported43):
    return d.load(imported43.project_path)


def test_43_constraints_use_one_array_and_translate_options(doc43):
    con.add_constraint(doc43, "ik", "reach", bones=["arm_left"], target="hand_left", uniform=True, stretch=True)
    con.add_constraint(doc43, "transform", "follow", bones=["hand_right"], target="hand_left", local=True,
                       relative=True, mixRotate=0.5)
    rig.set_bone(doc43, "head", length=60)
    con.add_constraint(doc43, "physics", "hair", bone="head", preset="hair")
    con.add_constraint(doc43, "transform", "first", bones=["foot_left"], target="foot_right", order=0,
                       properties={"x": {"to": {"y": {"scale": -1}}}})
    cons = {c["name"]: c for c in doc43["constraints"]}
    assert [c["name"] for c in doc43["constraints"]] == ["first", "reach", "follow", "hair"]
    assert cons["reach"]["scaleY"] == "uniform" and "uniform" not in cons["reach"]
    assert cons["follow"]["source"] == "hand_left" and cons["follow"]["additive"] is True
    assert cons["follow"]["localSource"] and cons["follow"]["localTarget"]
    assert set(cons["follow"]["properties"]) == {"rotate", "x", "y", "scaleX", "scaleY", "shearY"}
    assert "inertia" not in cons["hair"] and "damping" not in cons["hair"]  # the preset matches 4.3's defaults
    assert cons["first"]["mixY"] == 1 and "order" not in cons["first"]
    assert check(doc43) == []
    con.add_constraint(doc43, "transform", "follow", bones=["hand_right"], target="head", replace=True)
    assert [c["name"] for c in doc43["constraints"]][2] == "follow"  # keeps its place
    rig.rename_bone(doc43, "head", "skull")
    assert {c["name"]: c for c in doc43["constraints"]}["follow"]["source"] == "skull"
    con.remove_constraint(doc43, "hair")
    assert "hair" not in {c["name"] for c in doc43["constraints"]} and check(doc43) == []


def test_sliders(doc43):
    with pytest.raises(EditError, match="give animation"):
        con.add_constraint(doc43, "slider", "blink")
    add_animation(doc43, "blink", timelines={"bones": {"head": {"scale": [{"y": 1}, {"time": 0.2, "y": 0.1}]}}})
    con.add_constraint(doc43, "slider", "blink_ctl", animation="blink", time=0.5)
    con.add_constraint(doc43, "slider", "nod", bone="torso", animation="blink", property="rotate", to=1, scale=0.1)
    add_animation(doc43, "idle", timelines={"slider": {"blink_ctl": {"time": [{"value": 0, "ease": "inout"},
                                                                              {"time": 1, "value": 1}]}}})
    assert check(doc43) == []
    assert len(doc43["animations"]["idle"]["slider"]["blink_ctl"]["time"][0]["curve"]) == 4
    with pytest.raises(EditError, match="needs property"):
        con.add_constraint(doc43, "slider", "bad", bone="torso", animation="blink")


def test_42_refuses_43_only_options(doc):
    for kind, kwargs in (("slider", {"animation": "x"}), ("ik", {"scaleY": "volume"}),
                         ("transform", {"clamp": True}), ("physics", {"scaleY": "uniform", "rotate": 1})):
        bones = {"bones": ["arm_left"], "target": "hand_left"} if kind in ("ik", "transform") else {"bone": "head"}
        with pytest.raises(EditError, match="needs format version 4.3"):
            con.add_constraint(doc, kind, "x", **(bones if kind != "slider" else {}), **kwargs)
    con.add_constraint(doc, "ik", "ok", bones=["arm_left"], target="hand_left", scaleY="uniform")
    assert doc["ik"][0]["uniform"] is True and check(doc) == []


@pytest.fixture
def doc(imported):
    return d.load(imported.project_path)


def test_animation_defaults_follow_the_version(doc, doc43):
    for target, version in ((doc, "4.2"), (doc43, "4.3")):
        con.add_constraint(target, "transform", "t", bones=["hand_right"], target="hand_left")
        add_animation(target, "a", timelines={"transform": {"t": [{"mixScaleX": 0, "ease": "inout"},
                                                                  {"time": 1, "mixScaleX": 1}]}})
        curve = target["animations"]["a"]["transform"]["t"][0]["curve"]
        # mixScaleY's channel: 4.2 reads it as mixScaleX (0 -> 1), 4.3 as 1 (1 -> 1)
        assert curve[17] == (0.0 if version == "4.2" else 1.0), version


# ── per-version releases ────────────────────────────────────────────────────

def test_preview_uses_the_files_player(imported43):
    page = preview_html(imported43.project_path).read_text()
    assert "spine-player@4.3.13" in page and "premultipliedAlpha" not in page


@needs_node
def test_runtime_check_uses_the_files_runtime(imported43):
    path = str(imported43.project_path)
    server.add_animation(path, "idle", preset="idle")
    result = runtime_check(path)
    assert result["ran"] and result["ok"], result
    assert result["runtime"] == "spine-core 4.3.13" and result["version"].startswith("4.3.")


def test_editor_choice(monkeypatch, tmp_path):
    (tmp_path / "updates").mkdir()
    for name in ("4.2.40", "4.2.43", "4.3.26", "4.3.39-beta"):
        (tmp_path / "updates" / name).write_bytes(b"")
    monkeypatch.setattr(spine_cli, "_UPDATES", (tmp_path / "updates",))
    assert spine_cli.installed_editors() == ["4.2.40", "4.2.43", "4.3.26"]
    assert spine_cli.editor_for("4.3") == "4.3.26" and spine_cli.editor_for("4.2") == "4.2.43"
    assert spine_cli.editor_for(None) == "4.3.26"
    monkeypatch.setenv("SPINE_EDITOR_VERSION", "4.2.40")
    assert spine_cli.editor_for("4.2") == "4.2.40" and spine_cli.editor_for("4.3") == "4.3.26"
    with pytest.raises(spine_cli.SpineCliError, match="imports only its own format version"):
        spine_cli.editor_for("4.3", "4.2.43")
    monkeypatch.setattr(spine_cli, "_UPDATES", (tmp_path / "none",))
    monkeypatch.delenv("SPINE_EDITOR_VERSION")
    assert spine_cli.editor_for("4.3") == "4.3.26"  # the tested release; the launcher fetches it


def test_tools_keep_their_signatures():
    tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
    assert "convert_skeleton" in tools
    assert "allow_version_mismatch" in tools["add_constraint"].parameters["properties"]
    assert "format_version" in tools["import_psd_to_spine"].parameters["properties"]
    assert json.dumps(tools["convert_skeleton"].parameters)  # a plain JSON schema


# ── review fixes ────────────────────────────────────────────────────────────

def test_42_orders_stay_0_to_n_minus_1(doc):
    for name, bone in (("a", "head"), ("b", "torso"), ("c", "chest")):
        con.add_constraint(doc, "physics", name, bone=bone, x=1)
    con.remove_constraint(doc, "b")
    con.add_constraint(doc, "physics", "d", bone="neck", x=1)
    con.add_constraint(doc, "physics", "first", bone="hip", x=1, order=0)
    orders = {c["name"]: c["order"] for _, c in d.all_constraints(doc)}
    assert orders == {"first": 0, "a": 1, "c": 2, "d": 3}
    con.add_constraint(doc, "physics", "c", bone="chest", x=0.5, replace=True)
    assert {c["name"]: c["order"] for _, c in d.all_constraints(doc)}["c"] == 2
    assert d.never_applied(doc) == [] and check(doc) == []


def test_editing_renumbers_broken_42_orders_and_says_so(doc):
    doc["physics"] = [{"name": "lost", "order": 7, "bone": "head", "x": 1}]
    detail = con.add_constraint(doc, "physics", "new", bone="torso", x=1)
    assert "lost never ran before" in detail["note"] and d.never_applied(doc) == []


@needs_node
def test_converting_in_place_verifies_against_the_original(game):
    path = _copy_rig(game(declared="4.3") / "spine")
    result = server.convert_skeleton(str(path), out_path=str(path))
    assert d.load(path)["skeleton"]["spine"].startswith("4.3.")
    assert result["verify"]["ok"] and result["verify"]["runtimes"] == ["spine-core 4.2.120", "spine-core 4.3.13"]


def test_converted_copies_share_the_atlas_unless_they_have_their_own(tmp_path):
    from spine2d_mcp.atlas import default_atlas

    (tmp_path / "hero.atlas").write_text("")
    assert default_atlas(tmp_path / "hero-4.3.json") == tmp_path / "hero.atlas"
    assert default_atlas(tmp_path / "boss-1.5.json") == tmp_path / "boss-1.5.atlas"  # not a Spine version
    (tmp_path / "hero-4.3.atlas").write_text("")
    assert default_atlas(tmp_path / "hero-4.3.json") == tmp_path / "hero-4.3.atlas"


def test_convert_asks_its_own_question_on_mismatch(game):
    path = _copy_rig(game(declared="4.2") / "spine")
    asked = server.convert_skeleton(str(path), "4.3", verify=False)
    assert asked["needs_answer"] and asked["options"][0]["answer"] == "Keep rig.json in 4.2, the game project's version"
    assert asked["options"][1]["call_again_with"] == {"allow_version_mismatch": True}


def test_mismatch_question_names_the_file_being_edited(game):
    path = _copy_rig(game("old", declared="4.2") / "spine")
    elsewhere = game("new", declared="4.3") / "out.json"
    asked = server.remove_animation(str(path), "move", out_path=str(elsewhere))
    assert asked["question"].startswith("rig.json is a Spine 4.2 file")
    assert str(path) in asked["options"][0]["instead"] and not elsewhere.exists()


def test_converted_names(game, tmp_path):
    root = game(declared="4.3")
    named = _copy_rig(root / "a", "hero-4.2")
    assert Path(server.convert_skeleton(str(named), verify=False)["project_path"]).name == "hero-4.3.json"
    other = _copy_rig(root / "b", "boss-1.5")
    assert Path(server.convert_skeleton(str(other), verify=False)["project_path"]).name == "boss-1.5-4.3.json"


def test_editing_in_an_unclear_project_asks_once(game):
    root = game(package={"@esotericsoftware/spine-phaser-v4": "^4.2.0"})  # nothing installed: 4.2 or 4.3?
    path = _copy_rig(root / "spine")
    original = path.read_text()
    asked = server.remove_animation(str(path), "move")
    assert asked["needs_answer"] and "does this game project use" in asked["question"]
    assert path.read_text() == original
    answers = [o["answer"] for o in asked["options"]]
    assert answers[0].startswith("4.2, like rig.json") and answers[1].startswith("4.3: convert rig.json")
    assert "save_project_version=True" in asked["options"][1]["instead"]
    done = server.remove_animation(str(path), "move", **asked["options"][0]["call_again_with"])
    assert done["errors"] == [] and "saved format version 4.2" in done["notes"][0]
    assert (root / ".spine-version").read_text() == "4.2\n"
    again = server.add_animation(str(path), "nod", timelines={"bones": {"driver": {"rotate": [{"value": 0}]}}})
    assert "needs_answer" not in again and again["errors"] == []  # settled: not asked again


def test_saving_a_contradicting_version_is_refused(game):
    path = _copy_rig(game(package=PHASER_42, declared="4.3") / "spine")  # package says 4.2: unclear
    asked = server.remove_animation(str(path), "move")
    assert asked["needs_answer"] and asked["recommended"].startswith("4.2, like rig.json")
    path43 = Path(server.convert_skeleton(str(path), "4.3", allow_version_mismatch=True, verify=False)["project_path"])
    with pytest.raises(ValueError, match="package.json says 4.2"):
        server.remove_animation(str(path43), "move", save_project_version=True)


def test_a_missing_editor_is_reported(monkeypatch, tmp_path):
    (tmp_path / "updates").mkdir()
    (tmp_path / "updates" / "4.2.43").write_bytes(b"")
    monkeypatch.setattr(spine_cli, "_UPDATES", (tmp_path / "updates",))
    with pytest.raises(spine_cli.SpineCliError, match="download 4.3.26 in the Spine launcher"):
        spine_cli.editor_for("4.3")
    assert spine_cli.header_release("4.3") == "4.3.26"  # a new file can still name the tested release


@needs_spine
@pytest.mark.parametrize("make", ["imported43", "converted"])
def test_editor_round_trip(make, imported43, game, tmp_path, request):
    if make == "imported43":
        path = imported43.project_path
        server.add_animation(str(path), "idle", preset="idle")
        server.edit_rig(str(path), [{"op": "set_bone", "name": "head", "length": 60}])
        server.add_constraint(str(path), "physics", "hair", bone="head", preset="hair")
    else:
        source = _copy_rig(game(declared="4.3") / "spine")
        path = Path(server.convert_skeleton(str(source), verify=False)["project_path"])
    made = server.spine_import(str(path))
    assert made["ok"] and made["format_version"] == "4.3" and made["editor"].startswith("4.3."), made
    exported = server.spine_export(made["spine_project"], str(tmp_path / "back"), "json", format_version="4.3")
    assert exported["ok"] and exported["format_version"] == "4.3", exported
    back = next(Path(f) for f in exported["files"] if f.endswith(".json"))
    assert check(d.load(back)) == [] and versions.of(d.load(back)) == "4.3"
