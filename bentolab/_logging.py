"""Structured NDJSON session/run logging.

Promoted from ``tools/session_logger.py`` so both the CLI and the TUI can
share a single run-log format. The ``tools/`` module re-exports
:class:`SessionLogger` from here for compatibility.

A "session" log captures BLE traffic and freeform events (used by the
RE debug scripts). A "run" is a thin convenience layer on top that
adds typed helpers for PCR run state.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from ._data_dirs import runs_dir


class SessionLogger:
    """Append-only NDJSON event logger.

    Each ``_write`` call adds ``_ts`` and ``_seq`` fields. Callers do
    not need to close the logger — events flush on each write — but
    :meth:`close` should be called for an explicit ``session_end`` row.
    """

    def __init__(self, session_name: str, log_dir: Path | None = None):
        self.session_name = session_name
        self.log_dir = Path(log_dir) if log_dir else runs_dir()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        self.log_file = self.log_dir / f"{ts}_{_safe_slug(session_name)}.jsonl"
        self.start_time = datetime.now(tz=UTC)
        self._count = 0
        self._fp: IO[str] | None = open(self.log_file, "a", encoding="utf-8")  # noqa: SIM115
        self._write(
            {
                "type": "session_start",
                "session": session_name,
                "start_time": self.start_time.isoformat(),
                "python": sys.version,
            }
        )

    def _write(self, entry: dict) -> None:
        if self._fp is None:
            raise RuntimeError("SessionLogger is closed")
        entry["_ts"] = datetime.now(tz=UTC).isoformat()
        entry["_seq"] = self._count
        self._count += 1
        self._fp.write(json.dumps(entry, default=str) + "\n")
        self._fp.flush()

    def event(self, event_type: str, data: dict | None = None) -> None:
        entry: dict = {"type": "event", "event": event_type}
        if data:
            entry["data"] = data
        self._write(entry)

    def info(self, message: str) -> None:
        self._write({"type": "info", "message": message})

    def warning(self, message: str) -> None:
        self._write({"type": "warning", "message": message})

    def error(self, message: str) -> None:
        self._write({"type": "error", "message": message})

    def raw_bytes(self, channel: str, data: bytes, direction: str = "rx") -> None:
        self._write(
            {
                "type": "raw",
                "channel": channel,
                "direction": direction,
                "hex": data.hex(),
                "length": len(data),
            }
        )

    def ble_notification(self, uuid: str, data: bytes) -> None:
        self._write(
            {
                "type": "ble_notify",
                "uuid": uuid,
                "hex": data.hex(),
                "length": len(data),
            }
        )

    def ble_write(self, uuid: str, data: bytes) -> None:
        self._write(
            {
                "type": "ble_write",
                "uuid": uuid,
                "hex": data.hex(),
                "length": len(data),
            }
        )

    def http_request(
        self, method: str, url: str, status: int | None = None, body: str | None = None
    ) -> None:
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
        if self._fp is None:
            return
        self._write(
            {
                "type": "session_end",
                "duration_seconds": (datetime.now(tz=UTC) - self.start_time).total_seconds(),
                "total_events": self._count,
            }
        )
        self._fp.close()
        self._fp = None

    def __enter__(self) -> SessionLogger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _safe_slug(name: str) -> str:
    keep = "-_."
    return "".join(c if c.isalnum() or c in keep else "-" for c in name).strip("-")
