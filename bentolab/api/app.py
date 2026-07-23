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
from bentolab.runs import RunManager

from ._run_service import (
    ApprovalRequiredError,
    CannotAbortError,
    PreflightFailedError,
    RunNotFoundError,
    RunService,
    RunStartFailedError,
)
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
    TemperatureLogEntry,
    TemperatureSnapshot,
)

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
    warnings: list[str],
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
# Helpers to extract BLE client / RunService from the request
# ---------------------------------------------------------------------------


def _get_ble(request: Request) -> BleClientProtocol | None:
    """Retrieve the BLE client from app state."""
    return getattr(request.app.state, "ble_client", None)


def _get_run_service(request: Request) -> RunService:
    """Retrieve the RunService from app state, building one if absent.

    Tests inject a RunManager via app.state; if none is present we
    build a fresh manager. Production always injects one.
    """
    cached = getattr(request.app.state, "run_service", None)
    if cached is not None:
        return cached
    ble = _get_ble(request)
    run_manager: RunManager = getattr(request.app.state, "run_manager", RunManager())
    return RunService(ble, run_manager)


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
        except Exception:
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
    service = _get_run_service(request)
    try:
        started = await service.start_run(
            profile_dict=body.profile,
            device_address=body.device_address,
            operator=body.operator,
            approval_id=body.approval_id,
        )
    except PreflightFailedError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "preflight_failed",
                "severity": "error",
                "human_message": "Preflight checks failed",
                "operator_hint": "; ".join(exc.errors),
                "retryable": True,
                "details": {"errors": exc.errors},
            },
        ) from exc
    except ApprovalRequiredError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "approval_required",
                "severity": "error",
                "human_message": str(exc),
                "operator_hint": "Supply a gateway approval_id in the request body",
                "retryable": True,
                "details": {},
            },
        ) from exc
    except RunStartFailedError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "run_start_failed",
                "severity": "error",
                "human_message": str(exc),
                "operator_hint": "Check BLE connection and device state, then retry",
                "retryable": True,
                "details": {"run_id": exc.run_id},
            },
        ) from exc

    return RunAcceptedResponse(
        ok=True,
        run_id=started.run_id,
        state=started.state,
        started_at=started.started_at,
    )


async def _get_run_status_handler(run_id: str, request: Request) -> RunStatusDetailResponse:
    """GET /runs/{id} -- get run state and progress."""
    service = _get_run_service(request)
    try:
        detail = await service.get_run_status(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "run_not_found",
                "severity": "error",
                "human_message": str(exc),
                "operator_hint": "Check the run ID",
                "retryable": False,
                "details": {"run_id": run_id},
            },
        ) from exc

    progress = (
        RunProgressInfo(
            progress=detail.progress.progress,
            elapsed_seconds=detail.progress.elapsed_seconds,
        )
        if detail.progress is not None
        else None
    )
    temperature = (
        TemperatureSnapshot(
            current=detail.temperature.block,
            lid=detail.temperature.lid,
            block=detail.temperature.block,
        )
        if detail.temperature is not None
        else None
    )
    return RunStatusDetailResponse(
        run_id=detail.run_id,
        state=detail.state,
        progress=progress,
        temperature=temperature,
        errors=[StatusError(**e) for e in detail.errors],
    )


async def _abort_run(run_id: str, request: Request) -> RunAbortResponse:
    """POST /runs/{id}/abort -- abort a running job."""
    service = _get_run_service(request)
    try:
        aborted = await service.abort_run(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "run_not_found",
                "severity": "error",
                "human_message": str(exc),
                "operator_hint": "Check the run ID",
                "retryable": False,
                "details": {"run_id": run_id},
            },
        ) from exc
    except CannotAbortError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cannot_abort",
                "severity": "error",
                "human_message": str(exc),
                "operator_hint": "Only active runs can be aborted",
                "retryable": False,
                "details": {"run_id": run_id, "state": exc.state},
            },
        ) from exc

    return RunAbortResponse(
        ok=True,
        state=aborted.state,
        aborted_at=aborted.aborted_at,
    )


async def _get_results(run_id: str, request: Request) -> RunResultResponse:
    """GET /runs/{id}/results -- get terminal run results."""
    service = _get_run_service(request)
    try:
        results = service.get_results(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "run_not_found",
                "severity": "error",
                "human_message": str(exc),
                "operator_hint": "Check the run ID",
                "retryable": False,
                "details": {"run_id": run_id},
            },
        ) from exc

    return RunResultResponse(
        run_id=results.run_id,
        state=results.state,
        profile=results.profile,
        temperature_log=[TemperatureLogEntry(**entry) for entry in results.temperature_log],
        started_at=results.started_at,
        completed_at=results.completed_at,
        aborted_at=results.aborted_at,
        operator=results.operator,
        approval_id=results.approval_id,
        errors=[StatusError(**e) for e in results.errors],
        artifacts=results.artifacts,
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
