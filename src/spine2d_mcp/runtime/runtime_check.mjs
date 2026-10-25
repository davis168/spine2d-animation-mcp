// Load a Spine skeleton JSON + atlas with the real spine-core runtime (4.2 or
// 4.3) and step every animation, reporting what the runtime itself never says:
// non-finite transforms, slots whose attachment cannot be found, and how far
// bones move. With --poses it also prints every bone's world transform at each
// sampled frame, so two runtimes' results can be compared. Physics is then not
// simulated (4.3 changed the simulation, so the same settings move bones a
// little differently); instead each physics constraint's settings, and its
// keyed values at every frame, are printed as the runtime read them.
//
// Usage: node runtime_check.mjs <skeleton.json> <file.atlas> <spine-core dir> [--poses]
// Prints one JSON object on stdout.

import { readFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [jsonPath, atlasPath, coreDir, mode] = process.argv.slice(2);
const report = { errors: [], animations: [] };
const round = (v) => Number(v.toFixed(4));

try {
  const entry = path.join(coreDir, "node_modules", "@esotericsoftware", "spine-core", "dist", "index.js");
  const spine = await import(pathToFileURL(entry).href);
  const pkg = JSON.parse(readFileSync(path.join(path.dirname(entry), "..", "package.json"), "utf8"));
  report.runtime = `spine-core ${pkg.version}`;

  // 4.3 split bones and slots into poses and renamed the setup/apply calls.
  const v43 = typeof spine.MixFrom !== "undefined";
  const setupPose = (sk) => (v43 ? sk.setupPose() : sk.setToSetupPose());
  const pose = (bone) => (v43 ? bone.appliedPose : bone);
  const attachment = (slot) => (v43 ? slot.appliedPose.attachment : slot.getAttachment());
  const apply = (animation, sk, last, time, events) =>
    v43
      ? animation.apply(sk, last, time, false, events, 1, spine.MixFrom.setup, false, false, false)
      : animation.apply(sk, last, time, false, events, 1, spine.MixBlend.setup, spine.MixDirection.mixIn);

  const atlas = new spine.TextureAtlas(readFileSync(atlasPath, "utf8"));
  const reader = new spine.SkeletonJson(new spine.AtlasAttachmentLoader(atlas));
  const data = reader.readSkeletonData(readFileSync(jsonPath, "utf8"));
  report.version = data.version;
  report.bones = data.bones.length;
  report.slots = data.slots.length;
  report.skins = data.skins.length;
  if (v43) {
    const kinds = { IkConstraintData: "ik", TransformConstraintData: "transform", PathConstraintData: "path",
      PhysicsConstraintData: "physics", SliderData: "slider" };
    report.constraints = { ik: 0, transform: 0, path: 0, physics: 0, slider: 0 };
    for (const c of data.constraints) report.constraints[kinds[c.constructor.name] ?? c.constructor.name] += 1;
  } else {
    report.constraints = {
      ik: data.ikConstraints.length,
      transform: data.transformConstraints.length,
      path: data.pathConstraints.length,
      physics: data.physicsConstraints.length,
    };
  }

  const skeleton = new spine.Skeleton(data);
  setupPose(skeleton);
  skeleton.updateWorldTransform(spine.Physics.update);
  const setup = skeleton.bones.map((b) => [pose(b).worldX, pose(b).worldY]);
  const missing = skeleton.slots
    .filter((s) => s.data.attachmentName && !attachment(s))
    .map((s) => `${s.data.name}: ${s.data.attachmentName}`);
  if (missing.length) report.errors.push(`setup attachments not found: ${missing.join(", ")}`);

  const poses = mode === "--poses";
  const physicsMode = poses ? spine.Physics.none : spine.Physics.update;
  const physics = v43 ? skeleton.physics : skeleton.physicsConstraints;
  const physicsValues = (c) => {
    const p = v43 ? c.appliedPose : c;
    return [p.inertia, p.strength, p.damping, p.massInverse, p.wind, p.gravity, p.mix].map(round);
  };
  if (poses) {
    report.poses = {};
    report.physics_frames = {};
    report.physics = physics.map((c) => {
      const d = c.data;
      return { name: d.name, settings: [d.x, d.y, d.rotate, d.scaleX, d.shearX, d.limit, d.step, d.inertiaGlobal,
        d.strengthGlobal, d.dampingGlobal, d.massGlobal, d.windGlobal, d.gravityGlobal, d.mixGlobal]
        .map((v) => (typeof v === "number" ? round(v) : v)) };
    });
  }

  const dt = 1 / 60;
  for (const animation of data.animations) {
    setupPose(skeleton);
    skeleton.updateWorldTransform(spine.Physics.reset);
    const events = [];
    const frames = [], physicsFrames = [];
    let finite = true, travel = 0, last = 0;
    const count = Math.max(1, Math.ceil(animation.duration / dt));
    for (let i = 0; i <= count; i++) {
      const time = Math.min(i * dt, animation.duration);
      setupPose(skeleton);
      apply(animation, skeleton, last, time, events);
      skeleton.update(dt);
      skeleton.updateWorldTransform(physicsMode);
      last = time;
      const frame = [];
      skeleton.bones.forEach((b, j) => {
        const p = pose(b);
        for (const v of [p.a, p.b, p.c, p.d, p.worldX, p.worldY]) if (!Number.isFinite(v)) finite = false;
        travel = Math.max(travel, Math.hypot(p.worldX - setup[j][0], p.worldY - setup[j][1]));
        if (poses) frame.push([p.a, p.b, p.c, p.d, p.worldX, p.worldY].map(round));
      });
      if (poses) {
        frames.push(frame);
        physicsFrames.push(physics.map(physicsValues));
      }
    }
    if (poses) {
      report.poses[animation.name] = frames;
      report.physics_frames[animation.name] = physicsFrames;
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
  if (report.poses) report.bone_names = skeleton.bones.map((b) => b.data.name);
} catch (e) {
  report.errors.push(String(e && e.message ? e.message : e));
}

process.stdout.write(JSON.stringify(report));
