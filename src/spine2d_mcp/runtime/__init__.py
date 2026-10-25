"""Load a skeleton with the real spine-core runtime of its format version
(4.2.120 for 4.2 files, 4.3.13 for 4.3) through Node, as evidence beyond the
Python checker.

spine-core is installed on first use into a cache folder per release
(`$SPINE2D_MCP_CACHE`, default `~/.cache/spine2d-mcp`). Without node or npm,
or offline on first use, the check is skipped and says why.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..atlas import default_atlas
from ..spine import versions

_SCRIPT = Path(__file__).with_name("runtime_check.mjs")


def cache_dir(core_version: str) -> Path:
    root = Path(os.environ.get("SPINE2D_MCP_CACHE", Path.home() / ".cache" / "spine2d-mcp"))
    return root / f"spine-core-{core_version}"


def _ensure_core(core_version: str) -> tuple[Path | None, str | None]:
    folder = cache_dir(core_version)
    if (folder / "node_modules" / "@esotericsoftware" / "spine-core" / "dist" / "index.js").is_file():
        return folder, None
    npm = shutil.which("npm")
    if npm is None:
        return None, "npm not found (needed once to install spine-core)"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [npm, "install", "--prefix", str(folder), "--no-audit", "--no-fund", "--silent",
             f"@esotericsoftware/spine-core@{core_version}"],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return None, f"installing spine-core {core_version} timed out after 300 s"
    if proc.returncode != 0:
        return None, f"could not install spine-core {core_version}: {proc.stderr.strip()[-300:]}"
    return folder, None


def _file_version(project_path: Path) -> tuple[str | None, str | None]:
    try:
        doc = json.loads(project_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return None, f"cannot read {project_path}: {e}"
    version = versions.of(doc) if isinstance(doc, dict) else None
    if not versions.is_supported(version):
        return None, f"format version {version!r} is not supported (supported: {versions.supported_text()})"
    return version, None


def _run(project_path: Path, atlas_path: Path, poses: bool) -> dict:
    if not atlas_path.is_file():
        return {"ran": False, "reason": f"no atlas at {atlas_path}"}
    version, reason = _file_version(project_path)
    if version is None:
        return {"ran": False, "reason": reason}
    node = shutil.which("node")
    if node is None:
        return {"ran": False, "reason": "node not found"}
    core, reason = _ensure_core(versions.get(version).runtime)
    if core is None:
        return {"ran": False, "reason": reason}
    args = [node, str(_SCRIPT), str(project_path), str(atlas_path), str(core)] + (["--poses"] if poses else [])
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"ran": False, "reason": "the runtime check timed out after 300 s"}
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ran": False, "reason": f"runtime check failed: {(proc.stderr or proc.stdout).strip()[-500:]}"}
    return {"ran": True, "ok": not result.get("errors"), **result}


def runtime_check(project_path: str | Path, atlas_path: str | Path | None = None) -> dict:
    """{"ran": bool, ...}: the runtime's load and per-animation results, or why it was skipped."""
    project_path = Path(project_path)
    atlas_path = Path(atlas_path) if atlas_path else default_atlas(project_path)
    return _run(project_path, atlas_path, poses=False)


def compare_poses(original: str | Path, converted: str | Path, atlas_path: str | Path,
                  tolerance: float = 0.01) -> dict:
    """Play both skeletons, each in its own format version's runtime, and
    compare every bone's world transform at every frame of every animation
    (1/60 s steps). Physics is not simulated, since 4.3 changed the simulation
    itself; each physics constraint's settings and keyed values per frame are
    compared instead, as the two runtimes read them."""
    a = _run(Path(original), Path(atlas_path), poses=True)
    b = _run(Path(converted), Path(atlas_path), poses=True)
    for label, run in (("original", a), ("converted", b)):
        if not run.get("ran"):
            return {"ran": False, "reason": f"{label}: {run.get('reason')}"}
        if run.get("errors"):
            return {"ran": True, "ok": False, "reason": f"{label} failed to play: {'; '.join(run['errors'])}"}
    result = {"ran": True, "runtimes": [a["runtime"], b["runtime"]], "animations": len(a["poses"])}
    if a["bone_names"] != b["bone_names"] or set(a["poses"]) != set(b["poses"]):
        return {**result, "ok": False, "reason": "the two skeletons have different bones or animations"}
    if a["physics"] != b["physics"]:
        changed = [pa["name"] for pa, pb in zip(a["physics"], b["physics"]) if pa != pb]
        return {**result, "ok": False,
                "reason": f"physics settings differ: {', '.join(changed) or 'different constraints'}"}
    worst = {"difference": 0.0}
    for name, frames in a["poses"].items():
        other = b["poses"][name]
        if len(frames) != len(other):
            return {**result, "ok": False, "reason": f"animation {name!r} has a different duration"}
        for i, (fa, fb) in enumerate(zip(frames, other)):
            for j, (ta, tb) in enumerate(zip(fa, fb)):
                diff = max(abs(x - y) for x, y in zip(ta, tb))
                if diff > worst["difference"]:
                    worst = {"difference": round(diff, 4), "animation": name, "frame": i, "bone": a["bone_names"][j]}
        for i, (oa, ob) in enumerate(zip(a["draw_orders"][name], b["draw_orders"][name])):
            if oa != ob:
                return {**result, "ok": False,
                        "reason": f"draw order differs in {name!r} at frame {i}: {', '.join(oa)} vs {', '.join(ob)}"}
        for i, (pa, pb) in enumerate(zip(a["physics_frames"][name], b["physics_frames"][name])):
            for j, (va, vb) in enumerate(zip(pa, pb)):
                diff = max(abs(x - y) for x, y in zip(va, vb))
                if diff > worst["difference"]:
                    worst = {"difference": round(diff, 4), "animation": name, "frame": i,
                             "physics": a["physics"][j]["name"]}
    return {**result, "ok": worst["difference"] <= tolerance, "largest_difference": worst,
            "note": "physics settings and keys compared; the simulation itself changed in 4.3 and is not"}
