"""Regression test: BentoLabBLE.connect refreshes stale BLE addresses.

macOS's CoreBluetooth rotates the random address used for the
peripheral. Passing the cached address straight to BleakClient fails
with "device not found" even though the unit is advertising; the fix
is to look the address up via BleakScanner first and fall back to a
broader scan.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bentolab.ble_client import BentoLabBLE, BentoLabConnectionError


class _FakeDevice:
    def __init__(self, address: str, name: str = "Bento Lab 4A23") -> None:
        self.address = address
        self.name = name


class _FakeAdv:
    service_uuids: list[str] = []


class _FakeClient:
    """Mimics BleakClient for the connect path."""

    last_target: Any = None

    def __init__(self, target: Any, **_kwargs: Any) -> None:
        type(self).last_target = target
        self.is_connected = False

    async def connect(self) -> None:
        self.is_connected = True

    async def start_notify(self, _uuid: str, _cb: Any) -> None:
        return None

    async def write_gatt_char(self, _uuid: str, _data: bytes, **_kw: Any) -> None:
        return None

    async def disconnect(self) -> None:
        self.is_connected = False


async def test_connect_uses_live_bledevice_when_address_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _FakeDevice("AA:BB:CC:DD:EE:FF")

    async def fake_find(address: str, timeout: float = 10.0) -> Any:
        assert address == "AA:BB:CC:DD:EE:FF"
        return live

    monkeypatch.setattr("bentolab.ble_client.BleakScanner.find_device_by_address", fake_find)
    monkeypatch.setattr("bentolab.ble_client.BleakClient", _FakeClient)

    lab = BentoLabBLE()
    await lab.connect("AA:BB:CC:DD:EE:FF")
    # The fresh BLEDevice should have been handed to BleakClient, not the raw string.
    assert _FakeClient.last_target is live
    assert lab._connected_address == "AA:BB:CC:DD:EE:FF"


async def test_connect_falls_back_to_scan_when_address_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    new_device = _FakeDevice("11:22:33:44:55:66")

    async def fake_find(_address: str, timeout: float = 10.0) -> Any:
        return None  # stale — not advertising under that address

    async def fake_discover(self: Any, timeout: float = 10.0) -> list[tuple[Any, Any]]:
        return [(new_device, _FakeAdv())]

    monkeypatch.setattr("bentolab.ble_client.BleakScanner.find_device_by_address", fake_find)
    monkeypatch.setattr(BentoLabBLE, "discover", fake_discover)
    monkeypatch.setattr("bentolab.ble_client.BleakClient", _FakeClient)

    lab = BentoLabBLE()
    await lab.connect("AA:BB:CC:DD:EE:FF")
    # We connected to the freshly-discovered device, not the stale string.
    assert _FakeClient.last_target is new_device
    assert lab._connected_address == "11:22:33:44:55:66"


async def test_connect_raises_when_no_device_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_find(_address: str, timeout: float = 10.0) -> Any:
        return None

    async def fake_discover(self: Any, timeout: float = 10.0) -> list:
        return []

    monkeypatch.setattr("bentolab.ble_client.BleakScanner.find_device_by_address", fake_find)
    monkeypatch.setattr(BentoLabBLE, "discover", fake_discover)

    lab = BentoLabBLE()
    with pytest.raises(BentoLabConnectionError, match="No Bento Lab"):
        await lab.connect("AA:BB:CC:DD:EE:FF")


# Suppress pytest unused-import lint
_ = SimpleNamespace
