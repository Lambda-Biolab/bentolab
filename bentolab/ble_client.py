"""BLE client for Bento Lab Pro — real protocol implementation.

Connects via Nordic UART Service and speaks the semicolon-delimited
text protocol decoded from HCI snoop capture.

Usage:
    async with BentoLabBLE() as lab:
        status = await lab.get_status()
        print(f"Block: {status.block_temperature}°C, Lid: {status.lid_temperature}°C")

        profiles = await lab.list_profiles()
        for p in profiles:
            print(f"  {p.index}: {p.name}")

        profile = await lab.get_profile(5)
        for stage in profile.stages:
            print(f"  {stage.temperature}°C for {stage.duration}s")
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from bleak import BleakClient, BleakError, BleakScanner

from .protocol import (
    BENTO_ADV_SERVICE_UUID,
    NUS_RX_CHAR_UUID,
    NUS_TX_CHAR_UUID,
    CycleData,
    ProfileEntry,
    RunStatus,
    StageData,
    StatusBroadcast,
    decode_response,
    encode_command,
    encode_cycle,
    encode_lid_temp,
    encode_profile_name,
    encode_profile_slot,
    encode_stage,
)


@dataclass
class ProfileData:
    """Complete PCR profile data retrieved from the device."""

    name: str = ""
    slot: int = 0
    stages: list[StageData] = field(default_factory=list)
    cycles: list[CycleData] = field(default_factory=list)
    lid_temperature: float = 0.0


class BentoLabBLE:
    """Async BLE client for controlling a Bento Lab Pro.

    Communicates via Nordic UART Service using the decoded text protocol.
    """

    def __init__(
        self,
        address: str | None = None,
        name_filter: str = r"(?i)bento",
    ):
        self.address = address
        self.name_filter = re.compile(name_filter)
        self._client: BleakClient | None = None
        self._rx_buffer: list[dict] = []
        self._rx_event = asyncio.Event()
        self._status_callbacks: list[Callable[[StatusBroadcast], Any]] = []
        self._last_status: StatusBroadcast | None = None

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        """Handle NUS TX notifications from the device."""
        parsed = decode_response(bytes(data))
        if parsed["type"] == "status":
            self._last_status = parsed["data"]
            for cb in self._status_callbacks:
                cb(self._last_status)
        elif parsed["type"] != "continuation":
            self._rx_buffer.append(parsed)
            self._rx_event.set()

    async def _send(self, cmd: str) -> None:
        """Send a command to the device via NUS RX."""
        if not self._client or not self._client.is_connected:
            raise ConnectionError("Not connected to Bento Lab")
        data = encode_command(cmd)
        await self._client.write_gatt_char(NUS_RX_CHAR_UUID, data, response=False)

    async def _send_raw(self, data: bytes) -> None:
        """Send raw bytes to NUS RX."""
        if not self._client or not self._client.is_connected:
            raise ConnectionError("Not connected to Bento Lab")
        await self._client.write_gatt_char(NUS_RX_CHAR_UUID, data, response=False)

    async def _collect_responses(
        self, timeout: float = 3.0, expected_end: str | None = None
    ) -> list[dict]:
        """Collect responses until timeout or expected end marker."""
        self._rx_buffer.clear()
        self._rx_event.clear()
        deadline = asyncio.get_event_loop().time() + timeout
        results = []

        while asyncio.get_event_loop().time() < deadline:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(self._rx_event.wait(), timeout=remaining)
                self._rx_event.clear()
                results.extend(self._rx_buffer)
                self._rx_buffer.clear()
                if expected_end and any(r["type"] == expected_end for r in results):
                    break
            except TimeoutError:
                break

        return results

    async def discover(self, timeout: float = 10.0) -> list[tuple[Any, Any]]:
        """Scan for Bento Lab BLE devices.

        Returns list of (BLEDevice, AdvertisementData) tuples.
        """
        discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
        return [
            (dev, adv)
            for _addr, (dev, adv) in discovered.items()
            if (dev.name and self.name_filter.search(dev.name))
            or BENTO_ADV_SERVICE_UUID in adv.service_uuids
        ]

    async def connect(self, address: str | None = None) -> None:
        """Connect to a Bento Lab device."""
        target = address or self.address
        if not target:
            devices = await self.discover()
            if not devices:
                raise ConnectionError("No Bento Lab device found")
            target = devices[0][0].address

        self._client = BleakClient(target)
        await self._client.connect()
        await self._client.start_notify(NUS_TX_CHAR_UUID, self._on_notify)

        # Send handshake
        await self._send("Xa")
        await asyncio.sleep(0.5)

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        if self._client and self._client.is_connected:
            with contextlib.suppress(BleakError):
                await self._client.stop_notify(NUS_TX_CHAR_UUID)
            await self._client.disconnect()
        self._client = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def get_status(self) -> StatusBroadcast:
        """Get the current device status.

        Waits for the next status broadcast (sent every ~5 seconds).
        If a cached status is available and fresh, returns immediately.
        """
        if self._last_status:
            return self._last_status

        # Wait for next status broadcast
        event = asyncio.Event()
        old_status = self._last_status

        def on_status(s: StatusBroadcast) -> None:
            if s is not old_status:
                event.set()

        self._status_callbacks.append(on_status)
        try:
            await asyncio.wait_for(event.wait(), timeout=10.0)
        finally:
            self._status_callbacks.remove(on_status)

        if not self._last_status:
            raise TimeoutError("No status broadcast received")
        return self._last_status

    async def list_profiles(self) -> list[ProfileEntry]:
        """List all PCR profiles stored on the device."""
        await self._send("p")
        responses = await self._collect_responses(timeout=5.0, expected_end="profile_end")

        profiles = []
        for r in responses:
            if r["type"] == "profile_entry":
                profiles.append(r["data"])
        return profiles

    async def get_profile(self, slot: int) -> ProfileData:
        """Retrieve a complete PCR profile from the device by slot ID."""
        await self._send(f"{slot}\npc")
        responses = await self._collect_responses(timeout=5.0)

        profile = ProfileData(slot=slot)
        for r in responses:
            if r["type"] == "stage":
                profile.stages.append(r["data"])
            elif r["type"] == "cycle":
                profile.cycles.append(r["data"])
            elif r["type"] == "lid_temp":
                profile.lid_temperature = r["temperature"]
            elif r["type"] == "profile_name":
                profile.name = r["name"]
            elif r["type"] == "profile_slot":
                profile.slot = r["slot"]
        return profile

    async def upload_profile(
        self,
        name: str,
        stages: list[tuple[float, int]],
        cycles: list[tuple[int, int, int]],
        lid_temp: float = 110.0,
        slot: int = 0,
    ) -> None:
        """Upload a PCR profile to the device.

        Args:
            name: Profile name
            stages: List of (temperature_celsius, duration_seconds)
            cycles: List of (from_stage, to_stage, num_cycles)
            lid_temp: Lid temperature in Celsius
            slot: Storage slot (0 = new)
        """
        # Begin profile upload
        await self._send("0\n0\npb")
        await asyncio.sleep(0.2)

        # Begin stages
        await self._send("w")
        await asyncio.sleep(0.1)

        # Send each stage
        for temp, duration in stages:
            await self._send_raw(encode_stage(temp, duration))
            await asyncio.sleep(0.1)

        # Send cycles
        for from_s, to_s, n_cycles in cycles:
            await self._send_raw(encode_cycle(from_s, to_s, n_cycles))
            await asyncio.sleep(0.1)

        # Set lid temperature
        await self._send_raw(encode_lid_temp(lid_temp))
        await asyncio.sleep(0.1)

        # Set name
        await self._send_raw(encode_profile_name(name))
        await asyncio.sleep(0.1)

        # Set slot and commit
        await self._send_raw(encode_profile_slot(slot))
        await asyncio.sleep(0.1)

        # Finalize
        await self._send("B")
        await self._collect_responses(timeout=3.0, expected_end="ack")

    async def start_run(self) -> None:
        """Start running the currently loaded PCR profile.

        Upload a profile first with upload_profile(), or load one with get_profile().
        """
        await self._send("pa")
        responses = await self._collect_responses(timeout=3.0, expected_end="ack")
        for r in responses:
            if r["type"] == "ack":
                return
        # Run may start without explicit ack

    async def poll_run_status(self) -> RunStatus:
        """Poll the current PCR run status."""
        await self._send("pe")
        responses = await self._collect_responses(timeout=3.0)
        for r in responses:
            if r["type"] == "run_status":
                return r["data"]
        raise TimeoutError("No run status response")

    async def stop_run(self) -> None:
        """Stop the currently running PCR program."""
        await self._send("pg")
        await self._collect_responses(timeout=3.0)

    def on_status(self, callback: Callable[[StatusBroadcast], Any]) -> None:
        """Register a callback for status broadcasts.

        Callback receives StatusBroadcast with block_temperature, lid_temperature, etc.
        Called every ~5 seconds while connected.
        """
        self._status_callbacks.append(callback)

    async def __aenter__(self) -> BentoLabBLE:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()
