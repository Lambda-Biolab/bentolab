"""Shared protocol constants and encoding/decoding for Bento Lab communication.

This module is populated incrementally as the protocol is reverse-engineered.
Initial values come from APK decompilation (tools/apk_strings.py), then
refined through live BLE/Wi-Fi interaction.
"""

from enum import Enum

# ---------------------------------------------------------------------------
# BLE GATT UUIDs (to be discovered via APK analysis / BLE scanning)
# ---------------------------------------------------------------------------
BENTO_SERVICE_UUID: str = "UNKNOWN"
BENTO_COMMAND_CHAR: str = "UNKNOWN"
BENTO_STATUS_CHAR: str = "UNKNOWN"
BENTO_TEMPERATURE_CHAR: str = "UNKNOWN"

# ---------------------------------------------------------------------------
# Wi-Fi endpoints (to be discovered via network scanning / APK analysis)
# ---------------------------------------------------------------------------
FIRMWARE_UPDATE_URL: str = "UNKNOWN"

# ---------------------------------------------------------------------------
# Standard Bluetooth SIG UUID lookup (16-bit shorthand -> name)
# ---------------------------------------------------------------------------
SIG_SERVICES: dict[str, str] = {
    "00001800-0000-1000-8000-00805f9b34fb": "Generic Access",
    "00001801-0000-1000-8000-00805f9b34fb": "Generic Attribute",
    "0000180a-0000-1000-8000-00805f9b34fb": "Device Information",
    "0000180f-0000-1000-8000-00805f9b34fb": "Battery Service",
    "00001810-0000-1000-8000-00805f9b34fb": "Blood Pressure",
    "00001816-0000-1000-8000-00805f9b34fb": "Cycling Speed and Cadence",
    "0000181c-0000-1000-8000-00805f9b34fb": "User Data",
    "0000fe59-0000-1000-8000-00805f9b34fb": "Nordic Semiconductor DFU",
}

SIG_CHARACTERISTICS: dict[str, str] = {
    "00002a00-0000-1000-8000-00805f9b34fb": "Device Name",
    "00002a01-0000-1000-8000-00805f9b34fb": "Appearance",
    "00002a04-0000-1000-8000-00805f9b34fb": "Peripheral Preferred Connection Parameters",
    "00002a19-0000-1000-8000-00805f9b34fb": "Battery Level",
    "00002a24-0000-1000-8000-00805f9b34fb": "Model Number String",
    "00002a25-0000-1000-8000-00805f9b34fb": "Serial Number String",
    "00002a26-0000-1000-8000-00805f9b34fb": "Firmware Revision String",
    "00002a27-0000-1000-8000-00805f9b34fb": "Hardware Revision String",
    "00002a28-0000-1000-8000-00805f9b34fb": "Software Revision String",
    "00002a29-0000-1000-8000-00805f9b34fb": "Manufacturer Name String",
}


class CommandType(Enum):
    """Known command opcodes (populated from APK analysis)."""

    pass


def encode_command(cmd_type: CommandType, payload: bytes = b"") -> bytes:
    """Encode a command for sending to the Bento Lab.

    Format TBD — likely: [opcode][length][payload][checksum]
    """
    raise NotImplementedError("Protocol not yet reverse-engineered")


def decode_response(data: bytes) -> dict:
    """Decode a response from the Bento Lab."""
    raise NotImplementedError("Protocol not yet reverse-engineered")


def decode_temperature(data: bytes) -> float:
    """Decode temperature from raw characteristic bytes.

    Common encodings: int16 LE / 100, IEEE 754 float32, BCD.
    """
    raise NotImplementedError("Protocol not yet reverse-engineered")


def lookup_uuid(uuid: str) -> str:
    """Look up a UUID in the SIG tables, return name or 'Custom'."""
    uuid_lower = uuid.lower()
    if uuid_lower in SIG_SERVICES:
        return SIG_SERVICES[uuid_lower]
    if uuid_lower in SIG_CHARACTERISTICS:
        return SIG_CHARACTERISTICS[uuid_lower]
    return "Custom"
