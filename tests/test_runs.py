"""Behavioral tests for the unified run state machine.

Covers the A1 refactor that collapsed ``PCRRunState`` and the API-side
``RunStates`` into one canonical model in :mod:`bentolab.runs`.
Behavioral invariants tested:
    - Lifecycle transitions: accepted -> running -> terminal are valid;
      terminal -> anything is rejected.
    - Device lock: at most one run holds the lock at a time.
    - Result package: includes temperature log + errors in expected shape.
    - State helpers: ``is_active`` / ``is_terminal`` agree with membership
      in the public frozensets.
"""

from __future__ import annotations

import pytest

from bentolab.runs import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    RunLifecycle,
    RunManager,
    RunState,
    is_active,
    is_terminal,
)


def _profile_dict() -> dict:
    return {
        "name": "test",
        "initial_denaturation": {"temperature": 95, "duration": 180},
        "cycles": [
            {"repeat": 30, "denaturation": [95, 30], "annealing": [58, 30], "extension": [72, 60]}
        ],
        "final_extension": {"temperature": 72, "duration": 300},
    }


# ---------------------------------------------------------------------------
# Lifecycle transition rules
# ---------------------------------------------------------------------------


def test_terminal_states_are_final() -> None:
    """Once a run is in a terminal state, transition_to rejects any further change."""
    mgr = RunManager()
    run_id = mgr.create_run(profile=_profile_dict())
    assert mgr.transition_to(run_id, RunLifecycle.RUNNING) is True
    assert mgr.transition_to(run_id, RunLifecycle.COMPLETED) is True

    # Any further transition is rejected
    assert mgr.transition_to(run_id, RunLifecycle.RUNNING) is False
    assert mgr.transition_to(run_id, RunLifecycle.ABORTED) is False


def test_aborted_sets_aborted_at_timestamp() -> None:
    """Transitioning to ABORTED stamps the run with an aborted_at ISO timestamp."""
    mgr = RunManager()
    run_id = mgr.create_run(profile=_profile_dict())
    mgr.transition_to(run_id, RunLifecycle.ABORTED)

    run = mgr.get_run(run_id)
    assert run is not None
    assert run["aborted_at"] is not None
    assert run["completed_at"] is not None  # terminal stamps completed_at too


def test_completed_sets_completed_at_but_not_aborted_at() -> None:
    """Non-aborted terminal states stamp completed_at only."""
    mgr = RunManager()
    run_id = mgr.create_run(profile=_profile_dict())
    mgr.transition_to(run_id, RunLifecycle.COMPLETED)

    run = mgr.get_run(run_id)
    assert run is not None
    assert run["completed_at"] is not None
    assert run["aborted_at"] is None


def test_unknown_run_id_returns_false() -> None:
    """transition_to returns False (not raises) for unknown run ids."""
    mgr = RunManager()
    assert mgr.transition_to("does-not-exist", RunLifecycle.RUNNING) is False


# ---------------------------------------------------------------------------
# Device lock semantics
# ---------------------------------------------------------------------------


def test_create_run_acquires_lock() -> None:
    """create_run acquires the exclusive device lock."""
    mgr = RunManager()
    assert mgr.is_locked is False
    run_id = mgr.create_run(profile=_profile_dict())
    assert mgr.is_locked is True
    assert mgr.locked_by == run_id


def test_terminal_transition_releases_lock() -> None:
    """Reaching a terminal state releases the device lock."""
    mgr = RunManager()
    run_id = mgr.create_run(profile=_profile_dict())
    mgr.transition_to(run_id, RunLifecycle.COMPLETED)
    assert mgr.is_locked is False
    assert mgr.locked_by is None


def test_concurrent_create_run_raises() -> None:
    """Trying to create a second run while the lock is held raises RuntimeError."""
    mgr = RunManager()
    mgr.create_run(profile=_profile_dict())
    with pytest.raises(RuntimeError, match="already locked"):
        mgr.create_run(profile=_profile_dict())


def test_force_release_lock_releases_without_changing_state() -> None:
    """force_release_lock is for operator intervention -- does not transition state."""
    mgr = RunManager()
    run_id = mgr.create_run(profile=_profile_dict())
    assert mgr.force_release_lock() == run_id
    assert mgr.is_locked is False
    # The run record is still in ACCEPTED -- operator is expected to
    # transition it explicitly afterwards.
    assert mgr.get_run(run_id)["state"] == RunLifecycle.ACCEPTED


# ---------------------------------------------------------------------------
# Result packaging
# ---------------------------------------------------------------------------


def test_get_results_includes_temperature_log() -> None:
    """Result package contains the captured temperature history."""
    mgr = RunManager()
    run_id = mgr.create_run(profile=_profile_dict())
    mgr.record_temperature(run_id, 25.5, 24.0)
    mgr.record_temperature(run_id, 95.0, 110.0)
    mgr.transition_to(run_id, RunLifecycle.COMPLETED)

    results = mgr.get_results(run_id)
    assert results is not None
    assert len(results["temperature_log"]) == 2
    assert results["temperature_log"][0]["block"] == 25.5
    assert results["temperature_log"][1]["lid"] == 110.0


def test_get_results_for_unknown_run_returns_none() -> None:
    """get_results returns None for unknown run ids (handler raises NotFound)."""
    mgr = RunManager()
    assert mgr.get_results("missing") is None


# ---------------------------------------------------------------------------
# State membership helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", list(RunLifecycle))
def test_is_active_agrees_with_active_set(state: RunLifecycle) -> None:
    assert is_active(state) is (state in ACTIVE_STATES)


@pytest.mark.parametrize("state", list(RunLifecycle))
def test_is_terminal_agrees_with_terminal_set(state: RunLifecycle) -> None:
    assert is_terminal(state) is (state in TERMINAL_STATES)


def test_active_and_terminal_are_disjoint() -> None:
    """No lifecycle phase is both active and terminal -- this would be a bug."""
    assert ACTIVE_STATES.isdisjoint(TERMINAL_STATES)


# ---------------------------------------------------------------------------
# RunState dataclass
# ---------------------------------------------------------------------------


def test_run_state_running_property_reflects_lifecycle() -> None:
    """RunState.running is True iff the lifecycle is RUNNING."""
    idle = RunState(state=RunLifecycle.IDLE)
    running = RunState(state=RunLifecycle.RUNNING)
    completed = RunState(state=RunLifecycle.COMPLETED)

    assert idle.running is False
    assert running.running is True
    assert completed.running is False


def test_run_state_str_enum_survives_serialization() -> None:
    """RunLifecycle values serialize to their string forms for JSON/dict storage."""
    # StrEnum.__str__ returns the string value, which is what we want for JSON.
    assert str(RunLifecycle.RUNNING) == "running"
    assert RunLifecycle.RUNNING.value == "running"
    assert RunLifecycle.UNKNOWN_REVIEW.value == "unknown_requires_operator_review"
    # JSON.dumps emits the string value directly
    import json

    assert json.dumps({"state": RunLifecycle.RUNNING}) == '{"state": "running"}'


def test_run_manager_accepts_string_lifecycle() -> None:
    """RunManager.transition_to accepts plain strings (matches existing call sites)."""
    mgr = RunManager()
    run_id = mgr.create_run(profile=_profile_dict())
    assert mgr.transition_to(run_id, "running") is True
    assert mgr.get_run(run_id)["state"] == RunLifecycle.RUNNING
