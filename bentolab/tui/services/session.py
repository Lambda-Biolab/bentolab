"""TUI session service — owns the single :class:`BentoLabBLE` instance.

All widgets observe the session via Textual messages (see
:mod:`bentolab.tui.messages`). The BLE notification callback runs on
bleak's loop; ``post_message`` hands the event back to the UI loop.
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

    def __init__(self, app: App):
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
            self.address = getattr(self.lab, "_connected_address", address)
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
        if self.lab is None:
            return
        try:
            await self.lab.disconnect()
        finally:
            self.lab = None
            self.app.post_message(ConnectionChanged(connected=False))

    async def run_profile(self, profile: PCRProfile) -> None:
        """Start a run and tail it, emitting messages until it finishes."""
        if not self.lab or not self.lab.is_connected:
            raise RuntimeError("Not connected")
        run_id = profile.name
        self._run_log = SessionLogger(profile.name)
        self._run_log.event(
            "run_config",
            {"profile": profile.to_dict(), "lid_temp": profile.lid_temperature},
        )
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
