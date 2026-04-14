"""Data models for Bento Lab device state and PCR profiles."""

from dataclasses import dataclass, field
from enum import Enum


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
    ) -> "PCRProfile":
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
