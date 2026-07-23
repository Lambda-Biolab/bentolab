"""TUI services — pure-function support modules used by :mod:`bentolab.tui.app`.

Each submodule owns a single concern: run-history summarization,
orphan-run attachment, or session wiring. They are loaded on demand
by :class:`bentolab.tui.app.BentoLabApp`.
"""

from __future__ import annotations
