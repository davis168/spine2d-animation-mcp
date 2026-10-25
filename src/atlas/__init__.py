"""Atlas packing and writing, and which atlas a skeleton uses."""

from __future__ import annotations

from pathlib import Path

from ..spine.versions import unversioned


def default_atlas(skeleton: str | Path) -> Path:
    """The atlas a skeleton uses unless one is given: <name>.atlas beside it,
    or for a converted copy (<name>-4.3.json) the atlas it shares with its
    original, <name>.atlas."""
    skeleton = Path(skeleton)
    own = skeleton.with_suffix(".atlas")
    shared = skeleton.with_name(f"{unversioned(skeleton.stem)}.atlas")
    return own if own.exists() or not shared.exists() else shared
