"""``bentolab`` Typer entry point.

Subcommands:
    scan, status, monitor, run, stop, profile, logs
"""

from __future__ import annotations

import typer

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
    help="Bento Lab PCR workstation control. Run subcommands or invoke with no args for the TUI.",
)


@app.callback()
def _root(ctx: typer.Context) -> None:
    """If no subcommand was given, launch the workbench TUI."""
    if ctx.invoked_subcommand is None:
        from ..tui import run as run_tui  # noqa: PLC0415

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
