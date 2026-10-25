// Put back, in a 4.3 binary skeleton (.skel) the Spine editor exported, what
// the editor's JSON import lost: clipping attachments' convex and inverse
// flags, and draw order folder keys. spine-core's own reader reads the file
// and notes where each clipping attachment's flags byte and each animation's
// draw order folder section are; those bytes are patched, and the file is
// written only when spine-core reads the result back as intended.
//
// Usage: node binary_restore.mjs <skeleton.skel> <spine-core dir> < record.json
// The record is editor_quirks' {clips: [[skin, slot, name, {convex, inverse}]],
// folder_keys: {animation: [{slots, keys}]}}. Prints {restored, errors} on stdout.

import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [skelPath, coreDir] = process.argv.slice(2);
const report = { restored: [], errors: [] };
const CONVEX = 32, INVERSE = 64;

try {
  const entry = path.join(coreDir, "node_modules", "@esotericsoftware", "spine-core", "dist", "index.js");
  const spine = await import(pathToFileURL(entry).href);
  const record = JSON.parse(readFileSync(0, "utf8"));
  const original = new Uint8Array(readFileSync(skelPath));

  // Where the reader finds things: a clipping attachment's flags byte is the
  // first byte readAttachment reads; an animation's varints are candidates
  // for the start of its draw order folder section.
  let clips, animations, starts = null;
  const readInt = spine.BinaryInput.prototype.readInt;
  spine.BinaryInput.prototype.readInt = function (optimizePositive) {
    if (starts) starts.push(this.index);
    return readInt.call(this, optimizePositive);
  };
  const readAttachment = spine.SkeletonBinary.prototype.readAttachment;
  spine.SkeletonBinary.prototype.readAttachment = function (input, skeletonData, skin, slotIndex, placeholder) {
    const at = input.index;
    const attachment = readAttachment.apply(this, arguments);
    if (attachment instanceof spine.ClippingAttachment)
      clips.push({ at, key: [skin.name, skeletonData.slots[slotIndex].name, placeholder].join("/"), attachment });
    return attachment;
  };
  const readAnimation = spine.SkeletonBinary.prototype.readAnimation;
  spine.SkeletonBinary.prototype.readAnimation = function (input, name, skeletonData, nonessential) {
    const at = input.index;
    starts = [];
    const animation = readAnimation.apply(this, arguments);
    animations.push({ name, at, end: input.index, starts, animation, nonessential });
    starts = null;
    return animation;
  };
  // Every region resolves to one placeholder region: only the data matters.
  const atlas = new spine.TextureAtlas("page.png\nsize: 2,2\nfilter: Linear,Linear\nregion\nbounds: 0,0,2,2\n");
  atlas.findRegion = () => atlas.regions[0];
  const read = (bytes) => {
    clips = [];
    animations = [];
    const data = new spine.SkeletonBinary(new spine.AtlasAttachmentLoader(atlas)).readSkeletonData(bytes);
    return { data, clips, animations };
  };
  const before = read(original);
  if (!Array.isArray(before.data.constraints)) throw new Error("not a 4.3 binary skeleton");

  const folderTimelines = (animation) =>
    animation.timelines.filter((t) => t instanceof spine.DrawOrderFolderTimeline);

  // The tail of an animation: folder timelines, events, then (nonessential) its colour.
  const parseTail = (anim, at, data) => {
    try {
      const input = new spine.BinaryInput(original, [], at);
      const slotCount = data.slots.length, folders = [];
      const folderCount = input.readInt(true);
      if (folderCount > slotCount) return null;
      for (let i = 0; i < folderCount; i++) {
        const n = input.readInt(true);
        if (n > slotCount) return null;
        const slots = [];
        for (let ii = 0; ii < n; ii++) slots.push(input.readInt(true));
        const keyCount = input.readInt(true), times = [];
        if (keyCount > original.length) return null;
        for (let k = 0; k < keyCount; k++) {
          times.push(input.readFloat());
          const changes = input.readInt(true);
          if (changes > n) return null;
          for (let c = 0; c < changes; c++) input.readInt(true), input.readInt(true);
        }
        folders.push({ slots, times });
      }
      const eventsAt = input.index;
      const eventCount = input.readInt(true);
      for (let i = 0; i < eventCount; i++) {
        input.readFloat();
        const event = data.events[input.readInt(true)];
        if (!event) return null;
        input.readInt(false);
        input.readFloat();
        input.readString();
        if (event.audioPath) input.readFloat(), input.readFloat();
      }
      if (anim.nonessential) input.readInt32();
      if (input.index !== anim.end) return null;
      const have = folderTimelines(anim.animation);
      const same = have.length === folders.length && have.every((t, i) =>
        t.slots.join() === folders[i].slots.join() && Array.from(t.frames).join() === folders[i].times.join());
      return same ? { foldersAt: at, eventsAt, folderCount } : null;
    } catch {
      return null;
    }
  };

  const varint = (value) => {
    const out = [];
    value >>>= 0;
    while (value > 0x7f) {
      out.push((value & 0x7f) | 0x80);
      value >>>= 7;
    }
    out.push(value);
    return out;
  };
  const float = (value) => {
    const bytes = new Uint8Array(4);
    new DataView(bytes.buffer).setFloat32(0, value);
    return Array.from(bytes);
  };

  const patches = []; // {at, remove, bytes}, applied from the end
  const expected = { clips: {}, folders: {} };
  const bytes = original.slice();

  for (const [skin, slot, name, flags] of record.clips || []) {
    const key = [skin, slot, name].join("/");
    const clip = before.clips.find((c) => c.key === key);
    if (!clip) continue;
    if (!clip.attachment.convex || clip.attachment.inverse) continue; // changed in the editor: keep that
    bytes[clip.at] = (bytes[clip.at] & ~(CONVEX | INVERSE)) | (flags.convex ? CONVEX : 0) | (flags.inverse ? INVERSE : 0);
    expected.clips[key] = { convex: !!flags.convex, inverse: !!flags.inverse };
    const set = Object.keys(flags).filter((k) => flags[k]);
    report.restored.push(`clip ${key}: ${set.join(", ") || "not convex"}`);
  }

  const slotIndex = new Map(before.data.slots.map((s) => [s.name, s.index]));
  for (const [animName, entries] of Object.entries(record.folder_keys || {})) {
    const anim = before.animations.find((a) => a.name === animName);
    if (!anim) continue;
    const have = new Set(folderTimelines(anim.animation).map((t) => [...t.slots].sort((a, b) => a - b).join()));
    const add = [];
    for (const entry of entries) {
      const slots = entry.slots.map((s) => slotIndex.get(s));
      if (slots.some((i) => i === undefined) || have.has([...slots].sort((a, b) => a - b).join())) continue;
      const out = [...varint(slots.length), ...slots.flatMap(varint), ...varint(entry.keys.length)];
      for (const key of entry.keys) {
        const changes = (key.offsets || [])
          .map((o) => [entry.slots.indexOf(o.slot), o.offset])
          .sort((a, b) => a[0] - b[0]);
        out.push(...float(key.time || 0), ...varint(changes.length), ...changes.flatMap(([i, o]) => [...varint(i), ...varint(o)]));
      }
      add.push({ out, slots, keys: entry.keys.length, names: entry.slots });
    }
    if (!add.length) continue;
    const found = [...new Set(anim.starts)].map((at) => parseTail(anim, at, before.data)).filter(Boolean);
    if (found.length !== 1) {
      report.errors.push(`animation '${animName}': could not find its draw order folder keys in the binary`);
      continue;
    }
    const { foldersAt, eventsAt, folderCount } = found[0];
    // The animation's timeline count, then its folder count, then the new folders after the existing ones.
    const counted = new spine.BinaryInput(original, [], anim.at);
    const timelineCount = counted.readInt(true);
    patches.push({ at: anim.at, remove: counted.index - anim.at, bytes: varint(timelineCount + add.length) });
    const counter = new spine.BinaryInput(original, [], foldersAt);
    counter.readInt(true);
    patches.push({ at: foldersAt, remove: counter.index - foldersAt, bytes: varint(folderCount + add.length) });
    patches.push({ at: eventsAt, remove: 0, bytes: add.flatMap((a) => a.out) });
    expected.folders[animName] = add;
    for (const a of add) report.restored.push(`animation '${animName}': draw order keys of ${a.names.join(", ")}`);
  }

  if (report.restored.length && !report.errors.length) {
    let out = Array.from(bytes);
    for (const p of patches.sort((a, b) => b.at - a.at)) out.splice(p.at, p.remove, ...p.bytes);
    const patched = new Uint8Array(out);
    const after = read(patched);
    for (const [key, flags] of Object.entries(expected.clips)) {
      const clip = after.clips.find((c) => c.key === key);
      if (!clip || clip.attachment.convex !== flags.convex || clip.attachment.inverse !== flags.inverse)
        report.errors.push(`clip ${key} did not read back as restored`);
    }
    for (const [animName, add] of Object.entries(expected.folders)) {
      const anim = after.animations.find((a) => a.name === animName);
      const got = anim ? folderTimelines(anim.animation).map((t) => [t.slots.join(), t.frames.length].join("|")) : [];
      for (const a of add)
        if (!got.includes([a.slots.join(), a.keys].join("|")))
          report.errors.push(`animation '${animName}': the restored folder keys did not read back`);
    }
    if (after.data.animations.length !== before.data.animations.length || after.data.skins.length !== before.data.skins.length)
      report.errors.push("the patched file reads back with different skins or animations");
    if (!report.errors.length) writeFileSync(skelPath, patched);
  }
  if (report.errors.length) report.restored = [];
} catch (e) {
  report.errors.push(String(e?.message ?? e));
  report.restored = [];
}
process.stdout.write(JSON.stringify(report));
