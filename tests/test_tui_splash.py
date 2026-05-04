"""Splash screen pilot test."""

from __future__ import annotations

from pathlib import Path

import pytest

from bentolab.tui.app import BentoLabApp


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BENTOLAB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BENTOLAB_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path


async def test_splash_appears_then_dismisses(data_dir: Path) -> None:
    app = BentoLabApp(show_splash=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Splash modal should be on top of the screen stack.
        assert any("SplashModal" in type(s).__name__ for s in app.screen_stack)
        await pilot.press("space")
        await pilot.pause()
        # Splash dismissed.
        assert not any("SplashModal" in type(s).__name__ for s in app.screen_stack)
