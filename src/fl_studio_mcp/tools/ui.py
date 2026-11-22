"""UI helpers: focused window, show hints, open editors."""

from __future__ import annotations

from typing import Any, Literal

from ..bridge_client import get_client
from ._meta import ToolRegistrar


WindowName = Literal["mixer", "channel_rack", "playlist", "piano_roll", "browser",
                     "plugin", "plugin_generator", "plugin_effect"]


def register(tools: ToolRegistrar) -> None:
    @tools.read("Get Focused FL Studio Window")
    def ui_focused_window() -> dict[str, Any]:
        """Return info about the currently focused FL Studio window."""
        return get_client().call("ui.focusedWindow")

    @tools.write("Show FL Studio Window", idempotent=True)
    def ui_show_window(name: WindowName, focus: bool = True) -> dict[str, Any]:
        """Open / focus a main window by name."""
        return get_client().call("ui.showWindow", name=name, focus=focus)

    @tools.write("Hide FL Studio Window", idempotent=True)
    def ui_hide_window(name: WindowName) -> dict[str, Any]:
        """Close a main window."""
        return get_client().call("ui.hideWindow", name=name)

    @tools.write("Display FL Studio Hint")
    def ui_hint(message: str) -> dict[str, Any]:
        """Display a transient hint message in FL Studio's status bar."""
        return get_client().call("ui.hint", message=message)

    @tools.write("Open Plugin or Project Picker")
    def ui_show_picker(mode: str = "plugin") -> dict[str, Any]:
        """Open FL Studio 2026's picker panel: mode "plugin" (so the user can load an
        instrument or effect, which scripts cannot do) or "project"."""
        return get_client().call("ui.showPicker", mode=mode)

    @tools.write("Open Channel Piano Roll", idempotent=True)
    def ui_open_piano_roll_for_channel(channel: int, pattern: int | None = None) -> dict[str, Any]:
        """Open the piano roll for a given channel (optionally switch pattern first)."""
        return get_client().call("ui.openPianoRoll", channel=channel, pattern=pattern)

    @tools.read("Get Selected Channel from UI")
    def ui_selected_channel() -> dict[str, Any]:
        """Return selected channel index + name."""
        return get_client().call("ui.selectedChannel")

    @tools.write("Scroll to Channel", idempotent=True)
    def ui_scroll_to_channel(channel: int) -> dict[str, Any]:
        """Scroll the channel rack to show a channel."""
        return get_client().call("ui.scrollToChannel", channel=channel)
