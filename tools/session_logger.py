"""Backwards-compat shim — :class:`SessionLogger` lives in :mod:`bentolab._logging`."""

from bentolab._logging import SessionLogger

__all__ = ["SessionLogger"]
