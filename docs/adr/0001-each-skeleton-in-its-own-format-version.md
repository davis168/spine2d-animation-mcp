# Each skeleton stays in its own format version; the game project decides new ones

Spine runtimes load only data of their own format version (4.2, 4.3) and never check it: a 4.3 runtime silently drops a 4.2 file's constraints, and games on different runtimes need different versions (a Phaser game on spine-phaser-v4 4.2.x next to a page on spine-player 4.3). So instead of targeting one "latest" version, every tool reads and writes a skeleton in the version its `skeleton.spine` header says, one tool API translating per version. New skeletons take the game project's version, found from its `@esotericsoftware/spine-*` dependency (the installed release first, else a range that pins one version) or a `.spine-version` declaration. Nothing changes version silently: when the project's version is unknown, unclear or contradicts the file, the tool returns a question (`needs_answer`) for the agent to put to the user, and moving between versions is an explicit `convert_skeleton` that refuses a lossy conversion unless the user agrees to the listed losses.

## Considered options

- **Upgrade everything to the newest version.** Breaks every game still on an older runtime, and the 4.3 editor itself only exports older data as a lossy last resort.
- **Ask through MCP elicitation (a dialog from the server).** Claude Desktop does not support it and non-interactive Claude Code runs cancel it, so the question would need a chat fallback anyway; the question is returned as a tool result instead.
- **Follow the Spine editor's own 4.2 -> 4.3 upgrade exactly.** Spine 4.3.26 drops a two-bone IK's `uniform` and deletes transform constraints whose mixes are all zero. Both runtimes apply two-bone `uniform`, so dropping it moves bones (the pose check catches it); the converter keeps both.

## Consequences

- Supporting a new format version (4.4, 5.0) means adding it to `spine/versions.py` once it is stable, plus checker rules and a conversion step; betas are never supported.
- The physics simulation changed in 4.3, so the same settings move bones slightly differently. The conversion check compares bone poses with physics off and compares physics settings and keys as each runtime reads them, rather than simulated motion.
- New files carry the full editor release in `skeleton.spine` (e.g. 4.3.26), because the 4.3 editor warns on any other value; runtimes read only the major.minor.
