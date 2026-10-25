"""A self-contained HTML preview using the official Spine Web Player.

The skeleton, atlas and page images are embedded as data URIs, so the page
opens straight from disk; only the player script and stylesheet come from the
CDN, pinned to the player release of the skeleton's format version (a 4.2
file plays in spine-player 4.2.120, a 4.3 file in 4.3.13).
"""

from __future__ import annotations

import base64
import html
import json
import os
from pathlib import Path

from .atlas import default_atlas
from .spine import document as d
from .spine import versions


def player_version(skeleton_doc: dict) -> str:
    """The spine-player release for the skeleton's format version."""
    return versions.get(versions.of(skeleton_doc)).runtime


def _cdn(version: str) -> str:
    return f"https://unpkg.com/@esotericsoftware/spine-player@{version}/dist"


def _data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _atlas_pages(atlas_text: str) -> list[str]:
    """Page image names: the first line of each blank-line-separated block."""
    pages, expect_page = [], True
    for line in atlas_text.splitlines():
        if not line.strip():
            expect_page = True
        elif expect_page:
            pages.append(line.strip())
            expect_page = False
    return pages


def preview_html(
    project_path: str | Path,
    atlas_path: str | Path | None = None,
    out_path: str | Path | None = None,
    animation: str | None = None,
    background: str = "#1b1b24",
    title: str | None = None,
) -> Path:
    project_path = Path(project_path)
    atlas_path = Path(atlas_path) if atlas_path else default_atlas(project_path)
    out_path = Path(out_path) if out_path else project_path.with_name(f"{project_path.stem}_preview.html")
    atlas_text = atlas_path.read_text(encoding="utf-8")
    skeleton_doc = json.loads(project_path.read_text(encoding="utf-8"))
    version = player_version(skeleton_doc)
    animations = list(skeleton_doc.get("animations") or {})
    if animation is not None and animation not in animations:
        raise ValueError(f"no animation {animation!r} (have {', '.join(animations) or 'none'})")

    raw = {
        project_path.name: _data_uri(project_path, "application/json"),
        atlas_path.name: _data_uri(atlas_path, "text/plain"),
    }
    for page in _atlas_pages(atlas_text):
        page_path = atlas_path.parent / page
        if not page_path.is_file():
            raise FileNotFoundError(f"atlas page {page} not found next to {atlas_path}")
        raw[page] = _data_uri(page_path, "image/png")

    config = {
        "skeleton": project_path.name,
        "atlas": atlas_path.name,
        "rawDataURIs": raw,
        "backgroundColor": background,
        "showControls": True,
    }
    if version.startswith("4.2."):
        # The atlas is straight alpha. 4.3 reads that from the atlas page and
        # dropped this option.
        config["premultipliedAlpha"] = False
    if animation or animations:
        config["animation"] = animation or animations[0]
    skin = d.starting_skin(skeleton_doc)
    if skin:
        config["skin"] = skin
    name = html.escape(title or project_path.stem)
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}</title>
<link rel="stylesheet" href="{_cdn(version)}/spine-player.min.css">
<script src="{_cdn(version)}/iife/spine-player.min.js"></script>
<style>
  body {{ margin: 0; padding: 16px; background: {background}; color: #ddd;
         font: 14px/1.4 -apple-system, system-ui, sans-serif; }}
  h1 {{ font-size: 16px; margin: 0 0 4px; }}
  #status {{ margin: 0 0 12px; color: #8a8; }}
  #player {{ width: 100%; max-width: 720px; height: min(70vh, 640px); }}
</style>
</head>
<body>
<h1>{name}</h1>
<p id="status">Loading...</p>
<div id="player"></div>
<script>
  const status = document.getElementById("status");
  const config = {json.dumps(config)};
  config.success = () => {{ status.textContent = "Loaded successfully"; }};
  config.error = (player, msg) => {{ status.textContent = "Error: " + msg; status.style.color = "#e88"; }};
  new spine.SpinePlayer("player", config);
</script>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    return out_path


def phaser_html(
    project_path: str | Path,
    atlas_path: str | Path | None = None,
    out_path: str | Path | None = None,
    animation: str | None = None,
    background: str = "#1b1b24",
) -> Path:
    """A page playing the skeleton in a Phaser 4 game with spine-phaser-v4,
    the releases of its format version (4.2: spine-phaser-v4 4.2.120 on
    Phaser 4.1.0; 4.3: 4.3.13 on Phaser 4.2.1), with pickers for animation
    and skin. It loads the skeleton and atlas files beside it, so it must be
    served over HTTP (browsers block file:// loads); `window.results` holds
    what loaded and any errors."""
    project_path = Path(project_path)
    atlas_path = Path(atlas_path) if atlas_path else default_atlas(project_path)
    out_path = Path(out_path) if out_path else project_path.with_name(f"{project_path.stem}_phaser.html")
    skeleton_doc = json.loads(project_path.read_text(encoding="utf-8"))
    release = versions.get(versions.of(skeleton_doc))
    animations = list(skeleton_doc.get("animations") or {})
    if animation is not None and animation not in animations:
        raise ValueError(f"no animation {animation!r} (have {', '.join(animations) or 'none'})")
    rel = lambda p: Path(os.path.relpath(p, out_path.parent)).as_posix()  # noqa: E731
    settings = {"skeleton": rel(project_path), "atlas": rel(atlas_path), "animation": animation or
                (animations[0] if animations else None), "skin": d.starting_skin(skeleton_doc),
                "background": background}
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(project_path.stem)} in Phaser</title>
<script src="https://cdn.jsdelivr.net/npm/phaser@{release.phaser}/dist/phaser.min.js"></script>
<script src="https://unpkg.com/@esotericsoftware/spine-phaser-v4@{release.runtime}/dist/iife/spine-phaser-v4.js"></script>
<style>
  body {{ margin: 0; padding: 12px; background: {background}; color: #ddd; font: 13px system-ui, sans-serif; }}
  #bar {{ display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }}
  #status {{ color: #8a8; }}
</style>
</head>
<body>
<div id="bar">
  <b>{html.escape(project_path.stem)}</b>
  <span>Phaser {release.phaser} + spine-phaser-v4 {release.runtime}</span>
  <select id="animation"></select> <select id="skin"></select>
  <span id="status">Loading...</span>
</div>
<div id="game"></div>
<script>
  const settings = {json.dumps(settings)};
  const status = document.getElementById("status");
  window.results = {{ errors: [] }};
  const fail = (msg) => {{ window.results.errors.push(msg); status.textContent = "Error: " + msg; status.style.color = "#e88"; }};
  window.addEventListener("error", (e) => fail(String(e.message)));
  class Preview extends Phaser.Scene {{
    preload() {{
      this.load.on("loaderror", (file) => fail("could not load " + file.src));
      // spine-phaser-v4 4.3 has spineSkeleton (JSON or binary); 4.2 has spineJson.
      if (this.load.spineSkeleton) this.load.spineSkeleton("data", settings.skeleton);
      else this.load.spineJson("data", settings.skeleton);
      this.load.spineAtlas("atlas", settings.atlas);
    }}
    create() {{
      try {{
        const obj = this.add.spine(0, 0, "data", "atlas");
        const data = obj.skeleton.data;
        if (settings.skin) obj.skeleton.setSkinByName(settings.skin);  // slots whose art is only in skins
        obj.skeleton.updateWorldTransform(spine.Physics.update);  // bounds need world positions
        const bounds = obj.skeleton.getBoundsRect();  // y down: spine-phaser flips the skeleton
        const scale = Math.min(1, 600 / Math.max(bounds.width, 1), 560 / Math.max(bounds.height, 1));
        obj.setScale(scale);
        obj.setPosition(320 - (bounds.x + bounds.width / 2) * scale, 580 - (bounds.y + bounds.height) * scale);
        const animationPick = document.getElementById("animation"), skinPick = document.getElementById("skin");
        for (const a of data.animations) animationPick.add(new Option(a.name, a.name));
        for (const s of data.skins) skinPick.add(new Option(s.name, s.name));
        const play = () => {{ if (animationPick.value) obj.animationState.setAnimation(0, animationPick.value, true); }};
        animationPick.onchange = play;
        skinPick.onchange = () => {{ obj.skeleton.setSkinByName(skinPick.value); obj.skeleton.setupPoseSlots ?
          obj.skeleton.setupPoseSlots() : obj.skeleton.setSlotsToSetupPose(); }};
        if (settings.animation) animationPick.value = settings.animation;
        if (settings.skin) skinPick.value = settings.skin;
        play();
        Object.assign(window.results, {{ loaded: true, version: data.version, animations: data.animations.map((a) => a.name),
          skins: data.skins.map((s) => s.name) }});
        if (!window.results.errors.length) status.textContent = "Loaded " + data.version;
        window.spineObject = obj;
      }} catch (e) {{ fail(String(e && e.message || e)); }}
    }}
  }}
  new Phaser.Game({{ type: Phaser.WEBGL, parent: "game", width: 640, height: 600, backgroundColor: settings.background,
    scene: Preview, plugins: {{ scene: [{{ key: "spine.SpinePlugin", plugin: spine.SpinePlugin, mapping: "spine" }}] }} }});
</script>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    return out_path
