"""``bentolab monitor`` — passive live tail of device status."""

from __future__ import annotations

import asyncio
import contextlib
import sys

import typer

from ..ble_client import BentoLabBLE, BentoLabCommandError
from ..protocol import StatusBroadcast
from ._device import resolve_address
from ._format import emit_json, fail, stdout


def monitor_command(
    device: str | None = typer.Option(None, "--device", help="BLE address."),
    duration: float = typer.Option(0.0, "--duration", help="Seconds (0 = until Ctrl+C)."),
    poll_interval: float = typer.Option(15.0, "--poll-interval", help="Seconds between pe polls."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON-per-line to stdout."),
) -> None:
    """Subscribe and print every status broadcast plus periodic run polls."""
    address = resolve_address(device)
    try:
        asyncio.run(_monitor(address, duration, poll_interval, json_output))
    except KeyboardInterrupt:
        pass
    except Exception as e:
        fail(f"monitor failed: {e}")


def _print_status(s: StatusBroadcast, json_output: bool) -> None:
    if json_output:
        emit_json(
            {
                "kind": "status",
                "running": s.running,
                "block": s.block_temperature,
                "lid": s.lid_temperature,
            }
        )
        sys.stdout.flush()
    else:
        stdout.print(
            f"status running={s.running} block={s.block_temperature}°C lid={s.lid_temperature}°C"
        )


def _print_run(rs, json_output: bool) -> None:
    if json_output:
        emit_json({"kind": "run", "running": rs.running, "progress": rs.progress})
        sys.stdout.flush()
    else:
        stdout.print(f"run    running={rs.running} progress={rs.progress}%")


async def _monitor(
    address: str | None, duration: float, poll_interval: float, json_output: bool
) -> None:
    lab = BentoLabBLE(address=address) if address else BentoLabBLE()
    async with lab:
        lab.on_status(lambda s: _print_status(s, json_output))
        deadline = asyncio.get_event_loop().time() + duration if duration > 0 else None
        while True:
            await asyncio.sleep(poll_interval)
            with contextlib.suppress(BentoLabCommandError):
                _print_run(await lab.poll_run_status(), json_output)
            if deadline is not None and asyncio.get_event_loop().time() >= deadline:
                break
