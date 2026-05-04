"""Textual messages posted by the session service.

Widgets subscribe to these via ``on_<message-name>`` handlers; nothing
else writes to widgets directly. Keeps the BLE callback off the UI
thread.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.message import Message

from ..ble_client import PCRRunState
from ..protocol import StatusBroadcast


@dataclass
class StatusUpdated(Message):
    status: StatusBroadcast


@dataclass
class RunStarted(Message):
    profile_name: str
    run_id: str


@dataclass
class RunProgressed(Message):
    state: PCRRunState


@dataclass
class RunFinished(Message):
    profile_name: str
    run_id: str
    success: bool


@dataclass
class ConnectionChanged(Message):
    connected: bool
    address: str | None = None
    error: str | None = None


@dataclass
class ProfilesChanged(Message):
    """Emitted by the editor or import flows so the list re-reads."""
