"""Tests for the bundled bento ASCII art."""

from __future__ import annotations

from bentolab.tui._assets import bento_art


def test_bento_art_loads() -> None:
    art = bento_art()
    assert isinstance(art, str)
    assert len(art) > 100
    # First and last lines should not be blank after trimming.
    lines = art.splitlines()
    assert lines[0].strip()
    assert lines[-1].strip()
