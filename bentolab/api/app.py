"""FastAPI application for the BentoLab HTTP API.

Wraps the BLE client library behind an HTTP interface following the C22
contract. Supports injection of a **ble client** (real or stub) for use
with and without hardware.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import fastapi
from fastapi import HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from bentolab import __version__
from bentolab.models import PCRProfile
from bentolab.protocol import StatusBroadcast

from .models import (
    DeviceInfo,
    DevicesResponse,
    DryRunRequest,
    DryRunResponse,
    DryRunSimulation,
    DryRunStep,
    ErrorResponse,
    HealthResponse,
    ProfileValidationRequest,
    ProfileValidationResponse,
    RunAbortResponse,
    RunAcceptedResponse,
    RunProgressInfo,
    RunRequest,
    RunResultResponse,
    RunStateInfo,
    RunStatusDetailResponse,
    StatusError,
    StatusResponse,
    TemperatureSnapshot,
)
from .runs import RunManager, RunStates

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BLE client protocol -- the API depends on this, not on concrete classes
# ---------------------------------------------------------------------------


class BleClientProtocol(Protocol):
    """Minimal BLE client interface the API needs.

    Both :class:`bentolab.ble_client.BentoLabBLE` and :class:`StubBleClient`
    satisfy this protocol.
    """

    async def discover(self, timeout: float = 10.0) -> list[tuple[Any, Any]]:
        """Scan for nearby Bento Lab devices. Returns (device, adv_data) tuples."""
        ...

    async def get_status(self) -> StatusBroadcast:
        """Return the latest status broadcast from the connected device."""
        ...

    @property
    def is_connected(self) -> bool:
        """Whether a device is currently connected."""
        ...

    async def start_run(self, profile: PCRProfile) -> None:
        """Start a PCR run with the given profile.

        Converts the high-level PCRProfile into stage/cycle tuples and
        sends the start command to the device.  Does not block until
        completion -- use get_run_status() to poll.
        """
        ...

    async def abort_run(self) -> None:
        """Abort the currently running PCR program on the device."""
        ...

    async def get_run_status(self) -> dict[str, Any]:
        """Poll the current run status from the device.

        Returns a dict with at least:
            running (bool)
            progress (int)
            block_temperature (float)
            lid_temperature (float)
        """
        ...


# ---------------------------------------------------------------------------
# Profile validation helpers
# ---------------------------------------------------------------------------

# Instrument temperature limits (Bento Lab Pro V1.4)
TEMP_MIN = 4.0
TEMP_MAX = 100.0
LID_TEMP_MIN = 30.0
LID_TEMP_MAX = 115.0
DURATION_MIN = 0
DURATION_MAX = 86_400  # 24 hours
CYCLES_MIN = 1
CYCLES_MAX = 999


def _validate_profile(profile_dict: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    """Validate a PCR profile dict without hardware side effects.

    Returns (ok, errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Build PCRProfile from dict to normalize structure
    try:
        profile = PCRProfile.from_dict(profile_dict)
    except (ValueError, KeyError, TypeError) as exc:
        errors.append(f"Invalid profile structure: {exc}")
        return False, errors, warnings

    # 2. Name
    if not profile.name or profile.name == "Untitled":
        warnings.append("Profile has no meaningful name")

    # 3. Lid temperature
    if profile.lid_temperature < LID_TEMP_MIN or profile.lid_temperature > LID_TEMP_MAX:
        errors.append(
            f"Lid temperature {profile.lid_temperature} C is outside "
            f"safe range ({LID_TEMP_MIN}-{LID_TEMP_MAX} C)"
        )

    # 4. Initial denaturation
    _validate_step(errors, warnings, "initial_denaturation", profile.initial_denaturation)

    # 5. Cycles
    if not profile.cycles:
        warnings.append("Profile has no thermal cycles (denaturation/annealing/extension)")
    for i, cycle in enumerate(profile.cycles):
        prefix = f"cycle[{i}]"
        if cycle.repeat_count < CYCLES_MIN or cycle.repeat_count > CYCLES_MAX:
            errors.append(
                f"{prefix} repeat_count {cycle.repeat_count} is outside "
                f"allowed range ({CYCLES_MIN}-{CYCLES_MAX})"
            )
        _validate_step(errors, warnings, f"{prefix}.denaturation", cycle.denaturation)
        _validate_step(errors, warnings, f"{prefix}.annealing", cycle.annealing)
        _validate_step(errors, warnings, f"{prefix}.extension", cycle.extension)

    # 6. Final extension
    _validate_step(errors, warnings, "final_extension", profile.final_extension)

    # 7. Hold temperature
    if profile.hold_temperature < 0 or profile.hold_temperature > TEMP_MAX:
        warnings.append(f"Hold temperature {profile.hold_temperature} C is unusual")

    return len(errors) == 0, errors, warnings


def _validate_step(
    errors: list[str],
    warnings: list[str],  # noqa: ARG001
    label: str,
    step: Any,
) -> None:
    temp = step.temperature
    dur = step.duration
    if temp < TEMP_MIN or temp > TEMP_MAX:
        errors.append(
            f"{label} temperature {temp} C is outside instrument range ({TEMP_MIN}-{TEMP_MAX} C)"
        )
    if dur < DURATION_MIN or dur > DURATION_MAX:
        errors.append(
            f"{label} duration {dur}s is outside allowed range ({DURATION_MIN}-{DURATION_MAX}s)"
        )


# ---------------------------------------------------------------------------
# Device discovery helpers
# ---------------------------------------------------------------------------


def _device_from_discovery(item: tuple[Any, Any]) -> DeviceInfo:
    """Normalize a BLE discovery result to a DeviceInfo."""
    dev, _adv = item
    try:
        addr = dev.address
    except AttributeError:
        addr = str(dev)
    try:
        name = dev.name or ""
    except AttributeError:
        name = ""
    return DeviceInfo(address=addr, name=name, transport="ble")


def _status_to_state_name(status: StatusBroadcast) -> str:
    """Map a protocol StatusBroadcast to a status string."""
    if status.running:
        return "running"
    return "idle"


# ---------------------------------------------------------------------------
# Helpers to extract BLE client / RunManager from the request
# ---------------------------------------------------------------------------


def _get_ble(request: Request) -> BleClientProtocol | None:
    """Retrieve the BLE client from app state."""
    return getattr(request.app.state, "ble_client", None)


def _get_run_manager(request: Request) -> RunManager:
    """Retrieve the RunManager from app state."""
    return getattr(request.app.state, "run_manager", RunManager())


# ---------------------------------------------------------------------------
# Preflight  (C22 contract Preflight, lock, abort, and recovery)
# ---------------------------------------------------------------------------


async def _preflight(
    ble: BleClientProtocol | None,
    profile_dict: dict[str, Any],
    device_address: str | None,
    run_mgr: RunManager,
) -> list[str]:
    """Run hardware preflight checks.  Returns a list of error messages (empty = pass).

    Checks (per contract):
      1. BLE availability
      2. Device reachability / connected
      3. Device idle (not already running)
      4. Profile compatible
      5. Device lock available
    """
    errors: list[str] = []

    # 1. BLE availability
    if ble is None:
        errors.append("BLE adapter not available")
        return errors  # No point checking further without BLE

    # 2. Device connected
    if not ble.is_connected:
        errors.append("Device not connected")

    # 3. Device idle
    try:
        status = await ble.get_status()
        if status.running:
            errors.append("Device is already running a job")
    except Exception:
        errors.append("Failed to query device status")

    # 4. Profile compatible
    ok, profile_errors, _warnings = _validate_profile(profile_dict)
    if not ok:
        errors.extend(profile_errors)

    # 5. Device lock available
    lock_ok, held_by = run_mgr.check_lock_available()
    if not lock_ok:
        errors.append(
            f"Device is busy with run {held_by} -- wait for it to finish or force-release"
        )

    return errors


# ---------------------------------------------------------------------------
# Endpoint handlers -- read / status
# ---------------------------------------------------------------------------


async def _health(request: Request) -> HealthResponse:
    """GET /health -- never requires hardware."""
    ble = _get_ble(request)
    ble_status: str
    if ble is None:
        ble_status = "not_available"
    else:
        try:
            _ = ble.is_connected
            ble_status = "ok"
        except Exception:  # noqa: BLE001
            ble_status = "error"

    return HealthResponse(
        status="ok",
        version=__version__,
        ble=ble_status,
        wifi="not_supported",
    )


async def _devices(request: Request) -> DevicesResponse:
    """GET /devices -- discover BentoLab devices via BLE scan."""
    ble = _get_ble(request)
    if ble is None:
        return DevicesResponse(devices=[])

    try:
        discovered = await ble.discover(timeout=5.0)
    except Exception:
        logger.exception("BLE discovery failed")
        return DevicesResponse(devices=[])

    device_list = [_device_from_discovery(d) for d in discovered]
    return DevicesResponse(devices=device_list)


async def _status(request: Request) -> StatusResponse:
    """GET /status -- current device state."""
    ble = _get_ble(request)
    if ble is None or not ble.is_connected:
        return StatusResponse(state="disconnected")

    try:
        raw: StatusBroadcast = await ble.get_status()
    except Exception:
        logger.exception("Failed to get device status")
        return StatusResponse(
            state="error",
            errors=[
                StatusError(code="status_fetch_failed", message="Could not read device status")
            ],
        )

    temps = TemperatureSnapshot(
        current=float(raw.block_temperature),
        lid=float(raw.lid_temperature),
        block=float(raw.block_temperature),
    )
    run_info = None
    if raw.running:
        run_info = RunStateInfo(running=True)

    return StatusResponse(
        state=_status_to_state_name(raw),
        temperature=temps,
        run=run_info,
    )


# ---------------------------------------------------------------------------
# Endpoint handlers -- validation / simulation
# ---------------------------------------------------------------------------


async def _validate_profile_handler(body: ProfileValidationRequest) -> ProfileValidationResponse:
    """POST /profiles/validate -- validate a PCR profile without hardware."""
    ok, errors, warnings = _validate_profile(body.profile)
    return ProfileValidationResponse(ok=ok, errors=errors, warnings=warnings)


async def _dry_run(body: DryRunRequest) -> DryRunResponse:
    """POST /runs/dry-run -- simulate a run without hardware."""
    # 1. Validate the profile
    ok, errors, warnings = _validate_profile(body.profile)
    if not ok:
        return DryRunResponse(ok=False, errors=errors)

    # 2. Build PCRProfile for simulation
    try:
        profile = PCRProfile.from_dict(body.profile)
    except (ValueError, KeyError, TypeError) as exc:
        return DryRunResponse(ok=False, errors=[str(exc)])

    total_duration = profile.estimated_runtime_seconds()

    # 3. Build simulation steps
    steps: list[DryRunStep] = []

    # Initial denaturation
    steps.append(
        DryRunStep(
            phase="initial_denaturation",
            temperature=profile.initial_denaturation.temperature,
            duration_s=profile.initial_denaturation.duration,
        )
    )

    # Cycles
    for i, cycle in enumerate(profile.cycles):
        for _ in range(cycle.repeat_count):
            steps.append(
                DryRunStep(
                    phase=f"cycle_{i}_denaturation",
                    temperature=cycle.denaturation.temperature,
                    duration_s=cycle.denaturation.duration,
                )
            )
            steps.append(
                DryRunStep(
                    phase=f"cycle_{i}_annealing",
                    temperature=cycle.annealing.temperature,
                    duration_s=cycle.annealing.duration,
                )
            )
            steps.append(
                DryRunStep(
                    phase=f"cycle_{i}_extension",
                    temperature=cycle.extension.temperature,
                    duration_s=cycle.extension.duration,
                )
            )

    # Final extension
    steps.append(
        DryRunStep(
            phase="final_extension",
            temperature=profile.final_extension.temperature,
            duration_s=profile.final_extension.duration,
        )
    )

    return DryRunResponse(
        ok=True,
        simulation=DryRunSimulation(
            duration_s=total_duration,
            steps=steps,
            warnings=warnings,
        ),
    )


# ---------------------------------------------------------------------------
# Endpoint handlers -- execution (Tier 2)
# ---------------------------------------------------------------------------


async def _start_run(body: RunRequest, request: Request) -> RunAcceptedResponse:
    """POST /runs -- start a real run (requires preflight + lock + approval)."""
    ble = _get_ble(request)
    run_mgr = _get_run_manager(request)

    # --- Preflight ---
    pf_errors = await _preflight(ble, body.profile, body.device_address, run_mgr)
    if pf_errors:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "preflight_failed",
                "severity": "error",
                "human_message": "Preflight checks failed",
                "operator_hint": "; ".join(pf_errors),
                "retryable": True,
                "details": {"errors": pf_errors},
            },
        )

    # --- Approval check ---
    if not body.approval_id:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "approval_required",
                "severity": "error",
                "human_message": "Approval ID is required to start a run",
                "operator_hint": "Supply a gateway approval_id in the request body",
                "retryable": True,
                "details": {},
            },
        )

    # --- Create run record (acquires lock) ---
    run_id = run_mgr.create_run(
        profile=body.profile,
        device_address=body.device_address,
        operator=body.operator,
        approval_id=body.approval_id,
    )

    # --- Start on hardware ---
    try:
        profile = PCRProfile.from_dict(body.profile)
        await ble.start_run(profile)
        run_mgr.transition_to(run_id, RunStates.RUNNING)
    except Exception as exc:
        logger.exception("Failed to start run %s on hardware", run_id)
        run_mgr.record_error(run_id, "run_start_failed", str(exc))
        run_mgr.transition_to(run_id, RunStates.FAILED)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "run_start_failed",
                "severity": "error",
                "human_message": f"Failed to start run on hardware: {exc}",
                "operator_hint": "Check BLE connection and device state, then retry",
                "retryable": True,
                "details": {"run_id": run_id},
            },
        ) from exc

    run = run_mgr.get_run(run_id)
    return RunAcceptedResponse(
        ok=True,
        run_id=run_id,
        state=RunStates.RUNNING,
        started_at=run["started_at"],
    )


async def _get_run_status_handler(run_id: str, request: Request) -> RunStatusDetailResponse:
    """GET /runs/{id} -- get run state and progress."""
    run_mgr = _get_run_manager(request)
    run = run_mgr.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "run_not_found",
                "severity": "error",
                "human_message": f"Run {run_id} not found",
                "operator_hint": "Check the run ID",
                "retryable": False,
                "details": {"run_id": run_id},
            },
        )

    state = run["state"]
    progress: RunProgressInfo | None = None
    temperature: TemperatureSnapshot | None = None

    # Poll hardware if run is active
    if state in RunStates.ACTIVE:
        ble = _get_ble(request)
        if ble is not None and ble.is_connected:
            try:
                hw = await ble.get_run_status()
                run_mgr.record_temperature(
                    run_id, hw.get("block_temperature"), hw.get("lid_temperature")
                )  # noqa: E501
                progress = RunProgressInfo(
                    progress=hw.get("progress", 0),
                    elapsed_seconds=hw.get("elapsed_seconds", 0.0),
                )
            except Exception:
                logger.debug("Could not poll hardware for run %s", run_id)

    # Build temperature from latest log entry
    if run["temperature_log"]:
        last = run["temperature_log"][-1]
        temperature = TemperatureSnapshot(
            current=last.get("block"),
            lid=last.get("lid"),
            block=last.get("block"),
        )

    return RunStatusDetailResponse(
        run_id=run_id,
        state=state,
        progress=progress,
        temperature=temperature,
        errors=[StatusError(**e) for e in run["error_log"]],
    )


async def _abort_run(run_id: str, request: Request) -> RunAbortResponse:
    """POST /runs/{id}/abort -- abort a running job."""
    run_mgr = _get_run_manager(request)
    run = run_mgr.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "run_not_found",
                "severity": "error",
                "human_message": f"Run {run_id} not found",
                "operator_hint": "Check the run ID",
                "retryable": False,
                "details": {"run_id": run_id},
            },
        )

    state = run["state"]

    # Idempotent: already terminal -> return current state
    if state in RunStates.TERMINAL:
        return RunAbortResponse(
            ok=True,
            state=state,
            aborted_at=run.get("aborted_at"),
        )

    # If not running/accepted, reject
    if state not in RunStates.ACTIVE:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cannot_abort",
                "severity": "error",
                "human_message": f"Cannot abort run in state {state}",
                "operator_hint": "Only active runs can be aborted",
                "retryable": False,
                "details": {"run_id": run_id, "state": state},
            },
        )

    # Try BLE abort
    ble = _get_ble(request)
    ble_ok = False
    if ble is not None and ble.is_connected:
        try:
            await ble.abort_run()
            ble_ok = True
        except Exception:
            logger.exception("BLE abort failed for run %s", run_id)

    if ble_ok:
        run_mgr.transition_to(run_id, RunStates.ABORTED)
        logger.info("Run %s aborted by operator", run_id)
    else:
        # BLE unreachable after disconnect => mark for operator review
        run_mgr.record_error(
            run_id, "abort_ble_failed", "BLE abort failed -- device may be disconnected"
        )  # noqa: E501
        run_mgr.transition_to(run_id, RunStates.UNKNOWN_REVIEW)
        logger.warning(
            "Run %s -> unknown_requires_operator_review (BLE unreachable during abort)",
            run_id,
        )

    updated = run_mgr.get_run(run_id)
    return RunAbortResponse(
        ok=True,
        state=updated["state"],
        aborted_at=updated.get("aborted_at"),
    )


async def _get_results(run_id: str, request: Request) -> RunResultResponse:
    """GET /runs/{id}/results -- get terminal run results."""
    run_mgr = _get_run_manager(request)
    results = run_mgr.get_results(run_id)
    if results is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "run_not_found",
                "severity": "error",
                "human_message": f"Run {run_id} not found",
                "operator_hint": "Check the run ID",
                "retryable": False,
                "details": {"run_id": run_id},
            },
        )

    return RunResultResponse(
        run_id=results["run_id"],
        state=results["state"],
        profile=results["profile"],
        temperature_log=results["temperature_log"],
        started_at=results["started_at"],
        completed_at=results["completed_at"],
        aborted_at=results.get("aborted_at"),
        operator=results["operator"],
        approval_id=results["approval_id"],
        errors=[StatusError(**e) for e in results["errors"]],
        artifacts=results["artifacts"],
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(ble_client: BleClientProtocol | None = None) -> fastapi.FastAPI:
    """Create a configured FastAPI application.

    Args:
        ble_client: Optional BLE client instance. When ``None``, the app
            reports BLE as ``not_available`` and device/status endpoints
            return degraded responses.
    """
    app = fastapi.FastAPI(
        title="BentoLab HTTP API",
        version=__version__,
        description="HTTP wrapper around the BentoLab BLE control library",
    )

    # Store the BLE client and run manager in app state
    app.state.ble_client = ble_client  # type: ignore[attr-defined]
    app.state.run_manager = RunManager()  # type: ignore[attr-defined]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Tier 1 -- read / status / validation
    app.add_api_route("/health", _health, methods=["GET"], response_model=HealthResponse)
    app.add_api_route("/devices", _devices, methods=["GET"], response_model=DevicesResponse)
    app.add_api_route("/status", _status, methods=["GET"], response_model=StatusResponse)
    app.add_api_route(
        "/profiles/validate",
        _validate_profile_handler,
        methods=["POST"],
        response_model=ProfileValidationResponse,
        responses={422: {"model": ErrorResponse}},
    )

    # Tier 2 -- execution
    app.add_api_route(
        "/runs/dry-run",
        _dry_run,
        methods=["POST"],
        response_model=DryRunResponse,
        responses={422: {"model": ErrorResponse}},
    )
    app.add_api_route(
        "/runs",
        _start_run,
        methods=["POST"],
        response_model=RunAcceptedResponse,
        responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    )
    app.add_api_route(
        "/runs/{run_id}",
        _get_run_status_handler,
        methods=["GET"],
        response_model=RunStatusDetailResponse,
        responses={404: {"model": ErrorResponse}},
    )
    app.add_api_route(
        "/runs/{run_id}/abort",
        _abort_run,
        methods=["POST"],
        response_model=RunAbortResponse,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    app.add_api_route(
        "/runs/{run_id}/results",
        _get_results,
        methods=["GET"],
        response_model=RunResultResponse,
        responses={404: {"model": ErrorResponse}},
    )

    return app
