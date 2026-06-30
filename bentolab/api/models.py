"""Pydantic models for the BentoLab HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Response from GET /health."""

    status: str = Field(description="'ok', 'degraded', or 'down'")
    version: str = Field(description="Service version")
    ble: str = Field(description="'ok', 'not_available', or 'error'")
    wifi: str = Field(default="not_supported", description="Wi-Fi transport status")


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


class DeviceInfo(BaseModel):
    """A discovered BentoLab device."""

    address: str = Field(description="BLE MAC address")
    name: str = Field(default="", description="Device name")
    connected: bool = Field(default=False, description="Currently connected")
    transport: str = Field(default="ble", description="Transport type")


class DevicesResponse(BaseModel):
    """Response from GET /devices."""

    devices: list[DeviceInfo]


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TemperatureSnapshot(BaseModel):
    """Temperature readings."""

    current: float | None = Field(default=None, description="Current block temperature")
    lid: float | None = Field(default=None, description="Lid temperature")
    block: float | None = Field(default=None, description="Block temperature (alias)")


class RunStateInfo(BaseModel):
    """Run state summary."""

    running: bool = False
    progress: int = 0
    elapsed_seconds: float = 0.0


class StatusError(BaseModel):
    """A structured error in the status response."""

    code: str
    message: str


class StatusResponse(BaseModel):
    """Response from GET /status."""

    state: str = Field(description="'idle', 'running', 'error', or 'disconnected'")
    device: str | None = Field(default=None, description="Connected device address")
    temperature: TemperatureSnapshot = Field(default_factory=TemperatureSnapshot)
    run: RunStateInfo | None = Field(default=None, description="Run info when running")
    errors: list[StatusError] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Profile validation
# ---------------------------------------------------------------------------


class ProfileValidationRequest(BaseModel):
    """Request body for POST /profiles/validate."""

    profile: dict[str, Any] = Field(description="PCR profile as a JSON/YAML dict")


class ProfileValidationResponse(BaseModel):
    """Response from POST /profiles/validate."""

    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Error response (structured error shape)
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Structured error response body."""

    code: str
    severity: str = "error"
    human_message: str
    operator_hint: str = ""
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
