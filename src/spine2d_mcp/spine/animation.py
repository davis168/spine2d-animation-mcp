"""Animation shorthand → Spine wire format (4.2 or 4.3).

The shorthand is the wire format plus one convenience: a key may carry
`"ease"` instead of a raw `"curve"`. As in Spine, a key's easing shapes the
segment that STARTS at that key (from it to the next key). Easing names:

- `linear` (or no ease): straight interpolation
- `stepped`: hold the value until the next key
- `in`: accelerate away from the key (animators' "slow out")
- `out`: decelerate into the next key (animators' "slow in")
- `inout`: accelerate, then decelerate
- `smooth`: a gentler in-out, for breathing and sway
- `back`: overshoot the next value, then settle (slight bounce)
- `[x1, y1, x2, y2]`: any CSS cubic-bezier, normalized to the segment

Spine stores a bezier per channel as absolute (time, value) handles,
`[cx1, cy1, cx2, cy2]`, so the conversion needs both keys' values; a key with
an explicit `"curve"` is passed through untouched. A value a key leaves out
is the reader's default, which for a few timelines depends on the format
version (a transform key's mixScaleY, the physics mix timeline).
"""

from __future__ import annotations

import copy

EASES: dict[str, tuple[float, float, float, float] | str | None] = {
    "linear": None,
    "stepped": "stepped",
    "in": (0.42, 0.0, 1.0, 1.0),
    "out": (0.0, 0.0, 0.58, 1.0),
    "inout": (0.42, 0.0, 0.58, 1.0),
    "smooth": (0.3, 0.0, 0.7, 1.0),
    "back": (0.34, 1.56, 0.64, 1.0),
}


class AnimationError(ValueError):
    """The shorthand cannot be converted (unknown ease, misplaced key, ...)."""


def _value_channels(fields, defaults):
    def read(key: dict) -> list[float]:
        return [float(key.get(f, defaults.get(f, 0.0))) for f in fields]

    return read


def _color_channels(*fields):
    def read(key: dict) -> list[float]:
        out = []
        for f in fields:
            hex_ = key.get(f)
            if not isinstance(hex_, str) or len(hex_) not in (6, 8):
                raise AnimationError(f"colour key needs {f!r} as 6 or 8 hex digits, got {hex_!r}")
            out += [int(hex_[i:i + 2], 16) / 255.0 for i in range(0, len(hex_), 2)]
        return out

    return read


# Timeline -> reader of its curve channels, in the reader's channel order.
_BONE = {
    "rotate": _value_channels(("value",), {}),
    "translate": _value_channels(("x", "y"), {}),
    "translatex": _value_channels(("value",), {}),
    "translatey": _value_channels(("value",), {}),
    "scale": _value_channels(("x", "y"), {"x": 1.0, "y": 1.0}),
    "scalex": _value_channels(("value",), {"value": 1.0}),
    "scaley": _value_channels(("value",), {"value": 1.0}),
    "shear": _value_channels(("x", "y"), {}),
    "shearx": _value_channels(("value",), {}),
    "sheary": _value_channels(("value",), {}),
}
_SLOT = {
    "alpha": _value_channels(("value",), {}),
    "rgba": _color_channels("color"),
    "rgb": _color_channels("color"),
    "rgba2": _color_channels("light", "dark"),
    "rgb2": _color_channels("light", "dark"),
}
_IK = _value_channels(("mix", "softness"), {"mix": 1.0})
_PHYSICS = _value_channels(("value",), {})
_ONE = _value_channels(("value",), {"value": 1.0})  # 4.3 physics mix; slider time and mix


def _transform_mixes(version: str):
    def read(key: dict) -> list[float]:
        mix_x = float(key.get("mixX", 1.0))
        mix_scale_x = float(key.get("mixScaleX", 1.0))
        # 4.2 reads a missing mixScaleY as mixScaleX, 4.3 as 1.
        scale_y_default = mix_scale_x if version == "4.2" else 1.0
        return [
            float(key.get("mixRotate", 1.0)), mix_x, float(key.get("mixY", mix_x)),
            mix_scale_x, float(key.get("mixScaleY", scale_y_default)), float(key.get("mixShearY", 1.0)),
        ]

    return read


def _path_mixes(key: dict) -> list[float]:
    mix_x = float(key.get("mixX", 1.0))
    return [float(key.get("mixRotate", 1.0)), mix_x, float(key.get("mixY", mix_x))]


def _deform_channel(_key: dict) -> list[float]:
    raise AssertionError("deform segments are handled in _convert")


def ease_handles(ease, t0: float, t1: float, v0: float, v1: float) -> list[float] | None:
    """Absolute bezier handles for one channel of one segment."""
    spec = EASES.get(ease, ease) if isinstance(ease, str) else ease
    if isinstance(ease, str) and ease not in EASES:
        raise AnimationError(f"unknown ease {ease!r} (have {', '.join(EASES)} or [x1, y1, x2, y2])")
    if spec is None or spec == "stepped":
        return None
    if not (isinstance(spec, (list, tuple)) and len(spec) == 4):
        raise AnimationError(f"ease must be a name or [x1, y1, x2, y2], got {ease!r}")
    x1, y1, x2, y2 = (float(v) for v in spec)
    if not (0 <= x1 <= 1 and 0 <= x2 <= 1):
        raise AnimationError(f"ease {ease!r}: x1 and x2 must lie in 0..1")
    dt, dv = t1 - t0, v1 - v0
    return [round(c, 5) for c in (t0 + x1 * dt, v0 + y1 * dv, t0 + x2 * dt, v0 + y2 * dv)]


def _convert(keys, channels, path: str) -> list[dict]:
    if not isinstance(keys, list) or not all(isinstance(k, dict) for k in keys):
        raise AnimationError(f"{path}: must be a list of key objects")
    out = []
    for i, key in enumerate(keys):
        key = dict(key)
        if "angle" in key:
            raise AnimationError(f"{path}[{i}]: use 'value' for rotations ('angle' is the 3.x key and reads as 0)")
        ease = key.pop("ease", None)
        if ease is not None and "curve" in key:
            raise AnimationError(f"{path}[{i}]: give either 'ease' or 'curve', not both")
        if ease is not None and i + 1 < len(keys):
            if ease == "stepped":
                key["curve"] = "stepped"
            else:
                nxt = keys[i + 1]
                t0, t1 = float(key.get("time", 0)), float(nxt.get("time", 0))
                curve = []
                # A deform curve blends from this key's vertices (0) to the next's (1).
                pairs = [(0.0, 1.0)] if channels is _deform_channel else zip(channels(key), channels(nxt))
                for v0, v1 in pairs:
                    handles = ease_handles(ease, t0, t1, v0, v1)
                    if handles is None:
                        curve = None
                        break
                    curve += handles
                if curve:
                    key["curve"] = curve
        out.append(key)
    return out


def to_wire(timelines: dict, version: str = "4.2") -> dict:
    """Convert an animation written in the shorthand into Spine JSON of the
    given format version."""
    if not isinstance(timelines, dict):
        raise AnimationError("an animation must be an object of timeline groups")
    anim = copy.deepcopy(timelines)
    for bone, tls in (anim.get("bones") or {}).items():
        for name, keys in list((tls or {}).items()):
            if name in _BONE:
                tls[name] = _convert(keys, _BONE[name], f"bones.{bone}.{name}")
    for slot, tls in (anim.get("slots") or {}).items():
        for name, keys in list((tls or {}).items()):
            if name in _SLOT:
                tls[name] = _convert(keys, _SLOT[name], f"slots.{slot}.{name}")
    for name, keys in list((anim.get("ik") or {}).items()):
        anim["ik"][name] = _convert(keys, _IK, f"ik.{name}")
    for name, keys in list((anim.get("transform") or {}).items()):
        anim["transform"][name] = _convert(keys, _transform_mixes(version), f"transform.{name}")
    for name, tls in (anim.get("path") or {}).items():
        for tl, keys in list((tls or {}).items()):
            reader = _path_mixes if tl == "mix" else _value_channels(("value",), {})
            tls[tl] = _convert(keys, reader, f"path.{name}.{tl}")
    for name, tls in (anim.get("physics") or {}).items():
        for tl, keys in list((tls or {}).items()):
            if tl != "reset":
                reader = _ONE if tl == "mix" and version != "4.2" else _PHYSICS
                tls[tl] = _convert(keys, reader, f"physics.{name}.{tl}")
    for name, tls in (anim.get("slider") or {}).items():
        for tl, keys in list((tls or {}).items()):
            tls[tl] = _convert(keys, _ONE, f"slider.{name}.{tl}")
    for skin, slots in (anim.get("attachments") or {}).items():
        for slot, atts in (slots or {}).items():
            for att, tls in (atts or {}).items():
                if "deform" in (tls or {}):
                    tls["deform"] = _convert(tls["deform"], _deform_channel, f"attachments.{skin}.{slot}.{att}.deform")
    return anim


def rename_bones(timelines: dict, bone_map: dict[str, str]) -> dict:
    """Rename bone keys of an animation (preset bone names → the rig's)."""
    anim = copy.deepcopy(timelines)
    if "bones" in anim:
        anim["bones"] = {bone_map.get(b, b): t for b, t in anim["bones"].items()}
    return anim
