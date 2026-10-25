"""Read a PSD's layer tree the way the Spine editor's Import PSD does.

Group and layer names carry tags such as `[bone]`, `[slot:name]`, `[skin]`
(https://esotericsoftware.com/spine-import-psd). This module checks the tags
and resolves, for every layer that becomes an attachment, its slot, skin,
placeholder, attachment name, image path, bone and image options. Where the
documentation is silent it follows Esoteric's PhotoshopToSpine script, which
the editor's importer replaced.

Order CONTRACT: `TaggedPsd.layers` is bottom-to-top (psd-tools order), the
order slots are created in, so draw order follows the layer panel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from psd_tools import PSDImage
from psd_tools.api.layers import AdjustmentLayer
from psd_tools.constants import BlendMode, Resource

TAG = re.compile(r"\[([^\]]*)\]")

# Tags valid on both groups and layers.
_ANY = {"bone", "slot", "skin", "scale", "rotate", "pad", "folder", "overlay", "trim", "ignore",
        "!bones", "!slots", "!name", "!path", "!bone", "!slot"}
# Layer tags: also valid on [merge] groups.
_LAYER = {"mesh", "path", "origin"}
# Group tags. `name` always takes a pattern; `path`, `bone` and `slot` take one on a group.
_GROUP = {"merge", "bones", "slots", "name"}
_NO_VALUE = {"overlay", "ignore", "origin", "merge", "bones", "slots"} | {t for t in _ANY if t.startswith("!")}
_NEEDS_VALUE = {"scale", "rotate", "pad", "path", "name"}
_TRIM = {None: "ws", "true": "ws", "ws": "ws", "false": "canvas", "canvas": "canvas", "mask": "mask"}

BLENDS = {BlendMode.LINEAR_DODGE: "additive", BlendMode.MULTIPLY: "multiply", BlendMode.SCREEN: "screen"}


class PsdTagError(ValueError):
    """The PSD's tags are invalid or contradict each other; every problem is listed."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("the PSD's Spine tags have problems:\n- " + "\n- ".join(errors))


@dataclass(eq=False)
class Item:
    """A PSD layer or group as the importer sees it."""

    layer: object  # psd_tools Layer
    raw_name: str  # the name with its tags removed
    tags: dict[str, str | None]
    parent: Item | None
    children: list[Item] = field(default_factory=list)  # bottom-to-top

    @property
    def is_group(self) -> bool:
        return self.layer.is_group()

    @property
    def path(self) -> str:
        """Where the item is in the layer panel, for messages."""
        names, node = [], self
        while node is not None:
            names.append(node.layer.name)
            node = node.parent
        return " / ".join(reversed(names))

    def nearest(self, tag: str) -> Item | None:
        """This item or its closest ancestor with `tag` (a pattern value does not count)."""
        node = self
        while node is not None:
            if tag in node.tags and not _is_pattern(node.tags[tag]):
                return node
            node = node.parent
        return None


@dataclass(eq=False)
class TaggedLayer:
    """A layer (or [merge] group) that becomes an attachment."""

    item: Item
    name: str  # layer name after [name:*] patterns
    attachment: str
    placeholder: str
    skin: str  # "default" when no [skin] applies
    path: str  # image path, without .png
    slot: str
    bone: str | None  # None: the root bone
    visible: bool
    mesh: bool | str  # True: a mesh; a str: a linked mesh's source name
    scale: float
    rotate: float
    pad: int
    trim: str  # "ws", "canvas" or "mask"
    blend: str | None
    overlays: list[Item]  # [overlay] layers above it, top first


@dataclass
class BoneDef:
    name: str
    parent: str | None  # None: the root bone
    layers: list[TaggedLayer] = field(default_factory=list)  # layers on this bone, bottom-to-top


@dataclass
class TaggedPsd:
    psd: PSDImage
    width: int
    height: int
    origin: tuple[float, float] | Item  # canvas point (y-down), or the [origin] layer to centre on
    layers: list[TaggedLayer]  # bottom-to-top
    bones: dict[str, BoneDef]  # parents before children
    warnings: list[str]


def tag_names(name: str) -> list[str]:
    return [t.split(":", 1)[0].strip().lower() for t in TAG.findall(name)]


def has_spine_tags(psd: PSDImage) -> bool:
    """Whether any layer name uses an Import PSD tag (the body-part rigger's `[pivot]` does not count)."""
    known = _ANY | _LAYER | _GROUP
    return any(t in known for layer in psd.descendants() for t in tag_names(layer.name))


def read(psd: PSDImage, *, padding: int = 1, trim: bool = True, ignore_hidden: bool = False) -> TaggedPsd:
    """Resolve every attachment layer's properties; PsdTagError lists every problem found."""
    errors: list[str] = []
    warnings: list[str] = []
    roots = _items(psd, None, errors)

    found: list[tuple[Item, list[Item]]] = []
    overlays: list[Item] = []
    origins: list[Item] = []
    _collect(reversed(roots), found, overlays, origins, ignore_hidden)  # top-down, like the layer panel
    found.reverse()

    layers: list[TaggedLayer] = []
    for item, above in found:
        layer = _resolve(item, above, padding, trim, errors)
        if layer is not None:
            layers.append(layer)

    bones = _bones(layers, errors)
    _check_placeholders(layers, errors)
    if errors:
        raise PsdTagError(errors)

    if len(origins) > 1:
        warnings.append(f"{len(origins)} [origin] layers; the top one sets the origin: {origins[0].path}")
    origin = origins[0] if origins else _guides_origin(psd)
    return TaggedPsd(psd, psd.width, psd.height, origin, layers, bones, warnings)


def _items(node, parent: Item | None, errors: list[str]) -> list[Item]:
    items = []
    for layer in node:
        if "ignore" in tag_names(layer.name):  # skipped whole, its tags unread (as in Spine)
            items.append(Item(layer, _clean(layer.name), {"ignore": None}, parent))
            continue
        item = Item(layer, _clean(layer.name), _parse_tags(layer, errors), parent)
        if layer.is_group() and "merge" not in item.tags:
            item.children = _items(layer, item, errors)
        items.append(item)
    return items


def _parse_tags(layer, errors: list[str]) -> dict[str, str | None]:
    tags: dict[str, str | None] = {}
    names, node = [], layer
    while node is not None and not isinstance(node, PSDImage):
        names.append(node.name)
        node = node.parent
    where = " / ".join(reversed(names))
    group = layer.is_group()
    merge = group and "merge" in tag_names(layer.name)
    for text in TAG.findall(layer.name):
        name, _, value = text.partition(":")
        name, value = name.strip().lower(), (value.strip() or None) if ":" in text else None
        if name in tags:
            errors.append(f"{where}: [{name}] appears twice")
            continue
        pattern = _is_pattern(value)
        if name not in _ANY | _LAYER | _GROUP:
            errors.append(f"{where}: [{text}] is not an Import PSD tag")
            continue
        if name in _NO_VALUE and value is not None:
            errors.append(f"{where}: [{name}] takes no value")
            continue
        if name in _NEEDS_VALUE and value is None:
            errors.append(f"{where}: [{name}] needs a value, e.g. [{name}:{_example(name)}]")
            continue
        if name == "name" and not pattern:
            errors.append(f"{where}: [name:{value}] needs a * where the layer name goes, e.g. [name:arm-*]")
            continue
        if pattern and name not in ("name", "path", "bone", "slot"):
            errors.append(f"{where}: [{text}] cannot be a pattern")
            continue
        if pattern and not group:
            errors.append(f"{where}: the pattern [{text}] goes on a group")
            continue
        if name in _GROUP and not group and name != "merge":  # [merge] on a layer: already merged by hand
            errors.append(f"{where}: [{name}] goes on a group")
            continue
        if name in _LAYER and group and not merge and not pattern:
            errors.append(f"{where}: [{name}] goes on a layer or a [merge] group")
            continue
        if not _valid_value(name, value):
            errors.append(f"{where}: [{text}] has an invalid value")
            continue
        tags[name] = value
    return tags


def _example(tag: str) -> str:
    return {"scale": "0.5", "rotate": "90", "pad": "2", "path": "name", "name": "prefix-*"}[tag]


def _valid_value(tag: str, value: str | None) -> bool:
    try:
        if tag == "scale":
            return float(value) > 0
        if tag == "rotate":
            float(value)
        if tag == "pad":
            return int(value) >= 0
    except ValueError:
        return False
    if tag == "trim":
        return (value.lower() if value else None) in _TRIM
    return True


def _is_pattern(value: str | None) -> bool:
    return value is not None and "*" in value


def _clean(name: str) -> str:
    name = TAG.sub("", name).strip()
    if name.lower().endswith(".png"):
        name = name[:-4]
    return re.sub(r'[\\:"*?<>|]', "", name).strip()


def _collect(items, found, overlays, origins, ignore_hidden: bool) -> None:
    """Walk top-down; gather attachment layers with the [overlay] layers above them."""
    for item in items:
        layer = item.layer
        if "ignore" in item.tags or (ignore_hidden and not layer.visible):
            continue
        if isinstance(layer, AdjustmentLayer) or getattr(layer, "clipping", False):
            continue  # clipping layers are drawn into the layer they clip
        if "overlay" in item.tags:
            if layer.is_visible():
                overlays.append(item)
            continue
        if "origin" in item.tags:
            origins.append(item)
            continue
        if item.is_group and "merge" not in item.tags:
            _collect(reversed(item.children), found, overlays, origins, ignore_hidden)
            continue
        found.append((item, list(overlays)))


def _patterns(item: Item, tag: str) -> list[str]:
    """[tag:*] patterns from the groups above `item`, closest first; [!tag] stops them."""
    out, node = [], item
    while node is not None and f"!{tag}" not in node.tags:
        node = node.parent
        if node is not None and _is_pattern(node.tags.get(tag)):
            out.append(node.tags[tag])
    return out


def _apply(value: str, patterns: list[str]) -> str:
    for pattern in patterns:
        value = pattern.replace("*", value, 1)
    return value


def name_of(item: Item) -> str:
    """The item's name after the [name:*] patterns above it (a leading / opts out)."""
    if item.raw_name.startswith("/"):
        return item.raw_name
    return _apply(item.raw_name, _patterns(item, "name"))


def _value(item: Item, tag: str) -> str:
    """A tag's value, or the item's name when the tag has none."""
    value = item.tags.get(tag)
    return value if value is not None else name_of(item)


def _folders(item: Item | None) -> str:
    """The [skin] and [folder] values from `item` up, as a path prefix ("a/b/" or "")."""
    prefix, node = "", item
    while node is not None:
        tag = "skin" if "skin" in node.tags else "folder" if "folder" in node.tags else None
        if tag is not None:
            value = _value(node, tag)
            if value.startswith("/"):
                return value[1:] + "/" + prefix
            prefix = value + "/" + prefix
        node = node.parent
    return prefix


def _bone_item(item: Item) -> Item | None:
    """The closest item that is a bone: [bone], or an immediate child of a [bones] group."""
    node = item
    while node is not None:
        if _is_bone(node):
            return node
        node = node.parent
    return None


def _is_bone(item: Item) -> bool:
    if "bone" in item.tags and not _is_pattern(item.tags["bone"]):
        return True
    return item.parent is not None and "bones" in item.parent.tags and "!bones" not in item.tags


def _bone_name(item: Item) -> str:
    value = item.tags.get("bone")
    value = value if value is not None and not _is_pattern(value) else name_of(item)
    if value.startswith("/"):
        return value[1:]
    return _apply(value, _patterns(item, "bone"))


def _slot_item(item: Item) -> Item | None:
    node = item
    while node is not None:
        if "slot" in node.tags and not _is_pattern(node.tags["slot"]):
            return node
        if node.parent is not None and "slots" in node.parent.tags and "!slots" not in node.tags:
            return node
        node = node.parent
    return None


def _resolve(item: Item, overlays: list[Item], padding: int, trim: bool, errors: list[str]) -> TaggedLayer | None:
    name = name_of(item)
    if not name.strip("/"):
        errors.append(f"{item.path}: the layer has no name to name its attachment")
        return None
    if name.startswith("/"):
        attachment = name[1:]
    else:
        attachment = _folders(item) + name

    skin_item = item.nearest("skin")
    skin = "default"
    if skin_item is not None:
        skin = _value(skin_item, "skin")
        skin = skin[1:] if skin.startswith("/") else _folders(skin_item.parent) + skin
        if skin.lower() == "default":
            errors.append(f"{item.path}: \"default\" is the default skin's name; layers outside any [skin] go in it")
            return None
    if skin == "default":
        placeholder = attachment
    elif attachment.startswith(skin + "/"):
        placeholder = attachment[len(skin) + 1:]
    else:
        errors.append(f"{item.path}: attachment {attachment!r} is in skin {skin!r} but not under its folder "
                      f"(a leading / in the layer name takes it out of the skin)")
        return None

    explicit = item.tags.get("path")
    path_patterns = _patterns(item, "path")
    if explicit is not None and explicit.startswith("/"):
        path = explicit[1:]
    elif explicit is not None or path_patterns:
        path = _folders(item) + _apply(explicit if explicit is not None else item.raw_name, path_patterns)
    else:
        path = attachment

    slot_item = _slot_item(item)
    slot = _value(slot_item, "slot") if slot_item is not None else name
    if slot.startswith("/"):
        slot = slot[1:]
    else:
        slot = _apply(slot, _patterns(slot_item or item, "slot"))

    bone_item = _bone_item(item)
    mesh = (item.tags["mesh"] or True) if "mesh" in item.tags else False
    trim_item = item.nearest("trim")
    trim_mode = _TRIM[(trim_item.tags["trim"] or "").lower() or None] if trim_item else None
    if trim_mode is None or (trim_mode == "mask" and not _has_mask(item.layer)):
        trim_mode = "ws" if trim else "canvas"  # [trim:mask] without a layer mask is ignored
    return TaggedLayer(
        item=item, name=name, attachment=attachment, placeholder=placeholder, skin=skin, path=path, slot=slot,
        bone=_bone_name(bone_item) if bone_item is not None else None,
        visible=item.layer.is_visible(),
        mesh=mesh,
        scale=float(_tag(item, "scale", 1)),
        rotate=float(_tag(item, "rotate", 0)),
        pad=int(_tag(item, "pad", padding)),
        trim=trim_mode,
        blend=_blend(item),
        overlays=overlays,
    )


def _has_mask(layer) -> bool:
    return layer.has_mask() and not layer.mask.disabled


def _tag(item: Item, tag: str, default):
    node = item.nearest(tag)
    return node.tags[tag] if node is not None else default


def _blend(item: Item) -> str | None:
    node = item
    while node is not None:
        mode = node.layer.blend_mode
        if mode in BLENDS:
            return BLENDS[mode]
        if mode not in (BlendMode.NORMAL, BlendMode.PASS_THROUGH):
            return None
        node = node.parent
    return None


def _bones(layers: list[TaggedLayer], errors: list[str]) -> dict[str, BoneDef]:
    """Every bone a layer is on, parents first; a bone named twice must have one parent."""
    bones: dict[str, BoneDef] = {}
    where: dict[str, Item] = {}
    defined: dict[Item, str] = {}

    def define(item: Item) -> str:
        if item in defined:
            return defined[item]
        name = defined[item] = _bone_name(item)
        parent_item = _bone_item(item.parent) if item.parent is not None else None
        parent = define(parent_item) if parent_item is not None else None
        if name == "root":
            errors.append(f"{item.path}: \"root\" is the skeleton's root bone; name the bone something else")
        elif name in bones and bones[name].parent != parent:
            errors.append(f"bone {name!r} has two parents, {bones[name].parent or 'root'!r} at {where[name].path} "
                          f"and {parent or 'root'!r} at {item.path}")
        elif name not in bones:
            bones[name] = BoneDef(name, parent)
            where[name] = item
        return name

    for layer in layers:
        item = _bone_item(layer.item)
        if item is not None and define(item) in bones:
            bones[define(item)].layers.append(layer)
    return bones


def _check_placeholders(layers: list[TaggedLayer], errors: list[str]) -> None:
    seen: dict[tuple[str, str, str], TaggedLayer] = {}
    default: dict[tuple[str, str], TaggedLayer] = {}
    for layer in layers:
        key = (layer.skin, layer.slot, layer.placeholder)
        if key in seen:
            errors.append(f"skin {layer.skin!r} has two {layer.placeholder!r} attachments in slot {layer.slot!r}: "
                          f"{seen[key].item.path} and {layer.item.path} (rename one)")
        seen[key] = layer
        if layer.skin == "default":
            default[(layer.slot, layer.placeholder)] = layer
    for (skin, slot, placeholder), layer in seen.items():
        clash = default.get((slot, placeholder))
        if skin != "default" and clash is not None:
            errors.append(f"slot {slot!r}: {placeholder!r} is both a skin placeholder ({layer.item.path}) and a "
                          f"default skin attachment ({clash.item.path}); Spine refuses that (rename one)")


def _guides_origin(psd: PSDImage) -> tuple[float, float]:
    """The first vertical and horizontal guides, else the canvas centre (canvas coordinates, y-down)."""
    x, y = psd.width / 2, psd.height / 2
    info = psd.image_resources.get_data(Resource.GRID_AND_GUIDES_INFO)
    vertical = horizontal = None
    for location, direction in getattr(info, "data", None) or []:
        if direction == 0 and vertical is None:
            vertical = location / 32
        elif direction == 1 and horizontal is None:
            horizontal = location / 32
    return (x if vertical is None else vertical, y if horizontal is None else horizontal)
