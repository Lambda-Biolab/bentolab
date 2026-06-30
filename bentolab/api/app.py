"""FastAPI application for the BentoLab HTTP API.

Wraps the BLE client library behind an HTTP interface following the C22
contract. Supports injection of a **ble client** (real or stub) for use
with and without hardware.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import fastapi
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware

from bentolab import __version__
from bentolab.models import PCRProfile
from bentolab.protocol import StatusBroadcast

from .models import (
    DeviceInfo,
    DevicesResponse,
    ErrorResponse,
    HealthResponse,
    ProfileValidationRequest,
    ProfileValidationResponse,
    RunStateInfo,
    StatusError,
    StatusResponse,
    TemperatureSnapshot,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BLE client protocol — the API depends on this, not on concrete classes
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
# Helpers to extract the BLE client from the request
# ---------------------------------------------------------------------------


def _get_ble(request: Request) -> BleClientProtocol | None:
    """Retrieve the BLE client from app state."""
    return getattr(request.app.state, "ble_client", None)


# ---------------------------------------------------------------------------
# Endpoint handlers
# ---------------------------------------------------------------------------


async def _health(request: Request) -> HealthResponse:
    """GET /health — never requires hardware."""
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
    """GET /devices — discover BentoLab devices via BLE scan."""
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
    """GET /status — current device state."""
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


async def _validate_profile_handler(body: ProfileValidationRequest) -> ProfileValidationResponse:
    """POST /profiles/validate — validate a PCR profile without hardware."""
    ok, errors, warnings = _validate_profile(body.profile)
    return ProfileValidationResponse(ok=ok, errors=errors, warnings=warnings)


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

    # Store the BLE client in app state so endpoint handlers can access it
    app.state.ble_client = ble_client  # type: ignore[attr-defined]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

    return app
