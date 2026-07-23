"""``bentolab`` Typer entry point.

Subcommands:
    scan, status, monitor, run, stop, profile, logs

With no subcommand, launches the TUI workbench (requires the optional
``bentolab[tui]`` extras group to be installed).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from bentolab.tui import run as run_tui_typed  # noqa: F401

from .logs import logs_app
from .monitor import monitor_command
from .profile import profile_app
from .run import run_command
from .scan import scan_command
from .status import status_command
from .stop import stop_command

app = typer.Typer(
    name="bentolab",
    invoke_without_command=True,
    help=(
        "Bento Lab PCR workstation control. Run subcommands, or invoke with no args for "
        "the TUI workbench (requires `pip install bentolab[tui]`)."
    ),
)


@app.callback()
def _root(ctx: typer.Context) -> None:
    """If no subcommand was given, launch the workbench TUI."""
    if ctx.invoked_subcommand is not None:
        return
    try:
        # Lazy import so `bentolab[tui]` is optional; the import is
        # intentionally inside the callback path, not at module scope.
        from ..tui import run as run_tui  # type: ignore[attr-defined]

        run_tui: Callable[[], None] = run_tui  # satisfy pyright without re-exporting
    except ImportError:
        typer.echo(
            "TUI extras not installed. Install with `pip install bentolab[tui]`.",
            err=True,
        )
        raise typer.Exit(code=1) from None
    run_tui()


app.command("scan")(scan_command)
app.command("status")(status_command)
app.command("monitor")(monitor_command)
app.command("run")(run_command)
app.command("stop")(stop_command)
app.add_typer(profile_app, name="profile")
app.add_typer(logs_app, name="logs")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
