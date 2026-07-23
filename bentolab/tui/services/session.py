"""TUI session service — owns the single :class:`BentoLabBLE` instance.

All widgets observe the session via Textual messages (see
:mod:`bentolab.tui.messages`). The BLE notification callback runs on
bleak's loop; :meth:`textual.app.App.post_message` hands the event
back to the UI loop.

Critical fix vs PR #15: emits a ``run_started`` NDJSON event BEFORE
the ``async for`` loop starts. orphan_attach.find_active_run() filters
NDJSON files by ``saw_run_started``; without this line every log
file looks like a stub from a failed connect, and orphan detection
silently never returns a match.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ... import devices as device_registry
from ..._logging import SessionLogger
from ...ble_client import BentoLabBLE
from ...models import PCRProfile
from ...protocol import StatusBroadcast
from ..messages import (
    ConnectionChanged,
    RunFinished,
    RunProgressed,
    RunStarted,
    StatusUpdated,
)

if TYPE_CHECKING:
    from textual.app import App


class Session:
    """Live BLE session with the Bento Lab.

    Posts :class:`StatusUpdated` for every status broadcast,
    :class:`RunProgressed` for every run-tail step, and
    :class:`ConnectionChanged` whenever the link state flips.
    """

    def __init__(self, app: App) -> None:
        self.app = app
        self.lab: BentoLabBLE | None = None
        self.address: str | None = None
        self._run_log: SessionLogger | None = None

    @property
    def connected(self) -> bool:
        return bool(self.lab and self.lab.is_connected)

    async def connect(self, address: str | None = None) -> None:
        """Connect (auto-discover if address is None) and wire callbacks."""
        if self.connected:
            return
        self.lab = BentoLabBLE(address=address) if address else BentoLabBLE()
        self.lab.on_status(self._forward_status)
        self.lab.on_disconnect(self._forward_disconnect)
        try:
            await self.lab.connect(address)
            # _connected_address is set in BentoLabBLE.connect(); treat as a
            # temporary attribute the TUI reads once after connect.
            self.address = self.lab._connected_address or address
            if self.address:
                # Refresh the cached entry so future launches don't retry a
                # rotated/stale address.
                device_registry.remember(
                    device_registry.Device(address=self.address, transport="ble")
                )
            self.app.post_message(ConnectionChanged(connected=True, address=self.address))
        except Exception as e:
            self.lab = None
            self.app.post_message(ConnectionChanged(connected=False, error=str(e)))
            raise

    async def disconnect(self) -> None:
        """Tear down the BLE link and emit ``ConnectionChanged(False)``."""
        if self.lab is None:
            return
        try:
            await self.lab.disconnect()
        finally:
            self.lab = None
            self.app.post_message(ConnectionChanged(connected=False))

    async def run_profile(self, profile: PCRProfile) -> None:
        """Start a run, tail it, and emit messages until it finishes.

        The actual run timing/termination logic lives in
        :meth:`BentoLabBLE.run_profile` (yields ``RunState``); this
        method consumes that iterator, logs each tick, and bridges
        into the UI loop.
        """
        if not self.lab or not self.lab.is_connected:
            raise RuntimeError("Not connected")
        run_id = profile.name
        self._run_log = SessionLogger(profile.name)
        self._run_log.event(
            "run_config",
            {"profile": profile.to_dict(), "lid_temp": profile.lid_temperature},
        )
        # CRITICAL: orphan_attach's find_active_run filters by saw_run_started;
        # omit this and orphan detection silently fails.
        self._run_log.event("run_started", {"profile": profile.name})
        self.app.post_message(RunStarted(profile=profile, run_id=run_id))
        success = False
        try:
            async for state in self.lab.run_profile(profile, lid_temp=profile.lid_temperature):
                self._run_log.event(
                    "run_progress",
                    {
                        "running": state.running,
                        "progress": state.progress,
                        "block": state.block_temperature,
                        "lid": state.lid_temperature,
                        "elapsed": state.elapsed_seconds,
                    },
                )
                self.app.post_message(RunProgressed(state=state))
            success = True
        finally:
            if self._run_log is not None:
                self._run_log.event("run_finished", {"success": success})
                self._run_log.close()
                self._run_log = None
            self.app.post_message(
                RunFinished(profile_name=profile.name, run_id=run_id, success=success)
            )

    async def stop_run(self) -> None:
        if self.lab is None:
            return
        await self.lab.stop_run()

    # ------------------------------------------------------------------
    # Internal callbacks (run on bleak's loop)
    # ------------------------------------------------------------------

    def _forward_status(self, status: StatusBroadcast) -> None:
        self.app.post_message(StatusUpdated(status=status))

    def _forward_disconnect(self) -> None:
        self.lab = None
        self.app.post_message(ConnectionChanged(connected=False))
