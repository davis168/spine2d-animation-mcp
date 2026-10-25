# spine2d-mcp

Tools that let an AI assistant build, edit, check and render Spine skeletons (2D skeletal animation data) for games.

## Versions

**Format version**:
The major.minor version a skeleton file is written in, such as 4.2 or 4.3; a runtime loads only files of its own format version.
_Avoid_: Spine version, data version, export version

**Editor version**:
A full release of the Spine editor application, such as 4.2.43 or 4.3.26; an editor writes files in its own format version.
_Avoid_: Spine version, Spine app version

**Runtime version**:
A release of the code a game uses to load and play skeletons (spine-core, spine-phaser), such as 4.2.120 or 4.3.13.
_Avoid_: Spine version, player version

**Stable**:
A release Esoteric Software publishes for production use; betas never count.
_Avoid_: Latest (unqualified)

**Supported format versions**:
Every stable format version from 4.2 up to the newest one this tool has been updated for (4.2 and 4.3 today).
_Avoid_: Target version, latest Spine

**Project format version**:
The format version a game project's runtime loads, as declared by its runtime dependency or by a version declaration.
_Avoid_: Default version, target version

**Version declaration**:
A statement written into a game project naming its project format version, for projects whose runtime dependency doesn't say (such as a game that loads its runtime from a CDN).
_Avoid_: Version config, version pin

**Version mismatch**:
A skeleton whose format version differs from the project format version of the game project it belongs to; the game cannot load it.
_Avoid_: Wrong version, incompatible file

**Conversion**:
Rewriting a skeleton from one format version into another.
_Avoid_: Migration, upgrade, export

**Lossy conversion**:
A conversion that would drop or change something because the target format version cannot express it.
_Avoid_: Downgrade (unqualified), partial conversion

## Projects

**Game project**:
The codebase of a game that loads skeletons with a Spine runtime; its format version requirement applies to every skeleton inside it.
_Avoid_: Project (unqualified)

**Spine project**:
A Spine editor's own working file (.spine), as opposed to the exported skeleton JSON a game loads.
_Avoid_: Project (unqualified)

## Drawing

**Draw order**:
The back-to-front order in which a skeleton's slots are drawn; the setup draw order is the order of the slots themselves, and animations can key changes to it.
_Avoid_: Z-order, layer order

**Draw order folder**:
A named group of slots that sit next to each other in the draw order, which can itself hold draw order folders; in 4.3 an animation can key the order of one draw order folder's slots on their own.
_Avoid_: Slot folder, slot group, layer folder, folder (unqualified: skins and animations have folders too)

**Mesh**:
An attachment whose image is stretched over a set of vertices, so it can bend with bones or be reshaped by deform keys.
_Avoid_: Mesh region, polygon (for this)

**Weights**:
How strongly each bone moves each vertex of a mesh; a weighted mesh bends with several bones.
_Avoid_: Skinning (for this), binding strength

**Linked mesh**:
A mesh that reuses another mesh's vertices and weights with a different image.
_Avoid_: Mesh copy, child mesh

**Skin bone**:
A bone (or, likewise, a constraint) that is active only while a skin that lists it is the current skin.
_Avoid_: Optional bone, skin-only bone

**Clipping attachment**:
A polygon on a slot that clips the slots drawn after it, up to an end slot; it can be inverse (hiding what is inside instead) in 4.3.
_Avoid_: Mask (in code), clip path
