"""Shared address-resolution helper for CLI subcommands."""

from __future__ import annotations

from .. import devices as device_registry
from ._format import fail


def resolve_address(explicit: str | None) -> str | None:
    """Return ``explicit`` if given, else the most-recently-seen device.

    Returns ``None`` if no devices are remembered — the BLE client will
    auto-discover. Calls :func:`fail` only if the user passed an
    address that's clearly malformed (empty after strip).
    """
    if explicit is not None:
        explicit = explicit.strip()
        if not explicit:
            fail("--device address is empty", code=2)
        return explicit

    known = device_registry.list_devices()
    if not known:
        return None
    known.sort(key=lambda d: d.last_seen, reverse=True)
    return known[0].address
