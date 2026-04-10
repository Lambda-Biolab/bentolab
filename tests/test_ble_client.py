"""Tests for BentoLabBLE client with mocked BLE."""

import pytest

from bentolab.ble_client import (
    BentoLabBLE,
    BentoLabCommandError,
    BentoLabConnectionError,
    PCRRunState,
    ProfileData,
)

# --- Fixtures ---


@pytest.fixture
def lab():
    """BentoLabBLE instance (not connected)."""
    return BentoLabBLE(address="AA:BB:CC:DD:EE:FF")


# --- Construction tests ---


def test_default_construction():
    lab = BentoLabBLE()
    assert lab.address is None
    assert not lab.is_connected
    assert lab.auto_reconnect is True


def test_construction_with_address():
    lab = BentoLabBLE(address="AA:BB:CC:DD:EE:FF")
    assert lab.address == "AA:BB:CC:DD:EE:FF"


def test_construction_with_name_filter():
    lab = BentoLabBLE(name_filter=r"MyDevice")
    assert lab.name_filter.pattern == "MyDevice"


def test_not_connected_by_default(lab):
    assert not lab.is_connected


# --- Connection tests ---


def test_check_connected_raises_when_not_connected(lab):
    with pytest.raises(BentoLabConnectionError, match="Not connected"):
        lab._check_connected()


# --- Error types ---


def test_error_hierarchy():
    from bentolab.ble_client import BentoLabError

    assert issubclass(BentoLabConnectionError, BentoLabError)
    assert issubclass(BentoLabCommandError, BentoLabError)


# --- PCRRunState ---


def test_pcr_run_state_defaults():
    state = PCRRunState()
    assert not state.running
    assert state.progress == 0
    assert state.block_temperature == 0.0
    assert state.lid_temperature == 0.0
    assert state.elapsed_seconds == 0.0


def test_pcr_run_state_values():
    state = PCRRunState(
        running=True,
        progress=42,
        block_temperature=95.0,
        lid_temperature=110.0,
        elapsed_seconds=120.0,
    )
    assert state.running
    assert state.progress == 42
    assert state.block_temperature == 95.0


# --- ProfileData ---


def test_profile_data_defaults():
    p = ProfileData()
    assert p.name == ""
    assert p.slot == 0
    assert p.stages == []
    assert p.cycles == []
    assert p.lid_temperature == 0.0


# --- Notification handler ---


def test_on_notify_parses_status(lab):
    data = bytearray(b"bb;0;0;0;0;20;25;0")
    lab._on_notify(None, data)
    assert lab._last_status is not None
    assert lab._last_status.block_temperature == 20
    assert lab._last_status.lid_temperature == 25


def test_on_notify_calls_status_callbacks(lab):
    called = []
    lab.on_status(lambda s: called.append(s))
    lab._on_notify(None, bytearray(b"bb;1;0;0;0;95;110;0"))
    assert len(called) == 1
    assert called[0].running == 1
    assert called[0].block_temperature == 95


def test_on_notify_buffers_non_status(lab):
    lab._on_notify(None, bytearray(b"q;0;5;;;"))
    assert len(lab._rx_buffer) == 1
    assert lab._rx_buffer[0]["type"] == "profile_count"


def test_on_notify_ignores_continuation(lab):
    lab._on_notify(None, bytearray(b";;;"))
    assert len(lab._rx_buffer) == 0


def test_on_notify_handles_bad_data(lab):
    # Should not raise
    lab._on_notify(None, bytearray(b"\xff\xfe\xfd"))
    assert lab._last_status is None


# --- Disconnect callback ---


def test_on_disconnect_callback(lab):
    called = []
    lab.on_disconnect(lambda: called.append(True))
    lab._on_disconnect(None)
    assert called == [True]
    assert lab._client is None
