"""``BentoLabApp`` — the workbench Textual application."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header

from .. import profiles as profile_store
from ..models import PCRProfile
from .messages import (
    ConnectionChanged,
    ProfilesChanged,
    RunFinished,
    RunProgressed,
    RunStarted,
    StatusUpdated,
)
from .modals.confirm_quit import ConfirmQuitModal, QuitChoice
from .modals.confirm_run import ConfirmRunModal
from .modals.scan_modal import ScanModal
from .modals.splash import SplashModal
from .services.orphan_attach import find_active_run
from .services.session import Session
from .widgets.device_list import DeviceList
from .widgets.profile_list import ProfileList
from .widgets.program_diagram import ProgramDiagram
from .widgets.run_history import RunHistory
from .widgets.status_pane import StatusPane
from .widgets.temp_chart import TempChart


class BentoLabApp(App):
    """Bento Lab workbench."""

    CSS_PATH = "style.tcss"
    TITLE = "Bento Lab"
    BINDINGS = [
        Binding("c", "connect", "Connect"),
        Binding("d", "disconnect", "Disconnect"),
        Binding("r", "run", "Run"),
        Binding("s", "stop", "Stop"),
        Binding("e", "edit_profile", "Edit"),
        Binding("R", "refresh_lists", "Refresh"),
        Binding("D", "forget_device", "Forget device"),
        Binding("question_mark", "splash", "Help"),
        Binding("q", "quit_workbench", "Quit"),
    ]

    def __init__(self, *, show_splash: bool = True) -> None:
        super().__init__()
        self.session = Session(self)
        self._current_profile: str | None = None
        self._current_progress: int = 0
        self._is_running: bool = False
        self._show_splash = show_splash

    def on_mount(self) -> None:
        if self._show_splash:
            self.push_screen(SplashModal())

    def action_splash(self) -> None:
        self.push_screen(SplashModal())

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-row"):
            with Vertical(id="left-column"):
                self.device_list = DeviceList()
                yield self.device_list
                self.profile_list = ProfileList()
                yield self.profile_list
                self.history = RunHistory()
                yield self.history
            with Vertical(id="right-column"):
                self.status_pane = StatusPane()
                yield self.status_pane
                self.diagram = ProgramDiagram()
                yield self.diagram
                self.chart = TempChart()
                yield self.chart
        yield Footer()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    @work
    async def action_connect(self) -> None:
        if self.session.connected:
            self.notify("Already connected.")
            return
        address = self.device_list.selected
        if address is not None:
            try:
                await self.session.connect(address=address)
                return
            except Exception as e:
                # Cached address rotated / device no longer advertising
                # under that ID. Forget it and fall through to a scan.
                self.notify(
                    f"Cached connect failed ({e}). Scanning for new address…",
                    severity="warning",
                    timeout=5.0,
                )
                from .. import devices as device_registry  # noqa: PLC0415

                device_registry.forget(address)
                self.device_list.refresh_list()

        picked = await self.push_screen_wait(ScanModal())
        if not picked:
            return
        self.device_list.refresh_list()
        try:
            await self.session.connect(address=picked)
        except Exception as e:
            self.notify(f"Connect failed: {e}", severity="error")

    @work
    async def action_forget_device(self) -> None:
        address = self.device_list.selected
        if address is None:
            self.notify("Highlight a device first.")
            return
        from .. import devices as device_registry  # noqa: PLC0415

        device_registry.forget(address)
        self.device_list.refresh_list()
        self.notify(f"Forgot {address}")

    async def action_disconnect(self) -> None:
        await self.session.disconnect()

    @work
    async def action_run(self) -> None:
        if self._is_running:
            self.notify("A run is already in progress.")
            return
        if not self.session.connected:
            self.notify("Connect to a device first ([c]).", severity="warning")
            return
        name = self.profile_list.selected
        if not name:
            self.notify("Select a profile first.", severity="warning")
            return
        try:
            profile = profile_store.load(name)
        except profile_store.ProfileNotFoundError:
            self.notify(f"Profile not found: {name}", severity="error")
            return
        ok = await self.push_screen_wait(ConfirmRunModal(profile, self.session.address))
        if not ok:
            return
        self.run_worker(self._run_profile(profile), exclusive=True)

    async def action_stop(self) -> None:
        if not self._is_running:
            self.notify("No run in progress.")
            return
        try:
            await self.session.stop_run()
            self.notify("Stop sent.")
        except Exception as e:
            self.notify(f"Stop failed: {e}", severity="error")

    def action_edit_profile(self) -> None:
        name = self.profile_list.selected
        if not name:
            self.notify("Select a profile first.", severity="warning")
            return
        try:
            path: Path = Path(profile_store._path_for(name))
        except Exception as e:
            self.notify(f"Cannot resolve profile: {e}", severity="error")
            return
        self.notify(
            f"Profile YAML at {path}. Use `bentolab profile edit {name}` to edit in $EDITOR."
        )

    def action_refresh_lists(self) -> None:
        self.device_list.refresh_list()
        self.profile_list.refresh_list()
        self.history.refresh_list()

    @work
    async def action_quit_workbench(self) -> None:
        if not self._is_running:
            self.exit()
            return
        choice = await self.push_screen_wait(
            ConfirmQuitModal(
                profile_name=self._current_profile or "(unknown)",
                progress=self._current_progress,
            )
        )
        if choice == QuitChoice.CANCEL:
            return
        if choice == QuitChoice.STOP_AND_QUIT:
            with contextlib.suppress(Exception):
                await self.session.stop_run()
        self.exit()

    # ------------------------------------------------------------------
    # Run worker
    # ------------------------------------------------------------------

    async def _run_profile(self, profile: PCRProfile) -> None:
        self._is_running = True
        try:
            await self.session.run_profile(profile)
        except Exception as e:
            self.notify(f"Run failed: {e}", severity="error")
        finally:
            self._is_running = False
            self.history.refresh_list()

    # ------------------------------------------------------------------
    # Message handlers (forward to widgets)
    # ------------------------------------------------------------------

    def on_status_updated(self, message: StatusUpdated) -> None:
        self._maybe_attach_orphan(message)
        self.status_pane.on_status_updated(message)
        self.chart.on_status_updated(message)
        self._refresh_external_diagram()

    def _maybe_attach_orphan(self, message: StatusUpdated) -> None:
        """Attach to a CLI- or externally-started run, if one is in flight."""
        if self._is_running:
            return
        if self.status_pane._active_profile is not None:
            return
        if message.status.running == 0:
            return
        active = find_active_run()
        if active is None:
            return
        self.status_pane.attach_external_run(active.profile, active.started_at)
        self.diagram.set_profile(active.profile)
        self._current_profile = active.profile.name
        self.notify(
            f"Attached to in-flight run: {active.profile.name}",
            severity="information",
            timeout=4.0,
        )

    def _refresh_external_diagram(self) -> None:
        pane = self.status_pane
        if pane._active_profile is None or pane._external_started_at is None:
            return
        elapsed = (datetime.now(UTC) - pane._external_started_at).total_seconds()
        self.diagram.update_stage(pane._active_profile.stage_at(elapsed))

    def on_connection_changed(self, message: ConnectionChanged) -> None:
        self.status_pane.on_connection_changed(message)

    def on_run_started(self, message: RunStarted) -> None:
        self._current_profile = message.profile_name
        self._current_progress = 0
        self.status_pane.on_run_started(message)
        self.diagram.set_profile(message.profile)

    def on_run_progressed(self, message: RunProgressed) -> None:
        self._current_progress = message.state.progress
        self.status_pane.on_run_progressed(message)
        if self.status_pane._active_profile is not None:
            stage = self.status_pane._active_profile.stage_at(message.state.elapsed_seconds)
            self.diagram.update_stage(stage)

    def on_run_finished(self, message: RunFinished) -> None:
        self.status_pane.on_run_finished(message)
        self.diagram.update_stage(None)

    def on_profiles_changed(self, _message: ProfilesChanged) -> None:
        self.profile_list.refresh_list()


def run() -> None:
    BentoLabApp().run()
