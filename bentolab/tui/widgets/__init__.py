"""Bento Lab workbench UI widgets.

Companion to :mod:`bentolab.tui.messages`: each widget subscribes to
the message types defined there via Textual's ``on_<message>``
handlers. Widgets are loaded on demand by :class:`bentolab.tui.app.BentoLabApp`.
"""

from __future__ import annotations
