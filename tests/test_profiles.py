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


def test_slug_collapses_runs_of_bad_chars() -> None:
    """Regression: the previous _safe_slug produced 'a---b' for 'a!!!b'.

    Different from profiles.slug_for which collapsed to 'a-b'. After
    consolidation both call sites use the regex version. Pin the
    collapsing behavior so future refactors don't regress.
    """
    assert profile_store.slug_for("a!!!b") == "a-b"
    assert profile_store.slug_for("  spaces  ") == "spaces"
    assert profile_store.slug_for("###weird###") == "weird"


def test_slug_rejects_empty() -> None:
    with pytest.raises(ValueError):
        profile_store.slug_for("///")


def test_path_for_returns_yaml_in_root(tmp_path: Path) -> None:
    """``path_for`` is the public TUI-facing accessor for the YAML file path."""
    path = profile_store.path_for("alpha", root=tmp_path)
    assert path == tmp_path / "alpha.yaml"


def test_path_for_uses_slug_for_name_normalization(tmp_path: Path) -> None:
    """Special chars in the name collapse to a stable slug for the file path."""
    path = profile_store.path_for("My Cool Run!!!", root=tmp_path)
    # exact slug depends on _slugs.slug_for; assert .yaml extension and slug-stability
    assert path.suffix == ".yaml"
    assert path.stem != "My Cool Run!!!"


def test_path_for_does_not_require_file_existence(tmp_path: Path) -> None:
    """``path_for`` returns the path even when no file has been saved."""
    path = profile_store.path_for("not-yet-saved", root=tmp_path)
    assert path.parent == tmp_path
    assert not path.exists()
