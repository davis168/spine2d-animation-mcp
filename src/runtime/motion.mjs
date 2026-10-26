// Play animations in the real spine-core runtime (4.2 or 4.3) the way a game
// does (AnimationState, constraints, physics) and report how things move.
//
// Usage: node motion.mjs <skeleton.json> <file.atlas> <spine-core dir>, with a
// request on stdin:
//   {"mode": "sample", "skins": [...], "rate": 60,
//    "animations": [{"name": "idle", "cycles": 2}], "mixes": [{"from": "a", "to": "b", "mix": 0.2}]}
//     -> the setup pose, then every frame: each bone's world x, y, rotation
//        and scale along its x and y axes; each visible slot's attachment,
//        alpha, vertex centroid and four tracked vertices.
//   {"mode": "render", "skins": [...], "rate": 60, "animation": "idle", "times": [0, 0.5]}
//     -> at each time: every drawn slot's triangles (world vertices, atlas UVs,
//        page, colour, blend), clipped, in draw order.
// Prints one JSON object on stdout.

import { readFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [jsonPath, atlasPath, coreDir] = process.argv.slice(2);
const request = JSON.parse(readFileSync(0, "utf8"));
const report = { errors: [] };
const r2 = (v) => Math.round(v * 100) / 100;
const r3 = (v) => Math.round(v * 1000) / 1000;
const r5 = (v) => Math.round(v * 100000) / 100000;
const QUAD = [0, 1, 2, 2, 3, 0];

try {
  const entry = path.join(coreDir, "node_modules", "@esotericsoftware", "spine-core", "dist", "index.js");
  const spine = await import(pathToFileURL(entry).href);
  const pkg = JSON.parse(readFileSync(path.join(path.dirname(entry), "..", "package.json"), "utf8"));
  report.runtime = `spine-core ${pkg.version}`;

  const atlas = new spine.TextureAtlas(readFileSync(atlasPath, "utf8"));
  const data = new spine.SkeletonJson(new spine.AtlasAttachmentLoader(atlas)).readSkeletonData(readFileSync(jsonPath, "utf8"));
  const v43 = Array.isArray(data.constraints);
  const skeleton = new spine.Skeleton(data);
  const setupPose = () => (v43 ? skeleton.setupPose() : skeleton.setToSetupPose());
  const bonePose = (bone) => (v43 ? bone.appliedPose : bone);
  const slotPose = (slot) => (v43 ? slot.appliedPose : slot);
  const attachmentOf = (slot) => (v43 ? slot.appliedPose.attachment : slot.getAttachment());
  const drawOrder = () => (v43 ? skeleton.drawOrder.appliedPose : skeleton.drawOrder);

  const skin = new spine.Skin("motion");
  for (const name of request.skins || []) {
    const found = data.findSkin(name);
    if (!found) throw new Error(`no skin ${name}`);
    skin.addSkin(found);
  }
  if ((request.skins || []).length) skeleton.setSkin(skin);
  setupPose();

  // The attachment's world vertices, UVs, triangles and atlas region, or null.
  const geometry = (slot) => {
    const att = attachmentOf(slot);
    if (!att || !slot.bone.active) return null;
    const pose = slotPose(slot);
    if (att instanceof spine.RegionAttachment) {
      const vertices = new Float32Array(8);
      if (v43) {
        const index = att.sequence.resolveIndex(pose);
        att.computeWorldVertices(slot, att.getOffsets(pose), vertices, 0, 2);
        return { att, vertices, uvs: att.sequence.getUVs(index), triangles: QUAD, region: att.sequence.regions[index], hull: 4 };
      }
      att.computeWorldVertices(slot, vertices, 0, 2);
      return { att, vertices, uvs: att.uvs, triangles: QUAD, region: att.region, hull: 4 };
    }
    if (att instanceof spine.MeshAttachment) {
      const n = att.worldVerticesLength;
      const vertices = new Float32Array(n);
      if (v43) {
        att.computeWorldVertices(skeleton, slot, 0, n, vertices, 0, 2);
        const index = att.sequence.resolveIndex(pose);
        return { att, vertices, uvs: att.sequence.getUVs(index), triangles: att.triangles, region: att.sequence.regions[index],
          hull: Math.max(3, att.hullLength >> 1) };
      }
      att.computeWorldVertices(slot, 0, n, vertices, 0, 2);
      return { att, vertices, uvs: att.uvs, triangles: att.triangles, region: att.region, hull: Math.max(3, att.hullLength >> 1) };
    }
    return null;
  };
  const alphaOf = (slot, att) => skeleton.color.a * slotPose(slot).color.a * att.color.a;

  // 4.2 takes an Animation through setAnimationWith; 4.3's setAnimation takes either.
  const start = (state, animation, loop) =>
    state.setAnimationWith ? state.setAnimationWith(0, animation, loop) : state.setAnimation(0, animation, loop);

  // A player stepping the skeleton like a game loop: AnimationState, then physics.
  const player = (mixes) => {
    const stateData = new spine.AnimationStateData(data);
    for (const m of mixes || []) stateData.setMix(m.from, m.to, m.mix);
    const state = new spine.AnimationState(stateData);
    let started = false;
    return {
      state,
      step(dt) {
        if (started) state.update(dt);
        state.apply(skeleton);
        if (started) skeleton.update(dt);
        skeleton.updateWorldTransform(started ? spine.Physics.update : spine.Physics.reset);
        started = true;
      },
    };
  };

  const names = [];
  const nameIndex = new Map();
  const attachmentId = (att) => {
    if (!nameIndex.has(att)) {
      nameIndex.set(att, names.length);
      names.push(att.name);
    }
    return nameIndex.get(att);
  };
  const frame = () => {
    const bones = skeleton.bones.map((b) => {
      const p = bonePose(b);
      return [r3(p.worldX), r3(p.worldY), r3((Math.atan2(p.c, p.a) * 180) / Math.PI), r5(Math.hypot(p.a, p.c)),
        r5(Math.hypot(p.b, p.d))];
    });
    const slots = skeleton.slots.map((slot) => {
      const g = geometry(slot);
      if (!g) return null;
      const n = g.vertices.length >> 1;
      let cx = 0, cy = 0;
      for (let i = 0; i < n; i++) { cx += g.vertices[i * 2]; cy += g.vertices[i * 2 + 1]; }
      const out = [attachmentId(g.att), r5(alphaOf(slot, g.att)), r3(cx / n), r3(cy / n)];
      for (let k = 0; k < 4; k++) {
        const i = Math.floor((k * g.hull) / 4);
        out.push(r3(g.vertices[i * 2]), r3(g.vertices[i * 2 + 1]));
      }
      return out;
    });
    return { bones, slots };
  };

  const rate = request.rate || 60;
  const dt = 1 / rate;
  report.bones = data.bones.map((b) => ({ name: b.name, parent: b.parent ? b.parent.name : null, length: b.length }));
  report.slots = data.slots.map((s) => ({ name: s.name, bone: s.boneData.name }));
  report.constraints = (v43 ? data.constraints : [...data.ikConstraints, ...data.transformConstraints, ...data.pathConstraints])
    .filter((c) => c.bones && (c.target || c.slot))
    .map((c) => ({ name: c.name, bones: c.bones.map((b) => b.name), target: (c.target || c.source || {}).name || null }));

  report.physics_bones = (v43 ? data.constraints.filter((c) => c instanceof spine.PhysicsConstraintData) : data.physicsConstraints)
    .map((c) => c.bone.name);

  if (request.mode === "sample") {
    setupPose();
    skeleton.updateWorldTransform(spine.Physics.reset);
    report.setup = frame();
    report.animations = {};
    for (const want of request.animations || []) {
      const animation = data.findAnimation(want.name);
      if (!animation) throw new Error(`no animation ${want.name}`);
      setupPose();
      const play = player();
      start(play.state, animation, (want.cycles || 1) > 1);
      const total = animation.duration * (want.cycles || 1);
      const frames = [];
      for (let i = 0, n = Math.max(1, Math.round(total * rate)); i <= n; i++) {
        play.step(i === 0 ? 0 : dt);
        frames.push(frame());
      }
      report.animations[want.name] = { duration: animation.duration, cycles: want.cycles || 1, frames };
      if ((want.cycles || 1) > 1) {
        // A loop wraps at its duration, so its last pose is never sampled: pose it once, without physics.
        setupPose();
        const last = player();
        start(last.state, animation, false);
        last.state.update(animation.duration);
        last.state.apply(skeleton);
        skeleton.updateWorldTransform(spine.Physics.none);
        report.animations[want.name].end = frame();
      }
    }
    report.mixes = [];
    for (const m of request.mixes || []) {
      const from = data.findAnimation(m.from), to = data.findAnimation(m.to);
      if (!from || !to) throw new Error(`no animation ${!from ? m.from : m.to}`);
      setupPose();
      const play = player([m]);
      start(play.state, from, false);
      const frames = [];
      const at = Math.round(from.duration * rate);
      const end = at + Math.round((Math.max(m.mix, 0) + Math.min(to.duration, 1)) * rate);
      for (let i = 0; i <= end; i++) {
        if (i === at) start(play.state, to, false);
        play.step(i === 0 ? 0 : dt);
        frames.push(frame());
      }
      report.mixes.push({ from: m.from, to: m.to, mix: m.mix, switch: at / rate, frames });
    }
    report.attachments = names;
  } else if (request.mode === "render") {
    const animation = data.findAnimation(request.animation);
    if (!animation) throw new Error(`no animation ${request.animation}`);
    const play = player();
    start(play.state, animation, !!request.loop);
    const clipper = new spine.SkeletonClipping();
    const wanted = [...new Set(request.times)].sort((a, b) => a - b);
    report.frames = [];
    let now = 0;
    play.step(0);
    for (const time of wanted) {
      while (now + dt <= time + 1e-9) { play.step(dt); now += dt; }
      if (time - now > 1e-6) { play.step(time - now); now = time; }
      const draws = [];
      for (const slot of drawOrder()) {
        const att = attachmentOf(slot);
        if (att instanceof spine.ClippingAttachment && slot.bone.active) {
          v43 ? clipper.clipEnd(slot) : clipper.clipEndWithSlot(slot);
          v43 ? clipper.clipStart(skeleton, slot, att) : clipper.clipStart(slot, att);
          continue;
        }
        const g = geometry(slot);
        const pose = slotPose(slot);
        const alpha = g ? alphaOf(slot, g.att) : 0;
        if (g && alpha > 0 && g.region) {
          let { vertices, uvs, triangles } = g;
          if (clipper.isClipping()) {
            if (v43) {
              clipper.clipTrianglesUnpacked(vertices, 0, triangles, triangles.length, uvs, 2);
              vertices = clipper.clippedVerticesTyped.subarray(0, clipper.clippedVerticesLength);
              uvs = clipper.clippedUVsTyped.subarray(0, clipper.clippedVerticesLength);
              triangles = clipper.clippedTrianglesTyped.subarray(0, clipper.clippedTrianglesLength);
            } else {
              clipper.clipTrianglesUnpacked(vertices, triangles, triangles.length, uvs);
              ({ clippedVertices: vertices, clippedUVs: uvs, clippedTriangles: triangles } = clipper);
            }
          }
          const sc = skeleton.color, pc = pose.color, ac = g.att.color;
          draws.push({
            slot: slot.data.name, page: g.region.page.name, blend: slot.data.blendMode,
            color: [sc.r * pc.r * ac.r, sc.g * pc.g * ac.g, sc.b * pc.b * ac.b, alpha].map(r5),
            vertices: Array.from(vertices, r2), uvs: Array.from(uvs, r5), triangles: Array.from(triangles),
          });
        }
        v43 ? clipper.clipEnd(slot) : clipper.clipEndWithSlot(slot);
      }
      clipper.clipEnd();
      report.frames.push({ time, draws });
    }
  } else {
    throw new Error(`unknown mode ${request.mode}`);
  }
} catch (e) {
  report.errors.push(String(e && e.message ? e.message : e));
}

process.stdout.write(JSON.stringify(report));
