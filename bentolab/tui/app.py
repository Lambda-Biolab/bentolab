"""``BentoLabApp`` — the workbench Textual application."""

from __future__ import annotations

import contextlib
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
from .services.session import Session
from .widgets.device_list import DeviceList
from .widgets.profile_list import ProfileList
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
        if address is None:
            picked = await self.push_screen_wait(ScanModal())
            if not picked:
                return
            address = picked
            self.device_list.refresh_list()
        try:
            await self.session.connect(address=address)
        except Exception as e:
            self.notify(f"Connect failed: {e}", severity="error")

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
        self.status_pane.on_status_updated(message)
        self.chart.on_status_updated(message)

    def on_connection_changed(self, message: ConnectionChanged) -> None:
        self.status_pane.on_connection_changed(message)

    def on_run_started(self, message: RunStarted) -> None:
        self._current_profile = message.profile_name
        self._current_progress = 0
        self.status_pane.on_run_started(message)

    def on_run_progressed(self, message: RunProgressed) -> None:
        self._current_progress = message.state.progress
        self.status_pane.on_run_progressed(message)

    def on_run_finished(self, message: RunFinished) -> None:
        self.status_pane.on_run_finished(message)

    def on_profiles_changed(self, _message: ProfilesChanged) -> None:
        self.profile_list.refresh_list()


def run() -> None:
    BentoLabApp().run()
