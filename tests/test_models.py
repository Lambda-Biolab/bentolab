"""Tests for bentolab data models."""

from bentolab.models import (
    CycleStep,
    DeviceState,
    DeviceStatus,
    PCRProfile,
    ThermalStep,
)


def test_thermal_step_creation():
    step = ThermalStep(temperature=95.0, duration=30)
    assert step.temperature == 95.0
    assert step.duration == 30


def test_cycle_step_creation():
    cycle = CycleStep(
        denaturation=ThermalStep(95.0, 30),
        annealing=ThermalStep(58.0, 30),
        extension=ThermalStep(72.0, 60),
        repeat_count=35,
    )
    assert cycle.repeat_count == 35
    assert cycle.denaturation.temperature == 95.0


def test_pcr_profile_simple_factory():
    profile = PCRProfile.simple(
        name="Test PCR",
        num_cycles=30,
        denaturation=(95.0, 30),
        annealing=(55.0, 30),
        extension=(72.0, 45),
    )
    assert profile.name == "Test PCR"
    assert len(profile.cycles) == 1
    assert profile.cycles[0].repeat_count == 30
    assert profile.cycles[0].annealing.temperature == 55.0
    assert profile.initial_denaturation.temperature == 95.0
    assert profile.final_extension.duration == 300


def test_device_state_defaults():
    state = DeviceState()
    assert state.connected is False
    assert state.status == DeviceStatus.UNKNOWN
    assert state.block_temperature is None
    assert state.current_cycle is None


def test_device_status_values():
    assert DeviceStatus.IDLE.value == "idle"
    assert DeviceStatus.RUNNING.value == "running"
    assert DeviceStatus.ERROR.value == "error"
