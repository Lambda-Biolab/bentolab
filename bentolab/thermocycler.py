"""Unified high-level interface for Bento Lab control."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .ble_client import BentoLabBLE
from .models import DeviceState, PCRProfile
from .wifi_client import BentoLabWiFi


class BentoLab:
    """Unified interface for Bento Lab control, regardless of connection type."""

    def __init__(self, transport: BentoLabBLE | BentoLabWiFi):
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
        transport = BentoLabWiFi(host=host)
        await transport.connect()
        return cls(transport)

    async def get_state(self) -> DeviceState:
        """Get current device state."""
        return await self._transport.get_state()

    async def run_pcr(
        self,
        profile: PCRProfile,
        progress_callback: Callable[[DeviceState], Any] | None = None,
    ) -> None:
        """Start a PCR run and optionally monitor progress."""
        await self._transport.start_pcr(profile)

    async def stop(self) -> None:
        """Stop the current run."""
        await self._transport.stop_pcr()

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        await self._transport.disconnect()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()
