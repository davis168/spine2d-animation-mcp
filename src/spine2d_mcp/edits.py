"""Load → change → check → save, shared by every tool that edits a skeleton.

A change is written only if it adds no new checker errors; errors the file
already had are reported but do not block the edit (so a rig can be fixed one
step at a time). The file is edited in its own format version; a version this
package does not support is refused, and a file whose version differs from
its game project's needs the user's go-ahead (`allow_version_mismatch`). The
result is what the MCP tool returns.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from . import project as proj
from .spine import document as d
from .spine import versions
from .spine.check import Finding, check, summary


class EditError(ValueError):
    """A requested change is invalid (unknown bone, bad value, ...)."""


def _key(f: Finding) -> tuple[str, str, str]:
    return f.level, f.path, f.message


def report(findings: list[Finding]) -> dict:
    return {
        "errors": [str(f) for f in findings if f.level == "error"],
        "warnings": [str(f) for f in findings if f.level == "warning"],
    }


def editable_version(doc: dict, path: str | Path) -> str:
    """The document's format version, or EditError if no supported reader can edit it."""
    version = versions.of(doc)
    if not versions.is_supported(version):
        raw = (doc.get("skeleton") or {}).get("spine") if isinstance(doc.get("skeleton"), dict) else None
        raise EditError(
            f"{Path(path).name} is format version {raw!r}; the editing tools support "
            f"{versions.supported_text()} (re-export it from the Spine editor in one of those versions)")
    return version


def edit_skeleton(
    project_path: str,
    change: Callable[[dict], object],
    out_path: str | None = None,
    loops: list[str] | None = None,
    allow_version_mismatch: bool = False,
) -> dict:
    """Apply `change(doc)` and save to `out_path` (default: in place).

    `change` mutates the document and may return a JSON-ready description of
    what it did. Raises EditError (nothing written) when the change itself
    is invalid or introduces checker errors, and project.NeedsAnswer when the
    file's format version differs from its game project's.
    """
    doc = d.load(project_path)
    version = editable_version(doc, project_path)
    target = Path(out_path or project_path)
    game = proj.for_existing_file(target, version, allow_version_mismatch, file=project_path)
    before = {_key(f) for f in check(doc, loops)}
    detail = change(doc)
    findings = check(doc, loops)
    introduced = [f for f in findings if f.level == "error" and _key(f) not in before]
    if introduced:
        raise EditError(
            "change not written, it would introduce errors:\n" + "\n".join(f"- {f}" for f in introduced)
        )
    d.save(doc, target)
    result = {"project_path": str(target), "summary": summary(doc), "version": game.note(version),
              **report(findings)}
    if detail is not None:
        result["changed"] = detail
    return result
