"""Wi-Fi/HTTP client for Bento Lab V1.31.

Stub. The Wi-Fi protocol has not been reverse-engineered yet; every
method raises :class:`NotImplementedError`. The public surface is kept
aligned with :class:`bentolab.ble_client.BentoLabBLE` so a future
implementation can slot in behind the shared
:class:`bentolab.thermocycler.BentoLab` wrapper without an API break.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .protocol import StatusBroadcast

if TYPE_CHECKING:
    import aiohttp


class BentoLabWiFi:
    """HTTP/WebSocket client stub for Bento Lab V1.31."""

    def __init__(self, host: str | None = None, port: int = 80):
        self.host = host
        self.port = port
        self._session: aiohttp.ClientSession | None = None

    async def discover(self) -> str | None:
        """Discover Bento Lab on the network via mDNS."""
        raise NotImplementedError("Protocol not yet reverse-engineered")

    async def connect(self, host: str | None = None) -> None:
        """Connect to the Bento Lab Wi-Fi unit."""
        import aiohttp  # local import: keeps aiohttp optional for the core install

        self.host = host or self.host
        if not self.host:
            self.host = await self.discover()
        self._session = aiohttp.ClientSession()

    async def disconnect(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def get_status(self) -> StatusBroadcast:
        """Read current device status."""
        raise NotImplementedError("Protocol not yet reverse-engineered")

    async def get_firmware_version(self) -> str:
        """Query firmware version."""
        raise NotImplementedError("Protocol not yet reverse-engineered")

    async def start_run(
        self,
        name: str = "Python Run",
        stages: list[tuple[float, int]] | None = None,
        cycles: list[tuple[int, int, int]] | None = None,
        lid_temp: float = 110.0,
        slot: int = 0,
    ) -> None:
        """Start a PCR run."""
        raise NotImplementedError("Protocol not yet reverse-engineered")

    async def stop_run(self) -> None:
        """Stop the current PCR run."""
        raise NotImplementedError("Protocol not yet reverse-engineered")

    async def __aenter__(self) -> BentoLabWiFi:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()
