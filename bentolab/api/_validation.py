"""PCR profile validation (pure function, no transport dependencies).

Lives in its own module so both ``app.py`` (HTTP handlers) and
``_run_service.py`` (run orchestration) can import it without a
runtime import cycle.

Validation rules mirror the C22 contract — profile structure must be
constructible via ``PCRProfile.from_dict`` (catches schema errors)
and the resulting profile must have parameters within instrument-safe
ranges.
"""

from __future__ import annotations

from typing import Any

from ..models import PCRProfile

# Instrument-safe parameter ranges.
TEMP_MIN = 4.0
TEMP_MAX = 100.0
LID_TEMP_MIN = 30.0
LID_TEMP_MAX = 115.0
DURATION_MIN = 0
DURATION_MAX = 86_400  # 24 hours
CYCLES_MIN = 1
CYCLES_MAX = 999


def validate_profile(
    profile_dict: dict[str, Any],
) -> tuple[bool, list[str], list[str]]:
    """Validate a PCR profile dict without hardware side effects.

    Returns ``(ok, errors, warnings)``. ``errors`` empty means the
    profile is acceptable to start a run; ``warnings`` are advisory.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Build PCRProfile from dict to normalize structure
    try:
        profile = PCRProfile.from_dict(profile_dict)
    except (ValueError, KeyError, TypeError) as exc:
        errors.append(f"Invalid profile structure: {exc}")
        return False, errors, warnings

    # 2. Name
    if not profile.name or profile.name == "Untitled":
        warnings.append("Profile has no meaningful name")

    # 3. Lid temperature
    if profile.lid_temperature < LID_TEMP_MIN or profile.lid_temperature > LID_TEMP_MAX:
        errors.append(
            f"Lid temperature {profile.lid_temperature} C is outside "
            f"safe range ({LID_TEMP_MIN}-{LID_TEMP_MAX} C)"
        )

    # 4. Initial denaturation
    _validate_step(errors, warnings, "initial_denaturation", profile.initial_denaturation)

    # 5. Cycles
    if not profile.cycles:
        warnings.append("Profile has no thermal cycles (denaturation/annealing/extension)")
    for i, cycle in enumerate(profile.cycles):
        prefix = f"cycle[{i}]"
        if cycle.repeat_count < CYCLES_MIN or cycle.repeat_count > CYCLES_MAX:
            errors.append(
                f"{prefix} repeat_count {cycle.repeat_count} is outside "
                f"allowed range ({CYCLES_MIN}-{CYCLES_MAX})"
            )
        _validate_step(errors, warnings, f"{prefix}.denaturation", cycle.denaturation)
        _validate_step(errors, warnings, f"{prefix}.annealing", cycle.annealing)
        _validate_step(errors, warnings, f"{prefix}.extension", cycle.extension)

    # 6. Final extension
    _validate_step(errors, warnings, "final_extension", profile.final_extension)

    # 7. Hold temperature
    if profile.hold_temperature < 0 or profile.hold_temperature > TEMP_MAX:
        warnings.append(f"Hold temperature {profile.hold_temperature} C is unusual")

    return len(errors) == 0, errors, warnings


def _validate_step(
    errors: list[str],
    warnings: list[str],
    label: str,
    step: Any,
) -> None:
    temp = step.temperature
    dur = step.duration
    if temp < TEMP_MIN or temp > TEMP_MAX:
        errors.append(
            f"{label} temperature {temp} C is outside instrument range ({TEMP_MIN}-{TEMP_MAX} C)"
        )
    if dur < DURATION_MIN or dur > DURATION_MAX:
        errors.append(
            f"{label} duration {dur}s is outside allowed range ({DURATION_MIN}-{DURATION_MAX}s)"
        )


__all__ = [
    "CYCLES_MAX",
    "CYCLES_MIN",
    "DURATION_MAX",
    "DURATION_MIN",
    "LID_TEMP_MAX",
    "LID_TEMP_MIN",
    "TEMP_MAX",
    "TEMP_MIN",
    "validate_profile",
]