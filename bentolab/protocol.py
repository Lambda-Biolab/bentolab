"""Shared protocol constants and encoding/decoding for Bento Lab communication.

This module is populated incrementally as the protocol is reverse-engineered.
Initial values come from APK decompilation (tools/apk_strings.py), then
refined through live BLE/Wi-Fi interaction.

## APK Analysis Summary (2026-04-10)
- App: Bento Bio v0.5.4 (Flutter + flutter_blue_plus)
- BLE: Nordic UART Service (NUS) — confirmed live
- MCU: Nordic nRF52840 (confirmed from firmware binary)
- Firmware: Downloaded from https://api2.bento.bio/static/firmware-images/
- Protocol: Text commands over NUS (processBluetoothCommand/Message)
- Data model: PcrProgram > PcrProgramCycle > PcrProgramStage (JSON serializable)
- Two device types: DeviceBentoLab (V1.7+) and DeviceGoPCR

## Live GATT Validation (2026-04-10)
- Device: Bento Lab 4A23 (serial BL13489, hw 1.4, sw 18.1)
- Manufacturer: Bento Bioworks Ltd
- Model: Bento Lab Pro
- FW commit: 57bc234ca48866e1e9394d3451499317ea27cd0e
- Advertising UUID: 6e409a18 (custom, used as scan filter only)
- Communication: NUS (6e400001/02/03) — request-response, not streaming
- DFU: Buttonless (8ec90003) in app mode
"""

from enum import Enum

# ---------------------------------------------------------------------------
# BLE GATT UUIDs — discovered from APK string extraction (libapp.so)
# ---------------------------------------------------------------------------

# Nordic UART Service (NUS) — primary communication channel
NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Write to device
NUS_TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Notify from device

# Bento-specific UUIDs (same base as NUS)
BENTO_ADV_SERVICE_UUID = "6e409a18-b5a3-f393-e0a9-e50e24dcca9e"  # Advertising only
BENTO_ADV_ALT_UUID = "6e409a19-b5a3-f393-e0a9-e50e24dcca9e"  # Alt advertising UUID

# Client Characteristic Configuration Descriptor
CCCD_UUID = "00002902-0000-1000-8000-00805f9b34fb"

# Aliases matching the original plan's naming
BENTO_SERVICE_UUID = NUS_SERVICE_UUID
BENTO_COMMAND_CHAR = NUS_RX_CHAR_UUID  # Commands are WRITTEN to RX
BENTO_STATUS_CHAR = NUS_TX_CHAR_UUID  # Status comes back via TX NOTIFY

# Nordic DFU (Buttonless, in application mode)
DFU_BUTTONLESS_UUID = "8ec90003-f315-4f60-9fb8-838830daea50"

# ---------------------------------------------------------------------------
# Confirmed device info (live read 2026-04-10)
# ---------------------------------------------------------------------------
DEVICE_NAME_PREFIX = "Bento Lab"  # Advertises as "Bento Lab XXXX" (MAC suffix)
DEVICE_MANUFACTURER = "Bento Bioworks Ltd"
DEVICE_MODEL = "Bento Lab Pro"
DEVICE_SERIAL = "BL13489"  # Unit 1
DEVICE_HW_REV = "1.4"
DEVICE_SW_REV = "18.1"
DEVICE_FW_COMMIT = "57bc234ca48866e1e9394d3451499317ea27cd0e"

# ---------------------------------------------------------------------------
# API & Firmware URLs — discovered from APK string extraction
# ---------------------------------------------------------------------------
BENTO_API_BASE = "https://api2.bento.bio"
BENTO_DEVICES_API = "https://api2.bento.bio/devices/"
FIRMWARE_IMAGES_URL = "https://api2.bento.bio/static/firmware-images/"
BENTO_LEGACY_UPDATE_PATH = "bl-legacy-update/"

# Firmware update metadata fields (from string extraction)
# new_version_checksum, new_version_description, new_version_filename, new_version_str

# ---------------------------------------------------------------------------
# Device types and BLE advertising
# ---------------------------------------------------------------------------
DEVICE_TYPES = {
    "DeviceBentoLab": "Bento Lab (V1.7+, BLE)",
    "DeviceBentoLabV17": "Bento Lab V1.7 (specific variant)",
    "BentoLab15": "Bento Lab V1.5 (legacy)",
    "DeviceGoPCR": "goPCR (companion device)",
}

# ---------------------------------------------------------------------------
# PCR Protocol — discovered from Dart class/method names in libapp.so
# ---------------------------------------------------------------------------
# Key functions: processBluetoothCommand, processBluetoothMessage
# Key methods: sendPcrProfileToRun, sendStagesToDevice
# Data flow: PcrProgram -> serialize -> send via NUS RX characteristic
# Response: device sends status via NUS TX notification
#
# PCR profile format uses commands with framing:
#   - First command: pcrProfileBegin
#   - Stage/cycle data in between
#   - Last command: pcrProfileDone
#
# Data model hierarchy:
#   PcrProgram (JSON serializable via PcrProgram.fromJson)
#     ├── PcrProgramCycle (repeatable group of stages)
#     │     ├── PcrProgramStage (temp + duration)
#     │     └── repeatCount
#     ├── PcrSettings (device-specific settings)
#     └── PcrProgramNode (base class for Cycle/Stage)
#
# Known PCR-related fields:
#   temperature, duration, repeatCount, stage, cycle, beforeCycle
#   lidTemperature, touchdownRepeats, bentoPcrProfileVersion
#
# Known device control methods:
#   updateCentMode — centrifuge control
#   updateGelMode — gel electrophoresis/transilluminator
#   sendPcrProfileToRun — start PCR
#   updatePcrProfileList — sync profiles with device
#   checkIfUpdateAvailable — firmware update check

# ---------------------------------------------------------------------------
# Error codes — discovered from string extraction
# ---------------------------------------------------------------------------
KNOWN_ERRORS = {
    "centrifuge_lid": "Centrifuge lid state is uncertain",
    "centrifuge_lock": "Centrifuge lock can not be confirmed",
    "centrifuge_stuck": "Centrifuge may be stuck and can not be unlocked",
    "centrifuge_motor": "Centrifuge motor is not responding",
    "heatblock_sensor": "Heat block temperature sensor error",
    "heatblock_range": "Heated block temperature out of range",
    "lid_sensor": "Lid temperature sensor error",
    "lid_range": "Heated lid temperature out of range",
    "gel_current_high": "Gel current is too high at this voltage",
    "gel_no_current": "Gel is not drawing current",
    "usb_power": "Cannot use current USB-C power supply",
}

# ---------------------------------------------------------------------------
# Standard Bluetooth SIG UUID lookup (16-bit shorthand -> name)
# ---------------------------------------------------------------------------
SIG_SERVICES: dict[str, str] = {
    "00001800-0000-1000-8000-00805f9b34fb": "Generic Access",
    "00001801-0000-1000-8000-00805f9b34fb": "Generic Attribute",
    "0000180a-0000-1000-8000-00805f9b34fb": "Device Information",
    "0000180f-0000-1000-8000-00805f9b34fb": "Battery Service",
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
    "00002902-0000-1000-8000-00805f9b34fb": "Client Characteristic Configuration",
}

# Bento-specific UUIDs for lookup
BENTO_UUIDS: dict[str, str] = {
    NUS_SERVICE_UUID: "Nordic UART Service (NUS)",
    NUS_RX_CHAR_UUID: "NUS RX (write commands to device)",
    NUS_TX_CHAR_UUID: "NUS TX (notifications from device)",
    BENTO_ADV_SERVICE_UUID: "Bento Advertising Service (scan filter)",
    BENTO_ADV_ALT_UUID: "Bento Advertising Alt UUID",
    DFU_BUTTONLESS_UUID: "Nordic Buttonless DFU",
}


class CommandType(Enum):
    """Known command opcodes (populated from APK analysis).

    The protocol uses processBluetoothCommand/processBluetoothMessage,
    suggesting a text or structured binary command format over NUS.
    Known command names from string extraction:
      - pcrProfileBegin / pcrProfileDone (PCR profile framing)
      - addLoadingData (incremental profile data)
    """

    pass


def encode_command(cmd_type: CommandType, payload: bytes = b"") -> bytes:
    """Encode a command for sending to the Bento Lab.

    Commands are written to NUS_RX_CHAR_UUID.
    Format TBD — may be text-based given the Flutter/Dart architecture.
    """
    raise NotImplementedError("Protocol not yet reverse-engineered")


def decode_response(data: bytes) -> dict:
    """Decode a response from the Bento Lab.

    Responses arrive as notifications on NUS_TX_CHAR_UUID.
    """
    raise NotImplementedError("Protocol not yet reverse-engineered")


def decode_temperature(data: bytes) -> float:
    """Decode temperature from raw characteristic bytes."""
    raise NotImplementedError("Protocol not yet reverse-engineered")


def lookup_uuid(uuid: str) -> str:
    """Look up a UUID in all tables, return name or 'Custom'."""
    uuid_lower = uuid.lower()
    if uuid_lower in SIG_SERVICES:
        return SIG_SERVICES[uuid_lower]
    if uuid_lower in SIG_CHARACTERISTICS:
        return SIG_CHARACTERISTICS[uuid_lower]
    if uuid_lower in BENTO_UUIDS:
        return BENTO_UUIDS[uuid_lower]
    return "Custom"
