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
    initial_denaturation: ThermalStep = field(default_factory=lambda: _DEFAULT_INITIAL_DENATURATION)
    cycles: list[CycleStep] = field(default_factory=list)
    final_extension: ThermalStep = field(default_factory=lambda: _DEFAULT_FINAL_EXTENSION)
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
        from ._profile_io import profile_to_dict

        return profile_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PCRProfile:
        """Build a profile from a dict produced by :meth:`to_dict`."""
        from ._profile_io import profile_from_dict

        return profile_from_dict(data)

    def to_yaml(self) -> str:
        """Render as YAML text. Requires :mod:`pyyaml`."""
        from ._profile_io import profile_to_yaml

        return profile_to_yaml(self)

    @classmethod
    def from_yaml(cls, text: str) -> PCRProfile:
        """Parse a YAML profile document."""
        from ._profile_io import profile_from_yaml

        return profile_from_yaml(text)

    @classmethod
    def from_yaml_file(cls, path: Path) -> PCRProfile:
        """Read a profile from a YAML file on disk."""
        from ._profile_io import profile_from_yaml_file

        return profile_from_yaml_file(path)


# Module-level constants — single source of truth for default thermal
# parameters. _profile_io.profile_from_dict uses the same constants for
# its fallback paths.
_DEFAULT_INITIAL_DENATURATION = ThermalStep(95.0, 180)
_DEFAULT_FINAL_EXTENSION = ThermalStep(72.0, 300)
_DEFAULT_HOLD_TEMPERATURE = 4.0
_DEFAULT_LID_TEMPERATURE = 110.0


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
