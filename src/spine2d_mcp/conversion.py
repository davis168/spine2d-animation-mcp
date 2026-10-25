"""Convert a skeleton file to another format version: pick the target, ask
when the user must decide, write the converted copy beside the original
(sharing its atlas and images), check it, and verify it in both runtimes.

`spine/convert.py` converts the document; this module handles the files and
the questions around it, as `edits.py` does for editing.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from . import project as proj
from .atlas import default_atlas
from .edits import EditError, editable_version, report
from .pipeline import header_version
from .runtime import compare_poses
from .spine import document as d
from .spine import versions
from .spine.check import check, summary
from .spine.convert import LossyConversion, convert


def converted_path(skeleton: Path, version: str) -> Path:
    """hero.json (or hero-4.2.json) -> hero-4.3.json beside it."""
    return skeleton.with_name(f"{versions.unversioned(skeleton.stem)}-{version}.json")


def convert_file(
    project_path: str | Path,
    to_version: str | None = None,
    out_path: str | Path | None = None,
    allow_lossy: bool = False,
    allow_version_mismatch: bool = False,
    verify: bool = True,
    atlas_path: str | Path | None = None,
    save_project_version: bool = False,
) -> dict:
    """Raises project.NeedsAnswer (nothing written) when the user must decide,
    EditError when the conversion cannot be done."""
    src = Path(project_path)
    doc = d.load(src)
    file_version = editable_version(doc, src)
    game = proj.detect(Path(out_path) if out_path else src)
    if to_version is None:
        if game.version == file_version:
            return {"converted": False, "note": f"{src.name} is already {file_version}, the game project's "
                                                f"version ({game.source}); pass to_version to convert anyway"}
        others = [v for v in versions.SUPPORTED if v != file_version]
        if versions.is_supported(game.version):
            to_version = game.version
        elif len(others) == 1:
            to_version = others[0]
        else:
            raise proj.NeedsAnswer(f"Convert {src.name} (format {file_version}) to which format version?",
                                   [{"answer": v, "call_again_with": {"to_version": v}} for v in reversed(others)],
                                   recommended=others[-1])
    target = versions.get(to_version).version
    if target == file_version:
        raise EditError(f"{src.name} is already format version {file_version}")
    out = Path(out_path) if out_path else converted_path(src, target)
    if out_path is None and out.exists():
        raise EditError(f"{out} already exists; pass out_path (that file to overwrite it, or another name)")
    game = proj.detect(out)
    _check_target(game, src, file_version, target, allow_version_mismatch, save_project_version)
    try:
        conversion = convert(doc, target, allow_lossy)
    except LossyConversion as lossy:
        raise proj.NeedsAnswer(
            f"Converting {src.name} from {file_version} to {target} would lose: "
            + "; ".join(lossy.losses) + ". Convert anyway and drop these?",
            [{"answer": f"Keep it in {file_version} (do not convert)", "instead": "nothing to do"},
             {"answer": "Convert and drop them", "call_again_with": {"allow_lossy": True}}],
            recommended=f"Keep it in {file_version} (do not convert)") from None

    new_doc = conversion.doc
    new_doc["skeleton"]["spine"] = header_version(target)
    images = new_doc["skeleton"].get("images")
    if images and out.parent.resolve() != src.parent.resolve():
        folder = (src.parent / images).resolve()
        new_doc["skeleton"]["images"] = os.path.relpath(folder, out.parent.resolve()).replace(os.sep, "/") + "/"
    atlas = Path(atlas_path) if atlas_path else default_atlas(src)
    original = _snapshot(src) if verify and out.resolve() == src.resolve() else src
    d.save(new_doc, out)

    result = {"converted": True, "from": file_version, "to": target, "project_path": str(out),
              "summary": summary(new_doc), "changed": conversion.changed, **report(check(new_doc)),
              "version": game.note(target)}
    if conversion.lost:
        result["lost"] = conversion.lost
    if conversion.dropped_editor_data:
        result["dropped_editor_data"] = conversion.dropped_editor_data
    if save_project_version:
        result["notes"] = [proj.save_declaration(game, target)]
    if atlas.is_file():
        result["atlas_path"] = str(atlas)  # shared with the original: same images, same regions
        if default_atlas(out).resolve() != atlas.resolve():
            result["atlas_note"] = f"pass atlas_path={str(atlas)!r} to preview_html and validate_skeleton"
    try:
        if verify:
            result["verify"] = (compare_poses(original, out, atlas) if atlas.is_file()
                                else {"ran": False, "reason": f"no atlas at {atlas} (pass atlas_path)"})
            if conversion.lost and result["verify"].get("ran"):
                result["verify"]["note"] = "parts were dropped (see lost), so differences there are expected"
    except Exception as e:  # the converted file is written; report this rather than fail
        result["verify"] = {"ran": False, "reason": f"{type(e).__name__}: {e}"}
    finally:
        if original != src:
            original.unlink(missing_ok=True)
    return result


def _check_target(game: proj.ProjectVersion, src: Path, file_version: str, target: str,
                  allow_version_mismatch: bool, save_project_version: bool) -> None:
    """NeedsAnswer when the target is not the game project's version."""
    if save_project_version:
        proj.check_declaration(game, target)
        return
    if allow_version_mismatch or not game.version or game.version == target:
        return
    if game.version == file_version:
        keep = {"answer": f"Keep {src.name} in {file_version}, the game project's version", "instead": "nothing to do"}
    else:
        keep = {"answer": f"Convert to {game.version}, the game project's version",
                "call_again_with": {"to_version": game.version}}
    raise proj.NeedsAnswer(
        f"The game project uses Spine {game.version} ({game.source}), but conversion to {target} was asked "
        f"for; a {target} skeleton will not load in this game.",
        [keep, {"answer": f"Convert to {target} anyway", "call_again_with": {"allow_version_mismatch": True}}],
        recommended=keep["answer"])


def _snapshot(src: Path) -> Path:
    """A copy of the original, to verify against when converting in place."""
    fd, name = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    return Path(shutil.copyfile(src, name))
