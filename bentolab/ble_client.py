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
from .runs import RunLifecycle, RunState

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
        keep_alive_seconds: float = 15.0,
    ):
        self.address = address
        self.name_filter = re.compile(name_filter)
        self.auto_reconnect = auto_reconnect
        # Bento firmware appears to drop the BLE link after tens of
        # seconds of application-layer silence even though the device is
        # happily broadcasting status. Re-issuing the handshake on a
        # timer keeps the connection up. Set 0 to disable.
        self.keep_alive_seconds = keep_alive_seconds
        self._client: BleakClient | None = None
        self._rx_buffer: list[dict] = []
        self._rx_event = asyncio.Event()
        self._status_callbacks: list[Callable[[StatusBroadcast], Any]] = []
        self._disconnect_callbacks: list[Callable[[], Any]] = []
        self._reconnect_callbacks: list[Callable[[], Any]] = []
        self._last_status: StatusBroadcast | None = None
        self._connected_address: str | None = None
        self._keep_alive_task: asyncio.Task[None] | None = None
        # Background reconnect task -- started automatically when the
        # 95s firmware drop kicks us out (issue #55). The task tries
        # to reconnect with exponential backoff (1s, 2s, 4s, ...,
        # 30s cap) until it succeeds or is cancelled. Only one runs
        # at a time; a fresh drop replaces the in-flight task.
        self._reconnect_task: asyncio.Task[None] | None = None
        # Event loop reference, captured at connect() time. Bleak's
        # ``disconnected_callback`` runs on its own loop/thread, so
        # we use ``run_coroutine_threadsafe`` from that thread to
        # schedule the reconnect task back on our asyncio loop.
        self._loop: asyncio.AbstractEventLoop | None = None

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
        """Handle unexpected BLE disconnection.

        Fires the disconnect callbacks (so SSE consumers can emit a
        ``disconnected`` event + retry hint) and, if ``auto_reconnect``
        is set, schedules a background reconnect task so a long-running
        server recovers without operator intervention.
        """
        logger.warning("BLE connection lost; will auto-reconnect to %s", self._connected_address)
        self._client = None
        if self._keep_alive_task is not None:
            self._keep_alive_task.cancel()
            self._keep_alive_task = None
        for cb in self._disconnect_callbacks:
            try:
                cb()
            except Exception:
                logger.exception("Disconnect callback error")

        # Schedule the background reconnect. Bleak's disconnect callback
        # is bridged to our asyncio loop by ``call_soon_threadsafe``
        # (CoreBluetooth) or fires synchronously on the loop (BlueZ),
        # so we're already on the right thread. Use ``create_task``
        # for a real ``asyncio.Task`` we can cancel cleanly.
        if not self.auto_reconnect:
            return
        if not self._connected_address or self._loop is None:
            logger.debug(
                "Skipping auto-reconnect: _connected_address=%s, _loop=%s",
                self._connected_address,
                self._loop,
            )
            return
        # Replace any in-flight reconnect so we don't double up.
        if self._reconnect_task is not None and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        try:
            self._reconnect_task = self._loop.create_task(
                self._background_reconnect(),
                name="bentolab-auto-reconnect",
            )
            logger.info(
                "Auto-reconnect scheduled after BLE drop (target=%s, attempt-budget=10)",
                self._connected_address,
            )
        except RuntimeError as exc:
            # Loop is closed (server shutting down). Nothing to do.
            logger.debug("loop closed; cannot schedule reconnect: %s", exc)

    # ------------------------------------------------------------------
    # Low-level send/receive
    # ------------------------------------------------------------------

    def _require_client(self) -> BleakClient:
        """Return the live BleakClient or raise if we're not connected."""
        if not self._client or not self._client.is_connected:
            raise BentoLabConnectionError("Not connected to Bento Lab")
        return self._client

    async def _send(self, cmd: str) -> None:
        """Send a command to the device via NUS RX (fire-and-forget).

        Uses ``response=False`` (BLE write command, no ACK). The
        device's response, if any, arrives asynchronously via the
        NUS TX notification path; see :meth:`_collect_responses` for
        callers that need to wait for it.
        """
        client = self._require_client()
        data = encode_command(cmd)
        try:
            await client.write_gatt_char(NUS_RX_CHAR_UUID, data, response=False)
        except BleakError as e:
            raise BentoLabConnectionError(f"Write failed: {e}") from e

    async def _send_with_gatt_response(self, cmd: str) -> None:
        """Send a command and wait for the GATT-layer write response.

        Uses ``response=True`` (BLE write request, expects an ACK at
        the GATT protocol layer). This is slower than :meth:`_send`
        (one extra round-trip on the air) but some firmware treats
        the ACK as link activity and resets the link supervision
        timer. If the link-layer inactivity timeout is the cause
        of the t≈95s disconnect (#55), this variant may keep the
        link alive where the bare write command cannot.

        Application-level responses still arrive on NUS TX; this
        method only blocks on the GATT ACK, not on the device's
        app-level reply.
        """
        client = self._require_client()
        data = encode_command(cmd)
        try:
            await client.write_gatt_char(NUS_RX_CHAR_UUID, data, response=True)
        except BleakError as e:
            raise BentoLabConnectionError(f"Write with response failed: {e}") from e

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
        if not target:
            devices = await self.discover()
            if not devices:
                raise BentoLabConnectionError("No Bento Lab device found")
            target = devices[0][0].address
            logger.info("Auto-discovered: %s", devices[0][0].name)

        try:
            self._client = BleakClient(target, disconnected_callback=self._on_disconnect)
            await self._client.connect()
            await self._client.start_notify(NUS_TX_CHAR_UUID, self._on_notify)
            self._connected_address = target
            # Capture the loop reference while we're on the asyncio
            # thread that owns the BleakClient. bleak's
            # ``disconnected_callback`` will fire from a different
            # thread; the captured loop is what we need to schedule
            # the background reconnect task.
            self._loop = asyncio.get_running_loop()
            logger.info("Connected to %s", target)
        except BleakError as e:
            self._client = None
            raise BentoLabConnectionError(f"Connection failed: {e}") from e

        # Send handshake and wait for first status
        await self._send("Xa")
        await asyncio.sleep(0.5)
        self._start_keep_alive()

    def _start_keep_alive(self) -> None:
        if self.keep_alive_seconds <= 0:
            return
        if self._keep_alive_task is not None and not self._keep_alive_task.done():
            return
        self._keep_alive_task = asyncio.create_task(
            self._keep_alive_loop(), name="bentolab-keep-alive"
        )

    async def _keep_alive_loop(self) -> None:
        """Periodically poke the device so the firmware keeps the link.

        Sends ``Xa`` (handshake / device-info request) on a fixed
        cadence using the GATT write-with-response variant
        (:meth:`_send_with_gatt_response`). The GATT-layer ACK is
        the actual link activity some firmware uses to reset the
        link supervision timer -- a bare write command
        (``_send``) was insufficient and the link still dropped at
        ~95s (see issue #55).

        If the write fails the connection is already gone, so we
        just exit and let the disconnect callback fire.
        """
        try:
            while True:
                await asyncio.sleep(self.keep_alive_seconds)
                if not self.is_connected:
                    return
                try:
                    await self._send_with_gatt_response("Xa")
                except BentoLabConnectionError:
                    return
        except asyncio.CancelledError:
            return

    async def reconnect(self) -> None:
        """Reconnect to the last known device."""
        if not self._connected_address:
            raise BentoLabConnectionError("No previous connection to reconnect to")
        logger.info("Reconnecting to %s...", self._connected_address)
        await self.connect(self._connected_address)

    async def _background_reconnect(self) -> None:
        """Auto-reconnect loop with exponential backoff.

        Runs in the background after an unexpected disconnect. Tries
        :meth:`reconnect` with delays of 5s, 10s, 20s, 30s (capped).
        Gives up after 10 consecutive failures (operator intervention
        required). On success, fires the ``_reconnect_callbacks`` so
        SSE consumers can publish a ``reconnected`` event.

        Why start at 5s: on macOS CoreBluetooth the OS needs a beat
        to release the previous connection slot after a drop. A
        retry inside that window can hang the connect call. 5s is
        enough to let the slot free in practice.
        """
        if not self._connected_address:
            return
        logger.info(
            "Auto-reconnect starting for %s (10-attempt budget, 5s-30s backoff)",
            self._connected_address,
        )
        backoff = 5.0
        max_backoff = 30.0
        max_attempts = 10
        address = self._connected_address
        for attempt in range(1, max_attempts + 1):
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return
            if not self._connected_address:
                # Operator manually disconnected mid-reconnect; stop.
                return
            if await self._try_reconnect_once(attempt, max_attempts, address):
                return
            backoff = min(backoff * 2, max_backoff)
        logger.error(
            "Auto-reconnect gave up after %d attempts to %s; operator intervention required",
            max_attempts,
            address,
        )
        logger.error(
            "Auto-reconnect gave up after %d attempts to %s; operator intervention required",
            max_attempts,
            address,
        )

    async def _try_reconnect_once(self, attempt: int, max_attempts: int, address: str) -> bool:
        """One iteration of the reconnect loop. Returns True on success.

        Wraps :meth:`connect` in a 20s timeout. The Bento Lab drops
        the link every ~95s (issue #55). If the macOS CoreBluetooth
        stack is still holding the previous connection slot, the
        timeout kicks us out and the next backoff iteration tries
        again. On Linux (BlueZ) the timeout is rarely needed but
        harmless.
        """
        try:
            await asyncio.wait_for(self.connect(address), timeout=20.0)
        except TimeoutError:
            logger.warning(
                "Auto-reconnect attempt %d/%d to %s timed out after 20s",
                attempt,
                max_attempts,
                address,
            )
            return False
        except BentoLabConnectionError as exc:
            logger.warning(
                "Auto-reconnect attempt %d/%d to %s failed: %s",
                attempt,
                max_attempts,
                address,
                exc,
            )
            return False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Auto-reconnect attempt %d/%d to %s raised %s: %s",
                attempt,
                max_attempts,
                address,
                type(exc).__name__,
                exc,
            )
            return False
        logger.info("Auto-reconnect succeeded to %s", address)
        self._fire_reconnect_callbacks()
        return True

    def _fire_reconnect_callbacks(self) -> None:
        """Invoke all registered reconnect callbacks. Errors are logged."""
        for cb in self._reconnect_callbacks:
            try:
                cb()
            except Exception:
                logger.exception("Reconnect callback error")

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        # Operator-initiated disconnect: stop any in-flight auto-reconnect
        # and clear the address so a fresh drop can't sneak back in.
        if self._reconnect_task is not None:
            # concurrent.futures.Future.cancel() is synchronous; the
            # coroutine running on the asyncio loop will see CancelledError
            # on its next checkpoint. We don't await the future here
            # because it isn't directly awaitable in this form; the
            # background task checks for the cancel via its own sleep().
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self._keep_alive_task is not None:
            self._keep_alive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._keep_alive_task
            self._keep_alive_task = None
        if self._client and self._client.is_connected:
            with contextlib.suppress(BleakError):
                await self._client.stop_notify(NUS_TX_CHAR_UUID)
            await self._client.disconnect()
        self._client = None
        self._loop = None
        # Note: we intentionally do NOT clear ``_connected_address``
        # here. ``reconnect()`` (and the background reconnect loop)
        # use it as the target. ``disconnect()`` is paired with the
        # operator intending to be done; clearing the address would
        # also break callers that explicitly reconnect later.
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

    def off_status(self, callback: Callable[[StatusBroadcast], Any]) -> None:
        """Remove a previously-registered status callback. No-op if not present."""
        with contextlib.suppress(ValueError):
            self._status_callbacks.remove(callback)

    def on_disconnect(self, callback: Callable[[], Any]) -> None:
        """Register a callback for unexpected disconnections."""
        self._disconnect_callbacks.append(callback)

    def off_disconnect(self, callback: Callable[[], Any]) -> None:
        """Remove a previously-registered disconnect callback. No-op if absent."""
        with contextlib.suppress(ValueError):
            self._disconnect_callbacks.remove(callback)

    def on_reconnect(self, callback: Callable[[], Any]) -> None:
        """Register a callback fired after a successful auto-reconnect.

        The callback is invoked from the background reconnect task as
        soon as :meth:`connect` returns successfully. Useful for SSE
        consumers that want to broadcast a ``reconnected`` event so
        long-lived clients can resume the telemetry stream.
        """
        self._reconnect_callbacks.append(callback)

    def off_reconnect(self, callback: Callable[[], Any]) -> None:
        """Remove a previously-registered reconnect callback. No-op if absent."""
        with contextlib.suppress(ValueError):
            self._reconnect_callbacks.remove(callback)

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
        profile: PCRProfile,
        lid_temp: float | None = None,
    ) -> None:
        """Start a PCR run from a high-level :class:`PCRProfile`.

        This is the public API used by the HTTP service layer. Flattens
        the profile into stages/cycles and delegates to the low-level
        :meth:`_start_pcr_program`. The protocol side-effect is to send
        ``pa`` and wait for a ``run_status`` response.

        Args:
            profile: A validated PCR profile.
            lid_temp: Optional lid temperature override; defaults to
                ``profile.lid_temperature``.
        """
        stages, cycles = profile.to_stages_and_cycles()
        effective_lid = lid_temp if lid_temp is not None else profile.lid_temperature
        await self._start_pcr_program(
            name=profile.name,
            stages=stages,
            cycles=cycles,
            lid_temp=effective_lid,
            slot=0,
        )

    async def _start_pcr_program(
        self,
        name: str = "Python Run",
        stages: list[tuple[float, int]] | None = None,
        cycles: list[tuple[int, int, int]] | None = None,
        lid_temp: float = 110.0,
        slot: int = 0,
    ) -> None:
        """Low-level: start a PCR run with explicit stages/cycles.

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

    async def get_run_status(self) -> RunState:
        """Return a typed snapshot of the current run for the API.

        Combines :meth:`poll_run_status` (progress + running flag) with
        :meth:`get_status` (block + lid temperatures) into a
        :class:`~bentolab.runs.RunState`. ``elapsed_seconds`` is
        reported as 0.0 since the device doesn't surface an elapsed-time
        field; the API layer tracks that on its own.
        """
        rs = await self.poll_run_status()
        sb = await self.get_status()
        return RunState(
            state=RunLifecycle.RUNNING if rs.running else RunLifecycle.IDLE,
            progress=int(rs.progress),
            block_temperature=float(sb.block_temperature),
            lid_temperature=float(sb.lid_temperature),
            elapsed_seconds=0.0,
        )

    async def abort_run(self) -> None:
        """Abort the current run. Adapter alias for :meth:`stop_run`."""
        await self.stop_run()

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
    ) -> AsyncIterator[RunState]:
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
    ) -> AsyncIterator[RunState]:
        """Run a PCR program and yield status updates until completion.

        This is the high-level API for running PCR with progress tracking.
        Yields :class:`~bentolab.runs.RunState` objects at each poll interval
        until the run completes or is stopped.

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
        await self._start_pcr_program(name=name, stages=stages, cycles=cycles, lid_temp=lid_temp)

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

            state = RunState(
                state=RunLifecycle.RUNNING if running else RunLifecycle.IDLE,
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
