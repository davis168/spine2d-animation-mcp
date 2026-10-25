"""Parse a PSD into a flat list of pixel layers.

Logic adapted from the original prototype `src/psd_parser.py` (verified to work
with psd-tools), simplified to a flat structure: groups are flattened, only
visible pixel layers with a bbox are returned. Coordinates are PSD-native
(origin top-left, y-down); the builder converts to Spine y-up.

Layer order CONTRACT: `ParsedPsd.layers` is bottom-to-top in PSD z-order
(psd-tools iterates a node bottom-up). The builder relies on this for Spine
slot draw order, so this ordering must be preserved.

Pivot-override marker layers (`<part>[pivot]`) are split out into
`pivot_markers` and not treated as body parts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image
from psd_tools import PSDImage

from .conventions import pivot_marker_part


@dataclass
class PsdLayer:
    name: str
    # bbox in PSD coordinates: top-left origin, y-down.
    left: int
    top: int
    width: int
    height: int
    image: Image.Image

    @property
    def center(self) -> tuple[float, float]:
        return self.left + self.width / 2.0, self.top + self.height / 2.0


@dataclass
class ParsedPsd:
    width: int
    height: int
    layers: list[PsdLayer]  # bottom-to-top z-order
    # canonical part name -> PSD-space center of its pivot marker
    pivot_markers: dict[str, tuple[float, float]] = field(default_factory=dict)


def parse_psd(file_path: str) -> ParsedPsd:
    psd = PSDImage.open(file_path)
    parsed = ParsedPsd(width=psd.width, height=psd.height, layers=[])
    _collect(psd, parsed)
    return parsed


def _collect(node, parsed: ParsedPsd) -> None:
    for layer in node:
        if not layer.is_visible():
            continue
        if layer.is_group():
            _collect(layer, parsed)
            continue
        if not layer.has_pixels():
            continue
        image = layer.composite()
        if image is None:
            continue
        left, top, right, bottom = layer.bbox

        marker_part = pivot_marker_part(layer.name)
        if marker_part is not None:
            cx = left + (right - left) / 2.0
            cy = top + (bottom - top) / 2.0
            parsed.pivot_markers[marker_part] = (cx, cy)
            continue

        parsed.layers.append(
            PsdLayer(
                name=layer.name,
                left=left,
                top=top,
                width=right - left,
                height=bottom - top,
                image=image,
            )
        )
