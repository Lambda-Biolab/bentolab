"""Tests for PCRProfile YAML round-trip and runtime estimation."""

from __future__ import annotations

import pytest

from bentolab.models import CycleStep, PCRProfile, ThermalStep


def _hf_profile() -> PCRProfile:
    return PCRProfile(
        name="HF-Pgl3-EGFP-Puro-linear",
        initial_denaturation=ThermalStep(95.0, 300),
        cycles=[
            CycleStep(
                denaturation=ThermalStep(98.0, 10),
                annealing=ThermalStep(60.0, 30),
                extension=ThermalStep(72.0, 150),
                repeat_count=35,
            )
        ],
        final_extension=ThermalStep(72.0, 300),
        hold_temperature=4.0,
        lid_temperature=110.0,
        notes="HF Pgl3 amplification",
    )


def test_to_dict_round_trips() -> None:
    p = _hf_profile()
    restored = PCRProfile.from_dict(p.to_dict())
    assert restored == p


def test_to_yaml_round_trips() -> None:
    p = _hf_profile()
    text = p.to_yaml()
    restored = PCRProfile.from_yaml(text)
    assert restored == p


def test_estimated_runtime() -> None:
    p = _hf_profile()
    # 300 + 35*(10+30+150) + 300 = 7250 s
    assert p.estimated_runtime_seconds() == 7250


def test_from_dict_requires_name() -> None:
    with pytest.raises(ValueError, match="name"):
        PCRProfile.from_dict({"cycles": []})


def test_from_yaml_rejects_non_mapping() -> None:
    with pytest.raises(ValueError, match="mapping"):
        PCRProfile.from_yaml("- 1\n- 2\n")


def test_estimated_runtime_no_cycles() -> None:
    p = PCRProfile(
        name="hold-only",
        initial_denaturation=ThermalStep(95.0, 60),
        cycles=[],
        final_extension=ThermalStep(72.0, 60),
    )
    assert p.estimated_runtime_seconds() == 120
