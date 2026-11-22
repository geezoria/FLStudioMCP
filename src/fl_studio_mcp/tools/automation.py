"""Parameter automation via processRECEvent (records live automation clips)."""

from __future__ import annotations

from typing import Any, Literal

from ..bridge_client import get_client
from ._meta import ToolRegistrar


def register(tools: ToolRegistrar) -> None:
    @tools.write("Record Tempo Automation")
    def automation_record_tempo(points: list[dict]) -> dict[str, Any]:
        """Record an automation ramp on the master tempo.

        `points`: list of {time_bars: float, bpm: float}. MCP will sequence them live
        with the correct REC flags to create a real automation clip.

        All automation_record_* tools arm FL's Record and play the song. FL records
        whatever its recording filter allows: 'Automation' must be enabled there,
        and if 'Audio into Edison' is enabled FL also opens Edison and records audio."""
        return get_client().call("automation.recordTempo", points=points)

    @tools.write("Record Channel Volume Automation")
    def automation_record_channel_volume(channel: int, points: list[dict]) -> dict[str, Any]:
        """Record [{time_bars, value}] on a global channel with volume values from 0 to 1."""
        return get_client().call("automation.recordChannelVolume", channel=channel, points=points)

    @tools.write("Record Channel Pan Automation")
    def automation_record_channel_pan(channel: int, points: list[dict]) -> dict[str, Any]:
        """Record [{time_bars, value}] on a global channel with pan values from -1 (left) to 1 (right)."""
        return get_client().call("automation.recordChannelPan", channel=channel, points=points)

    @tools.write("Record Mixer Volume Automation")
    def automation_record_mixer_volume(track: int, points: list[dict]) -> dict[str, Any]:
        """Record [{time_bars, value}] on a mixer track with volume values from 0 to 1."""
        return get_client().call("automation.recordMixerVolume", track=track, points=points)

    @tools.write("Record Plugin Parameter Automation")
    def automation_record_plugin_param(channel: int,
                                       param: int,
                                       points: list[dict],
                                       slot: int = -1,
                                       location: Literal["channel", "mixer"] = "channel") -> dict[str, Any]:
        """Record [{time_bars, value}] for a 0-based plugin parameter using normalized values from 0 to 1."""
        return get_client().call("automation.recordPluginParam",
                                 channel=channel, param=param, slot=slot,
                                 location=location, points=points)
