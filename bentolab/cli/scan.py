"""``bentolab scan`` — discover BLE Bento Lab devices."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import typer

from .. import devices as device_registry
from ..ble_client import BentoLabBLE
from ._format import emit_json, fail, stdout


def scan_command(
    timeout: float = typer.Option(10.0, "--timeout", help="Seconds to scan."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
    no_remember: bool = typer.Option(False, "--no-remember", help="Do not update devices.json."),
) -> None:
    """Scan for nearby Bento Lab BLE devices."""
    try:
        results = asyncio.run(_scan(timeout))
    except Exception as e:
        fail(f"scan failed: {e}")

    if not no_remember:
        for entry in results:
            device_registry.remember(
                device_registry.Device(
                    address=entry["address"],
                    name=entry["name"],
                    transport="ble",
                    last_seen=datetime.now(tz=UTC).isoformat(),
                )
            )

    if json_output:
        emit_json(results)
        return

    if not results:
        stdout.print("[yellow]No Bento Lab devices found.[/yellow]")
        return
    stdout.print(f"[green]Found {len(results)} device(s):[/green]")
    for entry in results:
        rssi = f" rssi={entry['rssi']}" if entry["rssi"] is not None else ""
        stdout.print(f"  {entry['address']}  {entry['name']}{rssi}")


async def _scan(timeout: float) -> list[dict]:
    lab = BentoLabBLE()
    discovered = await lab.discover(timeout=timeout)
    out: list[dict] = []
    for dev, adv in discovered:
        out.append(
            {
                "address": dev.address,
                "name": dev.name or "",
                "rssi": getattr(adv, "rssi", None),
            }
        )
    return out
