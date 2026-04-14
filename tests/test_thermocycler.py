"""Tests for the high-level BentoLab facade in thermocycler.py."""

import pytest

from bentolab.models import DeviceStatus, PCRProfile
from bentolab.protocol import StatusBroadcast
from bentolab.thermocycler import BentoLab, _status_to_state


def test_status_to_state_adapter_idle():
    status = StatusBroadcast(
        running=0,
        field2=0,
        field3=0,
        field4=0,
        block_temperature=24,
        lid_temperature=23,
        field7=0,
    )
    state = _status_to_state(status)
    assert state.connected is True
    assert state.status == DeviceStatus.IDLE
    assert state.block_temperature == 24.0
    assert state.lid_temperature == 23.0


def test_status_to_state_adapter_running():
    status = StatusBroadcast(
        running=1,
        field2=0,
        field3=0,
        field4=0,
        block_temperature=95,
        lid_temperature=110,
        field7=0,
    )
    state = _status_to_state(status)
    assert state.status == DeviceStatus.RUNNING


class _FakeTransport:
    """Minimal stand-in that records calls and returns canned status."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._status = StatusBroadcast(1, 0, 0, 0, 72, 110, 0)

    async def get_status(self) -> StatusBroadcast:
        self.calls.append(("get_status", {}))
        return self._status

    async def start_run(self, **kwargs) -> None:
        self.calls.append(("start_run", kwargs))

    async def stop_run(self) -> None:
        self.calls.append(("stop_run", {}))

    async def disconnect(self) -> None:
        self.calls.append(("disconnect", {}))


@pytest.fixture
def fake_lab():
    transport = _FakeTransport()
    return BentoLab(transport), transport  # type: ignore[arg-type]


async def test_get_state_calls_transport_get_status(fake_lab):
    lab, transport = fake_lab
    state = await lab.get_state()
    assert [c[0] for c in transport.calls] == ["get_status"]
    assert state.status == DeviceStatus.RUNNING
    assert state.block_temperature == 72.0


async def test_run_pcr_flattens_profile_and_calls_start_run(fake_lab):
    lab, transport = fake_lab
    profile = PCRProfile.simple(
        name="Facade Test",
        num_cycles=15,
        initial_denaturation=(95.0, 120),
        denaturation=(95.0, 20),
        annealing=(60.0, 20),
        extension=(72.0, 40),
        final_extension=(72.0, 180),
    )
    await lab.run_pcr(profile, lid_temp=108.0)
    assert len(transport.calls) == 1
    kind, kwargs = transport.calls[0]
    assert kind == "start_run"
    assert kwargs["name"] == "Facade Test"
    assert kwargs["stages"] == [
        (95.0, 120),
        (95.0, 20),
        (60.0, 20),
        (72.0, 40),
        (72.0, 180),
    ]
    assert kwargs["cycles"] == [(4, 2, 15)]
    assert kwargs["lid_temp"] == 108.0


async def test_stop_calls_transport_stop_run(fake_lab):
    lab, transport = fake_lab
    await lab.stop()
    assert transport.calls == [("stop_run", {})]


async def test_disconnect_calls_transport_disconnect(fake_lab):
    lab, transport = fake_lab
    await lab.disconnect()
    assert transport.calls == [("disconnect", {})]
