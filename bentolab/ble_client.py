"""BLE client for Bento Lab V1.4 (Pro, BLE-controlled)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from .models import DeviceState, PCRProfile


class BentoLabBLE:
    """Async BLE client for controlling a Bento Lab V1.4 unit."""

    def __init__(
        self,
        address: str | None = None,
        name_filter: str = r"(?i)bento",
    ):
        self.address = address
        self.name_filter = re.compile(name_filter)
        self._client: BleakClient | None = None
        self._notification_callbacks: dict[str, list[Callable]] = {}

    async def discover(self, timeout: float = 10.0) -> list[BLEDevice]:
        """Scan for Bento Lab BLE devices."""
        devices = await BleakScanner.discover(timeout=timeout)
        return [d for d in devices if d.name and self.name_filter.search(d.name)]

    async def connect(self, address: str | None = None) -> None:
        """Connect to a Bento Lab device."""
        target = address or self.address
        if not target:
            devices = await self.discover()
            if not devices:
                raise ConnectionError("No Bento Lab device found")
            target = devices[0].address
        self._client = BleakClient(target)
        await self._client.connect()

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None

    async def get_state(self) -> DeviceState:
        """Read current device state from status characteristics."""
        raise NotImplementedError("Protocol not yet reverse-engineered")

    async def send_command(self, cmd_bytes: bytes) -> bytes | None:
        """Send raw command bytes to the command characteristic."""
        raise NotImplementedError("Protocol not yet reverse-engineered")

    async def start_pcr(self, profile: PCRProfile) -> None:
        """Start a PCR run with the given profile."""
        raise NotImplementedError("Protocol not yet reverse-engineered")

    async def stop_pcr(self) -> None:
        """Stop the current PCR run."""
        raise NotImplementedError("Protocol not yet reverse-engineered")

    async def pause_pcr(self) -> None:
        """Pause the current PCR run."""
        raise NotImplementedError("Protocol not yet reverse-engineered")

    async def resume_pcr(self) -> None:
        """Resume a paused PCR run."""
        raise NotImplementedError("Protocol not yet reverse-engineered")

    async def subscribe_temperature(self, callback: Callable[[float, float], Any]) -> None:
        """Subscribe to temperature updates.

        Callback receives (block_temp, lid_temp).
        """
        raise NotImplementedError("Protocol not yet reverse-engineered")

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()
