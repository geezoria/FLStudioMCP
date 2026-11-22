"""Pure-Python tests for the voice-to-MIDI post-processing helpers.

These run without numpy / librosa / sounddevice — the heavy deps are only
needed for actual recording / transcription, not for the note maths here.
"""

from __future__ import annotations

import importlib.util

from fl_studio_mcp.voice_to_midi import (
    SCALE_INTERVALS,
    Note,
    drop_low_confidence,
    ensure_audio_deps,
    notes_as_piano_roll,
    quantize,
    snap_to_scale,
    transpose,
    _note_name_to_pc,
    _scale_pcs,
)


def test_note_to_piano_roll_120bpm():
    # At 120 BPM a bar (4 beats) is 2 s. So 2 s -> 1 bar, 1 s -> 0.5 bar.
    pr = Note(midi=60, start_sec=2.0, duration_sec=1.0, velocity=0.8).as_piano_roll_note(bpm=120.0)
    assert pr["midi"] == 60
    assert pr["time_bars"] == 1.0
    assert pr["duration_bars"] == 0.5
    assert pr["velocity"] == 0.8


def test_note_velocity_clamped():
    assert Note(60, 0, 1, velocity=2.0).as_piano_roll_note()["velocity"] == 1.0
    assert Note(60, 0, 1, velocity=-1.0).as_piano_roll_note()["velocity"] == 0.0


def test_snap_to_scale_unambiguous():
    # C minor pitch-classes: C D Eb F G Ab Bb. E natural (64) -> Eb (63).
    out = snap_to_scale([Note(64, 0.0, 1.0)], root="C", scale="minor")[0]
    assert out.midi == 63


def test_snap_to_scale_keeps_every_note_in_scale():
    pcs = _scale_pcs(_note_name_to_pc("C"), "minor")
    chromatic = [Note(60 + i, i * 0.1, 0.1) for i in range(12)]
    snapped = snap_to_scale(chromatic, root="C", scale="minor")
    for n in snapped:
        assert (n.midi % 12) in pcs


def test_snap_preserves_timing_and_count():
    notes = [Note(61, 0.5, 0.25, velocity=0.7, confidence=0.9)]
    out = snap_to_scale(notes, root="C", scale="major")
    assert len(out) == 1
    assert out[0].start_sec == 0.5
    assert out[0].duration_sec == 0.25
    assert out[0].velocity == 0.7


def test_quantize_snaps_to_grid():
    out = quantize([Note(60, 0.13, 0.5)], grid_sec=0.25, strength=1.0)[0]
    assert out.start_sec == 0.25  # 0.13 / 0.25 = 0.52 -> rounds to 1 -> 0.25


def test_quantize_half_strength():
    out = quantize([Note(60, 0.0, 0.5)], grid_sec=0.25, strength=0.5)[0]
    # Already on grid -> no movement.
    assert out.start_sec == 0.0


def test_transpose_clamps_to_midi_range():
    assert transpose([Note(125, 0, 1)], 10)[0].midi == 127
    assert transpose([Note(2, 0, 1)], -10)[0].midi == 0
    assert transpose([Note(60, 0, 1)], 12)[0].midi == 72


def test_drop_low_confidence():
    notes = [Note(60, 0, 1, confidence=0.2), Note(62, 0, 1, confidence=0.9)]
    assert [n.midi for n in drop_low_confidence(notes, min_conf=0.5)] == [62]


def test_note_name_to_pc():
    assert _note_name_to_pc("C") == 0
    assert _note_name_to_pc("B") == 11
    assert _note_name_to_pc("Bb") == 10
    assert _note_name_to_pc("F#") == 6
    assert _note_name_to_pc(" Ab ") == 8


def test_notes_as_piano_roll_offset():
    out = notes_as_piano_roll([Note(60, 0.0, 2.0)], bpm=120.0, time_offset_bars=1.0)
    assert out[0]["time_bars"] == 1.0  # 0 s + 1 bar offset


def test_all_scales_present():
    for required in ("major", "minor", "dorian", "blues", "chromatic"):
        assert required in SCALE_INTERVALS


def test_ensure_audio_deps_contract():
    have = all(importlib.util.find_spec(m) for m in ("numpy", "librosa", "soundfile"))
    res = ensure_audio_deps()
    if have:
        assert res is None
    else:
        assert isinstance(res, str)
        assert "flmcp[audio]" in res
