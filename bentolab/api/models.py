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


# ---------------------------------------------------------------------------
# Run execution
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    """Request body for POST /runs (start a real run)."""

    profile: dict[str, Any] = Field(description="PCR profile as a JSON/YAML dict")
    device_address: str | None = Field(default=None, description="BLE device address")
    approval_id: str | None = Field(default=None, description="Gateway approval token ID")
    operator: str | None = Field(default=None, description="Operator identifier")


class DryRunRequest(BaseModel):
    """Request body for POST /runs/dry-run."""

    profile: dict[str, Any] = Field(description="PCR profile as a JSON/YAML dict")


class DryRunStep(BaseModel):
    """A single step in a dry-run simulation."""

    phase: str = Field(description="Phase name (e.g. 'initial_denaturation')")
    temperature: float = Field(description="Target temperature in Celsius")
    duration_s: int = Field(description="Duration in seconds")


class DryRunSimulation(BaseModel):
    """Simulation result from a dry run."""

    duration_s: int = Field(description="Total estimated duration in seconds")
    steps: list[DryRunStep] = Field(description="Simulated steps")
    warnings: list[str] = Field(default_factory=list, description="Validation warnings")


class DryRunResponse(BaseModel):
    """Response from POST /runs/dry-run."""

    ok: bool
    simulation: DryRunSimulation | None = None
    errors: list[str] = Field(default_factory=list)


class RunAcceptedResponse(BaseModel):
    """Response from POST /runs when a run is accepted."""

    ok: bool
    run_id: str
    state: str
    started_at: str


class RunAbortResponse(BaseModel):
    """Response from POST /runs/{id}/abort."""

    ok: bool
    state: str
    aborted_at: str | None = None


class RunProgressInfo(BaseModel):
    """Run progress information."""

    progress: int = 0
    elapsed_seconds: float = 0.0


class RunStatusDetailResponse(BaseModel):
    """Response from GET /runs/{id}."""

    run_id: str
    state: str
    progress: RunProgressInfo | None = None
    temperature: TemperatureSnapshot | None = None
    errors: list[StatusError] = Field(default_factory=list)


class TemperatureLogEntry(BaseModel):
    """A single temperature snapshot in the run log."""

    t: str = Field(description="ISO8601 timestamp")
    block: float | None = None
    lid: float | None = None


class RunResultResponse(BaseModel):
    """Terminal result package from GET /runs/{id}/results.

    Available for all terminal states (completed, failed, aborted,
    unknown_requires_operator_review). Returns state='running' if
    the run is not yet complete.
    """

    run_id: str
    state: str
    profile: dict[str, Any] | None = None
    temperature_log: list[TemperatureLogEntry] = Field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None
    aborted_at: str | None = None
    operator: str | None = None
    approval_id: str | None = None
    errors: list[StatusError] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
