#!/usr/bin/env python3
"""BLE scanner and GATT enumerator for Bento Lab reverse engineering.

Discovers BLE devices, connects to targets, enumerates all GATT services
and characteristics, reads readable values, and monitors notifications.

Usage:
    python tools/ble_scanner.py                          # scan only
    python tools/ble_scanner.py --connect                # scan + connect + enumerate
    python tools/ble_scanner.py --device-address AA:BB:CC:DD:EE:FF --connect
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import re
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

from bleak import BleakClient, BleakError, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

# Add project root to path so we can import bentolab
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from bentolab.protocol import lookup_uuid  # noqa: E402

console = Console()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


def _props_list(char: BleakGATTCharacteristic) -> list[str]:
    """Extract the property strings from a characteristic."""
    return [p.strip().lower() for p in char.properties]


def _hex(data: bytes) -> str:
    return data.hex(" ") if data else ""


def _try_decode(data: bytes) -> str:
    """Attempt UTF-8 decode; return empty string on failure."""
    try:
        text = data.decode("utf-8")
        if all(32 <= ord(c) < 127 or c in "\r\n\t" for c in text):
            return text
        return ""
    except (UnicodeDecodeError, ValueError):
        return ""


def _manufacturer_hex(adv: AdvertisementData) -> str:
    """Format manufacturer data as hex string."""
    if not adv.manufacturer_data:
        return ""
    parts = []
    for company_id, data in adv.manufacturer_data.items():
        parts.append(f"0x{company_id:04X}: {data.hex(' ')}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Scan mode
# ---------------------------------------------------------------------------


async def scan_devices(
    scan_time: float,
    name_pattern: str,
) -> list[tuple[BLEDevice, AdvertisementData]]:
    """Scan for BLE devices and return all discovered."""
    console.print(
        Panel(
            f"Scanning for BLE devices ({scan_time}s)...\nName filter: [cyan]{name_pattern}[/cyan]",
            title="BLE Scanner",
            border_style="blue",
        )
    )

    discovered: list[tuple[BLEDevice, AdvertisementData]] = []

    def _callback(device: BLEDevice, adv: AdvertisementData) -> None:
        # Deduplicate by address
        for existing_dev, _ in discovered:
            if existing_dev.address == device.address:
                return
        discovered.append((device, adv))

    scanner = BleakScanner(detection_callback=_callback)
    await scanner.start()
    await asyncio.sleep(scan_time)
    await scanner.stop()

    return discovered


def display_scan_results(
    discovered: list[tuple[BLEDevice, AdvertisementData]],
    name_pattern: str,
) -> list[tuple[BLEDevice, AdvertisementData]]:
    """Display scan results in a rich table. Returns matched devices."""
    if not discovered:
        console.print("[yellow]No BLE devices found.[/yellow]")
        return []

    regex = re.compile(name_pattern)

    table = Table(
        title=f"BLE Devices Found ({len(discovered)})",
        show_lines=True,
        border_style="blue",
    )
    table.add_column("Name", style="white", min_width=20)
    table.add_column("Address", style="cyan", min_width=17)
    table.add_column("RSSI", style="green", justify="right", min_width=6)
    table.add_column("Manufacturer Data", style="dim", max_width=50)

    matches: list[tuple[BLEDevice, AdvertisementData]] = []

    # Sort by RSSI descending (strongest first)
    discovered.sort(key=lambda x: x[1].rssi if x[1].rssi is not None else -999, reverse=True)

    for device, adv in discovered:
        name = device.name or adv.local_name or "Unknown"
        is_match = bool(regex.search(name))
        if is_match:
            matches.append((device, adv))

        rssi_str = f"{adv.rssi} dBm" if adv.rssi is not None else "N/A"
        mfr = _manufacturer_hex(adv)

        row_style = "bold green" if is_match else "dim"
        name_display = f"[bold green]>> {name} <<[/bold green]" if is_match else name

        table.add_row(name_display, device.address, rssi_str, mfr, style=row_style)

    console.print(table)

    if matches:
        console.print(
            f"\n[green]Found {len(matches)} matching device(s) for pattern '{name_pattern}'[/green]"
        )
    else:
        console.print(f"\n[yellow]No devices matched pattern '{name_pattern}'[/yellow]")

    return matches


# ---------------------------------------------------------------------------
# Connect + GATT enumeration
# ---------------------------------------------------------------------------


async def enumerate_gatt(
    device: BLEDevice,
    notify_time: float,
) -> dict:
    """Connect to a device and enumerate its entire GATT profile."""
    result: dict = {
        "device": {
            "name": device.name or "Unknown",
            "address": device.address,
        },
        "services": [],
        "notifications": [],
    }

    console.print(
        Panel(
            f"Connecting to [cyan]{device.name or 'Unknown'}[/cyan] "
            f"([cyan]{device.address}[/cyan])...",
            title="GATT Enumeration",
            border_style="green",
        )
    )

    async with BleakClient(device, timeout=20.0) as client:
        if not client.is_connected:
            console.print("[red]Failed to connect.[/red]")
            return result

        console.print("[green]Connected successfully.[/green]\n")

        # Build the service tree for display
        tree = Tree(
            f"[bold cyan]{device.name or 'Unknown'}[/bold cyan] ({device.address})",
            guide_style="blue",
        )

        notifiable_chars: list[BleakGATTCharacteristic] = []

        for service in client.services:
            svc_name = lookup_uuid(service.uuid)
            svc_label = (
                f"[bold yellow]{svc_name}[/bold yellow]"
                if svc_name != "Custom"
                else "[bold magenta]Custom[/bold magenta]"
            )
            svc_node = tree.add(f"{svc_label}  [dim]{service.uuid}[/dim]")

            svc_data: dict = {
                "uuid": service.uuid,
                "name": svc_name,
                "characteristics": [],
            }

            for char in service.characteristics:
                props = _props_list(char)
                char_name = lookup_uuid(char.uuid)
                props_str = ", ".join(props)

                char_data: dict = {
                    "uuid": char.uuid,
                    "name": char_name,
                    "properties": props,
                    "handle": char.handle,
                    "value_hex": None,
                    "value_text": None,
                    "read_error": None,
                }

                # Try to read if readable
                if "read" in props:
                    try:
                        raw = await asyncio.wait_for(client.read_gatt_char(char), timeout=5.0)
                        char_data["value_hex"] = _hex(raw)
                        decoded = _try_decode(raw)
                        if decoded:
                            char_data["value_text"] = decoded
                    except TimeoutError:
                        char_data["read_error"] = "timeout"
                    except BleakError as exc:
                        char_data["read_error"] = str(exc)
                    except Exception as exc:  # noqa: BLE001
                        char_data["read_error"] = f"unexpected: {exc}"

                # Track notifiable characteristics
                if "notify" in props or "indicate" in props:
                    notifiable_chars.append(char)

                # Build display
                val_display = ""
                if char_data["value_text"]:
                    val_display = f'  = [green]"{char_data["value_text"]}"[/green]'
                elif char_data["value_hex"]:
                    val_display = f"  = [dim]{char_data['value_hex']}[/dim]"
                elif char_data["read_error"]:
                    val_display = f"  [red]({char_data['read_error']})[/red]"

                char_style = "white" if char_name != "Custom" else "magenta"
                svc_node.add(
                    f"[{char_style}]{char_name}[/{char_style}]  "
                    f"[dim]{char.uuid}[/dim]\n"
                    f"        [{char_style}]Properties:[/{char_style}] {props_str}"
                    f"{val_display}"
                )

                # Enumerate descriptors
                for desc in char.descriptors:
                    desc_name = lookup_uuid(desc.uuid)
                    try:
                        desc_val = await asyncio.wait_for(
                            client.read_gatt_descriptor(desc.handle), timeout=5.0
                        )
                        char_data.setdefault("descriptors", []).append(
                            {
                                "uuid": desc.uuid,
                                "name": desc_name,
                                "value_hex": _hex(desc_val),
                            }
                        )
                    except Exception:  # noqa: BLE001
                        char_data.setdefault("descriptors", []).append(
                            {
                                "uuid": desc.uuid,
                                "name": desc_name,
                                "value_hex": None,
                            }
                        )

                svc_data["characteristics"].append(char_data)

            result["services"].append(svc_data)

        console.print(tree)
        console.print()

        # --- Notification monitoring ---
        if notifiable_chars and notify_time > 0:
            console.print(
                Panel(
                    f"Subscribing to {len(notifiable_chars)} notifiable characteristic(s)\n"
                    f"Listening for [cyan]{notify_time}s[/cyan]... (Ctrl+C to stop early)",
                    title="Notification Monitor",
                    border_style="yellow",
                )
            )

            notifications: list[dict] = []
            stop_event = asyncio.Event()

            # Handle Ctrl+C gracefully
            loop = asyncio.get_running_loop()
            original_handler = signal.getsignal(signal.SIGINT)

            def _sigint_handler(_sig: int, _frame: object) -> None:
                console.print("\n[yellow]Stopping notification monitor...[/yellow]")
                loop.call_soon_threadsafe(stop_event.set)

            signal.signal(signal.SIGINT, _sigint_handler)

            def _make_callback(
                char_uuid: str,
            ) -> callable:
                def _notification_handler(
                    _sender: BleakGATTCharacteristic,
                    data: bytearray,
                ) -> None:
                    ts = datetime.now(tz=UTC).isoformat()
                    raw_bytes = bytes(data)
                    entry = {
                        "timestamp": ts,
                        "uuid": char_uuid,
                        "name": lookup_uuid(char_uuid),
                        "hex": _hex(raw_bytes),
                        "decoded": _try_decode(raw_bytes),
                        "length": len(raw_bytes),
                    }
                    notifications.append(entry)

                    decoded_str = f' "{entry["decoded"]}"' if entry["decoded"] else ""
                    console.print(
                        f"  [dim]{ts}[/dim]  "
                        f"[cyan]{char_uuid}[/cyan]  "
                        f"[green]{entry['hex']}[/green]"
                        f"{decoded_str}"
                    )

                return _notification_handler

            # Subscribe to all notifiable characteristics
            for char in notifiable_chars:
                try:
                    await client.start_notify(char, _make_callback(char.uuid))
                    console.print(
                        f"  Subscribed: [cyan]{char.uuid}[/cyan] ({lookup_uuid(char.uuid)})"
                    )
                except BleakError as exc:
                    console.print(f"  [red]Failed to subscribe {char.uuid}: {exc}[/red]")

            console.print()

            # Wait for notify_time or Ctrl+C
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=notify_time)

            # Restore signal handler
            signal.signal(signal.SIGINT, original_handler)

            # Unsubscribe
            for char in notifiable_chars:
                with contextlib.suppress(BleakError):
                    await client.stop_notify(char)

            result["notifications"] = notifications

            if notifications:
                notify_table = Table(
                    title=f"Notifications Received ({len(notifications)})",
                    show_lines=True,
                    border_style="yellow",
                )
                notify_table.add_column("Timestamp", style="dim", min_width=26)
                notify_table.add_column("UUID", style="cyan", min_width=36)
                notify_table.add_column("Hex", style="green")
                notify_table.add_column("Decoded", style="white")

                for n in notifications:
                    notify_table.add_row(n["timestamp"], n["uuid"], n["hex"], n["decoded"] or "")

                console.print(notify_table)
            else:
                console.print("[yellow]No notifications received.[/yellow]")

        elif not notifiable_chars:
            console.print("[dim]No notifiable characteristics found.[/dim]")

    return result


# ---------------------------------------------------------------------------
# Output: JSON + Markdown
# ---------------------------------------------------------------------------


def save_json(data: dict, output_dir: Path, timestamp: str) -> Path:
    """Save scan/GATT results as JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{timestamp}_scan.json"
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    console.print(f"[green]JSON saved:[/green] {path}")
    return path


def save_markdown(data: dict, output_dir: Path, timestamp: str) -> Path:
    """Save GATT results as markdown matching docs/ble-gatt-profile.md format."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{timestamp}_gatt.md"

    device = data.get("device", {})
    services = data.get("services", [])
    notifications = data.get("notifications", [])

    lines: list[str] = []
    lines.append(f"# BLE GATT Profile — {device.get('name', 'Unknown')}")
    lines.append("")
    lines.append(f"**Scanned:** {timestamp}")
    lines.append(f"**Device:** {device.get('name', 'Unknown')} ({device.get('address', 'N/A')})")
    lines.append("")

    # Discovered services
    lines.append("## Discovered Services")
    lines.append("")
    for svc in services:
        svc_label = svc["name"] if svc["name"] != "Custom" else "Custom / Vendor"
        lines.append(f"- **{svc_label}** `{svc['uuid']}`")
    lines.append("")

    # Characteristics table
    lines.append("## Characteristics Table")
    lines.append("")
    lines.append("| Service UUID | Char UUID | Properties | Description | Sample Value |")
    lines.append("|-------------|-----------|------------|-------------|--------------|")
    for svc in services:
        for char in svc["characteristics"]:
            props = ", ".join(char["properties"])
            desc = char["name"]
            value = ""
            if char.get("value_text"):
                value = char["value_text"]
            elif char.get("value_hex"):
                value = f"`{char['value_hex']}`"
            elif char.get("read_error"):
                value = f"_error: {char['read_error']}_"

            lines.append(f"| `{svc['uuid']}` | `{char['uuid']}` | {props} | {desc} | {value} |")
    lines.append("")

    # Notification channels
    notifiable = []
    for svc in services:
        for char in svc["characteristics"]:
            if "notify" in char["properties"] or "indicate" in char["properties"]:
                notifiable.append(char)

    lines.append("## Notification Channels")
    lines.append("")
    if notifiable:
        lines.append("| Char UUID | Data Format | Update Rate | Description |")
        lines.append("|-----------|-------------|-------------|-------------|")
        for char in notifiable:
            # Infer data format from any captured notifications
            fmt = "unknown"
            rate = "unknown"
            for n in notifications:
                if n["uuid"] == char["uuid"]:
                    fmt = f"{n['length']} bytes"
                    break
            lines.append(f"| `{char['uuid']}` | {fmt} | {rate} | {char['name']} |")
    else:
        lines.append("_No notifiable characteristics found._")
    lines.append("")

    # Unknown characteristics
    lines.append("## Unknown Characteristics")
    lines.append("")
    custom_chars = [
        char for svc in services for char in svc["characteristics"] if char["name"] == "Custom"
    ]
    if custom_chars:
        lines.append("_Characteristics with no identified purpose yet._")
        lines.append("")
        for char in custom_chars:
            val_str = ""
            if char.get("value_hex"):
                val_str = f" = `{char['value_hex']}`"
            lines.append(f"- `{char['uuid']}` [{', '.join(char['properties'])}]{val_str}")
    else:
        lines.append("_All characteristics identified._")
    lines.append("")

    # Raw notification log
    if notifications:
        lines.append("## Notification Log")
        lines.append("")
        lines.append("| Timestamp | UUID | Hex | Decoded |")
        lines.append("|-----------|------|-----|---------|")
        for n in notifications:
            lines.append(
                f"| {n['timestamp']} | `{n['uuid']}` | `{n['hex']}` | {n['decoded'] or ''} |"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]Markdown saved:[/green] {path}")
    return path


# ---------------------------------------------------------------------------
# macOS permission check
# ---------------------------------------------------------------------------


def _check_macos_bluetooth_permission(exc: Exception) -> None:
    """Detect macOS CoreBluetooth authorization errors and print fix instructions."""
    msg = str(exc).lower()
    permission_indicators = [
        "not authorized",
        "corebluetooth",
        "authorization",
        "permission",
        "cblmanager",
        "powered off",
    ]
    if any(indicator in msg for indicator in permission_indicators):
        console.print(
            Panel(
                "[bold red]Bluetooth permission denied on macOS.[/bold red]\n\n"
                "To fix this:\n"
                "  1. Open [bold]System Settings > Privacy & Security > Bluetooth[/bold]\n"
                "  2. Enable Bluetooth access for [cyan]Terminal[/cyan] "
                "(or your terminal app)\n"
                "  3. If using VS Code or another IDE, grant permission to that app\n"
                "  4. You may need to restart the terminal after granting access\n\n"
                "If Bluetooth is powered off:\n"
                '  - Click the Bluetooth icon in the menu bar and select "Turn On"\n'
                "  - Or: [dim]System Settings > Bluetooth > Turn On[/dim]",
                title="macOS Bluetooth Permission Error",
                border_style="red",
            )
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="BLE scanner and GATT enumerator for Bento Lab reverse engineering.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tools/ble_scanner.py                                    # scan only\n"
            "  python tools/ble_scanner.py --connect                          # scan + GATT enum\n"
            "  python tools/ble_scanner.py --device-address AA:BB:CC:DD:EE:FF --connect\n"
            "  python tools/ble_scanner.py --scan-time 20 --notify-time 60 --connect\n"
        ),
    )
    parser.add_argument(
        "--scan-time",
        type=float,
        default=10.0,
        metavar="SECONDS",
        help="BLE scan duration in seconds (default: 10)",
    )
    parser.add_argument(
        "--device-name",
        type=str,
        default=r"(?i)bento",
        metavar="PATTERN",
        help="Filter devices by name regex (default: '(?i)bento')",
    )
    parser.add_argument(
        "--device-address",
        type=str,
        default=None,
        metavar="ADDR",
        help="Connect to a specific device address (e.g., AA:BB:CC:DD:EE:FF)",
    )
    parser.add_argument(
        "--connect",
        action="store_true",
        help="Connect to the matched device and enumerate GATT profile",
    )
    parser.add_argument(
        "--notify-time",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="Duration to listen for notifications after GATT enum (default: 30)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("captures/ble/"),
        metavar="PATH",
        help="Directory for saving output files (default: captures/ble/)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "md", "both"],
        default="both",
        help="Output format (default: both)",
    )
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> None:
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir.resolve()

    # ---- Scan phase ----
    try:
        discovered = await scan_devices(args.scan_time, args.device_name)
    except BleakError as exc:
        _check_macos_bluetooth_permission(exc)
        console.print(f"[red]BLE scan failed: {exc}[/red]")
        sys.exit(1)

    matches = display_scan_results(discovered, args.device_name)

    # Build scan-only JSON data
    scan_data: dict = {
        "timestamp": timestamp,
        "scan_time": args.scan_time,
        "name_filter": args.device_name,
        "devices": [
            {
                "name": dev.name or adv.local_name or "Unknown",
                "address": dev.address,
                "rssi": adv.rssi,
                "manufacturer_data": {
                    f"0x{cid:04X}": data.hex()
                    for cid, data in (adv.manufacturer_data or {}).items()
                },
            }
            for dev, adv in discovered
        ],
    }

    if not args.connect:
        # Save scan results only
        if args.format in ("json", "both"):
            save_json(scan_data, output_dir, timestamp)
        if args.format in ("md", "both"):
            console.print("[dim]Markdown output requires --connect for GATT data.[/dim]")
        return

    # ---- Connect phase ----
    target_device: BLEDevice | None = None

    if args.device_address:
        # Find by address in discovered devices, or create a reference
        addr_upper = args.device_address.upper()
        for dev, _adv in discovered:
            if dev.address.upper() == addr_upper:
                target_device = dev
                break

        if target_device is None:
            # Device might not have been in scan results; try direct connect
            console.print(
                f"[yellow]Address {args.device_address} not found in scan. "
                f"Attempting direct connection...[/yellow]"
            )
            # Create a minimal BLEDevice-like target; BleakClient accepts address strings
            target_device = args.device_address  # type: ignore[assignment]
    elif matches:
        if len(matches) == 1:
            target_device = matches[0][0]
        else:
            # Multiple matches: pick the strongest signal
            target_device = matches[0][0]  # already sorted by RSSI
            console.print(
                f"[yellow]Multiple matches found. Connecting to strongest: "
                f"{target_device.name} ({target_device.address})[/yellow]"
            )
    else:
        console.print(
            "[red]No matching devices found. Cannot connect.[/red]\n"
            "[dim]Try broadening --device-name or specifying --device-address[/dim]"
        )
        return

    try:
        gatt_data = await enumerate_gatt(
            target_device,
            args.notify_time,
        )
    except BleakError as exc:
        _check_macos_bluetooth_permission(exc)
        console.print(f"[red]Connection failed: {exc}[/red]")
        sys.exit(1)

    # Merge scan data into GATT data for complete output
    gatt_data["scan"] = scan_data

    if args.format in ("json", "both"):
        save_json(gatt_data, output_dir, timestamp)
    if args.format in ("md", "both"):
        save_markdown(gatt_data, output_dir, timestamp)


def main() -> None:
    args = parse_args()

    console.print(
        Panel(
            "[bold]Bento Lab BLE Scanner & GATT Enumerator[/bold]\n"
            "[dim]Reverse engineering tool for BLE-connected Bento Lab devices[/dim]",
            border_style="blue",
        )
    )

    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    main()
