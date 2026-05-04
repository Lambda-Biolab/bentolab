"""``bentolab logs ...`` — read run-log NDJSON files."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from .._data_dirs import runs_dir
from ._format import emit_json, fail, stdout

logs_app = typer.Typer(help="Inspect run logs.")


@logs_app.command("list")
def list_cmd(json_output: bool = typer.Option(False, "--json")) -> None:
    """List all run-log files in chronological order."""
    files = sorted(runs_dir().glob("*.jsonl"))
    if json_output:
        emit_json([f.name for f in files])
        return
    if not files:
        stdout.print("[yellow]No runs recorded yet.[/yellow]")
        return
    for f in files:
        stdout.print(f"  {f.name}")


@logs_app.command("show")
def show_cmd(
    run_id: str = typer.Argument(..., help="Run-log filename (or filename stem)."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Stream a run-log to stdout (NDJSON pass-through with --json, pretty otherwise)."""
    path = _resolve(run_id)
    if path is None:
        fail(f"run not found: {run_id}", code=2)
    if json_output:
        sys.stdout.write(path.read_text(encoding="utf-8"))
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            stdout.print(line)
            continue
        ts = entry.pop("_ts", "")
        kind = entry.pop("type", "?")
        stdout.print(f"[dim]{ts}[/dim] [cyan]{kind}[/cyan] {entry}")


def _resolve(run_id: str) -> Path | None:
    base = runs_dir()
    direct = base / run_id
    if direct.exists():
        return direct
    direct_jsonl = base / f"{run_id}.jsonl"
    if direct_jsonl.exists():
        return direct_jsonl
    matches = list(base.glob(f"*{run_id}*.jsonl"))
    if len(matches) == 1:
        return matches[0]
    return None
