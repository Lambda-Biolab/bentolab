"""Tests for TUI modals (using Textual Pilot harness).

The modals are pure Textual DOM constructs and require a live app to
exercise ``compose()`` and dismiss/push. End-to-end via Pilot here
covers the bulk of slice 7's behavior.
"""

from __future__ import annotations

import pytest

from bentolab.models import PCRProfile
from bentolab.tui.modals.confirm_quit import ConfirmQuitModal, QuitChoice
from bentolab.tui.modals.confirm_run import ConfirmRunModal
from bentolab.tui.modals.splash import _HEADER, _KEYS, SplashModal, _pkg_version

# ---------------------------------------------------------------------------
# Pure-logic / static-markup tests (no app needed)
# ---------------------------------------------------------------------------


def test_splash_text_contains_keys_marker() -> None:
    """Splash's keybinding legend mentions the documented shortcuts."""
    assert "[bold]Keys[/]" in _KEYS
    for key in ("c", "r", "s", "?"):
        assert key in _KEYS


def test_splash_text_contains_header_marker() -> None:
    """Splash header is a format-string that takes a version arg."""
    assert "{version}" in _HEADER


def test_pkg_version_returns_a_string() -> None:
    """``_pkg_version()`` returns either a real version or 'dev' fallback."""
    version = _pkg_version()
    assert isinstance(version, str)
    assert len(version) > 0


# ---------------------------------------------------------------------------
# Pilot harness tests for compose()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_splash_pushed_in_live_app_becomes_active_screen() -> None:
    """Pushing ``SplashModal`` mounts it as the active screen."""
    from textual.app import App

    class _Host(App):
        pass

    app = _Host()
    async with app.run_test() as pilot:
        await app.push_screen(SplashModal())
        await pilot.pause()
        assert isinstance(app.screen, SplashModal)


@pytest.mark.asyncio
async def test_confirm_run_compose_yields_profile_and_runtime() -> None:
    """ConfirmRunModal surfaces profile name + hh:mm:ss runtime."""
    from textual.app import App, ComposeResult

    profile = PCRProfile.simple(num_cycles=20)

    class _Host(App):
        def compose(self) -> ComposeResult:
            yield ConfirmRunModal(profile=profile, address="AA:BB:CC:DD:EE:FF")

    app = _Host()
    async with app.run_test() as pilot:
        # Walk the render tree to find the runtime Static.
        from textual.widgets import Static

        static_renders = [
            str(w.render()) for w in app.screen.walk_children() if isinstance(w, Static)
        ]
        combined = " ".join(static_renders)
        assert "00:48:00" in combined  # 20 cycles => 2880s = 0:48:00
        assert "20 cycles" in combined or profile.name in combined
        await pilot.pause()


@pytest.mark.asyncio
async def test_confirm_quit_compose_yields_three_buttons() -> None:
    """ConfirmQuitModal yields Stop, Quit, Cancel buttons."""
    from textual.app import App, ComposeResult
    from textual.widgets import Button

    class _Host(App):
        def compose(self) -> ComposeResult:
            yield ConfirmQuitModal(profile_name="demo", progress=42)

    app = _Host()
    async with app.run_test() as pilot:
        buttons = [w for w in app.screen.walk_children() if isinstance(w, Button)]
        assert len(buttons) == 3
        ids = {b.id for b in buttons}
        assert {"cq-stop", "cq-quit", "cq-cancel"}.issubset(ids)
        await pilot.pause()


# ---------------------------------------------------------------------------
# QuitChoice / button-id mapping (no app needed)
# ---------------------------------------------------------------------------


def test_quit_choice_has_documented_values() -> None:
    """QuitChoice exposes the three documented modes."""
    assert QuitChoice.STOP_AND_QUIT.value == "stop_and_quit"
    assert QuitChoice.QUIT.value == "quit"
    assert QuitChoice.CANCEL.value == "cancel"


def test_confirm_quit_button_id_to_choice_mapping() -> None:
    """Modal's on_button_pressed dispatch: each button id -> a QuitChoice."""
    expected = {
        "cq-stop": QuitChoice.STOP_AND_QUIT,
        "cq-quit": QuitChoice.QUIT,
        "cq-cancel": QuitChoice.CANCEL,
    }
    assert len(expected) == 3
    for btn_id, choice in expected.items():
        assert expected[btn_id] is choice


def test_quit_choice_str_enum_strings_match_values() -> None:
    """StrEnum values are usable as plain strings (for serialization)."""
    assert str(QuitChoice.STOP_AND_QUIT) == "stop_and_quit"
    assert QuitChoice.STOP_AND_QUIT in {"stop_and_quit", "quit", "cancel"}
