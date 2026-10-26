"""analyze_motion: play animations in spine-core and measure how they move
(runtime/motion.mjs samples, spine/motion.py findings)."""

from __future__ import annotations

from pathlib import Path

from . import runtime
from .spine import document as d
from .spine.motion import analyze, display_skins
from .spine.presets import LOOPING

RATE = 60  # samples per second


class MotionError(ValueError):
    """The animations cannot be measured (unknown name, runtime missing, ...)."""


def analyze_motion(project_path: str | Path, animations: list[str] | None = None, loops: list[str] | None = None,
                   skins: list[str] | None = None, mixes: list[dict] | None = None) -> dict:
    project_path = Path(project_path)
    doc = d.load(project_path)
    have = list(doc.get("animations") or {})
    names = have if animations is None else animations
    unknown = sorted(set(names) - set(have) | {n for m in mixes or [] for n in (m.get("from"), m.get("to"))} - set(have))
    if unknown:
        raise MotionError(f"no animation {', '.join(map(str, unknown))} (have {', '.join(have) or 'none'})")
    loops = set(loops) if loops is not None else LOOPING & set(have)
    skins = display_skins(doc) if skins is None else skins
    sampled = runtime.motion(project_path, {
        "mode": "sample", "skins": skins, "rate": RATE,
        "animations": [{"name": n, "cycles": 2 if n in loops else 1} for n in names],
        "mixes": [{"from": m["from"], "to": m["to"], "mix": float(m.get("mix", 0.2))} for m in mixes or []],
    })
    if not sampled.get("ran"):
        raise MotionError(f"spine-core could not play the skeleton: {sampled.get('reason')}")
    if sampled.get("errors"):
        raise MotionError("spine-core: " + "; ".join(sampled["errors"]))
    report = analyze(doc, sampled, RATE, loops)
    findings = report["findings"]
    return {
        "summary": (f"{len(findings)} findings in {len(names)} animation(s): "
                    + ", ".join(f"{sum(f['rank'] == r for f in findings)} rank {r}" for r in (2, 3, 4, 5))),
        "findings": findings,
        "animations": report["animations"],
        "skins": skins,
        "loops": sorted(loops),
        "runtime": sampled.get("runtime"),
        "notes": [
            "ranks: 2 breaks the read, 3 weight and timing, 4 life, 5 polish (rank 1, outcome integrity and "
            "compliance, is not something motion shows)",
            "each animation plays alone from the setup pose on one track, with constraints and physics; "
            "times are seconds into the animation",
            "thresholds are starting points at the skeleton's size: confirm by eye with onion_skin or preview_html",
        ],
    }
