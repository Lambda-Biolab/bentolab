"""Tests for PCRProfile.stage_at — derives the live PCR stage from elapsed time."""

from __future__ import annotations

from bentolab.models import CycleStep, PCRProfile, ThermalStep


def _profile() -> PCRProfile:
    return PCRProfile(
        name="t",
        initial_denaturation=ThermalStep(95.0, 60),
        cycles=[
            CycleStep(
                denaturation=ThermalStep(98.0, 10),
                annealing=ThermalStep(60.0, 30),
                extension=ThermalStep(72.0, 60),
                repeat_count=3,
            )
        ],
        final_extension=ThermalStep(72.0, 30),
    )


def test_initial_phase() -> None:
    s = _profile().stage_at(15.0)
    assert s.phase == "initial"
    assert s.cycle == 0


def test_first_denat_in_cycling() -> None:
    s = _profile().stage_at(60.0 + 5.0)  # 5s into cycle 1 denat (10s)
    assert s.phase == "denat"
    assert s.cycle == 1
    assert s.total_cycles == 3


def test_anneal_in_second_cycle() -> None:
    # First cycle = 10+30+60 = 100s; second cycle starts at 60+100 = 160s.
    s = _profile().stage_at(60.0 + 100.0 + 10.0 + 5.0)
    assert s.phase == "anneal"
    assert s.cycle == 2


def test_extend_in_third_cycle() -> None:
    s = _profile().stage_at(60.0 + 200.0 + 10.0 + 30.0 + 5.0)
    assert s.phase == "extend"
    assert s.cycle == 3


def test_final_extension() -> None:
    s = _profile().stage_at(60.0 + 300.0 + 5.0)
    assert s.phase == "final"
    assert s.cycle == 3


def test_hold_after_program() -> None:
    s = _profile().stage_at(60.0 + 300.0 + 30.0 + 1.0)
    assert s.phase == "hold"
