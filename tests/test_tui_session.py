"""Tests for :mod:`bentolab.tui.services.session`.

Verifies the message stream produced by a :class:`Session` and —
critically — that the NDJSON log file receives a ``run_started``
event in addition to ``run_config``, ``run_progress`` and
``run_finished``. The ``run_started`` event is what
:mod:`bentolab.tui.services.orphan_attach` keys off to distinguish a
real in-flight run from a stub log left by a failed connect.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bentolab.models import PCRProfile
from bentolab.runs import RunLifecycle, RunState
from bentolab.tui.messages import (
    ConnectionChanged,
    RunFinished,
    StatusUpdated,
)
from bentolab.tui.services.session import Session


class _StubLab:
    """Minimal async BLE stub that yields a fixed sequence of RunStates.

    Captures every call so tests can assert the on-the-wire shape
    without any real bleak machinery.
    """

    def __init__(self, states: list[RunState]) -> None:
        self._states = states
        self.connect_called_with: str | None = None
        self.disconnect_called = False
        self.stop_run_called = False
        self.run_profile_called_with: tuple[PCRProfile, float] | None = None
        self._status_callbacks: list = []
        self._disconnect_callbacks: list = []

    def is_connected(self) -> bool:
        return True

    @property
    def _connected_address(self) -> str | None:
        return "AA:BB:CC:DD:EE:FF"

    async def connect(self, address: str | None = None) -> None:
        self.connect_called_with = address

    async def disconnect(self) -> None:
        self.disconnect_called = True

    async def stop_run(self) -> None:
        self.stop_run_called = True

    def on_status(self, callback) -> None:
        self._status_callbacks.append(callback)

    def on_disconnect(self, callback) -> None:
        self._disconnect_callbacks.append(callback)

    def run_profile(
        self,
        profile: PCRProfile,
        lid_temp: float = 110.0,
        poll_interval: float = 5.0,
    ) -> AsyncIterator[RunState]:
        self.run_profile_called_with = (profile, lid_temp)
        return self._aiter(self._states)

    async def _aiter(self, items: list):
        for it in items:
            yield it


class _CollectingApp:
    """Stand-in for a Textual App; records all posted messages."""

    def __init__(self) -> None:
        self.messages: list = []

    def post_message(self, msg) -> None:
        self.messages.append(msg)


@pytest.fixture
def runs_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect :mod:`bentolab._logging`'s ``runs_dir`` to ``tmp_path/runs``.

    ``SessionLogger`` imports ``runs_dir`` at module load time, so we
    patch the *binding inside ``bentolab._logging``* — not the original
    in :mod:`bentolab._data_dirs`.
    """
    target = tmp_path / "runs"
    monkeypatch.setattr("bentolab._logging.runs_dir", lambda: target)
    return target


def _make_state(
    progress: int,
    *,
    block: float = 50.0,
    lid: float = 110.0,
    elapsed: float = 5.0,
) -> RunState:
    return RunState(
        state=RunLifecycle.RUNNING,
        progress=progress,
        block_temperature=block,
        lid_temperature=lid,
        elapsed_seconds=elapsed,
    )


async def test_run_profile_emits_started_progressed_finished(
    runs_dir: Path, tmp_path: Path
) -> None:
    """A short fake run emits the expected message sequence in order."""
    app = _CollectingApp()
    session = Session(app)  # type: ignore[arg-type]
    states = [_make_state(10), _make_state(40), _make_state(99)]
    lab = _StubLab(states)
    session.lab = lab  # type: ignore[assignment]

    profile = PCRProfile(name="demo")
    await session.run_profile(profile)

    types = [type(m).__name__ for m in app.messages]
    assert types[0] == "RunStarted"
    assert types[-1] == "RunFinished"
    # Three progress messages sandwiched between start/finish.
    assert types[1:4] == ["RunProgressed", "RunProgressed", "RunProgressed"]
    finished: RunFinished = app.messages[-1]  # type: ignore[assignment]
    assert finished.success is True
    assert finished.profile_name == "demo"


async def test_run_profile_writes_run_started_event_to_ndjson(runs_dir: Path) -> None:
    """CRITICAL: NDJSON log must contain a 'run_started' event — orphan_attach keys off this.

    Without this row, :func:`find_active_run` would filter every log file
    as a stub from a failed connect, and orphan detection would
    silently never match a real in-flight run.
    """
    runs_dir.mkdir(parents=True, exist_ok=True)
    app = _CollectingApp()
    session = Session(app)  # type: ignore[arg-type]
    session.lab = _StubLab([_make_state(50)])  # type: ignore[assignment]

    profile = PCRProfile(name="orphan-demo")
    await session.run_profile(profile)

    # Find the NDJSON file SessionLogger created and verify its rows.
    ndjson_files = list(runs_dir.glob("*.jsonl"))
    assert ndjson_files, "SessionLogger should have created an NDJSON log file"
    rows = [json.loads(line) for line in ndjson_files[0].read_text().splitlines() if line.strip()]

    events = [row for row in rows if row.get("type") == "event"]
    event_names = [e["event"] for e in events]

    # The four canonical events. run_started is the critical one.
    assert "run_config" in event_names
    assert "run_started" in event_names, (
        "missing run_started event — orphan_attach.find_active_run will silently fail"
    )
    assert "run_progress" in event_names
    assert "run_finished" in event_names


async def test_run_profile_requires_connected_lab(runs_dir: Path) -> None:
    """Connecting-and-running without a link raises; no NDJSON, no messages."""
    app = _CollectingApp()
    session = Session(app)  # type: ignore[arg-type]
    profile = PCRProfile(name="demo")

    with pytest.raises(RuntimeError, match="Not connected"):
        await session.run_profile(profile)

    assert app.messages == []
    assert not list(runs_dir.glob("*.jsonl")) if runs_dir.exists() else True


async def test_run_profile_records_run_finished_with_success_false(
    runs_dir: Path,
) -> None:
    """If the iterator exits without raising, success is True; if exception, success False."""

    # Stub that raises immediately, simulating a mid-run BLE drop.
    class _RaisesLab(_StubLab):
        def run_profile(self, profile, lid_temp=110.0, poll_interval=5.0):
            raise RuntimeError("BLE dropped")

    app = _CollectingApp()
    session = Session(app)  # type: ignore[arg-type]
    session.lab = _RaisesLab([])  # type: ignore[assignment]

    profile = PCRProfile(name="demo")
    with pytest.raises(RuntimeError, match="BLE dropped"):
        await session.run_profile(profile)

    finished = [m for m in app.messages if isinstance(m, RunFinished)][-1]
    assert finished.success is False


async def test_connect_emits_connection_changed_true(runs_dir: Path) -> None:
    """ConnectionChanged(True, address) on successful connect; remembered in registry.

    Patches :class:`BentoLabBLE` because :meth:`Session.connect`
    constructs a fresh client — there is no way to inject a stub
    through the public API.
    """
    from bentolab.tui.services import session as session_mod

    fake_lab = _StubLab([])
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(session_mod, "BentoLabBLE", lambda address=None: fake_lab)
        app = _CollectingApp()
        session = Session(app)  # type: ignore[arg-type]
        await session.connect(address="AA:BB:CC:DD:EE:FF")
    finally:
        monkey.undo()

    # Session.connect sets self.lab BEFORE await; the StubLab returned our fake.
    assert isinstance(app.messages[0], ConnectionChanged)
    assert app.messages[0].connected is True
    assert app.messages[0].address == "AA:BB:CC:DD:EE:FF"


async def test_connect_error_emits_connection_changed_false(runs_dir: Path) -> None:
    """On connect failure, ConnectionChanged(False, error) is posted and re-raised."""
    from bentolab.tui.services import session as session_mod

    class _FailingLab(_StubLab):
        async def connect(self, address=None):  # type: ignore[override]
            raise RuntimeError("no device")

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(session_mod, "BentoLabBLE", lambda address=None: _FailingLab([]))
        app = _CollectingApp()
        session = Session(app)  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="no device"):
            await session.connect()
    finally:
        monkey.undo()

    assert isinstance(app.messages[-1], ConnectionChanged)
    assert app.messages[-1].connected is False
    assert app.messages[-1].error == "no device"


async def test_disconnect_when_lab_is_none_is_noop() -> None:
    """Disconnecting without ever connecting is a no-op (not an error)."""
    app = _CollectingApp()
    session = Session(app)  # type: ignore[arg-type]
    await session.disconnect()
    assert app.messages == []


async def test_forward_status_posts_status_updated(runs_dir: Path) -> None:
    """The on_status callback posts a StatusUpdated textually."""
    app = _CollectingApp()
    session = Session(app)  # type: ignore[arg-type]
    # Bypass public connect() — wire callbacks directly to the bound methods.
    session._forward_status(MagicMock())  # type: ignore[arg-type]
    assert len(app.messages) == 1
    assert isinstance(app.messages[0], StatusUpdated)
