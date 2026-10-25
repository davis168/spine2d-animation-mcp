"""Check a Spine skeleton document against what its format version's reader
needs (4.2 or 4.3, from the `skeleton.spine` header).

Two kinds of finding:
- **error**: the reader throws (missing bone, slot or event, malformed mesh), or
  it loads but renders wrongly without saying so (a rotate key under `angle`
  reads as 0, a 4.1 `transform` key is ignored, a slot's setup attachment does
  not exist, a curve of the wrong length, a key only the other format version
  reads).
- **warning**: legal but probably unintended (a loop that pops at the wrap,
  bezier handles outside their segment, mesh weights that do not sum to 1).

Field names, defaults and timeline shapes follow `reference/SkeletonJson-4.2.java`
and `reference/SkeletonJson-4.3.java` (spine-runtimes 4.2 and 4.3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import document as d
from . import versions

INHERIT_MODES = {"normal", "onlyTranslation", "noRotationOrReflection", "noScale", "noScaleOrReflection"}
BLEND_MODES = {"normal", "additive", "multiply", "screen"}
ATTACHMENT_TYPES = {"region", "mesh", "linkedmesh", "boundingbox", "path", "point", "clipping"}
SCALE_Y_MODES = {"none", "uniform", "volume"}  # 4.3 IK and physics `scaleY`
# 4.3 transform constraint properties (from -> to) and the mix each `to` reads.
TRANSFORM_PROPERTIES = ("rotate", "x", "y", "scaleX", "scaleY", "shearY")
MIX_OF = {"rotate": "mixRotate", "x": "mixX", "y": "mixY", "scaleX": "mixScaleX", "scaleY": "mixScaleY",
          "shearY": "mixShearY"}

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
SLIDER_TIMELINES = {"time", "mix"}
PATH_MODES = {"positionMode": ("fixed", "percent"), "spacingMode": ("length", "fixed", "percent", "proportional"),
              "rotateMode": ("tangent", "chain", "chainScale")}
SEQUENCE_MODES = {"hold", "once", "loop", "pingpong", "onceReverse", "loopReverse", "pingpongReverse"}

# Keys only the other format version reads, per constraint kind: the reader
# ignores them, so a truthy value means the rig behaves other than written.
_FOREIGN_KEYS = {
    "4.2": {"ik": ("scaleY",), "transform": ("source", "properties", "localSource", "localTarget", "additive", "clamp"),
            "path": ("slot",), "physics": ("scaleY",)},
    "4.3": {"ik": ("uniform",), "transform": ("target", "local", "relative"), "path": ("target",)},
}
_RENAMED = {("4.3", "ik", "uniform"): "scaleY: \"uniform\"", ("4.3", "transform", "target"): "source",
            ("4.3", "transform", "local"): "localSource and localTarget",
            ("4.3", "transform", "relative"): "additive", ("4.3", "path", "target"): "slot"}

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
        self.version = versions.layout(doc)  # the reader the rest of the checks follow
        self.kinds = versions.FORMATS[self.version].constraint_kinds
        self.findings: list[Finding] = []
        self.bone_names: set[str] = set()
        self.slot_names: list[str] = []
        self.folders: dict[str, list[str]] = {}
        self.skin_names: set[str] = set()
        self.event_names: set[str] = set()
        self.constraint_names: dict[str, set[str]] = {k: set() for k in d.CONSTRAINT_KINDS}

    def error(self, path: str, message: str) -> None:
        self.findings.append(Finding("error", path, message))

    def warn(self, path: str, message: str) -> None:
        self.findings.append(Finding("warning", path, message))

    # ── structure ────────────────────────────────────────────────────────────

    def run(self) -> list[Finding]:
        if not self.header():
            return self.findings
        self.bones()
        self.slots()
        self.constraint_defs()
        self.skins()
        self.slot_setup_attachments()
        self.events()
        self.animations()
        return self.findings

    def header(self) -> bool:
        """False when the version is one no supported reader can check."""
        raw = (self.doc.get("skeleton") or {}).get("spine")
        if not isinstance(raw, str):
            self.error("skeleton.spine", "missing: the editor and runtimes pick their reader from it")
        elif not versions.is_supported(versions.major_minor(raw)):
            self.error("skeleton.spine",
                       f"unsupported format version {raw} (supported: {versions.supported_text()}); "
                       "it is not checked further")
            return False
        header = self.doc.get("skeleton") or {}
        for key in ("referenceScale", "fps", "x", "y", "width", "height"):
            if key in header and not _num(header[key]):
                self.error(f"skeleton.{key}", "not a number")
        for key in ("referenceScale", "fps"):
            if _num(header.get(key, 1)) and header.get(key, 1) <= 0:
                self.error(f"skeleton.{key}", "must be above 0")
        if self.version == "4.2" and "constraints" in self.doc:
            self.error("constraints", "a 4.3 constraints array: 4.2 reads separate ik/transform/path/physics "
                                      "arrays and ignores this (convert_skeleton converts between versions)")
        if self.version == "4.3":
            for kind in ("ik", "transform", "path", "physics"):
                if kind in self.doc:
                    self.error(kind, f"a 4.2 {kind!r} array: 4.3 reads one 'constraints' array and ignores "
                                     "this, so these constraints are lost (convert_skeleton converts 4.2 data)")
        return True

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
                self.error(path, "'transform' is the 4.1 key; 4.2 and later read 'inherit' and ignore this")
            if bone.get("inherit", "normal") not in INHERIT_MODES:
                self.error(path, f"unknown inherit {bone.get('inherit')!r}")
            for key in ("x", "y", "rotation", "scaleX", "scaleY", "shearX", "shearY", "length", "iconSize",
                        "iconRotation"):
                if key in bone and not _num(bone[key]):
                    self.error(f"{path}.{key}", "not a number")
            if self.version == "4.2":
                for key in ("iconSize", "iconRotation"):
                    if key in bone:
                        self.warn(f"{path}.{key}", "4.3 editor data: the 4.2 editor ignores it")
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
        self.folders = d.draw_order_folders(self.slot_names)
        for folder in d.split_folders(self.slot_names):
            self.warn("slots", f"draw order folder {folder!r} ({', '.join(self.folders[folder])}) is not together in "
                               "the draw order: the Spine editor pulls its slots together on import, which "
                               "changes the draw order")

    # ── constraints ──────────────────────────────────────────────────────────

    def constraint_defs(self) -> None:
        if self.version == "4.2":
            # The runtime applies, for each i in 0..n-1, the first constraint
            # whose order is i: any other order never runs.
            orders: dict[int, str] = {}
            count = len(d.all_constraints(self.doc))
            for kind in self.kinds:
                self._entries(kind)
                for i, c in enumerate(d.constraints(self.doc, kind)):
                    path = self.constraint_name(kind, c, i)
                    if path is None:
                        continue
                    order = c.get("order", 0)
                    if not isinstance(order, int) or isinstance(order, bool):
                        self.error(f"{path}.order", "not an integer, so the constraint never runs")
                    elif not 0 <= order < count:
                        self.error(f"{path}.order", f"{order} is outside 0..{count - 1}: 4.2 runs only orders "
                                                    "0 to n-1 (n constraints), so this constraint never runs")
                    elif order in orders:
                        self.error(f"{path}.order", f"same order {order} as {orders[order]}: 4.2 runs only the "
                                                    "first of them, so this constraint never runs")
                    else:
                        orders[order] = path
                    self.constraint(kind, path, c)
            return
        self._entries("constraints")
        entries = self.doc.get("constraints")
        for i, c in enumerate(entries if isinstance(entries, list) else []):
            if not isinstance(c, dict):
                continue  # reported by _entries
            kind = c.get("type")
            if kind not in self.kinds:
                self.error(f"constraints[{i}]", f"unknown type {kind!r} (have {', '.join(self.kinds)})")
                continue
            path = self.constraint_name(kind, c, i, prefix="constraints")
            if path is None:
                continue
            if "order" in c:
                self.warn(f"{path}.order", "4.3 ignores 'order': constraints apply in array order")
            self.constraint(kind, path, c)

    def constraint_name(self, kind: str, c: dict, i: int, prefix: str | None = None) -> str | None:
        name = c.get("name")
        path = f"{prefix or kind}[{name if isinstance(name, str) else i}]"
        if not isinstance(name, str):
            self.error(path, "constraint without a name")
            return None
        if name in self.constraint_names[kind]:
            self.error(path, f"duplicate {kind} constraint name")
        elif any(name in names for names in self.constraint_names.values()):
            self.warn(path, "name shared with a constraint of another type")
        self.constraint_names[kind].add(name)
        return path

    def constraint(self, kind: str, path: str, c: dict) -> None:
        for key in _FOREIGN_KEYS.get(self.version, {}).get(kind, ()):
            if c.get(key) not in (None, False, "none"):
                other = "4.3" if self.version == "4.2" else "4.2"
                instead = _RENAMED.get((self.version, kind, key))
                self.error(f"{path}.{key}", f"a {other} key: {self.version} ignores it"
                                            + (f" (it reads {instead})" if instead else ""))
        if kind in ("ik", "physics") and self.version == "4.3":
            if c.get("scaleY", "none") not in SCALE_Y_MODES:
                self.error(f"{path}.scaleY", f"unknown scaleY {c.get('scaleY')!r} (have none, uniform, volume)")
        if kind == "physics":
            if c.get("bone") not in self.bone_names:
                self.error(path, f"bone {c.get('bone')!r} not found")
            fps = c.get("fps", 60)
            if not _num(fps) or fps <= 0 or fps != int(fps):
                self.error(f"{path}.fps", "must be a positive whole number")
            return
        if kind == "slider":
            self.slider(path, c)
            return
        members = c.get("bones") or []
        if not members:
            self.error(path, "no bones")
        for b in members:
            if b not in self.bone_names:
                self.error(path, f"bone {b!r} not found")
        if kind == "path":
            key = d.path_slot_key(self.doc)
            if c.get(key) not in set(self.slot_names):
                self.error(path, f"{'target slot' if key == 'target' else 'slot'} {c.get(key)!r} not found")
            for mode, allowed in PATH_MODES.items():
                if mode in c and c[mode] not in allowed:
                    self.error(f"{path}.{mode}", f"unknown {mode} {c[mode]!r} (have {', '.join(allowed)})")
            return
        key = d.target_bone_key(self.doc, kind)
        target = c.get(key)
        if target not in self.bone_names:
            self.error(path, f"{key} bone {target!r} not found")
        elif target in members:
            self.error(path, f"{key} {target!r} is also one of the constrained bones")
        if kind == "ik" and len(members) > 2:
            self.error(path, "IK takes one or two bones")
        if kind == "ik" and len(members) == 2:
            parent = d.bone_map(self.doc).get(members[1], {}).get("parent")
            if parent != members[0]:
                self.error(path, f"second IK bone {members[1]!r} must be a child of {members[0]!r}")
        if kind == "transform" and self.version == "4.3":
            self.transform_properties(path, c)

    def transform_properties(self, path: str, c: dict) -> None:
        props = c.get("properties")
        if props is None:
            props = {}
        if not isinstance(props, dict):
            self.error(f"{path}.properties", "must map source properties to {offset, to}")
            return
        mapped: set[str] = set()
        for source, spec in props.items():
            ppath = f"{path}.properties.{source}"
            if source not in TRANSFORM_PROPERTIES:
                self.error(ppath, f"unknown source property (have {', '.join(TRANSFORM_PROPERTIES)})")
                continue
            if not isinstance(spec, dict):
                self.error(ppath, "must be an object with 'to'")
                continue
            if "offset" in spec and not _num(spec["offset"]):
                self.error(f"{ppath}.offset", "not a number")
            to = spec.get("to") or {}
            if not isinstance(to, dict):
                self.error(f"{ppath}.to", "must map target properties to {offset, max, scale}")
                continue
            for target, tspec in to.items():
                tpath = f"{ppath}.to.{target}"
                if target not in TRANSFORM_PROPERTIES:
                    self.error(tpath, f"unknown target property (have {', '.join(TRANSFORM_PROPERTIES)})")
                    continue
                if not isinstance(tspec, dict):
                    self.error(tpath, "must be an object (offset, max, scale)")
                    continue
                for key in ("offset", "max", "scale"):
                    if key in tspec and not _num(tspec[key]):
                        self.error(f"{tpath}.{key}", "not a number")
                mapped.add(target)
        if not mapped:
            self.warn(path, "maps no properties, so it does nothing")
        for target, mix in MIX_OF.items():
            if mix in c and target not in mapped:
                self.warn(f"{path}.{mix}", f"ignored: no property maps to {target}")

    def slider(self, path: str, c: dict) -> None:
        animation = c.get("animation")
        if animation not in (self.doc.get("animations") or {}):
            self.error(path, f"animation {animation!r} not found")
        bone = c.get("bone")
        if bone is None:
            if "time" in c and not _num(c["time"]):
                self.error(f"{path}.time", "not a number")
            return
        if bone not in self.bone_names:
            self.error(path, f"bone {bone!r} not found")
        if c.get("property") not in TRANSFORM_PROPERTIES:
            self.error(f"{path}.property", f"a bone-driven slider needs property, one of {', '.join(TRANSFORM_PROPERTIES)}")
        for key in ("from", "to", "scale", "max", "mix"):
            if key in c and not _num(c[key]):
                self.error(f"{path}.{key}", "not a number")

    # ── skins and attachments ────────────────────────────────────────────────

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
            for kind in self.kinds:
                for c in skin.get(kind) or []:
                    if c not in self.constraint_names[kind]:
                        self.error(f"{path}.{kind}", f"{kind} constraint {c!r} not found")
            if self.version == "4.2" and skin.get("slider"):
                self.error(f"{path}.slider", "a 4.3 key: 4.2 has no sliders and ignores it")
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
        for c, used in d.tipless_physics(self.doc):
            self.warn(f"physics[{c.get('name')}]",
                      f"{', '.join(used)} on bone {c.get('bone')!r}, which has no length: they swing the bone's tip, "
                      "so the Spine editor drops them on import; give the bone a length (edit_rig set_bone length)")
        for skin, slot, name in d.default_skin_clashes(self.doc):
            self.error(f"skins[{skin}].attachments.{slot}.{name}",
                       f"{name!r} is also in the default skin's {slot!r} slot: runtimes show this skin's, but the "
                       "Spine editor refuses to import it (\"Multiple attachments have the same name\"), since a "
                       "name a skin uses is a skin placeholder, which the default skin cannot also hold; move it "
                       "out of the default skin into skins (edit_skin move_to_skins) or use another name here")
        if skins and "default" not in self.skin_names:
            self.warn("skins", "no 'default' skin: slots show nothing until a skin is set at runtime")
        listed = {n for skin in skins if isinstance(skin, dict) for key in ("bones", *d.CONSTRAINT_KINDS)
                  for n in skin.get(key) or [] if isinstance(n, str)}
        for i, bone in enumerate(d.bones(self.doc)):
            if bone.get("skin") and bone.get("name") not in listed:
                self.warn(f"bones[{bone.get('name')}]", "a skin bone no skin lists: it is never active")
        for kind, c in d.all_constraints(self.doc):
            if c.get("skin") and c.get("name") not in listed:
                self.warn(f"{kind}[{c.get('name')}]", "a skin constraint no skin lists: it never applies")

    def attachment(self, path: str, skin: dict, slot: str, att: dict) -> None:
        kind = att.get("type", "region")
        if kind not in ATTACHMENT_TYPES:
            self.error(path, f"unknown attachment type {kind!r}")
            return
        if "sequence" in att:
            if kind in ("region", "mesh", "linkedmesh"):
                self.sequence(f"{path}.sequence", att["sequence"])
            else:
                self.warn(f"{path}.sequence", f"a {kind} attachment has no sequence: the reader ignores it")
        if kind == "point":
            for key in ("x", "y", "rotation"):
                if key in att and not _num(att[key]):
                    self.error(f"{path}.{key}", "not a number")
        if kind == "linkedmesh" and not isinstance(att.get("timelines", True), bool):
            self.error(f"{path}.timelines", "must be true or false")
        if kind == "region":
            for key in ("width", "height"):
                if not _num(att.get(key)):
                    self.error(path, f"region needs a numeric {key} (the reader has no default)")
            self.color(f"{path}.color", att.get("color"), (6, 8))
        elif kind == "mesh":
            self.mesh(path, att)
        elif kind == "linkedmesh":
            self.linked_mesh(path, slot, att)
        elif kind == "clipping":
            end = att.get("end")
            if end is not None and end not in self.slot_names:
                self.error(path, f"clipping end slot {end!r} not found")
            if self.version == "4.2":
                for key in ("convex", "inverse"):
                    if att.get(key):
                        self.error(f"{path}.{key}", "a 4.3 key: 4.2 ignores it, so the clip shape differs")
            self.vertices(path, att, att.get("vertexCount"))
            count, vertices = att.get("vertexCount"), att.get("vertices")
            if isinstance(count, int) and count < 3:
                self.warn(path, "a clipping polygon with fewer than 3 points has no area: what it covers vanishes")
            elif (self.version == "4.3" and att.get("convex") and isinstance(vertices, list)
                  and len(vertices) == 2 * count and all(_num(v) for v in vertices)
                  and not d.is_convex(list(zip(vertices[::2], vertices[1::2])))):
                self.warn(f"{path}.convex", "the polygon is not convex, so a convex clip cuts it wrongly")
        elif kind in ("boundingbox", "path"):
            self.vertices(path, att, att.get("vertexCount"))
            if kind == "path":
                lengths, count = att.get("lengths"), att.get("vertexCount")
                if not isinstance(lengths, list):
                    self.error(path, "path needs a lengths array (the reader requires it)")
                elif isinstance(count, int) and len(lengths) != count // 3:
                    self.error(path, f"lengths has {len(lengths)} entries, expected vertexCount / 3 = {count // 3}")

    def linked_mesh(self, path: str, slot: str, att: dict) -> None:
        key = d.linked_mesh_key(self.doc)
        other = "source" if key == "parent" else "parent"
        source = att.get(key)
        if source is None:
            if att.get(other) is not None:
                self.error(path, f"{other!r} is the {'4.3' if key == 'parent' else '4.2'} key: {self.version} reads "
                                 f"{key!r}, so this reads as a plain mesh without uvs and fails")
            else:
                self.error(path, f"linked mesh without {key!r}")
            return
        source_slot = slot
        if att.get("slot") is not None:
            if self.version == "4.2":
                self.error(f"{path}.slot", "a 4.3 key: 4.2 looks the source mesh up in this slot only")
            elif att["slot"] not in self.slot_names:
                self.error(f"{path}.slot", f"source mesh slot {att['slot']!r} not found")
                return
            else:
                source_slot = att["slot"]
        owner = d.find_skin(self.doc, att["skin"]) if att.get("skin") else d.find_skin(self.doc, "default")
        if owner is None:
            self.error(path, f"skin {att.get('skin', 'default')!r} for the {key} mesh not found")
        elif source not in ((owner.get("attachments") or {}).get(source_slot) or {}):
            self.error(path, f"{key} mesh {source!r} not found in skin {owner.get('name')!r}, slot {source_slot!r}")

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
        elif isinstance(hull, int) and hull < 3:
            self.warn(path, f"hull of {hull} vertices: a mesh border needs at least 3")
        edges = att.get("edges")
        if edges is not None and (not isinstance(edges, list) or len(edges) % 2
                                  or any(not isinstance(e, int) or e % 2 or not 0 <= e // 2 < count for e in edges)):
            self.warn(f"{path}.edges", "edges must be pairs of vertex indices times 2 (editor data)")
        self.vertices(path, att, count)

    def sequence(self, path: str, seq) -> None:
        if not isinstance(seq, dict) or not isinstance(seq.get("count"), int) or seq["count"] < 1:
            self.error(path, "needs a count of 1 or more (the reader requires it)")
            return
        for key in ("start", "digits", "setup"):
            if key in seq and (not isinstance(seq[key], int) or seq[key] < 0):
                self.error(f"{path}.{key}", "must be a whole number, 0 or more")
        if isinstance(seq.get("setup", 0), int) and seq.get("setup", 0) >= seq["count"]:
            self.error(f"{path}.setup", f"must be below count ({seq['count']})")

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
            n = vertices[i]  # the editor writes counts and bone indices as floats (2.0) in 4.3
            if not _whole(n) or n < 1:
                self.error(path, f"weighted vertex {seen}: bad influence count {n!r}")
                return
            n = int(n)
            total = 0.0
            for j in range(n):
                k = i + 1 + j * 4
                if k + 3 >= len(vertices):
                    self.error(path, "weighted vertices end mid-record")
                    return
                bone_index, weight = vertices[k], vertices[k + 3]
                if not _whole(bone_index) or not 0 <= bone_index < bone_count:
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
                continue
            if "int" in value and not _whole(value["int"]):
                self.error(f"events.{name}.int", "must be a whole number")
            for key in ("float", "volume", "balance"):
                if key in value and not _num(value[key]):
                    self.error(f"events.{name}.{key}", "not a number")
            for key in ("string", "audio"):
                if key in value and not isinstance(value[key], str):
                    self.error(f"events.{name}.{key}", "must be text")
            if ("volume" in value or "balance" in value) and "audio" not in value:
                self.warn(f"events.{name}", "volume and balance are read only with audio")
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
        if "color" in anim:
            if self.version == "4.2":
                self.warn(f"{path}.color", "4.3 editor data: the 4.2 editor ignores it")
            else:
                self.color(f"{path}.color", anim["color"], (6, 8))
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
                            self.error(f"{tpath}[{i}]", "'angle' is the 3.x key: 4.x reads 'value' and treats this key as 0")
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
        if anim.get("slider") and self.version == "4.2":
            self.error(f"{path}.slider", "4.3 slider timelines: 4.2 has no sliders and ignores them")
        elif self.version != "4.2":
            for cname, timelines, cpath in self._groups(path, anim, "slider"):
                if cname not in self.constraint_names["slider"]:
                    self.error(cpath, "slider not found")
                    continue
                for tname, keys in timelines.items():
                    if tname in SLIDER_TIMELINES:
                        self.curve_keys(f"{cpath}.{tname}", keys, ("value",), 1.0, duration, False)
                    else:
                        self.warn(f"{cpath}.{tname}", "unknown slider timeline: the reader skips it")
        self.deforms(path, anim)
        self.draw_order(f"{path}.drawOrder", anim.get("drawOrder"), self.slot_names)
        self.folder_keys(path, anim.get("drawOrderFolder"))
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
                        if not self.keys(f"{apath}.{tname}", keys):
                            continue
                        if tname == "sequence":
                            self.sequence_keys(f"{apath}.sequence", att, keys)
                            continue
                        if tname != "deform":
                            self.warn(f"{apath}.{tname}", "unknown attachment timeline: the reader skips it")
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

    def sequence_keys(self, path: str, att: dict, keys: list) -> None:
        kind = att.get("type", "region")
        if kind not in ("region", "mesh", "linkedmesh"):
            self.error(path, f"sequence keys on a {kind} attachment, which has no images (the reader fails)")
            return
        if not isinstance(att.get("sequence"), dict):
            self.warn(path, "the attachment has no sequence, so these keys change nothing")
        count = (att.get("sequence") or {}).get("count") if isinstance(att.get("sequence"), dict) else None
        for i, k in enumerate(keys):
            if k.get("mode", "hold") not in SEQUENCE_MODES:
                self.error(f"{path}[{i}].mode", f"unknown mode {k.get('mode')!r} (have {', '.join(sorted(SEQUENCE_MODES))})")
            index = k.get("index", 0)
            if not isinstance(index, int) or index < 0 or (isinstance(count, int) and index >= count):
                self.error(f"{path}[{i}].index", f"must be an image number 0..{count - 1 if count else 'count-1'}")
            if "delay" in k and (not _num(k["delay"]) or k["delay"] < 0):
                self.error(f"{path}[{i}].delay", "must be seconds, 0 or more")

    def draw_order(self, path: str, keys, order: list[str]) -> None:
        """The reader walks offsets in slot order: they must name slots in
        increasing position (else it loops forever in JS or throws in Java),
        and no two may land on the same draw position. `order` is every slot,
        or a 4.3 draw order folder's slots."""
        if keys is None or not self.keys(path, keys):
            return
        count = len(order)
        index = {name: i for i, name in enumerate(order)}
        for i, k in enumerate(keys):
            kpath = f"{path}[{i}]"
            offsets = k.get("offsets") or []
            if not isinstance(offsets, list) or not all(isinstance(o, dict) for o in offsets):
                self.error(kpath, "offsets must be a list of {slot, offset} objects")
                continue
            last, taken = -1, set()
            for o in offsets:
                slot, offset = o.get("slot"), o.get("offset", 0)
                if slot not in index:
                    self.error(kpath, f"slot {slot!r} not found" if order is self.slot_names
                               else f"slot {slot!r} is not in the folder")
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

    def folder_keys(self, path: str, folders) -> None:
        if folders is None:
            return
        fpath = f"{path}.drawOrderFolder"
        if self.version == "4.2":
            self.error(fpath, "4.3 draw order folders: 4.2 ignores them")
            return
        if not isinstance(folders, list):
            self.error(fpath, "must be a list of {slots, keys} (the runtime iterates it)")
            return
        keyed: set[str] = set()
        for i, folder in enumerate(folders):
            npath = f"{fpath}[{i}]"
            if not isinstance(folder, dict) or not isinstance(folder.get("slots"), list):
                self.error(npath, "needs 'slots' (the folder's slots) and 'keys'")
                continue
            slots = folder["slots"]
            missing = [s for s in slots if s not in self.slot_names]
            if missing:
                self.error(f"{npath}.slots", f"slots not found: {', '.join(map(str, missing))}")
                continue
            in_setup_order = [s for s in self.slot_names if s in slots]
            if len(set(slots)) != len(slots):
                self.error(f"{npath}.slots", "a slot is listed twice")
            elif slots != in_setup_order:
                self.error(f"{npath}.slots", f"must list the slots in setup draw order ({', '.join(in_setup_order)}): "
                                             "the runtime counts the keys' offsets that way")
            name = d.folder_of(self.slot_names, slots)
            if name is None:
                self.warn(f"{npath}.slots", "not the slots of one draw order folder (the slots named 'folder/...'): "
                                            "the Spine editor drops these keys on import")
            elif name in keyed:
                self.warn(npath, f"folder {name!r} is keyed twice; the later timeline wins")
            elif "/" in name:
                self.warn(npath, f"keys nested folder {name!r}: the Spine 4.3 editor (4.3.26) drops them on "
                                 "import (spine_export puts them back); the runtime plays them")
            if name:
                keyed.add(name)
            if "keys" not in folder:
                self.error(npath, "needs 'keys' (the reader requires them)")
                continue
            self.draw_order(f"{npath}.keys", folder["keys"], in_setup_order)

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


def _whole(value) -> bool:
    return _num(value) and value == int(value)


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
