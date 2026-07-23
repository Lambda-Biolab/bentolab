"""Tests for :mod:`bentolab.tui._stages` — TUI-local stage tracking."""

from __future__ import annotations

import pytest

from bentolab.models import CycleStep, PCRProfile, ThermalStep
from bentolab.tui._stages import stage_at, total_cycle_count


def _profile() -> PCRProfile:
    """Standard 35-cycle profile: 180s init + 35 * 120s cycles + 300s final = 4680s."""
    return PCRProfile.simple(num_cycles=35)


def test_total_cycle_count_simple_profile() -> None:
    """Sum of repeat_count over cycles; simple(num_cycles=35) has one CycleStep with 35."""
    assert total_cycle_count(_profile()) == 35


def test_total_cycle_count_empty_profile() -> None:
    """No cycles → 0."""
    assert total_cycle_count(PCRProfile()) == 0


def test_total_cycle_count_multiple_cycles() -> None:
    """Sum across multiple CycleStep blocks."""
    p = PCRProfile(
        cycles=[
            CycleStep(
                denaturation=ThermalStep(95.0, 30),
                annealing=ThermalStep(58.0, 30),
                extension=ThermalStep(72.0, 60),
                repeat_count=10,
            ),
            CycleStep(
                denaturation=ThermalStep(95.0, 30),
                annealing=ThermalStep(55.0, 30),
                extension=ThermalStep(72.0, 60),
                repeat_count=5,
            ),
        ],
    )
    assert total_cycle_count(p) == 15


def test_stage_at_zero_is_initial_denaturation() -> None:
    """elapsed=0 → initial denaturation, full duration remaining."""
    info = stage_at(_profile(), 0)
    assert info is not None
    assert info.phase == "initial"
    assert info.label == "Initial denaturation"
    assert info.setpoint == pytest.approx(95.0)
    assert info.seconds_remaining == 180


def test_stage_at_mid_initial_denaturation() -> None:
    """Mid-init elapsed → still initial, partial remaining."""
    info = stage_at(_profile(), 30)
    assert info is not None
    assert info.phase == "initial"
    assert info.seconds_remaining == 150


def test_stage_at_boundary_initial_to_first_cycle() -> None:
    """elapsed = 180s lands on cycle 0 denaturation (boundary inclusive)."""
    info = stage_at(_profile(), 180)
    assert info is not None
    assert info.phase == "denat"
    assert info.label == "Cycle 1, Denaturation"
    assert info.setpoint == pytest.approx(95.0)
    assert info.seconds_remaining == 30


def test_stage_at_first_cycle_annealing() -> None:
    """180s (init) + 30s (denat) = 210s → anneal of cycle 1."""
    info = stage_at(_profile(), 210)
    assert info is not None
    assert info.phase == "anneal"
    assert info.setpoint == pytest.approx(58.0)
    assert info.seconds_remaining == 30


def test_stage_at_first_cycle_extension() -> None:
    """210s + 30s = 240s → extend of cycle 1."""
    info = stage_at(_profile(), 240)
    assert info is not None
    assert info.phase == "extend"
    assert info.setpoint == pytest.approx(72.0)
    assert info.seconds_remaining == 60


def test_stage_at_later_cycle_iteration_uses_same_label() -> None:
    """iter_steps repeats the same ``cycle_<i>_<phase>`` label for every iteration;
    stage_at cannot distinguish iteration count from the label alone — only the
    ``seconds_remaining`` and ``setpoint`` differ across iterations.

    Verified across iter 1 (t=200, denat: secs_remaining=10) and iter 35
    (t=4265, denat: secs_remaining=25). Same label and setpoint.
    """
    info_first = stage_at(_profile(), 200)  # 1st denat, 10s in
    info_last = stage_at(_profile(), 4265)  # 35th denat, 5s in (block runs 4260..4290)
    assert info_first is not None
    assert info_last is not None
    assert info_first.label == info_last.label == "Cycle 1, Denaturation"
    assert info_first.setpoint == info_last.setpoint == pytest.approx(95.0)
    assert info_first.seconds_remaining == 10
    assert info_last.seconds_remaining == 25


def test_stage_at_final_extension() -> None:
    """elapsed past all cycles but before final -> final extension."""
    # 180s init + 35 cycles * 120s = 4380s; final extension starts at 4380s.
    info = stage_at(_profile(), 4380)
    assert info is not None
    assert info.phase == "final"
    assert info.label == "Final extension"
    assert info.setpoint == pytest.approx(72.0)


def test_stage_at_past_final_is_hold() -> None:
    """elapsed beyond runtime → hold at hold_temperature."""
    info = stage_at(_profile(), 100000)
    assert info is not None
    assert info.phase == "hold"
    assert info.label == "Hold"
    assert info.setpoint == pytest.approx(4.0)
    assert info.seconds_remaining == 0


def test_stage_at_negative_elapsed_clamps_to_zero() -> None:
    """Negative elapsed is clamped to 0 → initial denaturation with full duration."""
    info = stage_at(_profile(), -10)
    assert info is not None
    assert info.phase == "initial"
    assert info.seconds_remaining == 180


def test_stage_at_custom_hold_temperature() -> None:
    """Hold stage setpoint reflects profile's hold_temperature, not the domain default.

    Default PCRProfile has initial_denaturation=(95,180) + final_extension=(72,300),
    so total runtime is 480s. Past that lands in hold.
    """
    p = PCRProfile(hold_temperature=10.0)
    info = stage_at(p, 10000)  # way past 480s runtime
    assert info is not None
    assert info.phase == "hold"
    assert info.setpoint == pytest.approx(10.0)
