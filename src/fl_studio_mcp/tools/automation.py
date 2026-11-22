"""Parameter automation via processRECEvent (records live automation clips)."""

from __future__ import annotations

from typing import Any, Literal

from .. import fl_windows
from ..bridge_client import get_client
from ._meta import ToolRegistrar


def _record(action: str, **params: Any) -> dict[str, Any]:
    result = get_client().call(action, **params)
    if isinstance(result, dict) and result.get("ok", True):
        # Arming Record may open FL's "What would you like to record?" prompt.
        # The bridge waits for it; answering it lets the recording start.
        answered = fl_windows.answer_record_prompt(fl_windows.NOTES_AND_AUTOMATION)
        if answered:
            result["record_prompt"] = (f"FL asked what to record; answered '{answered}'. "
                                       "Tick 'Don't show this in the future' in that "
                                       "prompt to skip it.")
    return result


def register(tools: ToolRegistrar) -> None:
    @tools.write("Record Tempo Automation")
    def automation_record_tempo(points: list[dict]) -> dict[str, Any]:
        """Record an automation ramp on the master tempo.

        `points`: list of {time_bars: float, bpm: float}. The values are played
        in live while FL records: in Song mode FL creates automation clips, and
        in Pattern mode it records into the current (looping) pattern, so keep
        the points within its length.

        All automation_record_* tools arm FL's Record, play the song from the
        current position and stop when the last point is written. Times count
        from when playback starts. If FL asks "What would you like to record?",
        the tool answers "Notes and automation" on Windows; elsewhere answer it in
        FL within 20 s. FL records what its recording filter allows: 'Automation'
        must be enabled there, and with 'Audio into Edison' FL also records audio."""
        return _record("automation.recordTempo", points=points)

    @tools.write("Record Channel Volume Automation")
    def automation_record_channel_volume(channel: int, points: list[dict]) -> dict[str, Any]:
        """Record [{time_bars, value}] on a global channel with volume values from 0 to 1."""
        return _record("automation.recordChannelVolume", channel=channel, points=points)

    @tools.write("Record Channel Pan Automation")
    def automation_record_channel_pan(channel: int, points: list[dict]) -> dict[str, Any]:
        """Record [{time_bars, value}] on a global channel with pan values from -1 (left) to 1 (right)."""
        return _record("automation.recordChannelPan", channel=channel, points=points)

    @tools.write("Record Mixer Volume Automation")
    def automation_record_mixer_volume(track: int, points: list[dict]) -> dict[str, Any]:
        """Record [{time_bars, value}] on a mixer track with volume values from 0 to 1."""
        return _record("automation.recordMixerVolume", track=track, points=points)

    @tools.write("Record Plugin Parameter Automation")
    def automation_record_plugin_param(channel: int,
                                       param: int,
                                       points: list[dict],
                                       slot: int = -1,
                                       location: Literal["channel", "mixer"] = "channel") -> dict[str, Any]:
        """Record [{time_bars, value}] for a 0-based plugin parameter using normalized values from 0 to 1."""
        return _record("automation.recordPluginParam",
                       channel=channel, param=param, slot=slot,
                       location=location, points=points)
