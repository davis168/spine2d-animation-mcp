from spine2d_mcp.spine.schema import (
    Skeleton,
    Bone,
    IkConstraint,
    RegionAttachment,
    Skin,
    Slot,
    SpineProject,
)
from spine2d_mcp.spine.writer import to_dict


def test_defaults_are_stripped():
    """A bone with only a name must serialize to just {"name": ...} —
    the editor rejects redundant default fields."""
    proj = SpineProject(bones=[Bone(name="root")])
    out = to_dict(proj)
    assert out["bones"] == [{"name": "root"}]


def test_non_default_bone_fields_kept():
    proj = SpineProject(
        bones=[Bone(name="head", parent="body", x=10.0, rotation=90.0)]
    )
    bone = to_dict(proj)["bones"][0]
    assert bone == {"name": "head", "parent": "body", "x": 10.0, "rotation": 90.0}


def test_inherit_field_name():
    """4.2 uses `inherit`, not `transform`."""
    from spine2d_mcp.spine.schema import Inherit

    proj = SpineProject(bones=[Bone(name="b", inherit=Inherit.noScale)])
    assert to_dict(proj)["bones"][0]["inherit"] == "noScale"


def test_region_attachment_required_dims():
    """width/height are required and always emitted even at default-looking
    values, because the reader reads them without a default."""
    att = RegionAttachment(width=64.0, height=128.0)
    proj = SpineProject(
        slots=[Slot(name="head", bone="head", attachment="head")],
        skins=[Skin(name="default", attachments={"head": {"head": att}})],
    )
    out = to_dict(proj)
    region = out["skins"][0]["attachments"]["head"]["head"]
    assert region == {"width": 64.0, "height": 128.0}


def test_ik_constraint_has_order():
    """`order` is required by the 4.2 reader (old code omitted it)."""
    ik = IkConstraint(name="leg_ik", bones=["thigh", "shin"], target="foot")
    proj = SpineProject(ik=[ik])
    out = to_dict(proj)["ik"][0]
    assert out["name"] == "leg_ik"
    assert out["bones"] == ["thigh", "shin"]
    assert out["target"] == "foot"


def test_skin_name_always_emitted():
    """The reader requires the skin `name` (no default), so the "default" skin
    must keep it — exclude_defaults stripped it and the editor rejected the
    file with "Named value not found: name"."""
    proj = SpineProject(skins=[Skin(name="default")])
    assert to_dict(proj)["skins"] == [{"name": "default"}]


def test_skeleton_version_present():
    """New skeletons default to the newest supported format version; the
    importer sets the version the game project needs."""
    out = to_dict(SpineProject())
    assert out["skeleton"]["spine"] == "4.3"
    assert to_dict(SpineProject(skeleton=Skeleton(spine="4.2")))["skeleton"]["spine"] == "4.2"


def test_roundtrip_through_json():
    proj = SpineProject(bones=[Bone(name="root"), Bone(name="body", parent="root")])
    reparsed = SpineProject.model_validate(to_dict(proj))
    assert [b.name for b in reparsed.bones] == ["root", "body"]
