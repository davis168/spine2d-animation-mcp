"""Synthetic PSD fixture so the pipeline is testable without a real PSD.

Builds a minimal humanoid from colored rectangles with body-part layer names,
laid out roughly anatomically in PSD (y-down) coordinates.
"""

from __future__ import annotations

import pytest
from PIL import Image
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer

# (name, left, top, width, height) in PSD coords (y-down, origin top-left).
# Canvas 400x600; head on top, body center, arms/legs around it.
_PARTS = [
    ("head", 160, 20, 80, 80),
    ("body", 150, 110, 100, 180),
    ("arm_left", 100, 120, 40, 140),
    ("arm_right", 260, 120, 40, 140),
    ("hand_left", 95, 260, 40, 40),
    ("hand_right", 265, 260, 40, 40),
    ("leg_left", 150, 300, 45, 180),
    ("leg_right", 205, 300, 45, 180),
    ("foot_left", 145, 480, 60, 30),
    ("foot_right", 205, 480, 60, 30),
]

_COLORS = [
    (220, 100, 100, 255), (100, 180, 220, 255), (120, 200, 120, 255),
    (200, 200, 100, 255), (200, 120, 200, 255), (120, 200, 200, 255),
    (180, 140, 100, 255), (140, 140, 200, 255), (200, 160, 120, 255),
    (160, 200, 160, 255),
]

CANVAS = (400, 600)


def _save_psd(tmp_path, parts):
    """parts: list of (name, left, top, w, h, color). Appended in order, so
    psd-tools z-order (bottom-to-top) matches list order."""
    psd = PSDImage.new("RGBA", CANVAS, color=(0, 0, 0, 0))
    for name, left, top, w, h, color in parts:
        tile = Image.new("RGBA", (w, h), color)
        psd.append(PixelLayer.frompil(tile, psd, name, top=top, left=left))
    path = tmp_path / "test_character.psd"
    psd.save(path)
    return str(path)


@pytest.fixture
def synthetic_psd(tmp_path):
    parts = [
        (name, l, t, w, h, c)
        for (name, l, t, w, h), c in zip(_PARTS, _COLORS)
    ]
    return _save_psd(tmp_path, parts)


@pytest.fixture(autouse=True)
def _no_ambient_versions(monkeypatch):
    """Tests decide format versions themselves, not the developer's environment."""
    monkeypatch.delenv("SPINE_FORMAT_VERSION", raising=False)
    monkeypatch.delenv("SPINE_EDITOR_VERSION", raising=False)


@pytest.fixture
def imported(synthetic_psd, tmp_path):
    """The synthetic character run through the PSD importer as a 4.2 skeleton (ImportResult)."""
    from spine2d_mcp.pipeline import import_psd_to_spine

    return import_psd_to_spine(synthetic_psd, str(tmp_path / "out"), format_version="4.2")


@pytest.fixture
def imported43(synthetic_psd, tmp_path):
    """The synthetic character as a 4.3 skeleton (ImportResult)."""
    from spine2d_mcp.pipeline import import_psd_to_spine

    return import_psd_to_spine(synthetic_psd, str(tmp_path / "out43"), format_version="4.3")


@pytest.fixture
def game(tmp_path):
    """Factory for a game project folder (a git repository, so the version
    search stops there): game(package={...}, installed={...}, declared="4.3")."""
    import json

    def _make(name="game", package=None, installed=None, declared=None):
        root = tmp_path / name
        (root / ".git").mkdir(parents=True, exist_ok=True)
        if package is not None:
            (root / "package.json").write_text(json.dumps({"dependencies": package}))
        for pkg, version in (installed or {}).items():
            folder = root / "node_modules" / pkg
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "package.json").write_text(json.dumps({"name": pkg, "version": version}))
        if declared is not None:
            (root / ".spine-version").write_text(declared + "\n")
        return root

    return _make


@pytest.fixture
def make_psd(tmp_path):
    """Factory: build a PSD from custom (name, left, top, w, h) tuples."""
    def _make(layers):
        parts = [
            (name, l, t, w, h, _COLORS[i % len(_COLORS)])
            for i, (name, l, t, w, h) in enumerate(layers)
        ]
        return _save_psd(tmp_path, parts)

    return _make
