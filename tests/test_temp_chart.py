"""Tests for the pure-logic braille renderer in
:mod:`bentolab.tui.widgets.temp_chart`.

Covers the renderer and its four private helpers without spinning up a
Textual app.
"""

from __future__ import annotations

from bentolab.tui.widgets.temp_chart import (
    Sample,
    _bits_to_rows,
    _braille_cell,
    _line,
    _plot_series,
    render_braille_chart,
)


def test_render_empty_samples_returns_blank_rows() -> None:
    """Empty input → a single blank tuple row at minimum height."""
    rows = render_braille_chart([], width=10, height=5, y_min=0, y_max=100)
    assert len(rows) >= 1
    for block_line, lid_line in rows:
        assert block_line == " " * 10
        assert lid_line == " " * 10


def test_render_width_below_two_returns_blank() -> None:
    """``width < 2`` is degenerate; renderer returns blank rows without crashing."""
    rows = render_braille_chart([Sample(0, 20, 60)], width=1, height=5, y_min=0, y_max=100)
    for block_line, lid_line in rows:
        assert block_line == " "  # width 1
        assert lid_line == " "


def test_render_height_zero_returns_one_row() -> None:
    """``height < 1`` collapses to one row, never zero."""
    rows = render_braille_chart([Sample(0, 50, 50)], width=20, height=0, y_min=0, y_max=100)
    assert len(rows) == 1


def test_render_two_samples_spans_row_count() -> None:
    """Two samples → ``height`` rows, each ``width`` chars wide; characters are valid braille."""
    samples = [Sample(0, 20, 60), Sample(10, 95, 110)]
    rows = render_braille_chart(samples, width=20, height=5, y_min=0, y_max=120)
    assert len(rows) == 5
    for block_line, lid_line in rows:
        assert len(block_line) == 20
        assert len(lid_line) == 20
        # Each char is either the empty braille cell (U+2800) or a braille glyph (>=U+2801).
        for ch in block_line:
            assert 0x2800 <= ord(ch) <= 0x28FF, f"non-braille char: {ch!r}"
        for ch in lid_line:
            assert 0x2800 <= ord(ch) <= 0x28FF, f"non-braille char: {ch!r}"


def test_render_y_range_collapse_handled_gracefully() -> None:
    """``y_min == y_max`` doesn't divide-by-zero; renderer returns rows without crashing."""
    samples = [Sample(0, 50, 50), Sample(10, 50, 50)]
    rows = render_braille_chart(samples, width=10, height=3, y_min=50, y_max=50)
    assert len(rows) == 3


def test_render_clamps_samples_outside_y_range() -> None:
    """Samples outside ``y_min``/``y_max`` clamp to the pixel grid (no IndexError)."""
    samples = [Sample(0, -1000, 1000), Sample(1, 1000, -1000)]
    rows = render_braille_chart(samples, width=10, height=3, y_min=0, y_max=100)
    assert len(rows) == 3


def test_plot_series_sets_at_least_one_bit_per_sample() -> None:
    """Each sample contributes at least one active pixel in its series grid."""
    samples = [Sample(0, 20, 60), Sample(1, 95, 110), Sample(2, 30, 50)]
    bits = _plot_series(samples, px_w=20, px_h=12, y_min=0, y_max=120, key="block")
    assert any(any(row) for row in bits), "block series should have visible pixels"
    lid_bits = _plot_series(samples, px_w=20, px_h=12, y_min=0, y_max=120, key="lid")
    assert any(any(row) for row in lid_bits), "lid series should have visible pixels"


def test_bits_to_rows_returns_correct_shape() -> None:
    """Helper returns ``height`` tuples of ``(block_str, lid_str)``, each width chars."""
    block_bits = [[0] * 20 for _ in range(12)]
    lid_bits = [[0] * 20 for _ in range(12)]
    rows = _bits_to_rows(block_bits, lid_bits, width=10, height=3)
    assert len(rows) == 3
    for block_line, lid_line in rows:
        assert len(block_line) == 10
        assert len(lid_line) == 10


def test_braille_cell_empty_returns_base() -> None:
    """A cell with no pixels set is the empty braille glyph U+2800."""
    bits = [[0] * 10 for _ in range(20)]
    cell = _braille_cell(bits, cell_row=0, cell_col=0)
    assert cell == "⠀"  # empty braille (U+2800)


def test_braille_cell_top_left_dot_only() -> None:
    """Setting only the top-left dot encodes U+2801."""
    bits = [[0] * 2 for _ in range(4)]
    bits[0][0] = 1  # dot at (0,0)
    # Need a 2-wide, 4-tall grid for the cell; supply that and call with cell_row=0, cell_col=0
    bits = [[0] * 2 for _ in range(4)]
    bits[0][0] = 1
    cell = _braille_cell(bits, cell_row=0, cell_col=0)
    assert cell == "⠁"  # U+2801: top-left dot only


def test_line_bresenham_runs_endpoint_to_endpoint() -> None:
    """Bresenham from (0,0) to (3,3) visits all 4 pixels on the diagonal."""
    points = _line(0, 0, 3, 3)
    assert points[0] == (0, 0)
    assert points[-1] == (3, 3)
    # No repeats
    assert len(points) == len(set(points))
    # All 4 pixels on the diagonal
    diag = {(i, i) for i in range(4)}
    assert set(points) == diag


def test_line_bresenham_horizontal() -> None:
    """Horizontal line yields exactly the integer x positions."""
    points = _line(0, 5, 4, 5)
    xs = [p[0] for p in points]
    ys = {p[1] for p in points}
    assert xs == [0, 1, 2, 3, 4]
    assert ys == {5}


def test_line_bresenham_same_point_yields_one_point() -> None:
    """Bresenham from (2,2) to (2,2) yields the single point."""
    assert _line(2, 2, 2, 2) == [(2, 2)]
