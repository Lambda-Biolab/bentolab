"""Confirm-quit modal — guards against quitting mid-run."""

from __future__ import annotations

from enum import StrEnum

from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class QuitChoice(StrEnum):
    STOP_AND_QUIT = "stop_and_quit"
    QUIT = "quit"
    CANCEL = "cancel"


class ConfirmQuitModal(ModalScreen[QuitChoice]):
    DEFAULT_CSS = """
    ConfirmQuitModal {
        align: center middle;
    }
    ConfirmQuitModal > Vertical {
        background: $surface;
        border: thick $error;
        padding: 1 2;
        width: 56;
        height: auto;
    }
    ConfirmQuitModal Button {
        margin: 1 1 0 0;
    }
    ConfirmQuitModal #cq-grid {
        grid-size: 3 1;
        height: auto;
    }
    """

    def __init__(self, profile_name: str, progress: int) -> None:
        super().__init__()
        self.profile_name = profile_name
        self.progress = progress

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Quit while a run is in progress?", classes="title")
            yield Static("The PCR will continue on the device.")
            yield Static("Live monitoring will stop. Run log will close.")
            yield Static("")
            yield Static(f"Profile  {self.profile_name}")
            yield Static(f"Progress {self.progress}%")
            with Grid(id="cq-grid"):
                yield Button("Stop run + quit", variant="error", id="cq-stop")
                yield Button("Quit", variant="warning", id="cq-quit")
                yield Button("Cancel", variant="default", id="cq-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        choice = {
            "cq-stop": QuitChoice.STOP_AND_QUIT,
            "cq-quit": QuitChoice.QUIT,
            "cq-cancel": QuitChoice.CANCEL,
        }[event.button.id or "cq-cancel"]
        self.dismiss(choice)
