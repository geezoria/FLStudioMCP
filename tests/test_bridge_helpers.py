"""Tests for the FL-side bridge's pure helper functions.

`device_FLStudioMCP.py` normally runs inside FL Studio, but it now guards its
FL-only imports so it can be loaded offline. We import it by file path and test
the colour conversion (a real bug was fixed here: FL uses 0xRRGGBB, the bridge
previously used 0xBBGGRR and swapped red/blue) plus the small mappers.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from types import SimpleNamespace

import pytest


def _load_bridge():
    p = pathlib.Path(__file__).resolve().parents[1] / "fl_bridge" / "device_FLStudioMCP.py"
    spec = importlib.util.spec_from_file_location("flmcp_device_bridge", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bridge = _load_bridge()


def test_color_uses_rrggbb_byte_order():
    # FL Studio: utils.RGBToColor(255,0,0) == 0xFF0000. Red must be the high byte.
    assert bridge._color_to_int("#FF0000") == 0xFF0000
    assert bridge._color_to_int("#00FF00") == 0x00FF00
    assert bridge._color_to_int("#0000FF") == 0x0000FF


def test_color_from_rgb_string():
    assert bridge._color_to_int("rgb(255, 0, 0)") == 0xFF0000
    assert bridge._color_to_int("rgb(0,128,255)") == 0x0080FF


def test_color_from_list():
    assert bridge._color_to_int([16, 32, 48]) == 0x102030


def test_color_int_passthrough():
    assert bridge._color_to_int(0x123456) == 0x123456


def test_color_bad_input_is_zero():
    assert bridge._color_to_int("not-a-color") == 0


@pytest.mark.parametrize("hexs", ["#000000", "#FFFFFF", "#102030", "#A1B2C3", "#FF0000"])
def test_color_roundtrip(hexs):
    assert bridge._int_to_color_hex(bridge._color_to_int(hexs)) == hexs


def test_int_to_hex_masks_high_bits():
    # FL sometimes returns colours with the alpha/high byte set; ignore it.
    assert bridge._int_to_color_hex(0xFF123456) == "#123456"


def test_position_unit_mapping():
    assert bridge._position_unit("ms") == 0
    assert bridge._position_unit("seconds") == 1
    assert bridge._position_unit("ticks") == 2
    assert bridge._position_unit("bars") == 3
    assert bridge._position_unit("steps") == 4
    assert bridge._position_unit("nonsense") == 3  # defaults to bars


def test_bool_int():
    assert bridge._bool_int(True) == 1
    assert bridge._bool_int(False) == 0
    assert bridge._bool_int(None) == 0
    assert bridge._bool_int(5) == 1


def test_handler_table_is_complete():
    for action in [
        "meta.ping", "transport.start", "transport.setTempo",
        "patterns.create", "channels.setColor", "channels.setStepSequence",
        "mixer.setEQBand", "mixer.arm", "playlist.muteTrack",
        "automation.recordTempo", "pianoroll.addNotes", "ui.focusedWindow",
    ]:
        assert action in bridge._HANDLERS, f"missing handler: {action}"


def test_safe_swallows_exceptions():
    def boom():
        raise RuntimeError("nope")
    assert bridge._safe(boom) is None
    assert bridge._safe(lambda x: x + 1, 41) == 42


class _MidiEvent:
    def __init__(self, status, data1, data2):
        self.status = status
        self.data1 = data1
        self.data2 = data2
        self.handled = False


def test_midi_wakeup_processes_file_request(tmp_path, monkeypatch):
    command = tmp_path / "mcp_command.json"
    response = tmp_path / "mcp_response.json"
    monkeypatch.setattr(bridge, "COMMAND_FILE", str(command))
    monkeypatch.setattr(bridge, "RESPONSE_FILE", str(response))
    monkeypatch.setattr(bridge, "_last_request_id", 0)
    monkeypatch.setitem(bridge._HANDLERS, "test.echo", lambda params: params)
    command.write_text(
        json.dumps({"id": 42, "action": "test.echo", "params": {"value": "ok"}}),
        encoding="utf-8",
    )

    event = _MidiEvent(bridge.WAKE_STATUS, bridge.WAKE_DATA1, bridge.WAKE_DATA2)
    bridge.OnMidiMsg(event)

    assert event.handled is True
    assert json.loads(response.read_text(encoding="utf-8")) == {
        "id": 42,
        "ok": True,
        "result": {"value": "ok"},
    }


def test_midi_wakeup_does_not_replay_same_request(tmp_path, monkeypatch):
    command = tmp_path / "mcp_command.json"
    response = tmp_path / "mcp_response.json"
    calls = []
    monkeypatch.setattr(bridge, "COMMAND_FILE", str(command))
    monkeypatch.setattr(bridge, "RESPONSE_FILE", str(response))
    monkeypatch.setattr(bridge, "_last_request_id", 0)
    monkeypatch.setitem(bridge._HANDLERS, "test.once", lambda params: calls.append(params) or {})
    command.write_text(
        json.dumps({"id": 7, "action": "test.once", "params": {"n": 1}}),
        encoding="utf-8",
    )

    bridge.OnMidiMsg(_MidiEvent(bridge.WAKE_STATUS, bridge.WAKE_DATA1, bridge.WAKE_DATA2))
    bridge.OnMidiMsg(_MidiEvent(bridge.WAKE_STATUS, bridge.WAKE_DATA1, bridge.WAKE_DATA2))

    assert calls == [{"n": 1}]


def test_non_wakeup_midi_passes_through(tmp_path, monkeypatch):
    response = tmp_path / "mcp_response.json"
    monkeypatch.setattr(bridge, "RESPONSE_FILE", str(response))
    event = _MidiEvent(0x90, 60, 100)
    bridge.OnMidiMsg(event)
    assert event.handled is False
    assert not response.exists()


def test_on_idle_remains_a_file_polling_fallback(tmp_path, monkeypatch):
    command = tmp_path / "mcp_command.json"
    response = tmp_path / "mcp_response.json"
    monkeypatch.setattr(bridge, "COMMAND_FILE", str(command))
    monkeypatch.setattr(bridge, "RESPONSE_FILE", str(response))
    monkeypatch.setattr(bridge, "_last_request_id", 0)
    monkeypatch.setitem(bridge._HANDLERS, "test.idle", lambda params: {"idle": True})
    command.write_text(
        json.dumps({"id": 99, "action": "test.idle", "params": {}}),
        encoding="utf-8",
    )

    bridge.OnIdle()

    result = json.loads(response.read_text(encoding="utf-8"))
    assert result["id"] == 99
    assert result["result"] == {"idle": True}


def test_plugin_value_string_uses_fl_api_38_signature(monkeypatch):
    calls = []

    def get_value_string(param, index, slot, use_global):
        calls.append((param, index, slot, use_global))
        return "50%"

    monkeypatch.setattr(bridge, "plugins", SimpleNamespace(getParamValueString=get_value_string))
    assert bridge._plugin_param_value_string(2, 4, -1, True) == "50%"
    assert calls == [(2, 4, -1, True)]


def test_plugin_value_string_falls_back_to_legacy_signature(monkeypatch):
    calls = []

    def get_value_string(*args):
        calls.append(args)
        if len(args) == 4:
            raise TypeError("legacy build expects pickupMode")
        return "legacy"

    monkeypatch.setattr(bridge, "plugins", SimpleNamespace(getParamValueString=get_value_string))
    assert bridge._plugin_param_value_string(2, 4, -1, True) == "legacy"
    assert calls == [(2, 4, -1, True), (2, 4, -1, 0, True)]


def test_trigger_note_schedules_and_processes_note_off(monkeypatch):
    calls = []
    clock = [10.0]
    monkeypatch.setattr(
        bridge,
        "channels",
        SimpleNamespace(midiNoteOn=lambda *args: calls.append(args)),
    )
    monkeypatch.setattr(bridge.time, "monotonic", lambda: clock[0])
    bridge._note_off_jobs.clear()

    result = bridge.h_channels_trigger_note({
        "index": 4,
        "note": 60,
        "velocity": 90,
        "duration_ms": 120,
    })
    assert result["mode"] == "async"
    assert result["duration_ms"] == 120
    assert calls == [(4, 60, 90, -1)]

    clock[0] = 10.2
    bridge._process_note_offs()
    assert calls == [(4, 60, 90, -1), (4, 60, 0, -1)]
    assert bridge._note_off_jobs == []


def test_playlist_iteration_uses_one_based_track_indices(monkeypatch):
    names = {1: "One", 2: "Two"}
    fake_playlist = SimpleNamespace(
        trackCount=lambda: 2,
        getTrackName=lambda index: names[index],
        getTrackColor=lambda index: 0,
        isTrackMuted=lambda index: 0,
        isTrackSolo=lambda index: 0,
    )
    monkeypatch.setattr(bridge, "playlist", fake_playlist)
    result = bridge.h_playlist_all_tracks({"include_empty": True})
    assert [track["index"] for track in result["tracks"]] == [1, 2]
