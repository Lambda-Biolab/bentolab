"""Bento Lab BLE protocol — fully decoded from HCI snoop capture.

## Protocol Summary
- Transport: Nordic UART Service (NUS) over BLE
- Format: Semicolon-delimited text, newline-framed commands
- Command prefix: '_.;' followed by command data and '\\n\\n'
- Response prefix: single letter or short code, semicolon-separated fields
- Status broadcast: 'bb;...' every ~5 seconds when connected

## Discovery Timeline
- 2026-04-10: APK decompilation (Flutter, flutter_blue_plus, nRF52840)
- 2026-04-10: Firmware binary string extraction (command handler states)
- 2026-04-10: Live GATT validation (Bento Lab 4A23, BL13489, HW 1.4, SW 18.1)
- 2026-04-10: HCI snoop capture — full protocol decoded from 193 messages
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# BLE GATT UUIDs
# ---------------------------------------------------------------------------
NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Write to device
NUS_TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Notify from device
BENTO_ADV_SERVICE_UUID = "6e409a18-b5a3-f393-e0a9-e50e24dcca9e"  # Scan filter
DFU_BUTTONLESS_UUID = "8ec90003-f315-4f60-9fb8-838830daea50"

# Convenience aliases
BENTO_SERVICE_UUID = NUS_SERVICE_UUID
BENTO_COMMAND_CHAR = NUS_RX_CHAR_UUID
BENTO_STATUS_CHAR = NUS_TX_CHAR_UUID

# ---------------------------------------------------------------------------
# Command prefix — all commands start with this
# ---------------------------------------------------------------------------
CMD_PREFIX = "_."

# ---------------------------------------------------------------------------
# Commands (APP >> DEVICE) — decoded from HCI snoop capture
# Format: CMD_PREFIX + ";" + <payload> + "\n\n"
# ---------------------------------------------------------------------------

# Device / connection
CMD_HANDSHAKE = "Xa"  # Initial handshake, get device info

# Profile list management
CMD_LIST_PROFILES = "p"  # List all stored PCR profiles
CMD_REQUEST_PROFILE = "pc"  # Request profile by slot: "<slot>\npc"
CMD_SAVE_PROFILE = "pa"  # Save/commit uploaded profile to device
CMD_DELETE_PROFILE = None  # TBD

# PCR profile upload sequence:
#   1. "0\n0\npb"    — begin new profile upload
#   2. "w"           — start stages section
#   3. "<temp>\n<dur>\nx"  — define each stage (repeated)
#   4. "<from>\n<to>\n<cycles>\nz"  — define cycle (which stages to loop)
#   5. "<lid_temp>\nA"  — set lid temperature
#   6. "<name>\nI"   — set profile name
#   7. "<slot>\nB"   — set profile slot/version
#   8. "B"           — commit/finalize profile
CMD_BEGIN_PROFILE = "pb"  # Begin profile upload: "0\n0\npb"
CMD_BEGIN_STAGES = "w"  # Start stages section
CMD_STAGE = "x"  # Define a stage: "<temp>\n<duration>\nx"
CMD_TOUCHDOWN_STAGE = "y"  # Touchdown stage: "<temp>\n<duration>\n<delta>\n<repeats>\ny"
CMD_CYCLE = "z"  # Define a cycle: "<from>\n<to>\n<cycles>\nz"
CMD_LID_TEMP = "A"  # Set lid temp: "<temp>\nA"
CMD_PROFILE_NAME = "I"  # Set name: "<name>\nI"
CMD_PROFILE_SLOT = "B"  # Set slot: "<slot>\nB"  OR  finalize: "B"

# PCR run control
CMD_START_RUN = "pa"  # Start running the loaded profile
CMD_POLL_STATUS = "pe"  # Poll run status
CMD_STOP_RUN = "pg"  # Stop the running PCR program


# ---------------------------------------------------------------------------
# Response types (DEVICE >> APP) — decoded from HCI snoop capture
# Format: "<prefix>;<field1>;<field2>;...;;;?"
# Messages may span multiple BLE notifications (chunked)
# ---------------------------------------------------------------------------

# Status broadcast (every ~5 seconds)
# bb;<running>;<f2>;<f3>;<f4>;<block_temp>;<lid_temp>;<f7>
# followed by ";;;" on next notification
RESP_STATUS = "bb"

# Profile list responses
RESP_PROFILE_COUNT = "q"  # q;0;<count>;;;
RESP_PROFILE_ENTRY = "r"  # r;<index>;<name>;<slot>;;[;]
RESP_PROFILE_END = "t"  # t;<next_index>;;;

# Profile data responses
RESP_STAGES_BEGIN = "w"  # w;0;;;
RESP_STAGE = "x"  # x;<index>;<temp>;<duration>;;;
RESP_TOUCHDOWN_STAGE = "y"  # y;<index>;<temp>;<duration>;<delta>;<repeats>;;;
RESP_CYCLE = "z"  # z;<index>;<from>;<to>;<cycles>[;<cycles2>];;;
RESP_LID_TEMP = "A"  # A;<index>;<temp>;;;
RESP_PROFILE_NAME = "C"  # C;<index>;<name>;;;
RESP_PROFILE_SLOT = "B"  # B;<index>;<slot>;;;

# Command acknowledgements
RESP_ACK_PREFIX = "/r/"  # /r/<cmd>;1;;;

# PCR run status
RESP_RUN_STATUS = "pf"  # pf;<running>;<checksum>;<progress>;;;
# running: 1=running, 0=stopped
# checksum: 4-digit number (e.g., 8099)
# progress: integer (0-100 or step count?)


# ---------------------------------------------------------------------------
# Protocol encoding/decoding
# ---------------------------------------------------------------------------


def encode_command(cmd: str) -> bytes:
    """Encode a command string for sending to the Bento Lab.

    Wraps the command in the protocol framing: '_.;<cmd>\\n\\n'
    Write the result to NUS_RX_CHAR_UUID.
    """
    return f"{CMD_PREFIX};{cmd}\n\n".encode("ascii")


def encode_stage(temperature: float, duration: int) -> bytes:
    """Encode a PCR stage command."""
    return encode_command(f"{temperature}\n{duration}\n{CMD_STAGE}")


def encode_touchdown_stage(temperature: float, duration: int, delta: float, repeats: int) -> bytes:
    """Encode a touchdown PCR stage.

    Args:
        temperature: Starting annealing temperature (e.g., 68.0)
        duration: Hold duration in seconds
        delta: Temperature change per repeat (e.g., -1.0 for -1°C/repeat)
        repeats: Number of touchdown repeats
    """
    return encode_command(f"{temperature}\n{duration}\n{delta}\n{repeats}\n{CMD_TOUCHDOWN_STAGE}")


def encode_cycle(from_stage: int, to_stage: int, cycles: int) -> bytes:
    """Encode a PCR cycle command."""
    return encode_command(f"{from_stage}\n{to_stage}\n{cycles}\n{CMD_CYCLE}")


def encode_lid_temp(temperature: float) -> bytes:
    """Encode a lid temperature command."""
    return encode_command(f"{int(temperature)}\n{CMD_LID_TEMP}")


def encode_profile_name(name: str) -> bytes:
    """Encode a profile name command."""
    return encode_command(f"{name}\n{CMD_PROFILE_NAME}")


def encode_profile_slot(slot: int) -> bytes:
    """Encode a profile slot/version command."""
    return encode_command(f"{slot}\n{CMD_PROFILE_SLOT}")


@dataclass
class StatusBroadcast:
    """Parsed status broadcast message."""

    running: int  # 0=idle, 1=running
    field2: int
    field3: int
    field4: int
    block_temperature: int  # Celsius (integer)
    lid_temperature: int  # Celsius (integer)
    field7: int

    @classmethod
    def from_message(cls, msg: str) -> StatusBroadcast:
        """Parse a 'bb;...' message."""
        parts = msg.split(";")

        def safe_int(s: str, default: int = 0) -> int:
            return int(s) if s else default

        return cls(
            running=safe_int(parts[1]),
            field2=safe_int(parts[2]),
            field3=safe_int(parts[3]),
            field4=safe_int(parts[4]),
            block_temperature=safe_int(parts[5]),
            lid_temperature=safe_int(parts[6]),
            field7=safe_int(parts[7]) if len(parts) > 7 else 0,
        )


@dataclass
class ProfileEntry:
    """A profile list entry from the 'r' response."""

    index: int
    name: str
    slot: int

    @classmethod
    def from_message(cls, msg: str) -> ProfileEntry:
        """Parse an 'r;...' message."""
        parts = msg.split(";")
        return cls(
            index=int(parts[1]),
            name=parts[2],
            slot=int(parts[3]) if parts[3] else 0,
        )


@dataclass
class StageData:
    """A PCR stage from the 'x' response."""

    index: int
    temperature: float
    duration: int

    @classmethod
    def from_message(cls, msg: str) -> StageData:
        """Parse an 'x;...' message."""
        parts = msg.split(";")
        return cls(
            index=int(parts[1]),
            temperature=float(parts[2]),
            duration=int(parts[3]),
        )


@dataclass
class TouchdownStageData:
    """A touchdown PCR stage from the 'y' response.

    The annealing temperature starts at `temperature` and decreases by
    `delta` each repeat for `repeats` cycles. For example:
    y;3;68.00;20;-1.00;8 means start at 68°C, drop 1°C per repeat, 8 times.
    """

    index: int
    temperature: float  # Starting temperature
    duration: int
    delta: float  # Temperature change per repeat (typically negative)
    repeats: int  # Number of touchdown repeats

    @classmethod
    def from_message(cls, msg: str) -> TouchdownStageData:
        """Parse a 'y;...' message.

        Note: the response may be split across two BLE notifications.
        The full format is: y;<index>;<temp>;<duration>;<delta>;<repeats>
        """
        parts = msg.split(";")
        return cls(
            index=int(parts[1]),
            temperature=float(parts[2]),
            duration=int(parts[3]),
            delta=float(parts[4]),
            repeats=int(parts[5]) if len(parts) > 5 and parts[5] else 0,
        )


@dataclass
class CycleData:
    """A PCR cycle from the 'z' response."""

    index: int
    from_stage: int
    to_stage: int
    cycles: int

    @classmethod
    def from_message(cls, msg: str) -> CycleData:
        """Parse a 'z;...' message."""
        parts = msg.split(";")
        return cls(
            index=int(parts[1]),
            from_stage=int(parts[2]),
            to_stage=int(parts[3]),
            cycles=int(parts[4]),
        )


@dataclass
class RunStatus:
    """PCR run status from the 'pf' response."""

    running: bool
    checksum: int
    progress: int

    @classmethod
    def from_message(cls, msg: str) -> RunStatus:
        """Parse a 'pf;...' message."""
        parts = msg.split(";")
        return cls(
            running=parts[1] == "1",
            checksum=int(parts[2]) if len(parts) > 2 and parts[2] else 0,
            progress=int(parts[3]) if len(parts) > 3 and parts[3] else 0,
        )


def decode_response(data: bytes) -> dict:
    """Decode a NUS TX notification into a structured dict.

    Returns dict with 'type' key and parsed fields.
    """
    text = data.decode("ascii", errors="replace").strip()
    if not text or text == ";;;":
        return {"type": "continuation", "raw": text}

    prefix = text.split(";")[0]

    if prefix == RESP_STATUS:
        return {"type": "status", "data": StatusBroadcast.from_message(text)}
    if prefix == RESP_PROFILE_COUNT:
        parts = text.split(";")
        return {"type": "profile_count", "count": int(parts[2])}
    if prefix == RESP_PROFILE_ENTRY:
        return {"type": "profile_entry", "data": ProfileEntry.from_message(text)}
    if prefix == RESP_PROFILE_END:
        return {"type": "profile_end"}
    if prefix == RESP_STAGES_BEGIN:
        return {"type": "stages_begin"}
    if prefix == RESP_STAGE:
        return {"type": "stage", "data": StageData.from_message(text)}
    if prefix == RESP_TOUCHDOWN_STAGE:
        return {"type": "touchdown_stage", "data": TouchdownStageData.from_message(text)}
    if prefix == RESP_CYCLE:
        return {"type": "cycle", "data": CycleData.from_message(text)}
    if prefix == RESP_LID_TEMP:
        parts = text.split(";")
        return {"type": "lid_temp", "temperature": float(parts[2])}
    if prefix == RESP_PROFILE_NAME:
        parts = text.split(";")
        return {"type": "profile_name", "name": parts[2]}
    if prefix == RESP_PROFILE_SLOT:
        parts = text.split(";")
        return {"type": "profile_slot", "slot": int(parts[2])}
    if prefix == RESP_RUN_STATUS:
        return {"type": "run_status", "data": RunStatus.from_message(text)}
    if text.startswith(RESP_ACK_PREFIX):
        cmd = text[len(RESP_ACK_PREFIX) :].split(";")[0]
        return {"type": "ack", "command": cmd}

    return {"type": "unknown", "raw": text}


# ---------------------------------------------------------------------------
# API & Firmware URLs
# ---------------------------------------------------------------------------
BENTO_API_BASE = "https://api2.bento.bio"
FIRMWARE_IMAGES_URL = "https://api2.bento.bio/static/firmware-images/"

# ---------------------------------------------------------------------------
# UUID lookup tables
# ---------------------------------------------------------------------------
SIG_SERVICES: dict[str, str] = {
    "00001800-0000-1000-8000-00805f9b34fb": "Generic Access",
    "00001801-0000-1000-8000-00805f9b34fb": "Generic Attribute",
    "0000180a-0000-1000-8000-00805f9b34fb": "Device Information",
    "0000fe59-0000-1000-8000-00805f9b34fb": "Nordic Semiconductor DFU",
}

SIG_CHARACTERISTICS: dict[str, str] = {
    "00002a00-0000-1000-8000-00805f9b34fb": "Device Name",
    "00002a01-0000-1000-8000-00805f9b34fb": "Appearance",
    "00002a24-0000-1000-8000-00805f9b34fb": "Model Number String",
    "00002a25-0000-1000-8000-00805f9b34fb": "Serial Number String",
    "00002a26-0000-1000-8000-00805f9b34fb": "Firmware Revision String",
    "00002a27-0000-1000-8000-00805f9b34fb": "Hardware Revision String",
    "00002a28-0000-1000-8000-00805f9b34fb": "Software Revision String",
    "00002a29-0000-1000-8000-00805f9b34fb": "Manufacturer Name String",
    "00002902-0000-1000-8000-00805f9b34fb": "Client Characteristic Configuration",
}

BENTO_UUIDS: dict[str, str] = {
    NUS_SERVICE_UUID: "Nordic UART Service (NUS)",
    NUS_RX_CHAR_UUID: "NUS RX (write commands)",
    NUS_TX_CHAR_UUID: "NUS TX (notifications)",
    BENTO_ADV_SERVICE_UUID: "Bento Advertising UUID",
    DFU_BUTTONLESS_UUID: "Nordic Buttonless DFU",
}


def lookup_uuid(uuid: str) -> str:
    """Look up a UUID in all tables, return name or 'Custom'."""
    uuid_lower = uuid.lower()
    for table in (SIG_SERVICES, SIG_CHARACTERISTICS, BENTO_UUIDS):
        if uuid_lower in table:
            return table[uuid_lower]
    return "Custom"
