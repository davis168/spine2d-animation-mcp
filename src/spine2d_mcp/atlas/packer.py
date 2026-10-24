"""Bin-pack region images into one or more atlas pages.

PoC scope: no rotation (keeps the writer and Unity import simpler), power-of-two
pages capped at `max_size`. Everything goes on the smallest single page that
fits; only if a max-size page is not enough are more pages added.
"""

from __future__ import annotations

from dataclasses import dataclass

from rectpack import newPacker


@dataclass
class Region:
    name: str
    width: int
    height: int


@dataclass
class PlacedRegion:
    name: str
    x: int
    y: int
    width: int
    height: int
    page: int


@dataclass
class AtlasLayout:
    pages: list[tuple[int, int]]  # (width, height) per page
    regions: list[PlacedRegion]


def pack(
    regions: list[Region],
    max_size: int = 2048,
    padding: int = 2,
) -> AtlasLayout:
    """Pack regions into pages. `padding` is added around each region to avoid
    edge bleeding when Unity samples sprite borders."""
    if not regions:
        return AtlasLayout(pages=[], regions=[])

    # Packing straight into a max-size bin strings small parts down one edge
    # (a 128x2048 page for a 10-part character), so try the smallest single
    # page first.
    area = sum((r.width + 2 * padding) * (r.height + 2 * padding) for r in regions)
    for w, h in _page_sizes(max_size):
        if w * h < area:
            continue
        layout = _pack_into(regions, w, h, bins=1, padding=padding)
        if len(layout.regions) == len(regions):
            return layout

    # Offer enough identical max-size bins that everything fits.
    layout = _pack_into(regions, max_size, max_size, bins=len(regions), padding=padding)
    if len(layout.regions) != len(regions):
        missing = {r.name for r in regions} - {p.name for p in layout.regions}
        raise ValueError(f"Regions did not fit in atlas: {sorted(missing)}")
    return layout


def _page_sizes(max_size: int) -> list[tuple[int, int]]:
    """Power-of-two (w, h) pages up to max_size, smallest area first, the most
    square shape first among equal areas."""
    sides = []
    side = 16
    while side <= max_size:
        sides.append(side)
        side <<= 1
    sizes = [(w, h) for w in sides for h in sides]
    return sorted(sizes, key=lambda wh: (wh[0] * wh[1], abs(wh[0] - wh[1]), -wh[0]))


def _pack_into(
    regions: list[Region], page_w: int, page_h: int, bins: int, padding: int
) -> AtlasLayout:
    """Pack into `bins` pages of page_w x page_h. Regions that don't fit are
    left out of the result; callers compare counts."""
    packer = newPacker(rotation=False)
    for r in regions:
        packer.add_rect(r.width + 2 * padding, r.height + 2 * padding, rid=r.name)
    for _ in range(bins):
        packer.add_bin(page_w, page_h)
    packer.pack()

    by_name = {r.name: r for r in regions}
    placed: list[PlacedRegion] = []
    used_pages: dict[int, tuple[int, int]] = {}
    for page_idx, x, y, w, h, rid in packer.rect_list():
        src = by_name[rid]
        placed.append(
            PlacedRegion(
                name=rid,
                x=x + padding,
                y=y + padding,
                width=src.width,
                height=src.height,
                page=page_idx,
            )
        )
        pw, ph = used_pages.get(page_idx, (0, 0))
        used_pages[page_idx] = (
            max(pw, x + w),
            max(ph, y + h),
        )

    pages = [
        _pow2(used_pages[i][0], used_pages[i][1])
        for i in sorted(used_pages)
    ]
    return AtlasLayout(pages=pages, regions=placed)


def _pow2(w: int, h: int) -> tuple[int, int]:
    def up(n: int) -> int:
        p = 1
        while p < n:
            p <<= 1
        return max(p, 1)

    return up(w), up(h)
