"""Preset animations for rigs built by import_psd_to_spine.

Bone names are the importer's canonical names (conventions.BODY_HIERARCHY);
`bone_map` renames them for other rigs. The presets assume the importer's rig
shape: y-up, bones with no setup rotation, arms and legs hanging down, and the
origin at the feet. Pixel amounts are tuned for a ~500 px tall character and
scale with the skeleton's height.

Timing follows the animation-principles beat plans: followers lag their driver
(head after torso, hands after arms), left and right never mirror, loops end
on their first values, and the walk moves only the lifted leg so the planted
foot never slides. Angles are degrees CCW: the viewer-left arm swings outward
with negative angles, the viewer-right arm with positive ones.
"""

from __future__ import annotations

from .animation import AnimationError

REFERENCE_HEIGHT = 496.0  # the character the pixel amounts were tuned on

LOOPING = {"idle", "walk"}


def _rot(*keys):
    """(time, degrees, ease of the segment starting here)."""
    return [{"time": t, "value": v, **({"ease": e} if e else {})} for t, v, e in keys]


def _xy(*keys):
    return [{"time": t, "x": x, "y": y, **({"ease": e} if e else {})} for t, (x, y), e in keys]


def _idle(s: float) -> dict:
    sm = "smooth"
    return {"bones": {
        "torso": {"translate": _xy((0, (0, 0), sm), (1.0, (0, 1.5 * s), sm), (2.0, (0, 0), None))},
        "chest": {"rotate": _rot((0, 0, sm), (1.0, 1.0, sm), (2.0, 0, None))},
        "head": {"rotate": _rot((0, 0, sm), (1.15, 2.5, sm), (2.0, 0, None))},
        "arm_left": {"rotate": _rot((0, 0, sm), (1.05, -2.5, sm), (2.0, 0, None))},
        "arm_right": {"rotate": _rot((0, 0, sm), (1.25, 2.0, sm), (2.0, 0, None))},
        "hand_left": {"rotate": _rot((0, 0, sm), (1.15, -4, sm), (2.0, 0, None))},
        "hand_right": {"rotate": _rot((0, 0, sm), (1.35, 3, sm), (2.0, 0, None))},
    }}


def _walk(s: float) -> dict:
    io = "inout"
    lift = 16 * s
    return {"bones": {
        # In place, front view: each leg lifts in turn (passing at 0.2 s / 0.6 s).
        "leg_right": {"translate": _xy((0, (0, 0), None), (0.05, (0, 0), "out"), (0.2, (0, lift), "in"),
                                       (0.35, (0, 0), None), (0.8, (0, 0), None))},
        "leg_left": {"translate": _xy((0, (0, 0), None), (0.45, (0, 0), "out"), (0.6, (0, lift), "in"),
                                      (0.75, (0, 0), None), (0.8, (0, 0), None))},
        # The upper body bobs and shifts over the standing leg.
        "torso": {
            "translate": _xy((0, (0, -2 * s), io), (0.2, (-3 * s, 2 * s), io), (0.4, (0, -2 * s), io),
                             (0.6, (3 * s, 2 * s), io), (0.8, (0, -2 * s), None)),
            "rotate": _rot((0, 0, io), (0.2, -1.5, io), (0.4, 0, io), (0.6, 1.5, io), (0.8, 0, None)),
        },
        "head": {"rotate": _rot((0, 0, io), (0.267, 1.5, io), (0.467, 0, io), (0.667, -1.5, io), (0.8, 0, None))},
        # The forward arm swings inward and foreshortens; the back arm barely moves.
        "arm_left": {
            "rotate": _rot((0, 5, io), (0.2, 0, io), (0.4, -2, io), (0.6, 0, io), (0.8, 5, None)),
            "scale": _xy((0, (1, 0.9), io), (0.2, (1, 1), io), (0.4, (1, 0.98), io), (0.6, (1, 1), io), (0.8, (1, 0.9), None)),
        },
        "arm_right": {
            "rotate": _rot((0, 2, io), (0.2, 0, io), (0.4, -5, io), (0.6, 0, io), (0.8, 2, None)),
            "scale": _xy((0, (1, 0.98), io), (0.2, (1, 1), io), (0.4, (1, 0.9), io), (0.6, (1, 1), io), (0.8, (1, 0.98), None)),
        },
        "hand_left": {"rotate": _rot((0, 3, io), (0.067, 4, io), (0.467, -4, io), (0.8, 3, None))},
        "hand_right": {"rotate": _rot((0, -3, io), (0.067, -4, io), (0.467, 4, io), (0.8, -3, None))},
    }}


def _wave(s: float) -> dict:
    io = "inout"
    # Viewer-right arm: dip (anticipation), raise past 150 deg, sway ±12 deg with
    # the hand trailing each swing, lower with a small overshoot.
    return {"bones": {
        "arm_right": {"rotate": _rot(
            (0, 0, "in"), (0.15, -8, "out"), (0.5, 155, io), (0.62, 140, io), (0.85, 160, io), (1.1, 138, io),
            (1.35, 160, io), (1.6, 140, io), (1.9, 150, io), (2.3, -4, io), (2.5, 0, None))},
        "hand_right": {"rotate": _rot(
            (0, 0, io), (0.5, 10, io), (0.72, 22, io), (0.95, -25, io), (1.2, 25, io), (1.45, -25, io),
            (1.7, 20, io), (2.0, 0, io), (2.35, 8, io), (2.5, 0, None))},
        "head": {"rotate": _rot((0, 0, io), (0.6, -6, io), (1.2, -4, io), (1.8, -6, io), (2.4, 0, None))},
        "chest": {"rotate": _rot((0, 0, io), (0.55, 3, io), (1.9, 3, io), (2.4, 0, None))},
        "arm_left": {"rotate": _rot((0, 0, io), (0.6, -4, io), (1.9, -3, io), (2.4, 0, None))},
    }}


def _jump(s: float) -> dict:
    io = "inout"
    # Root sits at the feet, so scaling it squashes toward the ground with the feet
    # planted; hip translate carries the body through the air.
    return {"bones": {
        "root": {"scale": _xy(
            (0, (1, 1), io), (0.25, (1.12, 0.86), "out"), (0.4, (0.9, 1.12), io), (0.6, (1, 1), io),
            (0.82, (0.95, 1.06), "out"), (0.9, (1.15, 0.85), "out"), (1.08, (0.97, 1.03), io), (1.3, (1, 1), None))},
        "hip": {"translate": _xy((0, (0, 0), None), (0.25, (0, 0), "out"), (0.55, (0, 100 * s), "in"),
                                 (0.85, (0, 0), None), (1.3, (0, 0), None))},
        "arm_left": {"rotate": _rot((0, 0, io), (0.25, -12, "out"), (0.45, -150, io), (0.62, -140, "in"),
                                    (0.88, -50, "out"), (1.05, 8, io), (1.3, 0, None))},
        "arm_right": {"rotate": _rot((0, 0, io), (0.283, 10, "out"), (0.483, 145, io), (0.653, 138, "in"),
                                     (0.913, 45, "out"), (1.083, -6, io), (1.3, 0, None))},
        "hand_left": {"rotate": _rot((0, 0, io), (0.3, 15, "out"), (0.5, -20, io), (0.9, 25, "out"),
                                     (1.15, -6, io), (1.3, 0, None))},
        "hand_right": {"rotate": _rot((0, 0, io), (0.32, -15, "out"), (0.52, 18, io), (0.92, -25, "out"),
                                      (1.17, 5, io), (1.3, 0, None))},
        "leg_left": {"rotate": _rot((0, 0, io), (0.4, 0, "out"), (0.55, -7, io), (0.85, 0, None), (1.3, 0, None))},
        "leg_right": {"rotate": _rot((0, 0, io), (0.4, 0, "out"), (0.57, 6, io), (0.85, 0, None), (1.3, 0, None))},
        "head": {"rotate": _rot((0, 0, io), (0.25, -2, "out"), (0.55, 4, io), (0.92, -3, "out"),
                                (1.15, 1, io), (1.3, 0, None))},
    }}


_PRESETS = {"idle": _idle, "walk": _walk, "wave": _wave, "jump": _jump}

_MIRROR = {
    "arm_left": "arm_right", "arm_right": "arm_left",
    "hand_left": "hand_right", "hand_right": "hand_left",
    "leg_left": "leg_right", "leg_right": "leg_left",
    "foot_left": "foot_right", "foot_right": "foot_left",
}

NAMES = tuple(_PRESETS)


def preset(name: str, height: float | None = None, side: str = "right",
           speed: float = 1.0, amount: float = 1.0) -> dict:
    """A preset animation in the shorthand (see animation.py).

    Args:
        name: one of NAMES.
        height: the character's height in skeleton units; scales pixel amounts.
        side: "right" or "left" — which arm waves (wave only).
        speed: >1 plays faster (key times divided by it).
        amount: scales rotations and translations; squash/stretch moves toward 1.
    """
    if name not in _PRESETS:
        raise AnimationError(f"unknown preset {name!r} (have {', '.join(NAMES)})")
    if speed <= 0:
        raise AnimationError("speed must be positive")
    if side not in ("right", "left"):
        raise AnimationError("side must be 'right' or 'left'")
    s = (height or REFERENCE_HEIGHT) / REFERENCE_HEIGHT
    anim = _PRESETS[name](s)
    if side == "left" and name == "wave":
        anim = _mirror(anim)
    for timelines in anim["bones"].values():
        for kind, keys in timelines.items():
            for k in keys:
                k["time"] = round(k["time"] / speed, 4)
                for f in ("value", "x", "y"):
                    if f in k:
                        k[f] = round(1 + (k[f] - 1) * amount if kind == "scale" else k[f] * amount, 4)
    return anim


def _mirror(anim: dict) -> dict:
    """Swap left and right bones and negate rotations (a mirrored performance)."""
    bones = {}
    for bone, timelines in anim["bones"].items():
        mirrored = {}
        for kind, keys in timelines.items():
            mirrored[kind] = [
                {**k, **({"value": -k["value"]} if kind == "rotate" else {}),
                 **({"x": -k["x"]} if kind == "translate" else {})}
                for k in keys
            ]
        bones[_MIRROR.get(bone, bone)] = mirrored
    return {**anim, "bones": bones}
