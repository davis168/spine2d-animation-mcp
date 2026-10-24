"""Check a Spine 4.2 skeleton document against what the 4.2 reader needs.

Two kinds of finding:
- **error**: the reader throws (missing bone, slot or event, malformed mesh), or
  it loads but renders wrongly without saying so (a rotate key under `angle`
  reads as 0, a 4.1 `transform` key is ignored, a slot's setup attachment does
  not exist, a curve of the wrong length).
- **warning**: legal but probably unintended (a loop that pops at the wrap,
  bezier handles outside their segment, mesh weights that do not sum to 1).

Field names, defaults and timeline shapes follow `reference/SkeletonJson.java`
(spine-runtimes 4.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import document as d

INHERIT_MODES = {"normal", "onlyTranslation", "noRotationOrReflection", "noScale", "noScaleOrReflection"}
BLEND_MODES = {"normal", "additive", "multiply", "screen"}
ATTACHMENT_TYPES = {"region", "mesh", "linkedmesh", "boundingbox", "path", "point", "clipping"}

# Bone timeline -> (value keys, default) ; curve length is 4 per value key.
BONE_TIMELINES = {
    "rotate": (("value",), 0.0),
    "translate": (("x", "y"), 0.0),
    "translatex": (("value",), 0.0),
    "translatey": (("value",), 0.0),
    "scale": (("x", "y"), 1.0),
    "scalex": (("value",), 1.0),
    "scaley": (("value",), 1.0),
    "shear": (("x", "y"), 0.0),
    "shearx": (("value",), 0.0),
    "sheary": (("value",), 0.0),
}
# Slot colour timeline -> (colour fields with hex length, curve channels).
SLOT_COLOR_TIMELINES = {
    "rgba": ((("color", 8),), 4),
    "rgb": ((("color", 6),), 3),
    "rgba2": ((("light", 8), ("dark", 6)), 7),
    "rgb2": ((("light", 6), ("dark", 6)), 6),
}
PHYSICS_TIMELINES = {"inertia", "strength", "damping", "mass", "wind", "gravity", "mix"}
PATH_TIMELINES = {"position": 1, "spacing": 1, "mix": 3}

_HEX = re.compile(r"^[0-9a-fA-F]+$")


@dataclass(frozen=True)
class Finding:
    level: str  # "error" | "warning"
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper()} {self.path}: {self.message}"


class _Checker:
    def __init__(self, doc: dict, loops: set[str]):
        self.doc = doc
        self.loops = loops
        self.findings: list[Finding] = []
        self.bone_names: set[str] = set()
        self.slot_names: list[str] = []
        self.skin_names: set[str] = set()
        self.event_names: set[str] = set()
        self.constraint_names: dict[str, set[str]] = {k: set() for k in d.CONSTRAINT_KINDS}

    def error(self, path: str, message: str) -> None:
        self.findings.append(Finding("error", path, message))

    def warn(self, path: str, message: str) -> None:
        self.findings.append(Finding("warning", path, message))

    # ── structure ────────────────────────────────────────────────────────────

    def run(self) -> list[Finding]:
        self.header()
        self.bones()
        self.slots()
        self.constraint_defs()
        self.skins()
        self.slot_setup_attachments()
        self.events()
        self.animations()
        return self.findings

    def header(self) -> None:
        version = (self.doc.get("skeleton") or {}).get("spine")
        if not isinstance(version, str):
            self.error("skeleton.spine", "missing: the editor and runtimes pick their reader from it")
        elif not (version == d.TARGET_VERSION or version.startswith(d.TARGET_VERSION + ".")):
            self.error(
                "skeleton.spine",
                f"version {version}: this project targets Spine {d.TARGET_VERSION} "
                f"(spine-phaser-v4 4.2.x), which loads only {d.TARGET_VERSION} data",
            )
        if "constraints" in self.doc:
            self.error("constraints", "a Spine 4.3 constraints array: 4.2 reads separate ik/transform/path/physics arrays")

    def bones(self) -> None:
        bones = self.doc.get("bones")
        if not isinstance(bones, list) or not bones:
            self.error("bones", "missing or empty")
            return
        for i, bone in enumerate(bones):
            path = f"bones[{i}]"
            if not isinstance(bone, dict):
                self.error(path, "not a bone object")
                continue
            name = bone.get("name")
            if not isinstance(name, str):
                self.error(path, "bone without a name")
                continue
            path = f"bones[{name}]"
            if name in self.bone_names:
                self.error(path, "duplicate bone name")
            parent = bone.get("parent")
            if parent is None and i > 0:
                self.warn(path, "a second root bone (no parent)")
            if parent is not None and parent not in self.bone_names:
                self.error(path, f"parent {parent!r} is not a bone declared before it")
            if "transform" in bone:
                self.error(path, "'transform' is the 4.1 key; 4.2 reads 'inherit' and ignores this")
            if bone.get("inherit", "normal") not in INHERIT_MODES:
                self.error(path, f"unknown inherit {bone.get('inherit')!r}")
            for key in ("x", "y", "rotation", "scaleX", "scaleY", "shearX", "shearY", "length"):
                if key in bone and not _num(bone[key]):
                    self.error(f"{path}.{key}", "not a number")
            self.bone_names.add(name)

    def _entries(self, key: str) -> None:
        """Report a top-level array that is not a list, or holds non-objects."""
        value = self.doc.get(key)
        if value is None:
            return
        if not isinstance(value, list):
            self.error(key, "must be a list")
            return
        for i, entry in enumerate(value):
            if not isinstance(entry, dict):
                self.error(f"{key}[{i}]", "not an object")

    def slots(self) -> None:
        self._entries("slots")
        for i, slot in enumerate(d.slots(self.doc)):
            name = slot.get("name")
            path = f"slots[{name if isinstance(name, str) else i}]"
            if not isinstance(name, str):
                self.error(path, "slot without a name")
                continue
            if name in self.slot_names:
                self.error(path, "duplicate slot name")
            self.slot_names.append(name)
            if slot.get("bone") not in self.bone_names:
                self.error(path, f"bone {slot.get('bone')!r} not found")
            if slot.get("blend", "normal") not in BLEND_MODES:
                self.error(path, f"unknown blend {slot.get('blend')!r}")
            # Static colours go through Color.valueOf: 6 digits, or 8 with alpha.
            self.color(f"{path}.color", slot.get("color"), (6, 8))
            self.color(f"{path}.dark", slot.get("dark"), (6, 8))

    def constraint_defs(self) -> None:
        orders: dict[int, str] = {}
        for kind in d.CONSTRAINT_KINDS:
            self._entries(kind)
            for i, c in enumerate(d.constraints(self.doc, kind)):
                name = c.get("name")
                path = f"{kind}[{name if isinstance(name, str) else i}]"
                if not isinstance(name, str):
                    self.error(path, "constraint without a name")
                    continue
                if name in self.constraint_names[kind]:
                    self.error(path, f"duplicate {kind} constraint name")
                elif any(name in names for names in self.constraint_names.values()):
                    self.warn(path, "name shared with a constraint of another type")
                self.constraint_names[kind].add(name)
                order = c.get("order", 0)
                if not isinstance(order, int):
                    self.error(f"{path}.order", "not an integer")
                elif order in orders:
                    self.warn(f"{path}.order", f"same order {order} as {orders[order]}; apply order is then ambiguous")
                else:
                    orders[order] = path
                if kind == "physics":
                    if c.get("bone") not in self.bone_names:
                        self.error(path, f"bone {c.get('bone')!r} not found")
                    fps = c.get("fps", 60)
                    if not _num(fps) or fps <= 0 or fps != int(fps):
                        self.error(f"{path}.fps", "must be a positive whole number")
                    continue
                members = c.get("bones") or []
                if not members:
                    self.error(path, "no bones")
                for b in members:
                    if b not in self.bone_names:
                        self.error(path, f"bone {b!r} not found")
                target = c.get("target")
                if kind == "path":
                    if target not in set(self.slot_names):
                        self.error(path, f"target slot {target!r} not found")
                elif target not in self.bone_names:
                    self.error(path, f"target bone {target!r} not found")
                elif target in members:
                    self.error(path, f"target {target!r} is also one of the constrained bones")
                if kind == "ik" and len(members) > 2:
                    self.error(path, "IK takes one or two bones")
                if kind == "ik" and len(members) == 2:
                    parent = d.bone_map(self.doc).get(members[1], {}).get("parent")
                    if parent != members[0]:
                        self.error(path, f"second IK bone {members[1]!r} must be a child of {members[0]!r}")

    def skins(self) -> None:
        skins = self.doc.get("skins")
        if skins is None:
            return
        if not isinstance(skins, list):
            self.error("skins", "must be a list of skins (the 3.x object form is not read by 4.x)")
            return
        slot_set = set(self.slot_names)
        for i, skin in enumerate(skins):
            if not isinstance(skin, dict):
                self.error(f"skins[{i}]", "not a skin object")
                continue
            name = skin.get("name")
            path = f"skins[{name if isinstance(name, str) else i}]"
            if not isinstance(name, str):
                self.error(path, "skin without a name: the reader requires it, even for 'default'")
                continue
            if name in self.skin_names:
                self.error(path, "duplicate skin name")
            self.skin_names.add(name)
            for b in skin.get("bones") or []:
                if b not in self.bone_names:
                    self.error(f"{path}.bones", f"bone {b!r} not found")
            for kind in d.CONSTRAINT_KINDS:
                for c in skin.get(kind) or []:
                    if c not in self.constraint_names[kind]:
                        self.error(f"{path}.{kind}", f"{kind} constraint {c!r} not found")
            attachments = skin.get("attachments") or {}
            if not isinstance(attachments, dict):
                self.error(f"{path}.attachments", "must map slot names to attachments")
                continue
            for slot, atts in attachments.items():
                if slot not in slot_set:
                    self.error(f"{path}.attachments", f"slot {slot!r} not found")
                    continue
                if not isinstance(atts, dict):
                    self.error(f"{path}.attachments.{slot}", "must map attachment names to attachments")
                    continue
                for att_name, att in atts.items():
                    apath = f"{path}.attachments.{slot}.{att_name}"
                    if not isinstance(att, dict):
                        self.error(apath, "not an attachment object")
                        continue
                    self.attachment(apath, skin, slot, att)
        if skins and "default" not in self.skin_names:
            self.warn("skins", "no 'default' skin: slots show nothing until a skin is set at runtime")

    def attachment(self, path: str, skin: dict, slot: str, att: dict) -> None:
        kind = att.get("type", "region")
        if kind not in ATTACHMENT_TYPES:
            self.error(path, f"unknown attachment type {kind!r}")
            return
        if kind == "region":
            for key in ("width", "height"):
                if not _num(att.get(key)):
                    self.error(path, f"region needs a numeric {key} (the reader has no default)")
            self.color(f"{path}.color", att.get("color"), (6, 8))
        elif kind == "mesh":
            self.mesh(path, att)
        elif kind == "linkedmesh":
            parent = att.get("parent")
            owner = d.find_skin(self.doc, att["skin"]) if att.get("skin") else d.find_skin(self.doc, "default")
            if owner is None:
                self.error(path, f"skin {att.get('skin', 'default')!r} for the parent mesh not found")
            elif parent not in ((owner.get("attachments") or {}).get(slot) or {}):
                self.error(path, f"parent mesh {parent!r} not found in skin {owner.get('name')!r}")
        elif kind == "clipping":
            end = att.get("end")
            if end is not None and end not in self.slot_names:
                self.error(path, f"clipping end slot {end!r} not found")
            self.vertices(path, att, att.get("vertexCount"))
        elif kind in ("boundingbox", "path"):
            self.vertices(path, att, att.get("vertexCount"))
            if kind == "path":
                lengths, count = att.get("lengths"), att.get("vertexCount")
                if not isinstance(lengths, list):
                    self.error(path, "path needs a lengths array (the reader requires it)")
                elif isinstance(count, int) and len(lengths) != count // 3:
                    self.error(path, f"lengths has {len(lengths)} entries, expected vertexCount / 3 = {count // 3}")

    def mesh(self, path: str, att: dict) -> None:
        uvs, triangles = att.get("uvs"), att.get("triangles")
        if not isinstance(uvs, list) or len(uvs) % 2:
            self.error(path, "mesh needs an even-length uvs array")
            return
        count = len(uvs) // 2
        if any(not (0 <= u <= 1) for u in uvs if _num(u)):
            self.warn(path, "uvs outside 0..1")
        if not isinstance(triangles, list) or len(triangles) % 3:
            self.error(path, "mesh needs a triangles array (indices in threes)")
        elif any(not isinstance(t, int) or not 0 <= t < count for t in triangles):
            self.error(path, f"triangle index outside 0..{count - 1}")
        hull = att.get("hull")
        if hull is not None and (not isinstance(hull, int) or hull > count):
            self.error(path, f"hull {hull} larger than the {count} vertices")
        self.vertices(path, att, count)

    def vertices(self, path: str, att: dict, count) -> None:
        vertices = att.get("vertices")
        if not isinstance(vertices, list) or not isinstance(count, int):
            self.error(path, "needs vertices and a vertex count")
            return
        if len(vertices) == count * 2:
            return  # unweighted: x, y per vertex in the slot bone's space
        bone_count = len(d.bones(self.doc))
        i, seen = 0, 0
        while i < len(vertices) and seen < count:
            n = vertices[i]
            if not isinstance(n, int) or n < 1:
                self.error(path, f"weighted vertex {seen}: bad influence count {n!r}")
                return
            total = 0.0
            for j in range(n):
                k = i + 1 + j * 4
                if k + 3 >= len(vertices):
                    self.error(path, "weighted vertices end mid-record")
                    return
                bone_index, weight = vertices[k], vertices[k + 3]
                if not isinstance(bone_index, int) or not 0 <= bone_index < bone_count:
                    self.error(path, f"weighted vertex {seen}: bone index {bone_index!r} out of range")
                total += weight if _num(weight) else 0
            if abs(total - 1) > 0.01:
                self.warn(path, f"weighted vertex {seen}: weights sum to {total:.3f}, not 1")
            i += 1 + n * 4
            seen += 1
        if seen != count or i != len(vertices):
            self.error(path, f"vertices hold {seen} weighted vertices, expected {count}")

    def slot_setup_attachments(self) -> None:
        for slot in d.slots(self.doc):
            att = slot.get("attachment")
            if att is not None and att not in d.attachment_names(self.doc, slot.get("name")):
                self.error(f"slots[{slot.get('name')}].attachment", f"{att!r} is in no skin: the slot renders nothing")

    def events(self) -> None:
        events = self.doc.get("events") or {}
        if not isinstance(events, dict):
            self.error("events", "must map event names to their defaults")
            return
        for name, value in events.items():
            if not isinstance(value, dict):
                self.error(f"events.{name}", "must be an object of defaults ({} for none)")
        self.event_names = set(events)

    # ── animations ───────────────────────────────────────────────────────────

    def _obj(self, path: str, value) -> dict | None:
        """The value if it is an object; otherwise report it and return None."""
        if value is None:
            return {}
        if not isinstance(value, dict):
            self.error(path, "must be an object")
            return None
        return value

    def animations(self) -> None:
        animations = self.doc.get("animations") or {}
        if not isinstance(animations, dict):
            self.error("animations", "must map animation names to animations")
            return
        for name in self.loops - set(animations):
            self.error(f"animations.{name}", "named as a loop but does not exist")
        for name, anim in animations.items():
            anim = self._obj(f"animations.{name}", anim)
            if anim is not None:
                self.animation(f"animations.{name}", name, anim)

    def _groups(self, path: str, anim: dict, group: str):
        """(name, timelines-object, path) for each entry of a name -> {timeline: keys} group."""
        entries = self._obj(f"{path}.{group}", anim.get(group))
        for name, timelines in (entries or {}).items():
            tpath = f"{path}.{group}.{name}"
            timelines = self._obj(tpath, timelines)
            if timelines is not None:
                yield name, timelines, tpath

    def animation(self, path: str, name: str, anim: dict) -> None:
        duration = d.animation_duration(anim)
        loop = name in self.loops
        for bone, timelines, bpath in self._groups(path, anim, "bones"):
            if bone not in self.bone_names:
                self.error(bpath, "bone not found")
                continue
            for tname, keys in timelines.items():
                tpath = f"{bpath}.{tname}"
                if tname == "inherit":
                    if self.keys(tpath, keys):
                        for k in keys:
                            if k.get("inherit", "normal") not in INHERIT_MODES:
                                self.error(tpath, f"unknown inherit {k.get('inherit')!r}")
                    continue
                if tname not in BONE_TIMELINES:
                    self.error(tpath, f"unknown bone timeline (have {', '.join(BONE_TIMELINES)})")
                    continue
                fields, default = BONE_TIMELINES[tname]
                if fields == ("value",) and isinstance(keys, list):
                    for i, k in enumerate(keys):
                        if isinstance(k, dict) and "angle" in k:
                            self.error(f"{tpath}[{i}]", "'angle' is the 3.x key: 4.2 reads 'value' and treats this key as 0")
                self.curve_keys(tpath, keys, fields, default, duration, loop)
        for slot, timelines, spath in self._groups(path, anim, "slots"):
            if slot not in self.slot_names:
                self.error(spath, "slot not found")
                continue
            for tname, keys in timelines.items():
                tpath = f"{spath}.{tname}"
                if tname == "attachment":
                    if self.keys(tpath, keys):
                        names = d.attachment_names(self.doc, slot)
                        for i, k in enumerate(keys):
                            if k.get("name") is not None and k["name"] not in names:
                                self.error(f"{tpath}[{i}]", f"attachment {k['name']!r} is in no skin for this slot")
                elif tname == "alpha":
                    self.curve_keys(tpath, keys, ("value",), 0.0, duration, loop)
                elif tname in SLOT_COLOR_TIMELINES:
                    if not self.keys(tpath, keys):
                        continue
                    colors, channels = SLOT_COLOR_TIMELINES[tname]
                    for i, k in enumerate(keys):
                        for field, length in colors:
                            if field not in k:
                                self.error(f"{tpath}[{i}]", f"needs {field!r} (no default)")
                            else:
                                self.color(f"{tpath}[{i}].{field}", k[field], (length,))
                    self.curve_shape(tpath, keys, channels)
                else:
                    self.error(tpath, "unknown slot timeline (have attachment, rgba, rgb, alpha, rgba2, rgb2)")
        for kind, shapes in (("ik", 2), ("transform", 6)):
            group = self._obj(f"{path}.{kind}", anim.get(kind))
            for cname, keys in (group or {}).items():
                cpath = f"{path}.{kind}.{cname}"
                if cname not in self.constraint_names[kind]:
                    self.error(cpath, f"{kind} constraint not found")
                    continue
                if self.keys(cpath, keys):
                    self.curve_shape(cpath, keys, shapes)
        for cname, timelines, cpath in self._groups(path, anim, "path"):
            if cname not in self.constraint_names["path"]:
                self.error(cpath, "path constraint not found")
                continue
            for tname, keys in timelines.items():
                if tname in PATH_TIMELINES and self.keys(f"{cpath}.{tname}", keys):
                    self.curve_shape(f"{cpath}.{tname}", keys, PATH_TIMELINES[tname])
        for cname, timelines, cpath in self._groups(path, anim, "physics"):
            if cname and cname not in self.constraint_names["physics"]:
                self.error(cpath, "physics constraint not found")
                continue
            for tname, keys in timelines.items():
                if tname == "reset":
                    self.keys(f"{cpath}.reset", keys)
                elif tname in PHYSICS_TIMELINES:
                    self.curve_keys(f"{cpath}.{tname}", keys, ("value",), 0.0, duration, False)
                else:
                    self.warn(f"{cpath}.{tname}", "unknown physics timeline: the reader skips it")
        self.deforms(path, anim)
        self.draw_order(path, anim.get("drawOrder"))
        events = anim.get("events")
        if events is not None and self.keys(f"{path}.events", events):
            for i, k in enumerate(events):
                if k.get("name") not in self.event_names:
                    self.error(f"{path}.events[{i}]", f"event {k.get('name')!r} not defined in the root 'events'")

    def deforms(self, path: str, anim: dict) -> None:
        skins = self._obj(f"{path}.attachments", anim.get("attachments"))
        for skin_name, slots in (skins or {}).items():
            spath = f"{path}.attachments.{skin_name}"
            skin = d.find_skin(self.doc, skin_name)
            if skin is None:
                self.error(spath, "skin not found")
                continue
            for slot, atts in (self._obj(spath, slots) or {}).items():
                for att_name, timelines in (self._obj(f"{spath}.{slot}", atts) or {}).items():
                    apath = f"{spath}.{slot}.{att_name}"
                    att = (((skin.get("attachments") or {}).get(slot)) or {}).get(att_name)
                    if not isinstance(att, dict):
                        self.error(apath, "attachment not found in that skin and slot")
                        continue
                    for tname, keys in (self._obj(apath, timelines) or {}).items():
                        if not self.keys(f"{apath}.{tname}", keys) or tname != "deform":
                            continue
                        if att.get("type") not in ("mesh", "linkedmesh", "boundingbox", "path", "clipping"):
                            self.error(apath, "deform keys on an attachment without vertices")
                            continue
                        self.curve_shape(f"{apath}.deform", keys, 1)
                        length = _deform_length(att)
                        for i, k in enumerate(keys):
                            offset, verts = k.get("offset", 0), k.get("vertices") or []
                            if length is not None and offset + len(verts) > length:
                                self.error(f"{apath}.deform[{i}]", f"offset + vertices = {offset + len(verts)} exceeds {length}")

    def draw_order(self, path: str, keys) -> None:
        """The reader walks offsets in slot order: they must name slots in
        increasing setup index (else it loops forever in JS or throws in Java),
        and no two may land on the same draw position."""
        if keys is None or not self.keys(f"{path}.drawOrder", keys):
            return
        count = len(self.slot_names)
        index = {name: i for i, name in enumerate(self.slot_names)}
        for i, k in enumerate(keys):
            kpath = f"{path}.drawOrder[{i}]"
            offsets = k.get("offsets") or []
            if not isinstance(offsets, list) or not all(isinstance(o, dict) for o in offsets):
                self.error(kpath, "offsets must be a list of {slot, offset} objects")
                continue
            last, taken = -1, set()
            for o in offsets:
                slot, offset = o.get("slot"), o.get("offset", 0)
                if slot not in index:
                    self.error(kpath, f"slot {slot!r} not found")
                    continue
                if index[slot] <= last:
                    self.error(kpath, f"offsets must list slots in setup order; {slot!r} comes too late")
                last = index[slot]
                target = index[slot] + offset if isinstance(offset, int) else None
                if target is None or not 0 <= target < count:
                    self.error(kpath, f"offset moves {slot!r} outside the draw order")
                elif target in taken:
                    self.error(kpath, f"two slots moved to draw position {target}")
                else:
                    taken.add(target)

    # ── keys and curves ──────────────────────────────────────────────────────

    def keys(self, path: str, keys) -> bool:
        if not isinstance(keys, list) or not all(isinstance(k, dict) for k in keys):
            self.error(path, "must be a list of key objects")
            return False
        last = None
        for i, k in enumerate(keys):
            t = k.get("time", 0)
            if not _num(t) or t < 0:
                self.error(f"{path}[{i}].time", f"bad time {t!r}")
                continue
            if last is not None and t < last:
                self.error(f"{path}[{i}].time", f"time {t} goes back from {last}")
            elif last is not None and t == last:
                self.warn(f"{path}[{i}].time", f"two keys at time {t}")
            last = t
        return True

    def curve_keys(self, path, keys, fields, default, duration, loop) -> None:
        if not self.keys(path, keys):
            return
        for i, k in enumerate(keys):
            for f in fields:
                if f in k and not _num(k[f]):
                    self.error(f"{path}[{i}].{f}", "not a number")
        self.curve_shape(path, keys, len(fields))
        if loop and len(keys) > 1:
            first = [keys[0].get(f, default) for f in fields]
            last = [keys[-1].get(f, default) for f in fields]
            if any(abs(a - b) > 1e-3 for a, b in zip(first, last) if _num(a) and _num(b)):
                self.warn(path, f"loop pops at the wrap: starts at {first}, ends at {last}")

    def curve_shape(self, path, keys, channels) -> None:
        for i, k in enumerate(keys or []):
            curve = k.get("curve")
            if curve is None or curve == "stepped":
                continue
            kpath = f"{path}[{i}].curve"
            if isinstance(curve, str):
                self.error(kpath, f"unknown curve {curve!r} (only 'stepped' or bezier numbers)")
                continue
            if not isinstance(curve, list) or len(curve) != 4 * channels or not all(_num(c) for c in curve):
                self.error(kpath, f"needs {4 * channels} numbers (cx1, cy1, cx2, cy2 per channel), got {curve!r}")
                continue
            if i == len(keys) - 1:
                self.warn(kpath, "curve on the last key shapes nothing")
                continue
            t0, t1 = k.get("time", 0), keys[i + 1].get("time", 0)
            for ch in range(channels):
                cx1, cx2 = curve[4 * ch], curve[4 * ch + 2]
                if not (t0 - 1e-6 <= cx1 <= t1 + 1e-6 and t0 - 1e-6 <= cx2 <= t1 + 1e-6):
                    self.warn(kpath, f"handle times outside the segment {t0}..{t1}: Spine curves use absolute times")
                    break

    def color(self, path: str, value, lengths: tuple[int, ...]) -> None:
        if value is None:
            return
        if not isinstance(value, str) or len(value) not in lengths or not _HEX.match(value):
            self.error(path, f"needs {' or '.join(map(str, lengths))} hex digits, got {value!r}")


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _deform_length(att: dict) -> int | None:
    uvs = att.get("uvs")
    if isinstance(uvs, list):
        return len(uvs)
    count = att.get("vertexCount")
    return count * 2 if isinstance(count, int) else None


def check(doc: dict, loops: list[str] | None = None) -> list[Finding]:
    """All findings, errors first."""
    findings = _Checker(doc, set(loops or [])).run()
    return sorted(findings, key=lambda f: f.level != "error")


def summary(doc: dict) -> str:
    version = (doc.get("skeleton") or {}).get("spine", "?")
    meshes = sum(
        1
        for skin in d.skins(doc)
        for atts in (skin.get("attachments") or {}).values()
        for att in (atts or {}).values()
        if (att or {}).get("type") in ("mesh", "linkedmesh")
    )
    attachments = sum(len(atts or {}) for skin in d.skins(doc) for atts in (skin.get("attachments") or {}).values())
    constraints = len(d.all_constraints(doc))
    return (
        f"spine {version} | {len(d.bones(doc))} bones, {len(d.slots(doc))} slots, "
        f"{len(d.skins(doc))} skins, {attachments} attachments ({meshes} meshes), "
        f"{constraints} constraints, {len(doc.get('animations') or {})} animations"
    )
