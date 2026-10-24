"""Load a skeleton with the real spine-core runtime (the one spine-phaser-v4
4.2.x ships) through Node, as evidence beyond the Python checker.

spine-core is installed on first use into a cache folder
(`$SPINE2D_MCP_CACHE`, default `~/.cache/spine2d-mcp`). Without node or npm,
or offline on first use, the check is skipped and says why.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

SPINE_CORE_VERSION = "4.2.120"
_SCRIPT = Path(__file__).with_name("runtime_check.mjs")


def cache_dir() -> Path:
    root = Path(os.environ.get("SPINE2D_MCP_CACHE", Path.home() / ".cache" / "spine2d-mcp"))
    return root / f"spine-core-{SPINE_CORE_VERSION}"


def _ensure_core() -> tuple[Path | None, str | None]:
    folder = cache_dir()
    if (folder / "node_modules" / "@esotericsoftware" / "spine-core" / "dist" / "index.js").is_file():
        return folder, None
    npm = shutil.which("npm")
    if npm is None:
        return None, "npm not found (needed once to install spine-core)"
    folder.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [npm, "install", "--prefix", str(folder), "--no-audit", "--no-fund", "--silent",
         f"@esotericsoftware/spine-core@{SPINE_CORE_VERSION}"],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        return None, f"could not install spine-core {SPINE_CORE_VERSION}: {proc.stderr.strip()[-300:]}"
    return folder, None


def runtime_check(project_path: str | Path, atlas_path: str | Path | None = None) -> dict:
    """{"ran": bool, ...}: the runtime's load and per-animation results, or why it was skipped."""
    project_path = Path(project_path)
    atlas_path = Path(atlas_path) if atlas_path else project_path.with_suffix(".atlas")
    if not atlas_path.is_file():
        return {"ran": False, "reason": f"no atlas at {atlas_path}"}
    node = shutil.which("node")
    if node is None:
        return {"ran": False, "reason": "node not found"}
    core, reason = _ensure_core()
    if core is None:
        return {"ran": False, "reason": reason}
    proc = subprocess.run(
        [node, str(_SCRIPT), str(project_path), str(atlas_path), str(core)],
        capture_output=True, text=True, timeout=300,
    )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ran": False, "reason": f"runtime check failed: {(proc.stderr or proc.stdout).strip()[-500:]}"}
    return {"ran": True, "ok": not result.get("errors"), **result}
