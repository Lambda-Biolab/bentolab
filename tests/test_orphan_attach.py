"""Tests for the orphan-run detector."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bentolab.models import CycleStep, PCRProfile, ThermalStep
from bentolab.tui.services.orphan_attach import find_active_run


def _profile() -> PCRProfile:
    return PCRProfile(
        name="orphan-demo",
        initial_denaturation=ThermalStep(95.0, 60),
        cycles=[
            CycleStep(
                denaturation=ThermalStep(98.0, 10),
                annealing=ThermalStep(60.0, 30),
                extension=ThermalStep(72.0, 60),
                repeat_count=3,
            )
        ],
        final_extension=ThermalStep(72.0, 60),
    )


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_finds_in_flight_run(tmp_path: Path) -> None:
    started = datetime.now(UTC) - timedelta(minutes=2)
    p = tmp_path / "20260504T030000_orphan.jsonl"
    _write(
        p,
        [
            {"type": "session_start", "session": "orphan-demo", "start_time": started.isoformat()},
            {"type": "event", "event": "run_config", "data": {"profile": _profile().to_dict()}},
            {"type": "event", "event": "run_started"},
            {"type": "event", "event": "run_progress", "data": {"progress": 30}},
        ],
    )
    active = find_active_run(root=tmp_path)
    assert active is not None
    assert active.profile.name == "orphan-demo"
    assert active.started_at == started


def test_skips_finished_run(tmp_path: Path) -> None:
    started = datetime.now(UTC) - timedelta(minutes=10)
    p = tmp_path / "20260504T020000_done.jsonl"
    _write(
        p,
        [
            {"type": "session_start", "session": "done", "start_time": started.isoformat()},
            {"type": "event", "event": "run_config", "data": {"profile": _profile().to_dict()}},
            {"type": "event", "event": "run_started"},
            {"type": "event", "event": "run_finished", "data": {"success": True}},
        ],
    )
    assert find_active_run(root=tmp_path) is None


def test_skips_stale_run(tmp_path: Path) -> None:
    started = datetime.now(UTC) - timedelta(hours=24)
    p = tmp_path / "20260503T010000_stale.jsonl"
    _write(
        p,
        [
            {"type": "session_start", "session": "stale", "start_time": started.isoformat()},
            {"type": "event", "event": "run_config", "data": {"profile": _profile().to_dict()}},
            {"type": "event", "event": "run_started"},
            {"type": "event", "event": "run_progress"},
        ],
    )
    assert find_active_run(root=tmp_path) is None


def test_skips_log_past_estimated_runtime(tmp_path: Path) -> None:
    """Even within max_age, an orphan whose elapsed exceeds the program
    runtime + ramp buffer can't be the run currently on the device."""
    profile = _profile()
    runtime = profile.estimated_runtime_seconds()
    started = datetime.now(UTC) - timedelta(seconds=runtime + 3600)  # 1h past buffer
    p = tmp_path / "20260504T010000_old.jsonl"
    _write(
        p,
        [
            {"type": "session_start", "session": "old", "start_time": started.isoformat()},
            {"type": "event", "event": "run_config", "data": {"profile": profile.to_dict()}},
            {"type": "event", "event": "run_started"},
        ],
    )
    assert find_active_run(root=tmp_path) is None


def test_skips_log_with_run_config_but_no_run_started(tmp_path: Path) -> None:
    """Failed-connect attempts log run_config but never reach run_started."""
    started = datetime.now(UTC) - timedelta(minutes=1)
    p = tmp_path / "20260504T030000_failed.jsonl"
    _write(
        p,
        [
            {"type": "session_start", "session": "failed", "start_time": started.isoformat()},
            {"type": "event", "event": "run_config", "data": {"profile": _profile().to_dict()}},
            {"type": "session_end", "duration_seconds": 0.2, "total_events": 2},
        ],
    )
    assert find_active_run(root=tmp_path) is None


def test_skips_run_without_config(tmp_path: Path) -> None:
    started = datetime.now(UTC)
    p = tmp_path / "20260504T030000_noconfig.jsonl"
    _write(
        p,
        [
            {"type": "session_start", "session": "x", "start_time": started.isoformat()},
            {"type": "event", "event": "run_started"},
        ],
    )
    assert find_active_run(root=tmp_path) is None


def test_picks_most_recent_when_multiple_orphans(tmp_path: Path) -> None:
    older = datetime.now(UTC) - timedelta(minutes=30)
    newer = datetime.now(UTC) - timedelta(minutes=10)
    for ts, name in [(older, "older"), (newer, "newer")]:
        path = tmp_path / f"{ts.strftime('%Y%m%dT%H%M%S')}_{name}.jsonl"
        _write(
            path,
            [
                {"type": "session_start", "session": name, "start_time": ts.isoformat()},
                {
                    "type": "event",
                    "event": "run_config",
                    "data": {"profile": {**_profile().to_dict(), "name": name}},
                },
                {"type": "event", "event": "run_started"},
                {"type": "event", "event": "run_progress"},
            ],
        )
    active = find_active_run(root=tmp_path)
    assert active is not None
    assert active.profile.name == "newer"


def test_empty_dir(tmp_path: Path) -> None:
    assert find_active_run(root=tmp_path) is None


def test_attaches_to_no_tail_run_with_session_end(tmp_path: Path) -> None:
    """CLI --no-tail closes the logger, but the device keeps running."""
    started = datetime.now(UTC) - timedelta(minutes=2)
    p = tmp_path / "20260504T030000_notail.jsonl"
    _write(
        p,
        [
            {"type": "session_start", "session": "no-tail", "start_time": started.isoformat()},
            {"type": "event", "event": "run_config", "data": {"profile": _profile().to_dict()}},
            {"type": "event", "event": "run_started", "data": {"tail": False}},
            {"type": "session_end", "duration_seconds": 1.2, "total_events": 4},
        ],
    )
    active = find_active_run(root=tmp_path)
    assert active is not None
    assert active.profile.name == "orphan-demo"
