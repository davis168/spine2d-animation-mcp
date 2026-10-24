import pytest

from spine2d_mcp.psd.parser import parse_psd
from spine2d_mcp.spine.builder import build
from spine2d_mcp.spine.schema import (
    Bone,
    IkConstraint,
    RegionAttachment,
    Skin,
    Slot,
    SpineProject,
)
from spine2d_mcp.spine.validator import SpineValidationError, validate


def test_valid_project_passes(synthetic_psd):
    # build() already validates; this just confirms it doesn't raise.
    build(parse_psd(synthetic_psd))


def test_slot_without_bone_fails():
    proj = SpineProject(
        bones=[Bone(name="root")],
        slots=[Slot(name="head", bone="missing", attachment="head")],
    )
    with pytest.raises(SpineValidationError, match="missing bone"):
        validate(proj)


def test_parent_declared_after_child_fails():
    proj = SpineProject(
        bones=[Bone(name="child", parent="parent"), Bone(name="parent")],
    )
    with pytest.raises(SpineValidationError, match="not declared before"):
        validate(proj)


def test_duplicate_bone_name_fails():
    proj = SpineProject(bones=[Bone(name="root"), Bone(name="root")])
    with pytest.raises(SpineValidationError, match="duplicate bone"):
        validate(proj)


def test_ik_missing_target_fails():
    proj = SpineProject(
        bones=[Bone(name="root"), Bone(name="a", parent="root")],
        ik=[IkConstraint(name="k", bones=["a"], target="ghost")],
    )
    with pytest.raises(SpineValidationError, match="target"):
        validate(proj)


def test_attachment_to_missing_slot_fails():
    proj = SpineProject(
        bones=[Bone(name="root")],
        skins=[
            Skin(
                name="default",
                attachments={"nope": {"a": RegionAttachment(width=1, height=1)}},
            )
        ],
    )
    with pytest.raises(SpineValidationError, match="missing slot"):
        validate(proj)
