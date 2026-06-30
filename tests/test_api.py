"""Tests for the BentoLab HTTP API (no hardware required)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from bentolab.api.app import create_app
from bentolab.protocol import StatusBroadcast

# ---------------------------------------------------------------------------
# Stub BLE client for testing
# ---------------------------------------------------------------------------


class _StubDevice:
    """Minimal stand-in for a BLEDevice."""

    def __init__(self, address: str, name: str = "") -> None:
        self.address = address
        self.name = name


class StubBleClient:
    """BleClientProtocol implementation with canned responses.

    Usage::

        stub = StubBleClient()
        stub._status = StatusBroadcast(0, 0, 0, 0, 25, 24, 0)
        app = create_app(ble_client=stub)
    """

    def __init__(self) -> None:
        self._status = StatusBroadcast(0, 0, 0, 0, 25, 24, 0)
        self._devices: list[tuple[Any, Any]] = []
        self._connected = True
        self.discover_called = False
        self.get_status_called = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def discover(self, timeout: float = 10.0) -> list[tuple[Any, Any]]:
        self.discover_called = True
        return self._devices

    async def get_status(self) -> StatusBroadcast:
        self.get_status_called = True
        return self._status


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stub():
    return StubBleClient()


@pytest.fixture
def client(stub):
    app = create_app(ble_client=stub)
    return TestClient(app)


@pytest.fixture
def client_no_hw():
    """App with no BLE client at all — tests degraded paths."""
    app = create_app(ble_client=None)
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_returns_ok_with_ble(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["ble"] == "ok"
        assert data["wifi"] == "not_supported"
        assert "version" in data

    def test_health_returns_not_available_without_ble(self, client_no_hw):
        resp = client_no_hw.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["ble"] == "not_available"

    def test_health_version_matches_library(self, client):
        from bentolab import __version__

        resp = client.get("/health")
        assert resp.json()["version"] == __version__


# ---------------------------------------------------------------------------
# GET /devices
# ---------------------------------------------------------------------------


class TestDevices:
    def test_devices_empty_when_none_discovered(self, client, stub):
        stub._devices = []
        resp = client.get("/devices")
        assert resp.status_code == 200
        data = resp.json()
        assert data["devices"] == []
        assert stub.discover_called

    def test_devices_returns_discovered(self, client, stub):
        stub._devices = [
            (_StubDevice("AA:BB:CC:DD:EE:01", "Bento Lab A"), None),
            (_StubDevice("AA:BB:CC:DD:EE:02", "Bento Lab B"), None),
        ]
        resp = client.get("/devices")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["devices"]) == 2
        assert data["devices"][0]["address"] == "AA:BB:CC:DD:EE:01"
        assert data["devices"][0]["name"] == "Bento Lab A"
        assert data["devices"][0]["transport"] == "ble"
        assert data["devices"][1]["address"] == "AA:BB:CC:DD:EE:02"

    def test_devices_empty_when_no_ble_client(self, client_no_hw):
        resp = client_no_hw.get("/devices")
        assert resp.status_code == 200
        data = resp.json()
        assert data["devices"] == []

    def test_devices_handles_discover_exception(self, client, stub, monkeypatch):
        async def failing_discover(**kwargs):
            raise RuntimeError("BLE adapter unavailable")

        monkeypatch.setattr(stub, "discover", failing_discover)
        resp = client.get("/devices")
        assert resp.status_code == 200
        assert resp.json()["devices"] == []


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status_idle_when_connected_and_not_running(self, client, stub):
        stub._status = StatusBroadcast(0, 0, 0, 0, 25, 24, 0)
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "idle"
        assert data["temperature"]["block"] == 25.0
        assert data["temperature"]["lid"] == 24.0
        assert data["run"] is None
        assert stub.get_status_called

    def test_status_running_when_device_busy(self, client, stub):
        stub._status = StatusBroadcast(1, 0, 0, 0, 95, 110, 0)
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "running"
        assert data["run"]["running"] is True
        assert data["temperature"]["block"] == 95.0

    def test_status_disconnected_when_not_connected(self, client, stub):
        stub._connected = False
        resp = client.get("/status")
        assert resp.status_code == 200
        assert resp.json()["state"] == "disconnected"

    def test_status_disconnected_when_no_ble_client(self, client_no_hw):
        resp = client_no_hw.get("/status")
        assert resp.status_code == 200
        assert resp.json()["state"] == "disconnected"

    def test_status_returns_error_on_exception(self, client, stub, monkeypatch):
        async def failing_status():
            raise RuntimeError("BLE read failed")

        monkeypatch.setattr(stub, "get_status", failing_status)
        stub._connected = True
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "error"
        assert len(data["errors"]) == 1
        assert data["errors"][0]["code"] == "status_fetch_failed"


# ---------------------------------------------------------------------------
# POST /profiles/validate
# ---------------------------------------------------------------------------


class TestProfileValidate:
    def test_valid_profile_returns_ok(self, client):
        body = {
            "profile": {
                "name": "Test PCR",
                "lid_temperature": 110,
                "initial_denaturation": {"temperature": 95, "duration": 180},
                "cycles": [
                    {
                        "repeat": 30,
                        "denaturation": {"temperature": 95, "duration": 30},
                        "annealing": {"temperature": 58, "duration": 30},
                        "extension": {"temperature": 72, "duration": 60},
                    }
                ],
                "final_extension": {"temperature": 72, "duration": 300},
                "hold_temperature": 4,
            }
        }
        resp = client.post("/profiles/validate", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["errors"] == []

    def test_invalid_lid_temperature(self, client):
        body = {
            "profile": {
                "name": "Hot lid",
                "lid_temperature": 200,
                "initial_denaturation": {"temperature": 95, "duration": 180},
                "cycles": [],
                "final_extension": {"temperature": 72, "duration": 300},
            }
        }
        resp = client.post("/profiles/validate", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert any("Lid temperature" in e for e in data["errors"])

    def test_invalid_step_temperature(self, client):
        body = {
            "profile": {
                "name": "Too hot",
                "lid_temperature": 110,
                "initial_denaturation": {"temperature": 150, "duration": 180},
                "cycles": [],
                "final_extension": {"temperature": 72, "duration": 300},
            }
        }
        resp = client.post("/profiles/validate", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert any("initial_denaturation" in e for e in data["errors"])

    def test_invalid_duration(self, client):
        body = {
            "profile": {
                "name": "Negative duration",
                "lid_temperature": 110,
                "initial_denaturation": {"temperature": 95, "duration": -5},
                "cycles": [],
                "final_extension": {"temperature": 72, "duration": 300},
            }
        }
        resp = client.post("/profiles/validate", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert any("duration" in e for e in data["errors"])

    def test_invalid_cycle_count(self, client):
        body = {
            "profile": {
                "name": "Zero cycles",
                "lid_temperature": 110,
                "initial_denaturation": {"temperature": 95, "duration": 180},
                "cycles": [
                    {
                        "repeat": 0,
                        "denaturation": {"temperature": 95, "duration": 30},
                        "annealing": {"temperature": 58, "duration": 30},
                        "extension": {"temperature": 72, "duration": 60},
                    }
                ],
                "final_extension": {"temperature": 72, "duration": 300},
            }
        }
        resp = client.post("/profiles/validate", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert any("repeat_count" in e for e in data["errors"])

    def test_missing_required_field(self, client):
        body = {"profile": {}}
        resp = client.post("/profiles/validate", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert any("name" in e for e in data["errors"])

    def test_missing_body_field_is_422(self, client):
        resp = client.post("/profiles/validate", json={})
        assert resp.status_code == 422

    def test_warns_for_no_cycles(self, client):
        body = {
            "profile": {
                "name": "No cycles",
                "lid_temperature": 110,
                "initial_denaturation": {"temperature": 95, "duration": 180},
                "cycles": [],
                "final_extension": {"temperature": 72, "duration": 300},
            }
        }
        resp = client.post("/profiles/validate", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert any("no thermal cycles" in w.lower() for w in data["warnings"])
