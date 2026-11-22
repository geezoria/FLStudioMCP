"""Tests for polyphonic transcription (Spotify Basic Pitch integration).

The conversion from Basic Pitch's output to our Note list is pure and is tested
here against the EXACT tuple shape Basic Pitch emits (verified against the real
library: note_events are 5-tuples
``(start_s, end_s, pitch_midi, amplitude, pitch_bends)``). basic-pitch itself is
an optional heavy extra and is not required to run these tests.
"""

from __future__ import annotations

import importlib.util
import json
import types

import pytest

from fl_studio_mcp.voice_to_midi import (
    _basic_pitch_to_notes,
    ensure_polyphonic_deps,
    notes_as_piano_roll,
)


def _fake_midi(notes):
    inst = types.SimpleNamespace(notes=[
        types.SimpleNamespace(pitch=p, start=s, end=e, velocity=v) for (p, s, e, v) in notes
    ])
    return types.SimpleNamespace(instruments=[inst])


def test_note_events_real_tuple_shape():
    # The first element is the real tuple captured from Basic Pitch on a C-major
    # triad: (start, end, pitch, amplitude, pitch_bends-list).
    events = [
        (0.023219, 0.940408, 60, 0.524668, [1] * 78),
        (0.0, 1.0, 64, 0.6, None),
        (0.5, 1.0, 67, 0.9, [1, 2, 3]),
    ]
    notes = _basic_pitch_to_notes(events)
    # All three notes are kept (polyphony) — order is by (start_sec, midi).
    assert sorted(n.midi for n in notes) == [60, 64, 67]
    c4 = next(n for n in notes if n.midi == 60)
    assert c4.start_sec == 0.0232
    assert c4.duration_sec == round(0.940408 - 0.023219, 4)
    # amplitude (index 3) is the confidence — NOT the pitch-bend list at index 4.
    assert c4.confidence == 0.525
    assert c4.velocity == round(0.5 + 0.45 * 0.525, 3)


def test_note_events_sorted_by_time_then_pitch():
    events = [
        (0.5, 1.0, 67, 0.8, None),
        (0.0, 1.0, 64, 0.8, None),
        (0.0, 1.0, 60, 0.8, None),
    ]
    notes = _basic_pitch_to_notes(events)
    assert [(n.start_sec, n.midi) for n in notes] == [(0.0, 60), (0.0, 64), (0.5, 67)]


def test_min_note_sec_filters_short_notes():
    events = [(0.0, 0.05, 60, 0.9, None), (0.0, 0.5, 62, 0.9, None)]
    notes = _basic_pitch_to_notes(events, min_note_sec=0.1)
    assert [n.midi for n in notes] == [62]


def test_min_confidence_filters_quiet_notes():
    events = [(0.0, 1.0, 60, 0.2, None), (0.0, 1.0, 62, 0.9, None)]
    notes = _basic_pitch_to_notes(events, min_confidence=0.5)
    assert [n.midi for n in notes] == [62]


def test_pitch_is_clamped_to_midi_range():
    events = [(0.0, 1.0, 200, 0.9, None), (0.0, 1.0, -5, 0.9, None)]
    notes = _basic_pitch_to_notes(events)
    assert sorted(n.midi for n in notes) == [0, 127]


def test_malformed_events_are_skipped():
    events = [("bad", "data"), (0.0, 1.0, 60, 0.8, None)]
    notes = _basic_pitch_to_notes(events)
    assert [n.midi for n in notes] == [60]


def test_fallback_to_midi_data_when_no_events():
    midi = _fake_midi([(60, 0.0, 1.0, 100), (64, 0.0, 1.0, 80)])
    notes = _basic_pitch_to_notes([], midi)
    assert sorted(n.midi for n in notes) == [60, 64]
    n60 = next(n for n in notes if n.midi == 60)
    assert n60.confidence == round(100 / 127.0, 3)


def test_events_take_precedence_over_midi_data():
    midi = _fake_midi([(99, 0.0, 1.0, 100)])
    notes = _basic_pitch_to_notes([(0.0, 1.0, 60, 0.8, None)], midi)
    assert [n.midi for n in notes] == [60]  # not 99


def test_empty_everything_returns_empty():
    assert _basic_pitch_to_notes([], None) == []
    assert _basic_pitch_to_notes(None, None) == []


def test_converter_emits_native_json_serializable_types():
    # Basic Pitch emits numpy scalars (np.float64 / np.int64 / np.float32). The
    # converter must cast to native Python types or MCP's JSON serialization
    # would choke. Feed numpy types and verify the output is plain + serializable.
    np = pytest.importorskip("numpy")
    events = [(np.float64(0.1), np.float64(0.9), np.int64(60), np.float32(0.7),
               [np.int64(1), np.int64(1)])]
    notes = _basic_pitch_to_notes(events)
    n = notes[0]
    assert type(n.midi) is int
    assert type(n.start_sec) is float
    assert type(n.duration_sec) is float
    assert type(n.velocity) is float
    assert type(n.confidence) is float
    # The dicts that actually flow back through the MCP layer must serialize.
    json.dumps(notes_as_piano_roll(notes, bpm=120))


def test_ensure_polyphonic_deps_contract():
    have_bp = importlib.util.find_spec("basic_pitch") is not None
    res = ensure_polyphonic_deps()
    if have_bp:
        assert res is None
    else:
        assert isinstance(res, str)
        assert "flmcp[polyphonic]" in res


def test_tools_expose_polyphonic_flag(monkeypatch):
    from anyio import run
    from fl_studio_mcp.server import build_app
    monkeypatch.delenv("FLMCP_TOOLSETS", raising=False)
    monkeypatch.delenv("FLMCP_DISABLE_TOOLS", raising=False)
    app = build_app()
    tools = {t.name: t for t in run(app.list_tools)}
    for name in ("voice_transcribe_file", "voice_record_and_transcribe",
                 "voice_to_piano_roll", "audio_analyze",
                 "audio_melody_to_piano_roll", "song_to_dnb_flip"):
        props = tools[name].input_schema.get("properties", {})
        assert "polyphonic" in props, f"{name} missing polyphonic flag"
