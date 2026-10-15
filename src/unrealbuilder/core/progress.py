"""Overall progress estimation for a running build (pure functions, shared by CLI and GUI)."""

from __future__ import annotations

from dataclasses import dataclass

from unrealbuilder.core.jobs import STEP_LABELS, STEPS
from unrealbuilder.core.logparse.uat import UatProgress

# Relative weight of each runner step in the overall bar (cook dominates).
STEP_WEIGHTS = {
    "preflight": 1,
    "sync": 2,
    "clean": 1,
    "build": 8,
    "cook": 75,
    "post": 1,
    "smoke": 5,
    "zip": 6,
    "summary": 1,
}


def cook_fraction(p: UatProgress) -> float:
    """Progress inside the UAT cook step: startup → shaders → packages → stage → archive."""
    if "ARCHIVE" in p.phases_completed:
        return 1.0
    if p.current_phase == "ARCHIVE":
        return 0.97
    if p.current_phase == "STAGE" or "COOK" in p.phases_completed:
        return 0.9
    if p.cook_total > 0:
        return 0.3 + 0.55 * (p.cook_fraction or 0.0)
    if p.global_shaders:
        sf = p.shader_fraction
        return 0.05 + 0.25 * (sf if sf is not None else 0.0)
    return 0.02 if p.current_phase == "COOK" else 0.0


def build_fraction(p: UatProgress) -> float:
    return p.ubt_fraction or 0.0


@dataclass
class OverallProgress:
    fraction: float
    step: str | None
    step_label: str
    detail: str


def overall(steps: dict, current_step: str | None, p: UatProgress) -> OverallProgress:
    total = 0.0
    done = 0.0
    for name in STEPS:
        info = steps.get(name, {})
        state = info.get("state", "pending")
        w = STEP_WEIGHTS[name]
        if state == "skipped":
            continue
        total += w
        if state in ("done", "warning", "failed"):
            done += w
        elif state == "running":
            if name == "cook":
                done += w * cook_fraction(p)
            elif name == "build":
                done += w * build_fraction(p)
    fraction = done / total if total else 0.0
    return OverallProgress(min(1.0, fraction), current_step, STEP_LABELS.get(current_step or "", "—"), detail_text(current_step, p))


def step_durations(steps: dict) -> dict[str, float]:
    out = {}
    for name, info in steps.items():
        if info.get("started") is not None and info.get("ended") is not None:
            out[name] = max(0.0, info["ended"] - info["started"])
    return out


def estimate_remaining(
    steps: dict,
    current_step: str | None,
    p: UatProgress,
    elapsed: float,
    fraction: float,
    reference: dict[str, float] | None,
    now: float,
) -> float | None:
    """Seconds left. Uses the step durations of the last comparable successful build when available,
    otherwise extrapolates the overall fraction."""
    if reference:
        remaining = 0.0
        for name in STEPS:
            state = steps.get(name, {}).get("state", "pending")
            ref = reference.get(name)
            if ref is None or state in ("done", "skipped", "failed", "warning"):
                continue
            if state == "running":
                started = steps[name].get("started") or now
                spent = now - started
                if name == "cook":
                    frac = cook_fraction(p)
                    remaining += max(ref * (1 - frac), ref - spent, 0.0) if frac > 0 else max(ref - spent, 0.0)
                else:
                    remaining += max(ref - spent, 0.0)
            else:
                remaining += ref
        return remaining
    if fraction >= 0.1 and elapsed > 30:
        return elapsed / fraction - elapsed
    return None


def detail_text(step: str | None, p: UatProgress) -> str:
    parts: list[str] = []
    if step == "build":
        if p.ubt_total:
            parts.append(f"UBT {p.ubt_done}/{p.ubt_total}")
        if p.ubt_last_action:
            parts.append(p.ubt_last_action[:60])
    elif step == "cook":
        phase = p.current_phase
        if phase in ("STAGE", "PACKAGE", "ARCHIVE"):
            parts.append({"STAGE": "Stage / pak", "PACKAGE": "Package", "ARCHIVE": "Archive"}[phase])
        elif p.cook_total:
            parts.append(f"Packages {p.cook_done}/{p.cook_total}")
        elif p.global_shaders:
            parts.append("Global shaders")
        else:
            parts.append("Starting the editor")
        if p.shaders_left:
            parts.append(f"{p.shaders_left:,} shaders left")
    return " · ".join(parts)
