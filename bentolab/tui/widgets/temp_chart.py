"""Two-series braille line chart for block + lid temperatures (pure math).

8-dot braille = 2x4 pixel grid per character. This module contains
**only the pure-function renderer** (``Sample``, ``render_braille_chart``
and helpers). The Textual :class:`Widget` wrapper that consumes this
renderer lives below the helpers, in a slice-6 follow-up, so the math
is unit-testable without spinning up an app.

Coordinate space
----------------
* ``width`` characters wide → ``width * 2`` pixel columns.
* ``height`` characters tall → ``height * 4`` pixel rows.

One character holds up to 8 dots. The renderer maps a time-series into
the pixel grid and emits the dots as a pair of strings per row so the
caller can colour the two series (block vs. lid) independently.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Sample:
    """A single point in the temperature time-series."""

    t: float
    block: float
    lid: float


def render_braille_chart(
    samples: list[Sample], *, width: int, height: int, y_min: float, y_max: float
) -> list[tuple[str, str]]:
    """Render a 2-series braille chart.

    Returns ``height`` rows, each ``(block_line, lid_line)`` so the
    caller can colour the two series independently. Drawing both into
    one row would force a single colour.

    Returns blank rows when samples is empty, width is < 2, or
    height is < 1. Defensive guard: ``y_max - y_min <= 0`` is
    collapsed to ``1e-9`` to avoid divide-by-zero (the chart will
    look degenerate but won't crash).
    """
    if width < 2 or height < 1 or not samples:
        blank = " " * width
        return [(blank, blank)] * max(1, height)

    px_w = width * 2
    px_h = height * 4
    block_bits = _plot_series(samples, px_w, px_h, y_min, y_max, "block")
    lid_bits = _plot_series(samples, px_w, px_h, y_min, y_max, "lid")
    return _bits_to_rows(block_bits, lid_bits, width, height)


def _plot_series(
    samples: list[Sample], px_w: int, px_h: int, y_min: float, y_max: float, key: str
) -> list[list[int]]:
    """Map one series (block or lid) into a 2-D pixel-bit grid.

    Bresenham connects consecutive samples so a single sample doesn't
    leave a lone pixel. Samples are clipped to the pixel grid.
    """
    bits = [[0] * px_w for _ in range(px_h)]
    span = max(samples[-1].t - samples[0].t, 1e-9)
    y_span = max(y_max - y_min, 1e-9)
    t0 = samples[0].t
    prev: tuple[int, int] | None = None
    for s in samples:
        value = s.block if key == "block" else s.lid
        x = int((s.t - t0) / span * (px_w - 1))
        y = int((y_max - value) / y_span * (px_h - 1))
        x = max(0, min(px_w - 1, x))
        y = max(0, min(px_h - 1, y))
        if prev is None:
            bits[y][x] = 1
        else:
            for px, py in _line(*prev, x, y):
                bits[py][px] = 1
        prev = (x, y)
    return bits


def _bits_to_rows(
    block_bits: list[list[int]], lid_bits: list[list[int]], width: int, height: int
) -> list[tuple[str, str]]:
    """Convert 2-D bit grids into ``(block, lid)`` char rows of length ``width``."""
    rows: list[tuple[str, str]] = []
    for cell_row in range(height):
        block_chars = [_braille_cell(block_bits, cell_row, c) for c in range(width)]
        lid_chars = [_braille_cell(lid_bits, cell_row, c) for c in range(width)]
        rows.append(("".join(block_chars), "".join(lid_chars)))
    return rows


def _braille_cell(bits: list[list[int]], cell_row: int, cell_col: int) -> str:
    """Encode one 2x4 character cell from a 2-D bit grid.

    Uses the canonical 8-dot braille pattern (U+2800 base code).
    """
    _BRAILLE_BASE = 0x2800
    _DOT_BITS = (
        (0x01, 0x08),
        (0x02, 0x10),
        (0x04, 0x20),
        (0x40, 0x80),
    )
    code = _BRAILLE_BASE
    for dy in range(4):
        for dx in range(2):
            if bits[cell_row * 4 + dy][cell_col * 2 + dx]:
                code |= _DOT_BITS[dy][dx]
    return chr(code)


def _line(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    """Bresenham — yield every pixel on the segment from ``(x0,y0)`` to ``(x1,y1)``."""
    points: list[tuple[int, int]] = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        points.append((x0, y0))
        if x0 == x1 and y0 == y1:
            return points
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
