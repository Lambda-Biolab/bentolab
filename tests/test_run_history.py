"""Tests for run-history NDJSON summarization."""

from __future__ import annotations

import json
from pathlib import Path

from bentolab.tui.services.run_history import load_history


def _write_ndjson(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_complete_run(tmp_path: Path) -> None:
    p = tmp_path / "20260504T024052_HF.jsonl"
    _write_ndjson(
        p,
        [
            {"type": "session_start", "session": "HF", "start_time": "2026-05-04T02:40:52Z"},
            {"type": "event", "event": "run_started"},
            {"type": "event", "event": "run_progress", "data": {"progress": 50}},
            {"type": "event", "event": "run_finished", "data": {"success": True}},
        ],
    )
    [entry] = load_history(root=tmp_path)
    assert entry.profile == "HF"
    assert entry.status == "complete"


def test_orphan_run(tmp_path: Path) -> None:
    p = tmp_path / "20260504T024052_orphan.jsonl"
    _write_ndjson(
        p,
        [
            {"type": "session_start", "session": "orphan"},
            {"type": "event", "event": "run_started"},
            {"type": "event", "event": "run_progress", "data": {"progress": 10}},
        ],
    )
    [entry] = load_history(root=tmp_path)
    assert entry.status == "orphan"


def test_error_run(tmp_path: Path) -> None:
    p = tmp_path / "20260504T024052_err.jsonl"
    _write_ndjson(
        p,
        [
            {"type": "session_start", "session": "err"},
            {"type": "event", "event": "run_started"},
            {"type": "event", "event": "run_finished", "data": {"success": False}},
        ],
    )
    [entry] = load_history(root=tmp_path)
    assert entry.status == "error"


def test_empty_dir(tmp_path: Path) -> None:
    assert load_history(root=tmp_path) == []


def test_unknown_status_no_run_events(tmp_path: Path) -> None:
    """NDJSON with only ``session_start`` (no run_* events) classifies as 'unknown'."""
    p = tmp_path / "20260504T024052_unknown.jsonl"
    _write_ndjson(
        p,
        [
            {"type": "session_start", "session": "unknown"},
            {"type": "info", "message": "stopped before any run"},
        ],
    )
    [entry] = load_history(root=tmp_path)
    assert entry.status == "unknown"


def test_limit_caps_returned_entries(tmp_path: Path) -> None:
    """``limit`` caps the number of returned history entries (newest-first)."""
    (tmp_path / "20260101T000001_old.jsonl").write_text(
        '{"type": "session_start", "session": "old"}\n', encoding="utf-8"
    )
    (tmp_path / "20260102T000002_mid.jsonl").write_text(
        '{"type": "session_start", "session": "mid"}\n', encoding="utf-8"
    )
    (tmp_path / "20260103T000003_new.jsonl").write_text(
        '{"type": "session_start", "session": "new"}\n', encoding="utf-8"
    )
    entries = load_history(root=tmp_path, limit=2)
    assert len(entries) == 2
    profiles = {e.profile for e in entries}
    assert "new" in profiles
    assert "mid" in profiles
    assert "old" not in profiles  # limited out


def test_unreadable_file_classifies_as_unknown(tmp_path: Path) -> None:
    """A file that can't be read returns 'unknown' — robust against logfile corruption."""
    target = tmp_path / "20260504T024052_unreadable.jsonl"
    target.mkdir()  # directory at the .jsonl path → text-read raises OSError
    [entry] = load_history(root=tmp_path)
    assert entry.status == "unknown"
