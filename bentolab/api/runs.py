"""Run lifecycle management for the BentoLab HTTP API.

Implements the state machine, exclusive device locking, and result
packaging specified in the C22 contract (Tier 2 — hardware execution).

In-memory storage is used for v1; the interface is designed so a SQLite
backend (see contract "Run state persistence") can be swapped in by
replacing the dict with a database adapter that implements the same
__getitem__ / __setitem__ contract.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Run state constants  (C22 contract State model)
# ---------------------------------------------------------------------------


class RunStates:
    """Literal state values matching the contract state model."""

    IDLE = "idle"
    ACCEPTED = "accepted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"
    UNKNOWN_REVIEW = "unknown_requires_operator_review"

    # States where the run is over and the device lock is released
    TERMINAL = {COMPLETED, FAILED, ABORTED, UNKNOWN_REVIEW}

    # States where the device lock is held and hardware may be active
    ACTIVE = {ACCEPTED, RUNNING}


# ---------------------------------------------------------------------------
# RunManager
# ---------------------------------------------------------------------------


class RunManager:
    """Manages run lifecycle, exclusive device locking, and state transitions.

    Thread safety
    -------------
    FastAPI / uvicorn runs request handlers on a single event-loop thread,
    so in-memory dict access is race-free for v1. If a multi-worker or
    SQLite backend is added later, wrap critical sections with
    asyncio.Lock.

    Usage::

        mgr = RunManager()
        run_id = mgr.create_run(profile={...}, operator="alice")
        mgr.transition_to(run_id, RunStates.RUNNING)
        # ... on completion:
        mgr.transition_to(run_id, RunStates.COMPLETED)
        results = mgr.get_results(run_id)
    """

    def __init__(self) -> None:
        # run_id -> record dict
        self._runs: dict[str, dict[str, Any]] = {}

        # Exclusive device lock - at most one run at a time
        self._device_lock_run_id: str | None = None
        self._device_lock_time: float | None = None

    # ------------------------------------------------------------------
    # Device lock
    # ------------------------------------------------------------------

    def check_lock_available(self) -> tuple[bool, str | None]:
        """Read-only check whether the device lock is free.

        Returns (True, None) if available, (False, held_by_run_id)
        if another run holds the lock.
        """
        if self._device_lock_run_id is not None:
            return False, self._device_lock_run_id
        return True, None

    def force_release_lock(self) -> str | None:
        """Force-release any stale lock.

        Returns the run_id that held the lock, or None if it was
        already free.  Intended for operator intervention.

        The run record is not modified -- the operator should also
        transition the stale run to unknown_requires_operator_review
        via transition_to().
        """
        released = self._device_lock_run_id
        if released:
            logger.warning("Lock force-released for run %s", released)
        self._device_lock_run_id = None
        self._device_lock_time = None
        return released

    @property
    def locked_by(self) -> str | None:
        return self._device_lock_run_id

    @property
    def is_locked(self) -> bool:
        return self._device_lock_run_id is not None

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def create_run(
        self,
        profile: dict[str, Any],
        device_address: str | None = None,
        operator: str | None = None,
        approval_id: str | None = None,
    ) -> str:
        """Create a new run in accepted state and acquire the lock.

        Args:
            profile: The validated PCR profile dict.
            device_address: BLE address or empty for auto.
            operator: Human-readable operator identifier.
            approval_id: Gateway approval token.

        Returns:
            The new run_id string.

        The caller must have already run preflight checks and verified
        lock availability.
        """
        run_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()

        self._runs[run_id] = {
            "run_id": run_id,
            "state": RunStates.ACCEPTED,
            "profile": profile,
            "device_address": device_address or "",
            "operator": operator or "",
            "approval_id": approval_id or "",
            "created_at": now,
            "started_at": now,
            "completed_at": None,
            "aborted_at": None,
            "temperature_log": [],
            "error_log": [],
            "writeback_state": "pending",
        }

        self._acquire_lock(run_id)
        logger.info("Run %s created  state=%s", run_id, RunStates.ACCEPTED)
        return run_id

    def transition_to(self, run_id: str, new_state: str) -> bool:
        """Transition a run to new_state.

        Returns True on success, False if the run does not exist
        or the transition is invalid (terminal states are final).
        """
        run = self._runs.get(run_id)
        if run is None:
            logger.warning("transition_to: run %s not found", run_id)
            return False

        current = run["state"]

        # Terminal states are final.
        if current in RunStates.TERMINAL:
            logger.warning(
                "Cannot transition run %s from terminal state %s to %s",
                run_id,
                current,
                new_state,
            )
            return False

        now = datetime.now(UTC).isoformat()
        if new_state in RunStates.TERMINAL:
            run["completed_at"] = now
            if new_state == RunStates.ABORTED:
                run["aborted_at"] = now

        run["state"] = new_state
        logger.info("Run %s: %s -> %s", run_id, current, new_state)

        # Release the device lock on terminal states
        if new_state in RunStates.TERMINAL:
            self._release_lock()

        return True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return the raw run record, or None."""
        return self._runs.get(run_id)

    def list_active_runs(self) -> list[dict[str, Any]]:
        """Return all runs in an active (non-terminal) state."""
        return [r for r in self._runs.values() if r["state"] in RunStates.ACTIVE]

    # ------------------------------------------------------------------
    # Result package  (C22 contract Terminal result package)
    # ------------------------------------------------------------------

    def get_results(self, run_id: str) -> dict[str, Any] | None:
        """Return the terminal result package, or None if not found.

        For non-terminal runs the package contains whatever data has
        been captured so far, with the live state.
        """
        run = self._runs.get(run_id)
        if run is None:
            return None

        return {
            "run_id": run["run_id"],
            "state": run["state"],
            "profile": run["profile"],
            "temperature_log": list(run["temperature_log"]),
            "started_at": run["started_at"],
            "completed_at": run["completed_at"],
            "aborted_at": run.get("aborted_at"),
            "operator": run["operator"] or None,
            "approval_id": run["approval_id"] or None,
            "errors": list(run["error_log"]),
            "artifacts": [],
        }

    # ------------------------------------------------------------------
    # Data recording
    # ------------------------------------------------------------------

    def record_temperature(self, run_id: str, block: float | None, lid: float | None) -> None:
        """Append a temperature snapshot to the run log."""
        run = self._runs.get(run_id)
        if run is None:
            return
        run["temperature_log"].append(
            {
                "t": datetime.now(UTC).isoformat(),
                "block": block,
                "lid": lid,
            }
        )

    def record_error(self, run_id: str, code: str, message: str) -> None:
        """Append a structured error to the run log."""
        run = self._runs.get(run_id)
        if run is None:
            return
        run["error_log"].append({"code": code, "message": message})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _acquire_lock(self, run_id: str) -> None:
        """Associate the lock with run_id."""
        if self._device_lock_run_id is not None:
            raise RuntimeError(f"Device already locked by run {self._device_lock_run_id}")
        self._device_lock_run_id = run_id
        self._device_lock_time = time.monotonic()

    def _release_lock(self) -> None:
        """Release the device lock."""
        self._device_lock_run_id = None
        self._device_lock_time = None
