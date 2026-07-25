"""End-to-end elabFTW integration demo against a real Bento Lab device.

Proves the C22 contract + the elabFTW extensions added for #31 work
against live hardware:

  - GET /health, GET /status, GET /devices
  - POST /profiles/validate
  - POST /runs/dry-run
  - POST /runs (real run on hardware)
  - GET /runs/{id}?wait=N (long-polling)
  - POST /runs/{id}/abort
  - GET /runs/{id}/results

The HTTP client used is the real ``HttpBentoLabClient`` shipped in
``lab-copilot-gateway`` (the actual consumer per issue #31). We import
it from the gateway's source tree, not a vendored copy, so the demo
exercises the exact code that will run in production.

Usage
-----
::

    # 1. Clone lab-copilot-gateway somewhere on disk
    gh repo clone antomicblitz/lab-copilot-gateway /tmp/lab-copilot-gateway

    # 2. Make sure the bentolab device is powered on and within BLE range

    # 3. Run the demo (from the bentolab repo root)
    uv run python examples/elabftw_demo.py

    # Custom gateway location
    LAB_COPILOT_GATEWAY_SRC=/path/to/gateway/src \
        uv run python examples/elabftw_demo.py

The demo aborts the run after a few progress polls (so the device
doesn't spend a full cycle on a demo). To let the run complete,
edit ``_MAX_POLLS`` to a larger value or remove the abort call.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Gateway source location. The demo uses the real HttpBentoLabClient
# from the gateway's source tree, not a vendored copy, so we exercise
# the exact code that runs in production. Override with the
# LAB_COPILOT_GATEWAY_SRC env var if you've cloned it elsewhere.
GATEWAY_SRC = os.environ.get(
    "LAB_COPILOT_GATEWAY_SRC", "/tmp/lab-copilot-gateway/src"
)

BASE_URL = os.environ.get("BENTOLAB_API_URL", "http://127.0.0.1:8765")
RECORD_PATH = Path(__file__).parent / "experiment_record.json"

# A short PCR profile (~35s total). Designed to exercise every step
# shape (initial denaturation, one cycle of denat/anneal/extend, final
# extension) without spending a real cycle on the device.
#
# ``hold_duration_s`` is explicitly 0 -- the hold is opt-in (#12) and
# we don't want the device LCD to show a 24 h hold after the demo
# completes. Set to e.g. 86400 for a real overnight protocol.
SHORT_PROFILE: dict[str, Any] = {
    "name": "Demo Run",
    "lid_temperature": 105,
    "initial_denaturation": {"temperature": 95, "duration": 10},
    "cycles": [
        {
            "denaturation": {"temperature": 95, "duration": 5},
            "annealing": {"temperature": 58, "duration": 5},
            "extension": {"temperature": 72, "duration": 5},
            "repeat_count": 1,
        }
    ],
    "final_extension": {"temperature": 72, "duration": 10},
    "hold_temperature": 4,
    "hold_duration_s": 0,
}

# How many progress polls to do before aborting. Each poll blocks up
# to ``_POLL_WAIT_S`` seconds (long-polling), so the demo completes
# in roughly ``_MAX_POLLS * _POLL_WAIT_S`` seconds of real time.
_MAX_POLLS = 3
_POLL_WAIT_S = 10

# Server startup budget. The bentolab API needs ~2s to start, plus
# BLE discovery/connect (~5-10s for a cold adapter). 30s is plenty
# for a warm adapter.
_HEALTH_TIMEOUT_S = 30.0


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def _start_server() -> subprocess.Popen:
    """Start the bentolab API server in a background subprocess."""
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bentolab.cli.main",
            "serve",
            "start",
            "--host",
            "127.0.0.1",
            "--port",
            str(_port_from_url(BASE_URL)),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _port_from_url(url: str) -> int:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return parsed.port or 8765


def _wait_for_health(timeout_s: float = _HEALTH_TIMEOUT_S) -> dict[str, Any]:
    """Poll ``GET /health`` until status is 'ok' or the budget elapses."""
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = requests.get(f"{BASE_URL}/health", timeout=2)
            if resp.status_code == 200:
                body = resp.json()
                if body.get("status") == "ok":
                    return body
        except requests.RequestException as exc:
            last_error = exc
        time.sleep(0.5)
    msg = f"server did not become healthy in {timeout_s:.0f}s"
    if last_error is not None:
        msg += f" (last error: {last_error})"
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Demo flow
# ---------------------------------------------------------------------------


def _print_step(n: int, total: int, message: str) -> None:
    print(f"[{n}/{total}] {message}")


def _import_gateway_client() -> Any:
    """Add the gateway's src dir to sys.path and import HttpBentoLabClient.

    Done lazily and with a clear error message so the user knows what
    to do if the gateway isn't checked out.
    """
    if GATEWAY_SRC not in sys.path:
        sys.path.insert(0, GATEWAY_SRC)
    try:
        from lab_copilot_gateway.bentolab import HttpBentoLabClient
    except ImportError as exc:
        raise SystemExit(
            f"Could not import HttpBentoLabClient from {GATEWAY_SRC!r}.\n"
            f"Clone the gateway with: gh repo clone antomicblitz/lab-copilot-gateway /tmp/lab-copilot-gateway\n"
            f"Or set LAB_COPILOT_GATEWAY_SRC to the path of the gateway's src/ directory.\n"
            f"Underlying error: {exc}"
        ) from exc
    return HttpBentoLabClient


def main() -> int:
    _print_step(0, 7, f"Bento Lab elabFTW integration demo against {BASE_URL}")
    print(f"        Profile: {SHORT_PROFILE['name']!r} "
          f"(initial={SHORT_PROFILE['initial_denaturation']['duration']}s, "
          f"1 cycle, final={SHORT_PROFILE['final_extension']['duration']}s)")
    print(f"        Gateway source: {GATEWAY_SRC}")
    print()

    HttpBentoLabClient = _import_gateway_client()

    server = _start_server()
    try:
        _print_step(1, 7, "Waiting for /health to return ok...")
        health = _wait_for_health()
        print(f"        server: {health}")
        if health.get("ble") != "ok":
            print(f"        WARNING: BLE status is {health.get('ble')!r} -- "
                  "the device may be unreachable. Demo will still attempt the flow.")
        print()

        client = HttpBentoLabClient(BASE_URL)

        _print_step(2, 7, "GET /status (real device state)")
        status = client.get_status()
        print(f"        state={status.get('state')!r}  "
              f"device={status.get('device')!r}  "
              f"temp={status.get('temperature')}")
        print()

        _print_step(3, 7, "POST /profiles/validate")
        validation = client.validate_profile(SHORT_PROFILE)
        ok = validation.get("ok")
        warnings = validation.get("warnings") or []
        errors = validation.get("errors") or []
        print(f"        ok={ok}  errors={errors}  warnings={warnings}")
        if not ok:
            print("ABORT: profile validation failed")
            return 1
        print()

        _print_step(4, 7, "POST /runs/dry-run (simulate, no hardware side effects)")
        dry_run = client.dry_run(SHORT_PROFILE)
        sim = dry_run.get("simulation") or {}
        steps = sim.get("steps") or []
        print(f"        ok={dry_run.get('ok')}  "
              f"duration_s={sim.get('duration_s')}  "
              f"steps={len(steps)}")
        for step in steps:
            print(f"          - {step.get('phase')}: "
                  f"{step.get('temperature')}°C × {step.get('duration_s')}s")
        print()

        _print_step(5, 7, "POST /runs (start the real run)")
        started = client.start_run(
            profile=SHORT_PROFILE,
            approval_id="demo-approval-token",
            operator="demo-operator",
        )
        run_id = started.get("run_id")
        print(f"        run_id={run_id}  state={started.get('state')}  "
              f"was_already_running={started.get('was_already_running')}")
        if not run_id:
            print(f"ABORT: start_run returned no run_id. Full response: {started}")
            return 1
        print()

        _print_step(6, 7, "GET /runs/{id}?wait=N (long-polling) -- "
                          f"up to {_MAX_POLLS} polls of {_POLL_WAIT_S}s each")
        last_state: str | None = None
        for i in range(_MAX_POLLS):
            resp = requests.get(
                f"{BASE_URL}/runs/{run_id}",
                params={"wait": _POLL_WAIT_S},
                timeout=_POLL_WAIT_S + 5,
            )
            data = resp.json()
            last_state = data.get("state")
            print(f"        poll {i+1}: state={last_state}")
            if last_state in {"completed", "failed", "aborted", "unknown_requires_operator_review"}:
                break

        # Abort if still running -- this is a demo, we don't want to
        # spend a full PCR cycle on the device.
        if last_state in {"accepted", "running"}:
            print()
            print("Aborting the run (demo mode)...")
            abort = requests.post(f"{BASE_URL}/runs/{run_id}/abort", timeout=10)
            print(f"        abort response: {abort.json()}")
        print()

        _print_step(7, 7, "GET /runs/{id}/results (terminal result package)")
        results_resp = requests.get(f"{BASE_URL}/runs/{run_id}/results", timeout=10)
        results = results_resp.json()
        print(f"        state={results.get('state')}")
        log = results.get("temperature_log") or []
        print(f"        temperature_log entries: {len(log)}")
        print()

        # Write the experiment record -- the elabFTW use case is to
        # capture the run outcome and stuff it into the experiment's
        # custom fields.
        record = {
            "experiment_id": f"exp-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
            "device": status.get("device"),
            "profile": SHORT_PROFILE,
            "started": started,
            "final": results,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        RECORD_PATH.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        print(f"Experiment record written to: {RECORD_PATH}")
        print()
        print("Demo complete.")
        return 0
    finally:
        # Stop the server cleanly so we don't leak a process.
        print("Stopping server...")
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
