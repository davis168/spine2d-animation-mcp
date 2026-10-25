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

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

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


def editor_for(format_version: str | None, editor_version: str | None = None) -> str | None:
    """The editor release to run for skeleton data of `format_version` (None:
    any supported version, newest first)."""
    if editor_version:
        if format_version and versions.major_minor(editor_version) != format_version:
            raise SpineCliError(
                f"editor {editor_version} reads and writes format {versions.major_minor(editor_version)}, but this "
                f"is {format_version} data: the Spine editor imports only its own format version (use a "
                f"{format_version}.x editor, e.g. {versions.get(format_version).editor})")
        return editor_version
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
    return versions.get(format_version).editor if format_version else None


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
) -> dict:
    project_path = Path(project_path)
    version = json_version(project_path)
    target = Path(spine_project) if spine_project else project_path.with_suffix(".spine")
    args = ["-i", str(project_path), "-o", str(target), "-r"]
    if skeleton_name:
        args.append(skeleton_name)
    run = run_spine(args, editor_for(version, editor_version))
    return _result(run, spine_project=str(target), format_version=version)


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


def _group_frames(folder: Path, skeleton: str) -> dict[str, list[Path]]:
    groups: dict[str, list[tuple[int, Path]]] = {}
    for p in folder.glob("*.png"):
        m = _FRAME.match(p.name)
        if m:
            name = m.group("prefix")
            name = name[len(skeleton) + 1:] if name.startswith(skeleton + "-") else name
            groups.setdefault(name, []).append((int(m.group("index")), p))
    return {k: [p for _, p in sorted(v)] for k, v in groups.items()}


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
) -> dict:
    """Export with the Spine CLI: png (frame sequences), gif (encoded from the
    frames), json or binary (skeleton data in the editor's format version).

    A JSON input is opened by an editor of its own format version, so its
    exports keep that version; a .spine project is opened by an editor of
    `format_version` (default: the newest installed supported one)."""
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
        if version and exported and exported != version:
            out.setdefault("warnings", []).append(
                f"exported by Spine {run.editor}: the data is format {exported}, not the {version} asked for")
        return out

    if input_path.suffix.lower() == ".json":
        skeleton = input_path.stem
    else:
        skeletons = project_info(input_path, editor).get("skeletons") or []
        if not skeletons:
            raise SpineCliError(f"no skeleton found in {input_path}")
        skeleton = skeletons[0]

    frames_dir = output_dir / "frames" if format == "png" else Path(tempfile.mkdtemp(prefix="spine-frames-"))
    fd, settings_name = tempfile.mkstemp(suffix=".export.json")
    os.close(fd)
    settings_file = Path(settings_name)
    try:
        settings_file.write_text(json.dumps(_png_settings(input_path, frames_dir, skeleton, fps, scale, background)))
        run = run_spine(["-e", str(settings_file)], editor)
        groups = _group_frames(frames_dir, skeleton)
        if animations:
            unknown = sorted(set(animations) - set(groups))
            if unknown and run.ok:
                raise SpineCliError(f"no frames exported for {', '.join(unknown)} (have {', '.join(sorted(groups))})")
            for name in set(groups) - set(animations):
                for p in groups.pop(name):
                    p.unlink()
        if format == "png":
            return _result(run, frames_dir=str(frames_dir),
                           animations={k: len(v) for k, v in sorted(groups.items())})
        gifs = {}
        for name, paths in sorted(groups.items()):
            images = [Image.open(p).convert("RGB") for p in paths]
            target = output_dir / f"{skeleton}-{name}.gif"
            images[0].save(target, save_all=True, append_images=images[1:],
                           duration=gif_durations(len(images), fps), loop=0, optimize=True)
            gifs[name] = {"path": str(target), "frames": len(images)}
        return _result(run, gifs=gifs)
    finally:
        settings_file.unlink(missing_ok=True)
        if format == "gif":
            shutil.rmtree(frames_dir, ignore_errors=True)
