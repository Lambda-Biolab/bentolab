"""CLI smoke tests via Typer's CliRunner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bentolab.cli.main import app


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect data dir to tmp; return the data root."""
    monkeypatch.setenv("BENTOLAB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BENTOLAB_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# --- profile lifecycle ---


def test_profile_new_no_edit_then_list(cli_env: Path, runner: CliRunner) -> None:
    r = runner.invoke(app, ["profile", "new", "demo", "--no-edit"])
    assert r.exit_code == 0, r.stdout
    r = runner.invoke(app, ["profile", "list", "--json"])
    assert r.exit_code == 0
    assert json.loads(r.stdout.splitlines()[-1]) == ["demo"]


def test_profile_new_rejects_duplicate(cli_env: Path, runner: CliRunner) -> None:
    runner.invoke(app, ["profile", "new", "demo", "--no-edit"])
    r = runner.invoke(app, ["profile", "new", "demo", "--no-edit"])
    assert r.exit_code == 2


def test_profile_show_json(cli_env: Path, runner: CliRunner) -> None:
    runner.invoke(app, ["profile", "new", "demo", "--no-edit"])
    r = runner.invoke(app, ["profile", "show", "demo", "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.stdout.splitlines()[-1])
    assert payload["name"] == "demo"
    assert payload["lid_temperature"] == 110


def test_profile_delete(cli_env: Path, runner: CliRunner) -> None:
    runner.invoke(app, ["profile", "new", "demo", "--no-edit"])
    r = runner.invoke(app, ["profile", "delete", "demo"])
    assert r.exit_code == 0
    r = runner.invoke(app, ["profile", "list", "--json"])
    assert json.loads(r.stdout.splitlines()[-1]) == []


def test_profile_show_missing(cli_env: Path, runner: CliRunner) -> None:
    r = runner.invoke(app, ["profile", "show", "ghost"])
    assert r.exit_code == 2


def test_profile_import(cli_env: Path, runner: CliRunner, tmp_path: Path) -> None:
    yaml_path = tmp_path / "src.yaml"
    yaml_path.write_text(
        "name: imported\n"
        "lid_temperature: 105\n"
        "initial_denaturation: { temperature: 95, duration: 60 }\n"
        "cycles: []\n"
        "final_extension: { temperature: 72, duration: 60 }\n"
        "hold_temperature: 4\n"
    )
    r = runner.invoke(app, ["profile", "import", str(yaml_path)])
    assert r.exit_code == 0
    r = runner.invoke(app, ["profile", "show", "imported", "--json"])
    payload = json.loads(r.stdout.splitlines()[-1])
    assert payload["lid_temperature"] == 105


# --- logs ---


def test_logs_list_empty(cli_env: Path, runner: CliRunner) -> None:
    r = runner.invoke(app, ["logs", "list", "--json"])
    assert r.exit_code == 0
    assert json.loads(r.stdout.splitlines()[-1]) == []


def test_logs_show_missing(cli_env: Path, runner: CliRunner) -> None:
    r = runner.invoke(app, ["logs", "show", "nope"])
    assert r.exit_code == 2


# --- run wires through profile_store + ble client ---


def test_run_unknown_profile_fails_cleanly(cli_env: Path, runner: CliRunner) -> None:
    r = runner.invoke(app, ["run", "ghost"])
    assert r.exit_code == 2
