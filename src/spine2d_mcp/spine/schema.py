"""Pydantic models for the Spine skeleton JSON the PSD importer writes.

The importer builds bones, slots and region skins, which read the same in
format 4.2 and 4.3; only the `skeleton.spine` header differs. (`ik` is the
4.2 layout and stays empty: constraints are added later by the editing tools,
which write each version's layout.)

Field names and defaults mirror the canonical readers
`reference/SkeletonJson-4.2.java` and `-4.3.java`. The Spine editor's reader
rejects unknown fields and is sensitive to exact names, so every field here is
verified against that reader. Serialization must use
`model_dump(by_alias=True, exclude_defaults=True, exclude_none=True)` — the
editor tolerates omitted fields (it applies the same defaults) but chokes on
redundant default-valued fields.

Notable 4.x specifics:
- Bone transform-inheritance field is `inherit` (default "normal"), NOT
  `transform` as in older versions.
- Rotate animation keyframes store the angle under `value` (not `angle`, the
  3.x key, nor `rotation`); the reader silently treats a missing `value` as 0.
  Animations are written by `spine/animation.py`.
- Region attachment `width`/`height` are read without a default → required.
- Skin `name` is read without a default → required (even for "default").
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .versions import NEWEST


class _SpineModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class Inherit(str, Enum):
    normal = "normal"
    onlyTranslation = "onlyTranslation"
    noRotationOrReflection = "noRotationOrReflection"
    noScale = "noScale"
    noScaleOrReflection = "noScaleOrReflection"


class BlendMode(str, Enum):
    normal = "normal"
    additive = "additive"
    multiply = "multiply"
    screen = "screen"


class Skeleton(_SpineModel):
    """The `skeleton` header object. `spine` version string is required for the
    editor to pick the right reader."""

    spine: str = NEWEST
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    images: str | None = None
    audio: str | None = None
    # referenceScale/fps/hash exist in the reader but default cleanly; omit.


class Bone(_SpineModel):
    name: str
    parent: str | None = None
    length: float = 0.0
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    scaleX: float = 1.0
    scaleY: float = 1.0
    shearX: float = 0.0
    shearY: float = 0.0
    inherit: Inherit = Inherit.normal
    color: str | None = None


class Slot(_SpineModel):
    name: str
    bone: str
    color: str | None = None
    dark: str | None = None
    attachment: str | None = None
    blend: BlendMode = BlendMode.normal


class IkConstraint(_SpineModel):
    name: str
    order: int = 0
    bones: list[str]
    target: str
    mix: float = 1.0
    softness: float = 0.0
    bendPositive: bool = True
    compress: bool = False
    stretch: bool = False
    uniform: bool = False


class RegionAttachment(_SpineModel):
    """A flat image attached to a bone. `type` defaults to "region" in the
    reader; we omit it for region attachments. `width`/`height` are required."""

    width: float
    height: float
    # `path` defaults to the attachment name in the reader; set only when the
    # atlas region name differs from the attachment key.
    path: str | None = None
    x: float = 0.0
    y: float = 0.0
    scaleX: float = 1.0
    scaleY: float = 1.0
    rotation: float = 0.0
    color: str | None = None


# A skin maps slot name -> attachment name -> attachment.
SkinAttachments = dict[str, dict[str, RegionAttachment]]


class Skin(_SpineModel):
    # Required, with no default: the reader reads `name` without a default, and
    # a "default" default would be dropped by exclude_defaults on serialization.
    name: str
    attachments: SkinAttachments = Field(default_factory=dict)


class SpineProject(_SpineModel):
    """Top-level skeleton document."""

    skeleton: Skeleton = Field(default_factory=Skeleton)
    bones: list[Bone] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)
    ik: list[IkConstraint] = Field(default_factory=list)
    skins: list[Skin] = Field(default_factory=list)
