"""Unified high-level interface for Bento Lab control.

Wraps either :class:`BentoLabBLE` or :class:`BentoLabWiFi` behind a single
:class:`BentoLab` facade. The facade speaks in high-level domain types
(:class:`DeviceState`, :class:`PCRProfile`) and delegates to whichever
transport was selected at construction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Union

from .ble_client import BentoLabBLE
from .models import DeviceState, DeviceStatus, PCRProfile
from .protocol import StatusBroadcast

if TYPE_CHECKING:
    from .wifi_client import BentoLabWiFi

Transport = Union[BentoLabBLE, "BentoLabWiFi"]


def _status_to_state(status: StatusBroadcast) -> DeviceState:
    """Adapt a raw protocol :class:`StatusBroadcast` to the public :class:`DeviceState`."""
    return DeviceState(
        connected=True,
        block_temperature=float(status.block_temperature),
        lid_temperature=float(status.lid_temperature),
        status=DeviceStatus.RUNNING if status.running else DeviceStatus.IDLE,
    )


class BentoLab:
    """Unified interface for Bento Lab control, regardless of connection type."""

    def __init__(self, transport: Transport):
        self._transport = transport

    @classmethod
    async def connect_ble(cls, address: str | None = None) -> BentoLab:
        """Connect to a Bento Lab via BLE."""
        transport = BentoLabBLE(address=address)
        await transport.connect()
        return cls(transport)

    @classmethod
    async def connect_wifi(cls, host: str | None = None) -> BentoLab:
        """Connect to a Bento Lab via Wi-Fi."""
        from .wifi_client import BentoLabWiFi  # deferred: keeps aiohttp optional

        transport = BentoLabWiFi(host=host)
        await transport.connect()
        return cls(transport)

    async def get_state(self) -> DeviceState:
        """Get current device state."""
        status = await self._transport.get_status()
        return _status_to_state(status)

    async def run_pcr(self, profile: PCRProfile, lid_temp: float = 110.0) -> None:
        """Start a PCR run from a :class:`PCRProfile`.

        Flattens the profile into the device's stage/cycle layout and
        issues a single ``start_run`` call. Does not block until
        completion — use :meth:`BentoLabBLE.run_profile` directly if you
        want streaming progress updates.
        """
        stages, cycles = profile.to_stages_and_cycles()
        await self._transport.start_run(
            name=profile.name,
            stages=stages,
            cycles=cycles,
            lid_temp=lid_temp,
        )

    async def stop(self) -> None:
        """Stop the current run."""
        await self._transport.stop_run()

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        await self._transport.disconnect()

    async def __aenter__(self) -> BentoLab:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()
