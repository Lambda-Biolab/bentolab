"""Startup splash + ``?``-help modal: bento-box ASCII art and key bindings."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from .._assets import bento_art

_KEYS = """\
[bold]Keys[/]
  c   connect          d   disconnect
  r   run profile      s   stop run
  e   edit profile     R   refresh lists
  ?   this screen      q   quit
"""


class SplashModal(ModalScreen[None]):
    """Show the bento ASCII art and keybindings. Dismiss on any key."""

    DEFAULT_CSS = """
    SplashModal {
        align: center middle;
    }
    SplashModal > Vertical {
        background: $surface;
        border: thick $accent;
        padding: 1 2;
        width: 104;
        height: auto;
        max-height: 90%;
    }
    SplashModal Static.art {
        width: 100;
        height: auto;
        color: $accent;
        text-align: left;
    }
    SplashModal Static.tagline {
        width: 100;
        color: $text;
        text-align: center;
        margin: 1 0 0 0;
    }
    SplashModal Static.keys {
        width: 100;
        margin: 1 0 0 0;
    }
    SplashModal Static.hint {
        width: 100;
        color: $text-muted;
        text-align: center;
        margin: 1 0 0 0;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(bento_art(), classes="art", markup=False)
            yield Static("[bold]Bento Lab Workbench[/]", classes="tagline")
            yield Static(_KEYS, classes="keys")
            yield Static("press any key to continue", classes="hint")

    def on_key(self) -> None:
        self.dismiss(None)

    def on_click(self) -> None:
        self.dismiss(None)
