"""Onion skins: an animation's poses before and after a moment, drawn as tinted
ghosts behind it, like the Spine editor's Ghosting view, plus spacing trails.

Poses are played in spine-core (runtime/motion.mjs: AnimationState,
constraints, physics, clipping), which hands back every drawn slot's
triangles; this module draws them with Pillow. Blend modes are approximated;
dark (tint black) colours are not drawn.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import runtime
from .atlas import default_atlas
from .spine import document as d
from .spine.motion import display_skins, keyed_times

BEFORE = (255, 110, 70)  # the Spine-like warm tint for earlier poses
AFTER = (70, 180, 255)  # and cool for later ones


class OnionError(ValueError):
    """The onion skin cannot be drawn (unknown animation, runtime missing, ...)."""


@dataclass
class View:
    """World (y-up) to image pixels."""

    left: float
    top: float
    scale: float
    pad: int
    size: tuple[int, int]

    def point(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.left) * self.scale + self.pad, (self.top - y) * self.scale + self.pad


def onion_skin(project_path: str | Path, animation: str, time: float | None = None, before: int = 6,
               after: int = 6, step: int = 2, keyed: bool = False, times: list[float] | None = None,
               skins: list[str] | None = None, trails: list[str] | None = None, size: int = 1024,
               background: str | None = "#1b1b24", out_path: str | Path | None = None) -> dict:
    project_path = Path(project_path)
    doc = d.load(project_path)
    anim = (doc.get("animations") or {}).get(animation)
    if anim is None:
        raise OnionError(f"no animation {animation!r} (have {', '.join(doc.get('animations') or {}) or 'none'})")
    fps = float((doc.get("skeleton") or {}).get("fps") or 30)
    duration = d.animation_duration(anim)
    time = duration / 2 if time is None else min(max(time, 0.0), duration)
    skins = display_skins(doc) if skins is None else skins

    if times is not None:
        ghosts = [(t, "before" if t < time else "after") for t in sorted(set(times)) if abs(t - time) > 1e-6]
    elif keyed:
        keys = keyed_times(anim)
        ghosts = [(t, "before") for t in keys if time - before / fps - 1e-6 <= t < time - 1e-6]
        ghosts += [(t, "after") for t in keys if time + 1e-6 < t <= time + after / fps + 1e-6]
    else:
        ghosts = [(time - k / fps, "before") for k in range(step, before + 1, step) if time - k / fps >= 0]
        ghosts += [(time + k / fps, "after") for k in range(step, after + 1, step) if time + k / fps <= duration + 1e-6]
    ghosts = sorted(ghosts)

    rendered = runtime.motion(project_path, {"mode": "render", "skins": skins, "animation": animation,
                                             "times": [time] + [t for t, _ in ghosts]})
    _raise_for(rendered)
    frames = {round(f["time"], 6): f["draws"] for f in rendered["frames"]}

    paths = {}
    if trails is None or trails:
        sampled = runtime.motion(project_path, {"mode": "sample", "skins": skins, "rate": fps,
                                                "animations": [{"name": animation}]})
        _raise_for(sampled)
        paths = _trail_paths(sampled, trails)

    xs, ys = [], []
    for draws in frames.values():
        for draw in draws:
            xs += draw["vertices"][0::2]
            ys += draw["vertices"][1::2]
    for points in paths.values():
        xs += [p[0] for p in points if p]
        ys += [p[1] for p in points if p]
    if not xs:
        raise OnionError(f"nothing is drawn in {animation!r} with skins {skins}")
    pad = 16
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    scale = (size - 2 * pad) / max(width, height, 1e-6)
    view = View(min(xs), max(ys), scale, pad,
                (math.ceil(width * scale) + 2 * pad, math.ceil(height * scale) + 2 * pad))

    pages = _pages(default_atlas(project_path))
    image = Image.new("RGBA", view.size, (0, 0, 0, 0))
    by_distance = sorted(ghosts, key=lambda g: -abs(g[0] - time))
    far = max((abs(t - time) for t, _ in ghosts), default=1) or 1
    for t, side in by_distance:
        ghost = render_frame(frames[round(t, 6)], pages, view)
        opacity = 0.5 - 0.32 * (abs(t - time) / far)
        image.alpha_composite(_tint(ghost, BEFORE if side == "before" else AFTER, opacity))
    image.alpha_composite(render_frame(frames[round(time, 6)], pages, view))
    if paths:
        _draw_trails(image, view, paths, time, fps)
    if background:
        backdrop = Image.new("RGBA", view.size, background)
        backdrop.alpha_composite(image)
        image = backdrop

    out_path = Path(out_path) if out_path else project_path.with_name(
        f"{project_path.stem}-{animation.replace('/', '_')}-onion.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return {"path": str(out_path), "animation": animation, "time": round(time, 4), "fps": fps,
            "ghosts": [{"time": round(t, 4), "side": side} for t, side in ghosts],
            "trails": sorted(paths), "skins": skins, "size": list(view.size)}


def render_frame(draws: list[dict], pages: dict[str, Image.Image], view: View) -> Image.Image:
    """One pose: each slot's triangles textured from its atlas page, in draw order."""
    frame = Image.new("RGBA", view.size, (0, 0, 0, 0))
    for draw in draws:
        page = pages.get(draw["page"])
        if page is None:
            continue
        points = [view.point(x, y) for x, y in zip(draw["vertices"][0::2], draw["vertices"][1::2])]
        if not points:
            continue
        left = max(int(min(p[0] for p in points)), 0)
        top = max(int(min(p[1] for p in points)), 0)
        right = min(math.ceil(max(p[0] for p in points)) + 1, view.size[0])
        bottom = min(math.ceil(max(p[1] for p in points)) + 1, view.size[1])
        if right <= left or bottom <= top:
            continue
        layer = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
        uvs = [(u * page.width, v * page.height) for u, v in zip(draw["uvs"][0::2], draw["uvs"][1::2])]
        tri = draw["triangles"]
        for i in range(0, len(tri) - 2, 3):
            _triangle(layer, page, [points[j] for j in tri[i:i + 3]], [uvs[j] for j in tri[i:i + 3]], left, top)
        _blend(frame, _colored(layer, draw["color"]), (left, top), draw["blend"])
    return frame


def _triangle(layer: Image.Image, page: Image.Image, screen, texture, ox: int, oy: int) -> None:
    (x0, y0), (x1, y1), (x2, y2) = screen
    a = np.array([[x0, y0, 1], [x1, y1, 1], [x2, y2, 1]], dtype=float)
    if abs(np.linalg.det(a)) < 1e-9:
        return
    u = np.linalg.solve(a, [p[0] for p in texture])
    v = np.linalg.solve(a, [p[1] for p in texture])
    left = max(int(min(x0, x1, x2)) - ox, 0)
    top = max(int(min(y0, y1, y2)) - oy, 0)
    right = min(math.ceil(max(x0, x1, x2)) + 1 - ox, layer.width)
    bottom = min(math.ceil(max(y0, y1, y2)) + 1 - oy, layer.height)
    if right <= left or bottom <= top:
        return
    bx, by = left + ox, top + oy  # the patch's top-left on screen
    coefficients = (u[0], u[1], u[0] * bx + u[1] * by + u[2], v[0], v[1], v[0] * bx + v[1] * by + v[2])
    patch = page.transform((right - left, bottom - top), Image.AFFINE, coefficients, resample=Image.BILINEAR)
    patch = patch.convert("RGBA")  # pages are premultiplied (RGBa) so Pillow filters them without converting each time
    mask = Image.new("L", patch.size, 0)
    ImageDraw.Draw(mask).polygon([(x - bx, y - by) for x, y in screen], fill=255)
    layer.paste(patch, (left, top), mask)


def _colored(layer: Image.Image, color: list[float]) -> Image.Image:
    if all(c >= 0.999 for c in color):
        return layer
    pixels = np.asarray(layer, dtype=np.float32) * np.array(color, dtype=np.float32)
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGBA")


def _blend(frame: Image.Image, layer: Image.Image, at: tuple[int, int], blend: int) -> None:
    """Spine blend modes: 0 normal, 1 additive, 2 multiply, 3 screen (approximated on straight alpha)."""
    if blend == 0:
        frame.alpha_composite(layer, at)
        return
    box = (at[0], at[1], at[0] + layer.width, at[1] + layer.height)
    base = np.asarray(frame.crop(box), dtype=np.float32) / 255
    top = np.asarray(layer, dtype=np.float32) / 255
    a = top[..., 3:]
    rgb = base[..., :3]
    if blend == 1:
        rgb = np.minimum(rgb + top[..., :3] * a, 1)
    elif blend == 2:
        rgb = rgb * (1 - a + a * top[..., :3])
    else:
        rgb = 1 - (1 - rgb) * (1 - a * top[..., :3])
    alpha = np.maximum(base[..., 3:], a)
    frame.paste(Image.fromarray(np.round(np.concatenate([rgb, alpha], -1) * 255).astype(np.uint8), "RGBA"), box[:2])


def _tint(frame: Image.Image, tint: tuple[int, int, int], opacity: float) -> Image.Image:
    pixels = np.asarray(frame, dtype=np.float32)
    rgb = pixels[..., :3] * 0.4 + np.array(tint, dtype=np.float32) * 0.6
    alpha = pixels[..., 3:] * opacity
    return Image.fromarray(np.concatenate([rgb, alpha], -1).astype(np.uint8), "RGBA")


def _pages(atlas_path: Path) -> dict[str, Image.Image]:
    """Every page image the atlas names (a line ending in an image extension), premultiplied."""
    pages = {}
    for line in atlas_path.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if name and not line[0].isspace() and name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            path = atlas_path.parent / name
            if path.is_file():
                pages[name] = Image.open(path).convert("RGBA").convert("RGBa")
    return pages


def _trail_paths(sampled: dict, trails: list[str] | None) -> dict[str, list]:
    """Slot name -> its attachment centre at every frame (None while hidden); by
    default the three slots whose centres travel farthest."""
    frames = next(iter(sampled["animations"].values()))["frames"]
    names = [s["name"] for s in sampled["slots"]]
    paths = {name: [(f["slots"][i][2], f["slots"][i][3]) if f["slots"][i] else None for f in frames]
             for i, name in enumerate(names)}
    if trails is not None:
        unknown = sorted(set(trails) - set(paths))
        if unknown:
            raise OnionError(f"no slot {', '.join(unknown)}")
        return {name: paths[name] for name in trails}

    def travel(points):
        shown = [p for p in points if p]
        return sum(math.dist(a, b) for a, b in zip(shown, shown[1:]))
    parent = {b["name"]: b["parent"] for b in sampled["bones"]}
    bone_of = {s["name"]: s["bone"] for s in sampled["slots"]}

    def ancestors(bone):
        out = set()
        while bone:
            out.add(bone)
            bone = parent.get(bone)
        return out
    chosen: list[str] = []
    for name in sorted(paths, key=lambda n: -travel(paths[n])):
        if travel(paths[name]) <= 0 or len(chosen) == 3:
            continue
        mine = ancestors(bone_of[name])
        if any(bone_of[c] in mine for c in chosen):  # a deeper part of a chosen chain shows its end
            chosen = [c for c in chosen if bone_of[c] not in mine] + [name]
        elif not any(bone_of[name] in ancestors(bone_of[c]) for c in chosen):
            chosen.append(name)
    return {name: paths[name] for name in chosen}


def _draw_trails(image: Image.Image, view: View, paths: dict[str, list], time: float, fps: float) -> None:
    """A dot per frame on each path (their spacing is the motion's spacing), warm before `time`, cool after."""
    draw = ImageDraw.Draw(image)
    for points in paths.values():
        shown = [(i, view.point(*p)) for i, p in enumerate(points) if p]
        for (_, a), (_, b) in zip(shown, shown[1:]):
            draw.line([a, b], fill=(255, 255, 255, 90), width=1)
        for i, (x, y) in shown:
            color = BEFORE if i / fps < time - 1e-6 else AFTER
            draw.ellipse([x - 2.5, y - 2.5, x + 2.5, y + 2.5], fill=(*color, 230))
        now = min(shown, key=lambda s: abs(s[0] / fps - time), default=None)
        if now:
            x, y = now[1]
            draw.ellipse([x - 5, y - 5, x + 5, y + 5], outline=(255, 255, 255, 255), width=2)


def _raise_for(result: dict) -> None:
    if not result.get("ran"):
        raise OnionError(f"spine-core could not play the animation: {result.get('reason')}")
    if result.get("errors"):
        raise OnionError("spine-core: " + "; ".join(result["errors"]))
