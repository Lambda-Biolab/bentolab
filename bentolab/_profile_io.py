"""PCR profile serialization (YAML and dict).

Kept out of :mod:`bentolab.models` so the domain types stay free of
persistence concerns. The :class:`bentolab.models.PCRProfile` class
exposes thin delegating methods (``to_dict`` / ``from_dict`` /
``to_yaml`` / ``from_yaml`` / ``from_yaml_file``) for ergonomics; this
module owns the actual implementation.

The :mod:`yaml` dependency is imported lazily inside each function so the
base library has no hard PyYAML requirement for callers that only use
the domain types.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import CycleStep, PCRProfile, ThermalStep

__all__ = [
    "profile_from_dict",
    "profile_from_yaml",
    "profile_from_yaml_file",
    "profile_to_dict",
    "profile_to_yaml",
]


def _step_to_dict(step: ThermalStep) -> dict[str, Any]:
    return {"temperature": step.temperature, "duration": step.duration}


def _step_from_dict(
    raw: dict[str, Any] | None, *, default: ThermalStep | None = None
) -> ThermalStep:
    if raw is None:
        if default is None:
            raise ValueError("Missing required thermal step")
        return default
    return ThermalStep(temperature=float(raw["temperature"]), duration=int(raw["duration"]))


def profile_to_dict(profile: PCRProfile) -> dict[str, Any]:
    """Serialize a :class:`PCRProfile` to a YAML/JSON-friendly dict."""
    return {
        "name": profile.name,
        "lid_temperature": profile.lid_temperature,
        "initial_denaturation": _step_to_dict(profile.initial_denaturation),
        "cycles": [
            {
                "repeat": c.repeat_count,
                "denaturation": _step_to_dict(c.denaturation),
                "annealing": _step_to_dict(c.annealing),
                "extension": _step_to_dict(c.extension),
            }
            for c in profile.cycles
        ],
        "final_extension": _step_to_dict(profile.final_extension),
        "hold_temperature": profile.hold_temperature,
        "notes": profile.notes,
    }


def profile_from_dict(data: dict[str, Any]) -> PCRProfile:
    """Build a profile from a dict produced by :func:`profile_to_dict`."""
    if "name" not in data:
        raise ValueError("Profile is missing required field: name")
    return PCRProfile(
        name=str(data["name"]),
        initial_denaturation=_step_from_dict(
            data.get("initial_denaturation"), default=ThermalStep(95.0, 180)
        ),
        cycles=[
            CycleStep(
                denaturation=_step_from_dict(c.get("denaturation")),
                annealing=_step_from_dict(c.get("annealing")),
                extension=_step_from_dict(c.get("extension")),
                repeat_count=int(c.get("repeat", 1)),
            )
            for c in data.get("cycles", [])
        ],
        final_extension=_step_from_dict(
            data.get("final_extension"), default=ThermalStep(72.0, 300)
        ),
        hold_temperature=float(data.get("hold_temperature", 4.0)),
        lid_temperature=float(data.get("lid_temperature", 110.0)),
        notes=str(data.get("notes", "")),
    )


def profile_to_yaml(profile: PCRProfile) -> str:
    """Render as YAML text. Requires :mod:`pyyaml`."""
    import yaml  # lazy import keeps base lib pyyaml-free

    return yaml.safe_dump(profile_to_dict(profile), sort_keys=False, allow_unicode=True)


def profile_from_yaml(text: str) -> PCRProfile:
    """Parse a YAML profile document."""
    import yaml

    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("Profile YAML must be a mapping at the top level")
    return profile_from_dict(data)


def profile_from_yaml_file(path: Path) -> PCRProfile:
    """Read a profile from a YAML file on disk."""
    return profile_from_yaml(Path(path).read_text(encoding="utf-8"))
