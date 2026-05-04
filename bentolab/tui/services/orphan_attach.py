"""Detect and attach to a CLI- or externally-started PCR run.

A run started outside this TUI process — e.g. by ``bentolab run`` in
another shell, or by a previous TUI session that was killed — leaves
an open NDJSON log under ``$DATA/runs/``. The first event captures
the profile, and ``session_start`` carries the wall-clock start time.

This module reads those logs and reconstructs ``(profile, started_at)``
so the StatusPane and ProgramDiagram can show full stage detail
without the operator having to "re-create" the run inside the TUI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..._data_dirs import runs_dir
from ...models import PCRProfile


@dataclass
class ActiveRun:
    profile: PCRProfile
    started_at: datetime
    log_path: Path


def find_active_run(
    *,
    root: Path | None = None,
    now: datetime | None = None,
    max_age_hours: float = 12.0,
) -> ActiveRun | None:
    """Return the most-recent in-flight run, or ``None``.

    "In flight" = the NDJSON file's last event isn't ``run_finished``,
    and ``session_start`` is within ``max_age_hours`` of ``now`` (default
    12 h, longer than any reasonable PCR program).
    """
    base = root or runs_dir()
    cutoff = (now or datetime.now(UTC)) - timedelta(hours=max_age_hours)
    for path in sorted(base.glob("*.jsonl"), reverse=True):
        active = _try_parse(path, cutoff)
        if active is not None:
            return active
    return None


def _try_parse(path: Path, cutoff: datetime) -> ActiveRun | None:
    rows = _read_rows(path)
    if rows is None:
        return None
    summary = _summarize_rows(rows)
    started = summary["started"]
    if started is None or started < cutoff:
        return None
    # `run_finished` is the only signal that the device-side run is done.
    # `session_end` only means our logger closed (e.g. CLI --no-tail) —
    # the device may still be cycling, so don't treat it as terminated.
    if summary["last_event"] == "run_finished":
        return None
    if summary["profile_dict"] is None:
        return None
    try:
        profile = PCRProfile.from_dict(summary["profile_dict"])
    except (ValueError, TypeError, KeyError):
        return None
    return ActiveRun(profile=profile, started_at=started, log_path=path)


def _read_rows(path: Path) -> list[dict] | None:
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    rows: list[dict] = []
    for raw in raw_lines:
        if not raw.strip():
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return rows


def _summarize_rows(rows: list[dict]) -> dict:
    started: datetime | None = None
    profile_dict: dict | None = None
    last_event = ""
    last_type = ""
    for entry in rows:
        last_type = entry.get("type", "")
        if last_type == "session_start":
            started = _parse_iso(entry.get("start_time", ""))
        elif last_type == "event":
            last_event = entry.get("event", "")
            if last_event == "run_config":
                cfg = entry.get("data") or {}
                if isinstance(cfg.get("profile"), dict):
                    profile_dict = cfg["profile"]
    return {
        "started": started,
        "profile_dict": profile_dict,
        "last_event": last_event,
        "last_type": last_type,
    }


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
