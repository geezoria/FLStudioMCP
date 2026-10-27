"""Channel Rack: CRUD + step sequencer grid."""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from ..bridge_client import get_client


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def channel_count(global_count: bool = True) -> dict:
        """Total channel count (global = across all groups)."""
        return get_client().call("channels.count", global_count=global_count)

    @mcp.tool()
    def channel_info(index: int) -> dict:
        """Detailed info on one channel: name/volume/pan/pitch/color/mute/solo/fx_route/type."""
        return get_client().call("channels.info", index=index)

    @mcp.tool()
    def channel_all() -> dict:
        """List of all channels with basic info."""
        return get_client().call("channels.all")

    @mcp.tool()
    def channel_selected() -> dict:
        """Return currently selected channel (or null)."""
        return get_client().call("channels.selected")

    @mcp.tool()
    def channel_select(index: int, exclusive: bool = True) -> dict:
        """Select channel (exclusive=True deselects others)."""
        return get_client().call("channels.select", index=index, exclusive=exclusive)

    @mcp.tool()
    def channel_set_volume(index: int, volume: float) -> dict:
        """Set channel volume (0.0..1.0, where 0.78 ≈ 0 dB)."""
        return get_client().call("channels.setVolume", index=index, volume=volume)

    @mcp.tool()
    def channel_set_pan(index: int, pan: float) -> dict:
        """Set channel pan (-1.0..1.0)."""
        return get_client().call("channels.setPan", index=index, pan=pan)

    @mcp.tool()
    def channel_set_pitch(index: int, semitones: float) -> dict:
        """Set channel pitch offset in semitones (-120..120)."""
        return get_client().call("channels.setPitch", index=index, semitones=semitones)

    @mcp.tool()
    def channel_mute(index: int, muted: Optional[bool] = None) -> dict:
        """Mute / unmute / toggle (None = toggle)."""
        return get_client().call("channels.mute", index=index, muted=muted)

    @mcp.tool()
    def channel_solo(index: int, solo: Optional[bool] = None) -> dict:
        """Solo / unsolo / toggle (None = toggle)."""
        return get_client().call("channels.solo", index=index, solo=solo)

    @mcp.tool()
    def channel_set_name(index: int, name: str) -> dict:
        """Rename a channel."""
        return get_client().call("channels.setName", index=index, name=name)

    @mcp.tool()
    def channel_set_color(index: int, color: str) -> dict:
        """Set channel color. Accepts '#RRGGBB' or 'rgb(r,g,b)'."""
        return get_client().call("channels.setColor", index=index, color=color)

    @mcp.tool()
    def channel_route_to_mixer(index: int, mixer_track: int) -> dict:
        """Route a channel to a given mixer insert track."""
        return get_client().call("channels.routeToMixer", index=index, mixer_track=mixer_track)

    @mcp.tool()
    def channel_trigger_note(index: int, note: int, velocity: int = 100, duration_ms: int = 250) -> dict:
        """Trigger a preview note and schedule note-off (duration defaults to 250 ms)."""
        return get_client().call("channels.triggerNote",
                                 index=index, note=note, velocity=velocity, duration_ms=duration_ms)

    @mcp.tool()
    def channel_get_grid_bit(index: int, position: int) -> dict:
        """Read a single step-sequencer bit (position is 0-based step within the pattern)."""
        return get_client().call("channels.getGridBit", index=index, position=position)

    @mcp.tool()
    def channel_set_grid_bit(index: int, position: int, value: bool) -> dict:
        """Set a single step-sequencer bit."""
        return get_client().call("channels.setGridBit", index=index, position=position, value=value)

    @mcp.tool()
    def channel_get_step_sequence(index: int, pattern: int | None = None) -> dict:
        """Read the full step sequence for a channel (optionally for a specific pattern)."""
        return get_client().call("channels.getStepSequence", index=index, pattern=pattern)

    @mcp.tool()
    def channel_set_step_sequence(index: int, steps: list[int], pattern: int | None = None) -> dict:
        """Overwrite step sequence. `steps` is a list of 0/1 ints (any length up to pattern length)."""
        return get_client().call("channels.setStepSequence", index=index, steps=steps, pattern=pattern)

    @mcp.tool()
    def channel_clear_step_sequence(index: int, pattern: int | None = None) -> dict:
        """Clear all step-sequencer bits for the channel (in the current or specified pattern)."""
        return get_client().call("channels.clearStepSequence", index=index, pattern=pattern)

    @mcp.tool()
    def channel_quick_quantize(index: int) -> dict:
        """Quantize the channel's notes in the current pattern."""
        return get_client().call("channels.quickQuantize", index=index)
