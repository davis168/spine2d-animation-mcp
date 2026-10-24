"""Load → change → check → save, shared by every tool that edits a skeleton.

A change is written only if it adds no new checker errors; errors the file
already had are reported but do not block the edit (so a rig can be fixed one
step at a time). The result is what the MCP tool returns.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .spine import document as d
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


def edit_skeleton(
    project_path: str,
    change: Callable[[dict], object],
    out_path: str | None = None,
    loops: list[str] | None = None,
) -> dict:
    """Apply `change(doc)` and save to `out_path` (default: in place).

    `change` mutates the document and may return a JSON-ready description of
    what it did. Raises EditError (nothing written) when the change itself
    is invalid or introduces checker errors.
    """
    doc = d.load(project_path)
    before = {_key(f) for f in check(doc, loops)}
    detail = change(doc)
    findings = check(doc, loops)
    introduced = [f for f in findings if f.level == "error" and _key(f) not in before]
    if introduced:
        raise EditError(
            "change not written, it would introduce errors:\n" + "\n".join(f"- {f}" for f in introduced)
        )
    target = Path(out_path or project_path)
    d.save(doc, target)
    result = {"project_path": str(target), "summary": summary(doc), **report(findings)}
    if detail is not None:
        result["changed"] = detail
    return result
