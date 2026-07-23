"""Behavioral tests for undertested CLI commands.

Existing tests in ``test_cli.py`` cover ``profile`` and ``logs`` only.
This file adds coverage for ``scan``, ``status``, ``stop``, ``run``, and
the top-level ``--help`` by mocking ``BentoLabBLE`` at the import site
of each CLI module.

Each test invokes the real Typer command and asserts on user-visible
outcomes (exit code, stdout, JSON output).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from bentolab.cli import run as cli_run_module
from bentolab.cli import scan as cli_scan_module
from bentolab.cli import status as cli_status_module
from bentolab.cli import stop as cli_stop_module
from bentolab.cli.main import app
from bentolab.protocol import StatusBroadcast


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect data + config dirs; return tmp root."""
    monkeypatch.setenv("BENTOLAB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BENTOLAB_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _stub_status(running: bool = False, block: float = 25.0, lid: float = 24.0) -> StatusBroadcast:
    return StatusBroadcast(0, 0, 0, 0, block, lid, int(running))


def _make_fake_instance(**methods: Any) -> MagicMock:
    """Build a MagicMock that mimics the BLE client async-context surface."""
    inst = MagicMock(name="BLE_instance")
    inst.__aenter__ = AsyncMock(return_value=inst)
    inst.__aexit__ = AsyncMock(return_value=None)
    inst._connected_address = "AA:BB:CC:DD:EE:FF"
    for name, mock in methods.items():
        setattr(inst, name, mock)
    return inst


def _patch_at(monkeypatch: pytest.MonkeyPatch, module: Any, **methods: Any) -> MagicMock:
    """Replace ``module.BentoLabBLE`` with a factory returning a configured mock."""
    inst = _make_fake_instance(**methods)
    fake_cls = MagicMock(name="BentoLabBLE_class", return_value=inst)
    monkeypatch.setattr(module, "BentoLabBLE", fake_cls)
    return inst


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def test_scan_empty_returns_zero(
    cli_env: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scan with no devices found exits 0, prints 'No Bento Lab devices found.'"""
    _patch_at(monkeypatch, cli_scan_module, discover=AsyncMock(return_value=[]))
    r = runner.invoke(app, ["scan"])
    assert r.exit_code == 0, r.stdout
    assert "No Bento Lab devices found" in r.stdout


def test_scan_json_outputs_device_list(
    cli_env: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--json emits a JSON array of discovered devices."""
    dev = MagicMock()
    dev.address = "AA:BB:CC:DD:EE:FF"
    dev.name = "Bento Lab"
    adv = MagicMock(rssi=-42)
    _patch_at(monkeypatch, cli_scan_module, discover=AsyncMock(return_value=[(dev, adv)]))

    r = runner.invoke(app, ["scan", "--json"])
    assert r.exit_code == 0, r.stdout
    payload = json.loads(r.stdout.splitlines()[-1])
    assert payload == [{"address": "AA:BB:CC:DD:EE:FF", "name": "Bento Lab", "rssi": -42}]


def test_scan_no_remember_skips_device_registry(
    cli_env: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--no-remember discovers but doesn't write to the device registry."""
    dev = MagicMock(address="AA:BB:CC:DD:EE:FF", name="Bento Lab")
    adv = MagicMock(rssi=-50)
    _patch_at(monkeypatch, cli_scan_module, discover=AsyncMock(return_value=[(dev, adv)]))

    r = runner.invoke(app, ["scan", "--no-remember"])
    assert r.exit_code == 0, r.stdout
    # Verify no devices.json was written
    devices_json = cli_env / "config" / "devices.json"
    assert not devices_json.exists()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_json_outputs_snapshot(
    cli_env: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """status --json emits one snapshot dict with the live block/lid temps."""
    _patch_at(
        monkeypatch,
        cli_status_module,
        get_status=AsyncMock(return_value=_stub_status(running=False, block=30.5, lid=29.0)),
    )
    r = runner.invoke(app, ["status", "--json"])
    assert r.exit_code == 0, r.stdout
    payload = json.loads(r.stdout.splitlines()[-1])
    assert int(payload["running"]) == 0  # idle
    assert payload["block_temperature"] == 30.5
    assert payload["lid_temperature"] == 29.0
    assert payload["address"] == "AA:BB:CC:DD:EE:FF"


def test_status_human_readable_output(
    cli_env: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default status (no --json) prints a human-readable line."""
    _patch_at(
        monkeypatch,
        cli_status_module,
        get_status=AsyncMock(return_value=_stub_status(block=30.5, lid=29.0)),
    )
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0, r.stdout
    assert "block=30.5" in r.stdout
    assert "lid=29.0" in r.stdout


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop_invokes_ble_stop_run_and_exits_zero(
    cli_env: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``bentolab stop`` calls ``lab.stop_run()`` and exits 0 on success."""
    stop_mock = AsyncMock()
    _patch_at(monkeypatch, cli_stop_module, stop_run=stop_mock)
    r = runner.invoke(app, ["stop"])
    assert r.exit_code == 0, r.stdout
    stop_mock.assert_awaited_once()
    assert "Stop sent" in r.stdout


def test_stop_reports_failure(
    cli_env: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If BLE stop_run raises, the CLI exits with the device-error code."""
    _patch_at(
        monkeypatch, cli_stop_module, stop_run=AsyncMock(side_effect=RuntimeError("BLE lost"))
    )
    r = runner.invoke(app, ["stop"])
    assert r.exit_code == 3  # _format.fail default code
    # Error message goes to stderr (fail() prints via stderr Console)
    assert "stop failed" in r.output  # output combines stdout + stderr


# ---------------------------------------------------------------------------
# run (no_tail mode for determinism)
# ---------------------------------------------------------------------------


def test_run_with_no_tail_starts_and_exits(
    cli_env: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``bentolab run --no-tail <name>`` uploads + starts + returns without polling."""
    runner.invoke(app, ["profile", "new", "demo", "--no-edit"])

    start_mock = AsyncMock()
    _patch_at(monkeypatch, cli_run_module, start_run=start_mock)
    r = runner.invoke(app, ["run", "demo", "--no-tail"])
    assert r.exit_code == 0, r.stdout
    start_mock.assert_awaited_once()
    assert "Started: demo" in r.stdout


def test_run_unknown_profile_fails_with_code_2(cli_env: Path, runner: CliRunner) -> None:
    """Unknown profile exits 2 (user error)."""
    r = runner.invoke(app, ["run", "ghost"])
    assert r.exit_code == 2
    assert "profile not found" in r.output


# ---------------------------------------------------------------------------
# CLI option parsing
# ---------------------------------------------------------------------------


def test_help_exits_zero_and_describes_commands(runner: CliRunner) -> None:
    """``bentolab --help`` exits 0 and lists the top-level subcommands."""
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0, r.stdout
    for cmd in ("scan", "status", "run", "stop", "profile", "logs"):
        assert cmd in r.stdout, f"{cmd!r} not listed in --help"


def test_unknown_subcommand_exits_nonzero(runner: CliRunner) -> None:
    """An unknown subcommand fails (typer default: exit code 2)."""
    r = runner.invoke(app, ["no-such-cmd"])
    assert r.exit_code != 0
