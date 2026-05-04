"""Tests for SessionLogger NDJSON output."""

from __future__ import annotations

import json
from pathlib import Path

from bentolab._logging import SessionLogger


def test_session_logger_writes_start_and_end(tmp_path: Path) -> None:
    with SessionLogger("test_run", log_dir=tmp_path) as log:
        log.info("hello")
        log.event("custom", {"k": "v"})

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert rows[0]["type"] == "session_start"
    assert rows[1]["type"] == "info"
    assert rows[2]["type"] == "event"
    assert rows[2]["event"] == "custom"
    assert rows[-1]["type"] == "session_end"


def test_session_logger_slug(tmp_path: Path) -> None:
    with SessionLogger("S4 6.9kb / spaces!", log_dir=tmp_path):
        pass
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    assert "/" not in files[0].name
    assert " " not in files[0].name
