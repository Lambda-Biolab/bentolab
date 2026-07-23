"""Run-history pane — past runs from NDJSON files in the data dir."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView

from ..services.run_history import HistoryEntry, load_history

_GLYPH = {
    "complete": "[green3]✓[/]",
    "running": "[cyan1]▶[/]",
    "orphan": "[yellow3]…[/]",
    "error": "[red3]✗[/]",
    "unknown": "[grey50]?[/]",
}


class RunHistory(Vertical):
    DEFAULT_CSS = """
    RunHistory {
        border: round $accent;
        padding: 0 1;
    }
    RunHistory ListView {
        height: auto;
        max-height: 8;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._list_view = ListView(id="history-listview")
        self._entries: list[HistoryEntry] = []

    def compose(self) -> ComposeResult:
        yield Label("Run history", classes="title")
        yield self._list_view

    def on_mount(self) -> None:
        self.refresh_list()

    def refresh_list(self) -> None:
        self._entries = load_history()
        self._list_view.clear()
        for entry in self._entries:
            glyph = _GLYPH.get(entry.status, _GLYPH["unknown"])
            label = f"{glyph} {entry.profile}  [dim]{entry.started[:16]}[/]"
            self._list_view.append(ListItem(Label(label), name=str(entry.path)))
        if not self._entries:
            self._list_view.append(ListItem(Label("(no runs recorded yet)"), name=""))

    def orphans(self) -> list[HistoryEntry]:
        return [e for e in self._entries if e.status == "orphan"]
