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
from bentolab.runs import RunManager, RunState

from ._run_service import (
    ApprovalRequiredError,
    CannotAbortError,
    PreflightFailedError,
    RunNotFoundError,
    RunService,
    RunStartFailedError,
)
from ._validation import validate_profile
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

    async def get_run_status(self) -> RunState:
        """Poll the current run status from the device.

        Returns a :class:`~bentolab.runs.RunState` with the lifecycle
        state, progress (0-100), block + lid temperatures, and elapsed
        seconds.
        """
        ...


# ---------------------------------------------------------------------------
# Profile validation helpers (constants + validate_profile live in _validation.py)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Device discovery helpers
# ---------------------------------------------------------------------------


def _http_error_body(
    code: str,
    human_message: str,
    operator_hint: str,
    *,
    status_code: int,
    retryable: bool,
    details: dict[str, Any] | None = None,
) -> HTTPException:
    """Build the canonical ErrorResponse-shaped HTTPException.

    All endpoints emit errors with the same envelope (code, severity,
    human_message, operator_hint, retryable, details). This helper
    keeps the envelope shape in one place so a future schema change
    doesn't require touching every handler.
    """
    body: dict[str, Any] = {
        "code": code,
        "severity": "error",
        "human_message": human_message,
        "operator_hint": operator_hint,
        "retryable": retryable,
        "details": details if details is not None else {},
    }
    return HTTPException(status_code=status_code, detail=body)


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

    temps = TemperatureSnapshot.from_readings(
        block=float(raw.block_temperature),
        lid=float(raw.lid_temperature),
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
    ok, errors, warnings, _profile = validate_profile(body.profile)
    return ProfileValidationResponse(ok=ok, errors=errors, warnings=warnings)


async def _dry_run(body: DryRunRequest) -> DryRunResponse:
    """POST /runs/dry-run -- simulate a run without hardware."""
    # 1. Validate the profile (gets the parsed profile in one pass)
    ok, errors, warnings, profile = validate_profile(body.profile)
    if not ok:
        return DryRunResponse(ok=False, errors=errors)
    assert profile is not None  # noqa: S101  type-narrowing for pyright: ok=True guarantees a parsed profile

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
        raise _http_error_body(
            status_code=400,
            retryable=True,
            code="preflight_failed",
            human_message="Preflight checks failed",
            operator_hint="; ".join(exc.errors),
            details={"errors": exc.errors},
        ) from exc
    except ApprovalRequiredError as exc:
        raise _http_error_body(
            status_code=400,
            retryable=True,
            code="approval_required",
            human_message=str(exc),
            operator_hint="Supply a gateway approval_id in the request body",
        ) from exc
    except RunStartFailedError as exc:
        raise _http_error_body(
            status_code=500,
            retryable=True,
            code="run_start_failed",
            human_message=str(exc),
            operator_hint="Check BLE connection and device state, then retry",
            details={"run_id": exc.run_id},
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
        raise _http_error_body(
            status_code=404,
            retryable=False,
            code="run_not_found",
            human_message=str(exc),
            operator_hint="Check the run ID",
            details={"run_id": run_id},
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
        TemperatureSnapshot.from_readings(
            block=detail.temperature.block,
            lid=detail.temperature.lid,
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
        raise _http_error_body(
            status_code=404,
            retryable=False,
            code="run_not_found",
            human_message=str(exc),
            operator_hint="Check the run ID",
            details={"run_id": run_id},
        ) from exc
    except CannotAbortError as exc:
        raise _http_error_body(
            status_code=409,
            retryable=False,
            code="cannot_abort",
            human_message=str(exc),
            operator_hint="Only active runs can be aborted",
            details={"run_id": run_id, "state": exc.state},
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
        raise _http_error_body(
            status_code=404,
            retryable=False,
            code="run_not_found",
            human_message=str(exc),
            operator_hint="Check the run ID",
            details={"run_id": run_id},
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
