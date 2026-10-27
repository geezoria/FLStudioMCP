"""Mixer: track volume/pan/mute/solo/arm/name/color/stereo-sep/routes/FX slots."""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from ..bridge_client import get_client


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def mixer_count() -> dict:
        """Number of mixer tracks (incl master @ index 0)."""
        return get_client().call("mixer.count")

    @mcp.tool()
    def mixer_track_info(track: int) -> dict:
        """Detailed info on a mixer track (name, vol, pan, mute, solo, arm, color, stereosep, sends, fx slots)."""
        return get_client().call("mixer.trackInfo", track=track)

    @mcp.tool()
    def mixer_get_peaks(track: int) -> dict:
        """Read live normalized left/right VU peaks for a mixer track."""
        return get_client().call("mixer.peaks", track=track)

    @mcp.tool()
    def mixer_all_tracks(include_empty: bool = False) -> dict:
        """List of all mixer tracks."""
        return get_client().call("mixer.allTracks", include_empty=include_empty)

    @mcp.tool()
    def mixer_set_volume(track: int, volume: float) -> dict:
        """Set mixer track volume (0.0..1.0)."""
        return get_client().call("mixer.setVolume", track=track, volume=volume)

    @mcp.tool()
    def mixer_set_pan(track: int, pan: float) -> dict:
        """Set mixer track pan (-1.0..1.0)."""
        return get_client().call("mixer.setPan", track=track, pan=pan)

    @mcp.tool()
    def mixer_mute(track: int, muted: Optional[bool] = None) -> dict:
        """Mute / unmute / toggle (None = toggle)."""
        return get_client().call("mixer.mute", track=track, muted=muted)

    @mcp.tool()
    def mixer_solo(track: int, solo: Optional[bool] = None) -> dict:
        """Solo / unsolo / toggle."""
        return get_client().call("mixer.solo", track=track, solo=solo)

    @mcp.tool()
    def mixer_arm(track: int, armed: Optional[bool] = None) -> dict:
        """Arm track for recording."""
        return get_client().call("mixer.arm", track=track, armed=armed)

    @mcp.tool()
    def mixer_set_name(track: int, name: str) -> dict:
        """Rename a mixer track."""
        return get_client().call("mixer.setName", track=track, name=name)

    @mcp.tool()
    def mixer_set_color(track: int, color: str) -> dict:
        """Set mixer track color ('#RRGGBB' or 'rgb(r,g,b)')."""
        return get_client().call("mixer.setColor", track=track, color=color)

    @mcp.tool()
    def mixer_set_stereo_separation(track: int, separation: float) -> dict:
        """Set mixer track stereo separation (-1.0..1.0)."""
        return get_client().call("mixer.setStereoSep", track=track, separation=separation)

    @mcp.tool()
    def mixer_set_send_level(src_track: int, dst_track: int, level: float) -> dict:
        """Set send level from one mixer track to another (0..1)."""
        return get_client().call("mixer.setSendLevel", src_track=src_track, dst_track=dst_track, level=level)

    @mcp.tool()
    def mixer_route(src_track: int, dst_track: int, enabled: bool = True) -> dict:
        """Enable or disable a mixer route (send) between two tracks."""
        return get_client().call("mixer.route", src_track=src_track, dst_track=dst_track, enabled=enabled)

    @mcp.tool()
    def mixer_fx_slots(track: int) -> dict:
        """List plugin IDs in the 10 FX slots of a mixer track (-1 = empty)."""
        return get_client().call("mixer.fxSlots", track=track)

    @mcp.tool()
    def mixer_select(track: int) -> dict:
        """Focus / select a mixer track."""
        return get_client().call("mixer.select", track=track)

    @mcp.tool()
    def mixer_get_eq(track: int) -> dict:
        """Return 3-band mixer EQ (gains + frequencies)."""
        return get_client().call("mixer.getEQ", track=track)

    @mcp.tool()
    def mixer_set_eq_band(track: int, band: int, gain: float | None = None, frequency: float | None = None) -> dict:
        """Set one band of the mixer EQ. band=0|1|2 (low/mid/high). gain -1..1, freq 0..1 (normalised)."""
        return get_client().call("mixer.setEQBand", track=track, band=band, gain=gain, frequency=frequency)

    @mcp.tool()
    def mixer_link_to_channel(channel: int, track: int, mode: str = "replace") -> dict:
        """Link a channel to a mixer track. mode='replace'|'add'."""
        return get_client().call("mixer.linkChannelToTrack", channel=channel, track=track, mode=mode)
