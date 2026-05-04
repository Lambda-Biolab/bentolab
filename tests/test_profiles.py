"""Tests for the YAML profile store."""

from __future__ import annotations

from pathlib import Path

import pytest

from bentolab import profiles as profile_store
from bentolab.models import CycleStep, PCRProfile, ThermalStep


def _make(name: str = "demo") -> PCRProfile:
    return PCRProfile(
        name=name,
        initial_denaturation=ThermalStep(95.0, 60),
        cycles=[
            CycleStep(
                denaturation=ThermalStep(98.0, 10),
                annealing=ThermalStep(60.0, 20),
                extension=ThermalStep(72.0, 30),
                repeat_count=5,
            )
        ],
        final_extension=ThermalStep(72.0, 60),
    )


def test_save_and_load(tmp_path: Path) -> None:
    p = _make()
    profile_store.save(p, root=tmp_path)
    loaded = profile_store.load("demo", root=tmp_path)
    assert loaded == p


def test_save_refuses_overwrite(tmp_path: Path) -> None:
    profile_store.save(_make(), root=tmp_path)
    with pytest.raises(profile_store.ProfileExistsError):
        profile_store.save(_make(), root=tmp_path)


def test_save_overwrite_creates_bak(tmp_path: Path) -> None:
    profile_store.save(_make(), root=tmp_path)
    p2 = _make()
    p2.notes = "changed"
    profile_store.save(p2, overwrite=True, root=tmp_path)
    assert (tmp_path / "demo.yaml.bak").exists()
    loaded = profile_store.load("demo", root=tmp_path)
    assert loaded.notes == "changed"


def test_load_missing(tmp_path: Path) -> None:
    with pytest.raises(profile_store.ProfileNotFoundError):
        profile_store.load("missing", root=tmp_path)


def test_list_profiles(tmp_path: Path) -> None:
    profile_store.save(_make("alpha"), root=tmp_path)
    profile_store.save(_make("beta"), root=tmp_path)
    assert profile_store.list_profiles(root=tmp_path) == ["alpha", "beta"]


def test_delete(tmp_path: Path) -> None:
    profile_store.save(_make(), root=tmp_path)
    profile_store.delete("demo", root=tmp_path)
    assert not profile_store.exists("demo", root=tmp_path)


def test_slug_strips_unsafe() -> None:
    assert profile_store.slug_for("S4 6.9kb 60°C 30x") == "S4-6.9kb-60-C-30x"


def test_slug_rejects_empty() -> None:
    with pytest.raises(ValueError):
        profile_store.slug_for("///")
