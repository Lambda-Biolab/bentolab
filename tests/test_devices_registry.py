"""Tests for the on-disk device registry."""

from __future__ import annotations

from pathlib import Path

from bentolab import devices


def test_remember_and_list(tmp_path: Path) -> None:
    path = tmp_path / "devices.json"
    devices.remember(devices.Device(address="AA:BB", name="Bento 1"), path=path)
    devices.remember(devices.Device(address="CC:DD", name="Bento 2"), path=path)
    found = {d.address: d.name for d in devices.list_devices(path=path)}
    assert found == {"AA:BB": "Bento 1", "CC:DD": "Bento 2"}


def test_remember_updates_existing(tmp_path: Path) -> None:
    path = tmp_path / "devices.json"
    devices.remember(devices.Device(address="AA:BB", name="old"), path=path)
    devices.remember(devices.Device(address="AA:BB", name="new"), path=path)
    listed = devices.list_devices(path=path)
    assert len(listed) == 1
    assert listed[0].name == "new"


def test_forget(tmp_path: Path) -> None:
    path = tmp_path / "devices.json"
    devices.remember(devices.Device(address="AA:BB"), path=path)
    devices.forget("AA:BB", path=path)
    assert devices.list_devices(path=path) == []


def test_list_missing_returns_empty(tmp_path: Path) -> None:
    assert devices.list_devices(path=tmp_path / "missing.json") == []
