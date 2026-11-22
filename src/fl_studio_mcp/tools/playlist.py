"""Playlist: track CRUD, clip placement, markers, live mode."""

from __future__ import annotations

from typing import Any, Optional

from ..bridge_client import get_client
from ._meta import ToolRegistrar


def register(tools: ToolRegistrar) -> None:
    @tools.read("Count Playlist Tracks")
    def playlist_track_count() -> dict[str, Any]:
        """Return the number of Playlist tracks."""
        return get_client().call("playlist.trackCount")

    @tools.read("Get Playlist Track Details")
    def playlist_track_info(track: int) -> dict[str, Any]:
        """Read a Playlist track's name, color, mute/solo state and optional height. Use an index from playlist_all_tracks."""
        return get_client().call("playlist.trackInfo", track=track)

    @tools.read("List Playlist Tracks")
    def playlist_all_tracks(include_empty: bool = False) -> dict[str, Any]:
        """Return Playlist track indices and details. By default omit tracks with no name."""
        return get_client().call("playlist.allTracks", include_empty=include_empty)

    @tools.write("Rename Playlist Track", idempotent=True)
    def playlist_set_track_name(track: int, name: str) -> dict[str, Any]:
        """Rename a playlist track."""
        return get_client().call("playlist.setTrackName", track=track, name=name)

    @tools.write("Set Playlist Track Color", idempotent=True)
    def playlist_set_track_color(track: int, color: str) -> dict[str, Any]:
        """Set playlist track color ('#RRGGBB')."""
        return get_client().call("playlist.setTrackColor", track=track, color=color)

    @tools.write("Mute or Unmute Playlist Track")
    def playlist_mute_track(track: int, muted: Optional[bool] = None) -> dict[str, Any]:
        """Set a Playlist track's mute state. Omit muted to toggle."""
        return get_client().call("playlist.muteTrack", track=track, muted=muted)

    @tools.write("Solo or Unsolo Playlist Track")
    def playlist_solo_track(track: int, solo: Optional[bool] = None) -> dict[str, Any]:
        """Set a Playlist track's solo state. Omit solo to toggle."""
        return get_client().call("playlist.soloTrack", track=track, solo=solo)

    @tools.read("Check Playlist Clip Availability")
    def playlist_list_clips(track: int | None = None) -> dict[str, Any]:
        """Check clip enumeration for all tracks or one track. The FL scripting API currently returns a limitation note."""
        return get_client().call("playlist.listClips", track=track)

    @tools.write("Place Pattern Clip")
    def playlist_place_pattern(track: int, pattern: int, position_bars: float, length_bars: float | None = None) -> dict[str, Any]:
        """Request placement of a pattern at a bar offset. The public FL API currently requires manual placement."""
        return get_client().call("playlist.placePattern",
                                 track=track, pattern=pattern,
                                 position_bars=position_bars, length_bars=length_bars)

    @tools.write("Delete Playlist Clip", destructive=True)
    def playlist_delete_clip(track: int, position_bars: float) -> dict[str, Any]:
        """Request deletion of a clip at a bar offset. The public FL API currently requires manual deletion."""
        return get_client().call("playlist.deleteClip", track=track, position_bars=position_bars)

    @tools.write("Refresh Playlist Display", idempotent=True)
    def playlist_refresh() -> dict[str, Any]:
        """Force playlist repaint (use after bulk edits)."""
        return get_client().call("playlist.refresh")

    @tools.read("Check Timeline Marker Availability")
    def playlist_list_markers() -> dict[str, Any]:
        """Check timeline marker enumeration. The public FL API currently returns an availability note."""
        return get_client().call("playlist.listMarkers")

    @tools.write("Add Timeline Marker")
    def playlist_add_marker(position_bars: float, name: str = "") -> dict[str, Any]:
        """Add a named timeline marker at a bar offset from the start (four quarter notes per bar)."""
        return get_client().call("playlist.addMarker", position_bars=position_bars, name=name)

    @tools.write("Delete Timeline Marker", destructive=True)
    def playlist_delete_marker(index: int) -> dict[str, Any]:
        """Request removal by timeline marker index. The public FL API currently requires manual deletion."""
        return get_client().call("playlist.deleteMarker", index=index)
