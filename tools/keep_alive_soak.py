"""5-minute hardware soak for PR #18 keep-alive validation.

Connects to the Bento Lab and maintains an active session for 5
minutes, polling /status every 10 s and recording any disconnect /
reconnect events. Reports whether the keep-alive (15 s ``Xa``
handshake) successfully held the BLE link throughout.

Exits 0 if the link was held for the full duration with no
unexpected disconnects, 1 otherwise. Designed to be readable as a
test artifact (``tools/keep_alive_soak.py``) and runnable as a
standalone validation.

Usage::

    uv run python tools/keep_alive_soak.py --device E5C3D166-7F1B-A6D9-6FD5-26B36FE5B6B2
    uv run python tools/keep_alive_soak.py --duration 300
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from bentolab.ble_client import BentoLabBLE, BentoLabConnectionError


def _ts() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S")


async def soak(device: str | None, duration_s: int, poll_interval_s: float) -> int:
    print(
        f"[{_ts()}] keep-alive soak start: duration={duration_s}s, "
        f"poll={poll_interval_s}s, device={device or 'auto'}"
    )
    start = time.monotonic()
    state = _SoakState()

    lab = BentoLabBLE()
    lab.on_disconnect(lambda: _on_disconnect(state, start))
    lab.on_status(lambda _s: None)  # notification path tracked separately; too noisy

    if not await _connect(lab, device):
        return 1
    print(f"[{_ts()}] connected. keep_alive_seconds={lab.keep_alive_seconds}")

    try:
        await _poll_loop(lab, state, start, duration_s, poll_interval_s)
    finally:
        await lab.disconnect()

    return _report(state, duration_s)


@dataclass
class _SoakState:
    """Counters + last-known status for the soak harness."""

    poll_successes: int = 0
    poll_failures: int = 0
    disconnects: list[float] = field(default_factory=list)
    reconnects: list[float] = field(default_factory=list)
    last_status_text: str = ""


def _on_disconnect(state: _SoakState, start: float) -> None:
    elapsed = time.monotonic() - start
    state.disconnects.append(elapsed)
    print(f"[{_ts()}] DISCONNECT detected at t={elapsed:.1f}s (keep-alive may have lost the link)")


async def _connect(lab: BentoLabBLE, device: str | None) -> bool:
    print(f"[{_ts()}] connecting...")
    try:
        await lab.connect(device)
    except BentoLabConnectionError as exc:
        print(f"[{_ts()}] FATAL: connect failed: {exc}")
        return False
    return True


async def _poll_loop(
    lab: BentoLabBLE,
    state: _SoakState,
    start: float,
    duration_s: int,
    poll_interval_s: float,
) -> None:
    deadline = start + duration_s
    next_poll = start
    while time.monotonic() < deadline:
        await _sleep_until_next_poll(next_poll, deadline)
        elapsed = time.monotonic() - start
        await _do_one_poll(lab, state, elapsed)
        next_poll = time.monotonic() + poll_interval_s
        if _just_disconnected(state, elapsed):
            await _try_reconnect(lab, state, start)


async def _sleep_until_next_poll(next_poll: float, deadline: float) -> None:
    sleep_for = max(0.0, min(next_poll - time.monotonic(), deadline - time.monotonic()))
    if sleep_for > 0:
        await asyncio.sleep(sleep_for)


async def _do_one_poll(lab: BentoLabBLE, state: _SoakState, elapsed: float) -> None:
    try:
        status = await lab.get_status()
    except Exception as exc:
        state.poll_failures += 1
        print(f"[{_ts()}] t={elapsed:>5.1f}s  poll_fail ({type(exc).__name__}: {exc})")
        state.disconnects.append(elapsed)
        return
    state.poll_successes += 1
    state.last_status_text = (
        f"running={int(status.running)} "
        f"block={status.block_temperature:.1f}°C "
        f"lid={status.lid_temperature:.1f}°C"
    )
    print(f"[{_ts()}] t={elapsed:>5.1f}s  poll_ok  {state.last_status_text}")


def _just_disconnected(state: _SoakState, elapsed: float) -> bool:
    return bool(state.disconnects) and state.disconnects[-1] >= elapsed - 1.0


async def _try_reconnect(lab: BentoLabBLE, state: _SoakState, start: float) -> None:
    print(f"[{_ts()}] attempting reconnect...")
    try:
        await lab.reconnect()
    except Exception as exc:
        print(f"[{_ts()}] reconnect failed: {exc}")
        await asyncio.sleep(2.0)
        return
    state.reconnects.append(time.monotonic() - start)
    print(f"[{_ts()}] reconnected at t={state.reconnects[-1]:.1f}s")


def _report(state: _SoakState, duration_s: int) -> int:
    print()
    print(f"[{_ts()}] soak complete: {duration_s}.0s elapsed")
    print(f"  poll_successes: {state.poll_successes}")
    print(f"  poll_failures:  {state.poll_failures}")
    print(f"  disconnects:    {len(state.disconnects)}")
    for t in state.disconnects:
        print(f"    - at t={t:.1f}s")
    print(f"  reconnects:     {len(state.reconnects)}")
    for t in state.reconnects:
        print(f"    - at t={t:.1f}s")
    print(f"  last_status:    {state.last_status_text}")
    print()

    # Pass criteria: no unexpected disconnects (the keep-alive should
    # prevent them), and the last poll was successful.
    unexpected = [t for t in state.disconnects if t < duration_s - 5]
    if unexpected or state.poll_failures > 0:
        print(
            f"FAIL: {len(unexpected)} unexpected disconnect(s), "
            f"{state.poll_failures} poll failure(s)"
        )
        return 1
    print("PASS: connection held for the full duration")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", help="BLE address (auto-discover if omitted)")
    parser.add_argument(
        "--duration", type=int, default=300, help="Soak duration in seconds (default 300)"
    )
    parser.add_argument(
        "--poll-interval", type=float, default=10.0, help="Status poll interval in seconds"
    )
    args = parser.parse_args()

    with contextlib.suppress(KeyboardInterrupt):
        return asyncio.run(soak(args.device, args.duration, args.poll_interval))
    print()
    print("[interrupted]")
    return 130


if __name__ == "__main__":
    sys.exit(main())
