"""Scan modal — discover BLE devices, pick one, remember it."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListItem, ListView, Static

from ... import devices as device_registry
from ...ble_client import BentoLabBLE


class ScanModal(ModalScreen[str | None]):
    DEFAULT_CSS = """
    ScanModal {
        align: center middle;
    }
    ScanModal > Vertical {
        background: $surface;
        border: thick $accent;
        padding: 1 2;
        width: 70;
        height: 22;
    }
    ScanModal Button {
        margin: 1 1 0 0;
    }
    ScanModal #scan-grid {
        grid-size: 2 1;
        height: auto;
    }
    ScanModal ListView {
        height: 1fr;
    }
    """

    def __init__(self, timeout: float = 8.0) -> None:
        super().__init__()
        self.timeout = timeout
        self._results: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Scan for Bento Lab devices", classes="title")
            self._status = Static("[dim]Scanning…[/]")
            yield self._status
            self._list = ListView(id="scan-list")
            yield self._list
            with Grid(id="scan-grid"):
                yield Button("Connect", variant="primary", id="scan-connect")
                yield Button("Cancel", variant="default", id="scan-cancel")

    async def on_mount(self) -> None:
        try:
            results = await self._scan()
        except Exception as e:
            self._status.update(f"[red3]Scan failed: {e}[/]")
            return
        self._results = results
        if not results:
            self._status.update("[yellow3]No devices found.[/]")
            return
        self._status.update(f"[green3]Found {len(results)} device(s).[/]")
        for address, name in results:
            self._list.append(
                ListItem(Label(f"{name or '(unnamed)'}  [dim]{address}[/]"), name=address)
            )

    async def _scan(self) -> list[tuple[str, str]]:
        lab = BentoLabBLE()
        discovered = await asyncio.wait_for(lab.discover(timeout=self.timeout), self.timeout + 5)
        return [(dev.address, dev.name or "") for dev, _adv in discovered]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "scan-cancel":
            self.dismiss(None)
            return
        item = self._list.highlighted_child
        if item is None or not item.name:
            self.dismiss(None)
            return
        address = item.name
        name = next((n for a, n in self._results if a == address), "")
        device_registry.remember(
            device_registry.Device(
                address=address,
                name=name,
                transport="ble",
                last_seen=datetime.now(tz=UTC).isoformat(),
            )
        )
        self.dismiss(address)
