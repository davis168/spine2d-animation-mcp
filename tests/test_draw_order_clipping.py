"""Draw order keys (the whole skeleton's, and 4.3 draw order folders'), slot
folders and clipping attachments, down to what the runtimes and the editor
make of them."""

import itertools
import json
import random
import shutil
from pathlib import Path

import pytest

from spine2d_mcp import server, spine_cli
from spine2d_mcp.edits import EditError
from spine2d_mcp.ops import attachments as att
from spine2d_mcp.ops import rig
from spine2d_mcp.ops.animation import (add_animation, draw_order_from_offsets, draw_order_offsets,
                                       set_draw_order_keys)
from spine2d_mcp.runtime import _run, compare_poses
from spine2d_mcp.spine import document as d
from spine2d_mcp.spine.check import check
from spine2d_mcp.spine.convert import convert
from spine2d_mcp.spine.inspect import inspect

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
needs_spine = pytest.mark.skipif(spine_cli.find_spine() is None, reason="Spine editor not installed")
SETUP = ["head", "torso", "arm_left", "arm_right", "hand_left", "hand_right", "leg_left", "leg_right",
         "foot_left", "foot_right"]
ARMS = ["arms/arm_left", "arms/arm_right", "arms/hand_left", "arms/hand_right"]


@pytest.fixture
def doc(imported):
    return d.load(imported.project_path)


@pytest.fixture
def doc43(imported43):
    return d.load(imported43.project_path)


def _errors(doc):
    return [str(f) for f in check(doc) if f.level == "error"]


def _warnings(doc):
    return [str(f) for f in check(doc) if f.level == "warning"]


def _arms_folder(doc):
    """Put the arm and hand slots (draw positions 2-5) in draw order folder 'arms'."""
    rig.edit_rig(doc, [{"op": "rename_slot", "name": n.split("/")[1], "to": n} for n in ARMS])


def _names(doc):
    return [s["name"] for s in d.slots(doc)]


# ── offsets and draw order folders ────────────────────────────────────────────────

def test_offsets_give_back_every_order():
    base = ["a", "b", "c", "d"]
    for order in itertools.permutations(base):
        assert draw_order_from_offsets(base, draw_order_offsets(base, list(order))) == list(order)
    rng = random.Random(7)
    base = [f"s{i}" for i in range(9)]
    for _ in range(200):
        order = rng.sample(base, len(base))
        assert draw_order_from_offsets(base, draw_order_offsets(base, order)) == order
    assert draw_order_offsets(base, base) == []
    with pytest.raises(EditError, match="missing s8"):
        draw_order_offsets(base, base[:-1])


def test_slot_folders_come_from_names():
    names = ["a", "face/eye", "face/brow/l", "face/brow/r", "b", "hat/top"]
    assert d.draw_order_folders(names) == {"face": ["face/eye", "face/brow/l", "face/brow/r"],
                                     "face/brow": ["face/brow/l", "face/brow/r"], "hat": ["hat/top"]}
    assert d.folder_of(names, ["face/brow/r", "face/brow/l"]) == "face/brow"
    assert d.folder_of(names, ["face/eye"]) is None and d.split_folders(names) == []
    assert d.split_folders(["face/a", "b", "face/c"]) == ["face"]
    # nested folders holding the same slots: the outer one (what the editor matches)
    assert d.folder_of(["g/s/a", "g/s/b"], ["g/s/a", "g/s/b"]) == "g"


def test_order_shorthand_for_the_whole_draw_order(doc):
    wanted = ["torso", "head"] + SETUP[2:]
    add_animation(doc, "turn", timelines={"drawOrder": [{"time": 0.2, "order": wanted}, {"time": 0.6}]})
    keys = doc["animations"]["turn"]["drawOrder"]
    assert keys[0]["offsets"] == [{"slot": "head", "offset": 1}, {"slot": "torso", "offset": -1}]
    assert "offsets" not in keys[1] and check(doc) == []


# ── renaming slots and keeping folders together ─────────────────────────────

def test_rename_slot_updates_every_reference(doc43):
    rig.add_slot(doc43, "mask", "torso", index=1)
    att.add_clipping(doc43, "mask", [[0, 0], [10, 0], [0, 10]], end="torso")
    add_animation(doc43, "a", timelines={
        "slots": {"torso": {"rgba": [{"color": "ffffffff"}]}},
        "attachments": {"default": {"mask": {"clip": {"deform": [{"vertices": [1, 1]}]}}}},
        "drawOrder": [{"time": 0.1, "order": ["torso", "head", "mask"] + SETUP[2:]}]})
    done = rig.edit_rig(doc43, [{"op": "rename_slot", "name": "torso", "to": "body/torso"},
                                {"op": "rename_slot", "name": "mask", "to": "body/mask"}])
    assert "game code" in done[0]["note"]
    assert _names(doc43)[:3] == ["head", "body/mask", "body/torso"]
    skin = d.find_skin(doc43, "default")["attachments"]
    assert "torso" not in skin and skin["body/mask"]["clip"]["end"] == "body/torso"
    anim = doc43["animations"]["a"]
    assert list(anim["slots"]) == ["body/torso"] and list(anim["attachments"]["default"]) == ["body/mask"]
    assert draw_order_from_offsets(_names(doc43), anim["drawOrder"][0]["offsets"])[:3] == \
        ["body/torso", "head", "body/mask"]
    assert check(doc43) == []
    for bad in ("head", "a//b", "/x", ""):
        with pytest.raises(EditError):
            rig.rename_slot(doc43, "arm_left", bad)


def test_folders_stay_together(doc43):
    rig.rename_slot(doc43, "head", "face/head")
    with pytest.raises(EditError, match="'face' .* no longer be together"):
        rig.rename_slot(doc43, "arm_left", "face/arm_left")  # torso is drawn between them
    rig.rename_slot(doc43, "torso", "face/torso")
    with pytest.raises(EditError, match="no longer be together"):
        rig.set_draw_order(doc43, slot="face/head", index=5)
    with pytest.raises(EditError, match="no longer be together"):
        rig.add_slot(doc43, "mask", "torso", index=1)  # between face/head and face/torso
    rig.add_slot(doc43, "face/mask", "torso", index=1)
    rig.set_draw_order(doc43, slot="face/head", index=2)  # moving within the folder is fine
    assert _names(doc43)[:3] == ["face/mask", "face/torso", "face/head"] and check(doc43) == []
    doc43["slots"].insert(4, doc43["slots"].pop(0))  # hand-made split
    assert any("draw order folder 'face'" in w and "not together" in w for w in _warnings(doc43))


# ── folder keys ─────────────────────────────────────────────────────────────

def test_folder_keys(doc43):
    _arms_folder(doc43)
    add_animation(doc43, "wave", preset="wave")
    wanted = ["arms/hand_left", "arms/arm_left", "arms/arm_right", "arms/hand_right"]
    result = set_draw_order_keys(doc43, "wave", [{"time": 0.5, "order": wanted}, {"time": 1}], folder="arms")
    entry, = doc43["animations"]["wave"]["drawOrderFolder"]
    assert entry["slots"] == ARMS == result["slots"] and "note" not in result
    assert draw_order_from_offsets(ARMS, entry["keys"][0]["offsets"]) == wanted and check(doc43) == []
    set_draw_order_keys(doc43, "wave", [{"time": 0.2, "order": list(reversed(ARMS))}], folder="arms")
    assert len(doc43["animations"]["wave"]["drawOrderFolder"]) == 1  # replaced, not added
    assert len(doc43["animations"]["wave"]["drawOrderFolder"][0]["keys"]) == 1
    with pytest.raises(EditError, match="each of these slots once"):
        set_draw_order_keys(doc43, "wave", [{"order": ARMS[:2]}], folder="arms")
    with pytest.raises(EditError, match="no draw order folder 'legs' \\(folders: arms\\)"):
        set_draw_order_keys(doc43, "wave", [{"time": 0}], folder="legs")
    rig.rename_slot(doc43, "head", "face/head")
    with pytest.raises(EditError, match="holds one slot"):
        set_draw_order_keys(doc43, "wave", [{"time": 0}], folder="face")
    set_draw_order_keys(doc43, "wave", [], folder="arms")
    assert "drawOrderFolder" not in doc43["animations"]["wave"]


def test_nested_folder_keys_carry_a_note(doc43):
    hands = ["arms/hands/hand_left", "arms/hands/hand_right"]
    rig.edit_rig(doc43, [{"op": "rename_slot", "name": n.split("/")[-1], "to": n} for n in hands])
    add_animation(doc43, "a", timelines={"bones": {"head": {"rotate": [{"value": 0}]}}})
    # arms holds the same slots as arms/hands, so these keys key arms, which the editor keeps
    result = set_draw_order_keys(doc43, "a", [{"time": 0.1, "order": hands[::-1]}], folder="arms/hands")
    assert "note" not in result and _warnings(doc43) == []
    rig.rename_slot(doc43, "arm_right", "arms/arm_right")
    result = set_draw_order_keys(doc43, "a", [{"time": 0.1, "order": hands[::-1]}], folder="arms/hands")
    assert "nested folder" in result["note"]
    assert any("keys nested folder 'arms/hands'" in w for w in _warnings(doc43)) and _errors(doc43) == []


def test_add_animation_folder_shorthands(doc, doc43):
    _arms_folder(doc43)
    wanted = list(reversed(ARMS))
    add_animation(doc43, "a", timelines={"drawOrderFolder": {"arms": [{"time": 0.3, "order": wanted}]}})
    add_animation(doc43, "b", timelines={"drawOrderFolder": [{"folder": "arms", "keys": [{"time": 0.3, "order": wanted}]}]})
    add_animation(doc43, "c", timelines={"drawOrderFolder": [{"slots": list(reversed(ARMS)), "keys": [
        {"time": 0.3, "order": wanted}]}]})  # the folder's slots, any order
    a, b, c = (doc43["animations"][n]["drawOrderFolder"] for n in "abc")
    assert a == b == c and a[0]["slots"] == ARMS
    assert check(doc43) == []
    with pytest.raises(EditError, match="not the slots of one draw order folder"):
        add_animation(doc43, "x", timelines={"drawOrderFolder": [{"slots": ARMS[:2], "keys": [{}]}]})
    with pytest.raises(EditError, match="keyed twice"):
        add_animation(doc43, "x", timelines={"drawOrderFolder": [{"folder": "arms", "keys": [{}]},
                                                                 {"folder": "arms", "keys": [{}]}]})
    with pytest.raises(EditError, match="need format version 4.3"):
        add_animation(doc, "x", timelines={"drawOrderFolder": {"arms": [{}]}})


def test_checker_on_folder_keys(doc43):
    _arms_folder(doc43)
    anims = doc43["animations"] = {}
    anims["map"] = {"drawOrderFolder": {"arms": {"slots": ARMS, "keys": [{}]}}}
    anims["unsorted"] = {"drawOrderFolder": [{"slots": list(reversed(ARMS)), "keys": [{}]}]}
    anims["partial"] = {"drawOrderFolder": [{"slots": ARMS[:2], "keys": [{}]}]}
    anims["twice"] = {"drawOrderFolder": [{"slots": ARMS, "keys": [{}]}, {"slots": ARMS, "keys": [{}]}]}
    errors, warnings = _errors(doc43), _warnings(doc43)
    assert any("animations.map.drawOrderFolder: must be a list" in e for e in errors)
    assert any("animations.unsorted.drawOrderFolder[0].slots: must list the slots in setup draw order" in e for e in errors)
    assert any("animations.partial.drawOrderFolder[0].slots: not the slots of one draw order folder" in w for w in warnings)
    assert any("animations.twice.drawOrderFolder[1]: folder 'arms' is keyed twice" in w for w in warnings)


def test_draw_order_keys_survive_slot_changes(doc43):
    _arms_folder(doc43)
    folder_order = ["arms/hand_right", "arms/arm_left", "arms/hand_left", "arms/arm_right"]
    whole = ["torso", "head"] + ARMS + SETUP[6:]
    add_animation(doc43, "a", timelines={"drawOrder": [{"time": 0.1, "order": whole}],
                                         "drawOrderFolder": {"arms": [{"time": 0.2, "order": folder_order}]}})
    anim = doc43["animations"]["a"]

    def shown():
        names = _names(doc43)
        entry = anim["drawOrderFolder"][0]
        return (draw_order_from_offsets(names, anim["drawOrder"][0].get("offsets") or []),
                draw_order_from_offsets(entry["slots"], entry["keys"][0].get("offsets") or []))

    rig.set_draw_order(doc43, slot="arms/hand_right", index=2)  # within the folder
    rig.set_draw_order(doc43, slot="head", index=1)
    assert shown() == (whole, folder_order) and check(doc43) == []
    rig.rename_slot(doc43, "arms/hand_left", "arms/left_hand")
    renamed = [s.replace("hand_left", "left_hand") for s in folder_order]
    assert shown()[1] == renamed
    rig.add_slot(doc43, "arms/glove", "torso", index=6)  # joins the folder at its setup place (last)
    assert anim["drawOrderFolder"][0]["slots"][-1] == "arms/glove" and shown()[1] == renamed + ["arms/glove"]
    rig.remove_slot(doc43, "arms/glove")
    assert shown()[1] == renamed and check(doc43) == []
    rig.rename_folder(doc43, "arms", "limbs")
    assert shown()[1] == [s.replace("arms/", "limbs/") for s in renamed] and check(doc43) == []
    done = rig.rename_folder(doc43, "limbs", "")
    assert "drawOrderFolder" not in anim and "folder 'limbs'" in done["notes"][0]
    assert shown is not None and check(doc43) == []


# ── what the runtimes do with them ──────────────────────────────────────────

def _layered(path: str) -> list[str]:
    """A whole draw order key (head to the back) under arms folder keys; the
    order both runtimes should show at 0.5 s."""
    server.edit_rig(path, [{"op": "rename_slot", "name": n.split("/")[1], "to": n} for n in ARMS])
    server.add_animation(path, "swap", timelines={
        "drawOrder": [{"time": 0.25, "order": SETUP[1:2] + ARMS + SETUP[6:] + SETUP[:1]}],
        "drawOrderFolder": {"arms": [{"time": 0.5, "order": [ARMS[2], ARMS[0], ARMS[1], ARMS[3]]}]}})
    # the folder refills the positions its slots hold, in the keyed order
    return ["torso", ARMS[2], ARMS[0], ARMS[1], ARMS[3]] + SETUP[6:] + ["head"]


@needs_node
def test_the_43_runtime_layers_folder_keys_over_the_whole_order(imported43):
    path = str(imported43.project_path)
    expected = _layered(path)
    run = _run(Path(path), imported43.atlas_path, poses=True)
    assert run["ran"] and not run["errors"], run
    orders = run["draw_orders"]["swap"]
    assert orders[0] == SETUP[:2] + ARMS + SETUP[6:] and orders[30] == expected


@needs_node
def test_converting_to_42_folds_folder_keys_and_keeps_the_orders(imported43, tmp_path):
    path = str(imported43.project_path)
    expected = _layered(path)
    down = server.convert_skeleton(path, "4.2", out_path=str(tmp_path / "swap-4.2.json"),
                                   atlas_path=str(imported43.atlas_path))
    assert down["converted"] and down["verify"]["ok"], down  # every frame's draw order compared
    assert any("folded" in c for c in down["changed"])
    back = d.load(down["project_path"])
    assert "drawOrderFolder" not in back["animations"]["swap"]
    assert draw_order_from_offsets(_names(back), back["animations"]["swap"]["drawOrder"][-1]["offsets"]) == expected


@needs_node
def test_the_42_runtime_shows_the_whole_order_and_conversion_keeps_it(imported, tmp_path):
    path = str(imported.project_path)
    wanted = list(reversed(SETUP))
    server.add_animation(path, "flip", timelines={"drawOrder": [{"time": 0.25, "order": wanted}]})
    run = _run(Path(path), imported.atlas_path, poses=True)
    assert run["draw_orders"]["flip"][-1] == wanted
    up = server.convert_skeleton(path, "4.3", out_path=str(tmp_path / "flip-4.3.json"),
                                 atlas_path=str(imported.atlas_path))
    assert up["verify"]["ok"], up["verify"]  # draw orders compared too


def test_inspect_lists_folders(doc43):
    _arms_folder(doc43)
    add_animation(doc43, "a", timelines={"drawOrderFolder": {"arms": [{"time": 0.3, "order": list(reversed(ARMS))}]}})
    seen = inspect(doc43)
    assert seen["draw_order_folders"] == {"arms": ARMS}
    assert next(a for a in seen["animations"] if a["name"] == "a")["folder_keys"] == ["arms"]


# ── clipping ────────────────────────────────────────────────────────────────

def _clip_slot(doc, before="torso"):
    rig.add_slot(doc, "mask", "torso", index=_names(doc).index(before))


def test_clipping_covers_the_slots_after_it(doc43):
    _clip_slot(doc43)
    square = [[-60, 0], [60, 0], [60, 150], [-60, 150]]
    result = att.add_clipping(doc43, "mask", square, end="arm_right", convex=True, inverse=True)
    assert result["clips"] == ["torso", "arm_left", "arm_right"] and result["convex"] and result["inverse"]
    assert result["notes"] == [att.EDITOR_CLIP_NOTE]  # inverse: the editor's import drops it
    clip = d.find_skin(doc43, "default")["attachments"]["mask"]["clip"]
    assert clip["type"] == "clipping" and clip["vertexCount"] == 4 and clip["end"] == "arm_right"
    assert d.slot_map(doc43)["mask"]["attachment"] == "clip" and check(doc43) == []


def test_clip_notes(doc, doc43):
    _clip_slot(doc43)
    notch = [[0, 0], [10, 0], [2, 2], [0, 10]]
    assert "notes" not in att.add_clipping(doc43, "mask", [[0, 0], [10, 0], [0, 10]])
    assert att.add_clipping(doc43, "mask", notch, replace=True)["notes"] == [att.EDITOR_CLIP_NOTE]
    notes = att.add_clipping(doc43, "mask", notch, inverse=True, replace=True)["notes"]
    assert notes[0].startswith("inverse clipping always clips to the polygon's convex hull")
    _clip_slot(doc)
    assert "notes" not in att.add_clipping(doc, "mask", notch)  # 4.2: no flags, nothing the editor drops


def test_import_changes_lists_what_the_43_editor_drops(doc43):
    _clip_slot(doc43)
    att.add_clipping(doc43, "mask", [[0, 0], [10, 0], [0, 10]], inverse=True)
    att.add_clipping(doc43, "mask", [[0, 0], [10, 0], [2, 2], [0, 10]], name="notch", set_as_setup=False)
    att.add_clipping(doc43, "mask", [[0, 0], [10, 0], [0, 10]], name="fine", set_as_setup=False)
    rig.edit_rig(doc43, [{"op": "rename_slot", "name": n, "to": f"arms/hands/{n}"} for n in ("hand_left", "hand_right")]
                 + [{"op": "rename_slot", "name": "arm_right", "to": "arms/arm_right"}])
    add_animation(doc43, "a", timelines={"drawOrderFolder": {"arms/hands": [{"time": 0.1}],
                                                             "arms": [{"time": 0.1}]}})  # arms: kept
    changes = spine_cli.import_changes(doc43)
    assert changes == ["clip default/mask/clip: inverse is dropped",
                       "clip default/mask/notch: turned convex (clips to its hull)",
                       "animation 'a': the draw order keys of nested folder 'arms/hands' are dropped"]
    assert spine_cli.import_changes(convert(doc43, "4.2", allow_lossy=True).doc) == []


def test_world_points_land_where_given(doc43):
    _clip_slot(doc43)
    torso = d.world_transforms(doc43)["torso"]
    att.add_clipping(doc43, "mask", [[torso.x, torso.y], [torso.x + 10, torso.y], [torso.x, torso.y + 10]], world=True)
    v = d.find_skin(doc43, "default")["attachments"]["mask"]["clip"]["vertices"]
    assert v[:4] == [0, 0, 10, 0]


def test_clipping_refusals(doc, doc43):
    _clip_slot(doc43)
    tri = [[0, 0], [10, 0], [0, 10]]
    with pytest.raises(EditError, match="not convex"):
        att.add_clipping(doc43, "mask", [[0, 0], [10, 0], [2, 2], [0, 10]], convex=True)
    with pytest.raises(EditError, match="drawn before or at"):
        att.add_clipping(doc43, "mask", tri, end="head")
    with pytest.raises(EditError, match="at least 3 points"):
        att.add_clipping(doc43, "mask", [[0, 0], [1, 1]])
    with pytest.raises(EditError, match="one attachment at a time"):
        att.add_clipping(doc43, "head", tri)
    with pytest.raises(EditError, match="drawn last"):
        att.add_clipping(doc43, "foot_right", tri, set_as_setup=False)
    att.add_clipping(doc43, "mask", tri)
    with pytest.raises(EditError, match="replace=true"):
        att.add_clipping(doc43, "mask", tri)
    _clip_slot(doc)
    with pytest.raises(EditError, match="need format version 4.3"):
        att.add_clipping(doc, "mask", tri, inverse=True)
    att.add_clipping(doc, "mask", tri)  # plain clipping is 4.2 too
    assert check(doc) == []


def test_checker_warns_about_odd_clips(doc43):
    _clip_slot(doc43)
    d.find_skin(doc43, "default")["attachments"]["mask"] = {
        "clip": {"type": "clipping", "vertexCount": 4, "vertices": [0, 0, 10, 0, 2, 2, 0, 10], "convex": True}}
    assert any("not convex" in w for w in _warnings(doc43))


@needs_node
def test_43_runtime_loads_clips(imported43):
    path = str(imported43.project_path)
    server.edit_rig(path, [{"op": "add_slot", "name": "mask", "bone": "torso", "index": 1}])
    server.add_clipping(path, "mask", [[-60, 0], [60, 0], [60, 150], [-60, 150]], convex=True, inverse=True)
    run = _run(Path(path), imported43.atlas_path, poses=False)
    assert run["ran"] and run["ok"], run


@needs_spine
def test_folders_and_clips_through_the_43_editor(imported43, tmp_path):
    path = str(imported43.project_path)
    server.edit_rig(path, [{"op": "add_slot", "name": "mask", "bone": "torso", "index": 1}])
    server.add_clipping(path, "mask", [[-60, 0], [60, 0], [60, 150], [-60, 150]], end="arm_left", inverse=True)
    server.edit_rig(path, [{"op": "rename_slot", "name": n.split("/")[1], "to": n} for n in ARMS])
    wanted = [ARMS[2], ARMS[0], ARMS[1], ARMS[3]]
    server.add_animation(path, "swap", timelines={"drawOrderFolder": {"arms": [{"time": 0.5, "order": wanted}]}})
    made = server.spine_import(path)
    assert made["ok"], made
    assert made["changed_on_import"] == ["clip default/mask/clip: inverse is dropped"]
    out = server.spine_export(made["spine_project"], str(tmp_path / "back"), "json", format_version="4.3")
    back = json.loads(Path(next(f for f in out["files"] if f.endswith(".json"))).read_text())
    assert [s["name"] for s in back["slots"]] == _names(d.load(path))  # folders kept, nothing pulled together
    clip = next(a for s in back["skins"] for a in (s["attachments"].get("mask") or {}).values())
    assert clip["end"] == "arms/arm_left"
    entry, = back["animations"]["swap"]["drawOrderFolder"]
    assert entry["slots"] == ARMS and draw_order_from_offsets(ARMS, entry["keys"][0]["offsets"]) == wanted
    assert _errors(back) == []


@needs_spine
def test_json_exports_get_back_what_the_43_editor_import_lost(imported43, tmp_path):
    path = str(imported43.project_path)
    server.edit_rig(path, [{"op": "add_slot", "name": "mask", "bone": "torso", "index": 1},
                           {"op": "add_slot", "name": "notch", "bone": "torso", "index": 2}]
                    + [{"op": "rename_slot", "name": n, "to": f"arms/{n}"} for n in ("arm_left", "arm_right")]
                    + [{"op": "rename_slot", "name": n, "to": f"arms/hands/{n}"} for n in ("hand_left", "hand_right")])
    server.add_clipping(path, "mask", [[-60, 0], [60, 0], [60, 150], [-60, 150]], end="torso", inverse=True)
    server.add_clipping(path, "notch", [[0, 0], [40, 0], [10, 10], [0, 40]], end="torso")  # concave, not convex
    hands = ["arms/hands/hand_left", "arms/hands/hand_right"]
    server.add_animation(path, "swap", timelines={"drawOrderFolder": {"arms/hands": [{"time": 0.5, "order": hands[::-1]}]}})
    made = server.spine_import(path)
    assert made["ok"] and len(made["changed_on_import"]) == 3 and "recorded" in made["note"]
    out = server.spine_export(made["spine_project"], str(tmp_path / "back"), "json", format_version="4.3")
    assert len(out["restored"]) == 3, out
    back = json.loads(Path(next(f for f in out["files"] if f.endswith(".json"))).read_text())
    skin = back["skins"][0]["attachments"]
    assert skin["mask"]["clip"].get("inverse") is True and not skin["mask"]["clip"].get("convex")
    assert "convex" not in skin["notch"]["clip"] and "inverse" not in skin["notch"]["clip"]
    entry, = back["animations"]["swap"]["drawOrderFolder"]
    assert entry == d.load(path)["animations"]["swap"]["drawOrderFolder"][0]
    assert _errors(back) == []
    binary = server.spine_export(made["spine_project"], str(tmp_path / "bin"), "binary", format_version="4.3")
    assert any("binary export cannot get back" in w for w in binary["warnings"])
    kept = server.spine_export(made["spine_project"], str(tmp_path / "raw"), "json", format_version="4.3",
                               restore=False)
    assert "restored" not in kept


@needs_node
def test_converting_clips_and_draw_orders_keeps_poses(imported, tmp_path):
    path = str(imported.project_path)
    server.edit_rig(path, [{"op": "add_slot", "name": "mask", "bone": "torso", "index": 1}])
    server.add_clipping(path, "mask", [[-60, 0], [60, 0], [60, 150], [-60, 150]])
    server.add_animation(path, "turn", timelines={"drawOrder": [{"time": 0.3, "order": list(reversed(SETUP[:1] + ["mask"] + SETUP[1:]))}]})
    up = server.convert_skeleton(path, "4.3", out_path=str(tmp_path / "c-4.3.json"), atlas_path=str(imported.atlas_path))
    assert up["converted"] and up["verify"]["ok"], up
    assert compare_poses(path, up["project_path"], imported.atlas_path)["ok"]
