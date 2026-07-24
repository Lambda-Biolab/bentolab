"""Tests for the BentoLab HTTP API (no hardware required)."""

from __future__ import annotations

import contextlib
from copy import deepcopy
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
        # Status callback registry (for SSE / events tests).
        self._status_callbacks: list[Any] = []

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

    def on_status(self, callback: Any) -> None:
        """Register a status callback (for SSE tests)."""
        self._status_callbacks.append(callback)

    def off_status(self, callback: Any) -> None:
        """Remove a previously-registered status callback. No-op if absent."""
        with contextlib.suppress(ValueError):
            self._status_callbacks.remove(callback)

    def emit_status(self, status: StatusBroadcast) -> None:
        """Test helper: fire a status broadcast to all registered callbacks."""
        for cb in list(self._status_callbacks):
            cb(status)


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

    def test_start_run_idempotent_returns_existing_run(self, client, stub):
        """Second POST /runs with the same profile + device returns the existing run.

        Idempotency contract: the elabFTW gateway can safely retry a
        start_run call without coordinating a lock. The second call
        returns 200 OK with was_already_running=True and the same
        run_id; start_run is NOT called twice on the hardware.
        """
        resp1 = client.post("/runs", json=_run_body())
        assert resp1.status_code == 200
        run_id_1 = resp1.json()["run_id"]
        assert resp1.json()["was_already_running"] is False

        # Reset the stub's start_run counter so we can assert it isn't
        # called again on the second request.
        stub.start_run_called = False

        resp2 = client.post("/runs", json=_run_body())
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["run_id"] == run_id_1
        assert body2["was_already_running"] is True
        assert stub.start_run_called is False

    def test_start_run_different_profile_on_locked_device_still_rejected(self, client, stub):
        """Idempotency does NOT apply when the profile or device differs.

        Only same-profile-on-same-device retries return the existing
        run. A different profile (or different device) is treated as
        a new request and falls through to preflight, which rejects it
        because the device is locked.
        """
        # First run succeeds
        resp1 = client.post("/runs", json=_run_body())
        assert resp1.status_code == 200

        # Second run with a different profile name -> preflight fails
        different_profile = deepcopy(VALID_PROFILE)
        different_profile["name"] = "Different PCR"
        resp2 = client.post("/runs", json=_run_body({"profile": different_profile}))
        assert resp2.status_code == 400
        data = resp2.json()
        assert "preflight_failed" in str(data)


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

    def test_preflight_fails_on_locked_device_with_different_profile(self, client, stub):
        """A run with a different profile on a locked device still fails preflight.

        Idempotency only returns the existing run when the profile name
        and device address match. Any other request falls through to
        preflight, which detects the held lock and rejects the run.
        """
        # First run acquires the lock
        resp1 = client.post("/runs", json=_run_body())
        assert resp1.status_code == 200

        # Second run with a different profile name -> preflight must fail
        different_profile = deepcopy(VALID_PROFILE)
        different_profile["name"] = "Other Profile"
        resp2 = client.post("/runs", json=_run_body({"profile": different_profile}))
        assert resp2.status_code == 400
        data = resp2.json()
        assert "preflight_failed" in str(data)
        assert "busy" in str(data)


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


# ===================================================================
# Long-polling on GET /runs/{id}
# ===================================================================


class TestLongPolling:
    """GET /runs/{id}?wait=N blocks until the run reaches a terminal state."""

    def test_wait_zero_returns_immediately(self, client):
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        # No wait -> returns right away, state is running
        resp = client.get(f"/runs/{run_id}?wait=0")
        assert resp.status_code == 200
        assert resp.json()["state"] == "running"

    def test_wait_blocks_until_terminal(self, client):
        from bentolab.runs import RunLifecycle

        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        # Schedule a transition to terminal in the background. The
        # GET ?wait=2 should block until the transition lands and
        # then return the terminal state.
        import threading
        import time as _time

        def complete_after_delay() -> None:
            _time.sleep(0.3)
            client.app.state.run_manager.transition_to(run_id, RunLifecycle.COMPLETED)

        threading.Thread(target=complete_after_delay, daemon=True).start()

        t0 = _time.monotonic()
        resp = client.get(f"/runs/{run_id}?wait=5")
        elapsed = _time.monotonic() - t0

        assert resp.status_code == 200
        # Returned the terminal state, not the original running one
        assert resp.json()["state"] == "completed"
        # Waited at least until the scheduled transition (>= 0.3s)
        assert elapsed >= 0.2
        # And didn't wait the full 5s budget
        assert elapsed < 4.0

    def test_wait_returns_on_timeout_if_still_running(self, client):
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]

        # No transition scheduled; wait=1 should return the current
        # running state after ~1s.
        import time as _time

        t0 = _time.monotonic()
        resp = client.get(f"/runs/{run_id}?wait=1")
        elapsed = _time.monotonic() - t0

        assert resp.status_code == 200
        assert resp.json()["state"] == "running"
        # Polled roughly on the 0.5s cadence; allow generous tolerance
        assert 0.5 <= elapsed < 2.0

    def test_wait_terminal_returns_immediately(self, client):
        from bentolab.runs import RunLifecycle

        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]
        client.app.state.run_manager.transition_to(run_id, RunLifecycle.COMPLETED)

        # Terminal state -> no waiting, return immediately
        resp = client.get(f"/runs/{run_id}?wait=5")
        assert resp.status_code == 200
        assert resp.json()["state"] == "completed"

    def test_wait_caps_at_60_seconds(self, client):
        """wait param validation: values > 60 are rejected by FastAPI."""
        create_resp = client.post("/runs", json=_run_body())
        run_id = create_resp.json()["run_id"]
        resp = client.get(f"/runs/{run_id}?wait=61")
        assert resp.status_code == 422


# ===================================================================
# SSE telemetry stream
# ===================================================================


class TestSSEEvents:
    """GET /events streams Server-Sent Events to the client.

    We test the underlying :func:`stream_events` generator and the
    :class:`EventBroker` directly, because the HTTP layer is a thin
    ``StreamingResponse`` wrapper and a hanging test client would
    never resolve (SSE streams are intentionally infinite).
    """

    def test_broker_publishes_to_subscribers(self, stub):
        """The EventBroker fans out events to all current subscribers."""
        import asyncio

        from bentolab.api.events import EventBroker, TelemetryEvent

        async def scenario() -> None:
            broker = EventBroker()
            q1 = broker.subscribe()
            q2 = broker.subscribe()
            assert broker.subscriber_count == 2

            broker.publish(TelemetryEvent(kind="status", data={"x": 1}))
            e1 = await asyncio.wait_for(q1.get(), timeout=1.0)
            e2 = await asyncio.wait_for(q2.get(), timeout=1.0)
            assert e1.kind == "status"
            assert e2.kind == "status"

            broker.unsubscribe(q1)
            assert broker.subscriber_count == 1

        asyncio.run(scenario())

    def test_broker_drops_for_full_subscribers(self):
        """A slow subscriber's full queue doesn't block other subscribers."""
        import asyncio

        from bentolab.api.events import EventBroker, TelemetryEvent

        async def scenario() -> None:
            broker = EventBroker(max_queue=2)
            fast = broker.subscribe()
            slow = broker.subscribe()
            # Fill the slow subscriber's queue
            for i in range(2):
                broker.publish(TelemetryEvent(kind="run", data={"i": i}))
            # Drain the fast subscriber
            for _ in range(2):
                await asyncio.wait_for(fast.get(), timeout=1.0)
            # Another publish: fast should still get it, slow is full -> drop
            broker.publish(TelemetryEvent(kind="status", data={"final": True}))
            e = await asyncio.wait_for(fast.get(), timeout=1.0)
            assert e.data == {"final": True}
            # Slow's queue is still at capacity
            assert slow.qsize() == 2

        asyncio.run(scenario())

    def test_stream_events_yields_connected_event(self, stub):
        """The first event from stream_events is the ``connected`` marker."""
        import asyncio

        from bentolab.api.events import EventBroker, stream_events

        async def scenario() -> None:
            broker = EventBroker()
            gen = stream_events(broker, stub)
            # Pull the first chunk; the generator stays open after.
            first = await asyncio.wait_for(anext(gen), timeout=2.0)
            assert "event: connected" in first
            assert "id: connected" in first
            # Tidy close so the broker unsubscribes.
            await gen.aclose()

        asyncio.run(scenario())

    def test_stream_events_attaches_and_detaches_status_callback(self, stub):
        """stream_events registers and cleans up the on_status callback."""
        import asyncio

        from bentolab.api.events import EventBroker, stream_events

        async def scenario() -> None:
            broker = EventBroker()
            assert len(stub._status_callbacks) == 0

            gen = stream_events(broker, stub)
            # Pull the connected event so the setup finishes
            await asyncio.wait_for(anext(gen), timeout=2.0)
            assert len(stub._status_callbacks) == 1

            await gen.aclose()
            # Cleanup should detach the callback
            assert len(stub._status_callbacks) == 0

        asyncio.run(scenario())


# ===================================================================
# API token authentication
# ===================================================================


class TestApiAuth:
    """Bearer token middleware: open mode without tokens, required with tokens."""

    def test_open_mode_allows_anonymous_when_no_tokens(self, client):
        """No tokens registered -> request goes through unauthenticated."""
        resp = client.get("/health")
        assert resp.status_code == 200

        resp = client.get("/devices")
        assert resp.status_code == 200

    def test_protected_endpoint_rejects_without_token_when_tokens_exist(self, tmp_path, stub):
        from bentolab.api.app import create_app
        from bentolab.api.auth import TokenStore

        token_path = tmp_path / "tokens.json"
        store = TokenStore(path=token_path)
        store.issue("AA:BB:CC:DD:EE:FF")
        assert len(store.list()) == 1

        app = create_app(ble_client=stub, token_store=store)
        c = TestClient(app)

        # No Authorization header
        resp = c.get("/devices")
        assert resp.status_code == 401
        assert "Missing" in str(resp.json()) or "Bearer" in str(resp.json())

    def test_protected_endpoint_accepts_valid_token(self, tmp_path, stub):
        from bentolab.api.app import create_app
        from bentolab.api.auth import TokenStore

        store = TokenStore(path=tmp_path / "tokens.json")
        tok = store.issue("AA:BB:CC:DD:EE:FF")

        app = create_app(ble_client=stub, token_store=store)
        c = TestClient(app)

        resp = c.get("/devices", headers={"Authorization": f"Bearer {tok.token}"})
        assert resp.status_code == 200

    def test_protected_endpoint_rejects_invalid_token(self, tmp_path, stub):
        from bentolab.api.app import create_app
        from bentolab.api.auth import TokenStore

        store = TokenStore(path=tmp_path / "tokens.json")
        store.issue("AA:BB:CC:DD:EE:FF")

        app = create_app(ble_client=stub, token_store=store)
        c = TestClient(app)

        resp = c.get("/devices", headers={"Authorization": "Bearer not-a-real-token"})
        assert resp.status_code == 401

    def test_health_is_always_exempt(self, tmp_path, stub):
        from bentolab.api.app import create_app
        from bentolab.api.auth import TokenStore

        store = TokenStore(path=tmp_path / "tokens.json")
        store.issue("AA:BB:CC:DD:EE:FF")

        app = create_app(ble_client=stub, token_store=store)
        c = TestClient(app)

        # /health must work without auth even when tokens are registered
        resp = c.get("/health")
        assert resp.status_code == 200

    def test_openapi_is_always_exempt(self, tmp_path, stub):
        from bentolab.api.app import create_app
        from bentolab.api.auth import TokenStore

        store = TokenStore(path=tmp_path / "tokens.json")
        store.issue("AA:BB:CC:DD:EE:FF")

        app = create_app(ble_client=stub, token_store=store)
        c = TestClient(app)

        resp = c.get("/openapi.json")
        assert resp.status_code == 200

    def test_force_auth_blocks_when_no_tokens(self, tmp_path, stub):
        """BENTOLAB_REQUIRE_AUTH=1 forces auth even with zero tokens."""
        from bentolab.api.app import create_app
        from bentolab.api.auth import TokenStore

        store = TokenStore(path=tmp_path / "tokens.json")
        # No tokens issued

        app = create_app(ble_client=stub, token_store=store, force_auth=True)
        c = TestClient(app)

        resp = c.get("/devices")
        assert resp.status_code == 401

    def test_valid_token_updates_last_used(self, tmp_path, stub):
        from bentolab.api.app import create_app
        from bentolab.api.auth import TokenStore

        store = TokenStore(path=tmp_path / "tokens.json")
        tok = store.issue("AA:BB:CC:DD:EE:FF")
        assert tok.last_used_at is None

        app = create_app(ble_client=stub, token_store=store)
        c = TestClient(app)
        c.get("/devices", headers={"Authorization": f"Bearer {tok.token}"})

        refreshed = store.lookup(tok.token)
        assert refreshed is not None
        assert refreshed.last_used_at is not None

    def test_token_store_persists_across_instances(self, tmp_path):
        from bentolab.api.auth import TokenStore

        path = tmp_path / "tokens.json"
        a = TokenStore(path=path)
        tok = a.issue("AA:BB:CC:DD:EE:FF")

        b = TokenStore(path=path)
        loaded = b.list()
        assert len(loaded) == 1
        assert loaded[0].token == tok.token
        assert loaded[0].device_address == "AA:BB:CC:DD:EE:FF"

    def test_revoke_unknown_token_returns_false(self, tmp_path):
        from bentolab.api.auth import TokenStore

        store = TokenStore(path=tmp_path / "tokens.json")
        assert store.revoke("never-issued") is False

    def test_revoke_issued_token_succeeds(self, tmp_path):
        from bentolab.api.auth import TokenStore

        store = TokenStore(path=tmp_path / "tokens.json")
        tok = store.issue("AA:BB:CC:DD:EE:FF")
        assert store.revoke(tok.token) is True
        assert store.lookup(tok.token) is None
