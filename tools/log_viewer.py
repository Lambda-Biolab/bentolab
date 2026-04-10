#!/usr/bin/env python3
"""Real-time log viewer for Bento Lab RE sessions.

Tails session log files (JSONL format) with rich formatting.
Can view live sessions or replay completed ones.

Usage:
    # View the latest session in real-time
    python tools/log_viewer.py

    # View a specific session file
    python tools/log_viewer.py captures/sessions/20260410_123456_ble_scan.jsonl

    # View only BLE notifications
    python tools/log_viewer.py --filter ble_notify

    # List all sessions
    python tools/log_viewer.py --list
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()

SESSION_DIR = Path(__file__).resolve().parent.parent / "captures" / "sessions"

# Color scheme for event types
TYPE_STYLES = {
    "session_start": "bold blue",
    "session_end": "bold blue",
    "info": "white",
    "warning": "yellow",
    "error": "bold red",
    "event": "green",
    "raw": "cyan",
    "ble_notify": "magenta",
    "ble_write": "yellow",
    "http": "cyan",
}


def format_entry(entry: dict) -> str:
    """Format a single log entry for display."""
    ts = entry.get("_ts", "")
    if ts:
        ts = ts[11:23]  # HH:MM:SS.mmm

    event_type = entry.get("type", "unknown")
    style = TYPE_STYLES.get(event_type, "dim")
    seq = entry.get("_seq", "")

    if event_type == "session_start":
        return f"[{style}]#{seq} {ts} SESSION START: {entry.get('session', '?')}[/{style}]"

    if event_type == "session_end":
        dur = entry.get("duration_seconds", 0)
        total = entry.get("total_events", 0)
        return f"[{style}]#{seq} {ts} SESSION END: {dur:.1f}s, {total} events[/{style}]"

    if event_type == "info":
        return f"[dim]#{seq} {ts}[/dim] [{style}]{entry.get('message', '')}[/{style}]"

    if event_type == "warning":
        return f"[dim]#{seq} {ts}[/dim] [{style}]WARN: {entry.get('message', '')}[/{style}]"

    if event_type == "error":
        return f"[dim]#{seq} {ts}[/dim] [{style}]ERROR: {entry.get('message', '')}[/{style}]"

    if event_type == "ble_notify":
        uuid = entry.get("uuid", "?")[:23]
        hex_data = entry.get("hex", "")
        ascii_data = entry.get("ascii", "")
        return (
            f"[dim]#{seq} {ts}[/dim] "
            f"[{style}]BLE <<[/{style}] "
            f"[cyan]{uuid}[/cyan] "
            f"[white]{hex_data}[/white] "
            f"[dim]{ascii_data}[/dim]"
        )

    if event_type == "ble_write":
        uuid = entry.get("uuid", "?")[:23]
        hex_data = entry.get("hex", "")
        return (
            f"[dim]#{seq} {ts}[/dim] "
            f"[{style}]BLE >>[/{style}] "
            f"[cyan]{uuid}[/cyan] "
            f"[white]{hex_data}[/white]"
        )

    if event_type == "event":
        evt = entry.get("event", "?")
        data = entry.get("data", {})
        data_str = " ".join(f"{k}={v}" for k, v in data.items()) if data else ""
        return f"[dim]#{seq} {ts}[/dim] [{style}]{evt}[/{style}] [dim]{data_str[:100]}[/dim]"

    if event_type == "http":
        method = entry.get("method", "?")
        url = entry.get("url", "?")
        status = entry.get("status", "?")
        return f"[dim]#{seq} {ts}[/dim] [{style}]HTTP {method} {url} -> {status}[/{style}]"

    if event_type == "raw":
        channel = entry.get("channel", "?")
        direction = entry.get("direction", "?")
        hex_data = entry.get("hex", "")
        arrow = ">>" if direction == "tx" else "<<"
        return (
            f"[dim]#{seq} {ts}[/dim] [{style}]{channel} {arrow}[/{style}] [white]{hex_data}[/white]"
        )

    return f"[dim]#{seq} {ts}[/dim] [{style}]{event_type}: {json.dumps(entry)[:100]}[/{style}]"


def list_sessions() -> None:
    """List all available session files."""
    if not SESSION_DIR.exists():
        console.print("[yellow]No sessions directory yet.[/yellow]")
        return

    files = sorted(SESSION_DIR.glob("*.jsonl"), reverse=True)
    if not files:
        console.print("[yellow]No session files found.[/yellow]")
        return

    table = Table(title="Session Logs")
    table.add_column("File", style="cyan")
    table.add_column("Size", justify="right", style="white")
    table.add_column("Events", justify="right", style="yellow")
    table.add_column("Session", style="green")

    for f in files:
        size = f"{f.stat().st_size / 1024:.1f}KB"
        # Read first line for session name
        session = "?"
        event_count = 0
        with open(f) as fh:
            for line in fh:
                event_count += 1
                if event_count == 1:
                    try:
                        entry = json.loads(line)
                        session = entry.get("session", "?")
                    except json.JSONDecodeError:
                        pass
        table.add_row(f.name, size, str(event_count), session)

    console.print(table)


def replay_log(filepath: Path, type_filter: str | None = None) -> None:
    """Replay a completed session log file."""
    console.print(f"[bold]Replaying: {filepath.name}[/bold]\n")
    with open(filepath) as f:
        for line in f:
            try:
                entry = json.loads(line)
                if type_filter and entry.get("type") != type_filter:
                    continue
                console.print(format_entry(entry))
            except json.JSONDecodeError:
                console.print(f"[red]Bad line: {line[:80]}[/red]")


def tail_log(filepath: Path, type_filter: str | None = None) -> None:
    """Tail a live session log file (like tail -f)."""
    console.print(f"[bold]Tailing: {filepath.name}[/bold]")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    # First, replay existing content
    try:
        with open(filepath) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if type_filter and entry.get("type") != type_filter:
                        continue
                    console.print(format_entry(entry))
                except json.JSONDecodeError:
                    pass
            # Now tail for new content
            while True:
                line = f.readline()
                if line:
                    try:
                        entry = json.loads(line)
                        if type_filter and entry.get("type") != type_filter:
                            continue
                        console.print(format_entry(entry))
                    except json.JSONDecodeError:
                        pass
                else:
                    time.sleep(0.1)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


def main():
    parser = argparse.ArgumentParser(description="View Bento Lab RE session logs")
    parser.add_argument(
        "file",
        nargs="?",
        help="Session log file to view (default: latest)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all session files",
    )
    parser.add_argument(
        "--filter",
        help="Filter by event type (ble_notify, ble_write, event, info, etc.)",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Replay without tailing (don't wait for new events)",
    )
    args = parser.parse_args()

    if args.list:
        list_sessions()
        return

    if args.file:
        filepath = Path(args.file)
    else:
        # Find the latest session file
        if not SESSION_DIR.exists():
            console.print("[yellow]No sessions yet. Run a tool first.[/yellow]")
            return
        files = sorted(SESSION_DIR.glob("*.jsonl"), reverse=True)
        if not files:
            console.print("[yellow]No session files found.[/yellow]")
            return
        filepath = files[0]
        console.print(f"[dim]Using latest session: {filepath.name}[/dim]")

    if not filepath.exists():
        console.print(f"[red]File not found: {filepath}[/red]")
        return

    if args.replay:
        replay_log(filepath, args.filter)
    else:
        tail_log(filepath, args.filter)


if __name__ == "__main__":
    main()
