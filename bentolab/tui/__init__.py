"""Textual workbench TUI for the Bento Lab.

Entry point: :func:`bentolab.tui.app.run`. Wired to the Typer CLI via
``bentolab`` (no args) — see ``bentolab.cli.main``.
"""

from .app import BentoLabApp, run

__all__ = ["BentoLabApp", "run"]
