"""Tests for the main TUI application, ``BentoLabApp``.

End-to-end Pilot smoke exercising the 4-pane layout, key bindings,
and message wiring. Most paths require a real BLE device so coverage
of the action_* methods remains low — those will get Pilot coverage
in the slice-10 follow-up.
"""

from __future__ import annotations

import pytest

from bentolab.tui.app import BentoLabApp
from bentolab.tui.modals.splash import SplashModal


@pytest.mark.asyncio
async def test_app_compose_has_4_panes() -> None:
    """``BentoLabApp.compose()`` mounts all 6 widgets."""
    app = BentoLabApp(show_splash=False)
    async with app.run_test() as pilot:
        classes = {child.__class__.__name__ for child in app.screen.walk_children()}
        for required in (
            "DeviceList",
            "ProfileList",
            "RunHistory",
            "StatusPane",
            "ProgramDiagram",
            "TempChart",
        ):
            assert required in classes, f"missing widget: {required}"
        await pilot.pause()


@pytest.mark.asyncio
async def test_app_with_splash_pushes_splash_screen() -> None:
    """Default ``show_splash=True`` pushes SplashModal on mount."""
    app = BentoLabApp()  # default show_splash=True
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, SplashModal)


@pytest.mark.asyncio
async def test_app_no_splash_keeps_default_screen() -> None:
    """``show_splash=False`` keeps the default screen on mount."""
    app = BentoLabApp(show_splash=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not isinstance(app.screen, SplashModal)


@pytest.mark.asyncio
async def test_app_session_is_set_on_init() -> None:
    """``BentoLabApp.__init__`` creates a Session bound to the app."""
    app = BentoLabApp(show_splash=False)
    async with app.run_test() as pilot:
        assert app.session is not None
        assert app.session.app is app  # type: ignore[attr-defined]
        assert app.session.connected is False
        await pilot.pause()


@pytest.mark.asyncio
async def test_app_quit_when_idle_exits_immediately() -> None:
    """``q`` key without an active run directly calls ``self.exit()``."""
    app = BentoLabApp(show_splash=False)
    async with app.run_test() as pilot:
        # Quit clean path; pilot will see the exit and the run_test context exits.
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_app_exposes_run_entry() -> None:
    """``app.run()`` is reachable through ``bentolab.tui.run``."""
    from bentolab.tui.app import run as tui_run

    assert callable(tui_run)
    # Verify the lazy __getattr__ resolution path also works.
    import bentolab.tui as tui_pkg

    assert tui_pkg.run is tui_run


@pytest.mark.asyncio
async def test_orphan_attach_message_does_not_crash_app() -> None:
    """Posting a StatusUpdated with running=0 doesn't crash the orphan path."""
    from bentolab.protocol import StatusBroadcast
    from bentolab.tui.messages import StatusUpdated

    app = BentoLabApp(show_splash=False)
    async with app.run_test() as pilot:
        # Idle state with no orphan in flight: must not raise.
        status = StatusBroadcast(0, 0, 0, 0, 25.0, 110.0, 0)
        app.post_message(StatusUpdated(status=status))
        await pilot.pause()
        # When nothing matches, the pane stays detached.
        assert app.status_pane._active_profile is None
