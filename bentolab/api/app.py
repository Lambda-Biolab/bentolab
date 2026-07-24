"""FastAPI application for the BentoLab HTTP API.

Wraps the BLE client library behind an HTTP interface following the C22
contract. Supports injection of a **ble client** (real or stub) for use
with and without hardware.

OpenAPI
-------
FastAPI auto-serves a typed OpenAPI 3.1 schema at ``/openapi.json`` and
a Swagger UI at ``/docs``. elabFTW and any other client can fetch the
schema directly to generate typed bindings; no separate export step is
needed. The schema is regenerated on every request so response models
in this file are the single source of truth.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Protocol

import fastapi
from fastapi import HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from bentolab import __version__
from bentolab.models import PCRProfile
from bentolab.protocol import StatusBroadcast
from bentolab.runs import RunManager, RunState, is_terminal

from ._run_service import (
    ApprovalRequiredError,
    CannotAbortError,
    PreflightFailedError,
    RunNotFoundError,
    RunService,
    RunStartFailedError,
)
from ._validation import validate_profile
from .auth import TokenStore, auth_required_env
from .events import EventBroker, stream_events
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

    def on_status(self, callback: Any) -> None:
        """Register a callback for status broadcasts (~5s interval)."""
        ...

    def off_status(self, callback: Any) -> None:
        """Remove a previously-registered status callback. No-op if absent."""
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


async def _events(request: Request) -> StreamingResponse:
    """GET /events -- Server-Sent Events telemetry stream.

    Streams one record per device state change (status broadcasts from
    the BLE device) and one record per periodic run-status poll. The
    stream stays open until the client disconnects. See
    :mod:`bentolab.api.events` for the wire format and event kinds.

    Keep-alive comments are emitted every 15 s so reverse proxies
    don't kill the connection during quiet runs.
    """
    ble = _get_ble(request)
    broker: EventBroker = getattr(request.app.state, "event_broker", EventBroker())

    return StreamingResponse(
        stream_events(broker, ble),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx response buffering
        },
    )


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

    # 3. Build simulation steps from the shared iter_steps generator.
    #    Same walking order as the instrument execution: initial
    #    denaturation, then each cycle's denat/anneal/extend repeated
    #    repeat_count times, then final extension.
    steps = [
        DryRunStep(
            phase=phase,
            temperature=step.temperature,
            duration_s=step.duration,
        )
        for phase, step in profile.iter_steps()
    ]

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
        was_already_running=started.was_already_running,
    )


async def _get_run_status_handler(
    run_id: str,
    request: Request,
    wait: int = Query(
        0,
        ge=0,
        le=60,
        description=(
            "If > 0, block up to this many seconds waiting for the run "
            "to transition to a terminal state. Returns the latest state "
            "when the transition happens or the timeout elapses."
        ),
    ),
) -> RunStatusDetailResponse:
    """GET /runs/{id} -- get run state and progress.

    Long-polling: pass ``?wait=N`` (0-60) to block until the run
    reaches a terminal state or N seconds elapse, whichever comes
    first. Without ``wait`` (or with ``wait=0``) the call returns
    immediately with the current state, preserving the original
    snapshot semantics.
    """
    service = _get_run_service(request)
    deadline = time.monotonic() + wait if wait > 0 else None

    # Loop: poll the run manager every ~0.5s until the run reaches a
    # terminal state or the deadline elapses. We poll the manager
    # directly (not the device) because the state machine is the
    # authoritative source once a transition has been recorded.
    while True:
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

        # Already terminal -> return immediately
        if is_terminal(detail.state):
            break

        # Not waiting, or no time left -> return current state
        if deadline is None:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        # Sleep for ~0.5s or until the remaining budget, whichever is smaller
        await asyncio.sleep(min(0.5, remaining))

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


# Endpoints that never require authentication, regardless of token store state.
# /health is the liveness probe; /openapi.json + /docs are developer-facing
# schema surfaces; /redoc is the alt docs UI.
_AUTH_EXEMPT_PATHS = frozenset({"/health", "/openapi.json", "/docs", "/redoc"})


def _unauthorized_response(message: str) -> JSONResponse:
    """Build a 401 JSON response matching the ErrorResponse envelope.

    Returns a response object (not raises) because Starlette's
    BaseHTTPMiddleware does not reliably translate raised
    HTTPExceptions into 4xx responses -- a known issue with the
    ``http`` middleware decorator. Returning a response directly is
    the documented workaround.
    """
    return JSONResponse(
        status_code=401,
        content={
            "code": "unauthorized",
            "severity": "error",
            "human_message": message,
            "operator_hint": (
                "Issue a token with `bentolab token issue --device ADDR` and "
                "send it as `Authorization: Bearer <token>`"
            ),
            "retryable": False,
            "details": {},
        },
    )


def _install_auth_middleware(
    app: fastapi.FastAPI,
    token_store: TokenStore,
    *,
    force_auth: bool,
) -> None:
    """Install a bearer-token auth middleware on ``app``.

    Behavior:

    - If no tokens are registered AND ``force_auth`` is False, requests
      pass through unauthenticated (open mode for local dev).
    - If any token is registered, or ``force_auth`` is True, every
      non-exempt endpoint requires ``Authorization: Bearer <token>``.
    - A valid token has its ``last_used_at`` updated (best effort).
    """

    @app.middleware("http")
    async def _auth_middleware(request: fastapi.Request, call_next: Any) -> Any:
        if request.url.path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)

        tokens = token_store.list()
        if not tokens and not force_auth:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return _unauthorized_response("Missing or malformed Authorization header")
        presented = auth_header[len("Bearer ") :].strip()
        if not presented:
            return _unauthorized_response("Empty bearer token")
        record = token_store.lookup(presented)
        if record is None:
            return _unauthorized_response("Invalid bearer token")
        # Best-effort touch; a write failure must not block the request.
        with contextlib.suppress(Exception):
            token_store.touch(presented)
        # Expose the device to downstream handlers via request.state.
        request.state.device_address = record.device_address
        return await call_next(request)


def create_app(
    ble_client: BleClientProtocol | None = None,
    *,
    token_store: TokenStore | None = None,
    force_auth: bool | None = None,
) -> fastapi.FastAPI:
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
    # Singleton event broker for the SSE telemetry stream. The broker
    # has no background work of its own; the SSE generator is what
    # subscribes/publishes, so the broker is cheap to construct.
    app.state.event_broker = EventBroker()  # type: ignore[attr-defined]

    # Auto-connect on startup when a real BLE client is injected. This
    # is the production behavior: the server, when started against a
    # device, should be ready to serve /runs immediately. The /health
    # endpoint still works in degraded mode (reports ble=not_available)
    # if connect fails, so the server doesn't crash on a missing device.
    if ble_client is not None:

        @app.on_event("startup")
        async def _auto_connect() -> None:
            import asyncio

            try:
                discovered = await asyncio.wait_for(
                    ble_client.discover(timeout=5.0),  # type: ignore[union-attr]
                    timeout=10.0,
                )
            except Exception:
                logger.warning(
                    "BLE discovery on startup failed; device endpoints will report disconnected"
                )
                return

            if not discovered:
                logger.info(
                    "No Bento Lab discovered on startup; device endpoints will report disconnected"
                )
                return

            # Connect to the strongest (first) device. The discover()
            # return type is ``list[tuple[Any, Any]]`` where the first
            # element of each tuple is a bleak BLEDevice.
            try:
                target = discovered[0][0]
                await asyncio.wait_for(ble_client.connect(target.address), timeout=15.0)  # type: ignore[union-attr]
                logger.info("Auto-connected to %s on startup", target.address)
            except Exception:
                logger.warning(
                    "Auto-connect on startup failed; device endpoints will report disconnected"
                )

    # Bearer-token auth: install BEFORE adding CORS so the auth
    # middleware runs first (Starlette middleware is LIFO).
    _install_auth_middleware(
        app,
        token_store or TokenStore(),
        force_auth=force_auth if force_auth is not None else auth_required_env(),
    )

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
    app.add_api_route("/events", _events, methods=["GET"])
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
