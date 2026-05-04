"""Startup splash + ``?``-help modal: bento-box ASCII art and key bindings."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from textual import events
from textual.app import ComposeResult
from textual.containers import VerticalScroll
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

_DISMISS_KEYS = {"escape", "enter", "space", "q"}


class SplashModal(ModalScreen[None]):
    """Show the bento ASCII art and keybindings. Dismiss on any key."""

    DEFAULT_CSS = """
    SplashModal {
        align: center middle;
    }
    SplashModal > VerticalScroll {
        background: $surface;
        border: thick $accent;
        padding: 1 2;
        width: 104;
        max-width: 100%;
        height: auto;
        max-height: 90%;
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
        with VerticalScroll():
            yield Static(_HEADER.format(version=_pkg_version()), classes="header")
            yield Static(bento_art(), classes="art", markup=False)
            yield Static(_KEYS, classes="keys")
            yield Static("scroll to see keys • Esc / Enter / q to dismiss", classes="hint")

    def on_key(self, event: events.Key) -> None:
        # Let arrow / page keys scroll the content; only dismiss on the
        # explicit close keys.
        if event.key in _DISMISS_KEYS:
            event.stop()
            self.dismiss(None)

    def on_click(self) -> None:
        self.dismiss(None)
