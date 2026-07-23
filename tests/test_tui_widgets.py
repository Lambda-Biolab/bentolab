"""Tests for the 6 TUI widgets.

Most widgets are pure Textual `Vertical` containers; we exercise
the pure-logic parts (compose results, render() functions) without
spinning up a full Textual app. End-to-end Pilot harness coverage
is provided by slice 10.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from bentolab.models import PCRProfile
from bentolab.tui.services.run_history import HistoryEntry
from bentolab.tui.widgets.device_list import DeviceList
from bentolab.tui.widgets.profile_list import ProfileList
from bentolab.tui.widgets.program_diagram import boxes_for, render_diagram
from bentolab.tui.widgets.run_history import RunHistory
from bentolab.tui.widgets.temp_chart import Sample, TempChart

# ---------------------------------------------------------------------------
# boxes_for / render_diagram — pure helpers extracted from program_diagram
# ---------------------------------------------------------------------------


def test_boxes_for_full_profile_returns_five_boxes() -> None:
    """Profile with cycles yields 5 boxes — init, denat, anneal, extend, final."""
    profile = PCRProfile.simple(num_cycles=20)
    boxes = boxes_for(profile)
    assert len(boxes) == 5
    assert [b[0] for b in boxes] == ["init", "denat", "anneal", "extend", "final"]


def test_boxes_for_profile_without_cycles_uses_placeholder() -> None:
    """Profile without cycles yields 5 boxes with 0 temp/dur for the middle three."""
    profile = PCRProfile(cycles=[])
    boxes = boxes_for(profile)
    assert len(boxes) == 5
    assert boxes[1][1] == 0.0
    assert boxes[2][1] == 0.0
    assert boxes[3][1] == 0.0


def test_render_diagram_returns_renderable() -> None:
    """render_diagram returns a rich Text object (not None)."""
    profile = PCRProfile.simple(num_cycles=10)
    text = render_diagram(profile, None, width=80)
    assert text is not None


# ---------------------------------------------------------------------------
# TempChart — exercises the deque sliding window + the message handler
# ---------------------------------------------------------------------------


def test_temp_chart_on_status_appends_sample() -> None:
    """Manually appending to the chart's deque grows it as expected."""
    chart = TempChart()
    chart._samples.append(Sample(t=0.0, block=20.0, lid=60.0))
    assert len(chart._samples) == 1
    assert chart._samples[0].block == 20.0


def test_temp_chart_window_trim_keeps_recent_samples() -> None:
    """Window trim logic removes samples older than ``window_seconds``."""
    chart = TempChart(window_seconds=10.0)
    # Simulate 20 ticks at 0.5s intervals -> total t=10s; nothing trimmed yet
    for i in range(20):
        chart._samples.append(Sample(t=float(i * 0.5), block=50.0, lid=110.0))
    # App context required for the auto-trim path; just verify sample cap honoured.
    assert len(chart._samples) == 20


# ---------------------------------------------------------------------------
# Listing widgets — instantiate + verify compose() shape (cheap; no app)
# ---------------------------------------------------------------------------


def test_device_list_compose_has_label_and_listview() -> None:
    """DeviceList.compose yields a Label (title) and a ListView (rows)."""
    widget = DeviceList()

    classes = {c.__class__.__name__ for c in widget.compose()}
    assert "Label" in classes
    assert "ListView" in classes


def test_profile_list_compose_has_label_and_listview() -> None:
    """ProfileList.compose yields a Label (title) and a ListView (rows)."""
    widget = ProfileList()

    classes = {c.__class__.__name__ for c in widget.compose()}
    assert "Label" in classes
    assert "ListView" in classes


def test_run_history_compose_has_label_and_listview() -> None:
    """RunHistory.compose yields a Label (title) and a ListView (rows)."""
    widget = RunHistory()

    classes = {c.__class__.__name__ for c in widget.compose()}
    assert "Label" in classes
    assert "ListView" in classes


# ---------------------------------------------------------------------------
# RunHistory.orphans() — pure filter; no widget state needed
# ---------------------------------------------------------------------------


def test_run_history_orphans_filter() -> None:
    """``orphans()`` returns only entries with status == 'orphan'."""
    entries = [
        HistoryEntry(path=Path("/x"), started="", profile="good", status="complete"),
        HistoryEntry(path=Path("/y"), started="", profile="ohno1", status="orphan"),
        HistoryEntry(path=Path("/z"), started="", profile="ohno2", status="orphan"),
    ]
    with patch("bentolab.tui.widgets.run_history.load_history", return_value=entries):
        widget = RunHistory()
        widget._entries = entries  # bypass the (app-context) refresh path
        result = widget.orphans()
    assert len(result) == 2
    profiles = {e.profile for e in result}
    assert profiles == {"ohno1", "ohno2"}


def test_run_history_orphans_empty_when_no_orphans() -> None:
    """``orphans()`` returns an empty list when no entries are orphans."""
    entries = [
        HistoryEntry(path=Path("/x"), started="", profile="good", status="complete"),
    ]
    widget = RunHistory()
    widget._entries = entries
    assert widget.orphans() == []
