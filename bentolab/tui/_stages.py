"""TUI-local stage tracking.

Derives the active PCR stage from a wall-clock elapsed-time using the
shared :meth:`bentolab.models.PCRProfile.iter_steps` iterator. The
TUI owns this logic (rather than adding ``stage_at()`` /
``StageInfo`` to the domain layer) so the canonical step walker
stays the single source of truth in :mod:`bentolab.models`.

Phase label mapping
-------------------
``iter_steps`` yields labels like ``"cycle_2_annealing"``; this
module reduces them to one of six canonical phase keys used by the
program diagram and status pane:

================  =====================
``iter_steps``    Canonical phase key
================  =====================
initial_denaturation   "initial"
cycle_N_denaturation   "denat"
cycle_N_annealing      "anneal"
cycle_N_extension      "extend"
final_extension        "final"
(beyond last step)     "hold"
================  =====================
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import PCRProfile


@dataclass
class StageInfo:
    """A point-in-time snapshot of where the run is in its profile.

    Attributes:
        phase: Canonical phase key (initial / denat / anneal / extend
            / final / hold).
        label: Human-readable label; suitable for the status pane.
        setpoint: Temperature setpoint of the current step (°C).
        seconds_remaining: Seconds until the current step completes.
    """

    phase: str
    label: str
    setpoint: float
    seconds_remaining: int


def total_cycle_count(profile: PCRProfile) -> int:
    """Sum ``repeat_count`` over all cycle blocks in the profile."""
    return sum(c.repeat_count for c in profile.cycles)


def stage_at(profile: PCRProfile, elapsed_seconds: float) -> StageInfo | None:
    """Return the :class:`StageInfo` covering ``elapsed_seconds`` of the profile.

    Returns ``None`` only when the profile has no steps (e.g. an empty
    PCRProfile). Otherwise returns either the active step or a
    synthetic ``"hold"`` stage for the post-final-extension period.

    The walker advances through :meth:`PCRProfile.iter_steps`
    accumulating durations; the first step whose cumulative end
    exceeds ``elapsed_seconds`` is the active step.
    """
    if elapsed_seconds < 0:
        # Negative elapsed times are nonsense — clamp to "right at the start".
        elapsed_seconds = 0.0

    t = 0.0
    for label, step in profile.iter_steps():
        t += step.duration
        if elapsed_seconds < t:
            phase = _phase_for_label(label)
            return StageInfo(
                phase=phase,
                label=_humanize(label),
                setpoint=step.temperature,
                seconds_remaining=int(t - elapsed_seconds),
            )

    # Past the last step — assume the device holds at the configured hold setpoint.
    return StageInfo(
        phase="hold",
        label="Hold",
        setpoint=profile.hold_temperature,
        seconds_remaining=0,
    )


def _phase_for_label(label: str) -> str:
    """Map an ``iter_steps()`` label to one of the canonical phase keys.

    Order matters: ``final_extension`` ends with ``_extension`` but must
    take precedence over the cycle-extension match, otherwise the
    status pane would label the final hold stage as "extend".
    """
    if label == "initial_denaturation":
        return "initial"
    if label == "final_extension":
        return "final"
    if label.endswith("_denaturation"):
        return "denat"
    if label.endswith("_annealing"):
        return "anneal"
    if label.endswith("_extension"):
        return "extend"
    return "unknown"


def _humanize(label: str) -> str:
    """Make an iter_steps label slightly more presentable (e.g. for status pane)."""
    if label == "initial_denaturation":
        return "Initial denaturation"
    if label == "final_extension":
        return "Final extension"
    # cycle_N_<phase> -> "Cycle N, <phase pretty>"
    if label.startswith("cycle_"):
        parts = label.split("_", 2)
        # ['cycle', 'N', 'annealing']
        if len(parts) == 3 and parts[1].isdigit():
            cycle_no = int(parts[1]) + 1
            sub = parts[2].replace("_", " ").capitalize()
            return f"Cycle {cycle_no}, {sub}"
    return label.replace("_", " ").capitalize()
