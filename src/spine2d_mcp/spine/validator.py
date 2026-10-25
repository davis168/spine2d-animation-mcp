"""Structural validation of a SpineProject.

Level-1 validation: pure-Python checks of the invariants the Spine 4.x readers
(`reference/SkeletonJson-4.2.java`, `-4.3.java`) enforce while parsing. Catching them here makes
format regressions reproducible in tests instead of only surfacing as a failed
manual import in the Spine editor.

Level-2 (real reader parse via spine-canvaskit) lives in
`scripts/validate_skeleton.mjs` and is optional.
"""

from __future__ import annotations

from .schema import SpineProject


class SpineValidationError(ValueError):
    """Raised when a SpineProject violates a Spine structural invariant."""


def validate(project: SpineProject) -> None:
    errors: list[str] = []

    bone_names = [b.name for b in project.bones]
    seen: set[str] = set()
    declared_before: set[str] = set()
    for b in project.bones:
        if b.name in seen:
            errors.append(f"duplicate bone name: {b.name!r}")
        seen.add(b.name)
        # SkeletonJson resolves a bone's parent by lookup at read time; a parent
        # must be declared earlier in the list.
        if b.parent is not None and b.parent not in declared_before:
            errors.append(
                f"bone {b.name!r} has parent {b.parent!r} not declared before it"
            )
        declared_before.add(b.name)
    bone_set = set(bone_names)

    slot_names: set[str] = set()
    for s in project.slots:
        if s.name in slot_names:
            errors.append(f"duplicate slot name: {s.name!r}")
        slot_names.add(s.name)
        if s.bone not in bone_set:
            errors.append(f"slot {s.name!r} references missing bone {s.bone!r}")

    for ik in project.ik:
        for bn in ik.bones:
            if bn not in bone_set:
                errors.append(f"ik {ik.name!r} references missing bone {bn!r}")
        if ik.target not in bone_set:
            errors.append(f"ik {ik.name!r} target {ik.target!r} is not a bone")

    for skin in project.skins:
        for slot_name, atts in skin.attachments.items():
            if slot_name not in slot_names:
                errors.append(
                    f"skin {skin.name!r} attaches to missing slot {slot_name!r}"
                )
            for att_name, att in atts.items():
                if att.width <= 0 or att.height <= 0:
                    errors.append(
                        f"attachment {att_name!r} in slot {slot_name!r} has "
                        f"non-positive size {att.width}x{att.height}"
                    )

    if errors:
        raise SpineValidationError(
            "SpineProject failed validation:\n  - " + "\n  - ".join(errors)
        )
