"""Textual messages posted by the session service.

Widgets subscribe to these via ``on_<message-name>`` handlers; nothing
else writes to widgets directly. Keeps the BLE callback off the UI
thread.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.message import Message

from ..models import PCRProfile
from ..protocol import StatusBroadcast
from ..runs import RunState


@dataclass
class StatusUpdated(Message):
    """Per ``bb;...`` status broadcast (~5 s interval)."""

    status: StatusBroadcast


@dataclass
class RunStarted(Message):
    """Posted immediately before the device call to begin a run."""

    profile: PCRProfile
    run_id: str

    @property
    def profile_name(self) -> str:
        return self.profile.name


@dataclass
class RunProgressed(Message):
    """Per poll — the active run's :class:`~bentolab.runs.RunState`."""

    state: RunState


@dataclass
class RunFinished(Message):
    """Terminal message after the run loop exits."""

    profile_name: str
    run_id: str
    success: bool


@dataclass
class ConnectionChanged(Message):
    """Emitted whenever the BLE link state flips."""

    connected: bool
    address: str | None = None
    error: str | None = None


@dataclass
class ProfilesChanged(Message):
    """Emitted by the editor or import flows so the list re-reads."""
