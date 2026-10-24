# spine2d-mcp

An MCP server that builds, edits, checks and renders [Spine](http://esotericsoftware.com/) 4.2 characters: from a layered PSD to a rigged, animated skeleton that loads in the Spine editor and in spine-phaser-v4 4.2.x.

The goal is to cut the manual rig-and-animate work for a character from days down to roughly an hour.

## Status

PSD → rig → animations → checked → previewed, end to end through MCP tools. Every editing tool checks its result against the Spine 4.2 format before writing. Generated skeletons import into the Spine editor (verified with Spine 4.2.43 and 4.3.26 via the CLI), round-trip through the 4.2.43 editor's export, and load and play in spine-core 4.2.120, the runtime spine-phaser-v4 4.2.x ships. Not yet verified in a Phaser game or in Unity.

The Spine editor has no live scripting API, so the server works on the files (skeleton JSON, atlas, images) and drives the editor through its command line interface.

## Tools

| Tool | What it does |
|---|---|
| `import_psd_to_spine` | Layered PSD → skeleton JSON, atlas, page PNG(s) and per-part images |
| `inspect_skeleton` | Bones (setup and world positions), slots in draw order, skins, constraints, events, and what each animation keys |
| `validate_skeleton` | Spine 4.2 format check; `runtime=true` also loads and plays it in spine-core 4.2.120 |
| `add_animation` / `remove_animation` | Presets (idle, walk, wave, jump) or keyframes with an easing shorthand; preset + keyframe overrides |
| `edit_rig` | Add/set/rename/remove bones, move a pivot without moving the art, reparent, add/set/remove slots, draw order |
| `add_constraint` / `remove_constraint` | IK, transform and physics constraints (physics presets: hair, cloth, tail, antenna, jiggle) |
| `add_attachment` | Add a PNG as a region attachment on a slot (mouth shapes, alternate hands) and repack the atlas |
| `pack_atlas` | Repack the atlas from the images folder |
| `spine_import` | Import the JSON into a `.spine` project with the Spine CLI |
| `spine_export` | Render PNG frame sequences or GIFs, or export JSON/binary, with the Spine CLI |
| `preview_html` | A self-contained Spine Web Player 4.2 page (skeleton, atlas and images embedded) |

A typical session, in plain language to Claude: *"Import /path/to/hero.psd, add the idle, walk and wave presets, validate with the runtime check, export GIFs and make a preview."*

### The PSD importer

Layers named after body parts (`head`, `body`/`torso`, `arm_left`, `hand_right`, `leg_left`, `foot_right`, …) become:

- `<name>.json`: Spine 4.2 skeleton (bones, slots, default skin, region attachments)
- `<name>.atlas` and `<name>.png`: the atlas, on the smallest power-of-two page that fits
- `images/<part>.png`: one image per attachment; the Spine editor displays these, and the skeleton's `images` path points here

Rig conventions:

- **Multi-segment skeleton** (`root → hip → torso → chest → neck → head`, arms off `chest`, legs off `hip`); missing intermediate bones are created as positioned virtual bones.
- **Joint pivots at the round cap** — arms and legs pivot at the centre of their joint-end cap (half the limb width in from the joint edge), so the cap stays put as the joint turns; hands and feet turn about the wrist and ankle (the parent limb's far cap); the head pivots at the chin. A `<part>[pivot]` marker layer overrides any of these.
- **Origin at the feet** — the bottom-centre of the character, not the PSD canvas corner.
- **PSD draw order** becomes Spine slot order.
- Bones have no setup rotation (limbs hang along −y).

### Animations

`add_animation` takes Spine 4.2 animation JSON plus one convenience: a key may carry `"ease"` instead of a raw curve. As in Spine, a key's easing shapes the segment that starts at it. Easing names: `linear`, `stepped`, `in`, `out`, `inout`, `smooth`, `back`, or `[x1, y1, x2, y2]` (CSS cubic-bezier); the tool computes the absolute per-channel bezier handles Spine stores. Rotations use `value` in degrees (CCW); `angle` (the 3.x key, which 4.2 silently reads as 0) is rejected.

```json
{"bones": {"head": {"rotate": [{"time": 0, "value": 0, "ease": "inout"},
                                {"time": 0.3, "value": -8, "ease": "inout"},
                                {"time": 0.6, "value": 0}]}},
 "events": [{"time": 0.3, "name": "nod_down"}]}
```

Presets are tuned for importer rigs and scale with the skeleton's height: `idle` (2.0 s loop), `walk` (0.8 s in-place front-view loop; only the lifted leg moves, so the planted foot never slides), `wave` (2.5 s, either arm), `jump` (1.3 s, squash and stretch about the feet). `speed`, `amount` and `bone_map` (preset bone → your bone) adapt them.

### Limits

- **IK** aims a bone's x axis at the target, so it suits rigs whose bones point along their limbs. Importer rigs have unrotated bones; `add_constraint` warns when the setup pose would swing. Aim importer limbs with rotate keys.
- **Path constraints** are preserved and checked but cannot be added (they need path attachments).
- `move_pivot`, reparenting and slot rebinding work in world space and require `inherit: normal` along the chain.

## Spine versions

This project targets the **Spine 4.2** line: a runtime loads only data from its own major.minor version.

- **Phaser 4**: `npm i @esotericsoftware/spine-phaser-v4@v4.2-latest` (4.2.x needs Phaser ≥ 4.1). The untagged `latest` is the 4.3 line and will not load 4.2 data.
- **Spine editor**: JSON and binary exports carry the exporting editor's version. Run a 4.2 editor for round trips: set `SPINE_EDITOR_VERSION` (e.g. `4.2.43`, a version the Spine launcher has downloaded) or pass `editor_version`. `spine_export` warns when a newer editor exported the data. Saving a project in a newer editor upgrades it.
- `SPINE_EXECUTABLE` points at the Spine executable when it is not in the standard place (`/Applications/Spine.app/Contents/MacOS/Spine` on macOS).

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Optional: Node.js with npm, for the runtime check (spine-core 4.2.120 is installed on first use into `~/.cache/spine2d-mcp`, or `$SPINE2D_MCP_CACHE`)
- Optional: the Spine editor (Essential or Professional), for `spine_import` and `spine_export`

## Setup and tests

```bash
uv sync
uv run pytest
```

Tests that need Node or the Spine editor skip when they are not installed.

## Use it from Claude

Register the server with an absolute path to `uv`; Claude Desktop starts servers with a minimal `PATH`, so a bare `uv` fails with `spawn uv ENOENT`. Find yours with `which uv`.

**Claude Code** (`--scope user` makes it available in every project; `-e` sets the editor version for the CLI tools):

```bash
claude mcp add --scope user spine2d -e SPINE_EDITOR_VERSION=4.2.43 -- /absolute/path/to/uv --directory /absolute/path/to/spine2d-animation-mcp run spine2d-mcp
```

Run `/mcp` in a new session to check it is connected.

**Claude Desktop** — add to `mcpServers` in `~/Library/Application Support/Claude/claude_desktop_config.json` (Settings → Developer → Edit Config), then quit with ⌘Q and reopen:

```json
"mcpServers": {
  "spine2d": {
    "command": "/absolute/path/to/uv",
    "args": ["--directory", "/absolute/path/to/spine2d-animation-mcp", "run", "spine2d-mcp"],
    "env": {"SPINE_EDITOR_VERSION": "4.2.43"}
  }
}
```

## Project layout

```
src/spine2d_mcp/
  server.py            FastMCP server: tool definitions
  pipeline.py          PSD -> Spine project orchestration
  edits.py             load -> change -> check -> save for every editing tool
  preview.py           Spine Web Player page
  spine_cli.py         Spine editor CLI: import, export, project info
  runtime/             spine-core 4.2 runtime check (Node script + wrapper)
  ops/                 edits as pure functions: animation, rig, constraints, attachments
  psd/parser.py        PSD -> flat layer list (+ pivot markers)
  psd/conventions.py   bone hierarchy, joint sides, cap pivots, [pivot] tag
  spine/schema.py      Pydantic models the importer builds with
  spine/builder.py     layers -> SpineProject
  spine/writer.py      SpineProject -> JSON
  spine/validator.py   importer-level structural validation
  spine/document.py    skeleton JSON as a dict: load, save, world transforms
  spine/check.py       Spine 4.2 format checker
  spine/inspect.py     skeleton summary for inspect_skeleton
  spine/animation.py   easing shorthand -> Spine 4.2 animation JSON
  spine/presets.py     idle, walk, wave, jump
  atlas/packer.py      rectpack bin packing
  atlas/writer.py      .atlas text + composite PNG
reference/             SkeletonJson.java (spine-runtimes 4.2), spineboy.atlas
tests/                 pytest suite (synthetic PSD fixtures)
```

Format source of truth: `reference/SkeletonJson.java` (spine-runtimes 4.2).

## License

This project is released under the MIT License.

You may use, copy, modify, merge, publish, distribute, sublicense, and sell copies
of the software, provided that the original copyright notice and license text are
included with substantial portions of the software.

The software is provided "as is", without warranty of any kind. See
[LICENSE](LICENSE) for the full license text.
