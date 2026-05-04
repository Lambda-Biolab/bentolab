"""Tests for atomic write + .bak recovery."""

from __future__ import annotations

from pathlib import Path

from bentolab._store import atomic_write_text, load_with_backup


def test_atomic_write_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    atomic_write_text(target, "hello")
    assert target.read_text() == "hello"


def test_atomic_write_creates_bak_on_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    atomic_write_text(target, "v1")
    atomic_write_text(target, "v2")
    assert target.read_text() == "v2"
    assert (tmp_path / "x.txt.bak").read_text() == "v1"


def test_load_with_backup_prefers_primary(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    atomic_write_text(target, "v1")
    atomic_write_text(target, "v2")
    data, source = load_with_backup(target)
    assert source == "primary"
    assert data == b"v2"


def test_load_with_backup_falls_back(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    atomic_write_text(target, "v1")
    atomic_write_text(target, "v2")
    target.unlink()
    data, source = load_with_backup(target)
    assert source == "backup"
    assert data == b"v1"


def test_load_with_backup_missing(tmp_path: Path) -> None:
    data, source = load_with_backup(tmp_path / "nope.txt")
    assert source == "missing"
    assert data == b""


def test_atomic_write_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deep" / "x.txt"
    atomic_write_text(target, "data")
    assert target.read_text() == "data"
