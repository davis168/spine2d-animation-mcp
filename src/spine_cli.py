"""Drive the Spine editor's command line interface (the editor has no live
scripting API; files plus the CLI are how it is automated).

The executable comes from `$SPINE_EXECUTABLE`, else the standard install
locations. An editor imports skeleton JSON of its own format version only,
and exports data in it, so the editor (passed to the launcher as `-u`) is
chosen per file: the `editor_version` argument, else a release of that
format version listed in `$SPINE_EDITOR_VERSION` (one or several,
comma-separated, e.g. "4.2.43,4.3.26"), else the newest stable release of it
the launcher has downloaded (betas are skipped), else the release the tools
are tested with (the launcher downloads it).
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import editor_quirks, runtime
from .spine import document as d
from .spine import versions

_CANDIDATES = (
    "/Applications/Spine.app/Contents/MacOS/Spine",
    r"C:\Program Files\Spine\Spine.com",
    r"C:\Program Files (x86)\Spine\Spine.com",
)
# Where the Spine launcher keeps the editor releases it has downloaded.
_UPDATES = (
    Path.home() / "Library" / "Application Support" / "Spine" / "updates",  # macOS
    Path.home() / "Spine" / "updates",  # Windows, Linux
)
_STABLE = re.compile(r"^\d+\.\d+\.\d+$")
_FRAME = re.compile(r"^(?P<prefix>.+)_(?P<index>\d+)\.png$")


class SpineCliError(RuntimeError):
    """The Spine CLI is missing or reported an error."""


@dataclass
class SpineRun:
    ok: bool
    editor: str | None
    lines: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        return [ln for ln in self.lines if ln.startswith("ERROR") or ln.startswith("[error]")]

    @property
    def warnings(self) -> list[str]:
        return [ln for ln in self.lines if ln.startswith("WARNING")]


def find_spine() -> str | None:
    env = os.environ.get("SPINE_EXECUTABLE")
    if env:
        return env if Path(env).exists() else None
    for candidate in _CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return shutil.which("Spine") or shutil.which("spine")


def _release_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def installed_editors() -> list[str]:
    """Stable editor releases the launcher has downloaded, oldest first."""
    found: set[str] = set()
    for folder in _UPDATES:
        if folder.is_dir():
            found.update(p.name for p in folder.iterdir() if _STABLE.match(p.name))
    return sorted(found, key=_release_key)


def _chosen(format_version: str | None) -> str | None:
    """A release in $SPINE_EDITOR_VERSION or installed for the format version
    (None: any supported version, newest first)."""
    wanted = [format_version] if format_version else list(reversed(versions.SUPPORTED))
    listed = [v.strip() for v in os.environ.get("SPINE_EDITOR_VERSION", "").split(",") if v.strip()]
    installed = installed_editors()
    for version in wanted:
        for release in listed:
            if versions.major_minor(release) == version:
                return release
        matching = [r for r in installed if versions.major_minor(r) == version]
        if matching:
            return matching[-1]
    return None


def header_release(format_version: str) -> str:
    """The editor release a new file of this format version names in its
    header: the one that would import it, else the tested release."""
    return _chosen(format_version) or versions.get(format_version).editor


def editor_for(format_version: str | None, editor_version: str | None = None) -> str | None:
    """The editor release to run for skeleton data of `format_version`.
    Refuses when the launcher has no stable release of it."""
    if editor_version:
        if format_version and versions.major_minor(editor_version) != format_version:
            raise SpineCliError(
                f"editor {editor_version} reads and writes format {versions.major_minor(editor_version)}, but this "
                f"is {format_version} data: the Spine editor imports only its own format version (use a "
                f"{format_version}.x editor, e.g. {versions.get(format_version).editor})")
        return editor_version
    release = _chosen(format_version)
    if release or format_version is None:
        return release
    if any(folder.is_dir() for folder in _UPDATES):
        raise SpineCliError(
            f"no stable Spine {format_version} editor is installed: download {versions.get(format_version).editor} "
            "in the Spine launcher (or name one in SPINE_EDITOR_VERSION)")
    return versions.get(format_version).editor  # the launcher's folder is elsewhere: let it fetch the release


def json_version(path: str | Path) -> str:
    """The format version of a skeleton JSON file, if supported."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SpineCliError(f"cannot read {path}: {e}") from None
    version = versions.of(doc) if isinstance(doc, dict) else None
    if not versions.is_supported(version):
        raise SpineCliError(f"{Path(path).name}: format version {version!r} is not supported "
                            f"(supported: {versions.supported_text()})")
    return version


def run_spine(args: list[str], editor_version: str | None = None, timeout: int = 900) -> SpineRun:
    exe = find_spine()
    if exe is None:
        raise SpineCliError("Spine not found: install it or set SPINE_EXECUTABLE to the Spine executable")
    cmd = [exe, *(["-u", editor_version] if editor_version else []), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    lines = [ln.rstrip() for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip()]
    editor = next((m.group(1) for ln in lines if (m := re.match(r"Starting: Spine (\S+)", ln))), None)
    run = SpineRun(ok=proc.returncode == 0, editor=editor, lines=lines)
    run.ok = run.ok and not run.errors
    return run


def _result(run: SpineRun, **extra) -> dict:
    out = {"ok": run.ok, "editor": run.editor, **extra}
    if run.errors:
        out["errors"] = run.errors
    if run.warnings:
        out["warnings"] = run.warnings
    if not run.ok:
        out["output"] = run.lines[-15:]
    return out


def project_info(path: str | Path, editor_version: str | None = None) -> dict:
    """Skeleton names, bone/slot counts and animations the CLI reports."""
    run = run_spine(["-i", str(path)], editor_version)
    info: dict = {"skeletons": []}
    for ln in run.lines:
        if m := re.match(r"\s*Skeleton: (.+)$", ln):
            info["skeletons"].append(m.group(1).strip())
        elif m := re.match(r"\s*(Bones|Slots|Animations) \((\d+)\): (.*)$", ln):
            info.setdefault(m.group(1).lower(), [n.strip() for n in m.group(3).split(",")])
    return {**_result(run), **info}


def spine_import(
    project_path: str | Path,
    spine_project: str | Path | None = None,
    skeleton_name: str | None = None,
    editor_version: str | None = None,
    replace: bool = False,
) -> dict:
    project_path = Path(project_path)
    version = json_version(project_path)
    target = Path(spine_project) if spine_project else project_path.with_suffix(".spine")
    name = skeleton_name or project_path.stem
    doc = json.loads(project_path.read_text(encoding="utf-8"))
    clashes = d.default_skin_clashes(doc)
    if clashes:
        raise SpineCliError(
            "the Spine editor refuses a skin attachment under a name the default skin also has in that slot ("
            + ", ".join("/".join(c) for c in clashes) + "): move it out of the default skin with edit_skin "
            "move_to_skins, or use another name in the skin")
    editor = editor_for(version, editor_version)
    output = target
    if target.exists():
        # The editor adds a skeleton whose name the project already holds as "<name>2".
        held = project_info(target, editor).get("skeletons") or []
        if name in held:
            if not replace:
                raise SpineCliError(
                    f"{target.name} already holds skeleton {name!r}, and the editor would add {name + '2'!r} beside "
                    "it: pass replace=true to replace the project, or give another spine_project or skeleton_name")
            if set(held) != {name}:
                raise SpineCliError(
                    f"{target.name} also holds {', '.join(sorted(set(held) - {name}))}, and the editor's CLI cannot "
                    "replace one skeleton of several: import into another .spine")
            output = target.with_name(f".{target.stem}.replacing.spine")
    given, header = dict(doc), (doc.get("skeleton") or {}).get("spine")
    if editor and header != editor and versions.major_minor(header) == versions.major_minor(editor):
        # An editor warns unless the header names its own release; the data
        # is the same across a format version's releases.
        given["skeleton"] = {**doc["skeleton"], "spine": editor}
    animations = doc.get("animations") or {}
    if len(animations) > 1 and d.constraints(doc, "slider"):
        # The 4.3 editor keeps a slider's animation as an index, then sorts
        # the animations by name, so give it them in its own order.
        order = editor_animation_order(list(animations), editor)
        if order != list(animations):
            given["animations"] = {name: animations[name] for name in order}
    from .ops.meshes import order_influences

    ordered = copy.deepcopy(given)
    reordered = order_influences(ordered)
    if reordered:
        # The editor refuses weighted vertices that list bones in different
        # orders; the runtimes read any order, so give it bone order.
        given = ordered
    source = project_path
    if given["skeleton"] is not doc.get("skeleton") or given.get("animations") is not doc.get("animations"):
        # import a copy beside the original, so its images path still resolves
        source = project_path.with_name(f".{project_path.stem}.import-{editor}.json")
        source.write_text(json.dumps(given), encoding="utf-8")
    try:
        run = run_spine(["-i", str(source), "-o", str(output), "-r", name], editor)
        if output != target:
            if run.ok and output.is_file():
                os.replace(output, target)
            else:
                output.unlink(missing_ok=True)
    finally:
        if source != project_path:
            source.unlink(missing_ok=True)
    result = _result(run, spine_project=str(target), format_version=version)
    if output != target and run.ok:
        result["replaced"] = True
    if reordered:
        result.setdefault("warnings", []).append(
            f"gave the editor {', '.join(reordered)} with each vertex's bones in bone order, since it refuses "
            "weighted vertices that list bones in different orders (the same mesh; the JSON is unchanged)")
    caveats = editor_quirks.editor_caveats(run.editor or editor, doc)
    if caveats:
        result.setdefault("warnings", []).extend(caveats)
    changed = import_changes(doc)
    if changed:
        result["changed_on_import"] = changed
    if run.ok and editor_quirks.save_record(target, name, project_path, doc):
        result["note"] = ("recorded what the editor's import loses; JSON and binary exports from this project "
                          "with spine_export put it back")
    return result


def editor_animation_order(names: list[str], editor: str | None) -> list[str]:
    """The order an editor keeps these animations in (its own sort by name),
    read from a skeleton holding only those names, imported and exported
    back; `names` unchanged when that fails."""
    with tempfile.TemporaryDirectory(prefix="spine-order-") as tmp:
        folder = Path(tmp)
        probe = {"skeleton": {"spine": editor}, "bones": [{"name": "root"}],
                 "animations": {n: {"bones": {"root": {"rotate": [{"value": 0}]}}} for n in names}}
        (folder / "order.json").write_text(json.dumps(probe), encoding="utf-8")
        run = run_spine(["-i", str(folder / "order.json"), "-o", str(folder / "order.spine"), "-r", "order"], editor)
        if run.ok:
            run = run_spine(["-i", str(folder / "order.spine"), "-o", str(folder / "out"), "-e", "json"], editor)
        exported = folder / "out" / "order.json"
        if not run.ok or not exported.is_file():
            return names
        order = list(json.loads(exported.read_text(encoding="utf-8")).get("animations") or {})
    return order if sorted(order) == sorted(names) else names


def import_changes(doc: dict) -> list[str]:
    """What the Spine editor's JSON import changes or drops: physics rotate,
    scaleX and shearX on a bone without length (4.2 and 4.3), and in 4.3
    (seen with 4.3.26) clipping flags, which it does not read, and draw order
    folder keys that do not key one top-level draw order folder."""
    out = [f"physics {c.get('name')!r}: {', '.join(used)} dropped (bone {c.get('bone')!r} has no length)"
           for c, used in d.tipless_physics(doc)]
    if versions.layout(doc) == "4.2":
        return out
    for skin in d.skins(doc):
        for slot, atts in (skin.get("attachments") or {}).items():
            for name, att in (atts or {}).items():
                if not isinstance(att, dict) or att.get("type") != "clipping":
                    continue
                vertices = att.get("vertices") or []
                flat = len(vertices) == 2 * att.get("vertexCount", 0)  # weighted clips: not checked
                points = list(zip(vertices[::2], vertices[1::2])) if flat else []
                if att.get("inverse"):
                    out.append(f"clip {skin.get('name')}/{slot}/{name}: inverse is dropped")
                elif points and not d.is_convex(points):
                    out.append(f"clip {skin.get('name')}/{slot}/{name}: turned convex (clips to its hull)")
    names = [s["name"] for s in d.slots(doc)]
    for anim_name, anim in (doc.get("animations") or {}).items():
        entries = (anim or {}).get("drawOrderFolder")
        for entry in entries if isinstance(entries, list) else []:
            folder = d.folder_of(names, (entry or {}).get("slots"))
            if folder is None or "/" in folder:
                what = f"nested folder {folder!r}" if folder else f"slots {(entry or {}).get('slots')}"
                out.append(f"animation {anim_name!r}: the draw order keys of {what} are dropped")
    return out


def _png_settings(input_path: Path, output: Path, skeleton: str, fps: int, scale: float, background: str | None) -> dict:
    settings = {
        "class": "export-png", "exportType": "animation", "skeletonType": "single", "skeleton": skeleton,
        "animationType": "all", "skinType": "current", "fps": fps, "scale": scale,
        "renderImages": True, "renderBones": False, "renderOthers": False,
        "input": str(input_path), "output": str(output) + os.sep,
    }
    if background:
        rgba = background.lstrip("#").ljust(8, "f")
        settings["background"] = {k: int(rgba[i:i + 2], 16) / 255 for k, i in zip("rgba", (0, 2, 4, 6))}
    return settings


def gif_durations(frames: int, fps: float) -> list[int]:
    """Per-frame GIF delays in ms. GIF counts in 10 ms steps, so 30 fps
    alternates 30/40/30 ms to average exactly 33.3 ms."""
    out, done = [], 0
    for i in range(frames):
        nxt = round((i + 1) * 100 / fps)
        out.append((nxt - done) * 10)
        done = nxt
    return out


SHOWN_SKIN = "shown"  # the one skin of the copy that frames are rendered from


def _safe(name: str) -> str:
    """How the editor writes an animation or skin name into frame file names."""
    return name.replace("/", "-")


def _group_frames(folder: Path, skeleton: str, names: list[str] | None = None,
                  skin: str | None = None) -> dict[str, list[Path]]:
    """Frames per animation. The editor names them <skeleton>[-<skin>]-<animation>_<n>.png with
    each / as -, so with `names` (the animations) groups are keyed by each animation's own name."""
    known = sorted(((_safe(n), n) for n in names or []), key=lambda pair: -len(pair[0]))
    head = f"{skeleton}-{_safe(skin)}-" if skin else f"{skeleton}-"
    groups: dict[str, list[tuple[int, Path]]] = {}
    for p in folder.glob("*.png"):
        m = _FRAME.match(p.name)
        if not m:
            continue
        prefix = m.group("prefix")
        name = next((n for safe, n in known if prefix == head + safe), None)
        if name is None:  # a skin we did not choose (a .spine project's current skin)
            name = next((n for safe, n in known if prefix.startswith(skeleton + "-") and prefix.endswith("-" + safe)),
                        prefix[len(skeleton) + 1:] if prefix.startswith(skeleton + "-") else prefix)
        groups.setdefault(name, []).append((int(m.group("index")), p))
    return {k: [p for _, p in sorted(v)] for k, v in groups.items()}


_LINK_KEYS = ("type", "skin", "source", "parent", "slot", "timelines")
_MESH_GEOMETRY = ("uvs", "triangles", "vertices", "hull", "edges")


def _shown_copy(doc: dict, base: Path, skins: list[str]) -> dict:
    """A copy of the skeleton whose only skin is `skins` combined in that order, the later
    winning a placeholder as with the runtime's addSkin, so the editor renders that look:
    with several skins it renders an arbitrary "current" one.

    One JSON skin cannot hold what addSkin keeps by reference, so a linked mesh whose source
    no longer shows (another skin, often the linked mesh itself, took its placeholder) is
    written as a full mesh: the source's geometry with its own name, path and size.
    Animation keys on skin attachments follow the attachment that shows. Image and audio
    folders become absolute, so the copy works from any folder."""
    have = {s.get("name") for s in d.skins(doc)}
    unknown = [s for s in skins if s not in have]
    if unknown:
        raise SpineCliError(f"no skin {', '.join(unknown)} (have {', '.join(sorted(have - {'default'}))})")
    copy = json.loads(json.dumps(doc))
    shown: dict = {"name": SHOWN_SKIN, "attachments": {}}
    winner: dict[tuple[str, str], str] = {}  # (slot, placeholder) -> the skin that shows there
    for name in dict.fromkeys(skins):
        skin = d.find_skin(copy, name) or {}
        for slot, atts in (skin.get("attachments") or {}).items():
            shown["attachments"].setdefault(slot, {}).update(atts)
            winner.update({(slot, key): name for key in atts})
        for key, value in skin.items():
            if isinstance(value, list):  # skin bones and constraints
                shown[key] = shown.get(key, []) + [v for v in value if v not in shown.get(key, [])]
    inherited: dict[tuple[str, str], tuple[str, str, str]] = {}  # unlinked mesh -> its source's keys
    for slot, atts in shown["attachments"].items():
        for name, att in atts.items():
            if not isinstance(att, dict) or att.get("type") != "linkedmesh":
                continue
            source_skin = att.get("skin", "default")
            if source_skin == "default":
                continue
            if source_skin not in skins:
                raise SpineCliError(f"linked mesh {slot}/{name} uses a mesh in skin {source_skin!r}: add it to skins")
            where = (source_skin, att.get("slot", slot), att.get("source", att.get("parent")))
            source = (((d.find_skin(copy, where[0]) or {}).get("attachments") or {}).get(where[1]) or {}).get(where[2])
            if not isinstance(source, dict):
                raise SpineCliError(f"linked mesh {slot}/{name}: no mesh {where[2]!r} in skin {where[0]!r}")
            if shown["attachments"].get(where[1], {}).get(where[2]) is source:
                att["skin"] = SHOWN_SKIN
                continue
            if att.get("timelines", True):
                inherited[(slot, name)] = where
            own = {k: v for k, v in att.items() if k not in _LINK_KEYS}
            att.clear()
            att.update({"type": "mesh", **{k: source[k] for k in _MESH_GEOMETRY if k in source},
                        **{k: source[k] for k in ("width", "height") if k in source}, **own})
    for anim in (copy.get("animations") or {}).values():
        keyed = (anim or {}).get("attachments")
        if not isinstance(keyed, dict):
            continue
        moved: dict = {}
        for (slot, name), skin_name in winner.items():
            timelines = ((keyed.get(skin_name) or {}).get(slot) or {}).get(name)
            if timelines is None and (slot, name) in inherited:
                src_skin, src_slot, src_name = inherited[(slot, name)]
                timelines = ((keyed.get(src_skin) or {}).get(src_slot) or {}).get(src_name)
            if timelines:
                moved.setdefault(slot, {})[name] = timelines
        kept = {k: v for k, v in keyed.items() if k == "default"}
        if moved:
            kept[SHOWN_SKIN] = moved
        if kept:
            anim["attachments"] = kept
        else:
            anim.pop("attachments")
    # The editor renders a named skin only beside a default one (an empty one will do).
    copy["skins"] = ([s for s in d.skins(copy) if s.get("name") == "default"] or [{"name": "default"}]) + [shown]
    header = copy.setdefault("skeleton", {})
    for folder in ("images", "audio"):
        if header.get(folder) and not Path(header[folder]).is_absolute():
            header[folder] = str((base / header[folder]).resolve()) + os.sep
    return copy


def _missing_images(doc: dict, base: Path) -> str | None:
    """A warning when attachment images the frames need are not in the skeleton's images folder."""
    from .ops.attachments import attachment_paths, image_files

    folder = Path((doc.get("skeleton") or {}).get("images") or ".")
    folder = folder if folder.is_absolute() else (base / folder).resolve()
    missing = sorted(attachment_paths(doc) - set(image_files(folder)))
    if not missing:
        return None
    return (f"{len(missing)} attachment image(s) are not in {folder} ({', '.join(missing[:5])}"
            f"{', ...' if len(missing) > 5 else ''}): the editor draws those parts blank")


def spine_export(
    input_path: str | Path,
    output_dir: str | Path,
    format: str = "png",
    animations: list[str] | None = None,
    fps: int = 30,
    scale: float = 100,
    background: str | None = "ffffffff",
    editor_version: str | None = None,
    format_version: str | None = None,
    restore: bool = True,
    skins: list[str] | None = None,
) -> dict:
    """Export with the Spine CLI: png (frame sequences), gif (encoded from the
    frames), json or binary (skeleton data in the editor's format version).

    A JSON input is opened by an editor of its own format version, so its
    exports keep that version; a .spine project is opened by an editor of
    `format_version` (default: the newest installed supported one).

    Frames of a JSON input show `skins` combined (default: the skins holding
    the setup pose's attachments), rendered from a copy with that one skin;
    a .spine project's frames show its current skin."""
    input_path, output_dir = Path(input_path), Path(output_dir)
    if format not in ("png", "gif", "json", "binary"):
        raise SpineCliError("format must be png, gif, json or binary")
    if input_path.suffix.lower() == ".json":
        version = json_version(input_path)
        if format_version and versions.get(format_version).version != version:
            raise SpineCliError(f"{input_path.name} is format {version}; exporting keeps that version "
                                "(convert_skeleton converts skeleton JSON between versions)")
    else:
        version = versions.get(format_version).version if format_version else None
    editor = editor_for(version, editor_version)
    output_dir.mkdir(parents=True, exist_ok=True)
    if format in ("json", "binary"):
        run = run_spine(["-i", str(input_path), "-o", str(output_dir), "-e", format], editor)
        out = _result(run, output_dir=str(output_dir),
                      files=sorted(str(p) for p in output_dir.iterdir() if p.is_file()))
        exported = versions.major_minor(run.editor) if run.editor else None
        out["format_version"] = exported
        record = editor_quirks.record_path(input_path)
        if restore and run.ok and input_path.suffix.lower() == ".spine" and record.is_file():
            saved = json.loads(record.read_text(encoding="utf-8"))
            target = output_dir / f"{saved.get('skeleton')}.{'json' if format == 'json' else 'skel'}"
            if not target.is_file():
                done = {"restored": [], "errors": [f"the export has no {target.name}"]}
            elif format == "json":
                done = {"restored": editor_quirks.restore(target, saved), "errors": []}
            else:
                done = runtime.restore_binary(target, saved)
            if done["restored"]:
                out["restored"] = done["restored"]
            if done["errors"]:
                out.setdefault("warnings", []).append(
                    "the editor's import lost clip convex/inverse flags or nested folder keys, and they could not be "
                    f"put back ({'; '.join(done['errors'])})")
        if version and exported and exported != version:
            out.setdefault("warnings", []).append(
                f"exported by Spine {run.editor}: the data is format {exported}, not the {version} asked for")
        return out

    warnings: list[str] = []
    shown_skins: list[str] | None = None
    source, names, skin, staging = input_path, None, None, None
    if input_path.suffix.lower() == ".json":
        skeleton = input_path.stem
        doc = json.loads(input_path.read_text(encoding="utf-8"))
        names = list(doc.get("animations") or {})
        shown_skins = d.display_skins(doc) if skins is None else list(skins)
        if any(s.get("name") != "default" for s in d.skins(doc)) or skins is not None:
            doc = _shown_copy(doc, input_path.parent, shown_skins)
            staging = Path(tempfile.mkdtemp(prefix="spine-shown-"))
            source = staging / input_path.name
            source.write_text(json.dumps(doc), encoding="utf-8")
            skin = SHOWN_SKIN
        if missing := _missing_images(doc, input_path.parent):
            warnings.append(missing)
    else:
        if skins:
            raise SpineCliError("skins applies to a JSON input; a .spine project's frames show its current skin")
        info = project_info(input_path, editor)
        if not info.get("skeletons"):
            raise SpineCliError(f"no skeleton found in {input_path}")
        skeleton, names = info["skeletons"][0], info.get("animations")

    frames_dir = output_dir / "frames" if format == "png" else Path(tempfile.mkdtemp(prefix="spine-frames-"))
    fd, settings_name = tempfile.mkstemp(suffix=".export.json")
    os.close(fd)
    settings_file = Path(settings_name)
    try:
        settings_file.write_text(json.dumps(_png_settings(source, frames_dir, skeleton, fps, scale, background)))
        run = run_spine(["-e", str(settings_file)], editor)
        groups = _group_frames(frames_dir, skeleton, names, skin)
        if animations:
            unknown = sorted(set(animations) - set(groups))
            if unknown and run.ok:
                raise SpineCliError(f"no frames exported for {', '.join(unknown)} (have {', '.join(sorted(groups))})")
            for name in set(groups) - set(animations):
                for p in groups.pop(name):
                    p.unlink()
        empty = sorted(n for n, paths in groups.items() if paths and max(Image.open(paths[0]).size) <= 2)
        if empty:
            warnings.append(f"the editor drew nothing for {', '.join(empty)}: check the skins and the images folder")
        if editor_quirks.record_path(input_path).is_file():
            warnings.append("the frames show the project as the editor imported it: clips lost convex/inverse or "
                            "nested folder keys (preview_html shows the JSON as written)")
        shown = {"skins": shown_skins} if shown_skins is not None else {}
        if format == "png":
            out = _result(run, frames_dir=str(frames_dir), animations={k: len(v) for k, v in sorted(groups.items())},
                          **shown)
        else:
            gifs = {}
            for name, paths in sorted(groups.items()):
                images = [Image.open(p).convert("RGB") for p in paths]
                target = output_dir / f"{skeleton}-{_safe(name)}.gif"
                images[0].save(target, save_all=True, append_images=images[1:],
                               duration=gif_durations(len(images), fps), loop=0, optimize=True)
                gifs[name] = {"path": str(target), "frames": len(images)}
            out = _result(run, gifs=gifs, **shown)
        if warnings:
            out.setdefault("warnings", []).extend(warnings)
        return out
    finally:
        settings_file.unlink(missing_ok=True)
        if format == "gif":
            shutil.rmtree(frames_dir, ignore_errors=True)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
