"""Run-history reader — surfaces past runs from NDJSON files on disk."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..._data_dirs import runs_dir


@dataclass
class HistoryEntry:
    path: Path
    started: str
    profile: str
    status: str  # "complete", "running", "orphan", "error", "unknown"


def load_history(*, root: Path | None = None, limit: int = 25) -> list[HistoryEntry]:
    """Return the most-recent ``limit`` runs, newest first.

    Status is derived from the *last* event in the NDJSON file:
    ``run_finished`` → ``complete``/``error`` (depending on payload),
    ``run_progress`` or ``run_started`` → ``orphan`` (process died
    before completion was logged), anything else → ``unknown``.
    """
    base = root or runs_dir()
    files = sorted(base.glob("*.jsonl"), reverse=True)[:limit]
    out: list[HistoryEntry] = []
    for path in files:
        out.append(_summarize(path))
    return out


def _summarize(path: Path) -> HistoryEntry:
    profile = path.stem
    try:
        rows = list(_iter_rows(path))
    except OSError:
        return HistoryEntry(path=path, started="", profile=profile, status="unknown")

    started, profile = _extract_header(rows, fallback=profile)
    status = _classify(rows)
    return HistoryEntry(path=path, started=started, profile=profile, status=status)


def _iter_rows(path: Path):
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue


def _extract_header(rows: list[dict], *, fallback: str) -> tuple[str, str]:
    for entry in rows:
        if entry.get("type") == "session_start":
            return entry.get("start_time", ""), entry.get("session", fallback)
    return "", fallback


def _classify(rows: list[dict]) -> str:
    last_event = ""
    success = False
    for entry in rows:
        if entry.get("type") != "event":
            continue
        last_event = entry.get("event", "")
        if last_event == "run_finished":
            success = bool(entry.get("data", {}).get("success", False))
    if last_event == "run_finished":
        return "complete" if success else "error"
    if last_event in {"run_started", "run_progress"}:
        return "orphan"
    return "unknown"
