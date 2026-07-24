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


def test_pcr_profile_to_stages_and_cycles_simple():
    """Default profile emits no hold (hold_duration_s defaults to 0)."""
    profile = PCRProfile.simple(
        num_cycles=30,
        initial_denaturation=(95.0, 180),
        denaturation=(95.0, 30),
        annealing=(58.0, 30),
        extension=(72.0, 60),
        final_extension=(72.0, 300),
    )
    stages, cycles = profile.to_stages_and_cycles()
    assert stages == [
        (95.0, 180),
        (95.0, 30),
        (58.0, 30),
        (72.0, 60),
        (72.0, 300),
    ]
    assert cycles == [(4, 2, 30)]


def test_pcr_profile_to_stages_and_cycles_opt_in_hold():
    """Setting hold_duration_s > 0 appends a hold stage with that duration."""
    profile = PCRProfile(
        name="overnight-hold",
        initial_denaturation=ThermalStep(95.0, 180),
        cycles=[
            CycleStep(
                denaturation=ThermalStep(95.0, 30),
                annealing=ThermalStep(58.0, 30),
                extension=ThermalStep(72.0, 60),
                repeat_count=30,
            ),
        ],
        final_extension=ThermalStep(72.0, 300),
        hold_temperature=4.0,
        hold_duration_s=86_400,
    )
    stages, _cycles = profile.to_stages_and_cycles()
    # initial + 3 cycle steps + final + 24 h hold
    assert len(stages) == 1 + 3 + 1 + 1
    assert stages[-1] == (4.0, 86_400)
    assert stages[-2] == (72.0, 300)


def test_pcr_profile_to_stages_and_cycles_multi_step():
    profile = PCRProfile(
        name="Touchdown then amplification",
        initial_denaturation=ThermalStep(95.0, 180),
        cycles=[
            CycleStep(
                denaturation=ThermalStep(95.0, 15),
                annealing=ThermalStep(65.0, 20),
                extension=ThermalStep(72.0, 30),
                repeat_count=10,
            ),
            CycleStep(
                denaturation=ThermalStep(95.0, 15),
                annealing=ThermalStep(55.0, 20),
                extension=ThermalStep(72.0, 30),
                repeat_count=25,
            ),
        ],
        final_extension=ThermalStep(72.0, 300),
    )
    stages, cycles = profile.to_stages_and_cycles()
    # initial + 2x(d,a,e) + final; no hold by default
    assert len(stages) == 1 + 3 + 3 + 1
    assert stages[0] == (95.0, 180)
    assert stages[-1] == (72.0, 300)
    assert cycles == [(4, 2, 10), (7, 5, 25)]


def test_pcr_profile_to_stages_and_cycles_respects_hold_temperature():
    """A custom hold_temperature surfaces in the emitted hold stage when hold is enabled."""
    profile = PCRProfile(
        name="custom-hold",
        cycles=[
            CycleStep(
                denaturation=ThermalStep(95.0, 15),
                annealing=ThermalStep(55.0, 20),
                extension=ThermalStep(72.0, 30),
                repeat_count=10,
            ),
        ],
        final_extension=ThermalStep(72.0, 60),
        hold_temperature=10.0,
        hold_duration_s=86_400,
    )
    stages, _cycles = profile.to_stages_and_cycles()
    assert stages[-1] == (10.0, 86_400)


def test_pcr_profile_hold_defaults_to_disabled():
    """Default profile has hold_duration_s=0 → no hold stage emitted.

    The opt-in design keeps demos and one-shot runs from showing a 24 h
    hold on the device's display. Users explicitly opt in by setting
    hold_duration_s > 0.
    """
    p = PCRProfile()
    assert p.hold_duration_s == 0
    stages, _ = p.to_stages_and_cycles()
    # No (4.0, 86_400) hold stage
    assert (4.0, 86_400) not in stages
    assert stages[-1][0] == 72.0  # last stage is final_extension temp


def test_pcr_profile_hold_duration_zero_emits_no_hold():
    """Explicit hold_duration_s=0 still means no hold (idempotent with default)."""
    p = PCRProfile(
        hold_temperature=4.0,
        hold_duration_s=0,
    )
    stages, _ = p.to_stages_and_cycles()
    assert (4.0, 86_400) not in stages


def test_pcr_profile_hold_duration_custom_value():
    """Non-default hold_duration_s is honored verbatim (not clamped to 24 h)."""
    p = PCRProfile(hold_duration_s=60)
    stages, _ = p.to_stages_and_cycles()
    assert stages[-1] == (p.hold_temperature, 60)


def test_pcr_profile_round_trips_hold_duration():
    """hold_duration_s survives to_dict / from_dict round-trip."""
    p = PCRProfile(hold_duration_s=3600)
    restored = PCRProfile.from_dict(p.to_dict())
    assert restored.hold_duration_s == 3600


def test_pcr_profile_from_dict_defaults_hold_duration_to_zero():
    """Profile dicts without hold_duration_s (legacy / hand-written YAML) default to 0."""
    data = {
        "name": "legacy",
        "initial_denaturation": {"temperature": 95, "duration": 60},
        "cycles": [],
        "final_extension": {"temperature": 72, "duration": 60},
    }
    p = PCRProfile.from_dict(data)
    assert p.hold_duration_s == 0
    # And to_stages_and_cycles respects the default
    stages, _ = p.to_stages_and_cycles()
    assert (4.0, 86_400) not in stages


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
