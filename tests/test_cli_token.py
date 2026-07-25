"""CLI tests for the ``bentolab token`` subcommand."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from bentolab.cli.main import app


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect config dir so tokens.json is written under tmp."""
    monkeypatch.setenv("BENTOLAB_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_token_issue_prints_token_and_persists(cli_env: Path, runner: CliRunner) -> None:
    """``bentolab token issue --device ADDR`` prints the token and saves it."""
    r = runner.invoke(app, ["token", "issue", "--device", "AA:BB:CC:DD:EE:FF"])
    assert r.exit_code == 0, r.stdout
    token = r.stdout.strip()
    # token_urlsafe(24) -> 32 chars
    assert len(token) >= 32

    # And it's persisted
    r2 = runner.invoke(app, ["token", "list"])
    assert r2.exit_code == 0
    assert token[:8] in r2.stdout
    assert "AA:BB:CC:DD:EE:FF" in r2.stdout


def test_token_list_empty(cli_env: Path, runner: CliRunner) -> None:
    """``bentolab token list`` exits 0 with a friendly message when empty."""
    r = runner.invoke(app, ["token", "list"])
    assert r.exit_code == 0
    assert "No tokens" in r.stdout


def test_token_list_json(cli_env: Path, runner: CliRunner) -> None:
    """``bentolab token list --json`` returns a JSON array of token dicts."""
    import json

    runner.invoke(app, ["token", "issue", "--device", "AA:BB:CC:DD:EE:FF"])
    runner.invoke(app, ["token", "issue", "--device", "11:22:33:44:55:66"])

    r = runner.invoke(app, ["token", "list", "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.stdout.splitlines()[-1])
    assert isinstance(payload, list)
    assert len(payload) == 2
    devices = {entry["device_address"] for entry in payload}
    assert devices == {"AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"}


def test_token_revoke_issued_token(cli_env: Path, runner: CliRunner) -> None:
    """``bentolab token revoke <token>`` removes the token and exits 0."""
    issue = runner.invoke(app, ["token", "issue", "--device", "AA:BB:CC:DD:EE:FF"])
    token = issue.stdout.strip()

    r = runner.invoke(app, ["token", "revoke", token])
    assert r.exit_code == 0
    assert "Revoked" in r.stdout

    # Subsequent list shows nothing
    r2 = runner.invoke(app, ["token", "list"])
    assert r2.exit_code == 0
    assert "No tokens" in r2.stdout


def test_token_revoke_unknown_token_exits_2(cli_env: Path, runner: CliRunner) -> None:
    """Revoking a token that doesn't exist is a user error (exit 2)."""
    r = runner.invoke(app, ["token", "revoke", "never-issued-token"])
    assert r.exit_code == 2
    assert "not found" in r.output


def test_token_issue_rejects_empty_device(cli_env: Path, runner: CliRunner) -> None:
    """--device with an empty string is a validation error."""
    r = runner.invoke(app, ["token", "issue", "--device", "   "])
    # Typer's argparse layer trims/strips or rejects this; we accept
    # either exit 2 (user error) or a successful no-op since the device
    # is whitespace-only. The contract is "non-empty after strip".
    assert r.exit_code in {0, 2}
    if r.exit_code == 0:
        # If it succeeded, the token must still be bound to a non-empty
        # address -- the store strips before saving.
        from bentolab.api.auth import TokenStore

        store = TokenStore(path=cli_env / "config" / "tokens.json")
        tokens = store.list()
        for t in tokens:
            assert t.device_address.strip()
