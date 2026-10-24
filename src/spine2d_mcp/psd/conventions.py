"""Layer-name conventions and skeleton topology for the PoC.

A layer named after a known body part maps to a bone + slot + region
attachment. Bones follow a fixed, anatomically multi-segment hierarchy so that
hand-authored few-shot animations (Phase 1, e.g. spineboy with a real spine
chain) transfer without reshaping the skeleton.

Intermediate bones (hip/torso/chest/neck) may have no corresponding layer; they
are still created as *virtual* bones when needed as a parent of a present part
(see `resolve_chain`).
"""

from __future__ import annotations

ROOT_BONE = "root"

# part -> parent. Order = bone creation order (parent precedes child).
# Multi-segment torso: root -> hip -> torso -> chest -> neck -> head.
# Limbs hang off chest; legs off hip.
BODY_HIERARCHY: dict[str, str] = {
    "hip": ROOT_BONE,
    "torso": "hip",
    "chest": "torso",
    "neck": "chest",
    "head": "neck",
    "arm_left": "chest",
    "arm_right": "chest",
    "hand_left": "arm_left",
    "hand_right": "arm_right",
    "leg_left": "hip",
    "leg_right": "hip",
    "foot_left": "leg_left",
    "foot_right": "leg_right",
}

# Bones that are structural only — created virtually if absent as layers.
INTERMEDIATE_BONES = {"hip", "torso", "chest", "neck"}

# Accepted aliases -> canonical part name. A single `body` layer maps to torso.
_ALIASES: dict[str, str] = {
    "body": "torso",
    "chest_body": "chest",
    "pelvis": "hip",
}

# Side of a part's bbox that faces its parent joint (the pivot edge).
# "top": pivot at top edge midpoint (y-up max), "bottom": bottom edge, etc.
# Limbs/extremities pivot at the end nearest the body; head pivots at its base.
_JOINT_SIDE: dict[str, str] = {
    "head": "bottom",
    "neck": "bottom",
    "arm_left": "top",
    "arm_right": "top",
    "hand_left": "top",
    "hand_right": "top",
    "leg_left": "top",
    "leg_right": "top",
    "foot_left": "top",
    "foot_right": "top",
}

# Limbs whose joint end is a round cap: the bone sits at the cap's centre (half
# the limb width in from the joint edge), so the cap stays put as the joint
# turns instead of orbiting a point on its rim.
CAP_PARTS = {
    "arm_left", "arm_right", "leg_left", "leg_right",
    "hand_left", "hand_right", "foot_left", "foot_right",
}

# Extremities that turn about the parent limb's far cap (wrist, ankle) when the
# parent limb is present, rather than about their own edge.
TIP_PARTS = {"hand_left", "hand_right", "foot_left", "foot_right"}

_OPPOSITE_SIDE = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}

# Marker suffix for an explicit pivot override layer, e.g. "arm_left[pivot]".
PIVOT_TAG = "[pivot]"


def canonical_part(layer_name: str) -> str | None:
    """Canonical body-part name for a layer, or None if unrecognized."""
    key = _normalize(layer_name)
    if key in BODY_HIERARCHY:
        return key
    return _ALIASES.get(key)


def pivot_marker_part(layer_name: str) -> str | None:
    """If the layer is a pivot-override marker (`<part>[pivot]`), return the
    canonical part it overrides; else None."""
    raw = layer_name.strip().lower()
    if not raw.endswith(PIVOT_TAG):
        return None
    base = raw[: -len(PIVOT_TAG)].strip()
    return canonical_part(base)


def parent_of(part: str) -> str:
    return BODY_HIERARCHY[part]


def joint_side(part: str) -> str | None:
    """Bbox edge facing the parent, or None to fall back to bbox center
    (used for the torso/root area where no single joint dominates)."""
    return _JOINT_SIDE.get(part)


def far_side(part: str) -> str | None:
    """Bbox edge facing away from the parent (the limb's free end)."""
    side = joint_side(part)
    return _OPPOSITE_SIDE.get(side) if side else None


def resolve_chain(present_parts: set[str]) -> list[str]:
    """All bones needed in creation order, including virtual intermediates.

    A bone is included if it is present, or if it is an intermediate ancestor
    of a present bone. Result is ordered so every bone's parent precedes it.
    """
    needed: set[str] = set()
    for part in present_parts:
        needed.add(part)
        cur = part
        while True:
            par = BODY_HIERARCHY[cur]
            if par == ROOT_BONE:
                break
            needed.add(par)
            cur = par
    return [p for p in BODY_HIERARCHY if p in needed]


def is_virtual(part: str, present_parts: set[str]) -> bool:
    return part not in present_parts


def _normalize(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(" ", "_")
