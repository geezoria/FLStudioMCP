"""Transport controls: play, stop, record, position, tempo, loop, signature."""

from __future__ import annotations

from typing import Any, Literal

from ..bridge_client import get_client
from ._meta import ToolRegistrar


def register(tools: ToolRegistrar) -> None:
    @tools.write("Toggle Playback")
    def transport_play() -> dict[str, Any]:
        """Start or toggle playback (FL Studio's Play button)."""
        return get_client().call("transport.start")

    @tools.write("Stop Playback", idempotent=True)
    def transport_stop() -> dict[str, Any]:
        """Stop playback."""
        return get_client().call("transport.stop")

    @tools.write("Toggle Recording")
    def transport_record() -> dict[str, Any]:
        """Toggle record arm state."""
        return get_client().call("transport.record")

    @tools.read("Read Transport Status")
    def transport_status() -> dict[str, Any]:
        """Read playback and record state, position in ticks/bars/seconds, loop mode and BPM."""
        return get_client().call("transport.status")

    @tools.write("Seek Playhead", idempotent=True)
    def transport_set_position(position: float, unit: Literal["seconds", "bars", "ticks"] = "bars") -> dict[str, Any]:
        """Seek from the start of the song in bars, seconds or ticks. Returns the position in bars."""
        return get_client().call("transport.setPosition", position=position, unit=unit)

    @tools.read("Read Song Length")
    def transport_song_length() -> dict[str, Any]:
        """Return song length in ticks, seconds, ms, bars, steps."""
        return get_client().call("transport.length")

    @tools.write("Select Song or Pattern Mode", idempotent=True)
    def transport_set_loop_mode(mode: Literal["song", "pattern"]) -> dict[str, Any]:
        """Set FL Studio's song/pattern loop mode."""
        return get_client().call("transport.setLoopMode", mode=mode)

    @tools.write("Set Playback Speed", idempotent=True)
    def transport_set_playback_speed(speed: float) -> dict[str, Any]:
        """Set the playback speed multiplier (0.25..4.0)."""
        return get_client().call("transport.setPlaybackSpeed", speed=speed)

    @tools.write("Set Project Tempo", idempotent=True)
    def transport_set_tempo(bpm: float) -> dict[str, Any]:
        """Set the project tempo in BPM. Uses processRECEvent for proper undo history."""
        return get_client().call("transport.setTempo", bpm=bpm)

    @tools.write("Tap Project Tempo")
    def transport_tap_tempo() -> dict[str, Any]:
        """Send a tap-tempo event (accumulates to set BPM)."""
        return get_client().call("transport.tapTempo")

    @tools.write("Set Project Time Signature", idempotent=True)
    def transport_set_time_signature(numerator: int, denominator: int) -> dict[str, Any]:
        """Set project time signature (e.g. 4/4, 3/4, 7/8)."""
        return get_client().call("transport.setTimeSignature", numerator=numerator, denominator=denominator)

    @tools.write("Toggle Metronome")
    def transport_toggle_metronome() -> dict[str, Any]:
        """Toggle metronome on/off."""
        return get_client().call("transport.toggleMetronome")

    @tools.write("Toggle Recording Countdown")
    def transport_toggle_countdown_before_recording() -> dict[str, Any]:
        """Toggle 'countdown before recording'."""
        return get_client().call("transport.toggleCountdownBeforeRec")

    @tools.write("Nudge Playhead")
    def transport_jog(steps: int) -> dict[str, Any]:
        """Nudge playhead by `steps` 16th-notes (negative = backward)."""
        return get_client().call("transport.jog", steps=steps)
