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
