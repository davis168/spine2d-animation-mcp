"""What the Spine 4.3 editor's JSON import loses, recorded at import and put
back in its JSON exports.

Seen with editor 4.3.26 (the newest stable 4.3 release): its JSON import
does not read a clipping attachment's `convex` or `inverse` (every clip comes
in convex and not inverse), and drops draw order keys of a nested draw order
folder or of slots that are not one folder. `spine_import` records what the
skeleton had in `<project>.spine.mcp-restore.json`; a JSON or binary
`spine_export` from that project puts it back where the export still shows
the editor's default, so a change made in the editor wins (binary files are
patched by `runtime.restore_binary`). Rendered frames cannot be.
"""

from __future__ import annotations

import json
from pathlib import Path

from .spine import document as d
from .spine import versions

RECORD_SUFFIX = ".mcp-restore.json"


# Fixes in stable 4.3 editor releases (the Spine changelog) to importing or
# exporting data the tools write: (first fixed release, what was broken, does
# this skeleton have it). Older editors work otherwise; the format is the
# same across 4.3.x.
def _has_deform(doc: dict) -> bool:
    return any(timelines.get("deform") for anim in (doc.get("animations") or {}).values()
               for slots in ((anim or {}).get("attachments") or {}).values() for atts in (slots or {}).values()
               for timelines in (atts or {}).values() if isinstance(timelines, dict))


def _curve_into_last_key(value) -> bool:
    """A Bezier curve on the key before a timeline's last key, anywhere in
    the animations (its second handle belongs to the last key)."""
    if isinstance(value, dict):
        return any(_curve_into_last_key(v) for v in value.values())
    if isinstance(value, list):
        keys = [k for k in value if isinstance(k, dict)]
        return (len(keys) >= 2 and isinstance(keys[-2].get("curve"), list)) or \
            any(_curve_into_last_key(k) for k in keys)
    return False


EDITOR_FIXES = (
    ("4.3.05", "keeping a Bezier curve's handle into a timeline's last key (4.3.04 loses it and exports garbage)",
     lambda doc: _curve_into_last_key(doc.get("animations"))),
    ("4.3.08", "importing bone-driven sliders",
     lambda doc: any(c.get("type") == "slider" and c.get("bone") for c in doc.get("constraints") or [])),
    ("4.3.14", "CLI data import (it could crash)", lambda doc: True),
    ("4.3.18", "importing animations with deform keys", _has_deform),
    ("4.3.24", "exporting transform constraints' mixY and mixScaleY",
     lambda doc: any(c.get("type") == "transform" for c in doc.get("constraints") or [])),
    ("4.3.26", "loading projects with deform keys", _has_deform),
)


def _release(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split("-")[0].split(".")[:3])


def editor_caveats(editor: str | None, doc: dict) -> list[str]:
    """Known editor bugs a 4.3 editor older than the fix has for this skeleton."""
    if not editor or versions.major_minor(editor) != "4.3" or versions.layout(doc) != "4.3":
        return []
    if "beta" in editor:
        return [f"Spine {editor} is a beta: the 4.3 betas came before 4.3.00 and lack later fixes; use a "
                "stable 4.3 release"]
    out = [f"Spine {editor} predates the {fixed} fix for {what}" for fixed, what, has in EDITOR_FIXES
           if _release(editor) < _release(fixed) and has(doc)]
    return out + ([f"use Spine {EDITOR_FIXES[-1][0]} or newer"] if out else [])


def record_path(spine_project: str | Path) -> Path:
    spine_project = Path(spine_project)
    return spine_project.with_name(spine_project.name + RECORD_SUFFIX)


def _clips(doc: dict):
    for skin in d.skins(doc):
        for slot, atts in (skin.get("attachments") or {}).items():
            for name, att in (atts or {}).items():
                if isinstance(att, dict) and att.get("type") == "clipping":
                    yield skin.get("name"), slot, name, att


def dropped(doc: dict) -> dict:
    """What the editor's import will lose: clips that are not convex or are
    inverse, and draw order folder keys it cannot place."""
    if versions.layout(doc) == "4.2":
        return {}
    clips = [[skin, slot, name, {"convex": bool(att.get("convex")), "inverse": bool(att.get("inverse"))}]
             for skin, slot, name, att in _clips(doc) if not att.get("convex") or att.get("inverse")]
    names = [s["name"] for s in d.slots(doc)]
    folder_keys = {}
    for anim_name, anim in (doc.get("animations") or {}).items():
        entries = (anim or {}).get("drawOrderFolder")
        lost = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            folder = d.folder_of(names, entry.get("slots"))
            if folder is None or "/" in folder:  # not a top-level folder's keys
                lost.append(entry)
        if lost:
            folder_keys[anim_name] = lost
    return {k: v for k, v in (("clips", clips), ("folder_keys", folder_keys)) if v}


def save_record(spine_project: str | Path, skeleton: str, source: str | Path, doc: dict) -> Path | None:
    """Record what the import loses next to the project (or clear an old record)."""
    path = record_path(spine_project)
    lost = dropped(doc)
    if not lost:
        path.unlink(missing_ok=True)
        return None
    path.write_text(json.dumps({"skeleton": skeleton, "source": str(source), **lost}, indent=2), encoding="utf-8")
    return path


def restore(json_path: str | Path, record: dict) -> list[str]:
    """Put back what the record says the import lost, where the export still
    shows the editor's default. Returns what was restored."""
    json_path = Path(json_path)
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    done = []
    for skin_name, slot, name, flags in record.get("clips") or []:
        att = ((((d.find_skin(doc, skin_name) or {}).get("attachments") or {}).get(slot)) or {}).get(name)
        if not isinstance(att, dict) or att.get("type") != "clipping":
            continue
        if not att.get("convex") or att.get("inverse"):
            continue  # changed in the editor: keep that
        att.pop("convex", None)
        att.pop("inverse", None)
        att.update({k: True for k, v in flags.items() if v})
        done.append(f"clip {skin_name}/{slot}/{name}: " + (", ".join(k for k, v in flags.items() if v) or
                                                            "not convex"))
    slot_names = {s["name"] for s in d.slots(doc)}
    for anim_name, entries in (record.get("folder_keys") or {}).items():
        anim = (doc.get("animations") or {}).get(anim_name)
        if not isinstance(anim, dict):
            continue
        have = [set(e.get("slots") or []) for e in anim.get("drawOrderFolder") or [] if isinstance(e, dict)]
        for entry in entries:
            if not set(entry["slots"]) <= slot_names or set(entry["slots"]) in have:
                continue
            anim.setdefault("drawOrderFolder", []).append(entry)
            done.append(f"animation {anim_name!r}: draw order keys of {', '.join(entry['slots'])}")
    if done:
        json_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return done
