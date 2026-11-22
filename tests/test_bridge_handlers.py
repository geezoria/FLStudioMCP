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


class FakeTransport:
    """Record/play toggles that report their state, like FL's transport module."""

    def __init__(self):
        self.playing = 0
        self.recording = 0
        self.calls = []

    def isPlaying(self):
        return self.playing

    def isRecording(self):
        return self.recording

    def start(self):
        self.calls.append("start")
        self.playing ^= 1

    def stop(self):
        self.calls.append("stop")
        self.playing = 0

    def record(self):
        self.calls.append("record")
        self.recording ^= 1


@pytest.fixture
def fake_automation(monkeypatch):
    fake = FakeTransport()
    clock = {"t": 1000.0}
    applied = []
    prompt = {"open": False}
    monkeypatch.setattr(bridge, "transport", fake)
    monkeypatch.setattr(bridge.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(bridge, "_rec_tempo", lambda bpm: applied.append(bpm))
    monkeypatch.setattr(bridge, "_song_transport", lambda: None)
    monkeypatch.setattr(bridge, "_record_prompt_open", lambda: prompt["open"])
    monkeypatch.setattr(bridge, "_seconds_per_bar", lambda: 2.0)
    bridge._auto_jobs.clear()
    bridge._set_auto_state(None)

    def tick(dt=0.0):
        clock["t"] += dt
        bridge._process_automation()

    yield fake, applied, prompt, tick
    bridge._auto_jobs.clear()
    bridge._set_auto_state(None)


def _record_tempo(points):
    r = bridge._execute("automation.recordTempo", {"points": points})
    bridge._process_automation()   # the command's own tick
    return r


def test_automation_starts_playback_after_arming_and_times_from_there(fake_automation):
    fake, applied, _, tick = fake_automation
    r = _record_tempo([{"time_bars": 0, "bpm": 100}, {"time_bars": 1, "bpm": 140}])
    assert r["mode"] == "async" and r["start_timeout_sec"] > 0
    # Armed, but starting in the same callback would cancel the arm in FL.
    assert fake.calls == ["record"] and applied == []
    tick(bridge.AUTO_ARM_SETTLE + 0.01)
    assert fake.calls == ["record", "start"]
    tick(0.02)                      # playback confirmed: time 0 starts here
    assert applied == [100]
    tick(1.0)
    assert applied == [100]
    tick(1.0)                       # 1 bar = 2 s
    assert applied == [100, 140]
    assert fake.calls == ["record", "start", "stop", "record"]
    assert (fake.playing, fake.recording) == (0, 0)
    assert bridge._auto_state is None


def test_automation_waits_for_record_prompt(fake_automation):
    fake, applied, prompt, tick = fake_automation
    _record_tempo([{"time_bars": 0, "bpm": 100}])
    prompt["open"] = True           # FL asks "What would you like to record?"
    tick(1.0)
    tick(1.0)
    assert fake.calls == ["record"]    # start() now would close the prompt and cancel
    prompt["open"] = False          # answered
    tick(0.02)
    tick(0.02)
    assert applied == [100]


def test_automation_aborts_when_prompt_is_cancelled(fake_automation):
    fake, applied, _, tick = fake_automation
    _record_tempo([{"time_bars": 0, "bpm": 100}])
    fake.recording = 0              # prompt closed with X: Record never armed
    tick(bridge.AUTO_ARM_SETTLE + 0.01)
    assert applied == [] and "start" not in fake.calls
    assert bridge._auto_state is None
    assert fake.calls == ["record"]    # never toggles Record back on


def test_automation_retries_unsafe_point(fake_automation, monkeypatch):
    fake, applied, _, tick = fake_automation
    busy = {"n": 2}

    def apply(bpm):
        if busy["n"]:
            busy["n"] -= 1
            raise RuntimeError("Operation unsafe at current time")
        applied.append(bpm)

    monkeypatch.setattr(bridge, "_rec_tempo", apply)
    _record_tempo([{"time_bars": 0, "bpm": 100}])
    tick(bridge.AUTO_ARM_SETTLE + 0.01)
    tick(0.02)
    tick(0.02)
    tick(0.02)
    assert applied == [100]


def test_automation_does_not_rearm_when_already_recording(fake_automation):
    fake, _, _, tick = fake_automation
    fake.recording = 1
    fake.playing = 1
    _record_tempo([{"time_bars": 0, "bpm": 100}])
    tick(bridge.AUTO_ARM_SETTLE + 0.01)
    tick(0.02)
    assert "record" not in fake.calls[:1] and "start" not in fake.calls


def test_transport_refocuses_song_when_plugin_has_focus(monkeypatch):
    focus = {"plugin": True}
    calls = []
    plugin_wids = {getattr(bridge.midi, n) for n in ("widPlugin", "widPluginEffect", "widPluginGenerator")
                   if hasattr(bridge.midi, n)}
    monkeypatch.setattr(bridge.ui, "getFocused", lambda wid: int(focus["plugin"] and wid in plugin_wids))
    monkeypatch.setattr(bridge.ui, "getFocusedPluginName", lambda: "Edison")

    def set_focused(wid):
        calls.append(("focus", wid))
        focus["plugin"] = False

    monkeypatch.setattr(bridge.ui, "setFocused", set_focused)
    monkeypatch.setattr(bridge.transport, "stop", lambda: calls.append("stop"))
    r = bridge._execute("transport.stop", {})
    assert r == {"stopped": True, "refocused_from": "Edison"}
    assert calls == [("focus", bridge.midi.widPlaylist), "stop"]
    # Nothing focused: no refocus, no extra key.
    calls.clear()
    assert bridge._execute("transport.stop", {}) == {"stopped": True}
    assert calls == ["stop"]


# ---- FL 2026 (API 39+) features: gated on hasattr, faked here ---------------

def _fake_patterns(monkeypatch, used, current=1, top=999):
    """Make `used` (1-based) the patterns with content; the stubs say all are default."""
    monkeypatch.setattr(bridge.patterns, "patternMax", lambda: top)
    monkeypatch.setattr(bridge.patterns, "patternCount", lambda: 0)  # FL's count is no index bound
    monkeypatch.setattr(bridge.patterns, "isPatternDefault", lambda i: i not in used)
    monkeypatch.setattr(bridge.patterns, "getPatternName", lambda i: "Pattern %d" % i)
    monkeypatch.setattr(bridge.patterns, "patternNumber", lambda: current)


def test_pattern_indices_use_pattern_max_not_count(monkeypatch):
    _fake_patterns(monkeypatch, used={1, 2, 5}, current=7, top=10)
    assert bridge._execute("patterns.count", {}) == {"count": 3, "last_index": 5}
    listed = [p["index"] for p in bridge._execute("patterns.list", {})["patterns"]]
    assert listed == [1, 2, 5, 7]
    named = []
    monkeypatch.setattr(bridge.patterns, "setPatternName", lambda i, n: named.append((i, n)))
    assert bridge._execute("patterns.create", {"name": "Drums"})["index"] == 6
    assert named == [(6, "Drums")]
    bridge._execute("patterns.rename", {"index": 9, "name": "x"})  # empty but valid
    with pytest.raises(ValueError):
        bridge._execute("patterns.select", {"index": 11})


def test_pattern_clone_reads_new_index_and_gates_destination(monkeypatch):
    _fake_patterns(monkeypatch, used={1, 2})
    state = {"current": 1}
    monkeypatch.setattr(bridge.patterns, "patternNumber", lambda: state["current"])
    calls = []

    def clone(*args):
        calls.append(args)
        state["current"] = args[1] if len(args) > 1 else 3

    monkeypatch.setattr(bridge.patterns, "clonePattern", clone)
    r = bridge._execute("patterns.clone", {"index": 1})
    assert calls == [(1,)] and r["new_index"] == 3
    monkeypatch.setattr(bridge.general, "getVersion", lambda: 40)
    assert bridge._execute("patterns.clone", {"index": 1, "dest_index": 8})["ok"] is False
    monkeypatch.setattr(bridge.general, "getVersion", lambda: 45)
    assert bridge._execute("patterns.clone", {"index": 1, "dest_index": 8})["new_index"] == 8
    assert calls[-1] == (1, 8)


def test_pattern_length_uses_steps_and_degrades(monkeypatch):
    _fake_patterns(monkeypatch, used={1})
    monkeypatch.delattr(bridge.patterns, "setPatternLength", raising=False)
    r = bridge._execute("patterns.setLength", {"index": 2, "bars": 2})
    assert r["ok"] is False and "2026" in r["hint"]

    calls = []
    monkeypatch.setattr(bridge.patterns, "setPatternLength",
                        lambda i, steps: calls.append((i, steps)), raising=False)
    monkeypatch.setattr(bridge.patterns, "getPatternLength", lambda i: 48, raising=False)
    monkeypatch.setattr(bridge.general, "getRecPPQ", lambda: 96)
    monkeypatch.setattr(bridge.general, "getRecPPB", lambda: 96 * 3, raising=False)  # 3/4
    r = bridge._execute("patterns.setLength", {"index": 2, "bars": 4})
    assert calls == [(2, 48)]
    assert r == {"ok": True, "index": 2, "requested_steps": 48, "length_steps": 48,
                 "length_beats": 12.0, "length_bars": 4.0, "steps_per_bar": 12}
    bridge._execute("patterns.setLength", {"index": 1, "beats": 7})
    assert calls[-1] == (1, 28)
    bridge._execute("patterns.setLength", {"index": 1, "steps": 5})
    assert calls[-1] == (1, 5)
    with pytest.raises(ValueError):
        bridge._execute("patterns.setLength", {"index": 1000, "bars": 1})


def test_pattern_clear_and_picker(monkeypatch):
    _fake_patterns(monkeypatch, used={1, 2})
    cleared, shown = [], []
    monkeypatch.setattr(bridge.patterns, "clearPattern", cleared.append, raising=False)
    monkeypatch.setattr(bridge.ui, "showPicker", lambda m, c: shown.append((m, c)), raising=False)
    assert bridge._execute("patterns.clear", {"index": 2})["ok"] is True
    assert cleared == [2]
    assert bridge._execute("ui.showPicker", {"mode": "project"})["shown"] == "project"
    assert shown == [(1, -1)]
    with pytest.raises(ValueError):
        bridge._execute("ui.showPicker", {"mode": "browser"})


def test_channel_rack_swing_clamps_and_degrades(monkeypatch):
    monkeypatch.delattr(bridge.channels, "getSwing", raising=False)
    monkeypatch.delattr(bridge.channels, "setSwing", raising=False)
    assert bridge._execute("channels.getSwing", {})["ok"] is False
    state = {"swing": 0.0}
    monkeypatch.setattr(bridge.channels, "getSwing", lambda: state["swing"], raising=False)
    monkeypatch.setattr(bridge.channels, "setSwing", lambda v: state.update(swing=v), raising=False)
    assert bridge._execute("channels.setSwing", {"amount": 1.7}) == {"ok": True, "swing": 1.0}
    assert bridge._execute("channels.getSwing", {}) == {"ok": True, "swing": 1.0}


def test_metadata_includes_project_info_when_available(monkeypatch):
    monkeypatch.setattr(bridge.general, "getProjectTitle", lambda: "Night Drive", raising=False)
    monkeypatch.setattr(bridge.mixer, "getTrackCount", lambda: 125, raising=False)
    assert bridge._execute("project.metadata", {})["title"] == "Night Drive"
    assert bridge._execute("mixer.count", {})["insert_count"] == 125
