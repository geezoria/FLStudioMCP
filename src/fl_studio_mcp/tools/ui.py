"""UI helpers: focused window, show hints, open editors."""

from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP

from ..bridge_client import get_client


WindowName = Literal["mixer", "channel_rack", "playlist", "piano_roll", "browser",
                     "plugin", "plugin_generator", "plugin_effect"]


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def ui_focused_window() -> dict:
        """Return info about the currently focused FL Studio window."""
        return get_client().call("ui.focusedWindow")

    @mcp.tool()
    def ui_window_status(name: WindowName | None = None) -> dict:
        """Return visible/focused state for one FL window, or all known windows."""
        return get_client().call("ui.windowStatus", name=name)

    @mcp.tool()
    def ui_show_window(name: WindowName, focus: bool = True) -> dict:
        """Open / focus a main window by name."""
        return get_client().call("ui.showWindow", name=name, focus=focus)

    @mcp.tool()
    def ui_hide_window(name: WindowName) -> dict:
        """Close a main window."""
        return get_client().call("ui.hideWindow", name=name)

    @mcp.tool()
    def ui_hint(message: str) -> dict:
        """Display a transient hint message in FL Studio's status bar."""
        return get_client().call("ui.hint", message=message)

    @mcp.tool()
    def ui_open_piano_roll_for_channel(channel: int, pattern: int | None = None) -> dict:
        """Open the piano roll for a given channel (optionally switch pattern first)."""
        return get_client().call("ui.openPianoRoll", channel=channel, pattern=pattern)

    @mcp.tool()
    def ui_selected_channel() -> dict:
        """Return selected channel index + name."""
        return get_client().call("ui.selectedChannel")

    @mcp.tool()
    def ui_scroll_to_channel(channel: int) -> dict:
        """Scroll the channel rack to show a channel."""
        return get_client().call("ui.scrollToChannel", channel=channel)
