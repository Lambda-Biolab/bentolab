"""Tests for protocol encoding/decoding.

These tests will be populated as the protocol is reverse-engineered.
Initially they verify that stubs raise NotImplementedError correctly.
"""

import pytest

from bentolab.protocol import (
    decode_response,
    decode_temperature,
    encode_command,
    lookup_uuid,
)


def test_encode_command_not_implemented():
    with pytest.raises(NotImplementedError, match="not yet reverse-engineered"):
        encode_command(None)


def test_decode_response_not_implemented():
    with pytest.raises(NotImplementedError, match="not yet reverse-engineered"):
        decode_response(b"\x00\x01")


def test_decode_temperature_not_implemented():
    with pytest.raises(NotImplementedError, match="not yet reverse-engineered"):
        decode_temperature(b"\x00\x01")


def test_lookup_uuid_sig_service():
    assert lookup_uuid("0000180a-0000-1000-8000-00805f9b34fb") == "Device Information"


def test_lookup_uuid_sig_characteristic():
    assert lookup_uuid("00002a00-0000-1000-8000-00805f9b34fb") == "Device Name"


def test_lookup_uuid_custom():
    assert lookup_uuid("12345678-1234-1234-1234-123456789abc") == "Custom"


def test_lookup_uuid_case_insensitive():
    assert lookup_uuid("0000180A-0000-1000-8000-00805F9B34FB") == "Device Information"
