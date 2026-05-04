"""Pilot smoke tests for BentoLabApp.

We don't snapshot full pixel output (would couple to terminal width and
Textual version); instead we drive the app, inject messages, and assert
on widget-visible state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bentolab import profiles as profile_store
from bentolab.ble_client import PCRRunState
from bentolab.models import CycleStep, PCRProfile, ThermalStep
from bentolab.protocol import StatusBroadcast
from bentolab.tui.app import BentoLabApp
from bentolab.tui.messages import RunProgressed, RunStarted, StatusUpdated


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BENTOLAB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BENTOLAB_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path


def _make_profile() -> PCRProfile:
    return PCRProfile(
        name="tui-demo",
        initial_denaturation=ThermalStep(95.0, 60),
        cycles=[
            CycleStep(
                denaturation=ThermalStep(98.0, 10),
                annealing=ThermalStep(60.0, 20),
                extension=ThermalStep(72.0, 30),
                repeat_count=3,
            )
        ],
        final_extension=ThermalStep(72.0, 30),
    )


async def test_app_starts_and_shows_panes(data_dir: Path) -> None:
    profile_store.save(_make_profile())
    app = BentoLabApp(show_splash=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.profile_list.selected == "tui-demo"
        assert app.device_list is not None
        assert app.history is not None
        assert app.status_pane is not None
        assert app.chart is not None


async def test_status_message_updates_pane(data_dir: Path) -> None:
    app = BentoLabApp(show_splash=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.post_message(
            StatusUpdated(
                status=StatusBroadcast(
                    running=1,
                    field2=0,
                    field3=0,
                    field4=0,
                    block_temperature=72,
                    lid_temperature=110,
                    field7=0,
                )
            )
        )
        await pilot.pause()
        # Chart records a sample.
        assert len(app.chart._samples) == 1
        # With no profile context, status pane reports running-from-device.
        assert "running" in str(app.status_pane._stage_label.render()).lower()


async def test_status_idle_when_running_zero(data_dir: Path) -> None:
    app = BentoLabApp(show_splash=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.post_message(
            StatusUpdated(
                status=StatusBroadcast(
                    running=0,
                    field2=0,
                    field3=0,
                    field4=0,
                    block_temperature=20,
                    lid_temperature=22,
                    field7=0,
                )
            )
        )
        await pilot.pause()
        assert "idle" in str(app.status_pane._stage_label.render()).lower()


async def test_run_started_progress_then_finished(data_dir: Path) -> None:
    app = BentoLabApp(show_splash=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.post_message(RunStarted(profile=_make_profile(), run_id="x"))
        app.post_message(
            RunProgressed(state=PCRRunState(running=True, progress=42, block_temperature=60.0))
        )
        await pilot.pause()
        assert app._current_progress == 42
        assert app._current_profile == "tui-demo"


async def test_quit_when_idle_exits_immediately(data_dir: Path) -> None:
    app = BentoLabApp(show_splash=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        # If we got here without hanging, quit succeeded.
        assert not app._is_running
