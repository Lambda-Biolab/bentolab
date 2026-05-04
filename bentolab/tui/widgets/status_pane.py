"""Live device-status pane."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, ProgressBar, Static

from ..messages import (
    ConnectionChanged,
    RunFinished,
    RunProgressed,
    RunStarted,
    StatusUpdated,
)


class StatusPane(Vertical):
    DEFAULT_CSS = """
    StatusPane {
        border: round $accent;
        padding: 0 1;
    }
    StatusPane Label {
        margin: 0;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._profile_name = "—"
        self._block = "—"
        self._lid = "—"
        self._stage = "idle"
        self._progress = 0
        self._connected_str = "[grey50]disconnected[/]"

    def compose(self) -> ComposeResult:
        yield Label("Live Status", classes="title")
        self._connection = Static(self._connected_str, id="status-conn")
        yield self._connection
        self._profile = Static(f"Profile  {self._profile_name}", id="status-profile")
        yield self._profile
        self._stage_label = Static(f"Stage    {self._stage}", id="status-stage")
        yield self._stage_label
        self._temps = Static(f"Block    {self._block}      Lid    {self._lid}", id="status-temps")
        yield self._temps
        self._bar = ProgressBar(total=100, show_eta=False, id="status-bar")
        yield self._bar

    def on_connection_changed(self, message: ConnectionChanged) -> None:
        if message.connected:
            self._connected_str = f"[green3]● connected[/] [dim]{message.address or ''}[/]"
        else:
            err = f" [red3]({message.error})[/]" if message.error else ""
            self._connected_str = f"[grey50]○ disconnected[/]{err}"
        self._connection.update(self._connected_str)

    def on_status_updated(self, message: StatusUpdated) -> None:
        s = message.status
        self._block = f"{s.block_temperature}°C"
        self._lid = f"{s.lid_temperature}°C"
        self._temps.update(f"Block    {self._block}      Lid    {self._lid}")

    def on_run_started(self, message: RunStarted) -> None:
        self._profile_name = message.profile_name
        self._profile.update(f"Profile  {self._profile_name}")
        self._stage = "starting"
        self._stage_label.update(f"Stage    {self._stage}")
        self._bar.update(progress=0)

    def on_run_progressed(self, message: RunProgressed) -> None:
        self._progress = message.state.progress
        self._bar.update(progress=max(0, min(100, self._progress)))
        self._stage = "running"
        self._stage_label.update(f"Stage    {self._stage}  {self._progress}%")

    def on_run_finished(self, message: RunFinished) -> None:
        self._stage = "complete" if message.success else "stopped"
        self._stage_label.update(f"Stage    {self._stage}")
