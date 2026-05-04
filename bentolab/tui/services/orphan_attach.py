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


_RAMP_BUFFER_SECONDS = 1800.0  # 30 min headroom past nominal program runtime


def find_active_run(
    *,
    root: Path | None = None,
    now: datetime | None = None,
    max_age_hours: float = 6.0,
) -> ActiveRun | None:
    """Return the most-recent in-flight run, or ``None``.

    A candidate qualifies when the NDJSON file:
      * has a ``session_start`` within ``max_age_hours`` (default 6 h),
      * contains a ``run_config`` event so we know the profile,
      * contains a ``run_started`` event so we know the device-side
        start command actually went through (filters out failed
        connect attempts that left a stub log behind),
      * does not yet contain ``run_finished``,
      * and the elapsed time since ``session_start`` is still less than
        the profile's nominal runtime plus a 30-minute ramp buffer
        (anything older is by definition no longer the current run on
        the device, even if ``run_finished`` was never logged).
    """
    base = root or runs_dir()
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=max_age_hours)
    for path in sorted(base.glob("*.jsonl"), reverse=True):
        active = _try_parse(path, cutoff=cutoff, now=now)
        if active is not None:
            return active
    return None


def _try_parse(path: Path, *, cutoff: datetime, now: datetime) -> ActiveRun | None:
    rows = _read_rows(path)
    if rows is None:
        return None
    summary = _summarize_rows(rows)
    started = summary["started"]
    if started is None or started < cutoff:
        return None
    if summary["last_event"] == "run_finished":
        # run_finished is the only definitive end signal. session_end on
        # its own (e.g. CLI --no-tail) doesn't mean the device stopped.
        return None
    if not summary["saw_run_started"]:
        # Stub logs from failed connect attempts have run_config but
        # never reached run_started. They aren't actual in-flight runs.
        return None
    if summary["profile_dict"] is None:
        return None
    try:
        profile = PCRProfile.from_dict(summary["profile_dict"])
    except (ValueError, TypeError, KeyError):
        return None
    elapsed = (now - started).total_seconds()
    if elapsed > profile.estimated_runtime_seconds() + _RAMP_BUFFER_SECONDS:
        # Run-time math says this log can't represent the program the
        # device is currently executing. Skip it.
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
    saw_run_started = False
    for entry in rows:
        last_type = entry.get("type", "")
        if last_type == "session_start":
            started = _parse_iso(entry.get("start_time", ""))
        elif last_type == "event":
            last_event = entry.get("event", "")
            if last_event == "run_started":
                saw_run_started = True
            elif last_event == "run_config":
                cfg = entry.get("data") or {}
                if isinstance(cfg.get("profile"), dict):
                    profile_dict = cfg["profile"]
    return {
        "started": started,
        "profile_dict": profile_dict,
        "last_event": last_event,
        "last_type": last_type,
        "saw_run_started": saw_run_started,
    }


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
