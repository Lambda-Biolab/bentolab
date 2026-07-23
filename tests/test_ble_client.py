"""Tests for BentoLabBLE client with mocked BLE."""

from unittest.mock import AsyncMock

import pytest

from bentolab.ble_client import (
    BentoLabBLE,
    BentoLabCommandError,
    BentoLabConnectionError,
    ProfileData,
)
from bentolab.models import PCRProfile
from bentolab.protocol import RunStatus, StatusBroadcast
from bentolab.runs import RunLifecycle, RunState

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


def test_construction_with_address():
    lab = BentoLabBLE(address="AA:BB:CC:DD:EE:FF")
    assert lab.address == "AA:BB:CC:DD:EE:FF"


def test_construction_with_name_filter():
    lab = BentoLabBLE(name_filter=r"MyDevice")
    assert lab.name_filter.pattern == "MyDevice"


def test_not_connected_by_default(lab):
    assert not lab.is_connected


# --- Connection tests ---


def test_require_client_raises_when_not_connected(lab):
    with pytest.raises(BentoLabConnectionError, match="Not connected"):
        lab._require_client()


# --- Error types ---


def test_error_hierarchy():
    from bentolab.ble_client import BentoLabError

    assert issubclass(BentoLabConnectionError, BentoLabError)
    assert issubclass(BentoLabCommandError, BentoLabError)


# --- RunState (was PCRRunState; unified into bentolab.runs) ---


def test_run_state_defaults():
    state = RunState()
    assert state.state == RunLifecycle.IDLE
    assert not state.running
    assert state.progress == 0
    assert state.block_temperature is None
    assert state.lid_temperature is None
    assert state.elapsed_seconds == 0.0


def test_run_state_values():
    state = RunState(
        state=RunLifecycle.RUNNING,
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


# --- run_profile convenience wrapper ---


async def test_run_profile_flattens_and_forwards(lab):
    profile = PCRProfile.simple(
        name="Unit Test PCR",
        num_cycles=12,
        initial_denaturation=(95.0, 120),
        denaturation=(95.0, 20),
        annealing=(60.0, 20),
        extension=(72.0, 40),
        final_extension=(72.0, 180),
    )

    captured: dict = {}

    async def fake_run_pcr(**kwargs):
        captured.update(kwargs)
        yield RunState(
            state=RunLifecycle.IDLE,
            progress=100,
            block_temperature=72.0,
        )

    lab.run_pcr = fake_run_pcr  # type: ignore[method-assign]

    states = [s async for s in lab.run_profile(profile, lid_temp=108.0, poll_interval=1.5)]

    assert len(states) == 1
    assert states[0].progress == 100
    assert captured["name"] == "Unit Test PCR"
    assert captured["stages"] == [
        (95.0, 120),
        (95.0, 20),
        (60.0, 20),
        (72.0, 40),
        (72.0, 180),
    ]
    assert captured["cycles"] == [(4, 2, 12)]
    assert captured["lid_temp"] == 108.0
    assert captured["poll_interval"] == 1.5


# --- public start_run(profile) adapter (matches BleClientProtocol) ---


async def test_start_run_adapter_flattens_profile_and_forwards(lab, monkeypatch):
    """start_run(profile) delegates to _start_pcr_program with flattened args.

    Regression: the BleClientProtocol contract in api/app.py declares
    ``start_run(profile: PCRProfile)``. Before this adapter was added,
    ``BentoLabBLE.start_run`` had a different signature and the
    service-layer call would land the PCRProfile in ``name`` and skip
    sending stages silently. This test pins the flattening behavior.
    """
    profile = PCRProfile.simple(
        name="Protocol Probe",
        num_cycles=5,
        initial_denaturation=(95.0, 60),
        denaturation=(95.0, 10),
        annealing=(58.0, 15),
        extension=(72.0, 30),
        final_extension=(72.0, 120),
    )

    captured: dict = {}

    async def fake_start_program(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(lab, "_start_pcr_program", fake_start_program)

    # Default lid_temp should come from profile.lid_temperature (110.0)
    await lab.start_run(profile)

    assert captured["name"] == "Protocol Probe"
    assert captured["stages"] == [
        (95.0, 60),
        (95.0, 10),
        (58.0, 15),
        (72.0, 30),
        (72.0, 120),
    ]
    assert captured["cycles"] == [(4, 2, 5)]  # (extend_idx, denat_idx, repeat_count)
    assert captured["lid_temp"] == 110.0  # from profile.lid_temperature
    assert captured["slot"] == 0


async def test_start_run_adapter_accepts_lid_temp_override(lab, monkeypatch):
    """start_run(profile, lid_temp=X) overrides the profile default."""
    profile = PCRProfile.simple(name="Lid Override", num_cycles=3)

    captured: dict = {}

    async def fake_start_program(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(lab, "_start_pcr_program", fake_start_program)
    await lab.start_run(profile, lid_temp=95.0)

    assert captured["lid_temp"] == 95.0  # override applied


async def test_abort_run_delegates_to_stop_run(lab, monkeypatch):
    """abort_run() is the public API name; stop_run() is the wire-level one."""
    called = {"stop": False}

    async def fake_stop_run():
        called["stop"] = True

    monkeypatch.setattr(lab, "stop_run", fake_stop_run)
    await lab.abort_run()
    assert called["stop"] is True


async def test_get_run_status_combines_poll_and_status(lab, monkeypatch):
    """get_run_status returns the dict shape the API service expects."""
    monkeypatch.setattr(
        lab,
        "poll_run_status",
        AsyncMock(return_value=RunStatus(running=True, checksum=42, progress=75)),
    )
    monkeypatch.setattr(lab, "get_status", AsyncMock(return_value=_status(block=55, lid=105)))

    hw = await lab.get_run_status()
    assert hw["running"] is True
    assert hw["progress"] == 75
    assert hw["block_temperature"] == 55.0
    assert hw["lid_temperature"] == 105.0


# --- run_pcr termination logic ---


def _status(block: int = 25, lid: int = 110, running: int = 1) -> StatusBroadcast:
    return StatusBroadcast(
        running=running,
        field2=0,
        field3=0,
        field4=0,
        block_temperature=block,
        lid_temperature=lid,
        field7=0,
    )


def _patch_run_pcr_dependencies(
    lab: BentoLabBLE,
    *,
    run_status_seq: list[RunStatus],
    monkeypatch: pytest.MonkeyPatch,
) -> list[RunState]:
    """Wire up _start_pcr_program/get_status/poll_run_status/sleep for run_pcr tests."""

    async def fake_start_program(**_kwargs):
        return None

    async def fake_get_status():
        return _status()

    seq_iter = iter(run_status_seq)

    async def fake_poll_run_status():
        try:
            return next(seq_iter)
        except StopIteration:
            return RunStatus(running=False, checksum=0, progress=100)

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(lab, "_start_pcr_program", fake_start_program)
    monkeypatch.setattr(lab, "get_status", fake_get_status)
    monkeypatch.setattr(lab, "poll_run_status", fake_poll_run_status)
    monkeypatch.setattr("bentolab.ble_client.asyncio.sleep", fake_sleep)
    return []


async def test_run_pcr_ignores_transient_not_running_during_grace(lab, monkeypatch):
    """A single early running=False (lid-heat ramp) must not end the run."""
    run_status_seq = [
        RunStatus(running=True, checksum=0, progress=10),
        RunStatus(running=True, checksum=0, progress=23),
        RunStatus(running=True, checksum=0, progress=36),
        RunStatus(running=True, checksum=0, progress=50),
        RunStatus(running=False, checksum=0, progress=63),  # transient flip
        RunStatus(running=True, checksum=0, progress=70),
        RunStatus(running=True, checksum=0, progress=99),
        RunStatus(running=False, checksum=0, progress=100),  # real completion
    ]
    _patch_run_pcr_dependencies(lab, run_status_seq=run_status_seq, monkeypatch=monkeypatch)

    states = [
        s
        async for s in lab.run_pcr(
            poll_interval=10.0,
            startup_grace_seconds=120.0,
            completion_confirmations=3,
        )
    ]

    # Must consume past the transient (index 4) and reach completion at index 7.
    assert len(states) == 8
    assert states[-1].progress == 100
    assert not states[-1].running


async def test_run_pcr_completes_on_progress_99(lab, monkeypatch):
    """Reaching peak progress >=99% terminates immediately on next idle."""
    run_status_seq = [
        RunStatus(running=True, checksum=0, progress=50),
        RunStatus(running=True, checksum=0, progress=99),
        RunStatus(running=False, checksum=0, progress=100),
    ]
    _patch_run_pcr_dependencies(lab, run_status_seq=run_status_seq, monkeypatch=monkeypatch)

    states = [
        s
        async for s in lab.run_pcr(
            poll_interval=10.0,
            startup_grace_seconds=600.0,  # well past elapsed
            completion_confirmations=5,
        )
    ]

    assert len(states) == 3
    assert states[-1].progress == 100


async def test_run_pcr_requires_consecutive_idle_after_grace(lab, monkeypatch):
    """After grace, N consecutive idle polls (without progress=99) terminate."""
    run_status_seq = [
        RunStatus(running=True, checksum=0, progress=20),
        RunStatus(running=True, checksum=0, progress=40),
        RunStatus(running=False, checksum=0, progress=50),  # past grace
        RunStatus(running=False, checksum=0, progress=50),
        RunStatus(running=False, checksum=0, progress=50),  # 3rd consecutive
    ]
    _patch_run_pcr_dependencies(lab, run_status_seq=run_status_seq, monkeypatch=monkeypatch)

    states = [
        s
        async for s in lab.run_pcr(
            poll_interval=10.0,
            startup_grace_seconds=20.0,  # grace ends after 2 polls
            completion_confirmations=3,
        )
    ]

    assert len(states) == 5
    assert not states[-1].running
