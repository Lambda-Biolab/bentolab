"""``bentolab stop`` — abort the current PCR run."""

from __future__ import annotations

import asyncio

import typer

from ..ble_client import BentoLabBLE
from ._device import resolve_address
from ._format import fail, stdout


def stop_command(
    device: str | None = typer.Option(None, "--device", help="BLE address."),
) -> None:
    """Send the stop command to the device."""
    address = resolve_address(device)
    try:
        asyncio.run(_stop(address))
    except Exception as e:
        fail(f"stop failed: {e}")
    stdout.print("[green]Stop sent.[/green]")


async def _stop(address: str | None) -> None:
    lab = BentoLabBLE(address=address) if address else BentoLabBLE()
    async with lab:
        await lab.stop_run()
