"""Mixer: track volume/pan/mute/solo/arm/name/color/stereo-sep/routes/FX slots."""

from __future__ import annotations

import math
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from .. import mix_doctor
from ..bridge_client import get_client
from ._meta import ToolRegistrar

PEAK_SAMPLE_INTERVAL = 0.05
METER_SETTLE = 0.3  # after a start, the meters still hold the last level heard


def _watch_peaks(seconds: float, play: bool) -> tuple[dict[int, float], dict[int, str], int, bool]:
    """Sample mixer.peaks for `seconds`, holding each track's loudest linear peak."""
    seconds = min(120.0, max(1.0, float(seconds)))
    client = get_client()
    started = False
    if play and not client.call("transport.status").get("is_playing"):
        client.call("transport.start")
        started = True
    held: dict[int, float] = {}
    names: dict[int, str] = {}
    samples = 0
    begin = time.monotonic()
    try:
        while time.monotonic() - begin < seconds:
            result = client.call("mixer.peaks", include_silent=False)
            if not started or time.monotonic() - begin >= METER_SETTLE:
                samples += 1
                for t in result.get("tracks", []):
                    level = max(float(t.get("left") or 0), float(t.get("right") or 0))
                    held[t["track"]] = max(held.get(t["track"], 0.0), level)
                    names[t["track"]] = t.get("name")
            time.sleep(PEAK_SAMPLE_INTERVAL)
    finally:
        if started:
            client.call("transport.stop")
    return held, names, samples, started


def _stage_preset(preset: Path, data_system: str) -> Path | None:
    """Copy a preset to the temp folder when that is on FL's drive: FL loads presets only
    through a path relative to its Data/System folder, and none reaches another drive."""
    staging = Path(tempfile.gettempdir()) / "fLMCP presets"
    if not data_system or Path(data_system).drive.lower() != staging.drive.lower():
        return None
    staging.mkdir(parents=True, exist_ok=True)
    target = staging / preset.name
    shutil.copyfile(preset, target)
    return target


def register(tools: ToolRegistrar) -> None:
    @tools.read("Count Mixer Tracks")
    def mixer_count() -> dict[str, Any]:
        """Number of mixer tracks, including Master at index 0 (`count`); FL 2025.1+ also
        reports `insert_count`, the inserts without Master and the hidden Current track."""
        return get_client().call("mixer.count")

    @tools.read("Get Mixer Track Details")
    def mixer_track_info(track: int) -> dict[str, Any]:
        """Read gain, pan, mute/solo/arm, stereo separation and FX slots for a mixer track. Track 0 is Master."""
        return get_client().call("mixer.trackInfo", track=track)

    @tools.read("List Mixer Tracks")
    def mixer_all_tracks(include_empty: bool = False) -> dict[str, Any]:
        """List mixer tracks and fader settings. Track 0 is Master. Set include_empty to include unnamed and default-named inserts."""
        return get_client().call("mixer.allTracks", include_empty=include_empty)

    @tools.write("Set Mixer Track Volume", idempotent=True)
    def mixer_set_volume(track: int, volume: float) -> dict[str, Any]:
        """Set mixer track volume (0.0..1.0)."""
        return get_client().call("mixer.setVolume", track=track, volume=volume)

    @tools.write("Set Mixer Track Pan", idempotent=True)
    def mixer_set_pan(track: int, pan: float) -> dict[str, Any]:
        """Set mixer track pan (-1.0..1.0)."""
        return get_client().call("mixer.setPan", track=track, pan=pan)

    @tools.write("Mute or Unmute Mixer Track")
    def mixer_mute(track: int, muted: Optional[bool] = None) -> dict[str, Any]:
        """Mute / unmute / toggle (None = toggle)."""
        return get_client().call("mixer.mute", track=track, muted=muted)

    @tools.write("Solo or Unsolo Mixer Track")
    def mixer_solo(track: int, solo: Optional[bool] = None) -> dict[str, Any]:
        """Solo / unsolo / toggle."""
        return get_client().call("mixer.solo", track=track, solo=solo)

    @tools.write("Arm or Disarm Mixer Track")
    def mixer_arm(track: int, armed: Optional[bool] = None) -> dict[str, Any]:
        """Set an insert's recording arm state. Omit armed to toggle."""
        return get_client().call("mixer.arm", track=track, armed=armed)

    @tools.write("Rename Mixer Track", idempotent=True)
    def mixer_set_name(track: int, name: str) -> dict[str, Any]:
        """Rename a mixer track."""
        return get_client().call("mixer.setName", track=track, name=name)

    @tools.write("Set Mixer Track Color", idempotent=True)
    def mixer_set_color(track: int, color: str) -> dict[str, Any]:
        """Set mixer track color ('#RRGGBB' or 'rgb(r,g,b)')."""
        return get_client().call("mixer.setColor", track=track, color=color)

    @tools.write("Set Mixer Stereo Separation", idempotent=True)
    def mixer_set_stereo_separation(track: int, separation: float) -> dict[str, Any]:
        """Set mixer track stereo separation (-1.0..1.0)."""
        return get_client().call("mixer.setStereoSep", track=track, separation=separation)

    @tools.write("Set Mixer Send Level", idempotent=True)
    def mixer_set_send_level(src_track: int, dst_track: int, level: float) -> dict[str, Any]:
        """Set send level from one mixer track to another (0..1)."""
        return get_client().call("mixer.setSendLevel", src_track=src_track, dst_track=dst_track, level=level)

    @tools.write("Enable or Disable Mixer Route", idempotent=True)
    def mixer_route(src_track: int, dst_track: int, enabled: bool = True) -> dict[str, Any]:
        """Enable or disable a mixer route (send) between two tracks."""
        return get_client().call("mixer.route", src_track=src_track, dst_track=dst_track, enabled=enabled)

    @tools.read("List Mixer FX Slots")
    def mixer_fx_slots(track: int) -> dict[str, Any]:
        """List the 10 FX slots (0-9) of a mixer track: plugin name, plugin ID (-1 = empty) and, for
        loaded slots, `enabled` (on/off) and `mix` (dry/wet, 0..1). `slots_enabled` is the track's
        switch that bypasses every slot at once."""
        return get_client().call("mixer.fxSlots", track=track)

    @tools.write("Set Mixer FX Slot", idempotent=True)
    def mixer_set_slot(
        track: int, slot: int, enabled: Optional[bool] = None, mix: Optional[float] = None,
    ) -> dict[str, Any]:
        """Turn a loaded FX slot (0-9) on or off and/or set its Mix (dry/wet) knob, 0.0 = dry..1.0 = wet.
        Leave an argument out to keep it. Returns the slot's state as FL reports it."""
        return get_client().call("mixer.setSlot", track=track, slot=slot, enabled=enabled, mix=mix)

    @tools.write("Enable or Bypass All FX Slots", idempotent=True)
    def mixer_set_slots_enabled(track: int, enabled: bool = True) -> dict[str, Any]:
        """Switch all 10 FX slots of a mixer track on (enabled=true) or bypass them (false)."""
        return get_client().call("mixer.setSlotsEnabled", track=track, enabled=enabled)

    @tools.write("Load Effect into Mixer Track")
    def mixer_load_effect(track: int, plugin: str) -> dict[str, Any]:
        """Load an effect plugin into the next free FX slot of a mixer track.

        `plugin` is a name from FL's Plugin database (see `plugin_search`, e.g. "Fruity Reeverb 2",
        "Fruity Limiter", or a scanned VST), or an absolute path to an .fst preset. Returns the slot it
        landed in; then use `plugin_params` / `plugin_set_param` with location="mixer" to set it up."""
        from .. import paths
        from ..plugin_database import resolve

        entry = resolve(plugin, "effect")
        install = paths.fl_install_dir()
        client = get_client()
        fl_dir = str(install) if install else None
        result = client.call("mixer.loadEffect", track=track, preset=str(entry.path), fl_dir=fl_dir)
        if result.get("other_drive"):
            staged = _stage_preset(entry.path, str(result.get("data_system") or ""))
            if staged is not None:
                result = client.call("mixer.loadEffect", track=track, preset=str(staged), fl_dir=fl_dir)
        result.pop("other_drive", None)
        result.pop("data_system", None)
        result["plugin"] = entry.name
        return result

    @tools.read("Read Mixer Peak Meters")
    def mixer_peaks(track: Optional[int] = None, include_silent: bool = False) -> dict[str, Any]:
        """Current output peak of mixer tracks while FL plays: `left`/`right` are linear (1.0 = 0 dBFS,
        above 1.0 clips) and `peak_db` is the louder side in dBFS. With no `track`, lists the tracks
        that carry signal (all of them with include_silent). Read them while FL plays: after a stop
        the meters hold their last level for a moment."""
        result = get_client().call("mixer.peaks", track=track, include_silent=include_silent)
        for t in result.get("tracks", []):
            level = max(float(t.get("left") or 0), float(t.get("right") or 0))
            t["peak_db"] = round(20 * math.log10(level), 1) if level > 0 else None
            t["clipping"] = level > 1.0
        return result

    @tools.write("Set Mixer Track Volume in dB", idempotent=True)
    def mixer_set_volume_db(track: int, db: float) -> dict[str, Any]:
        """Set a mixer fader to a level in dB (0 = unity; FL's faders go from -inf to about +5.6 dB).
        Track 0 is Master. Returns the track with `volume_db` as FL now shows it, `clamped` (above
        the fader's top) and `verified` (FL shows the requested level within 0.05 dB)."""
        client = get_client()
        result = client.call("mixer.setVolumeDb", track=track, db=db)
        info = client.call("mixer.trackInfo", track=result["track"])
        shown = info.get("volume_db")
        expected = result.get("expected_db")
        info["clamped"] = result.get("clamped", False)
        info["verified"] = isinstance(shown, (int, float)) and (
            shown == float("-inf") if expected is None else abs(shown - expected) <= 0.05)
        if not info["verified"] and isinstance(shown, (int, float)) and db < -60 and shown < -60:
            info["note"] = "FL's fader moves in steps too coarse to hit levels below about -60 dB exactly."
        return info

    @tools.read("Watch Mixer Peaks During Playback")
    def mixer_peak_watch(seconds: float = 8.0, play: bool = True) -> dict[str, Any]:
        """Listen for `seconds` (1-120) and report the loudest peak each mixer track reached, so
        level decisions use the loud parts of the song, not one instant. With `play` it starts
        playback if FL is stopped (and stops it again); FL plays the song or the pattern,
        whichever its transport is set to. Peaks are in dBFS; `clipping` means above 0 dBFS."""
        held, names, samples, started = _watch_peaks(seconds, play)
        tracks = [{"track": t, "name": names.get(t), "peak": round(v, 4),
                   "peak_db": mix_doctor.to_db(v), "clipping": v >= mix_doctor.CLIP}
                  for t, v in sorted(held.items(), key=lambda kv: -kv[1])]
        return {"seconds": seconds, "samples": samples, "started_playback": started, "tracks": tracks}

    @tools.read("Diagnose Mix")
    def mixer_diagnose(listen_seconds: float = 0.0) -> dict[str, Any]:
        """Review the whole mix and list concrete problems, worst first. It checks clipping and
        headroom on Master, hot inserts, inserts routed nowhere, solos left on, bypassed or
        switched-off effects, muted inserts that carry channels, silent tracks, faders pushed up and
        channels playing straight into Master. Each finding has `evidence`, a `fix`, and usually the
        `tool` and `args` that apply it; apply fixes one at a time and only with the user's OK.
        `listen_seconds` > 0 first plays and holds peaks for that long (see mixer_peak_watch); with
        0, levels come from this instant and only if FL is playing. Changes nothing itself."""
        held = None
        if listen_seconds and listen_seconds > 0:
            held, _names, _samples, _started = _watch_peaks(listen_seconds, True)
        snapshot = get_client().call("mixer.snapshot")
        result = mix_doctor.diagnose(snapshot, held)
        if not snapshot.get("routing_known"):
            result["note"] = "This FL can't read routes, so 'goes nowhere' isn't checked."
        return result

    @tools.write("Select Mixer Track", idempotent=True)
    def mixer_select(track: int) -> dict[str, Any]:
        """Focus / select a mixer track."""
        return get_client().call("mixer.select", track=track)

    @tools.read("Read Mixer EQ")
    def mixer_get_eq(track: int) -> dict[str, Any]:
        """Read the mixer track's EQ bands with gain, frequency and bandwidth values where available."""
        return get_client().call("mixer.getEQ", track=track)

    @tools.write("Set Mixer EQ Band", idempotent=True)
    def mixer_set_eq_band(track: int, band: int, gain: float | None = None, frequency: float | None = None) -> dict[str, Any]:
        """Set one band of the mixer EQ. band=0|1|2 (low/mid/high). gain -1..1, freq 0..1 (normalised)."""
        return get_client().call("mixer.setEQBand", track=track, band=band, gain=gain, frequency=frequency)

    @tools.write("Link Channel to Mixer Track", idempotent=True)
    def mixer_link_to_channel(channel: int, track: int, mode: str = "replace") -> dict[str, Any]:
        """Link a 0-based global channel to a mixer track. The legacy mode argument is accepted for compatibility."""
        return get_client().call("mixer.linkChannelToTrack", channel=channel, track=track, mode=mode)
