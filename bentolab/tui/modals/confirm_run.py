"""Confirm-run modal — last gate before sending the program."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from ...models import PCRProfile


class ConfirmRunModal(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmRunModal {
        align: center middle;
    }
    ConfirmRunModal > Vertical {
        background: $surface;
        border: thick $accent;
        padding: 1 2;
        width: 56;
        height: auto;
    }
    ConfirmRunModal Button {
        margin: 1 1 0 0;
        min-width: 12;
    }
    ConfirmRunModal #cr-row {
        height: auto;
        align-horizontal: center;
    }
    """

    def __init__(self, profile: PCRProfile, address: str | None) -> None:
        super().__init__()
        self.profile = profile
        self.address = address

    def compose(self) -> ComposeResult:
        runtime_s = self.profile.estimated_runtime_seconds()
        hh = runtime_s // 3600
        mm = (runtime_s % 3600) // 60
        ss = runtime_s % 60
        with Vertical():
            yield Label("Start PCR run?", classes="title")
            yield Static(f"Profile     {self.profile.name}")
            yield Static(f"Device      {self.address or 'auto-discover'}")
            yield Static(f"Lid temp    {self.profile.lid_temperature:.0f} °C")
            yield Static(f"Estimated   {hh:02d}:{mm:02d}:{ss:02d}")
            yield Static("")
            yield Static(
                f"[yellow3]⚠[/] Lid will heat to "
                f"{self.profile.lid_temperature:.0f} °C — verify samples loaded "
                f"and lid closed."
            )
            with Horizontal(id="cr-row"):
                yield Button("Start", variant="success", id="cr-start")
                yield Button("Cancel", variant="default", id="cr-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "cr-start")
