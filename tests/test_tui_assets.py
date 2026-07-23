"""Tests for :mod:`bentolab.tui._assets`: bundled ASCII art loader."""

from __future__ import annotations

import pytest

from bentolab.tui._assets import bento_art


def test_bento_art_default_returns_nonempty_string() -> None:
    """Default call returns a trimmed, non-empty ASCII art string."""
    text = bento_art()
    assert isinstance(text, str)
    assert len(text) > 1000, f"bento art suspiciously short: {len(text)} chars"


def test_bento_art_caps_lines() -> None:
    """``max_lines`` caps the returned lines."""
    full = bento_art(max_lines=0).splitlines()
    assert len(bento_art(max_lines=5).splitlines()) == 5
    assert len(bento_art(max_lines=22).splitlines()) == 22
    # sanity: full > capped
    assert len(full) > 22


def test_bento_art_trims_leading_and_trailing_blank_lines() -> None:
    """Leading/trailing blank lines are stripped before the cap applies."""
    text = bento_art()
    lines = text.splitlines()
    assert lines, "trimmed art should not be empty"
    assert lines[0].strip() != ""
    assert lines[-1].strip() != ""


def test_bento_art_max_lines_invalid_type_raises() -> None:
    """Non-int ``max_lines`` (or anything non-numeric) is rejected."""
    with pytest.raises(TypeError):
        bento_art(max_lines="five")  # type: ignore[arg-type]
