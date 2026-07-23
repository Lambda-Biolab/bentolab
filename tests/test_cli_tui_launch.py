"""Tests for the CLI's no-args TUI launch behavior.

Verifies:
- ``bentolab --help`` shows subcommands (no-op to existing behavior).
- ``bentolab`` (no args) launches the TUI when textual is available.
- ``bentolab`` (no args) prints an install hint + exits 1 when textual
  is missing.
"""

from __future__ import annotations

import sys

import pytest
from typer.testing import CliRunner

from bentolab.cli.main import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_help_lists_subcommands(runner: CliRunner) -> None:
    """``--help`` lists all installed subcommands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("scan", "status", "monitor", "run", "stop", "profile", "logs"):
        assert cmd in result.output, f"missing subcommand in help: {cmd}"


def test_help_mentions_tui_when_no_args(runner: CliRunner) -> None:
    """The Typer help text advertises the no-args TUI launch path."""
    result = runner.invoke(app, ["--help"])
    assert "TUI" in result.output or "tui" in result.output


def test_no_args_with_tui_launches_workbench(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``bentolab`` (no args) calls the ``bentolab.tui.run`` entry point.

    Patches the import target the callback actually loads from:
    ``bentolab.cli.main`` does ``from ..tui import run as run_tui``
    inside the callback; we patch ``bentolab.tui.run`` via the
    subpackage's module attrs.
    """
    captured: dict = {}

    def fake_run() -> None:
        captured["called"] = True

    # The callback does ``from ..tui import run as run_tui``. That
    # import resolves through the package's __getattr__ hook to
    # bentolab.tui.app.run. Patch that path directly.
    import bentolab.tui.app as tui_app_mod

    monkeypatch.setattr(tui_app_mod, "run", fake_run)
    runner.invoke(app, [])
    assert captured.get("called") is True, "TUI run was not invoked"


def test_no_args_without_tui_exits_1_with_hint(runner: CliRunner) -> None:
    """When ``bentolab.tui`` can't be imported, exits 1 with the install hint."""
    # Force the import to fail by registering a sentinel that's not the real module.
    real = sys.modules.pop("bentolab.tui", None)
    sys.modules["bentolab.tui"] = None  # sentinel -> ImportError when accessed
    try:
        result = runner.invoke(app, [])
        assert result.exit_code == 1
        assert "bentolab[tui]" in result.output
        assert "not installed" in result.output.lower() or "install" in result.output.lower()
    finally:
        # Restore the real module so other tests don't break.
        sys.modules.pop("bentolab.tui", None)
        if real is not None:
            sys.modules["bentolab.tui"] = real
