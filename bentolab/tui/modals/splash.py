"""Startup splash + ``?``-help modal: bento-box ASCII art and key bindings."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from .._assets import bento_art


def _pkg_version() -> str:
    try:
        return version("bentolab")
    except PackageNotFoundError:
        return "dev"


_HEADER = (
    "[bold $accent]Bento Lab Workbench[/]  [dim]v{version}[/]\n"
    "[dim]Open-source BLE workbench for the Bento Lab PCR workstation[/]\n"
    "[dim]maintained by [bold]@antomicblitz[/] and [bold]@qte77[/][/]"
)

_KEYS = """\
[bold]Keys[/]
  c   connect          d   disconnect
  r   run profile      s   stop run
  e   edit profile     R   refresh lists
  D   forget device    ?   this screen
  q   quit
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
        max-width: 100%;
        height: auto;
    }
    SplashModal Static.header {
        width: 100;
        text-align: center;
        margin: 0 0 1 0;
    }
    SplashModal Static.art {
        width: 100;
        height: auto;
        color: $accent;
        text-align: left;
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
            yield Static(_HEADER.format(version=_pkg_version()), classes="header")
            yield Static(bento_art(), classes="art", markup=False)
            yield Static(_KEYS, classes="keys")
            yield Static("press any key to dismiss", classes="hint")

    def on_key(self, _event: events.Key) -> None:
        self.dismiss(None)

    def on_click(self) -> None:
        self.dismiss(None)
