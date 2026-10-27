"""
Audio file analysis: tempo + key + onsets + melody extraction.

Works on any soundfile-readable format (WAV / FLAC / MP3 / OGG / AIFF).
Uses librosa for beat tracking, chroma-based key detection, onset detection
and melody extraction (pyin on the whole mix — best guess for the most
prominent monophonic line).
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# numpy / librosa / soundfile are heavy optional deps (the `audio` extra) and are
# imported lazily inside the functions that need them, so importing this module
# (e.g. when the MCP server registers the audio tools) does not require them.

from .voice_to_midi import Note, transcribe_monophonic, transcribe_polyphonic

log = logging.getLogger("fl_studio_mcp.audio_analysis")


# Key profiles (Krumhansl-Schmuckler) for major/minor key detection. Kept as
# plain Python tuples so this module imports without numpy; converted to arrays
# lazily inside `_estimate_key`.
_KEY_PROFILE_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                      2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_KEY_PROFILE_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                      2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


@dataclass
class AudioAnalysis:
    path: str
    duration_sec: float
    samplerate: int
    tempo_bpm: float
    beats_sec: list[float]
    onsets_sec: list[float]
    key_root: str
    key_scale: str          # "major" | "minor"
    key_confidence: float   # 0..1
    loudness_db: float
    energy_rms: float
    notes: list[Note] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "duration_sec": round(self.duration_sec, 2),
            "samplerate": self.samplerate,
            "tempo_bpm": round(self.tempo_bpm, 1),
            "beats_count": len(self.beats_sec),
            "onsets_count": len(self.onsets_sec),
            "key": f"{self.key_root} {self.key_scale}",
            "key_root": self.key_root,
            "key_scale": self.key_scale,
            "key_confidence": round(self.key_confidence, 3),
            "loudness_db": round(self.loudness_db, 1),
            "energy_rms": round(self.energy_rms, 4),
            "beats_sec": [round(b, 3) for b in self.beats_sec[:32]],
            "onsets_sec": [round(o, 3) for o in self.onsets_sec[:32]],
            "notes_count": len(self.notes),
        }


def load_audio(path: str | Path, target_sr: int = 22050) -> tuple[Any, int]:
    """Load any audio file (WAV/MP3/FLAC/OGG) as mono float32 (numpy array)."""
    import librosa
    y, sr = librosa.load(str(path), sr=target_sr, mono=True)
    return y, sr


def analyze_audio(path: str | Path,
                  extract_melody: bool = False,
                  target_sr: int = 22050,
                  polyphonic: bool = False) -> AudioAnalysis:
    """Full analysis: tempo, key, onsets, loudness, (optional) melody/notes.

    When `extract_melody` is set, `polyphonic=True` runs Spotify Basic Pitch to
    capture chords / all notes; otherwise the monophonic pyin engine extracts a
    single dominant line.
    """
    import librosa
    import numpy as np

    path = str(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    y, sr = load_audio(path, target_sr=target_sr)
    duration = len(y) / sr

    # Tempo & beats. A 256-sample hop avoids unstable neighbouring tempo bins
    # on syncopated material (e.g. 126 BPM being reported as 129.2 at hop 512).
    beat_hop = 256
    try:
        onset_envelope = librosa.onset.onset_strength(y=y, sr=sr, hop_length=beat_hop)
        tempo_arr, beats = librosa.beat.beat_track(
            onset_envelope=onset_envelope, sr=sr, hop_length=beat_hop,
        )
        tempo = float(tempo_arr.item() if hasattr(tempo_arr, "item") else tempo_arr)
        beats_sec = librosa.frames_to_time(beats, sr=sr, hop_length=beat_hop).tolist()
    except Exception as e:
        log.warning("beat tracking failed: %s", e)
        tempo = 0.0
        beats_sec = []

    # Onsets
    try:
        onsets = librosa.onset.onset_detect(
            y=y, sr=sr, units="time", hop_length=beat_hop,
        )
        onsets_sec = [float(o) for o in onsets]
    except Exception as e:
        log.warning("onset detection failed: %s", e)
        onsets_sec = []

    # Key via chroma + Krumhansl-Schmuckler
    try:
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        pc_mean = chroma.mean(axis=1)
        key_root, key_scale, key_conf = _estimate_key(pc_mean)
    except Exception as e:
        log.warning("key estimation failed: %s", e)
        key_root, key_scale, key_conf = "C", "major", 0.0

    # Loudness + RMS
    rms = float(np.sqrt(np.mean(y * y))) if y.size else 0.0
    loudness_db = 20.0 * math.log10(max(rms, 1e-9))

    a = AudioAnalysis(
        path=path,
        duration_sec=duration,
        samplerate=sr,
        tempo_bpm=tempo,
        beats_sec=beats_sec,
        onsets_sec=onsets_sec,
        key_root=key_root,
        key_scale=key_scale,
        key_confidence=key_conf,
        loudness_db=loudness_db,
        energy_rms=rms,
    )

    if extract_melody:
        if polyphonic:
            # Basic Pitch: polyphonic, captures chords / all notes in the mix.
            a.notes = transcribe_polyphonic(path, min_note_sec=0.1)
        else:
            # pyin on the full mix — best-effort monophonic extraction.
            # Works well on vocals / lead lines, less so on dense mixes.
            a.notes = transcribe_monophonic(path, min_note_sec=0.1,
                                            merge_gap_sec=0.15,
                                            semitone_tolerance=1.2,
                                            hysteresis_sec=0.08)

    return a


def _estimate_key(pc_mean) -> tuple[str, str, float]:
    """Krumhansl-Schmuckler key profile correlation."""
    import numpy as np

    profile_major = np.array(_KEY_PROFILE_MAJOR)
    profile_minor = np.array(_KEY_PROFILE_MINOR)
    pc_mean = np.asarray(pc_mean, dtype=float)
    pc = pc_mean / (pc_mean.sum() + 1e-9)
    best_name, best_scale, best_corr = "C", "major", -2.0
    for i in range(12):
        maj_shifted = np.roll(profile_major, i)
        min_shifted = np.roll(profile_minor, i)
        corr_maj = float(np.corrcoef(pc, maj_shifted)[0, 1])
        corr_min = float(np.corrcoef(pc, min_shifted)[0, 1])
        if corr_maj > best_corr:
            best_corr = corr_maj
            best_name = _NAMES[i]
            best_scale = "major"
        if corr_min > best_corr:
            best_corr = corr_min
            best_name = _NAMES[i]
            best_scale = "minor"
    confidence = max(0.0, min(1.0, (best_corr + 1.0) / 2.0))
    return best_name, best_scale, confidence


def slice_at_onsets(path: str | Path,
                    output_dir: str | Path | None = None,
                    max_slices: int = 32,
                    min_slice_sec: float = 0.25,
                    pad_before_sec: float = 0.01,
                    pad_after_sec: float = 0.05) -> list[str]:
    """Slice the input audio at detected onsets. Writes WAV files to disk."""
    import librosa
    import soundfile as sf

    y, sr = load_audio(path)
    onsets = librosa.onset.onset_detect(y=y, sr=sr, units="samples")
    if len(onsets) == 0:
        return []

    out_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir()) / "fLMCP_slices"
    out_dir.mkdir(parents=True, exist_ok=True)

    src_stem = Path(path).stem
    written: list[str] = []
    total = len(y)
    for i in range(min(len(onsets), max_slices)):
        start = int(max(0, onsets[i] - pad_before_sec * sr))
        end = int(min(total,
                      onsets[i + 1] + pad_after_sec * sr if i + 1 < len(onsets)
                      else total))
        if (end - start) / sr < min_slice_sec:
            continue
        slice_wav = y[start:end]
        dest = out_dir / f"{src_stem}_slice_{i:03d}.wav"
        sf.write(str(dest), slice_wav, sr, subtype="PCM_16")
        written.append(str(dest))

    return written
