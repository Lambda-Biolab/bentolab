#!/usr/bin/env python3
"""Passive BLE notification monitor for Bento Lab.

Connects to a BLE device and subscribes to ALL notifiable characteristics,
logging every notification with timestamps. Designed to run in a separate
terminal while using the Android app, to correlate app actions with BLE traffic.

Usage:
    python tools/ble_monitor.py --device <address>
    python tools/ble_monitor.py --device <address> --duration 120 --live
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

from bleak import BleakClient, BleakError
from rich.console import Console
from rich.live import Live
from rich.table import Table

# Allow importing from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import contextlib

from bentolab.protocol import lookup_uuid

console = Console()


def make_summary_table(
    stats: dict[str, dict],
) -> Table:
    """Build a live-updating summary table."""
    table = Table(title="BLE Notification Monitor", expand=True)
    table.add_column("UUID", style="cyan", no_wrap=True)
    table.add_column("Name", style="green")
    table.add_column("Count", style="yellow", justify="right")
    table.add_column("Last Value (hex)", style="white")
    table.add_column("Last ASCII", style="dim")
    table.add_column("Last Seen", style="dim")

    for uuid, info in sorted(stats.items()):
        ascii_attempt = ""
        if info["last_bytes"]:
            try:
                ascii_attempt = info["last_bytes"].decode("ascii", errors="replace")
                ascii_attempt = "".join(c if c.isprintable() else "." for c in ascii_attempt)
            except Exception:
                ascii_attempt = ""

        table.add_row(
            uuid[:23] + "..." if len(uuid) > 23 else uuid,
            info["name"],
            str(info["count"]),
            info["last_hex"],
            ascii_attempt[:20],
            info["last_time"],
        )

    return table


async def monitor(
    address: str,
    duration: int,
    output_dir: Path,
    live_display: bool,
) -> None:
    """Connect and monitor all notifiable characteristics."""
    console.print(f"[bold]Connecting to {address}...[/bold]")

    try:
        async with _connect_to_device(address) as client:
            notifiable = _collect_notifiable_chars(client)
            if not notifiable:
                console.print("[yellow]No notifiable characteristics found.[/yellow]")
                return

            console.print(
                f"[bold]Found {len(notifiable)} notifiable characteristics. Subscribing...[/bold]"
            )

            event_log: list[dict] = []
            stats = _init_stats(notifiable)
            await _subscribe_all(client, notifiable, stats, event_log, live_display)

            stop_event = _install_signal_handlers()
            console.print(
                f"\n[bold green]Monitoring... "
                f"{'(Ctrl+C to stop)' if duration == 0 else f'for {duration}s'}[/bold green]\n"
            )

            await _wait_for_stop(stop_event, duration, stats, live_display)
            await _unsubscribe_all(client, notifiable)

            _save_results(address, event_log, notifiable, stats, output_dir)
            console.print()
            console.print(make_summary_table(stats))

    except BleakError as e:
        _handle_ble_error(e)
        sys.exit(1)


def _collect_notifiable_chars(client) -> list:
    """Return all characteristics across services that support notify/indicate."""
    notifiable: list = []
    for service in client.services:
        for char in service.characteristics:
            if "notify" in char.properties or "indicate" in char.properties:
                notifiable.append(char)
    return notifiable


def _connect_to_device(address: str):
    """Return an async-context-manager that connects to ``address``."""
    return BleakClient(address)


def _init_stats(notifiable: list) -> dict[str, dict]:
    """Pre-seed the per-characteristic stats dict with the lookup name."""
    stats: dict[str, dict] = {}
    for char in notifiable:
        uuid_str = str(char.uuid)
        stats[uuid_str] = {
            "name": lookup_uuid(uuid_str),
            "count": 0,
            "last_hex": "",
            "last_bytes": None,
            "last_time": "",
        }
    return stats


def _make_monitor_callback(char_uuid: str, stats: dict, event_log: list[dict], live_display: bool):
    """Build a notification callback that records stats and (optionally) prints."""

    def callback(_sender, data: bytearray) -> None:
        now = datetime.now(tz=UTC)
        hex_str = data.hex()
        event_log.append(
            {
                "timestamp": now.isoformat(),
                "uuid": char_uuid,
                "name": stats[char_uuid]["name"],
                "hex": hex_str,
                "bytes": list(data),
                "length": len(data),
            }
        )
        stats[char_uuid]["count"] += 1
        stats[char_uuid]["last_hex"] = hex_str
        stats[char_uuid]["last_bytes"] = bytes(data)
        stats[char_uuid]["last_time"] = now.strftime("%H:%M:%S.%f")[:-3]

        if not live_display:
            console.print(
                f"[dim]{now.strftime('%H:%M:%S.%f')[:-3]}[/dim] "
                f"[cyan]{char_uuid[:23]}[/cyan] "
                f"[white]{hex_str}[/white]"
            )

    return callback


async def _subscribe_all(
    client, notifiable: list, stats: dict, event_log: list[dict], live_display: bool
) -> None:
    """Subscribe to every notifiable char; per-char failures print but don't abort."""
    for char in notifiable:
        uuid_str = str(char.uuid)
        try:
            await client.start_notify(
                char, _make_monitor_callback(uuid_str, stats, event_log, live_display)
            )
            name = stats[uuid_str]["name"]
            console.print(f"  [green]+[/green] Subscribed: {uuid_str} ({name})")
        except BleakError as e:
            console.print(f"  [red]x[/red] Failed: {uuid_str} — {e}")


def _install_signal_handlers() -> asyncio.Event:
    """Wire SIGINT/SIGTERM to set the stop event; return the event for the wait loop."""
    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()

    def signal_handler() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)
    return stop_event


async def _wait_for_stop(
    stop_event: asyncio.Event, duration: int, stats: dict, live_display: bool
) -> None:
    """Wait for either the stop event or the duration timeout."""
    if live_display:
        with Live(make_summary_table(stats), refresh_per_second=2, console=console) as live:
            await _await_stop_or_timeout(stop_event, duration)
            live.update(make_summary_table(stats))
    else:
        await _await_stop_or_timeout(stop_event, duration)


async def _await_stop_or_timeout(stop_event: asyncio.Event, duration: int) -> None:
    """Block on the stop event; apply the duration timeout if non-zero."""
    if duration > 0:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=duration)
    else:
        await stop_event.wait()


async def _unsubscribe_all(client, notifiable: list) -> None:
    """Stop notifications on every previously-subscribed characteristic (best-effort)."""
    for char in notifiable:
        with contextlib.suppress(BleakError):
            await client.stop_notify(char)


def _save_results(
    address: str,
    event_log: list[dict],
    notifiable: list,
    stats: dict,
    output_dir: Path,
) -> None:
    """Write the JSON summary file and print the saved-count line."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "device": address,
        "start_time": (
            event_log[0]["timestamp"] if event_log else datetime.now(tz=UTC).isoformat()
        ),
        "total_notifications": len(event_log),
        "characteristics_monitored": len(notifiable),
        "summary": {
            uuid: {"name": s["name"], "count": s["count"], "last_hex": s["last_hex"]}
            for uuid, s in stats.items()
        },
        "events": event_log,
    }

    json_path = output_dir / f"{timestamp}_monitor.json"
    json_path.write_text(json.dumps(result, indent=2))
    console.print(f"\n[bold]Saved {len(event_log)} events to {json_path}[/bold]")


def _handle_ble_error(error: BleakError) -> None:
    """Print a helpful error message for known BLE failure modes."""
    error_msg = str(error)
    if "CoreBluetooth" in error_msg or "authorization" in error_msg.lower():
        console.print(
            "[red]Bluetooth permission denied.[/red]\n"
            "Go to System Settings > Privacy & Security > Bluetooth\n"
            "and grant access to Terminal/iTerm."
        )
    else:
        console.print(f"[red]BLE error: {error}[/red]")


def main():
    parser = argparse.ArgumentParser(
        description="Monitor all BLE notifications from a Bento Lab device"
    )
    parser.add_argument(
        "--device",
        required=True,
        help="BLE device address to connect to",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=0,
        help="Monitoring duration in seconds (0 = until Ctrl+C, default: 0)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("captures/ble"),
        help="Output directory (default: captures/ble/)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Show live-updating summary table instead of streaming events",
    )

    args = parser.parse_args()
    asyncio.run(monitor(args.device, args.duration, args.output_dir, args.live))


if __name__ == "__main__":
    main()
