"""Data models for Bento Lab device state and PCR profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class DeviceStatus(Enum):
    """Current operational status of the Bento Lab."""

    UNKNOWN = "unknown"
    IDLE = "idle"
    HEATING = "heating"
    COOLING = "cooling"
    HOLDING = "holding"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class ThermalStep:
    """A single temperature hold step."""

    temperature: float  # Celsius
    duration: int  # seconds


@dataclass
class CycleStep:
    """One cycle of denaturation -> annealing -> extension."""

    denaturation: ThermalStep
    annealing: ThermalStep
    extension: ThermalStep
    repeat_count: int = 1


@dataclass
class PCRProfile:
    """A complete PCR thermal cycling profile."""

    name: str = "Untitled"
    initial_denaturation: ThermalStep = field(default_factory=lambda: ThermalStep(95.0, 180))
    cycles: list[CycleStep] = field(default_factory=list)
    final_extension: ThermalStep = field(default_factory=lambda: ThermalStep(72.0, 300))
    hold_temperature: float = 4.0
    lid_temperature: float = 110.0
    notes: str = ""

    @classmethod
    def simple(
        cls,
        name: str = "Standard PCR",
        num_cycles: int = 35,
        denaturation: tuple[float, int] = (95.0, 30),
        annealing: tuple[float, int] = (58.0, 30),
        extension: tuple[float, int] = (72.0, 60),
        initial_denaturation: tuple[float, int] = (95.0, 180),
        final_extension: tuple[float, int] = (72.0, 300),
    ) -> PCRProfile:
        """Create a standard 3-step PCR profile."""
        return cls(
            name=name,
            initial_denaturation=ThermalStep(*initial_denaturation),
            cycles=[
                CycleStep(
                    denaturation=ThermalStep(*denaturation),
                    annealing=ThermalStep(*annealing),
                    extension=ThermalStep(*extension),
                    repeat_count=num_cycles,
                )
            ],
            final_extension=ThermalStep(*final_extension),
        )

    def to_stages_and_cycles(
        self,
    ) -> tuple[list[tuple[float, int]], list[tuple[int, int, int]]]:
        """Flatten the profile into (stages, cycles) for the device protocol.

        The Bento Lab protocol expects a flat list of thermal stages plus a
        list of ``(from_stage, to_stage, count)`` loop tuples referencing
        stages by 1-based index. This method walks the profile and emits:

        - Stage 1: initial denaturation
        - Stages 2..K: the thermal steps of each ``CycleStep`` in order
          (denaturation, annealing, extension)
        - One cycle tuple per ``CycleStep`` looping from its extension stage
          back to its denaturation stage ``repeat_count`` times
        - Final stage: final extension
        """
        stages: list[tuple[float, int]] = [
            (self.initial_denaturation.temperature, self.initial_denaturation.duration)
        ]
        cycles: list[tuple[int, int, int]] = []

        for cycle in self.cycles:
            denat_idx = len(stages) + 1
            stages.append((cycle.denaturation.temperature, cycle.denaturation.duration))
            stages.append((cycle.annealing.temperature, cycle.annealing.duration))
            stages.append((cycle.extension.temperature, cycle.extension.duration))
            extend_idx = len(stages)
            cycles.append((extend_idx, denat_idx, cycle.repeat_count))

        stages.append((self.final_extension.temperature, self.final_extension.duration))
        return stages, cycles

    def estimated_runtime_seconds(self) -> int:
        """Sum of step durations across the program (excludes ramp time)."""
        total = self.initial_denaturation.duration
        for cycle in self.cycles:
            per_cycle = (
                cycle.denaturation.duration + cycle.annealing.duration + cycle.extension.duration
            )
            total += per_cycle * cycle.repeat_count
        total += self.final_extension.duration
        return total

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a YAML/JSON-friendly dict."""
        return {
            "name": self.name,
            "lid_temperature": self.lid_temperature,
            "initial_denaturation": _step_to_dict(self.initial_denaturation),
            "cycles": [
                {
                    "repeat": c.repeat_count,
                    "denaturation": _step_to_dict(c.denaturation),
                    "annealing": _step_to_dict(c.annealing),
                    "extension": _step_to_dict(c.extension),
                }
                for c in self.cycles
            ],
            "final_extension": _step_to_dict(self.final_extension),
            "hold_temperature": self.hold_temperature,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PCRProfile:
        """Build a profile from a dict produced by :meth:`to_dict`."""
        if "name" not in data:
            raise ValueError("Profile is missing required field: name")
        return cls(
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

    def to_yaml(self) -> str:
        """Render as YAML text. Requires :mod:`pyyaml`."""
        import yaml  # noqa: PLC0415  # lazy import keeps base lib pyyaml-free

        return yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True)

    @classmethod
    def from_yaml(cls, text: str) -> PCRProfile:
        """Parse a YAML profile document."""
        import yaml  # noqa: PLC0415

        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("Profile YAML must be a mapping at the top level")
        return cls.from_dict(data)

    @classmethod
    def from_yaml_file(cls, path: Path) -> PCRProfile:
        return cls.from_yaml(Path(path).read_text(encoding="utf-8"))


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


@dataclass
class DeviceState:
    """Current state of a connected Bento Lab."""

    connected: bool = False
    lid_temperature: float | None = None
    block_temperature: float | None = None
    target_temperature: float | None = None
    status: DeviceStatus = DeviceStatus.UNKNOWN
    current_cycle: int | None = None
    total_cycles: int | None = None
    elapsed_time: int | None = None  # seconds
    firmware_version: str | None = None
    serial_number: str | None = None
