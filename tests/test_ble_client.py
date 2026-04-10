"""Tests for BLE client (using mocked bleak)."""

from bentolab.ble_client import BentoLabBLE


def test_ble_client_not_connected_by_default():
    client = BentoLabBLE()
    assert client.is_connected is False


def test_ble_client_custom_name_filter():
    client = BentoLabBLE(name_filter=r"MyDevice")
    assert client.name_filter.pattern == "MyDevice"


def test_ble_client_with_address():
    client = BentoLabBLE(address="AA:BB:CC:DD:EE:FF")
    assert client.address == "AA:BB:CC:DD:EE:FF"
