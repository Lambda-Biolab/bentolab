"""Bento Lab workbench TUI (Textual).

Optional extra. Install with::

    pip install bentolab[tui]

Public entry point: :func:`run`. Domain types and widgets live in
submodules; importing this package without :mod:`textual` installed is
allowed (the package ships as an empty namespace — actual widgets are
loaded on demand by :func:`run`).
"""

from __future__ import annotations

__all__: list[str] = []
