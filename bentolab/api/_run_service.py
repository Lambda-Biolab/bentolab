"""Run orchestration service for the BentoLab HTTP API.

Owns the business logic for run lifecycle: preflight, start, status,
abort, results. Pulls BLE client and run manager together so route
handlers stay thin and stay type-safe (no ``Optional[dict]`` leaking
out of the service boundary).

Usage from a handler::

    service = _get_run_service(request)
    try:
        result = await service.get_run_status(run_id)
    except RunNotFoundError:
        raise HTTPException(404, ...)

The service never raises :class:`HTTPException` -- domain exceptions
are translated at the handler boundary so the service remains usable
from non-HTTP contexts (CLI, batch scripts, future in-process callers).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..runs import RunLifecycle, RunManager, is_active, is_terminal
from ._validation import validate_profile

if TYPE_CHECKING:
    from .app import BleClientProtocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class RunServiceError(Exception):
    """Base class for service-level errors."""


class RunNotFoundError(RunServiceError):
    """Raised when a run_id does not exist in the manager."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"Run {run_id} not found")
        self.run_id = run_id


class PreflightFailedError(RunServiceError):
    """Raised when hardware preflight checks fail before starting a run."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class CannotAbortError(RunServiceError):
    """Raised when abort is called on a run in a non-active state."""

    def __init__(self, run_id: str, state: str) -> None:
        super().__init__(f"Cannot abort run {run_id} in state {state}")
        self.run_id = run_id
        self.state = state


class RunStartFailedError(RunServiceError):
    """Raised when the BLE transport rejects a start_run call."""

    def __init__(self, run_id: str, cause: Exception) -> None:
        super().__init__(f"Failed to start run {run_id} on hardware: {cause}")
        self.run_id = run_id
        self.cause = cause


class ApprovalRequiredError(RunServiceError):
    """Raised when start_run is called without an approval_id."""


# ---------------------------------------------------------------------------
# Service result types
# ---------------------------------------------------------------------------


@dataclass
class StartedRun:
    """Result of :meth:`RunService.start_run`."""

    run_id: str
    state: RunLifecycle
    started_at: str


@dataclass
class RunStatusDetail:
    """Result of :meth:`RunService.get_run_status`."""

    run_id: str
    state: RunLifecycle
    progress: ProgressInfo | None
    temperature: TemperatureReading | None
    errors: list[dict[str, str]]


@dataclass
class ProgressInfo:
    progress: int
    elapsed_seconds: float


@dataclass
class TemperatureReading:
    block: float | None
    lid: float | None


@dataclass
class AbortedRun:
    run_id: str
    state: RunLifecycle
    aborted_at: str | None


@dataclass
class RunResults:
    run_id: str
    state: RunLifecycle
    profile: dict[str, Any]
    temperature_log: list[dict[str, Any]]
    started_at: str | None
    completed_at: str | None
    aborted_at: str | None
    operator: str | None
    approval_id: str | None
    errors: list[dict[str, str]] = field(default_factory=list)
    artifacts: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class RunService:
    """Orchestrates PCR runs: preflight, start, status, abort, results.

    Both ``ble`` and ``run_manager`` are required dependencies at the
    handler boundary. ``ble`` may be ``None`` at construction time to
    represent a deployment without BLE hardware -- preflight and start
    paths will then raise :class:`PreflightFailedError`.
    """

    def __init__(
        self,
        ble: BleClientProtocol | None,
        run_manager: RunManager,
    ) -> None:
        self._ble = ble
        self._run_manager = run_manager

    # ------------------------------------------------------------------
    # Preflight
    # ------------------------------------------------------------------

    async def preflight(
        self,
        profile_dict: dict[str, Any],
        device_address: str | None,
    ) -> list[str]:
        """Run hardware preflight checks. Returns list of errors (empty = pass).

        Checks (per contract):
          1. BLE adapter available
          2. Device reachable / connected
          3. Device idle (not already running)
          4. Profile compatible
          5. Device lock available
        """
        errors: list[str] = []

        # 1. BLE adapter available
        if self._ble is None:
            errors.append("BLE adapter not available")
            return errors

        # 2. Device connected
        if not self._ble.is_connected:
            errors.append("Device not connected")

        # 3. Device idle
        try:
            status = await self._ble.get_status()
            if status.running:
                errors.append("Device is already running a job")
        except Exception:
            errors.append("Failed to query device status")

        # 4. Profile compatible — caller is expected to have run
        #    validate_profile() already. We re-check via the public
        #    function so /runs/dry-run (which calls preflight without
        #    a separate validate) still gets the check.
        ok, profile_errors, _warnings, _parsed = validate_profile(profile_dict)
        if not ok:
            errors.extend(profile_errors)

        # 5. Device lock available
        lock_ok, held_by = self._run_manager.check_lock_available()
        if not lock_ok:
            errors.append(
                f"Device is busy with run {held_by} -- wait for it to finish or force-release"
            )

        return errors

    # ------------------------------------------------------------------
    # Start run
    # ------------------------------------------------------------------

    async def start_run(
        self,
        profile_dict: dict[str, Any],
        device_address: str | None,
        operator: str | None,
        approval_id: str | None,
    ) -> StartedRun:
        """Preflight + acquire lock + start on hardware. Returns the new run record.

        Raises:
            PreflightFailedError: hardware preflight failed.
            ApprovalRequiredError: no approval_id was supplied.
            RunStartFailedError: BLE transport rejected start_run.
        """
        # Validate profile once — get the parsed PCRProfile for both
        # preflight's profile check AND the hardware start below.
        ok, profile_errors, _warnings, profile = validate_profile(profile_dict)
        if not ok:
            raise PreflightFailedError(profile_errors)
        assert profile is not None  # noqa: S101  type-narrowing for pyright: ok=True guarantees a parsed profile

        # --- Preflight (hardware checks) ---
        pf_errors = await self.preflight(profile_dict, device_address)
        if pf_errors:
            raise PreflightFailedError(pf_errors)

        # --- Approval check ---
        if not approval_id:
            raise ApprovalRequiredError("Approval ID is required to start a run")

        # At this point preflight has confirmed self._ble is not None
        assert self._ble is not None, "preflight should reject when BLE is missing"  # noqa: S101  type-narrowing for pyright

        # --- Create run record (acquires lock) ---
        run_id = self._run_manager.create_run(
            profile=profile_dict,
            device_address=device_address,
            operator=operator,
            approval_id=approval_id,
        )

        # --- Start on hardware ---
        try:
            # Reuse the profile parsed above (no second
            # PCRProfile.from_dict pass on the hot path).
            await self._ble.start_run(profile)
            self._run_manager.transition_to(run_id, RunLifecycle.RUNNING)
        except Exception as exc:
            logger.exception("Failed to start run %s on hardware", run_id)
            self._run_manager.record_error(run_id, "run_start_failed", str(exc))
            self._run_manager.transition_to(run_id, RunLifecycle.FAILED)
            raise RunStartFailedError(run_id, exc) from exc

        run = self._run_manager.get_run(run_id)
        assert run is not None, "run was just created"  # noqa: S101  type-narrowing for pyright
        return StartedRun(
            run_id=run_id,
            state=RunLifecycle.RUNNING,
            started_at=run["started_at"],
        )

    # ------------------------------------------------------------------
    # Get run status
    # ------------------------------------------------------------------

    async def get_run_status(self, run_id: str) -> RunStatusDetail:
        """Look up a run by id; if active, poll hardware for live progress.

        Raises:
            RunNotFoundError: run_id does not exist.
        """
        run = self._run_manager.get_run(run_id)
        if run is None:
            raise RunNotFoundError(run_id)

        state = run["state"]
        progress: ProgressInfo | None = None
        temperature: TemperatureReading | None = None

        if is_active(state) and self._ble is not None and self._ble.is_connected:
            try:
                hw = await self._ble.get_run_status()
                self._run_manager.record_temperature(
                    run_id, hw.get("block_temperature"), hw.get("lid_temperature")
                )
                progress = ProgressInfo(
                    progress=int(hw.get("progress", 0)),
                    elapsed_seconds=float(hw.get("elapsed_seconds", 0.0)),
                )
            except Exception:
                logger.debug("Could not poll hardware for run %s", run_id)

        if run["temperature_log"]:
            last = run["temperature_log"][-1]
            temperature = TemperatureReading(
                block=last.get("block"),
                lid=last.get("lid"),
            )

        return RunStatusDetail(
            run_id=run_id,
            state=RunLifecycle(state),
            progress=progress,
            temperature=temperature,
            errors=list(run["error_log"]),
        )

    # ------------------------------------------------------------------
    # Abort run
    # ------------------------------------------------------------------

    async def abort_run(self, run_id: str) -> AbortedRun:
        """Abort an active run. Idempotent on terminal runs.

        Raises:
            RunNotFoundError: run_id does not exist.
            CannotAbortError: run is in a non-active state and not yet terminal.
        """
        run = self._run_manager.get_run(run_id)
        if run is None:
            raise RunNotFoundError(run_id)

        state = run["state"]

        # Idempotent: already terminal -> return current state
        if is_terminal(state):
            return AbortedRun(
                run_id=run_id,
                state=RunLifecycle(state),
                aborted_at=run.get("aborted_at"),
            )

        if not is_active(state):
            raise CannotAbortError(run_id, str(state))

        ble_ok = False
        if self._ble is not None and self._ble.is_connected:
            try:
                await self._ble.abort_run()
                ble_ok = True
            except Exception:
                logger.exception("BLE abort failed for run %s", run_id)

        if ble_ok:
            self._run_manager.transition_to(run_id, RunLifecycle.ABORTED)
            logger.info("Run %s aborted by operator", run_id)
        else:
            self._run_manager.record_error(
                run_id, "abort_ble_failed", "BLE abort failed -- device may be disconnected"
            )
            self._run_manager.transition_to(run_id, RunLifecycle.UNKNOWN_REVIEW)
            logger.warning(
                "Run %s -> unknown_requires_operator_review (BLE unreachable during abort)",
                run_id,
            )

        updated = self._run_manager.get_run(run_id)
        assert updated is not None, "run was just looked up"  # noqa: S101  type-narrowing for pyright
        return AbortedRun(
            run_id=run_id,
            state=RunLifecycle(updated["state"]),
            aborted_at=updated.get("aborted_at"),
        )

    # ------------------------------------------------------------------
    # Get results
    # ------------------------------------------------------------------

    def get_results(self, run_id: str) -> RunResults:
        """Return the terminal result package for a run.

        Raises:
            RunNotFoundError: run_id does not exist.
        """
        results = self._run_manager.get_results(run_id)
        if results is None:
            raise RunNotFoundError(run_id)

        return RunResults(
            run_id=results["run_id"],
            state=RunLifecycle(results["state"]),
            profile=results["profile"],
            temperature_log=list(results["temperature_log"]),
            started_at=results["started_at"],
            completed_at=results["completed_at"],
            aborted_at=results.get("aborted_at"),
            operator=results["operator"] or None,
            approval_id=results["approval_id"] or None,
            errors=list(results["errors"]),
            artifacts=list(results["artifacts"]),
        )
