"""Profile picker — lists YAML profiles from the user-data dir."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView

from ... import profiles as profile_store
from ..messages import ProfilesChanged


class ProfileList(Vertical):
    DEFAULT_CSS = """
    ProfileList {
        border: round $accent;
        padding: 0 1;
    }
    ProfileList ListView {
        height: auto;
        max-height: 12;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._list_view = ListView(id="profile-listview")

    def compose(self) -> ComposeResult:
        yield Label("Profiles", classes="title")
        yield self._list_view

    def on_mount(self) -> None:
        self.refresh_list()

    def on_profiles_changed(self, _message: ProfilesChanged) -> None:
        self.refresh_list()

    def refresh_list(self) -> None:
        self._list_view.clear()
        names = profile_store.list_profiles()
        for name in names:
            self._list_view.append(ListItem(Label(name), name=name))
        if names:
            self._list_view.index = 0
        else:
            self._list_view.append(
                ListItem(Label("(no profiles — `bentolab profile new <name>`)"), name="")
            )

    @property
    def selected(self) -> str | None:
        item = self._list_view.highlighted_child
        if item is None:
            return None
        return item.name or None
