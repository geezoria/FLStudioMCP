"""Channel Rack: CRUD + step sequencer grid."""

from __future__ import annotations

from typing import Any, Optional

from ..bridge_client import get_client
from ._meta import ToolRegistrar


def register(tools: ToolRegistrar) -> None:
    @tools.read("Count Channels")
    def channel_count(global_count: bool = True) -> dict[str, Any]:
        """Total channel count (global = across all groups)."""
        return get_client().call("channels.count", global_count=global_count)

    @tools.read("Get Channel Details")
    def channel_info(index: int) -> dict[str, Any]:
        """Return volume, pan, pitch, mixer routing and other details for a 0-based global channel index."""
        return get_client().call("channels.info", index=index)

    @tools.read("List Channels")
    def channel_all() -> dict[str, Any]:
        """List channels with 0-based global indices, names, controls and mixer routing."""
        return get_client().call("channels.all")

    @tools.read("Get Channel Rack Swing")
    def channel_get_swing() -> dict[str, Any]:
        """Return the Channel rack's global swing, 0.0 (straight) to 1.0 (maximum). FL Studio 2026+."""
        return get_client().call("channels.getSwing")

    @tools.write("Set Channel Rack Swing", idempotent=True)
    def channel_set_swing(amount: float) -> dict[str, Any]:
        """Set the Channel rack's global swing, 0.0 (straight) to 1.0 (maximum); clamped. FL Studio 2026+.

        This is the rack-wide Swing knob and affects every step-sequenced channel.
        """
        return get_client().call("channels.setSwing", amount=amount)

    @tools.read("Get Selected Channel")
    def channel_selected() -> dict[str, Any]:
        """Return the selected channel's global index and name, or null values if none is selected."""
        return get_client().call("channels.selected")

    @tools.write("Select Channel", idempotent=True)
    def channel_select(index: int, exclusive: bool = True) -> dict[str, Any]:
        """Select channel (exclusive=True deselects others)."""
        return get_client().call("channels.select", index=index, exclusive=exclusive)

    @tools.write("Set Channel Volume", idempotent=True)
    def channel_set_volume(index: int, volume: float) -> dict[str, Any]:
        """Set channel volume (0.0..1.0, where 0.78 ≈ 0 dB)."""
        return get_client().call("channels.setVolume", index=index, volume=volume)

    @tools.write("Set Channel Pan", idempotent=True)
    def channel_set_pan(index: int, pan: float) -> dict[str, Any]:
        """Set channel pan (-1.0..1.0)."""
        return get_client().call("channels.setPan", index=index, pan=pan)

    @tools.write("Set Channel Pitch", idempotent=True)
    def channel_set_pitch(index: int, semitones: float) -> dict[str, Any]:
        """Set channel pitch offset in semitones, subject to the channel's configured pitch range."""
        return get_client().call("channels.setPitch", index=index, semitones=semitones)

    @tools.write("Mute or Unmute Channel")
    def channel_mute(index: int, muted: Optional[bool] = None) -> dict[str, Any]:
        """Mute / unmute / toggle (None = toggle)."""
        return get_client().call("channels.mute", index=index, muted=muted)

    @tools.write("Solo or Unsolo Channel")
    def channel_solo(index: int, solo: Optional[bool] = None) -> dict[str, Any]:
        """Solo / unsolo / toggle (None = toggle)."""
        return get_client().call("channels.solo", index=index, solo=solo)

    @tools.write("Rename Channel", idempotent=True)
    def channel_set_name(index: int, name: str) -> dict[str, Any]:
        """Rename a channel."""
        return get_client().call("channels.setName", index=index, name=name)

    @tools.write("Set Channel Color", idempotent=True)
    def channel_set_color(index: int, color: str) -> dict[str, Any]:
        """Set channel color. Accepts '#RRGGBB' or 'rgb(r,g,b)'."""
        return get_client().call("channels.setColor", index=index, color=color)

    @tools.write("Route Channel to Mixer", idempotent=True)
    def channel_route_to_mixer(index: int, mixer_track: int) -> dict[str, Any]:
        """Route a channel to a given mixer insert track."""
        return get_client().call("channels.routeToMixer", index=index, mixer_track=mixer_track)

    @tools.write("Audition Channel Note")
    def channel_trigger_note(index: int, note: int, velocity: int = 100, duration_ms: int = 0) -> dict[str, Any]:
        """Send a note-on for a 0-based global channel. Pitch and velocity use 0–127.

        duration_ms is reserved for timed playback. The current bridge sends only note-on.
        """
        return get_client().call("channels.triggerNote",
                                 index=index, note=note, velocity=velocity, duration_ms=duration_ms)

    @tools.read("Read Sequencer Step")
    def channel_get_grid_bit(index: int, position: int) -> dict[str, Any]:
        """Read a single step-sequencer bit (position is 0-based step within the pattern)."""
        return get_client().call("channels.getGridBit", index=index, position=position)

    @tools.write("Set Sequencer Step", destructive=True, idempotent=True)
    def channel_set_grid_bit(index: int, position: int, value: bool) -> dict[str, Any]:
        """Enable or clear a 0-based step in the selected pattern for a global channel index."""
        return get_client().call("channels.setGridBit", index=index, position=position, value=value)

    @tools.read("Read Step Sequence")
    def channel_get_step_sequence(index: int, pattern: int | None = None) -> dict[str, Any]:
        """Read 0/1 step values, length and 1-based pattern index. Omit pattern to read the selected pattern."""
        return get_client().call("channels.getStepSequence", index=index, pattern=pattern)

    @tools.write("Overwrite Step Sequence", destructive=True, idempotent=True)
    def channel_set_step_sequence(index: int, steps: list[int], pattern: int | None = None) -> dict[str, Any]:
        """Overwrite steps from index 0 with the given 0/1 values. Later steps stay as they were. Pattern indices start at 1."""
        return get_client().call("channels.setStepSequence", index=index, steps=steps, pattern=pattern)

    @tools.write("Clear Step Sequence", destructive=True, idempotent=True)
    def channel_clear_step_sequence(index: int, pattern: int | None = None) -> dict[str, Any]:
        """Clear all step-sequencer bits for the channel (in the current or specified pattern)."""
        return get_client().call("channels.clearStepSequence", index=index, pattern=pattern)

    @tools.write("Quantize Channel Note Starts", destructive=True, idempotent=True)
    def channel_quick_quantize(index: int) -> dict[str, Any]:
        """Select the channel and snap note starts in the current pattern using FL Studio's quick quantize."""
        return get_client().call("channels.quickQuantize", index=index)
