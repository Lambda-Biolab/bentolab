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

        # Run PCR with progress tracking
        async for state in lab.run_pcr(
            stages=[(95, 180), (95, 30), (58, 30), (72, 60), (72, 300)],
            cycles=[(4, 2, 35)],
        ):
            print(f"  {state.block_temperature}°C, progress={state.progress}")
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from .models import PCRProfile
from .protocol import (
    BENTO_ADV_SERVICE_UUID,
    NUS_RX_CHAR_UUID,
    NUS_TX_CHAR_UUID,
    CycleData,
    ProfileEntry,
    RunStatus,
    StageData,
    StatusBroadcast,
    TouchdownStageData,
    decode_response,
    encode_command,
    encode_cycle,
    encode_lid_temp,
    encode_profile_name,
    encode_profile_slot,
    encode_stage,
)

logger = logging.getLogger(__name__)


class BentoLabError(Exception):
    """Base exception for Bento Lab errors."""


class BentoLabConnectionError(BentoLabError):
    """Raised when BLE connection fails or is lost."""


class BentoLabCommandError(BentoLabError):
    """Raised when a command fails or times out."""


@dataclass
class ProfileData:
    """Complete PCR profile data retrieved from the device."""

    name: str = ""
    slot: int = 0
    stages: list[StageData | TouchdownStageData] = field(default_factory=list)
    cycles: list[CycleData] = field(default_factory=list)
    lid_temperature: float = 0.0


@dataclass
class PCRRunState:
    """Snapshot of a running PCR program's state."""

    running: bool = False
    progress: int = 0
    block_temperature: float = 0.0
    lid_temperature: float = 0.0
    elapsed_seconds: float = 0.0


class BentoLabBLE:
    """Async BLE client for controlling a Bento Lab Pro.

    Communicates via Nordic UART Service using the decoded text protocol.
    Handles connection management, error recovery, and status monitoring.
    """

    def __init__(
        self,
        address: str | None = None,
        name_filter: str = r"(?i)bento",
        auto_reconnect: bool = True,
    ):
        self.address = address
        self.name_filter = re.compile(name_filter)
        self.auto_reconnect = auto_reconnect
        self._client: BleakClient | None = None
        self._rx_buffer: list[dict] = []
        self._rx_event = asyncio.Event()
        self._status_callbacks: list[Callable[[StatusBroadcast], Any]] = []
        self._disconnect_callbacks: list[Callable[[], Any]] = []
        self._last_status: StatusBroadcast | None = None
        self._connected_address: str | None = None

    # ------------------------------------------------------------------
    # Notification handler
    # ------------------------------------------------------------------

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        """Handle NUS TX notifications from the device."""
        try:
            parsed = decode_response(bytes(data))
        except Exception:
            logger.warning("Failed to decode notification: %s", data.hex())
            return

        if parsed["type"] == "status":
            status: StatusBroadcast = parsed["data"]
            self._last_status = status
            for cb in self._status_callbacks:
                try:
                    cb(status)
                except Exception:
                    logger.exception("Status callback error")
        elif parsed["type"] != "continuation":
            self._rx_buffer.append(parsed)
            self._rx_event.set()

    def _on_disconnect(self, _client: Any) -> None:
        """Handle unexpected BLE disconnection."""
        logger.warning("BLE connection lost")
        self._client = None
        for cb in self._disconnect_callbacks:
            try:
                cb()
            except Exception:
                logger.exception("Disconnect callback error")

    # ------------------------------------------------------------------
    # Low-level send/receive
    # ------------------------------------------------------------------

    def _require_client(self) -> BleakClient:
        """Return the live BleakClient or raise if we're not connected."""
        if not self._client or not self._client.is_connected:
            raise BentoLabConnectionError("Not connected to Bento Lab")
        return self._client

    def _check_connected(self) -> None:
        self._require_client()

    async def _send(self, cmd: str) -> None:
        """Send a command to the device via NUS RX."""
        client = self._require_client()
        data = encode_command(cmd)
        try:
            await client.write_gatt_char(NUS_RX_CHAR_UUID, data, response=False)
        except BleakError as e:
            raise BentoLabConnectionError(f"Write failed: {e}") from e

    async def _send_raw(self, data: bytes) -> None:
        """Send raw bytes to NUS RX."""
        client = self._require_client()
        try:
            await client.write_gatt_char(NUS_RX_CHAR_UUID, data, response=False)
        except BleakError as e:
            raise BentoLabConnectionError(f"Write failed: {e}") from e

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

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

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
        """Connect to a Bento Lab device.

        Args:
            address: BLE address. If None, auto-discovers the first device.

        Raises:
            BentoLabConnectionError: If connection fails.
        """
        target = address or self.address
        ble_target = await self._resolve_target(target)

        try:
            self._client = BleakClient(ble_target, disconnected_callback=self._on_disconnect)
            await self._client.connect()
            await self._client.start_notify(NUS_TX_CHAR_UUID, self._on_notify)
            resolved_address = (
                getattr(ble_target, "address", None)
                if not isinstance(ble_target, str)
                else ble_target
            )
            self._connected_address = resolved_address or target
            logger.info("Connected to %s", self._connected_address)
        except BleakError as e:
            self._client = None
            raise BentoLabConnectionError(f"Connection failed: {e}") from e

        # Send handshake and wait for first status
        await self._send("Xa")
        await asyncio.sleep(0.5)

    async def _resolve_target(self, target: str | None) -> Any:
        """Refresh the BLEDevice handle so CoreBluetooth has a live entry.

        macOS's BLE stack rotates the random address it exposes for
        unidentified peripherals. Passing a stale address straight to
        ``BleakClient`` fails with "device with address ... was not
        found", even though the device is advertising. The canonical
        bleak fix is to look the address up with :func:`BleakScanner.
        find_device_by_address` (or do a broader scan) before connecting.
        """
        if target:
            try:
                device = await BleakScanner.find_device_by_address(target, timeout=10.0)
            except BleakError:
                device = None
            if device is not None:
                return device
            logger.info("Address %s not advertising; rescanning for any Bento...", target)

        results = await self.discover()
        if not results:
            suffix = f" (looked for {target})" if target else ""
            raise BentoLabConnectionError(f"No Bento Lab device found{suffix}")
        device = results[0][0]
        logger.info("Resolved to %s (%s)", device.name, device.address)
        return device

    async def reconnect(self) -> None:
        """Reconnect to the last known device."""
        if not self._connected_address:
            raise BentoLabConnectionError("No previous connection to reconnect to")
        logger.info("Reconnecting to %s...", self._connected_address)
        await self.connect(self._connected_address)

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        if self._client and self._client.is_connected:
            with contextlib.suppress(BleakError):
                await self._client.stop_notify(NUS_TX_CHAR_UUID)
            await self._client.disconnect()
        self._client = None
        logger.info("Disconnected")

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    # ------------------------------------------------------------------
    # Status and monitoring
    # ------------------------------------------------------------------

    async def get_status(self) -> StatusBroadcast:
        """Get the current device status.

        Returns cached status if available, otherwise waits for the next
        broadcast (sent every ~5 seconds).
        """
        if self._last_status:
            return self._last_status

        event = asyncio.Event()
        old_status = self._last_status

        def on_status(s: StatusBroadcast) -> None:
            if s is not old_status:
                event.set()

        self._status_callbacks.append(on_status)
        try:
            await asyncio.wait_for(event.wait(), timeout=10.0)
        except TimeoutError as e:
            raise BentoLabCommandError("No status broadcast received") from e
        finally:
            self._status_callbacks.remove(on_status)

        return self._last_status  # type: ignore[return-value]

    def on_status(self, callback: Callable[[StatusBroadcast], Any]) -> None:
        """Register a callback for status broadcasts (~5s interval)."""
        self._status_callbacks.append(callback)

    def on_disconnect(self, callback: Callable[[], Any]) -> None:
        """Register a callback for unexpected disconnections."""
        self._disconnect_callbacks.append(callback)

    # ------------------------------------------------------------------
    # Profile management
    # ------------------------------------------------------------------

    async def list_profiles(self) -> list[ProfileEntry]:
        """List all PCR profiles stored on the device."""
        await self._send("p")
        responses = await self._collect_responses(timeout=5.0, expected_end="profile_end")
        return [r["data"] for r in responses if r["type"] == "profile_entry"]

    async def get_profile(self, slot: int) -> ProfileData:
        """Retrieve a complete PCR profile from the device by slot ID."""
        await self._send(f"{slot}\npc")
        responses = await self._collect_responses(timeout=5.0)

        profile = ProfileData(slot=slot)
        for r in responses:
            if r["type"] in ("stage", "touchdown_stage"):
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
        """Upload a PCR profile to the device's storage.

        Args:
            name: Profile name.
            stages: List of (temperature_celsius, duration_seconds).
            cycles: List of (from_stage, to_stage, num_cycles).
            lid_temp: Lid temperature in Celsius.
            slot: Storage slot (0 = new).
        """
        await self._send("0\n0\npb")
        await asyncio.sleep(0.2)
        await self._send_stages(stages, cycles, lid_temp, name, slot)
        await self._send("B")
        await self._collect_responses(timeout=3.0, expected_end="ack")
        logger.info("Uploaded profile '%s' to slot %d", name, slot)

    # ------------------------------------------------------------------
    # PCR run control
    # ------------------------------------------------------------------

    async def start_run(
        self,
        name: str = "Python Run",
        stages: list[tuple[float, int]] | None = None,
        cycles: list[tuple[int, int, int]] | None = None,
        lid_temp: float = 110.0,
        slot: int = 0,
    ) -> None:
        """Start a PCR run.

        Sends the profile inline with the start command, matching the
        device's protocol.

        Args:
            name: Profile name.
            stages: List of (temperature_celsius, duration_seconds).
            cycles: List of (from_stage, to_stage, num_cycles).
            lid_temp: Lid temperature in Celsius.
            slot: Profile slot ID.
        """
        await self._send("pa")
        await asyncio.sleep(0.1)

        if stages:
            await self._send_stages(stages, cycles or [], lid_temp, name, slot)

        await self._collect_responses(timeout=5.0, expected_end="run_status")
        logger.info("PCR run started: %s", name)

    async def poll_run_status(self) -> RunStatus:
        """Poll the current PCR run status.

        Returns:
            RunStatus with running, checksum, and progress fields.

        Raises:
            BentoLabCommandError: If no response received.
        """
        await self._send("pe")
        responses = await self._collect_responses(timeout=3.0)
        for r in responses:
            if r["type"] == "run_status":
                return r["data"]
        raise BentoLabCommandError("No run status response")

    async def stop_run(self) -> None:
        """Stop the currently running PCR program."""
        await self._send("pg")
        await self._collect_responses(timeout=3.0)
        logger.info("PCR run stopped")

    def run_profile(
        self,
        profile: PCRProfile,
        lid_temp: float = 110.0,
        poll_interval: float = 5.0,
    ) -> AsyncIterator[PCRRunState]:
        """Run a :class:`PCRProfile` and yield live status updates.

        Convenience wrapper around :meth:`run_pcr` that accepts a
        high-level :class:`PCRProfile` and flattens it into the stage/cycle
        tuples the device protocol expects.

        Usage::

            async with BentoLabBLE() as lab:
                profile = PCRProfile.simple(num_cycles=30)
                async for state in lab.run_profile(profile):
                    print(f"{state.block_temperature}C  progress={state.progress}")
        """
        stages, cycles = profile.to_stages_and_cycles()
        return self.run_pcr(
            name=profile.name,
            stages=stages,
            cycles=cycles,
            lid_temp=lid_temp,
            poll_interval=poll_interval,
        )

    async def run_pcr(
        self,
        name: str = "Python Run",
        stages: list[tuple[float, int]] | None = None,
        cycles: list[tuple[int, int, int]] | None = None,
        lid_temp: float = 110.0,
        poll_interval: float = 5.0,
        startup_grace_seconds: float = 120.0,
        completion_confirmations: int = 3,
    ) -> AsyncIterator[PCRRunState]:
        """Run a PCR program and yield status updates until completion.

        This is the high-level API for running PCR with progress tracking.
        Yields PCRRunState objects at each poll interval until the run
        completes or is stopped.

        Termination requires *either* progress >= 99% OR
        ``completion_confirmations`` consecutive ``running=False`` polls
        after ``startup_grace_seconds`` has elapsed. This avoids exiting
        on transient ``running=False`` flips that the device emits during
        the lid-heat / pre-cycle ramp before stage 1 reaches setpoint.

        Usage::

            async for state in lab.run_pcr(
                stages=[(95, 180), (95, 30), (58, 30), (72, 60), (72, 300)],
                cycles=[(4, 2, 35)],
            ):
                print(f"Block: {state.block_temperature}°C, progress: {state.progress}")

        Args:
            name: Profile name.
            stages: List of (temperature_celsius, duration_seconds).
            cycles: List of (from_stage, to_stage, num_cycles).
            lid_temp: Lid temperature in Celsius.
            poll_interval: Seconds between status polls.
            startup_grace_seconds: Ignore ``running=False`` for this long
                after starting, to ride out the lid-heat ramp.
            completion_confirmations: Number of consecutive ``running=False``
                polls (after the grace period) required to declare done.
        """
        await self.start_run(name=name, stages=stages, cycles=cycles, lid_temp=lid_temp)

        elapsed = 0.0
        consecutive_not_running = 0
        peak_progress = 0
        while True:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            status = await self.get_status()
            try:
                run_status = await self.poll_run_status()
                running = run_status.running
                progress = run_status.progress
            except BentoLabCommandError:
                running = bool(status.running)
                progress = 0

            peak_progress = max(peak_progress, progress)

            state = PCRRunState(
                running=running,
                progress=progress,
                block_temperature=float(status.block_temperature),
                lid_temperature=float(status.lid_temperature),
                elapsed_seconds=elapsed,
            )
            yield state

            if running:
                consecutive_not_running = 0
                continue

            consecutive_not_running += 1

            if peak_progress >= 99:
                logger.info("PCR run completed after %.0fs (progress=%d)", elapsed, progress)
                break
            if (
                elapsed >= startup_grace_seconds
                and consecutive_not_running >= completion_confirmations
            ):
                logger.info(
                    "PCR run completed after %.0fs (%d consecutive idle polls)",
                    elapsed,
                    consecutive_not_running,
                )
                break

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _send_stages(
        self,
        stages: list[tuple[float, int]],
        cycles: list[tuple[int, int, int]],
        lid_temp: float,
        name: str,
        slot: int,
    ) -> None:
        """Send the stages/cycles/lid/name/slot sequence."""
        await self._send("w")
        await asyncio.sleep(0.1)

        for temp, duration in stages:
            await self._send_raw(encode_stage(temp, duration))
            await asyncio.sleep(0.05)

        for from_s, to_s, n_cycles in cycles:
            await self._send_raw(encode_cycle(from_s, to_s, n_cycles))
            await asyncio.sleep(0.05)

        await self._send_raw(encode_lid_temp(lid_temp))
        await asyncio.sleep(0.05)
        await self._send_raw(encode_profile_name(name))
        await asyncio.sleep(0.05)
        await self._send_raw(encode_profile_slot(slot))
        await asyncio.sleep(0.1)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> BentoLabBLE:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()
