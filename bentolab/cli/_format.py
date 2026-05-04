"""Output helpers shared by every subcommand."""

from __future__ import annotations

import json
import sys
from typing import Any, NoReturn

from rich.console import Console

stdout = Console()
stderr = Console(stderr=True)


def emit_json(payload: Any) -> None:
    """Write one JSON document followed by a newline to stdout."""
    json.dump(payload, sys.stdout, default=str)
    sys.stdout.write("\n")


def warn(msg: str) -> None:
    stderr.print(f"[yellow]warning:[/yellow] {msg}")


def fail(msg: str, code: int = 3) -> NoReturn:
    """Print an error to stderr and exit with ``code``.

    Exit code conventions (mirrored in the top-level help):
    ``0`` ok, ``2`` user error, ``3`` device error, ``4`` aborted.
    """
    stderr.print(f"[red]error:[/red] {msg}")
    raise SystemExit(code)
