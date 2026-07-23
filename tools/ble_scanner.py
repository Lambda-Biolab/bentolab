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

        tree = Tree(
            f"[bold cyan]{device.name or 'Unknown'}[/bold cyan] ({device.address})",
            guide_style="blue",
        )

        notifiable_chars: list[BleakGATTCharacteristic] = []
        for service in client.services:
            await _process_service(client, service, tree, notifiable_chars, result)

        console.print(tree)
        console.print()

        if notifiable_chars and notify_time > 0:
            await _monitor_notifications(client, notifiable_chars, notify_time, result)
        elif not notifiable_chars:
            console.print("[dim]No notifiable characteristics found.[/dim]")

    return result


def _format_value_display(char_data: dict) -> str:
    """Format a characteristic's current value or read error for the tree view."""
    if char_data["value_text"]:
        return f'  = [green]"{char_data["value_text"]}"[/green]'
    if char_data["value_hex"]:
        return f"  = [dim]{char_data['value_hex']}[/dim]"
    if char_data["read_error"]:
        return f"  [red]({char_data['read_error']})[/red]"
    return ""


async def _read_char_value(client, char) -> tuple[str | None, str | None, str | None]:
    """Try to read a characteristic's value. Returns (hex, text, error_str)."""
    try:
        raw = await asyncio.wait_for(client.read_gatt_char(char), timeout=5.0)
    except TimeoutError:
        return None, None, "timeout"
    except BleakError as exc:
        return None, None, str(exc)
    except Exception as exc:
        return None, None, f"unexpected: {exc}"
    return _hex(raw), _try_decode(raw) or None, None


async def _read_descriptors(client, char) -> list[dict]:
    """Read every descriptor of a characteristic. Per-descriptor failures yield null."""
    out: list[dict] = []
    for desc in char.descriptors:
        try:
            desc_val = await asyncio.wait_for(client.read_gatt_descriptor(desc.handle), timeout=5.0)
            out.append(
                {
                    "uuid": desc.uuid,
                    "name": lookup_uuid(desc.uuid),
                    "value_hex": _hex(desc_val),
                }
            )
        except Exception:
            out.append(
                {
                    "uuid": desc.uuid,
                    "name": lookup_uuid(desc.uuid),
                    "value_hex": None,
                }
            )
    return out


async def _enumerate_characteristic(client, char, svc_node) -> dict:
    """Build the per-characteristic data dict and add it to the service tree."""
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

    if "read" in props:
        value_hex, value_text, read_error = await _read_char_value(client, char)
        char_data["value_hex"] = value_hex
        char_data["value_text"] = value_text
        char_data["read_error"] = read_error

    val_display = _format_value_display(char_data)
    char_style = "white" if char_name != "Custom" else "magenta"
    svc_node.add(
        f"[{char_style}]{char_name}[/{char_style}]  "
        f"[dim]{char.uuid}[/dim]\n"
        f"        [{char_style}]Properties:[/{char_style}] {props_str}"
        f"{val_display}"
    )

    char_data["descriptors"] = await _read_descriptors(client, char)
    return char_data


async def _process_service(
    client,
    service,
    tree,
    notifiable_chars: list[BleakGATTCharacteristic],
    result: dict,
) -> None:
    """Enumerate one GATT service: append to tree, populate ``result['services']``."""
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
        char_data = await _enumerate_characteristic(client, char, svc_node)
        svc_data["characteristics"].append(char_data)
        if "notify" in char_data["properties"] or "indicate" in char_data["properties"]:
            notifiable_chars.append(char)

    result["services"].append(svc_data)


def _make_enumeration_callback(char_uuid: str, notifications: list[dict]) -> callable:
    """Build a notification callback that appends to ``notifications`` and prints."""

    def _notification_handler(_sender: BleakGATTCharacteristic, data: bytearray) -> None:
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


def _install_sigint_stop_handler() -> tuple[callable, asyncio.Event]:
    """Install a SIGINT handler that sets a stop event; return (orig_handler, event)."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    original_handler = signal.getsignal(signal.SIGINT)

    def _sigint_handler(_sig: int, _frame: object) -> None:
        console.print("\n[yellow]Stopping notification monitor...[/yellow]")
        loop.call_soon_threadsafe(stop_event.set)

    signal.signal(signal.SIGINT, _sigint_handler)
    return original_handler, stop_event


async def _subscribe_to_notifiable_chars(
    client, notifiable_chars: list[BleakGATTCharacteristic], notifications: list[dict]
) -> None:
    """Subscribe to every notifiable char; per-char failures print but don't abort."""
    for char in notifiable_chars:
        try:
            await client.start_notify(char, _make_enumeration_callback(char.uuid, notifications))
            console.print(f"  Subscribed: [cyan]{char.uuid}[/cyan] ({lookup_uuid(char.uuid)})")
        except BleakError as exc:
            console.print(f"  [red]Failed to subscribe {char.uuid}: {exc}[/red]")


async def _unsubscribe_notifiable_chars(
    client, notifiable_chars: list[BleakGATTCharacteristic]
) -> None:
    """Stop notifications on every previously-subscribed characteristic (best-effort)."""
    for char in notifiable_chars:
        with contextlib.suppress(BleakError):
            await client.stop_notify(char)


def _print_notification_summary(notifications: list[dict]) -> None:
    """Print the final notifications table, or a 'no notifications' line."""
    if not notifications:
        console.print("[yellow]No notifications received.[/yellow]")
        return
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


async def _monitor_notifications(
    client,
    notifiable_chars: list[BleakGATTCharacteristic],
    notify_time: float,
    result: dict,
) -> None:
    """Subscribe, listen, and unsubscribe; populate result['notifications']."""
    console.print(
        Panel(
            f"Subscribing to {len(notifiable_chars)} notifiable characteristic(s)\n"
            f"Listening for [cyan]{notify_time}s[/cyan]... (Ctrl+C to stop early)",
            title="Notification Monitor",
            border_style="yellow",
        )
    )

    notifications: list[dict] = []
    original_handler, stop_event = _install_sigint_stop_handler()

    await _subscribe_to_notifiable_chars(client, notifiable_chars, notifications)
    console.print()

    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop_event.wait(), timeout=notify_time)

    signal.signal(signal.SIGINT, original_handler)
    await _unsubscribe_notifiable_chars(client, notifiable_chars)

    result["notifications"] = notifications
    _print_notification_summary(notifications)


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
    lines.extend(_markdown_header(device, timestamp))
    lines.extend(_markdown_services_section(services))
    lines.extend(_markdown_characteristics_table(services))
    lines.extend(_markdown_notification_channels(services, notifications))
    lines.extend(_markdown_unknown_characteristics(services))
    lines.extend(_markdown_notification_log(notifications))

    path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]Markdown saved:[/green] {path}")
    return path


def _markdown_header(device: dict, timestamp: str) -> list[str]:
    """Title block: H1 + scanned + device lines."""
    name = device.get("name", "Unknown")
    return [
        f"# BLE GATT Profile — {name}",
        "",
        f"**Scanned:** {timestamp}",
        f"**Device:** {name} ({device.get('address', 'N/A')})",
        "",
    ]


def _markdown_services_section(services: list[dict]) -> list[str]:
    """H2 'Discovered Services' followed by a bulleted UUID list."""
    out = ["## Discovered Services", ""]
    for svc in services:
        label = svc["name"] if svc["name"] != "Custom" else "Custom / Vendor"
        out.append(f"- **{label}** `{svc['uuid']}`")
    out.append("")
    return out


def _markdown_characteristics_table(services: list[dict]) -> list[str]:
    """H2 'Characteristics Table' followed by a markdown table."""
    out = [
        "## Characteristics Table",
        "",
        "| Service UUID | Char UUID | Properties | Description | Sample Value |",
        "|-------------|-----------|------------|-------------|--------------|",
    ]
    for svc in services:
        for char in svc["characteristics"]:
            props = ", ".join(char["properties"])
            value = _format_sample_value(char)
            out.append(
                f"| `{svc['uuid']}` | `{char['uuid']}` | {props} | {char['name']} | {value} |"
            )
    out.append("")
    return out


def _format_sample_value(char: dict) -> str:
    """Pick the best string for the 'Sample Value' column of the characteristics table."""
    if char.get("value_text"):
        return char["value_text"]
    if char.get("value_hex"):
        return f"`{char['value_hex']}`"
    if char.get("read_error"):
        return f"_error: {char['read_error']}_"
    return ""


def _collect_notifiable_chars(services: list[dict]) -> list[dict]:
    """Flatten all characteristics that support notify or indicate."""
    out: list[dict] = []
    for svc in services:
        for char in svc["characteristics"]:
            if "notify" in char["properties"] or "indicate" in char["properties"]:
                out.append(char)
    return out


def _infer_data_format(char: dict, notifications: list[dict]) -> str:
    """Infer a char's data format from any captured notifications."""
    for n in notifications:
        if n["uuid"] == char["uuid"]:
            return f"{n['length']} bytes"
    return "unknown"


def _markdown_notification_channels(services: list[dict], notifications: list[dict]) -> list[str]:
    """H2 'Notification Channels' followed by a table (or 'none' fallback)."""
    notifiable = _collect_notifiable_chars(services)
    out = ["## Notification Channels", ""]
    if not notifiable:
        out.append("_No notifiable characteristics found._")
        out.append("")
        return out
    out.extend(
        [
            "| Char UUID | Data Format | Update Rate | Description |",
            "|-----------|-------------|-------------|-------------|",
        ]
    )
    for char in notifiable:
        fmt = _infer_data_format(char, notifications)
        out.append(f"| `{char['uuid']}` | {fmt} | unknown | {char['name']} |")
    out.append("")
    return out


def _collect_custom_chars(services: list[dict]) -> list[dict]:
    """Flatten all characteristics whose lookup name is 'Custom'."""
    return [char for svc in services for char in svc["characteristics"] if char["name"] == "Custom"]


def _markdown_unknown_characteristics(services: list[dict]) -> list[str]:
    """H2 'Unknown Characteristics' listing Custom-name chars (or 'all known')."""
    custom_chars = _collect_custom_chars(services)
    out = ["## Unknown Characteristics", ""]
    if not custom_chars:
        out.append("_All characteristics identified._")
        out.append("")
        return out
    out.append("_Characteristics with no identified purpose yet._")
    out.append("")
    for char in custom_chars:
        val_str = ""
        if char.get("value_hex"):
            val_str = f" = `{char['value_hex']}`"
        out.append(f"- `{char['uuid']}` [{', '.join(char['properties'])}]{val_str}")
    out.append("")
    return out


def _markdown_notification_log(notifications: list[dict]) -> list[str]:
    """H2 'Notification Log' table; empty if no notifications."""
    if not notifications:
        return []
    out = [
        "## Notification Log",
        "",
        "| Timestamp | UUID | Hex | Decoded |",
        "|-----------|------|-----|---------|",
    ]
    for n in notifications:
        out.append(f"| {n['timestamp']} | `{n['uuid']}` | `{n['hex']}` | {n['decoded'] or ''} |")
    out.append("")
    return out


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

    scan_data, discovered, matches = await _do_scan_phase(args, output_dir, timestamp)
    if not args.connect or scan_data is None:
        return

    target_device = _select_target_device(matches, discovered, args)
    if target_device is None:
        return

    await _do_connect_phase(target_device, scan_data, args, output_dir, timestamp)


async def _do_scan_phase(
    args: argparse.Namespace, output_dir: Path, timestamp: str
) -> tuple[dict | None, list, list[tuple]]:
    """Run the scan phase: discover, display, optionally save scan-only output.

    Returns (scan_data, discovered, matches). ``scan_data`` is None when
    the scan was cancelled (BLE error or no --connect) — caller exits.
    ``discovered`` is the full BLEDevice list; ``matches`` is the
    post-filter list of (BLEDevice, AdvertisementData) whose name
    matches ``--device-name``; used by the connect phase to pick a
    target.
    """
    try:
        discovered = await scan_devices(args.scan_time, args.device_name)
    except BleakError as exc:
        _check_macos_bluetooth_permission(exc)
        console.print(f"[red]BLE scan failed: {exc}[/red]")
        sys.exit(1)

    matches = display_scan_results(discovered, args.device_name)
    scan_data = _build_scan_data(discovered, args, timestamp)

    if not args.connect:
        _save_scan_only_output(scan_data, args, output_dir, timestamp)
        return None, discovered, []

    return scan_data, discovered, matches


def _build_scan_data(discovered, args, timestamp: str) -> dict:
    """Build the scan-only JSON dict from the discovery result."""
    return {
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


def _save_scan_only_output(scan_data: dict, args, output_dir: Path, timestamp: str) -> None:
    """Save scan-only JSON; explain why markdown isn't generated without --connect."""
    if args.format in ("json", "both"):
        save_json(scan_data, output_dir, timestamp)
    if args.format in ("md", "both"):
        console.print("[dim]Markdown output requires --connect for GATT data.[/dim]")


def _select_target_device(matches: list[tuple], discovered: list, args) -> BLEDevice | str | None:
    """Pick which device to connect to. Returns None when no candidate is found."""
    if args.device_address:
        return _select_by_address(args.device_address, discovered)
    if not matches:
        console.print(
            "[red]No matching devices found. Cannot connect.[/red]\n"
            "[dim]Try broadening --device-name or specifying --device-address[/dim]"
        )
        return None
    if len(matches) == 1:
        return matches[0][0]
    strongest = matches[0][0]
    console.print(
        f"[yellow]Multiple matches found. Connecting to strongest: "
        f"{strongest.name} ({strongest.address})[/yellow]"
    )
    return strongest


def _select_by_address(address: str, discovered: list) -> str:
    """Return the BLEDevice from ``discovered`` matching ``address``, or the
    raw address string for BleakClient to handle as a direct connection."""
    addr_upper = address.upper()
    for dev, _adv in discovered:
        if dev.address.upper() == addr_upper:
            return dev
    console.print(
        f"[yellow]Address {address} not in scan. Attempting direct connection...[/yellow]"
    )
    return address


async def _do_connect_phase(
    target_device,
    scan_data: dict,
    args,
    output_dir: Path,
    timestamp: str,
) -> None:
    """Connect, enumerate GATT, merge scan data, and save outputs."""
    try:
        gatt_data = await enumerate_gatt(target_device, args.notify_time)
    except BleakError as exc:
        _check_macos_bluetooth_permission(exc)
        console.print(f"[red]Connection failed: {exc}[/red]")
        sys.exit(1)

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
