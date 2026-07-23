"""Tests for :mod:`bentolab.tui.messages`."""

from __future__ import annotations

from unittest.mock import MagicMock

from bentolab.models import PCRProfile
from bentolab.protocol import StatusBroadcast
from bentolab.runs import RunLifecycle, RunState
from bentolab.tui.messages import (
    ConnectionChanged,
    ProfilesChanged,
    RunFinished,
    RunProgressed,
    RunStarted,
    StatusUpdated,
)


def _status() -> StatusBroadcast:
    """Construct a StatusBroadcast for tests; uses positional args matching the dataclass."""
    return StatusBroadcast(1, 0, 0, 0, 25, 110, 0)


def test_status_updated_carries_broadcast() -> None:
    msg = StatusUpdated(status=_status())
    assert msg.status.block_temperature == 25.0
    assert msg.status.lid_temperature == 110.0


def test_run_started_profile_name_property() -> None:
    profile = PCRProfile(name="demo")
    msg = RunStarted(profile=profile, run_id="run-1")
    assert msg.profile_name == "demo"
    assert msg.run_id == "run-1"


def test_run_progressed_carries_typed_state() -> None:
    state = RunState(
        state=RunLifecycle.RUNNING,
        progress=42,
        block_temperature=72.0,
        lid_temperature=110.0,
        elapsed_seconds=300.0,
    )
    msg = RunProgressed(state=state)
    assert msg.state.progress == 42
    assert msg.state.running is True
    assert msg.state.elapsed_seconds == 300.0


def test_run_finished_carries_outcome() -> None:
    msg = RunFinished(profile_name="demo", run_id="run-1", success=True)
    assert msg.success is True
    assert msg.profile_name == "demo"


def test_connection_changed_defaults_optional() -> None:
    """``address`` and ``error`` are optional on the message dataclass."""
    msg = ConnectionChanged(connected=True)
    assert msg.address is None
    assert msg.error is None
    msg2 = ConnectionChanged(connected=False, error="timeout")
    assert msg2.error == "timeout"


def test_profiles_changed_is_a_no_arg_marker() -> None:
    """``ProfilesChanged`` carries no payload; instantiation is the signal."""
    msg = ProfilesChanged()
    # No attributes to assert — sanity only that the type is constructible.
    assert isinstance(msg, ProfilesChanged)


def test_messages_subclass_textual_message() -> None:
    """Each dataclass message is a valid Textual Message subclass."""
    from textual.message import Message

    for cls in (StatusUpdated, RunFinished, ConnectionChanged):
        msg = cls(**{f: MagicMock() for f in cls.__dataclass_fields__})  # type: ignore[arg-type]
        assert isinstance(msg, Message)
