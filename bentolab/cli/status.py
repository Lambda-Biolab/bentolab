"""``bentolab status`` — print one device-status snapshot."""

from __future__ import annotations

import asyncio

import typer

from ..ble_client import BentoLabBLE
from ..protocol import StatusBroadcast
from ._device import resolve_address
from ._format import emit_json, fail, stdout


def status_command(
    device: str | None = typer.Option(None, "--device", help="BLE address."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
) -> None:
    """Connect, print one status snapshot, and exit."""
    address = resolve_address(device)
    try:
        snapshot = asyncio.run(_snapshot(address))
    except Exception as e:
        fail(f"status failed: {e}")

    if json_output:
        emit_json(snapshot)
        return

    stdout.print(
        f"[bold]{snapshot['address']}[/bold]  "
        f"running={snapshot['running']}  "
        f"block={snapshot['block_temperature']}°C  "
        f"lid={snapshot['lid_temperature']}°C"
    )


async def _snapshot(address: str | None) -> dict:
    lab = BentoLabBLE(address=address) if address else BentoLabBLE()
    async with lab:
        s: StatusBroadcast = await lab.get_status()
        return {
            "address": getattr(lab, "_connected_address", None) or address or "",
            "running": s.running,
            "block_temperature": s.block_temperature,
            "lid_temperature": s.lid_temperature,
        }
