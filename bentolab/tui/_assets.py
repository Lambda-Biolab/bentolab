"""Static assets bundled with the TUI package."""

from __future__ import annotations

from importlib import resources


def bento_art(max_lines: int = 22) -> str:
    """Return the bento-box ASCII art as a single string.

    The full file is ~50 lines; the recognizable silhouette (lid +
    contents) lives in the first ~22 non-blank lines. The default cap
    keeps the splash modal fitting on a 40-row terminal without
    scrolling. Pass a larger ``max_lines`` to show more.
    """
    text = (
        resources.files("bentolab.tui.asci-art")
        .joinpath("bento-art.txt")
        .read_text(encoding="utf-8")
    )
    # Trim leading/trailing blank lines but keep internal whitespace.
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if max_lines > 0:
        lines = lines[:max_lines]
    return "\n".join(lines)
