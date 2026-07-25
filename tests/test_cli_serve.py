"""Subprocess smoke test for ``bentolab serve start``.

The ``serve`` command wraps ``uvicorn.run`` which blocks until shutdown,
so it can't be exercised by ``CliRunner`` (the in-process runner
would deadlock on the foreground server). This test starts the real
server in a subprocess on a random port, polls ``/health`` until
ready, makes a couple of basic requests, and verifies a clean
shutdown on SIGTERM.

Coverage: closes the gap on ``bentolab/cli/serve.py`` (was 53% because
``uvicorn.run`` is never invoked in unit tests).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
HEALTH_TIMEOUT_S = 15.0
SHUTDOWN_TIMEOUT_S = 10.0


def _free_port() -> int:
    """Ask the kernel for a free TCP port; closes the socket immediately.

    A small race exists between this and the server binding, but in
    practice on a single-host dev box the window is microseconds and
    the test will simply retry via the readiness poll below.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _wait_for_health(port: int, timeout: float = HEALTH_TIMEOUT_S) -> dict[str, object]:
    """Poll ``/health`` until 200 OK or raise TimeoutError."""
    url = f"http://{HOST}:{port}/health"
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if resp.status == 200:
                    return json.loads(resp.read())
        except (urllib.error.URLError, ConnectionResetError, OSError) as exc:
            last_err = exc
        time.sleep(0.2)
    raise TimeoutError(f"/health never returned 200 within {timeout}s; last error: {last_err}")


def _http_get(path: str, port: int, timeout: float = 5.0) -> tuple[int, str]:
    url = f"http://{HOST}:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def test_serve_start_in_subprocess_returns_health_and_exits_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``bentolab serve start --no-hw`` boots a real uvicorn, serves /health, exits on SIGTERM."""
    # Isolate data / config dirs so the test doesn't pollute the host.
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    monkeypatch.setenv("BENTOLAB_DATA_DIR", str(data_dir))
    monkeypatch.setenv("BENTOLAB_CONFIG_DIR", str(config_dir))

    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bentolab.cli.main",
            "serve",
            "start",
            "--no-hw",
            "--host",
            HOST,
            "--port",
            str(port),
        ],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "BENTOLAB_DATA_DIR": str(data_dir),
            "BENTOLAB_CONFIG_DIR": str(config_dir),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # 1. Wait for the server to come up.
        health = _wait_for_health(port)
        assert health["status"] == "ok"
        # /health never requires hardware: with --no-hw, ble should be not_available.
        assert health["ble"] == "not_available"

        # 2. /openapi.json is the next-cheap endpoint to hit (auth-exempt).
        status, body = _http_get("/openapi.json", port)
        assert status == 200, body
        spec = json.loads(body)
        assert spec["info"]["title"] == "BentoLab HTTP API"

        # 3. /docs is also auth-exempt and rendered by FastAPI.
        status, _ = _http_get("/docs", port)
        assert status == 200

        # 4. GET /devices (auth-exempt-ish, degraded-mode safe) -- proves
        #    a tier-1 read endpoint is wired and reachable.
        status, body = _http_get("/devices", port)
        assert status == 200, body
    finally:
        # 5. Clean shutdown via SIGTERM. uvicorn handles it cleanly,
        #    but Python reports a process terminated by a signal as
        #    exit code = -N (negated signal number). So exit_code 0
        #    means "uvicorn exited cleanly" and -SIGTERM (-15) means
        #    "uvicorn handled SIGTERM via its shutdown hook". Both
        #    are success.
        proc.terminate()
        try:
            exit_code = proc.wait(timeout=SHUTDOWN_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            stderr = proc.stderr.read() if proc.stderr else ""
            pytest.fail(f"Server did not shut down within {SHUTDOWN_TIMEOUT_S}s. stderr:\n{stderr}")
        # uvicorn's graceful shutdown prints "Finished server process"
        # on its way out. If we see that log, the shutdown was clean
        # regardless of the exit code.
        stderr = proc.stderr.read() if proc.stderr else ""
        assert "Finished server process" in stderr, (
            f"Server did not shut down cleanly; exit={exit_code}; stderr:\n{stderr}"
        )


def test_serve_start_help_succeeds_without_booting_uvicorn() -> None:
    """``bentolab serve start --help`` exercises the Typer wiring without starting uvicorn.

    Sanity check so a refactor that breaks the option declarations
    fails fast without spinning up a subprocess.

    Why we don't assert on rendered help text: Typer's Rich panel
    rendering depends on terminal width and CI's TTY (80 columns)
    wraps the options panel in a way that can hide short-help options
    like ``--host`` and ``--port`` from captured stdout on some
    Python versions, even though they're declared. The CliRunner
    contract we care about is: ``--help`` returns exit 0, the
    "Usage:" header is present (so the command was found), and the
    docstring is rendered (so the command body was reached). Anything
    more fragile than that is a rendering-side concern.
    """
    from bentolab.cli.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["serve", "start", "--help"])

    # Strip ANSI escape codes for substring matching; Click/Rich emit
    # colour codes that would make literal searches flaky.
    import re

    ansi_re = re.compile(r"\x1b\[[0-9;]*m")
    plain = ansi_re.sub("", result.output)

    assert result.exit_code == 0, result.output
    assert "Usage:" in plain, plain
    assert "bentolab serve start" in plain, plain
    assert "Run the BentoLab HTTP API server" in plain, plain
