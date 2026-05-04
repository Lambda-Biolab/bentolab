"""Tests for the ProgramDiagram pure-render helpers."""

from __future__ import annotations

from bentolab.models import CycleStep, PCRProfile, ThermalStep
from bentolab.tui.widgets.program_diagram import boxes_for, render_diagram


def _profile() -> PCRProfile:
    return PCRProfile(
        name="t",
        initial_denaturation=ThermalStep(95.0, 300),
        cycles=[
            CycleStep(
                denaturation=ThermalStep(98.0, 10),
                annealing=ThermalStep(60.0, 30),
                extension=ThermalStep(72.0, 120),
                repeat_count=35,
            )
        ],
        final_extension=ThermalStep(72.0, 300),
    )


def test_boxes_for_returns_five() -> None:
    boxes = boxes_for(_profile())
    assert len(boxes) == 5
    labels = [b[0] for b in boxes]
    assert labels == ["init", "denat", "anneal", "extend", "final"]


def test_render_without_stage_shows_diagram() -> None:
    out = render_diagram(_profile(), None, width=80)
    text = out.plain  # type: ignore[union-attr]
    assert "95°C" in text
    assert "98°C" in text
    assert "60°C" in text
    assert "72°C" in text
    assert "× 35 cycles" in text


def test_render_with_stage_shows_remaining() -> None:
    s = _profile().stage_at(300.0 + 10.0 + 5.0)  # cycle 1 anneal, 5s in
    out = render_diagram(_profile(), s, width=80)
    text = out.plain  # type: ignore[union-attr]
    assert "anneal" in text
    assert "60°C" in text
    assert "remaining" in text


def test_render_no_cycles_handles_gracefully() -> None:
    p = PCRProfile(
        name="x",
        initial_denaturation=ThermalStep(95.0, 60),
        cycles=[],
        final_extension=ThermalStep(72.0, 60),
    )
    out = render_diagram(p, None, width=80)
    text = out.plain  # type: ignore[union-attr]
    assert "init" in text
    assert "final" in text
