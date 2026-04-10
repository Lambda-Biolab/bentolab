#!/usr/bin/env python3
"""Interactive BLE command REPL for Bento Lab reverse engineering.

Connects to a BLE device and provides an interactive shell for reading,
writing, subscribing to notifications, and fuzzing characteristics.

Usage:
    python tools/ble_commander.py
    python tools/ble_commander.py --device <address>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from bleak import BleakClient, BleakError, BleakScanner
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import contextlib

from bentolab.protocol import lookup_uuid

console = Console()

HELP_TEXT = """\
[bold]BLE Commander — Commands[/bold]

  [cyan]scan[/cyan]                          Scan for BLE devices
  [cyan]connect[/cyan] <address|index>       Connect to a device (use index from scan)
  [cyan]disconnect[/cyan]                    Disconnect current device
  [cyan]services[/cyan]                      List all GATT services and characteristics
  [cyan]read[/cyan] <uuid>                   Read a characteristic value
  [cyan]write[/cyan] <uuid> <hex_bytes>      Write hex bytes to a characteristic
  [cyan]notify[/cyan] <uuid> [seconds]       Subscribe to notifications (default: 30s)
  [cyan]notify-all[/cyan] [seconds]          Subscribe to ALL notifiable characteristics
  [cyan]fuzz[/cyan] <uuid> [strategy]        Fuzz a writable char (sequential|random|common)
  [cyan]log[/cyan]                           Show session log summary
  [cyan]export[/cyan] [filename]             Export session log to JSON
  [cyan]help[/cyan]                          Show this help
  [cyan]quit[/cyan]                          Exit
"""


class BentoCommander:
    def __init__(self):
        self.client: BleakClient | None = None
        self.address: str | None = None
        self.session_log: list[dict] = []
        self.known_uuids: list[str] = []
        self.scan_results: list = []
        self.active_notifies: set[str] = set()

    def log_entry(self, command: str, result: str, data: dict | None = None):
        entry = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "command": command,
            "result": result,
        }
        if data:
            entry["data"] = data
        self.session_log.append(entry)

    @property
    def is_connected(self) -> bool:
        return self.client is not None and self.client.is_connected

    async def cmd_scan(self, args: str):
        """Scan for BLE devices."""
        duration = 5
        parts = args.split()
        if parts:
            with contextlib.suppress(ValueError):
                duration = int(parts[0])

        console.print(f"[dim]Scanning for {duration}s...[/dim]")
        devices = await BleakScanner.discover(timeout=duration)
        self.scan_results = sorted(devices, key=lambda d: d.rssi or -999, reverse=True)

        table = Table(title=f"BLE Devices Found ({len(self.scan_results)})")
        table.add_column("#", style="dim", width=4)
        table.add_column("Name", style="cyan")
        table.add_column("Address", style="white")
        table.add_column("RSSI", style="yellow", justify="right")

        for i, d in enumerate(self.scan_results):
            name = d.name or "[unnamed]"
            style = "bold green" if "bento" in (name or "").lower() else ""
            table.add_row(str(i), name, d.address, str(d.rssi or "?"), style=style)

        console.print(table)
        self.log_entry("scan", f"Found {len(self.scan_results)} devices")

    async def cmd_connect(self, args: str):
        """Connect to a device by address or scan index."""
        if not args:
            console.print("[red]Usage: connect <address|index>[/red]")
            return

        target = args.strip()
        # Check if it's an index from scan results
        try:
            idx = int(target)
            if 0 <= idx < len(self.scan_results):
                target = self.scan_results[idx].address
                name = self.scan_results[idx].name or "unnamed"
                console.print(f"[dim]Connecting to #{idx}: {name} ({target})[/dim]")
            else:
                console.print(f"[red]Index {idx} out of range[/red]")
                return
        except ValueError:
            pass

        if self.is_connected:
            await self.cmd_disconnect("")

        try:
            self.client = BleakClient(target)
            await self.client.connect()
            self.address = target
            console.print(f"[green]Connected to {target}[/green]")
            self.log_entry("connect", f"Connected to {target}")

            # Auto-enumerate UUIDs for tab completion
            self.known_uuids = []
            for service in self.client.services:
                for char in service.characteristics:
                    self.known_uuids.append(str(char.uuid))

        except BleakError as e:
            console.print(f"[red]Connection failed: {e}[/red]")
            self.client = None

    async def cmd_disconnect(self, _args: str):
        """Disconnect from current device."""
        if self.is_connected:
            # Stop any active notifications
            for uuid in list(self.active_notifies):
                with contextlib.suppress(BleakError):
                    await self.client.stop_notify(uuid)
            self.active_notifies.clear()

            await self.client.disconnect()
            console.print(f"[yellow]Disconnected from {self.address}[/yellow]")
            self.log_entry("disconnect", f"Disconnected from {self.address}")
        self.client = None
        self.address = None

    async def cmd_services(self, _args: str):
        """List all GATT services and characteristics."""
        if not self.is_connected:
            console.print("[red]Not connected. Use 'connect' first.[/red]")
            return

        tree = Tree("[bold]GATT Profile[/bold]")
        for service in self.client.services:
            svc_name = lookup_uuid(str(service.uuid))
            svc_branch = tree.add(f"[bold cyan]{service.uuid}[/bold cyan] — {svc_name}")
            for char in service.characteristics:
                char_name = lookup_uuid(str(char.uuid))
                prop_colors = []
                if "read" in char.properties:
                    prop_colors.append("[green]read[/green]")
                if "write" in char.properties:
                    prop_colors.append("[yellow]write[/yellow]")
                if "write-without-response" in char.properties:
                    prop_colors.append("[yellow]write-nr[/yellow]")
                if "notify" in char.properties:
                    prop_colors.append("[magenta]notify[/magenta]")
                if "indicate" in char.properties:
                    prop_colors.append("[magenta]indicate[/magenta]")

                svc_branch.add(
                    f"[white]{char.uuid}[/white] — {char_name}\n"
                    f"  Properties: {', '.join(prop_colors)}"
                )

        console.print(tree)
        self.log_entry("services", f"Enumerated {len(list(self.client.services))} services")

    async def cmd_read(self, args: str):
        """Read a characteristic value."""
        if not self.is_connected:
            console.print("[red]Not connected.[/red]")
            return
        if not args:
            console.print("[red]Usage: read <uuid>[/red]")
            return

        uuid = args.strip()
        try:
            data = await self.client.read_gatt_char(uuid)
            hex_str = data.hex()
            decimal = list(data)
            decoded = data.decode("ascii", errors="replace")
            ascii_str = "".join(c if c.isprintable() else "." for c in decoded)

            console.print(
                Panel(
                    f"[bold]Hex:[/bold]     {hex_str}\n"
                    f"[bold]Decimal:[/bold] {decimal}\n"
                    f"[bold]ASCII:[/bold]   {ascii_str}\n"
                    f"[bold]Length:[/bold]  {len(data)} bytes",
                    title=f"Read {uuid}",
                    border_style="green",
                )
            )
            self.log_entry("read", uuid, {"hex": hex_str, "decimal": decimal, "ascii": ascii_str})

        except BleakError as e:
            console.print(f"[red]Read failed: {e}[/red]")
        except TimeoutError:
            console.print(f"[red]Read timed out for {uuid}[/red]")

    async def cmd_write(self, args: str):
        """Write hex bytes to a characteristic."""
        if not self.is_connected:
            console.print("[red]Not connected.[/red]")
            return

        parts = args.strip().split(maxsplit=1)
        if len(parts) < 2:
            console.print("[red]Usage: write <uuid> <hex_bytes>[/red]")
            console.print("[dim]Example: write 0000fff1-... 01020304[/dim]")
            return

        uuid = parts[0]
        try:
            hex_str = parts[1].replace(" ", "").replace("0x", "")
            data = bytes.fromhex(hex_str)
        except ValueError:
            console.print("[red]Invalid hex bytes[/red]")
            return

        try:
            await self.client.write_gatt_char(uuid, data)
            console.print(f"[green]Wrote {len(data)} bytes to {uuid}: {data.hex()}[/green]")
            self.log_entry("write", uuid, {"hex": data.hex(), "length": len(data)})
        except BleakError as e:
            console.print(f"[red]Write failed: {e}[/red]")

    async def cmd_notify(self, args: str):
        """Subscribe to notifications from a characteristic."""
        if not self.is_connected:
            console.print("[red]Not connected.[/red]")
            return

        parts = args.strip().split()
        if not parts:
            console.print("[red]Usage: notify <uuid> [seconds][/red]")
            return

        uuid = parts[0]
        duration = int(parts[1]) if len(parts) > 1 else 30

        events = []

        def callback(_sender, data: bytearray):
            now = datetime.now(tz=UTC)
            hex_str = data.hex()
            events.append({"time": now.isoformat(), "hex": hex_str})
            decoded = data.decode("ascii", errors="replace")
            ascii_str = "".join(c if c.isprintable() else "." for c in decoded)
            console.print(
                f"[dim]{now.strftime('%H:%M:%S.%f')[:-3]}[/dim] "
                f"[cyan]{uuid[:23]}[/cyan] "
                f"hex=[white]{hex_str}[/white] "
                f"ascii=[dim]{ascii_str}[/dim]"
            )

        try:
            await self.client.start_notify(uuid, callback)
            self.active_notifies.add(uuid)
            console.print(f"[green]Subscribed to {uuid} for {duration}s[/green]")

            await asyncio.sleep(duration)

            await self.client.stop_notify(uuid)
            self.active_notifies.discard(uuid)
            n = len(events)
            console.print(f"[yellow]Unsubscribed from {uuid}. Got {n} notifications.[/yellow]")
            self.log_entry(
                "notify",
                uuid,
                {"duration": duration, "event_count": n, "events": events},
            )

        except BleakError as e:
            console.print(f"[red]Notify failed: {e}[/red]")

    async def cmd_notify_all(self, args: str):
        """Subscribe to ALL notifiable characteristics."""
        if not self.is_connected:
            console.print("[red]Not connected.[/red]")
            return

        duration = 30
        parts = args.strip().split()
        if parts:
            with contextlib.suppress(ValueError):
                duration = int(parts[0])

        notifiable = []
        for service in self.client.services:
            for char in service.characteristics:
                if "notify" in char.properties or "indicate" in char.properties:
                    notifiable.append(char)

        if not notifiable:
            console.print("[yellow]No notifiable characteristics found.[/yellow]")
            return

        console.print(
            f"[bold]Subscribing to {len(notifiable)} characteristics for {duration}s...[/bold]"
        )

        events = []

        def make_cb(char_uuid):
            def callback(_sender, data: bytearray):
                now = datetime.now(tz=UTC)
                hex_str = data.hex()
                events.append({"time": now.isoformat(), "uuid": char_uuid, "hex": hex_str})
                console.print(
                    f"[dim]{now.strftime('%H:%M:%S.%f')[:-3]}[/dim] "
                    f"[cyan]{char_uuid[:23]}[/cyan] "
                    f"[white]{hex_str}[/white]"
                )

            return callback

        for char in notifiable:
            uuid_str = str(char.uuid)
            try:
                await self.client.start_notify(char, make_cb(uuid_str))
                self.active_notifies.add(uuid_str)
            except BleakError as e:
                console.print(f"  [red]Failed {uuid_str}: {e}[/red]")

        await asyncio.sleep(duration)

        for char in notifiable:
            try:
                await self.client.stop_notify(char)
                self.active_notifies.discard(str(char.uuid))
            except BleakError:
                pass

        console.print(f"[yellow]Got {len(events)} total notifications.[/yellow]")
        self.log_entry("notify-all", "all", {"duration": duration, "event_count": len(events)})

    async def cmd_fuzz(self, args: str):
        """Fuzz a writable characteristic with test payloads."""
        if not self.is_connected:
            console.print("[red]Not connected.[/red]")
            return

        parts = args.strip().split()
        if not parts:
            console.print("[red]Usage: fuzz <uuid> [sequential|random|common][/red]")
            return

        uuid = parts[0]
        strategy = parts[1] if len(parts) > 1 else "sequential"

        console.print(
            Panel(
                "[bold red]WARNING:[/bold red] Fuzzing sends arbitrary data to the device.\n"
                "This could cause unexpected behavior, crashes, or damage.\n"
                "Ensure you understand the risks before proceeding.",
                title="Fuzz Confirmation",
                border_style="red",
            )
        )

        session = PromptSession()
        confirm = await session.prompt_async("Continue? [y/N] ")
        if confirm.lower() != "y":
            console.print("[dim]Aborted.[/dim]")
            return

        results = []
        console.print(f"[bold]Fuzzing {uuid} with strategy: {strategy}[/bold]")

        if strategy == "sequential":
            # Send single bytes 0x00 through 0xFF
            for i in range(256):
                data = bytes([i])
                try:
                    await self.client.write_gatt_char(uuid, data, response=True)
                    console.print(f"  [green]0x{i:02x}[/green] — accepted")
                    results.append({"byte": f"0x{i:02x}", "status": "accepted"})
                except BleakError as e:
                    console.print(f"  [red]0x{i:02x}[/red] — {e}")
                    results.append({"byte": f"0x{i:02x}", "status": str(e)})
                await asyncio.sleep(0.1)

        elif strategy == "common":
            # Common command patterns for embedded devices
            payloads = [
                b"\x00",
                b"\x01",
                b"\x02",
                b"\x03",
                b"\xff",
                b"\x00\x00",
                b"\x01\x00",
                b"\x00\x01",
                b"\xff\xff",
                b"\x01\x01",
                b"\x01\x02",
                b"\x01\x03",
                b"\x02\x01",
                b"\x10\x00",
                b"\x10\x01",
                b"\x00" * 4,
                b"\x00" * 8,
                b"\x00" * 16,
                b"\x00" * 20,
            ]
            for data in payloads:
                try:
                    await self.client.write_gatt_char(uuid, data, response=True)
                    console.print(f"  [green]{data.hex()}[/green] — accepted")
                    results.append({"hex": data.hex(), "status": "accepted"})
                except BleakError as e:
                    console.print(f"  [red]{data.hex()}[/red] — {e}")
                    results.append({"hex": data.hex(), "status": str(e)})
                await asyncio.sleep(0.1)

        elif strategy == "random":
            import os

            for length in [1, 2, 4, 8, 16, 20]:
                for _ in range(5):
                    data = os.urandom(length)
                    try:
                        await self.client.write_gatt_char(uuid, data, response=True)
                        console.print(f"  [green]{data.hex()}[/green] ({length}B) — accepted")
                        results.append({"hex": data.hex(), "len": length, "status": "accepted"})
                    except BleakError as e:
                        console.print(f"  [red]{data.hex()}[/red] ({length}B) — {e}")
                        results.append({"hex": data.hex(), "len": length, "status": str(e)})
                    await asyncio.sleep(0.1)

        console.print(f"[bold]Fuzz complete. {len(results)} payloads tested.[/bold]")
        self.log_entry("fuzz", uuid, {"strategy": strategy, "results": results})

    async def cmd_log(self, _args: str):
        """Show session log summary."""
        if not self.session_log:
            console.print("[dim]No log entries yet.[/dim]")
            return

        table = Table(title=f"Session Log ({len(self.session_log)} entries)")
        table.add_column("Time", style="dim", width=12)
        table.add_column("Command", style="cyan")
        table.add_column("Result", style="white")

        for entry in self.session_log[-20:]:
            time_str = entry["timestamp"][11:23]
            table.add_row(time_str, entry["command"], entry["result"][:60])

        console.print(table)

    async def cmd_export(self, args: str):
        """Export session log to JSON."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = args.strip() or f"session_{ts}.json"
        path = Path("captures/ble") / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.session_log, indent=2))
        console.print(f"[green]Exported {len(self.session_log)} entries to {path}[/green]")

    async def run(self):
        """Main REPL loop."""
        commands = {
            "scan": self.cmd_scan,
            "connect": self.cmd_connect,
            "disconnect": self.cmd_disconnect,
            "services": self.cmd_services,
            "read": self.cmd_read,
            "write": self.cmd_write,
            "notify": self.cmd_notify,
            "notify-all": self.cmd_notify_all,
            "fuzz": self.cmd_fuzz,
            "log": self.cmd_log,
            "export": self.cmd_export,
        }

        completer = WordCompleter(
            list(commands.keys()) + ["help", "quit", "exit"],
            ignore_case=True,
        )

        session = PromptSession(completer=completer)
        console.print(Panel(HELP_TEXT, title="BLE Commander", border_style="blue"))

        while True:
            try:
                prompt_text = f"[{self.address[:17]}] > " if self.is_connected else "ble> "

                with patch_stdout():
                    user_input = await session.prompt_async(prompt_text)

                user_input = user_input.strip()
                if not user_input:
                    continue

                parts = user_input.split(maxsplit=1)
                cmd = parts[0].lower()
                cmd_args = parts[1] if len(parts) > 1 else ""

                if cmd in ("quit", "exit"):
                    if self.is_connected:
                        await self.cmd_disconnect("")
                    break
                elif cmd == "help":
                    console.print(HELP_TEXT)
                elif cmd in commands:
                    await commands[cmd](cmd_args)
                else:
                    console.print(f"[red]Unknown command: {cmd}. Type 'help' for usage.[/red]")

            except KeyboardInterrupt:
                console.print("\n[dim]Use 'quit' to exit[/dim]")
            except EOFError:
                break

        # Auto-export on exit
        if self.session_log:
            await self.cmd_export("")


async def async_main(device: str | None = None):
    commander = BentoCommander()
    if device:
        await commander.cmd_connect(device)
    await commander.run()


def main():
    parser = argparse.ArgumentParser(description="Interactive BLE command REPL")
    parser.add_argument(
        "--device",
        help="Auto-connect to this BLE device address on startup",
    )
    args = parser.parse_args()
    asyncio.run(async_main(args.device))


if __name__ == "__main__":
    main()
