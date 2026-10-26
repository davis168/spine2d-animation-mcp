"""Measure how animations move, as the viewer sees them.

The input is runtime/motion.mjs's samples: every frame, each bone's world
transform and each visible slot's attachment centre and four tracked vertices,
played in spine-core like a game plays them (AnimationState, constraints,
physics). From the slots' screen motion this finds the faults animators look
for, ranked like a critique:

2. breaks the read: hitches (velocity jumps while moving), teleports, loop
   seams, transitions that jerk, limbs drawn longer than in the setup pose;
3. weight and timing: abrupt starts and stops (no slow out / slow in), constant
   speed (linear spacing), straight paths where arcs are wanted, parts that all
   start and stop on one frame (no overlap), long single moves (floaty),
   linear keys far apart, nothing that settles (no poses);
4. life: dead-still holds, loops that barely move or move in lockstep,
   mirrored (twinned) limbs, attachment swaps with nothing moving around them;
5. polish: the same ease on every key, blocking (stepped) keys left in.

Rank 1 (outcome integrity, compliance) is not something motion shows. Every
threshold is a starting point at the skeleton's size, not a rule.
"""

from __future__ import annotations

import re
import warnings

import numpy as np

HOLD = 0.04  # body speed below this share of its peak: holding a pose
MOVING = 0.10  # a part moving: above this share of its own peak speed
MIN_TRAVEL = 0.005  # a move counts when its farthest point travels 0.5% of the skeleton's size
HITCH = 0.5  # a velocity change within one sample above this share of the move's peak, while moving
STOPPED = 0.12  # an abrupt stop/start: from/to below this share of the move's peak...
FAST = 0.5  # ...within one sample of above this share
STRAIGHT = 0.02  # a path whose farthest point from its chord is under 2% of the chord is straight
CONSTANT = 0.15  # speed varying less than this (coefficient of variation) across a move: linear spacing
FROZEN = 0.002  # a hold where nothing moves faster than 0.2% of the skeleton's size per second
TELEPORT = 0.05  # a jump of 5% of the skeleton's size in one sample, 3x the samples around it
STRETCH = 1.05  # a bone drawn 5% larger along one of its axes than in the setup pose
FLOATY = 20  # frames: one key-to-key move longer than this
LINEAR_GAP = 4  # frames: linear keys this far apart change speed abruptly at each key
MAX_PER_KIND = 4  # findings of one kind per animation; the rest are counted

_LEFT_RIGHT = [(r"left", "right"), (r"Left", "Right"), (r"(?<![a-z])l(?![a-z])", "r"),
               (r"(?<![a-zA-Z])L(?![a-zA-Z])", "R")]


def keyed_times(animation: dict) -> list[float]:
    """Every time anything is keyed (events excluded), sorted."""
    times: set[float] = set()

    def walk(node, key=None):
        if key == "events":
            return
        if isinstance(node, list):
            if node and all(isinstance(k, dict) for k in node):
                times.update(round(float(k.get("time", 0)), 4) for k in node)
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
    walk(animation)
    return sorted(times)


def analyze(doc: dict, sampled: dict, rate: float, loops: set[str]) -> dict:
    """Findings (ranked) and measurements per animation."""
    bones = [b["name"] for b in sampled["bones"]]
    setup = _arrays([sampled["setup"]])
    ctx = {
        "fps": float((doc.get("skeleton") or {}).get("fps") or 30), "rate": rate, "size": _size(setup) or 1.0,
        "bones": bones, "parents": [bones.index(b["parent"]) if b["parent"] else -1 for b in sampled["bones"]],
        "slot_bone": [bones.index(s["bone"]) for s in sampled["slots"]],
        "slot_names": [s["name"] for s in sampled["slots"]], "setup": setup,
        "physics": set(sampled.get("physics_bones") or []), "constraints": sampled.get("constraints") or [],
    }
    report: dict = {"animations": {}, "findings": []}
    for name, played in sampled["animations"].items():
        anim = (doc.get("animations") or {}).get(name) or {}
        frames = _arrays(played["frames"])
        start = round(played["duration"] * rate) if played["cycles"] > 1 else 0
        last = _arrays([played["end"]]) if played.get("end") else None
        found, measured = _animation(ctx, name, anim, frames, start, played["duration"], name in loops, last)
        report["animations"][name] = measured
        report["findings"] += found
    for mix in sampled.get("mixes") or []:
        report["findings"] += _mix(ctx, mix)
    report["findings"].sort(key=lambda f: (f["rank"], f["animation"], f["evidence"].get("time", 0)))
    return report


def _arrays(frames: list[dict]) -> dict:
    bones = np.array([f["bones"] for f in frames], dtype=float)
    n_slots = len(frames[0]["slots"])
    att = np.full((len(frames), n_slots), -1, dtype=int)
    points = np.full((len(frames), n_slots, 5, 2), np.nan)
    for i, f in enumerate(frames):
        for k, s in enumerate(f["slots"]):
            if s is not None:
                att[i, k] = s[0]
                points[i, k] = np.array(s[2:12]).reshape(5, 2)
    return {"bones": bones, "att": att, "points": points}


def _size(frames: dict) -> float:
    pts = frames["points"][0][~np.isnan(frames["points"][0][..., 0])]
    return float(max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1]))) if len(pts) else 0.0


def _quiet(fn, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return fn(*args, **kwargs)


def _velocities(ctx: dict, frames: dict) -> tuple[np.ndarray, np.ndarray]:
    """Per sample and slot: each tracked point's velocity (NaN across swaps) and their mean speed."""
    p = frames["points"]
    v = np.full(p.shape, np.nan)
    same = (frames["att"][1:] == frames["att"][:-1]) & (frames["att"][1:] >= 0)
    v[1:] = np.where(same[..., None, None], (p[1:] - p[:-1]) * ctx["rate"], np.nan)
    return v, np.linalg.norm(v, axis=-1).mean(axis=-1)


def _animation(ctx, name, anim, frames, start, duration, loop, last=None):
    found: list[dict] = []
    measured: dict = {"duration": round(duration, 3)}
    if duration <= 0:
        measured["note"] = "a pose (duration 0), not motion"
        return found, measured
    rate, size = ctx["rate"], ctx["size"]
    v, speed = _velocities(ctx, frames)
    end = len(speed) - 1
    lo = max(start, 1)
    points = frames["points"]
    extent = np.nan_to_num(np.linalg.norm(_quiet(np.nanmax, points, axis=(0, 2)) - _quiet(np.nanmin, points, axis=(0, 2)),
                                          axis=-1))
    shown = ~np.isnan(speed)
    body = np.nansum(np.nan_to_num(speed) * extent, axis=1) / np.maximum((shown * extent).sum(axis=1), 1e-9)
    peak = float(body[lo:].max()) if end >= lo else 0.0
    measured["peak_speed"] = round(peak / size, 4)
    if peak < 0.0005 * size:
        measured["note"] = "does not move"
        return found, measured

    def at(i):
        return round((i - start) / rate, 3)

    def add(kind, rank, principle, what, evidence, fix):
        found.append({"animation": name, "kind": kind, "rank": rank, "principle": principle, "what": what,
                      "evidence": evidence, "fix": fix})

    held = body < HOLD * peak
    holds = _runs(held, lo, end)
    measured["poses"] = [{"time": at(a), "hold": round((b - a + 1) / rate, 3)} for a, b in holds]
    window = max(2, round(0.08 * rate))
    minima = [i for i in range(lo + 1, end) if not held[i] and body[i] < 0.25 * peak
              and body[i] == body[max(lo, i - window):i + window + 1].min()]
    measured["passing_poses"] = [at(i) for i in minima]

    for kind, a, b, slots, share in _group_events(_part_events(ctx, frames, v, speed, lo, end)):
        keys = _keys_near(ctx, anim, slots, at(a), at(b) if b > a else None)
        parts, one = ", ".join(slots), len(slots) == 1
        evidence = {"slots": slots, "time": at(a), **({"until": at(b)} if b > a else {}), "keys": keys}
        if kind == "teleport":
            add(kind, 2, "5 follow-through, 6 slow in / slow out", f"{parts} jump{'s' if one else ''} {share:.0f} "
                f"units in one frame at {at(a)} s, far more than the frames around it", evidence,
                "key the travel over several frames, or swap the attachment on a frame hidden by motion")
        elif kind == "hitch":
            add(kind, 2, "6 slow in / slow out, 5 follow-through", f"{parts} jerk{'s' if one else ''} at {at(a)} s: "
                f"the velocity changes by {100 * share:.0f}% of the move's peak in one frame while moving", evidence,
                "carry the speed through the key: a bezier instead of a linear key mid-move, or spread the "
                "direction change over 2-4 frames")
        elif kind == "linear spacing":
            add(kind, 3, "6 slow in / slow out", f"{parts} move{'s' if one else ''} at a constant speed from "
                f"{at(a)} to {at(b)} s and start or stop dead", evidence,
                "ease out of the start pose (slow out) and into the end pose (slow in), or add a breakdown "
                "favouring the end pose")
        elif kind == "abrupt stop":
            add(kind, 3, "6 slow in / slow out", f"{parts} stop{'s' if one else ''} dead at {at(a)} s, from "
                f"{100 * share:.0f}% of the move's speed in one frame", evidence,
                "ease into the pose (slow in) over 2-4 frames, or overshoot slightly and settle; keep it sharp only "
                "for a contact (a hand landing on the table)")
        elif kind == "abrupt start":
            add(kind, 3, "6 slow in / slow out, 2 anticipation", f"{parts} start{'s' if one else ''} at "
                f"{100 * share:.0f}% of the move's speed at {at(a)} s, with no slow out", evidence,
                "ease out of the pose over 2-4 frames; a system or dealer move may take a small opposite move "
                "first (anticipation)")
        elif kind == "straight path":
            add(kind, 3, "7 arcs", f"{parts} travel{'s' if one else ''} {share:.0f} units in a straight line from "
                f"{at(a)} to {at(b)} s", evidence,
                "curve the path: key X and Y with different eases, add a breakdown off the line, or move it by "
                "rotating a parent")

    if loop and start:
        _loop_checks(ctx, frames, start, end, last, measured, add)

    for j, ratio, i in _stretches(ctx, frames, lo):
        bone = ctx["bones"][j]
        add("stretch", 2, "1 squash & stretch, 11 solid drawing",
            f"{bone} is drawn {100 * (ratio - 1):.0f}% larger along one axis than in the setup pose at {at(i)} s",
            {"bone": bone, "time": at(i), "ratio": round(ratio, 3),
             "keys": _keys_near(ctx, anim, [], at(i), bones=[bone])},
            "on a realistic limb, reach with IK, the shoulder and a torso lean, and keep scale at or under 1 "
            "(skip this if the part is meant to squash and stretch)")

    if not loop:
        for a, b in _runs(~held, lo, end):
            if (b - a) / rate < 0.15:
                continue
            spans = _bone_spans(ctx, frames, a, b)
            starts, stops = [s for s, _ in spans.values()], [e for _, e in spans.values()]
            if len(spans) >= 3 and max(starts) - min(starts) <= 1 and max(stops) - min(stops) <= 1:
                add("no overlap", 3, "5 follow-through & overlap",
                    f"{len(spans)} parts start and stop on the same frame between {at(a)} and {at(b)} s",
                    {"time": at(a), "until": at(b), "bones": sorted(spans)},
                    "lead with one part and let the others follow 1-3 frames (30-100 ms) later down each chain; let "
                    "loose parts (hands, head, hair) overshoot and settle")
            offsets = _chain_offsets(ctx, spans)
            if offsets:
                measured.setdefault("overlap", []).append({"time": at(a), "until": at(b), "chains": offsets[:8]})
        for a, b in holds:
            if (b - a + 1) / rate >= 0.3 and float(np.nan_to_num(_quiet(np.nanmax, speed[a:b + 1]))) < FROZEN * size:
                add("frozen hold", 4, "8 secondary action", f"everything is dead still from {at(a)} to {at(b)} s",
                    {"time": at(a), "until": at(b)},
                    "keep a moving hold: drift 1-3% of the move's range (a slow settle, breathing, a small head "
                    "turn); skip this if the game mixes to the next animation at once")
        if not minima and not holds and duration >= 1.0:
            add("no poses", 3, "4 pose to pose, 9 timing",
                f"nothing settles in {name}: no pose is held or passed through slowly enough to read",
                {"time": 0.0, "until": round(duration, 3)},
                "plan key poses (start, anticipation, extreme, settle) and give each a few frames of slow-down")

    for left, right in _twins(ctx["bones"]):
        if left in ctx["physics"] or right in ctx["physics"]:
            continue  # physics answers the same parent motion the same way on both sides
        da = _local_rotation(ctx, frames, ctx["bones"].index(left))[lo:]
        db = _local_rotation(ctx, frames, ctx["bones"].index(right))[lo:]
        da, db = da - da[0], db - db[0]
        if np.ptp(da) < 3 or np.ptp(db) < 3:
            continue
        corr = float(np.corrcoef(da, db)[0, 1])
        ratio = float(np.ptp(da) / np.ptp(db))
        if abs(corr) > 0.98 and 0.85 < ratio < 1.18:
            add("twinning", 4, "11 solid drawing",
                f"{left} and {right} turn as mirror images (correlation {corr:+.2f}, amplitude ratio {ratio:.2f})",
                {"bones": [left, right], "time": 0.0}, "change one side's pose and amplitude, then offset it 1-2 frames")

    for slot, i in _swaps(ctx, frames, lo, end, held):
        add("swap while still", 4, "5 follow-through", f"{slot} swaps its attachment at {at(i)} s while nothing moves",
            {"slot": slot, "time": at(i)},
            "swap on a frame hidden by motion, or add an in-between drawing (a half-lid for a blink)")

    found += _key_findings(ctx, name, anim, loop)
    return _cap(found), measured


def _stretches(ctx, frames, lo):
    """(bone, worst ratio, sample) for bones with visible parts drawn more than STRETCH times larger
    along their x or y axis than in the setup pose; a bone that only inherits its parent's is left out."""
    setup = ctx["setup"]["bones"][0, :, 3:5]
    ratio = (frames["bones"][lo:, :, 3:5] / np.maximum(setup, 1e-9)).max(axis=-1)
    worst = ratio.max(axis=0) if len(ratio) else np.zeros(len(ctx["bones"]))
    shown = {ctx["slot_bone"][k] for k in range(len(ctx["slot_names"])) if (frames["att"][lo:, k] >= 0).any()}
    out = []
    for j in sorted(shown):
        parent = ctx["parents"][j]
        if worst[j] > STRETCH and not (parent >= 0 and worst[parent] > STRETCH and worst[j] <= worst[parent] + 0.01):
            out.append((j, float(worst[j]), int(ratio[:, j].argmax()) + lo))
    return out


def _part_events(ctx, frames, v, speed, lo, end):
    """Per visible part: (kind, first sample, last sample, slot, measure) for teleports, hitches,
    abrupt starts and stops, linear spacing and straight paths."""
    size, rate, points = ctx["size"], ctx["rate"], frames["points"]
    events = []
    for k, slot in enumerate(ctx["slot_names"]):
        part = speed[:, k]
        jumps = np.linalg.norm(points[1:, k] - points[:-1, k], axis=-1).max(axis=-1)
        for i in range(lo, end + 1):
            if np.isnan(part[i]) or jumps[i - 1] < TELEPORT * size:
                continue
            before = [jumps[j - 1] for j in range(max(lo, i - 3), i) if not np.isnan(part[j])]
            after = [jumps[j - 1] for j in range(i + 1, min(end, i + 3) + 1) if not np.isnan(part[j])]
            usual = max(float(np.median(before)) if before else 0.0, float(np.median(after)) if after else 0.0)
            if jumps[i - 1] > 3 * usual:  # bigger than the motion on both sides, not a fast start or stop
                events.append(("teleport", i, i, slot, float(jumps[i - 1])))
        for a, b, top in _moves(part, points[:, k], lo, end, size):
            for i in range(a, b):
                if min(part[i], part[i + 1]) > 0.4 * top:
                    change = float(np.linalg.norm(v[i + 1, k] - v[i, k], axis=-1).mean())
                    if change > HITCH * top:
                        events.append(("hitch", i, i, slot, change / top))
            before = float(np.nan_to_num(part[a - 1])) if a - 1 >= lo else None
            after = float(np.nan_to_num(part[b + 1])) if b + 1 <= end else None
            starts_dead = before is not None and before < STOPPED * top and part[a] > FAST * top
            stops_dead = after is not None and after < STOPPED * top and part[b] > FAST * top
            seg = part[a:b + 1]
            middle = seg[len(seg) // 10: len(seg) - len(seg) // 10]
            if len(seg) >= 4 and np.std(middle) / max(np.mean(middle), 1e-9) < CONSTANT and (starts_dead or stops_dead):
                events.append(("linear spacing", a, b, slot, None))
            else:
                if stops_dead:
                    events.append(("abrupt stop", b + 1, b + 1, slot, float(part[b]) / top))
                if starts_dead:
                    events.append(("abrupt start", a, a, slot, float(part[a]) / top))
            path = points[a - 1:b + 1, k, 0]
            if (b - a) / rate >= 0.2:
                chord = float(np.linalg.norm(path[-1] - path[0]))
                if chord >= 0.03 * size:
                    direction = (path[-1] - path[0]) / chord
                    off = np.abs((path[:, 0] - path[0, 0]) * direction[1] - (path[:, 1] - path[0, 1]) * direction[0])
                    if float(off.max()) < STRAIGHT * chord:
                        events.append(("straight path", a, b, slot, chord))
    return events


def _moves(part, points, lo, hi, size):
    """A part's moves: runs above MOVING of its peak speed (gaps of 2 samples bridged), at least 3
    samples long, whose farthest tracked point travels MIN_TRAVEL of the size: (first, last, peak)."""
    if np.all(np.isnan(part[lo:hi + 1])):
        return []
    top = float(np.nanmax(part[lo:hi + 1]))
    if top < 0.02 * size:
        return []
    out = []
    for a, b in _runs(np.nan_to_num(part) > MOVING * top, lo, hi, merge=2):
        if b - a < 3:
            continue
        travel = np.linalg.norm(points[a:b + 1] - points[a - 1:b], axis=-1).max(axis=-1)
        if np.nansum(travel) >= MIN_TRAVEL * size:
            out.append((a, b, float(np.nanmax(part[a:b + 1]))))
    return out


def _group_events(events):
    """One kind's events on different slots within 2 samples, merged: (kind, first, last, slots, worst measure)."""
    out = []
    for kind in dict.fromkeys(e[0] for e in events):
        groups: list[list] = []
        for e in sorted((e for e in events if e[0] == kind), key=lambda e: e[1]):
            if groups and e[1] - groups[-1][0][1] <= 2:
                groups[-1].append(e)
            else:
                groups.append([e])
        for g in groups:
            measures = [e[4] for e in g if e[4] is not None]
            out.append((kind, g[0][1], max(e[2] for e in g), sorted({e[3] for e in g}), max(measures) if measures else None))
    return out


def _mix(ctx, mix):
    """Jerks while one animation mixes into the next."""
    frames = _arrays(mix["frames"])
    rate = ctx["rate"]
    switch = round(mix["switch"] * rate)
    last = min(switch + round(max(mix["mix"], 0) * rate) + 2, len(frames["att"]) - 1)
    v, speed = _velocities(ctx, frames)
    label = f"{mix['from']} -> {mix['to']}"
    found = []
    for kind, a, _, slots, _ in _group_events(_part_events(ctx, frames, v, speed, 1, len(speed) - 1)):
        if kind in ("hitch", "teleport") and switch - 1 <= a <= last:  # a mix's crossfade starts at full speed
            found.append({
                "animation": label, "kind": f"mix {kind}", "rank": 2, "principle": "6 slow in / slow out",
                "what": f"mixing {label} over {mix['mix']} s: {', '.join(slots)} "
                        f"{'jerk' if kind == 'hitch' else 'jump'} "
                        f"{round((a - switch) / rate, 3)} s after the switch",
                "evidence": {"slots": slots, "time": round((a - switch) / rate, 3), "mix": mix["mix"]},
                "fix": "lengthen the mix (0.2-0.4 s for most character transitions), or start the next animation "
                       "from a pose closer to where this one ends"})
    return found


def _loop_checks(ctx, frames, start, end, last, measured, add):
    """A loop's amplitude, whether its channels move in lockstep, and its seam."""
    size = ctx["size"]
    points = frames["points"][start:end + 1]
    spread = np.nan_to_num(np.linalg.norm(points - _quiet(np.nanmean, points, axis=0), axis=-1)).max(axis=(0, 2))
    amplitude = float(spread.max()) if len(spread) else 0.0
    measured["amplitude"] = round(amplitude, 2)
    if amplitude < max(1.0, 0.001 * size):
        add("frozen loop", 4, "8 secondary action", f"the loop barely moves: no part strays more than "
            f"{amplitude:.1f} units", {"time": 0.0, "amplitude": round(amplitude, 2)},
            "enlarge it a little (1-3% of the body's size on the torso and head) and let hair and jewellery move "
            "with physics")
    channels = []
    for j, bone in enumerate(ctx["bones"]):
        if bone not in ctx["physics"] and ctx["parents"][j] >= 0:
            rot = _local_rotation(ctx, frames, j)[start:end + 1]
            if np.ptp(rot) > 0.2:
                channels.append((bone, rot - rot.mean()))
    if len(channels) >= 3:
        pairs = [(x, y) for i, (_, x) in enumerate(channels) for _, y in channels[i + 1:]]
        locked = sum(1 for x, y in pairs if abs(np.corrcoef(x, y)[0, 1]) > 0.95)
        measured["channels_in_step"] = f"{locked} of {len(pairs)} pairs"
        if locked / len(pairs) >= 0.8:
            add("lockstep loop", 4, "8 secondary action, 5 overlap",
                f"{len(channels)} turning parts rise and fall together ({locked} of {len(pairs)} pairs in step)",
                {"time": 0.0, "bones": [b for b, _ in channels]},
                "give each channel its own period and phase (e.g. breathing over the whole loop, the head over half "
                "of it, offset by a few frames) so the parts never move in sync")
    gap, worst = 0.0, None
    for k, slot in enumerate(ctx["slot_names"]):
        if last is None or ctx["bones"][ctx["slot_bone"][k]] in ctx["physics"] or last["att"][0, k] < 0 \
                or frames["att"][0, k] < 0:
            continue
        g = float(np.nanmax(np.linalg.norm(last["points"][0, k] - frames["points"][0, k], axis=-1)))
        if g > gap:
            gap, worst = g, slot
    measured["loop_seam_gap"] = round(gap, 2)
    if gap > 0.005 * size:
        add("loop seam", 2, "6 slow in / slow out", f"the loop's last pose differs from its first: {worst} jumps "
            f"{gap:.1f} units when it wraps", {"slot": worst, "time": 0.0},
            "key the last frame with the first frame's values (and matching curves)")


def _runs(mask, lo, hi, merge=0):
    """Index runs (inclusive) where mask is true within [lo, hi]; gaps of up to `merge` samples are bridged."""
    runs: list[tuple[int, int]] = []
    i = lo
    while i <= hi:
        if mask[i]:
            j = i
            while j + 1 <= hi and mask[j + 1]:
                j += 1
            if runs and merge and i - runs[-1][1] - 1 <= merge:
                runs[-1] = (runs[-1][0], j)
            else:
                runs.append((i, j))
            i = j + 1
        else:
            i += 1
    return runs


def _local_rotation(ctx, frames, j):
    rot = frames["bones"][:, j, 2]
    parent = ctx["parents"][j]
    if parent >= 0:
        rot = rot - frames["bones"][:, parent, 2]
    return np.degrees(np.unwrap(np.radians(rot)))


def _bone_spans(ctx, frames, a, b):
    """Bones with visible parts that turn or move on their own in [a, b] (physics bones aside):
    their first and last active sample."""
    spans = {}
    shown = {ctx["slot_bone"][k] for k in range(len(ctx["slot_names"])) if (frames["att"][a:b + 1, k] >= 0).any()}
    for j in sorted(shown):
        if ctx["bones"][j] in ctx["physics"]:
            continue
        spin = np.abs(np.diff(_local_rotation(ctx, frames, j)[a - 1:b + 2])) * ctx["rate"]
        parent = ctx["parents"][j]
        pos = frames["bones"][a - 1:b + 2, j, :2]
        if parent >= 0:  # in the parent's axes, so the parent turning does not count as this bone moving
            offset = pos - frames["bones"][a - 1:b + 2, parent, :2]
            turn = np.radians(-frames["bones"][a - 1:b + 2, parent, 2])
            pos = np.stack([offset[:, 0] * np.cos(turn) - offset[:, 1] * np.sin(turn),
                            offset[:, 0] * np.sin(turn) + offset[:, 1] * np.cos(turn)], axis=-1)
        slide = np.linalg.norm(np.diff(pos, axis=0), axis=-1) * ctx["rate"]
        activity = spin / 20 + slide / (0.02 * ctx["size"])  # 20 deg/s, or 2% of the size per second, = 1
        if activity.max() < 1:
            continue
        on = np.nonzero(activity > 0.15 * activity.max())[0]
        spans[ctx["bones"][j]] = (int(on[0]) + a - 1, int(on[-1]) + a - 1)
    return spans


def _chain_offsets(ctx, spans):
    """For each moving bone and its closest moving ancestor: how much later it starts and stops (ms)."""
    out = []
    for child, (cs, ce) in spans.items():
        parent = ctx["parents"][ctx["bones"].index(child)]
        while parent >= 0 and ctx["bones"][parent] not in spans:
            parent = ctx["parents"][parent]
        if parent >= 0:
            ps, pe = spans[ctx["bones"][parent]]
            out.append({"parent": ctx["bones"][parent], "child": child,
                        "start_ms": round(1000 * (cs - ps) / ctx["rate"]), "stop_ms": round(1000 * (ce - pe) / ctx["rate"])})
    return out


def _twins(bones):
    seen = set()
    for name in bones:
        for left, right in _LEFT_RIGHT:
            if re.search(left, name):
                other = re.sub(left, right, name)
                if other in bones and (name, other) not in seen:
                    seen.add((name, other))
                    yield name, other
                break


def _swaps(ctx, frames, lo, hi, held):
    """(slot, sample) for lone attachment swaps (no other swap within 0.25 s) made while the body holds."""
    out = []
    att = frames["att"]
    for k, slot in enumerate(ctx["slot_names"]):
        changes = [i for i in range(max(lo, 1), hi + 1) if att[i, k] != att[i - 1, k] and att[i, k] >= 0 and att[i - 1, k] >= 0]
        for i in changes:
            if held[i] and sum(abs(c - i) <= 0.25 * ctx["rate"] for c in changes) == 1:
                out.append((slot, i))
    return out


def _drivers(ctx, slot):
    """Bones whose keys move a slot, closest first: its bone, its ancestors, and the targets of
    constraints acting on them."""
    order: list[str] = []
    todo = [ctx["slot_bone"][ctx["slot_names"].index(slot)]]
    while todo:
        j = todo.pop(0)
        while j >= 0 and ctx["bones"][j] not in order:
            order.append(ctx["bones"][j])
            for c in ctx["constraints"]:
                if ctx["bones"][j] in c["bones"] and c.get("target") in ctx["bones"]:
                    todo.append(ctx["bones"].index(c["target"]))
            j = ctx["parents"][j]
    return order


def _keys_near(ctx, anim, slots, time, until=None, bones=None):
    """'bone timeline @ time' for keys within 2 frames of [time, until] on `bones`, or on the bones that
    move every one of the slots (else any of them), closest bones first."""
    pad = 2 / ctx["fps"]
    lo, hi = time - pad, (until if until is not None else time) + pad
    if bones is None:
        chains = [_drivers(ctx, s) for s in slots]
        common = [b for b in chains[0] if all(b in c for c in chains[1:])]
        bones = common or list(dict.fromkeys(b for c in chains for b in c))
    out = []
    for bone in bones:
        for timeline, keys in ((anim.get("bones") or {}).get(bone) or {}).items():
            near = [round(float(k.get("time", 0)), 3) for k in keys if isinstance(k, dict)
                    and lo <= float(k.get("time", 0)) <= hi] if isinstance(keys, list) else []
            if near:
                out.append(f"{bone} {timeline} @ {near[0]}" + (f"-{near[-1]}" if len(near) > 1 else ""))
    return out[:6]


def _key_findings(ctx, name, anim, loop):
    """From the keys themselves: linear keys far apart, long single moves, one ease everywhere, stepped keys."""
    fps = ctx["fps"]
    found = []
    linear, floaty, eases, stepped = [], [], [], 0
    for bone, timelines in (anim.get("bones") or {}).items():
        for timeline, keys in (timelines or {}).items():
            if not isinstance(keys, list) or timeline not in ("rotate", "translate", "translatex", "translatey",
                                                                "scale", "scalex", "scaley", "shear"):
                continue
            for k0, k1 in zip(keys, keys[1:]):
                t0, t1 = float(k0.get("time", 0)), float(k1.get("time", 0))
                values0, values1 = _values(k0, timeline), _values(k1, timeline)
                if t1 <= t0 or not any(abs(b - a) > 1e-6 for a, b in zip(values0, values1)):
                    continue
                curve = k0.get("curve")
                gap = (t1 - t0) * fps
                if curve == "stepped":
                    stepped += 1
                    continue
                if curve is None and gap >= LINEAR_GAP:
                    linear.append(f"{bone} {timeline} @ {round(t0, 3)}-{round(t1, 3)}")
                if gap > FLOATY:
                    floaty.append(f"{bone} {timeline} @ {round(t0, 3)}-{round(t1, 3)}")
                if isinstance(curve, list):
                    for c, (a, b) in enumerate(zip(values0, values1)):
                        if len(curve) >= 4 * (c + 1) and abs(b - a) > 1e-6:
                            cx1, cy1, cx2, cy2 = curve[4 * c:4 * c + 4]
                            eases.append(tuple(round(x, 2) for x in ((cx1 - t0) / (t1 - t0), (cy1 - a) / (b - a),
                                                                     (cx2 - t0) / (t1 - t0), (cy2 - a) / (b - a))))

    def add(kind, rank, principle, what, evidence, fix):
        found.append({"animation": name, "kind": kind, "rank": rank, "principle": principle, "what": what,
                      "evidence": {"time": 0.0, **evidence}, "fix": fix})
    if linear:
        add("linear keys", 3, "6 slow in / slow out", f"{len(linear)} key-to-key moves of {LINEAR_GAP} or more frames "
            "are linear, so the speed changes at each key", {"keys": linear[:6]},
            "give them eases (ease-out leaving a pose, ease-in arriving) or bezier curves")
    if floaty and not loop:
        add("floaty", 3, "9 timing", f"{len(floaty)} moves last over {FLOATY} frames from one key to the next",
            {"keys": floaty[:6]}, "add breakdowns and shorten; sharpen the contacts")
    if len(eases) >= 6:
        common = max(set(eases), key=eases.count)
        if eases.count(common) / len(eases) >= 0.8:
            add("same ease everywhere", 5, "6 slow in / slow out",
                f"{eases.count(common)} of {len(eases)} curves use the same ease {list(common)}", {"ease": list(common)},
                "vary the spacing: favour the breakdowns, ease out of starts, ease into stops")
    if stepped:
        add("stepped keys", 5, "4 pose to pose", f"{stepped} keys are stepped (blocking)", {"count": stepped},
            "spline the blocking once the timing is approved: switch the keys to eases")
    return found


def _values(key, timeline):
    if timeline in ("translate", "scale", "shear"):
        default = 1.0 if timeline == "scale" else 0.0
        return [float(key.get("x", default)), float(key.get("y", default))]
    return [float(key.get("value", 1.0 if timeline.startswith("scale") else 0.0))]


def _cap(found):
    out, counts = [], {}
    for f in found:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
        if counts[f["kind"]] <= MAX_PER_KIND:
            out.append(f)
    for kind, n in counts.items():
        if n > MAX_PER_KIND:
            next(f for f in out if f["kind"] == kind)["what"] += f" (and {n - MAX_PER_KIND} more like it)"
    return out
