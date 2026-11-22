"""Arrangement management (multi-arrangement projects)."""

from __future__ import annotations

from typing import Any

from ..bridge_client import get_client
from ._meta import ToolRegistrar


def register(tools: ToolRegistrar) -> None:
    @tools.read("Current Arrangement")
    def arrangement_current() -> dict[str, Any]:
        """Return current arrangement index + name."""
        return get_client().call("arrangement.current")

    @tools.read("List Arrangements")
    def arrangement_list() -> dict[str, Any]:
        """Read the current arrangement placeholder. FL's public scripting API cannot enumerate arrangements."""
        return get_client().call("arrangement.list")

    @tools.write("Select Arrangement", idempotent=True)
    def arrangement_select(index: int) -> dict[str, Any]:
        """Request an arrangement switch. FL's public scripting API currently requires switching in the UI."""
        return get_client().call("arrangement.select", index=index)

    @tools.write("Jump to Timeline Marker")
    def arrangement_jump_marker(direction: int = 1) -> dict[str, Any]:
        """Jump playhead to next (+1) or previous (-1) marker within current arrangement."""
        return get_client().call("arrangement.jumpMarker", direction=direction)

    @tools.read("Read Arrangement Playback Time")
    def arrangement_play_time() -> dict[str, Any]:
        """Get current playback time in ticks + bars + seconds."""
        return get_client().call("arrangement.playTime")
