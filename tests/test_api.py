"""Tests for the BentoLab HTTP API (no hardware required)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from bentolab.api.app import create_app
from bentolab.models import PCRProfile
from bentolab.protocol import StatusBroadcast
from bentolab.runs import RunLifecycle, RunState

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
        self.start_run_called = False
        self.abort_run_called = False
        self.get_run_status_called = False
        self._started_profile: PCRProfile | None = None
        self._run_status: dict[str, Any] = {
            "running": False,
            "progress": 0,
            "block_temperature": 25.0,
            "lid_temperature": 24.0,
            "elapsed_seconds": 0.0,
        }
        self._start_run_fail: bool = False
        self._abort_run_fail: bool = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def discover(self, timeout: float = 10.0) -> list[tuple[Any, Any]]:
        self.discover_called = True
        return self._devices

    async def get_status(self) -> StatusBroadcast:
        self.get_status_called = True
        return self._status

    async def start_run(self, profile: PCRProfile) -> None:
        self.start_run_called = True
        self._started_profile = profile
        if self._start_run_fail:
            raise RuntimeError("Simulated hardware start failure")
        self._run_status["running"] = True
        self._run_status["progress"] = 0

    async def abort_run(self) -> None:
        self.abort_run_called = True
        if self._abort_run_fail:
            raise RuntimeError("Simulated BLE abort failure")
        self._run_status["running"] = False

    async def get_run_status(self) -> RunState:
        self.get_run_status_called = True
        return RunState(
            state=RunLifecycle.RUNNING if self._run_status["running"] else RunLifecycle.IDLE,
            progress=int(self._run_status["progress"]),
            block_temperature=self._run_status["block_temperature"],
            lid_temperature=self._run_status["lid_temperature"],
            elapsed_seconds=float(self._run_status["elapsed_seconds"]),
        )


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
    """App with no BLE client at all -- tests degraded paths."""
    app = create_app(ble_client=None)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Shared valid profile body (matches PCRProfile.simple())
# ---------------------------------------------------------------------------

VALID_PROFILE = {
    "name": "Standard PCR",
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


def _run_body(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a valid POST /runs body."""
    body: dict[str, Any] = {
        "profile": VALID_PROFILE,
        "device_address": "AA:BB:CC:DD:EE:FF",
        "approval_id": "approval-token-123",
        "operator": "test-operator",
    }
    if overrides:
        body.update(overrides)
    return body


def _dry_run_body(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a valid POST /runs/dry-run body."""
    body: dict[str, Any] = {
        "profile": VALID_PROFILE,
    }
    if overrides:
        body.update(overrides)
    return body


# ===================================================================
# GET /health  (unchanged)
# ===================================================================


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


# ===================================================================
# GET /devices  (unchanged)
# ===================================================================


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


# ===================================================================
# GET /status  (unchanged)
# ===================================================================


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


# ===================================================================
# POST /profiles/validate  (unchanged)
# ===================================================================


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


# ===================================================================
# POST /runs/dry-run
# ===================================================================


class TestDryRun:
    def test_dry_run_valid_profile(self, client):
        resp = client.post("/runs/dry-run", json=_dry_run_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        sim = data["simulation"]
        assert sim["duration_s"] > 0
        assert len(sim["steps"]) > 0
        # Standard PCR: 1 init denat + 30 cycles * 3 steps + 1 final ext
        assert len(sim["steps"]) == 1 + 30 * 3 + 1

    def test_dry_run_invalid_profile_returns_errors(self, client):
        body = _dry_run_body()
        body["profile"] = {
            "name": "Bad",
            "lid_temperature": 999,
            "initial_denaturation": {"temperature": 95, "duration": 180},
            "cycles": [],
            "final_extension": {"temperature": 72, "duration": 300},
        }
        resp = client.post("/runs/dry-run", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert len(data["errors"]) > 0

    def test_dry_run_empty_profile(self, client):
        resp = client.post("/runs/dry-run", json={"profile": {}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert any("name" in e for e in data["errors"])

    def test_dry_run_422_on_missing_body(self, client):
        resp = client.post("/runs/dry-run", json={})
        assert resp.status_code == 422


# ===================================================================
# POST /runs  -- start a real run
# ===================================================================


class TestStartRun:
    def test_start_run_success(self, client, stub):
        resp = client.post("/runs", json=_run_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["state"] == "running"
        assert len(data["run_id"]) > 0
        assert data["started_at"] is not None
        assert stub.start_run_called

    def test_start_run_no_approval_rejected(self, client):
        body = _run_body({"approval_id": None})
        resp = client.post("/runs", json=body)
        assert resp.status_code == 400
        data = resp.json()
        # FastAPI returns detail as dict when raised via HTTPException
        assert "approval_required" in str(data)

    def test_start_run_no_ble_client_rejected(self, client_no_hw):
        resp = client_no_hw.post("/runs", json=_run_body())
        assert resp.status_code == 400
        data = resp.json()
        assert "preflight_failed" in str(data)

    def test_start_run_device_not_connected_rejected(self, client, stub):
        stub._connected = False
        resp = client.post("/runs", json=_run_body())
        assert resp.status_code == 400
        data = resp.json()
        assert "preflight_failed" in str(data)

    def test_start_run_device_busy_rejected(self, client, stub):
        stub._status = StatusBroadcast(1, 0, 0, 0, 95, 110, 0)
        resp = client.post("/runs", json=_run_body())
        assert resp.status_code == 400
        data = resp.json()
        assert "preflight_failed" in str(data)

    def test_start_run_invalid_profile_rejected(self, client):
        body = _run_body()
        body["profile"] = {
            "name": "Bad",
            "lid_temperature": 999,
            "initial_denaturation": {"temperature": 95, "duration": 180},
            "cycles": [],
            "final_extension": {"temperature": 72, "duration": 300},
        }
        resp = client.post("/runs", json=body)
        assert resp.status_code == 400

    def test_start_run_hardware_failure_returns_500(self, client, stub):
        stub._start_run_fail = True
        resp = client.post("/runs", json=_run_body())
        assert resp.status_code == 500
        data = resp.json()
        assert "run_start_failed" in str(data)

    def test_start_run_calls_ble_start_run(self, client, stub):
        client.post("/runs", json=_run_body())
        assert stub.start_run_called
        assert stub._started_profile is not None
        assert stub._started_profile.name == "Standard PCR"

    def test_start_run_on_locked_device_rejected(self, client, stub):
        # First run succeeds
        resp1 = client.post("/runs", json=_run_body())
        assert resp1.status_code == 200

        # Second run on same device is rejected
        resp2 = client.post("/runs", json=_run_body())
        assert resp2.status_code == 400
        data = resp2.json()
        assert "preflight_failed" in str(data)
        assert "busy" in str(data)


# ===================================================================
# GET /runs/{id}
# ===================================================================


class TestGetRunStatus:
    def test_get_run_status_returns_state(self, client):
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        resp = client.get(f"/runs/{run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == run_id
        assert data["state"] == "running"

    def test_get_run_status_not_found(self, client):
        resp = client.get("/runs/nonexistent-id")
        assert resp.status_code == 404

    def test_get_run_status_after_completion(self, client):
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        from bentolab.runs import RunLifecycle

        run_mgr = client.app.state.run_manager
        run_mgr.transition_to(run_id, RunLifecycle.COMPLETED)

        resp = client.get(f"/runs/{run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "completed"


# ===================================================================
# POST /runs/{id}/abort
# ===================================================================


class TestAbortRun:
    def test_abort_running_run(self, client, stub):
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        resp = client.post(f"/runs/{run_id}/abort")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["state"] == "aborted"
        assert data["aborted_at"] is not None
        assert stub.abort_run_called

    def test_abort_idempotent_on_terminal(self, client, stub):
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        # First abort
        resp1 = client.post(f"/runs/{run_id}/abort")
        assert resp1.status_code == 200
        assert resp1.json()["state"] == "aborted"

        # Second abort should be idempotent
        resp2 = client.post(f"/runs/{run_id}/abort")
        assert resp2.status_code == 200
        assert resp2.json()["ok"] is True

    def test_abort_not_found(self, client):
        resp = client.post("/runs/nonexistent-id/abort")
        assert resp.status_code == 404

    def test_abort_after_disconnect_marks_review(self, client, stub):
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        stub._abort_run_fail = True
        stub._connected = False

        resp = client.post(f"/runs/{run_id}/abort")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "unknown_requires_operator_review"

    def test_abort_releases_lock(self, client, stub):
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        # Abort
        client.post(f"/runs/{run_id}/abort")

        # Now should be able to start a new run
        stub._status = StatusBroadcast(0, 0, 0, 0, 25, 24, 0)
        resp2 = client.post("/runs", json=_run_body())
        assert resp2.status_code == 200


# ===================================================================
# GET /runs/{id}/results
# ===================================================================


class TestRunResults:
    def test_get_results_on_completed_run(self, client):
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        from bentolab.runs import RunLifecycle

        run_mgr = client.app.state.run_manager
        run_mgr.transition_to(run_id, RunLifecycle.COMPLETED)

        resp = client.get(f"/runs/{run_id}/results")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == run_id
        assert data["state"] == "completed"
        assert data["profile"] is not None
        assert data["started_at"] is not None
        assert data["completed_at"] is not None

    def test_get_results_on_aborted_run(self, client, stub):
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        client.post(f"/runs/{run_id}/abort")

        resp = client.get(f"/runs/{run_id}/results")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "aborted"
        assert data["aborted_at"] is not None

    def test_get_results_on_review_run(self, client, stub):
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        stub._abort_run_fail = True
        stub._connected = False
        client.post(f"/runs/{run_id}/abort")

        resp = client.get(f"/runs/{run_id}/results")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "unknown_requires_operator_review"
        assert len(data["errors"]) > 0

    def test_get_results_not_found(self, client):
        resp = client.get("/runs/nonexistent-id/results")
        assert resp.status_code == 404

    def test_get_results_on_running_run(self, client, stub):
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        resp = client.get(f"/runs/{run_id}/results")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "running"
        assert data["profile"] is not None


# ===================================================================
# Preflight edge cases
# ===================================================================


class TestPreflight:
    def test_preflight_fails_without_ble(self, client_no_hw):
        resp = client_no_hw.post("/runs", json=_run_body())
        assert resp.status_code == 400
        assert "preflight_failed" in str(resp.json())

    def test_preflight_fails_when_disconnected(self, client, stub):
        stub._connected = False
        resp = client.post("/runs", json=_run_body())
        assert resp.status_code == 400
        assert "preflight_failed" in str(resp.json())

    def test_preflight_fails_on_invalid_profile(self, client):
        body = _run_body()
        body["profile"] = {
            "name": "Hot lid",
            "lid_temperature": 999,
            "initial_denaturation": {"temperature": 95, "duration": 180},
            "cycles": [],
            "final_extension": {"temperature": 72, "duration": 300},
        }
        resp = client.post("/runs", json=body)
        # Preflight includes profile validation, so invalid lid temp fails
        assert resp.status_code == 400

    def test_preflight_fails_on_locked_device(self, client, stub):
        # First run acquires lock
        client.post("/runs", json=_run_body())
        # Second run must fail preflight
        resp = client.post("/runs", json=_run_body())
        assert resp.status_code == 400


# ===================================================================
# Device lock tests
# ===================================================================


class TestDeviceLock:
    def test_lock_prevents_concurrent_runs(self, client, stub):
        client.post("/runs", json=_run_body())

        run_mgr = client.app.state.run_manager
        assert run_mgr.is_locked

    def test_lock_released_on_completion(self, client):
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        from bentolab.runs import RunLifecycle

        run_mgr = client.app.state.run_manager
        run_mgr.transition_to(run_id, RunLifecycle.COMPLETED)

        assert not run_mgr.is_locked

    def test_lock_released_on_abort(self, client, stub):
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        client.post(f"/runs/{run_id}/abort")

        run_mgr = client.app.state.run_manager
        assert not run_mgr.is_locked

    def test_force_release_lock(self, client, stub):
        client.post("/runs", json=_run_body())

        run_mgr = client.app.state.run_manager
        released = run_mgr.force_release_lock()
        assert released is not None
        assert not run_mgr.is_locked

        # Can now start a new run
        stub._status = StatusBroadcast(0, 0, 0, 0, 25, 24, 0)
        resp = client.post("/runs", json=_run_body())
        assert resp.status_code == 200


# ===================================================================
# Ambiguous failure -- no automatic repeat
# ===================================================================


class TestAmbiguousFailure:
    def test_unknown_review_is_terminal(self, client, stub):
        """After unknown_requires_operator_review, no further transitions."""
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        from bentolab.runs import RunLifecycle

        run_mgr = client.app.state.run_manager
        # Transition to review
        run_mgr.transition_to(run_id, RunLifecycle.UNKNOWN_REVIEW)
        # Try to transition back -- should fail
        result = run_mgr.transition_to(run_id, RunLifecycle.RUNNING)
        assert result is False
        # Lock is released
        assert not run_mgr.is_locked

    def test_no_auto_repeat_after_abort_disconnect(self, client, stub):
        """Abort-after-disconnect -> review state, not a repeated run."""
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        stub._abort_run_fail = True
        stub._connected = False
        resp = client.post(f"/runs/{run_id}/abort")
        assert resp.json()["state"] == "unknown_requires_operator_review"

        # Verify state is terminal -- results available
        results = client.get(f"/runs/{run_id}/results")
        assert results.json()["state"] == "unknown_requires_operator_review"

    def test_failed_start_does_not_lock_device(self, client, stub):
        """A run that fails during hardware start releases the lock."""
        stub._start_run_fail = True
        resp = client.post("/runs", json=_run_body())
        assert resp.status_code == 500

        run_mgr = client.app.state.run_manager
        assert not run_mgr.is_locked


# ===================================================================
# Writeback path tracking
# ===================================================================


class TestWritebackPath:
    def test_run_record_has_writeback_state(self, client, stub):
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        run_mgr = client.app.state.run_manager
        run = run_mgr.get_run(run_id)
        assert run["writeback_state"] == "pending"
