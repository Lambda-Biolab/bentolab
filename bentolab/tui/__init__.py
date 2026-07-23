"""Bento Lab workbench TUI (Textual).

Optional extra. Install with::

    pip install bentolab[tui]

Public entry point: :func:`run`. Submodules:

- :mod:`bentolab.tui.app` — the :class:`BentoLabApp` (lazy-imported by
  :func:`run` so importing this package doesn't pull textual eagerly).
- :mod:`bentolab.tui.messages` — Textual messages posted by services.
- :mod:`bentolab.tui.services.session` — owns the BLE client.
- :mod:`bentolab.tui.services.run_history` / :mod:`...orphan_attach`
  — pure logic over the runs data dir.
- :mod:`bentolab.tui.widgets.*` — six widgets (devices, profiles, run
  history, status, program diagram, temp chart).
- :mod:`bentolab.tui.modals.*` — splash, scan, confirm-run, confirm-quit.
- :mod:`bentolab.tui._stages` — TUI-local stage-walker built on
  :meth:`bentolab.models.PCRProfile.iter_steps`.
"""

from __future__ import annotations

__all__ = ["BentoLabApp", "run"]


def __getattr__(name: str) -> object:
    """Lazy import so the package is importable without textual installed.

    Returns the typed subclass through ``PEP 562`` module-level
    ``__getattr__``; pyright sees the attribute as unresolved but at
    runtime it's available after the first access.
    """
    if name == "BentoLabApp":
        from .app import BentoLabApp as _BentoLabApp

        return _BentoLabApp
    if name == "run":
        from .app import run as _run

        return _run
    raise AttributeError(f"module 'bentolab.tui' has no attribute {name!r}")
