"""SpliceCraft-style block schematic of a PCR program — mirrors the
on-device LCD view. Five boxes (init / denat / anneal / extend / final)
with `× N` cycle annotation; the active box is highlighted; bottom
line shows current setpoint and seconds-remaining-in-step.
"""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text
from textual.widget import Widget

from ...models import PCRProfile, StageInfo

_ACTIVE_STYLE = "bold black on bright_cyan"
_DIM_STYLE = "dim"
_BORDER_STYLE = "white"


def _phase_for_box(idx: int) -> str:
    return ("initial", "denat", "anneal", "extend", "final")[idx]


def _fmt_dur(seconds: int) -> str:
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60} min"
    if seconds >= 60:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds} s"


def boxes_for(profile: PCRProfile) -> list[tuple[str, float, int]]:
    """Return the 5 representative steps as (label, temp, duration_s).

    Cycle steps use the first ``CycleStep`` (the typical case). If the
    profile has no cycles, those three boxes get a placeholder.
    """
    init = profile.initial_denaturation
    final = profile.final_extension
    if profile.cycles:
        c = profile.cycles[0]
        return [
            ("init", init.temperature, init.duration),
            ("denat", c.denaturation.temperature, c.denaturation.duration),
            ("anneal", c.annealing.temperature, c.annealing.duration),
            ("extend", c.extension.temperature, c.extension.duration),
            ("final", final.temperature, final.duration),
        ]
    return [
        ("init", init.temperature, init.duration),
        ("denat", 0.0, 0),
        ("anneal", 0.0, 0),
        ("extend", 0.0, 0),
        ("final", final.temperature, final.duration),
    ]


def render_diagram(
    profile: PCRProfile,
    stage: StageInfo | None,
    *,
    width: int,
) -> RenderableType:
    boxes = boxes_for(profile)
    box_w = max(10, (width - 6) // 5)
    rows = _build_rows(boxes, _active_index(stage), box_w)

    out = Text()
    for row in rows:
        out.append(row)
        out.append("\n")
    cycles_label = _cycles_label(profile, box_w)
    if cycles_label is not None:
        out.append(cycles_label)
        out.append("\n")
    footer = _footer(stage)
    if footer is not None:
        out.append(footer)
    return out


def _build_rows(boxes: list[tuple[str, float, int]], active_idx: int, box_w: int) -> list[Text]:
    rows = [Text() for _ in range(6)]  # top, label, temp, dur, name, bottom
    for i, (label, temp, dur) in enumerate(boxes):
        active = i == active_idx
        _append_box(rows, label, temp, dur, i, box_w, active)
        if i < len(boxes) - 1:
            sep = " → " if i in (0, 3) else "─┬─"
            for j, row in enumerate(rows):
                style = _BORDER_STYLE if j in (0, 5) else _DIM_STYLE
                row.append(_pad(sep, 3), style=style)
    return rows


def _append_box(
    rows: list[Text],
    label: str,
    temp: float,
    dur: int,
    idx: int,
    box_w: int,
    active: bool,
) -> None:
    border = _ACTIVE_STYLE if active else _BORDER_STYLE
    inner = _ACTIVE_STYLE if active else _DIM_STYLE
    rows[0].append("┌" + "─" * (box_w - 2) + "┐", style=border)
    _append_inner(rows[1], _center(label, box_w - 2), border, inner)
    _append_inner(rows[2], _center(f"{temp:.0f}°C" if dur else "—", box_w - 2), border, inner)
    _append_inner(rows[3], _center(_fmt_dur(dur) if dur else "—", box_w - 2), border, inner)
    _append_inner(rows[4], _center(_phase_for_box(idx), box_w - 2), border, inner)
    rows[5].append("└" + "─" * (box_w - 2) + "┘", style=border)


def _append_inner(row: Text, content: str, border: str, inner: str) -> None:
    row.append("│", style=border)
    row.append(content, style=inner)
    row.append("│", style=border)


def _center(text: str, width: int) -> str:
    if len(text) >= width:
        return text[:width]
    pad = width - len(text)
    left = pad // 2
    right = pad - left
    return " " * left + text + " " * right


def _pad(text: str, width: int) -> str:
    if len(text) >= width:
        return text[:width]
    return text + " " * (width - len(text))


def _active_index(stage: StageInfo | None) -> int:
    if stage is None:
        return -1
    return {
        "initial": 0,
        "denat": 1,
        "anneal": 2,
        "extend": 3,
        "final": 4,
        "hold": -1,
    }.get(stage.phase, -1)


def _cycles_label(profile: PCRProfile, box_w: int) -> Text | None:
    if not profile.cycles:
        return None
    total = profile.total_cycle_count()
    # Position the "× N cycles" caption under boxes 1–3 (denat/anneal/extend).
    leading = (box_w + 3) + 1  # box 0 + arrow
    span = (box_w * 3) + 6
    caption = f"←─── × {total} cycles ───→"
    text = Text(" " * leading + _center(caption, span), style="bold bright_cyan")
    return text


def _footer(stage: StageInfo | None) -> Text | None:
    if stage is None:
        return Text("(start a run from `r` to track stage live)", style=_DIM_STYLE)
    if stage.phase == "hold":
        return Text(f"hold @ {stage.setpoint:.0f}°C", style="bold")
    remaining = max(0, int(stage.seconds_remaining))
    mm = remaining // 60
    ss = remaining % 60
    return Text.from_markup(
        f"[bold]{stage.label}[/]    "
        f"[bright_cyan]{stage.setpoint:.0f}°C[/]    "
        f"[dim]{mm}m{ss:02d}s remaining[/]"
    )


class ProgramDiagram(Widget):
    """Block schematic of the active PCR program.

    Mirrors the Bento Lab device's LCD: five boxes representing
    init / denat / anneal / extend / final, with cycle count and a
    live "current step" footer. Updated via :meth:`set_profile` and
    :meth:`update_stage`.
    """

    DEFAULT_CSS = """
    ProgramDiagram {
        height: auto;
        min-height: 10;
        border: round $accent;
        padding: 0 1;
    }
    """

    BORDER_TITLE = "Program"

    def __init__(self) -> None:
        super().__init__()
        self._profile: PCRProfile | None = None
        self._stage: StageInfo | None = None

    def set_profile(self, profile: PCRProfile | None) -> None:
        self._profile = profile
        self._stage = None
        self.refresh()

    def update_stage(self, stage: StageInfo | None) -> None:
        self._stage = stage
        self.refresh()

    def render(self) -> RenderableType:
        if self._profile is None:
            return Text(
                "(start a run from this TUI to see the program diagram)",
                style="dim",
            )
        return render_diagram(self._profile, self._stage, width=max(self.size.width - 2, 60))
