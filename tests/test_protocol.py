"""Tests for Bento Lab protocol encoding/decoding.

Based on HCI snoop capture from live device interaction (2026-04-10).
"""

from bentolab.protocol import (
    CycleData,
    ProfileEntry,
    RunStatus,
    StageData,
    StatusBroadcast,
    decode_response,
    encode_command,
    encode_cycle,
    encode_lid_temp,
    encode_profile_name,
    encode_stage,
    lookup_uuid,
)

# --- Command encoding ---


def test_encode_command_basic():
    result = encode_command("p")
    assert result == b"_.;p\n\n"


def test_encode_command_handshake():
    result = encode_command("Xa")
    assert result == b"_.;Xa\n\n"


def test_encode_command_stop():
    result = encode_command("pg")
    assert result == b"_.;pg\n\n"


def test_encode_stage():
    result = encode_stage(95.0, 300)
    assert result == b"_.;95.0\n300\nx\n\n"


def test_encode_cycle():
    result = encode_cycle(4, 2, 35)
    assert result == b"_.;4\n2\n35\nz\n\n"


def test_encode_lid_temp():
    result = encode_lid_temp(110.0)
    assert result == b"_.;110\nA\n\n"


def test_encode_profile_name():
    result = encode_profile_name("COLPCR")
    assert result == b"_.;COLPCR\nI\n\n"


# --- Response decoding ---


def test_decode_status_broadcast():
    data = b"bb;0;0;0;0;20;25;0"
    result = decode_response(data)
    assert result["type"] == "status"
    status = result["data"]
    assert isinstance(status, StatusBroadcast)
    assert status.running == 0
    assert status.block_temperature == 20
    assert status.lid_temperature == 25


def test_decode_status_running():
    data = b"bb;1;0;0;0;20;54;0"
    result = decode_response(data)
    assert result["data"].running == 1
    assert result["data"].lid_temperature == 54


def test_decode_profile_count():
    data = b"q;0;7;;;"
    result = decode_response(data)
    assert result["type"] == "profile_count"
    assert result["count"] == 7


def test_decode_profile_entry():
    data = b"r;1;kimchi-16S;2;;"
    result = decode_response(data)
    assert result["type"] == "profile_entry"
    entry = result["data"]
    assert isinstance(entry, ProfileEntry)
    assert entry.index == 1
    assert entry.name == "kimchi-16S"
    assert entry.slot == 2


def test_decode_profile_end():
    data = b"t;8;;;"
    result = decode_response(data)
    assert result["type"] == "profile_end"


def test_decode_stage():
    data = b"x;1;95.00;300;;;"
    result = decode_response(data)
    assert result["type"] == "stage"
    stage = result["data"]
    assert isinstance(stage, StageData)
    assert stage.index == 1
    assert stage.temperature == 95.0
    assert stage.duration == 300


def test_decode_cycle():
    data = b"z;7;4;2;35;;;"
    result = decode_response(data)
    assert result["type"] == "cycle"
    cycle = result["data"]
    assert isinstance(cycle, CycleData)
    assert cycle.from_stage == 4
    assert cycle.to_stage == 2
    assert cycle.cycles == 35


def test_decode_lid_temp():
    data = b"A;8;110.00;;;"
    result = decode_response(data)
    assert result["type"] == "lid_temp"
    assert result["temperature"] == 110.0


def test_decode_profile_name():
    data = b"C;9;COLPCR;;;"
    result = decode_response(data)
    assert result["type"] == "profile_name"
    assert result["name"] == "COLPCR"


def test_decode_run_status_running():
    data = b"pf;1;8099;5;;;"
    result = decode_response(data)
    assert result["type"] == "run_status"
    status = result["data"]
    assert isinstance(status, RunStatus)
    assert status.running is True
    assert status.checksum == 8099
    assert status.progress == 5


def test_decode_run_status_stopped():
    data = b"pf;0;;;"
    result = decode_response(data)
    assert result["type"] == "run_status"
    assert result["data"].running is False


def test_decode_ack():
    data = b"/r/pa;1;;;"
    result = decode_response(data)
    assert result["type"] == "ack"
    assert result["command"] == "pa"


def test_decode_continuation():
    data = b";;;"
    result = decode_response(data)
    assert result["type"] == "continuation"


def test_decode_unknown():
    data = b"ZZ;unknown;stuff"
    result = decode_response(data)
    assert result["type"] == "unknown"


# --- UUID lookup ---


def test_lookup_uuid_sig_service():
    assert lookup_uuid("0000180a-0000-1000-8000-00805f9b34fb") == "Device Information"


def test_lookup_uuid_nus():
    assert lookup_uuid("6e400001-b5a3-f393-e0a9-e50e24dcca9e") == "Nordic UART Service (NUS)"


def test_lookup_uuid_custom():
    assert lookup_uuid("12345678-1234-1234-1234-123456789abc") == "Custom"


def test_lookup_uuid_case_insensitive():
    assert lookup_uuid("0000180A-0000-1000-8000-00805F9B34FB") == "Device Information"
