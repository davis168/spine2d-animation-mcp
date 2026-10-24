from PIL import Image

from spine2d_mcp.atlas.packer import Region, pack
from spine2d_mcp.atlas.writer import compose_pages, write_atlas_text


def test_pack_places_all_regions():
    regions = [Region("head", 64, 64), Region("body", 128, 200), Region("arm", 30, 90)]
    layout = pack(regions, max_size=512, padding=2)
    assert {r.name for r in layout.regions} == {"head", "body", "arm"}
    assert len(layout.pages) >= 1


def test_pack_preserves_source_dimensions():
    layout = pack([Region("head", 64, 48)], max_size=256)
    placed = layout.regions[0]
    assert (placed.width, placed.height) == (64, 48)


def test_pack_pages_are_pow2():
    layout = pack([Region("a", 100, 100)], max_size=512)
    w, h = layout.pages[0]
    assert w & (w - 1) == 0 and h & (h - 1) == 0


def test_pack_uses_compact_page():
    """A handful of parts must land on a small, roughly square page — not a
    tall strip down one edge of a max-size bin (10 parts came out 128x2048)."""
    regions = [Region(f"part{i}", 40, 150) for i in range(10)]
    layout = pack(regions, max_size=2048, padding=2)
    assert len(layout.pages) == 1
    w, h = layout.pages[0]
    assert max(w, h) <= 512


def test_pack_spills_to_extra_pages_when_full():
    regions = [Region(f"part{i}", 200, 200) for i in range(3)]
    layout = pack(regions, max_size=256, padding=2)
    assert len(layout.pages) == 3
    assert {r.page for r in layout.regions} == {0, 1, 2}


def test_atlas_text_format(tmp_path):
    layout = pack([Region("head", 64, 64), Region("body", 100, 120)], max_size=512)
    out = write_atlas_text(layout, "character", tmp_path / "character.atlas")
    text = out.read_text()
    lines = text.splitlines()
    assert lines[0] == "character.png"
    assert lines[1].startswith("\tsize: ")
    assert lines[2] == "\tfilter: Linear, Linear"
    # region name unindented, bounds line tab-indented
    assert "body" in lines
    assert any(l.startswith("\tbounds: ") for l in lines)


def test_compose_pages_writes_png(tmp_path):
    imgs = {
        "head": Image.new("RGBA", (64, 64), (255, 0, 0, 255)),
        "body": Image.new("RGBA", (100, 120), (0, 255, 0, 255)),
    }
    layout = pack([Region("head", 64, 64), Region("body", 100, 120)], max_size=512)
    paths = compose_pages(layout, imgs, "character", tmp_path)
    assert len(paths) == 1
    assert paths[0].exists()
    page = Image.open(paths[0])
    assert page.mode == "RGBA"
