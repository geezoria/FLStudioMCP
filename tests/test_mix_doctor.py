from __future__ import annotations

from fl_studio_mcp import mix_doctor
from fl_studio_mcp.tools import mixer as mixer_tools


def _track(index, name, **kw):
    t = {"index": index, "name": name, "volume_db": 0.0, "is_muted": False, "is_solo": False,
         "peak": 0.0, "sends": [0] if index else None, "slots": [], "slots_enabled": True}
    t.update(kw)
    return t


def _snapshot(*tracks, channels=(), playing=False):
    return {"tracks": list(tracks), "channels": list(channels), "playing": playing, "routing_known": True}


def _problems(result):
    return [f["problem"] for f in result["findings"]]


def test_clean_mix_has_no_findings():
    snap = _snapshot(_track(0, "Master"), _track(1, "Kick"),
                     channels=[{"index": 0, "name": "Kick", "fx_track": 1, "muted": False}])
    result = mix_doctor.diagnose(snap, {0: 0.5, 1: 0.4})
    assert result["findings"] == []
    assert result["measured"] == "held peaks over playback"
    assert [lv["track"] for lv in result["levels"]] == [0, 1]


def test_clipping_master_points_at_the_loudest_source():
    snap = _snapshot(_track(0, "Master"), _track(1, "Kick", volume_db=-2.0), _track(2, "Bass"),
                     channels=[{"index": 0, "name": "Kick", "fx_track": 1},
                               {"index": 1, "name": "Bass", "fx_track": 2}])
    result = mix_doctor.diagnose(snap, {0: 1.41, 1: 1.2, 2: 0.3})
    first = result["findings"][0]
    assert first["severity"] == "high" and first["problem"] == "Master clips"
    assert first["tool"] == "mixer_set_volume_db" and first["args"]["track"] == 1
    assert first["args"]["db"] < -2.0
    assert "Insert 1 (Kick) runs hot" in _problems(result)
    assert result["summary"]["high"] == 2


def test_little_headroom_is_medium():
    result = mix_doctor.diagnose(_snapshot(_track(0, "Master")), {0: 0.95})
    assert [(f["severity"], f["problem"]) for f in result["findings"]] == [
        ("medium", "Little headroom on Master")]


def test_routing_solo_bypass_mute_and_faders():
    snap = _snapshot(
        _track(0, "Master"),
        _track(1, "Lead", sends=[]),
        _track(2, "Pad", is_solo=True),
        _track(3, "Vox", slots=[{"slot": 0, "name": "Fruity Limiter", "enabled": False}]),
        _track(4, "FX", slots=[{"slot": 0, "name": "Reeverb", "enabled": True}], slots_enabled=False),
        _track(5, "Hat", is_muted=True),
        _track(6, "Sub", volume_db=6.0),
        channels=[{"index": 0, "name": "Lead", "fx_track": 1}, {"index": 1, "name": "Hat", "fx_track": 5},
                  {"index": 2, "name": "Clap", "fx_track": 0}])
    result = mix_doctor.diagnose(snap)
    problems = _problems(result)
    assert "Insert 1 (Lead) goes nowhere" in problems
    assert "Insert 2 (Pad) is soloed" in problems
    assert "Fruity Limiter on Insert 3 (Vox) is bypassed" in problems
    assert "Effects of Insert 4 (FX) are switched off" in problems
    assert "Insert 5 (Hat) is muted" in problems
    assert "Fader of Insert 6 (Sub) is pushed up" in problems
    assert "Channels play straight into Master" in problems
    # Not playing and nothing held: no level findings, and severities come sorted.
    assert result["measured"] == "no levels: FL wasn't playing"
    order = [mix_doctor.SEVERITY_ORDER[f["severity"]] for f in result["findings"]]
    assert order == sorted(order)
    nowhere = next(f for f in result["findings"] if f["problem"].endswith("goes nowhere"))
    assert nowhere["args"] == {"src_track": 1, "dst_track": 0, "enabled": True}


def test_silent_fed_track_is_reported_only_when_heard():
    snap = _snapshot(_track(0, "Master"), _track(1, "Kick"),
                     channels=[{"index": 0, "name": "Kick", "fx_track": 1}])
    assert mix_doctor.diagnose(snap)["findings"] == []
    assert _problems(mix_doctor.diagnose(snap, {0: 0.2})) == ["No signal on Insert 1 (Kick)"]


def test_default_track_names_are_not_repeated():
    snap = _snapshot(_track(0, "Master"), _track(2, "Insert 2", sends=[]),
                     channels=[{"index": 0, "name": "Kick", "fx_track": 2}])
    assert _problems(mix_doctor.diagnose(snap)) == ["Insert 2 goes nowhere"]


def test_to_db():
    assert mix_doctor.to_db(1.0) == 0.0
    assert mix_doctor.to_db(0.5) == -6.0
    assert mix_doctor.to_db(0) is None and mix_doctor.to_db(None) is None


class PeakClient:
    def __init__(self, playing=False):
        self.playing = playing
        self.calls = []
        self.n = 0

    def call(self, action, **params):
        self.calls.append(action)
        if action == "transport.status":
            return {"is_playing": self.playing}
        if action == "mixer.peaks":
            self.n += 1
            return {"tracks": [{"track": 0, "name": "Master", "left": 0.1 * self.n, "right": 0.05},
                               {"track": 2, "name": "Bass", "left": 0.2, "right": 0.3}]}
        return {}


def test_watch_peaks_holds_the_max_and_stops_what_it_started(monkeypatch):
    client = PeakClient()
    monkeypatch.setattr(mixer_tools, "get_client", lambda: client)
    monkeypatch.setattr(mixer_tools, "METER_SETTLE", 0.0)
    monkeypatch.setattr(mixer_tools, "PEAK_SAMPLE_INTERVAL", 0.0)
    clock = iter([0.0] + [0.1 * i for i in range(1, 1000)])
    monkeypatch.setattr(mixer_tools.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(mixer_tools.time, "sleep", lambda s: None)
    held, names, samples, started = mixer_tools._watch_peaks(1.0, True)
    assert started is True and samples > 0
    assert client.calls[:2] == ["transport.status", "transport.start"]
    assert client.calls[-1] == "transport.stop"
    assert held[2] == 0.3 and abs(held[0] - 0.1 * client.n) < 1e-9
    assert names == {0: "Master", 2: "Bass"}


def test_watch_peaks_leaves_running_playback_alone(monkeypatch):
    client = PeakClient(playing=True)
    monkeypatch.setattr(mixer_tools, "get_client", lambda: client)
    monkeypatch.setattr(mixer_tools, "PEAK_SAMPLE_INTERVAL", 0.0)
    clock = iter([0.0] + [0.5 * i for i in range(1, 100)])
    monkeypatch.setattr(mixer_tools.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(mixer_tools.time, "sleep", lambda s: None)
    _held, _names, _samples, started = mixer_tools._watch_peaks(1.0, True)
    assert started is False
    assert "transport.start" not in client.calls and "transport.stop" not in client.calls


class LoadClient:
    def __init__(self):
        self.calls = []

    def call(self, action, **params):
        self.calls.append((action, params))
        if action == "mixer.loadEffect" and len(self.calls) == 1:
            return {"ok": False, "track": params["track"], "other_drive": True,
                    "data_system": self.data_system, "error": "another drive"}
        if action == "mixer.loadEffect":
            return {"ok": True, "track": params["track"], "loaded": [{"slot": 0, "name": "Fruity Limiter"}]}
        if action == "mixer.setVolumeDb":
            return {"track": params["track"], "volume": 0.6, "expected_db": -5.95, "clamped": False}
        if action == "mixer.trackInfo":
            return {"index": params["track"], "volume": 0.6, "volume_db": -5.951}
        return {}


def _tool(name):
    from test_fl_windows import Registrar

    registrar = Registrar()
    mixer_tools.register(registrar)
    return registrar.tools[name]


def test_load_effect_stages_a_preset_from_another_drive(monkeypatch, tmp_path):
    import tempfile
    from pathlib import Path

    from fl_studio_mcp import plugin_database

    preset = tmp_path / "Fruity Limiter.fst"
    preset.write_bytes(b"fst")
    monkeypatch.setattr(plugin_database, "resolve",
                        lambda name, kind: plugin_database.PluginEntry("Fruity Limiter", "effect", "Dynamics", preset))
    staging = tmp_path / "temp"
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(staging))
    data_system = str(Path(staging.anchor) / "FL" / "Data" / "System")
    client = LoadClient()
    client.data_system = data_system
    monkeypatch.setattr(mixer_tools, "get_client", lambda: client)
    result = _tool("mixer_load_effect")(track=1, plugin="fruity limiter")
    assert result["ok"] is True and "other_drive" not in result and "data_system" not in result
    staged = client.calls[1][1]["preset"]
    assert Path(staged) == staging / "fLMCP presets" / "Fruity Limiter.fst"
    result = mixer_tools._stage_preset(preset, data_system)
    assert result == staging / "fLMCP presets" / "Fruity Limiter.fst" and result.read_bytes() == b"fst"
    assert mixer_tools._stage_preset(preset, "") is None


def test_set_volume_db_verifies_with_a_second_read(monkeypatch):
    client = LoadClient()
    monkeypatch.setattr(mixer_tools, "get_client", lambda: client)
    fn = _tool("mixer_set_volume_db")
    r = fn(track=2, db=-6.0)
    assert r["verified"] is True and r["clamped"] is False
    assert [a for a, _ in client.calls] == ["mixer.setVolumeDb", "mixer.trackInfo"]
