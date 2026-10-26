# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

An MCP server (FastMCP, stdio) that builds, edits, checks, converts and renders Spine skeletons in format 4.2 and 4.3: PSD → rigged, animated skeleton JSON + atlas, which loads in the Spine editor and in game runtimes (spine-core, spine-phaser-v4, spine-player). The server works on files and drives the Spine editor through its CLI; the editor has no live API. README.md documents every tool and the version rules. GLOSSARY.md fixes the vocabulary: say *format version*, *editor version* and *runtime version*, never "Spine version".

## Commands

```bash
uv sync                                   # install (editable)
uv run pytest                             # full suite (~100 s with Node + Spine installed)
uv run pytest tests/test_rig.py -k pivot  # a subset
uv run pytest tests/test_rig.py::test_physics_transform_and_remove
uvx ruff check src tests --select F,E9    # the lint used here (no ruff config in the repo)
uv run spine2d-mcp                        # run the server on stdio
```

- Tests marked `needs_node` / `needs_spine` skip without Node or the Spine editor.
- The first Node run installs spine-core releases into `~/.cache/spine2d-mcp` (or `$SPINE2D_MCP_CACHE`).
- Editor tests run the real Spine CLI (`/Applications/Spine.app/Contents/MacOS/Spine`, or `$SPINE_EXECUTABLE`) and take seconds each.
- A running Claude session keeps its MCP server process: after changing server code, reconnect with `/mcp` to see the change.

## Package layout

`src/` *is* the `spine2d_mcp` package. setuptools maps it with `package-dir = {"spine2d_mcp" = "src"}`, so code imports `spine2d_mcp.…` and tests use the installed package (pytest does not add `src/` to the path).
- A new subpackage must be added to `packages` in `pyproject.toml`.
- `runtime/*.mjs` ships as package data.
- Don't switch back to hatchling: it cannot install this renamed folder in editable mode, which is how `uv run` installs the project.

## Architecture

**Editing tools** follow one path. A tool in `server.py` (`@mcp.tool()` plus `@_asks`) calls `edits.edit_skeleton(path, change)`, which:
1. loads the skeleton JSON as a plain dict;
2. resolves the game project's format version through `project.py`. If that version is unknown, unclear or differs from the file's, it raises `NeedsAnswer`, which `_asks` turns into a `needs_answer` result. The agent must put that question to the user, not guess.
3. runs `change(doc)`, a function from `ops/*` that mutates the dict, or raises `EditError` to refuse;
4. runs the checker (`spine/check.py`) and refuses only errors the change *introduced*, so files with existing errors stay editable;
5. saves.

**Two layouts, one API.** 4.2 keeps constraints in per-kind arrays ordered by `order`; 4.3 keeps one `constraints` array with `type`, applied in array order. Go through `spine/document.py` (`all_constraints`, `apply_order`, skin, slot and folder helpers) and `versions.layout(doc)` instead of touching those keys directly.

**Versions.**
- `spine/versions.py` `FORMATS` is the single table of format versions, with the editor, runtime and Phaser releases each is tested with.
- A file is always read and written in its own `skeleton.spine` version; nothing converts silently.
- `convert_skeleton` (`conversion.py` + `spine/convert.py`) is explicit, refuses lossy conversions unless agreed, and verifies poses in both runtimes.
- Adding a format version means a `FORMATS` entry, checker rules and a conversion step, and only for stable releases (ADR 0001).

**The checker** (`spine/check.py`) is per version.
- Errors: data that fails to load, renders wrongly, or that the Spine editor refuses. A key from the other format version is an error.
- Warnings: probably unintended.
- The format source of truth is `reference/SkeletonJson-4.2.java` / `-4.3.java`. Behaviour questions are settled by running spine-core, not by reading.

**The importer** (`pipeline.py`) has two paths; `atlas/` packs the images for both, and imported bones have length 0 and no setup rotation.
- A PSD with Spine's Import PSD tags, or an untagged one without body-part names: `psd/tags.py` (tag grammar; each attachment layer's slot, skin, placeholder, path and bone) → `psd/render.py` (pixels, overlays, trim/scale/rotate/pad) → `spine/psd_import.py` (the skeleton dict). Where the editor's docs are silent it follows Esoteric's PhotoshopToSpine script.
- An untagged PSD with body-part names: `psd/parser.py` → `psd/conventions.py` (bone hierarchy, cap pivots, `[pivot]` markers) → `spine/builder.py` (Pydantic `schema.py`) → `spine/writer.py`.
- The editor's Import PSD is GUI-only (its CLI refuses PSDs). Tag handling is checked by redrawing the setup pose from the written JSON against the PSD's composite (`tests/test_psd_tags.py`) and by editor round trips of the JSON.

**Runtime checks.**
- `runtime/__init__.py` installs a spine-core release per format version (or the game's installed release) and runs `runtime_check.mjs`. That script plays every animation in every skin, and in `--poses` mode dumps world transforms for `compare_poses`.
- `Animation.apply` changed in spine-core 4.3.6 (a boolean `fromSetup` became `MixFrom`); the harness handles both.

**Motion review** (`analyze_motion`, `onion_skin`) is read-only and needs Node, not the editor.
- `runtime/motion.mjs` plays animations through spine-core's AnimationState with constraints and physics. In "sample" mode it records each bone's world transform and each visible slot's attachment centre and four tracked vertices at 60 Hz; in "render" mode, each drawn slot's clipped triangles.
- `spine/motion.py` turns samples into ranked findings. It judges screen motion (so baked, key-per-frame animations are judged fairly) and reads keys only for curve-level findings. Thresholds are module constants scaled to the skeleton's size; tune them against real rigs (the navy dealer) before trusting a new one.
- `onion.py` draws the triangles with Pillow; atlas pages are kept premultiplied (`RGBa`) because Pillow otherwise converts the whole page on every triangle.

**The Spine editor** (`spine_cli.py`) runs the CLI with `-u <release>`. `editor_for` picks one release per file: explicit, else `$SPINE_EDITOR_VERSION`, else the newest stable release in the launcher's `updates` folder. `spine_import` imports a temporary copy whose header names the editor's own release. If the skeleton has sliders, the copy also lists animations in the editor's own sort order (`editor_animation_order`), because the 4.3 editor stores a slider's animation as an index.

**`editor_quirks.py`** covers what the editor's import loses (4.3 clip `convex`/`inverse`, nested draw order folder keys).
- `spine_import` records the losses beside the `.spine` file, and `spine_export` restores them: JSON in Python, binary `.skel` through `runtime/binary_restore.mjs`, which patches bytes located by spine-core's own reader.
- `EDITOR_FIXES` lists changelog fixes; `spine_import` warns when an older editor lacks one the skeleton needs.

## Editor rules the runtimes don't show

These were found by round trips through real editors (4.3.00–4.3.26 and 4.2.43). The tools enforce them:
- A named skin must not hold an attachment under a key the default skin also has in that slot; the editor refuses the file. Use `edit_skin` `move_to_skins` (skin placeholders) instead.
- Physics `rotate`/`scaleX`/`shearX` need a bone with length; the editor drops them otherwise. `add_constraint` refuses them on zero-length bones, which PSD-imported bones are.
- Draw order folders are slot-name prefixes (`arms/hand_left`) kept contiguous in setup order. `drawOrderFolder` is an array of `{slots, keys}`, with `slots` listing every slot of the folder in setup order. The editor keeps keys only for top-level folders.
- Re-check a new editor release with a round trip (import → JSON/binary export → compare in spine-core) before trusting a changelog line.

## Tests

- Fixtures in `tests/conftest.py`:
  - `synthetic_psd`, a generated layered PSD;
  - `imported` / `imported43`, that PSD imported as 4.2 / 4.3;
  - `game(...)`, a factory for game-project folders with a `package.json` or `.spine-version`.
- Test modules define their own `doc` / `doc43` (the loaded dicts) for calling `ops` functions directly.
- An autouse fixture clears `SPINE_FORMAT_VERSION` and `SPINE_EDITOR_VERSION`.
- `tests/fixtures/` holds a 4.2 rig with every constraint kind, and a 4.3.26 binary export with its restore record.
