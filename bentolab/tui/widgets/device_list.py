"""Device list — known/last-seen devices from devices.json."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView

from ... import devices as device_registry


class DeviceList(Vertical):
    DEFAULT_CSS = """
    DeviceList {
        border: round $accent;
        padding: 0 1;
    }
    DeviceList ListView {
        height: auto;
        max-height: 6;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._list_view = ListView(id="device-listview")

    def compose(self) -> ComposeResult:
        yield Label("Devices", classes="title")
        yield self._list_view

    def on_mount(self) -> None:
        self.refresh_list()

    def refresh_list(self) -> None:
        self._list_view.clear()
        seen = device_registry.list_devices()
        seen.sort(key=lambda d: d.last_seen, reverse=True)
        for d in seen:
            label = f"{d.name or '(unnamed)'}  [dim]{d.transport}[/]  [dim]{d.address}[/]"
            self._list_view.append(ListItem(Label(label), name=d.address))
        if not seen:
            self._list_view.append(
                ListItem(Label("(no devices — press [c]onnect to scan)"), name="")
            )

    @property
    def selected(self) -> str | None:
        item = self._list_view.highlighted_child
        if item is None:
            return None
        return item.name or None
