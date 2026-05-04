"""Static assets bundled with the TUI package."""

from __future__ import annotations

from importlib import resources


def bento_art() -> str:
    """Return the bento-box ASCII art as a single string."""
    text = (
        resources.files("bentolab.tui.asci-art")
        .joinpath("bento-art.txt")
        .read_text(encoding="utf-8")
    )
    # Trim any leading/trailing blank lines but keep the internal whitespace.
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)
