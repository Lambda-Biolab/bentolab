"""Tests for the Wi-Fi client stub.

The Wi-Fi transport is not implemented yet (no protocol was reverse-
engineered). These tests pin the contract: the documented no-op methods
raise :class:`NotImplementedError`, while ``connect``/``disconnect``
manage an HTTP session.
"""

from __future__ import annotations

import pytest

from bentolab.wifi_client import BentoLabWiFi


@pytest.mark.parametrize(
    "method_name",
    [
        "discover",
        "connect",
        "get_status",
        "get_firmware_version",
        "start_run",
        "stop_run",
    ],
)
@pytest.mark.asyncio
async def test_wifi_stub_methods_raise_not_implemented(method_name: str) -> None:
    """All public methods on the Wi-Fi stub raise NotImplementedError.

    Pinning this ensures the stub doesn't accidentally start returning
    fake values if someone half-implements a method without raising.
    """
    client = BentoLabWiFi(host="192.0.2.1")
    method = getattr(client, method_name)
    with pytest.raises(NotImplementedError, match="not yet reverse-engineered"):
        await method()


@pytest.mark.asyncio
async def test_wifi_stub_has_no_abort_run() -> None:
    """The Wi-Fi stub does not advertise abort_run / get_run_status.

    These were added to BentoLabBLE later and have no analogue on the
    Wi-Fi transport yet. Pinning the surface here catches accidental
    future additions without corresponding protocol support.
    """
    client = BentoLabWiFi(host="192.0.2.1")
    assert not hasattr(client, "abort_run")
    assert not hasattr(client, "get_run_status")


@pytest.mark.asyncio
async def test_wifi_disconnect_is_safe_when_not_connected() -> None:
    """Calling disconnect without a session is a no-op (idempotent)."""
    client = BentoLabWiFi(host="192.0.2.1")
    await client.disconnect()  # must not raise
    assert client._session is None
