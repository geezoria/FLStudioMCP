"""Transport controls: play, stop, record, position, tempo, loop, signature."""

from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP

from ..bridge_client import get_client


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def transport_play() -> dict:
        """Start or toggle playback (FL Studio's Play button)."""
        return get_client().call("transport.start")

    @mcp.tool()
    def transport_stop() -> dict:
        """Stop playback."""
        return get_client().call("transport.stop")

    @mcp.tool()
    def transport_record() -> dict:
        """Toggle record arm state."""
        return get_client().call("transport.record")

    @mcp.tool()
    def transport_status() -> dict:
        """Get is_playing, is_recording, position, loop mode, tempo, signature."""
        return get_client().call("transport.status")

    @mcp.tool()
    def transport_set_position(position: float, unit: Literal["seconds", "bars", "ticks"] = "bars") -> dict:
        """Seek to a specific position in the song."""
        return get_client().call("transport.setPosition", position=position, unit=unit)

    @mcp.tool()
    def transport_song_length() -> dict:
        """Return song length in ticks, seconds, ms, bars, steps."""
        return get_client().call("transport.length")

    @mcp.tool()
    def transport_set_loop_mode(mode: Literal["song", "pattern"]) -> dict:
        """Set FL Studio's song/pattern loop mode."""
        return get_client().call("transport.setLoopMode", mode=mode)

    @mcp.tool()
    def transport_set_playback_speed(speed: float) -> dict:
        """Set the playback speed multiplier (0.25..4.0)."""
        return get_client().call("transport.setPlaybackSpeed", speed=speed)

    @mcp.tool()
    def transport_set_tempo(bpm: float) -> dict:
        """Set the project tempo in BPM. Uses processRECEvent for proper undo history."""
        return get_client().call("transport.setTempo", bpm=bpm)

    @mcp.tool()
    def transport_tap_tempo() -> dict:
        """Send a tap-tempo event (accumulates to set BPM)."""
        return get_client().call("transport.tapTempo")

    @mcp.tool()
    def transport_set_time_signature(numerator: int, denominator: int) -> dict:
        """Set project time signature (e.g. 4/4, 3/4, 7/8)."""
        return get_client().call("transport.setTimeSignature", numerator=numerator, denominator=denominator)

    @mcp.tool()
    def transport_toggle_metronome() -> dict:
        """Toggle metronome on/off."""
        return get_client().call("transport.toggleMetronome")

    @mcp.tool()
    def transport_toggle_countdown_before_recording() -> dict:
        """Toggle 'countdown before recording'."""
        return get_client().call("transport.toggleCountdownBeforeRec")

    @mcp.tool()
    def transport_jog(steps: int) -> dict:
        """Nudge playhead by `steps` 16th-notes (negative = backward)."""
        return get_client().call("transport.jog", steps=steps)

    @mcp.tool()
    def transport_toggle_punch() -> dict:
        """Toggle punch-in/out recording mode."""
        return get_client().call("transport.togglePunch")

    @mcp.tool()
    def transport_toggle_overdub() -> dict:
        """Toggle overdub recording mode."""
        return get_client().call("transport.toggleOverdub")

    @mcp.tool()
    def transport_toggle_loop_recording() -> dict:
        """Toggle loop recording mode."""
        return get_client().call("transport.toggleLoopRecording")

    @mcp.tool()
    def transport_jump_marker(direction: Literal[-1, 1] = 1) -> dict:
        """Jump to the previous (-1) or next (+1) timeline marker."""
        return get_client().call("transport.jumpMarker", direction=direction)
