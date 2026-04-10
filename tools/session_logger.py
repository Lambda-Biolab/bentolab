#!/usr/bin/env python3
"""Centralized session logger for all Bento Lab RE tools.

Provides structured logging to both terminal (rich) and file (JSON lines).
All tools should import and use this logger for consistent capture.

Usage:
    from session_logger import SessionLogger
    log = SessionLogger("ble_scan")
    log.event("discovery", {"device": "BentoLab", "rssi": -45})
    log.info("Connected to device")
    log.raw_bytes("nus_tx", b"\\x01\\x02\\x03")
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

console = Console()

LOG_DIR = Path(__file__).resolve().parent.parent / "captures" / "sessions"


class SessionLogger:
    """Structured logger that writes JSON lines to a session file."""

    def __init__(self, session_name: str, log_dir: Path | None = None):
        self.session_name = session_name
        self.log_dir = log_dir or LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"{ts}_{session_name}.jsonl"
        self.start_time = datetime.now(tz=UTC)
        self._count = 0

        # Write session header
        self._write(
            {
                "type": "session_start",
                "session": session_name,
                "start_time": self.start_time.isoformat(),
                "python": sys.version,
            }
        )
        console.print(f"[dim]Session log: {self.log_file}[/dim]")

    def _write(self, entry: dict) -> None:
        entry["_ts"] = datetime.now(tz=UTC).isoformat()
        entry["_seq"] = self._count
        self._count += 1
        with open(self.log_file, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def event(self, event_type: str, data: dict | None = None) -> None:
        """Log a structured event."""
        entry = {"type": "event", "event": event_type}
        if data:
            entry["data"] = data
        self._write(entry)

    def info(self, message: str) -> None:
        """Log an informational message."""
        self._write({"type": "info", "message": message})
        console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] {message}")

    def warning(self, message: str) -> None:
        """Log a warning."""
        self._write({"type": "warning", "message": message})
        console.print(
            f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] [yellow]{message}[/yellow]"
        )

    def error(self, message: str) -> None:
        """Log an error."""
        self._write({"type": "error", "message": message})
        console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] [red]{message}[/red]")

    def raw_bytes(self, channel: str, data: bytes, direction: str = "rx") -> None:
        """Log raw bytes from a BLE characteristic or TCP stream."""
        self._write(
            {
                "type": "raw",
                "channel": channel,
                "direction": direction,
                "hex": data.hex(),
                "bytes": list(data),
                "length": len(data),
            }
        )

    def ble_notification(self, uuid: str, data: bytes) -> None:
        """Log a BLE notification."""
        ascii_attempt = "".join(
            c if c.isprintable() else "." for c in data.decode("ascii", errors="replace")
        )
        self._write(
            {
                "type": "ble_notify",
                "uuid": uuid,
                "hex": data.hex(),
                "ascii": ascii_attempt,
                "bytes": list(data),
                "length": len(data),
            }
        )
        console.print(
            f"[dim]{datetime.now().strftime('%H:%M:%S.%f')[:-3]}[/dim] "
            f"[cyan]{uuid[:23]}[/cyan] "
            f"[white]{data.hex()}[/white] "
            f"[dim]{ascii_attempt}[/dim]"
        )

    def ble_write(self, uuid: str, data: bytes) -> None:
        """Log a BLE write command."""
        self._write(
            {
                "type": "ble_write",
                "uuid": uuid,
                "hex": data.hex(),
                "bytes": list(data),
                "length": len(data),
            }
        )

    def http_request(
        self, method: str, url: str, status: int | None = None, body: str | None = None
    ) -> None:
        """Log an HTTP request/response."""
        self._write(
            {
                "type": "http",
                "method": method,
                "url": url,
                "status": status,
                "body_snippet": body[:500] if body else None,
            }
        )

    def close(self) -> None:
        """Write session end marker."""
        self._write(
            {
                "type": "session_end",
                "duration_seconds": (datetime.now(tz=UTC) - self.start_time).total_seconds(),
                "total_events": self._count,
            }
        )
        console.print(f"[bold]Session saved: {self.log_file} ({self._count} events)[/bold]")
