# spine2d-mcp

An MCP server that builds, edits, checks, converts and renders [Spine](http://esotericsoftware.com/) characters in format **4.2 or 4.3**: from a layered PSD to a rigged, animated skeleton that loads in the Spine editor and in the game's own Spine runtime (spine-phaser-v4, spine-player, ...).

The goal is to cut the manual rig-and-animate work for a character from days down to roughly an hour.

## Status

PSD → rig → animations → checked → previewed, end to end through MCP tools, in either format version. Every editing tool checks its result against the skeleton's own format before writing. Generated 4.2 skeletons round-trip through the Spine 4.2.43 editor (CLI import, then JSON export) and play in spine-core 4.2.120; 4.3 skeletons, sliders included, round-trip through Spine 4.3.26 and play in spine-core 4.3.13. Converted skeletons are checked pose by pose in both runtimes. Not yet verified in a Phaser game or in Unity.

The Spine editor has no live scripting API, so the server works on the files (skeleton JSON, atlas, images) and drives the editor through its command line interface.

## Tools

| Tool | What it does |
|---|---|
| `import_psd_to_spine` | Layered PSD → skeleton JSON, atlas, page PNG(s) and per-part images |
| `inspect_skeleton` | Bones (setup and world positions), slots in draw order, skins, constraints, events, and what each animation keys |
| `validate_skeleton` | Format check against the file's version, plus whether it matches the game project's; `runtime=true` also loads and plays it in that version's spine-core |
| `add_animation` / `remove_animation` | Presets (idle, walk, wave, jump) or keyframes with an easing shorthand; preset + keyframe overrides |
| `edit_rig` | Add/set/rename/remove bones, move a pivot without moving the art, reparent, add/set/rename/remove slots, draw order folders, draw order (draw order keys keep showing the same order) |
| `add_constraint` / `remove_constraint` | IK, transform, physics and (4.3) slider constraints (physics presets: hair, cloth, tail, antenna, jiggle) |
| `add_attachment` | Add a PNG as a region attachment on a slot (mouth shapes, alternate hands) and repack the atlas |
| `add_clipping` | A clipping polygon (mask) on a slot, clipping the slots drawn after it up to `end`; (4.3) convex and inverse |
| `set_draw_order_keys` | An animation's draw order keys by back-to-front slot order: the whole skeleton's, or (4.3) one draw order folder's |
| `pack_atlas` | Repack the atlas from the images folder |
| `convert_skeleton` | Convert a skeleton between 4.2 and 4.3 into a new file; refuses a lossy conversion unless agreed; verifies poses in both runtimes |
| `spine_import` | Import the JSON into a `.spine` project with the Spine CLI, using an editor of the file's version |
| `spine_export` | Render PNG frame sequences or GIFs, or export JSON/binary, with the Spine CLI |
| `preview_html` | A self-contained Spine Web Player page in the file's version (skeleton, atlas and images embedded) |

A typical session, in plain language to Claude: *"Import /path/to/hero.psd, add the idle, walk and wave presets, validate with the runtime check, export GIFs and make a preview."*

### The PSD importer

Layers named after body parts (`head`, `body`/`torso`, `arm_left`, `hand_right`, `leg_left`, `foot_right`, …) become:

- `<name>.json`: the skeleton (bones, slots, default skin, region attachments) in the game project's format version
- `<name>.atlas` and `<name>.png`: the atlas, on the smallest power-of-two page that fits
- `images/<part>.png`: one image per attachment; the Spine editor displays these, and the skeleton's `images` path points here

Rig conventions:

- **Multi-segment skeleton** (`root → hip → torso → chest → neck → head`, arms off `chest`, legs off `hip`); missing intermediate bones are created as positioned virtual bones.
- **Joint pivots at the round cap** — arms and legs pivot at the centre of their joint-end cap (half the limb width in from the joint edge), so the cap stays put as the joint turns; hands and feet turn about the wrist and ankle (the parent limb's far cap); the head pivots at the chin. A `<part>[pivot]` marker layer overrides any of these.
- **Origin at the feet** — the bottom-centre of the character, not the PSD canvas corner.
- **PSD draw order** becomes Spine slot order.
- Bones have no setup rotation (limbs hang along −y).

### Animations

`add_animation` takes Spine animation JSON (in the skeleton's format version) plus one convenience: a key may carry `"ease"` instead of a raw curve. As in Spine, a key's easing shapes the segment that starts at it. Easing names: `linear`, `stepped`, `in`, `out`, `inout`, `smooth`, `back`, or `[x1, y1, x2, y2]` (CSS cubic-bezier); the tool computes the absolute per-channel bezier handles Spine stores. Rotations use `value` in degrees (CCW); `angle` (the 3.x key, which 4.x silently reads as 0) is rejected.

```json
{"bones": {"head": {"rotate": [{"time": 0, "value": 0, "ease": "inout"},
                                {"time": 0.3, "value": -8, "ease": "inout"},
                                {"time": 0.6, "value": 0}]}},
 "events": [{"time": 0.3, "name": "nod_down"}]}
```

Presets are tuned for importer rigs and scale with the skeleton's height: `idle` (2.0 s loop), `walk` (0.8 s in-place front-view loop; only the lifted leg moves, so the planted foot never slides), `wave` (2.5 s, either arm), `jump` (1.3 s, squash and stretch about the feet). `speed`, `amount` and `bone_map` (preset bone → your bone) adapt them.

### Draw order and draw order folders

A draw order key may give `"order"` (every slot, back to front) instead of the offsets Spine stores. As in the editor, a **draw order folder** is the slots whose names share a prefix (`face/eye`, `face/mouth`; nested: `face/brows/left`), kept next to each other in the draw order: `edit_rig` `rename_slot` (`head` → `face/head`) and `rename_folder` manage them. In 4.3 a draw order folder can have its own draw order keys (`set_draw_order_keys` with `folder`, or `drawOrderFolder` in `add_animation`), which reorder only its slots, over the whole draw order's keys. Converting to 4.2 folds them into whole draw order keys that show the same orders.

### Limits

- **IK** aims a bone's x axis at the target, so it suits rigs whose bones point along their limbs. Importer rigs have unrotated bones; `add_constraint` warns when the setup pose would swing. Aim importer limbs with rotate keys.
- **Path constraints** are preserved and checked but cannot be added (they need path attachments).
- **4.3 extras the tools keep and check but do not create**: linked meshes whose source mesh is in another slot.
- **What the Spine 4.3 editor (4.3.26) changes when it imports JSON**: every clip comes in convex and none inverse (it does not read either flag), and a nested draw order folder's draw order keys are dropped; the runtime reads all of them. `add_clipping`, `set_draw_order_keys` and `spine_import` say when this applies.
- `move_pivot`, reparenting and slot rebinding work in world space and require `inherit: normal` along the chain.

## Spine versions

A Spine runtime loads only skeleton data of its own **format version** (the major.minor in `skeleton.spine`: 4.2, 4.3) and never checks it, so this server handles each skeleton in the version it is written in. Supported: **4.2 and 4.3**, the stable releases (betas never count; a newer version is added once it is stable). See [ADR 0001](docs/adr/0001-each-skeleton-in-its-own-format-version.md).

| Format | Spine editor | spine-core / spine-player | Phaser 4 runtime |
|---|---|---|---|
| 4.2 | 4.2.43 | 4.2.120 | `@esotericsoftware/spine-phaser-v4@v4.2-latest` (Phaser ≥ 4.1) |
| 4.3 | 4.3.26 | 4.3.13 | `@esotericsoftware/spine-phaser-v4@latest` (Phaser ≥ 4.2.1) |

- **Existing files** are edited, checked, previewed and run in their own version.
- **New files** (`import_psd_to_spine`, data exported from a `.spine` project) take, in order: the `format_version` you pass; the **game project's version**, found by searching up from the output folder to the repository root for a `package.json` with an `@esotericsoftware/spine-*` dependency (the installed release first, else a range that pins one version: `~4.2.0` counts, `^4.2.0` does not, since a fresh install of it gets 4.3) or a `.spine-version` file holding e.g. `4.3`; then `$SPINE_FORMAT_VERSION`.
- **When in doubt, the tools ask.** If nothing says which version a game project uses, its signals disagree or are unclear, or a file's version differs from its game project's, the tool writes nothing and returns `needs_answer` with a question and options; the agent asks you in chat. Your answer about the project can be saved as its `.spine-version` (`save_project_version`), so it is asked once. If you already named a version in your request, the agent uses it without asking (`allow_version_mismatch`) and mentions the difference once.
- **Converting** is explicit: `convert_skeleton` writes `hero-4.3.json` next to `hero.json`, sharing its atlas and images (tools find `hero.atlas` for it). 4.2 → 4.3 loses nothing; 4.3 → 4.2 lists what 4.2 cannot hold (sliders, transform property mappings, scale modes, ...) and drops it only if you agree.
- **Spine editor**: an editor imports JSON of its own version only, so the CLI tools pick one per file: `editor_version`, else a matching release in `SPINE_EDITOR_VERSION` (one or several, e.g. `4.2.43,4.3.26`), else the newest stable release the Spine launcher has downloaded; with none, they say which release to download. New files carry that full release in `skeleton.spine` (e.g. `4.3.26`), because the 4.3 editor warns on any other value; runtimes read only the major.minor.
- `SPINE_EXECUTABLE` points at the Spine executable when it is not in the standard place (`/Applications/Spine.app/Contents/MacOS/Spine` on macOS).

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Optional: Node.js with npm, for the runtime check and the conversion check (spine-core 4.2.120 and 4.3.13 are installed on first use into `~/.cache/spine2d-mcp`, or `$SPINE2D_MCP_CACHE`)
- Optional: the Spine editor (Essential or Professional), for `spine_import` and `spine_export`

## Setup and tests

```bash
uv sync
uv run pytest
```

Tests that need Node or the Spine editor skip when they are not installed.

## Use it from Claude

Register the server with an absolute path to `uv`; Claude Desktop starts servers with a minimal `PATH`, so a bare `uv` fails with `spawn uv ENOENT`. Find yours with `which uv`.

**Claude Code** (`--scope user` makes it available in every project; the optional `-e` pins editor releases for the CLI tools, which otherwise use the newest installed one per version):

```bash
claude mcp add --scope user spine2d -e SPINE_EDITOR_VERSION=4.2.43,4.3.26 -- /absolute/path/to/uv --directory /absolute/path/to/spine2d-animation-mcp run spine2d-mcp
```

Run `/mcp` in a new session to check it is connected.

**Claude Desktop** — add to `mcpServers` in `~/Library/Application Support/Claude/claude_desktop_config.json` (Settings → Developer → Edit Config), then quit with ⌘Q and reopen:

```json
"mcpServers": {
  "spine2d": {
    "command": "/absolute/path/to/uv",
    "args": ["--directory", "/absolute/path/to/spine2d-animation-mcp", "run", "spine2d-mcp"],
    "env": {"SPINE_EDITOR_VERSION": "4.2.43,4.3.26"}
  }
}
```

## Project layout

```
src/spine2d_mcp/
  server.py            FastMCP server: tool definitions
  pipeline.py          PSD -> Spine project orchestration
  edits.py             load -> change -> check -> save for every editing tool
  conversion.py        convert_skeleton: target, questions, the converted file, verification
  project.py           a game project's format version, and when to ask
  preview.py           Spine Web Player page
  spine_cli.py         Spine editor CLI: import, export, project info
  runtime/             spine-core runtime check and pose comparison (Node script + wrapper)
  ops/                 edits as pure functions: animation, rig, constraints, attachments
  psd/parser.py        PSD -> flat layer list (+ pivot markers)
  psd/conventions.py   bone hierarchy, joint sides, cap pivots, [pivot] tag
  spine/schema.py      Pydantic models the importer builds with
  spine/builder.py     layers -> SpineProject
  spine/writer.py      SpineProject -> JSON
  spine/validator.py   importer-level structural validation
  spine/versions.py    supported format versions and their editor/runtime releases
  spine/document.py    skeleton JSON as a dict (either constraint layout): load, save, world transforms
  spine/check.py       format checker, per version
  spine/convert.py     4.2 <-> 4.3 conversion of the skeleton document
  spine/inspect.py     skeleton summary for inspect_skeleton
  spine/animation.py   easing shorthand -> Spine animation JSON
  spine/presets.py     idle, walk, wave, jump
  atlas/packer.py      rectpack bin packing
  atlas/writer.py      .atlas text + composite PNG
reference/             SkeletonJson-4.2.java, SkeletonJson-4.3.java (spine-runtimes), spineboy.atlas
docs/adr/              decisions
tests/                 pytest suite (synthetic PSD fixtures, a 4.2 rig using every constraint kind)
```

Format source of truth: `reference/SkeletonJson-4.2.java` and `reference/SkeletonJson-4.3.java` (spine-runtimes 4.2 and 4.3).

## License

This project is released under the MIT License.

You may use, copy, modify, merge, publish, distribute, sublicense, and sell copies
of the software, provided that the original copyright notice and license text are
included with substantial portions of the software.

The software is provided "as is", without warranty of any kind. See
[LICENSE](LICENSE) for the full license text.
