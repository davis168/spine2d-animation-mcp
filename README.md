# spine2d-mcp

An MCP server that converts a layered PSD character into a [Spine](http://esotericsoftware.com/) 4.2 project — skeleton JSON, texture atlas, atlas page PNG(s), and per-part images — ready to import into the Spine editor and Unity.

The goal is to cut the manual rig-and-import work for a character from days down to roughly an hour.

## Status

**Phase 0 (proof of concept):** PSD → a correct, importable rig (no animations yet).

The pipeline is implemented and covered by tests against synthetic fixtures. Generated skeletons import into the Spine editor (verified with Spine 4.3.26 via its CLI importer and opened in the editor). Validation in Unity is the next milestone. Animation generation, headless preview, and Unity export are planned for later phases.

## What it does today

Given a PSD whose layers are named after body parts (`head`, `body`/`torso`, `arm_left`, `hand_right`, `leg_left`, `foot_right`, …), the `import_psd_to_spine` tool produces:

- `<name>.json` — Spine 4.2 skeleton (bones, slots, default skin, region attachments)
- `<name>.atlas` — libgdx/Spine texture atlas
- `<name>.png` — packed atlas page(s), on the smallest power-of-two page that fits
- `images/<part>.png` — one image per attachment; the Spine editor displays these (not the atlas), and the skeleton's `images` path points here

Key behaviors:

- **Multi-segment skeleton** (`root → hip → torso → chest → neck → head`, limbs off `chest`, legs off `hip`); missing intermediate bones are created as positioned virtual bones.
- **Joint-correct pivots** — each bone pivots at the joint edge of its part (or an explicit `<part>[pivot]` marker layer), not the image center, so rotation behaves anatomically.
- **Origin at the feet** — the skeleton origin is the bottom-center of the character, not the PSD canvas corner, so it is placed by its feet in Spine and Unity.
- **PSD draw order** is preserved as Spine slot order.
- **Structural validation** of the skeleton against the invariants the Spine 4.2 reader enforces.

Format source of truth: `reference/SkeletonJson.java` (spine-runtimes 4.2).

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
```

## Run the tests

```bash
uv run pytest
```

## Run the MCP server

```bash
uv run spine2d-mcp
```

This starts a stdio MCP server exposing a single tool:

```
import_psd_to_spine(psd_path: str, out_dir: str | None = None)
  -> { project_path, atlas_path, png_paths, images_dir }
```

`out_dir` defaults to `<psd_dir>/<psd_stem>_spine`. The PSD is read from disk, so pass an absolute path.

## Use it from Claude

Register the server with an absolute path to `uv`; Claude Desktop starts servers with a minimal `PATH`, so a bare `uv` fails with `spawn uv ENOENT`. Find yours with `which uv`.

**Claude Code** (`--scope user` makes it available in every project):

```bash
claude mcp add --scope user spine2d -- /absolute/path/to/uv --directory /absolute/path/to/spine2d-animation-mcp run spine2d-mcp
```

Run `/mcp` in a new session to check it is connected.

**Claude Desktop** — add to `mcpServers` in `~/Library/Application Support/Claude/claude_desktop_config.json` (Settings → Developer → Edit Config), then quit with ⌘Q and reopen:

```json
"mcpServers": {
  "spine2d": {
    "command": "/absolute/path/to/uv",
    "args": ["--directory", "/absolute/path/to/spine2d-animation-mcp", "run", "spine2d-mcp"]
  }
}
```

Then ask, for example: *"Import /path/to/hero.psd to Spine."*

## Open the result in Spine

Import the skeleton JSON into a project with the Spine CLI (or File → Import Data in the editor), then open it:

```bash
/Applications/Spine.app/Contents/MacOS/Spine -i hero_spine/hero.json -o hero_spine/hero.spine -r hero
open hero_spine/hero.spine
```

The editor finds the part images through the skeleton's `images` path. A newer editor warns that the data is 4.2; to export for a spine-unity 4.2 runtime, run the 4.2 editor (`Spine -u 4.2.xx`).

## Project layout

```
src/spine2d_mcp/
  server.py          FastMCP server + import_psd_to_spine tool
  pipeline.py        PSD -> Spine project orchestration
  psd/parser.py      PSD -> flat layer list (+ pivot markers)
  psd/conventions.py bone hierarchy, joint sides, [pivot] tag
  spine/schema.py    Pydantic models for Spine 4.2 JSON
  spine/builder.py   layers -> SpineProject
  spine/writer.py    SpineProject -> JSON
  spine/validator.py structural validation
  atlas/packer.py    rectpack bin packing
  atlas/writer.py    .atlas text + composite PNG
reference/           SkeletonJson.java, spineboy.atlas (format references)
tests/               pytest suite (synthetic PSD fixtures)
```

## License

This project is released under the MIT License.

You may use, copy, modify, merge, publish, distribute, sublicense, and sell copies
of the software, provided that the original copyright notice and license text are
included with substantial portions of the software.

The software is provided "as is", without warranty of any kind. See
[LICENSE](LICENSE) for the full license text.
