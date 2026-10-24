// Load a Spine skeleton JSON + atlas with the real spine-core runtime and step
// every animation, reporting what the runtime itself never says: non-finite
// transforms, slots whose attachment cannot be found, and how far bones move.
//
// Usage: node runtime_check.mjs <skeleton.json> <file.atlas> <spine-core dir>
// Prints one JSON object on stdout.

import { readFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [jsonPath, atlasPath, coreDir] = process.argv.slice(2);
const report = { errors: [], animations: [] };

try {
  const entry = path.join(coreDir, "node_modules", "@esotericsoftware", "spine-core", "dist", "index.js");
  const spine = await import(pathToFileURL(entry).href);
  const pkg = JSON.parse(readFileSync(path.join(path.dirname(entry), "..", "package.json"), "utf8"));
  report.runtime = `spine-core ${pkg.version}`;

  const atlas = new spine.TextureAtlas(readFileSync(atlasPath, "utf8"));
  const reader = new spine.SkeletonJson(new spine.AtlasAttachmentLoader(atlas));
  const data = reader.readSkeletonData(readFileSync(jsonPath, "utf8"));
  report.version = data.version;
  report.bones = data.bones.length;
  report.slots = data.slots.length;
  report.skins = data.skins.length;
  report.constraints = {
    ik: data.ikConstraints.length,
    transform: data.transformConstraints.length,
    path: data.pathConstraints.length,
    physics: data.physicsConstraints.length,
  };

  const skeleton = new spine.Skeleton(data);
  skeleton.setToSetupPose();
  skeleton.updateWorldTransform(spine.Physics.update);
  const setup = skeleton.bones.map((b) => [b.worldX, b.worldY]);
  const missing = skeleton.slots
    .filter((s) => s.data.attachmentName && !s.getAttachment())
    .map((s) => `${s.data.name}: ${s.data.attachmentName}`);
  if (missing.length) report.errors.push(`setup attachments not found: ${missing.join(", ")}`);

  const dt = 1 / 60;
  for (const animation of data.animations) {
    skeleton.setToSetupPose();
    skeleton.updateWorldTransform(spine.Physics.reset);
    const events = [];
    let finite = true, travel = 0, last = 0;
    const frames = Math.max(1, Math.ceil(animation.duration / dt));
    for (let i = 0; i <= frames; i++) {
      const time = Math.min(i * dt, animation.duration);
      skeleton.setToSetupPose();
      animation.apply(skeleton, last, time, false, events, 1, spine.MixBlend.setup, spine.MixDirection.mixIn);
      skeleton.update(dt);
      skeleton.updateWorldTransform(spine.Physics.update);
      last = time;
      skeleton.bones.forEach((b, j) => {
        for (const v of [b.a, b.b, b.c, b.d, b.worldX, b.worldY]) if (!Number.isFinite(v)) finite = false;
        travel = Math.max(travel, Math.hypot(b.worldX - setup[j][0], b.worldY - setup[j][1]));
      });
    }
    report.animations.push({
      name: animation.name,
      duration: Number(animation.duration.toFixed(4)),
      timelines: animation.timelines.length,
      finite,
      max_bone_travel: Number(travel.toFixed(2)),
      events: events.length,
    });
    if (!finite) report.errors.push(`${animation.name}: non-finite bone transforms`);
  }
} catch (e) {
  report.errors.push(String(e && e.message ? e.message : e));
}

process.stdout.write(JSON.stringify(report));
