"""Wi-Fi/HTTP client for Bento Lab V1.31."""

from __future__ import annotations

import aiohttp

from .models import DeviceState, PCRProfile


class BentoLabWiFi:
    """HTTP/WebSocket client for controlling a Bento Lab V1.31 unit."""

    def __init__(self, host: str | None = None, port: int = 80):
        self.host = host
        self.port = port
        self._session: aiohttp.ClientSession | None = None

    async def discover(self) -> str | None:
        """Discover Bento Lab on the network via mDNS."""
        raise NotImplementedError("Protocol not yet reverse-engineered")

    async def connect(self, host: str | None = None) -> None:
        """Connect to the Bento Lab Wi-Fi unit."""
        self.host = host or self.host
        if not self.host:
            self.host = await self.discover()
        self._session = aiohttp.ClientSession()

    async def disconnect(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def get_state(self) -> DeviceState:
        """Read current device state."""
        raise NotImplementedError("Protocol not yet reverse-engineered")

    async def get_firmware_version(self) -> str:
        """Query firmware version."""
        raise NotImplementedError("Protocol not yet reverse-engineered")

    async def start_pcr(self, profile: PCRProfile) -> None:
        """Start a PCR run."""
        raise NotImplementedError("Protocol not yet reverse-engineered")

    async def stop_pcr(self) -> None:
        """Stop the current PCR run."""
        raise NotImplementedError("Protocol not yet reverse-engineered")

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()
