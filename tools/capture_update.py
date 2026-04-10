#!/usr/bin/env python3
"""Capture a Bento Lab firmware update session.

Runs BLE scanning, BLE notification monitoring, and network traffic capture
simultaneously to catch the entire firmware update flow.

Usage:
    # Step 1: Find the device first
    python tools/capture_update.py --scan-only

    # Step 2: Capture everything during the update
    python tools/capture_update.py --device <address>

    # Step 3: If you know the device downloads firmware over the network,
    #         also capture HTTP traffic (needs the phone's IP)
    python tools/capture_update.py --device <address> --phone-ip 192.168.1.100
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from bleak import BleakClient, BleakError, BleakScanner
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import contextlib

from bentolab.protocol import lookup_uuid
from tools.session_logger import SessionLogger

console = Console()


async def scan_for_devices(duration: float, log: SessionLogger) -> list:
    """Scan for all BLE devices, highlight Bento-like ones."""
    console.print(f"[bold]Scanning for BLE devices ({duration}s)...[/bold]")
    discovered = await BleakScanner.discover(timeout=duration, return_adv=True)
    # Sort by RSSI (strongest first)
    items = sorted(discovered.values(), key=lambda da: da[1].rssi or -999, reverse=True)

    table = Table(title=f"BLE Devices ({len(items)} found)")
    table.add_column("#", width=4, style="dim")
    table.add_column("Name", style="cyan")
    table.add_column("Address", style="white")
    table.add_column("RSSI", justify="right", style="yellow")
    table.add_column("Services", style="dim")
    table.add_column("Manufacturer Data", style="dim")

    results = []
    for i, (dev, adv) in enumerate(items):
        name = dev.name or adv.local_name or "[unnamed]"
        mfr = ""
        if adv.manufacturer_data:
            for k, v in adv.manufacturer_data.items():
                mfr = f"0x{k:04X}: {bytes(v).hex()}"
        svc_uuids = ", ".join(s[:8] for s in adv.service_uuids) if adv.service_uuids else ""

        is_bento = "bento" in name.lower() or "gopcr" in name.lower()
        style = "bold green" if is_bento else ""
        table.add_row(
            str(i), name, dev.address, str(adv.rssi or "?"), svc_uuids, mfr, style=style
        )

        log.event(
            "ble_device",
            {
                "name": name,
                "address": dev.address,
                "rssi": adv.rssi,
                "service_uuids": adv.service_uuids,
                "manufacturer_data": mfr,
                "tx_power": adv.tx_power,
                "is_bento": is_bento,
            },
        )
        results.append((dev, adv))

    console.print(table)
    return results


async def monitor_device(address: str, log: SessionLogger) -> None:
    """Connect to device, enumerate GATT, and monitor ALL notifications."""
    console.print(f"\n[bold]Connecting to {address}...[/bold]")

    try:
        async with BleakClient(address) as client:
            console.print("[green]Connected![/green]")
            log.info(f"Connected to {address}")

            # Enumerate GATT
            console.print("\n[bold]GATT Profile:[/bold]")
            for service in client.services:
                svc_name = lookup_uuid(str(service.uuid))
                console.print(f"  [cyan]{service.uuid}[/cyan] — {svc_name}")
                log.event(
                    "gatt_service",
                    {
                        "uuid": str(service.uuid),
                        "name": svc_name,
                    },
                )

                for char in service.characteristics:
                    char_name = lookup_uuid(str(char.uuid))
                    props = ", ".join(char.properties)
                    console.print(f"    [white]{char.uuid}[/white] [{props}] — {char_name}")

                    # Try to read readable characteristics
                    if "read" in char.properties:
                        try:
                            data = await client.read_gatt_char(char)
                            hex_str = data.hex()
                            try:
                                text = data.decode("utf-8")
                            except UnicodeDecodeError:
                                text = None
                            console.print(
                                f"      Value: [green]{hex_str}[/green]"
                                + (f' = "{text}"' if text else "")
                            )
                            log.event(
                                "gatt_char_read",
                                {
                                    "uuid": str(char.uuid),
                                    "name": char_name,
                                    "properties": list(char.properties),
                                    "hex": hex_str,
                                    "text": text,
                                },
                            )
                        except (BleakError, TimeoutError) as e:
                            console.print(f"      [dim]Read failed: {e}[/dim]")
                            log.event(
                                "gatt_char_read_error",
                                {
                                    "uuid": str(char.uuid),
                                    "error": str(e),
                                },
                            )
                    else:
                        log.event(
                            "gatt_char",
                            {
                                "uuid": str(char.uuid),
                                "name": char_name,
                                "properties": list(char.properties),
                            },
                        )

            # Subscribe to ALL notifiable characteristics
            notifiable = []
            for service in client.services:
                for char in service.characteristics:
                    if "notify" in char.properties or "indicate" in char.properties:
                        notifiable.append(char)

            if notifiable:
                console.print(
                    f"\n[bold]Subscribing to {len(notifiable)} notification channels...[/bold]"
                )

                stop = asyncio.Event()
                loop = asyncio.get_event_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(sig, stop.set)

                def make_cb(char_uuid: str):
                    def callback(_sender, data: bytearray):
                        log.ble_notification(char_uuid, bytes(data))

                    return callback

                for char in notifiable:
                    uuid_str = str(char.uuid)
                    try:
                        await client.start_notify(char, make_cb(uuid_str))
                        console.print(f"  [green]+[/green] {uuid_str} ({lookup_uuid(uuid_str)})")
                    except BleakError as e:
                        console.print(f"  [red]x[/red] {uuid_str} — {e}")

                console.print(
                    "\n[bold green]Monitoring all notifications. "
                    "Proceed with the firmware update now.[/bold green]"
                )
                console.print("[dim]Press Ctrl+C when the update is complete.[/dim]\n")

                await stop.wait()

                for char in notifiable:
                    with contextlib.suppress(BleakError):
                        await client.stop_notify(char)
            else:
                console.print("[yellow]No notifiable characteristics found.[/yellow]")

    except BleakError as e:
        error_msg = str(e)
        if "CoreBluetooth" in error_msg or "authorization" in error_msg.lower():
            console.print(
                "[red]Bluetooth permission denied.[/red]\n"
                "Go to System Settings > Privacy & Security > Bluetooth\n"
                "and grant access to Terminal/iTerm."
            )
        else:
            console.print(f"[red]Connection failed: {e}[/red]")
        log.error(f"BLE error: {e}")


async def main():
    parser = argparse.ArgumentParser(description="Capture Bento Lab firmware update traffic")
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Just scan for BLE devices, don't connect",
    )
    parser.add_argument(
        "--device",
        help="BLE device address to connect to",
    )
    parser.add_argument(
        "--scan-time",
        type=float,
        default=15,
        help="BLE scan duration in seconds (default: 15)",
    )
    args = parser.parse_args()

    console.print(
        Panel(
            "[bold]Bento Lab Firmware Update Capture[/bold]\n"
            "This tool captures BLE traffic during a firmware update.\n"
            "All data is logged to captures/sessions/",
            border_style="blue",
        )
    )

    log = SessionLogger("firmware_update")

    if args.scan_only or not args.device:
        await scan_for_devices(args.scan_time, log)
        if not args.device:
            console.print(
                "\n[bold]Next step:[/bold] Re-run with --device <address> to connect and monitor."
            )
    else:
        # Quick scan first to verify device is present
        await scan_for_devices(5, log)
        await monitor_device(args.device, log)

    log.close()


if __name__ == "__main__":
    asyncio.run(main())
