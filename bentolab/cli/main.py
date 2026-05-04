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
    no_args_is_help=True,
    help="Bento Lab PCR workstation control. Run subcommands or `bentolab profile new`.",
)
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
