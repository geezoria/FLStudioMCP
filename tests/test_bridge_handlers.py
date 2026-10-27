"""Exercise the FL-side bridge handlers against the official API stubs.

When `fl-studio-api-stubs` is installed (it is part of the `dev` extra), the
bridge's FL-only imports resolve to the stub modules, so we can call its
handlers end-to-end offline. The stubs return default values, so this is a
*signature / arity* smoke test — it is exactly what catches mistakes like
`quickQuantize()` (missing required arg) or passing `useGlobalIndex` into a
`pickupMode` slot, which compile fine but fail at runtime in FL.

If the stubs are not installed, the whole module is skipped.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

pytest.importorskip("channels", reason="fl-studio-api-stubs not installed")


def _load_bridge():
    p = pathlib.Path(__file__).resolve().parents[1] / "fl_bridge" / "device_FLStudioMCP.py"
    spec = importlib.util.spec_from_file_location("flmcp_device_bridge_handlers", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bridge = _load_bridge()


# (action, params) pairs covering every handler touched in the 0.2.0 fixes.
CASES = [
    ("meta.ping", {}),
    ("transport.status", {}),
    ("transport.setTempo", {"bpm": 128}),
    ("channels.setVolume", {"index": 0, "volume": 0.8}),
    ("channels.setPan", {"index": 0, "pan": 0.2}),
    ("channels.setPitch", {"index": 0, "semitones": 12}),
    ("channels.mute", {"index": 0, "muted": True}),
    ("channels.mute", {"index": 0}),                       # toggle path
    ("channels.solo", {"index": 0, "solo": True}),
    ("channels.solo", {"index": 0}),                       # toggle path
    ("channels.info", {"index": 0}),
    ("channels.all", {}),
    ("channels.getGridBit", {"index": 0, "position": 0}),
    ("channels.setGridBit", {"index": 0, "position": 0, "value": True}),
    ("channels.getStepSequence", {"index": 0}),
    ("channels.setStepSequence", {"index": 0, "steps": [1, 0, 1, 0]}),
    ("channels.clearStepSequence", {"index": 0}),
    ("channels.quickQuantize", {"index": 0}),
    ("mixer.arm", {"track": 1, "armed": True}),
    ("mixer.arm", {"track": 1}),                            # toggle path
    ("mixer.setVolume", {"track": 1, "volume": 0.8}),
    ("mixer.trackInfo", {"track": 1}),
    ("mixer.peaks", {"track": 1}),
    ("mixer.allTracks", {}),
    ("mixer.getEQ", {"track": 1}),
    ("mixer.setEQBand", {"track": 1, "band": 0, "gain": 0.5, "frequency": 0.3}),
    ("playlist.muteTrack", {"track": 0, "muted": True}),
    ("playlist.soloTrack", {"track": 0, "solo": True}),
    ("playlist.trackInfo", {"track": 0}),
    ("playlist.allTracks", {}),
    ("playlist.refresh", {}),
    ("plugins.setParam", {"index": 0, "param": 0, "value": 0.5}),
    ("plugins.getParam", {"index": 0, "param": 0}),
    ("plugins.params", {"index": 0}),
    ("plugins.setPreset", {"index": 0, "preset": 1}),
    ("plugins.showEditor", {"index": 0, "show": True}),
    ("ui.focusedWindow", {}),
    ("ui.windowStatus", {}),
    ("transport.togglePunch", {}),
    ("transport.toggleOverdub", {}),
    ("transport.toggleLoopRecording", {}),
    ("transport.jumpMarker", {"direction": 1}),
    ("project.metadata", {}),
    ("project.version", {}),
    ("automation.recordTempo", {"points": [{"time_bars": 0, "bpm": 120}]}),
    ("automation.recordChannelVolume", {"channel": 0, "points": [{"time_bars": 0, "value": 0.5}]}),
    ("automation.recordChannelPan", {"channel": 0, "points": [{"time_bars": 0, "value": 0.0}]}),
    ("automation.recordMixerVolume", {"track": 1, "points": [{"time_bars": 0, "value": 0.5}]}),
    ("automation.recordPluginParam", {"channel": 0, "param": 0, "points": [{"time_bars": 0, "value": 0.5}]}),
]


@pytest.mark.parametrize("action,params", CASES, ids=[c[0] for c in CASES])
def test_handler_runs_without_error(action, params):
    result = bridge._execute(action, params)
    assert isinstance(result, dict)


def test_channel_info_reports_normalized_volume_field():
    info = bridge._execute("channels.info", {"index": 0})
    # volume must be the normalized field (0..1), with dB reported separately.
    assert "volume" in info and "volume_db" in info
    assert "pitch_semitones" in info and "pitch_cents" in info


def test_set_preset_degrades_when_unavailable():
    # Stubs (and real FL) have no plugins.setPreset -> structured error, no crash.
    r = bridge._execute("plugins.setPreset", {"index": 0, "preset": 2})
    assert isinstance(r, dict)
    if not hasattr(bridge.plugins, "setPreset"):
        assert r.get("ok") is False


def test_automation_is_async_non_blocking():
    r = bridge._execute(
        "automation.recordTempo",
        {"points": [{"time_bars": 0, "bpm": 120}, {"time_bars": 4, "bpm": 130}]},
    )
    assert r.get("mode") == "async"
    assert r.get("scheduled") == 2
    # Draining the scheduler must not raise.
    bridge._process_automation()
