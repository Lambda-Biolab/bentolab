"""Pure-logic tests for the braille temp-chart renderer."""

from __future__ import annotations

from bentolab.tui.widgets.temp_chart import Sample, render_braille_chart


def test_render_returns_correct_shape() -> None:
    samples = [Sample(t=0.0, block=20.0, lid=110.0), Sample(t=10.0, block=72.0, lid=110.0)]
    rows = render_braille_chart(samples, width=20, height=4, y_min=0, y_max=120)
    assert len(rows) == 4
    for block_row, lid_row in rows:
        assert len(block_row) == 20
        assert len(lid_row) == 20


def test_render_handles_empty_samples() -> None:
    rows = render_braille_chart([], width=20, height=4, y_min=0, y_max=120)
    # Returns a height-sized output even when empty
    assert len(rows) == 4
    for block_row, lid_row in rows:
        assert block_row == " " * 20
        assert lid_row == " " * 20


def test_render_clamps_to_range() -> None:
    samples = [
        Sample(t=0.0, block=-50.0, lid=200.0),  # both outside range
        Sample(t=1.0, block=300.0, lid=-10.0),
    ]
    rows = render_braille_chart(samples, width=10, height=2, y_min=0, y_max=120)
    # Renders without exceptions and produces the requested shape.
    assert len(rows) == 2
    for block_row, lid_row in rows:
        assert len(block_row) == 10
        assert len(lid_row) == 10


def test_render_single_sample() -> None:
    samples = [Sample(t=0.0, block=72.0, lid=110.0)]
    rows = render_braille_chart(samples, width=10, height=2, y_min=0, y_max=120)
    assert len(rows) == 2
