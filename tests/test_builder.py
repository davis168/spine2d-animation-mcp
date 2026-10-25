from spine2d_mcp.spine import versions
from spine2d_mcp.psd.parser import parse_psd
from spine2d_mcp.spine.builder import build
from spine2d_mcp.spine.writer import to_dict


def test_parse_finds_all_parts(synthetic_psd):
    parsed = parse_psd(synthetic_psd)
    names = {l.name for l in parsed.layers}
    assert {"head", "body", "leg_left", "foot_right"} <= names
    assert (parsed.width, parsed.height) == (400, 600)


def test_build_creates_root_plus_part_bones(synthetic_psd):
    result = build(parse_psd(synthetic_psd))
    bone_names = [b.name for b in result.project.bones]
    assert bone_names[0] == "root"
    # `body` is an alias for torso.
    assert "torso" in bone_names and "head" in bone_names


def test_multisegment_hierarchy(synthetic_psd):
    """body->torso alias; spine chain root->hip->torso->chest->neck->head;
    limbs off chest; legs off hip."""
    result = build(parse_psd(synthetic_psd))
    by_name = {b.name: b for b in result.project.bones}
    assert by_name["hip"].parent == "root"
    assert by_name["torso"].parent == "hip"
    assert by_name["chest"].parent == "torso"
    assert by_name["neck"].parent == "chest"
    assert by_name["head"].parent == "neck"
    assert by_name["arm_left"].parent == "chest"
    assert by_name["leg_right"].parent == "hip"
    assert by_name["hand_left"].parent == "arm_left"
    assert by_name["foot_right"].parent == "leg_right"


def test_virtual_bones_created_for_sparse_psd(make_psd):
    """A PSD with only head+body+arm_left must still get virtual
    chest/neck (parents of head/arm) and hip (parent of torso)."""
    psd = make_psd([
        ("body", 150, 110, 100, 180),
        ("head", 160, 20, 80, 80),
        ("arm_left", 100, 120, 40, 140),
    ])
    result = build(parse_psd(psd))
    bone_names = {b.name for b in result.project.bones}
    assert {"hip", "torso", "chest", "neck"} <= bone_names
    # Only present parts get slots.
    slot_names = {s.name for s in result.project.slots}
    assert slot_names == {"torso", "head", "arm_left"}


def test_child_bone_coords_are_relative_to_parent(synthetic_psd):
    """head sits above neck/chest in PSD, so in y-up its local y is positive."""
    result = build(parse_psd(synthetic_psd))
    by_name = {b.name: b for b in result.project.bones}
    assert by_name["head"].y > 0


def test_origin_is_bottom_center_of_character(synthetic_psd):
    """Root sits under the character's feet (Spine/Unity convention), not at
    the PSD canvas corner. Fixture parts span x 95..305, y 20..510 (y-down) on a
    600-high canvas, so the origin is (200, 90) in y-up canvas coordinates."""
    project = build(parse_psd(synthetic_psd)).project
    sk = project.skeleton
    assert (sk.x, sk.y, sk.width, sk.height) == (-105.0, 0.0, 210.0, 490.0)
    hip = next(b for b in project.bones if b.name == "hip")
    assert hip.x == 0.0  # symmetric fixture: hip is centered over the origin


def test_draw_order_follows_psd_zstack(make_psd):
    """Slots must be in PSD z-order (bottom-to-top), NOT bone hierarchy order.
    Put arm_left BELOW body, arm_right ABOVE body in the stack."""
    psd = make_psd([
        ("arm_left", 100, 120, 40, 140),   # bottom
        ("body", 150, 110, 100, 180),
        ("head", 160, 20, 80, 80),
        ("arm_right", 260, 120, 40, 140),  # top
    ])
    result = build(parse_psd(psd))
    slot_order = [s.name for s in result.project.slots]
    assert slot_order.index("arm_left") < slot_order.index("torso")
    assert slot_order.index("torso") < slot_order.index("arm_right")


def test_pivot_is_at_joint_not_center(synthetic_psd):
    """arm pivot must sit at the TOP edge of its bbox (joint to body), so its
    attachment gets a non-zero y offset compensating the shift."""
    result = build(parse_psd(synthetic_psd))
    att = result.project.skins[0].attachments["arm_left"]["arm_left"]
    # center-vs-top differ by height/2, so the attachment y offset is non-zero.
    assert abs(att.y) > 1.0


def _world(project):
    """World (x, y) of every bone; the builder writes no setup rotation or scale."""
    world = {}
    for b in project.bones:
        px, py = world.get(b.parent, (0.0, 0.0))
        world[b.name] = (px + b.x, py + b.y)
    return world


def test_limbs_pivot_at_their_round_cap_centre(synthetic_psd):
    """Arms and legs rotate about the centre of their joint-end cap (half the
    limb width in from the edge), so the round end stays put as the joint
    turns. Fixture origin is (200, 90) in y-up canvas space (600-high canvas):
    arm_left (100,120,40,140) -> cap centre PSD (120, 140) -> world (-80, 370);
    leg_left (150,300,45,180) -> PSD (172.5, 322.5) -> world (-27.5, 187.5)."""
    world = _world(build(parse_psd(synthetic_psd)).project)
    assert world["arm_left"] == (-80.0, 370.0)
    assert world["leg_left"] == (-27.5, 187.5)


def test_hands_and_feet_pivot_at_the_parent_limbs_far_cap(synthetic_psd):
    """A hand turns about the wrist, the centre of the arm's bottom cap, not
    about its own edge: arm_left bottom 260 - 20 = PSD y 240 -> world y 270.
    Feet likewise turn about the leg's bottom cap: 480 - 22.5 -> world y 52.5."""
    world = _world(build(parse_psd(synthetic_psd)).project)
    assert world["hand_left"] == (-80.0, 270.0)
    assert world["foot_left"] == (-27.5, 52.5)


def test_moving_pivots_keeps_every_image_where_the_psd_put_it(synthetic_psd):
    parsed = parse_psd(synthetic_psd)
    project = build(parsed).project
    world = _world(project)
    ox, oy = 200.0, 90.0  # fixture origin: bottom-centre of the parts
    for layer in parsed.layers:
        part = "torso" if layer.name == "body" else layer.name
        att = project.skins[0].attachments[part][part]
        cx = world[part][0] + att.x + ox
        cy = world[part][1] + att.y + oy
        assert (cx, cy) == (layer.left + layer.width / 2, 600 - (layer.top + layer.height / 2))


def test_pivot_marker_overrides_heuristic(make_psd):
    """A `arm_left[pivot]` marker layer overrides the edge heuristic."""
    psd = make_psd([
        ("body", 150, 110, 100, 180),
        ("arm_left", 100, 120, 40, 140),
        ("arm_left[pivot]", 118, 125, 4, 4),  # marker near top of arm
    ])
    parsed = parse_psd(psd)
    assert "arm_left" in parsed.pivot_markers
    # marker layer must NOT appear as a normal part
    assert all("[pivot]" not in l.name for l in parsed.layers)
    result = build(parsed)
    assert any(b.name == "arm_left" for b in result.project.bones)


def test_attachment_dims_match_layer(synthetic_psd):
    result = build(parse_psd(synthetic_psd))
    att = result.project.skins[0].attachments["head"]["head"]
    assert (att.width, att.height) == (80.0, 80.0)


def test_serializes_without_extra_fields(synthetic_psd):
    out = to_dict(build(parse_psd(synthetic_psd)).project)
    root = next(b for b in out["bones"] if b["name"] == "root")
    assert root == {"name": "root"}
    assert out["skeleton"]["spine"] == versions.NEWEST
