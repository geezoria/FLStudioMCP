"""Voice-to-MIDI tools: hum a melody, transcribe it, send it to the piano roll.

Workflow:
    voice_list_devices()                 # optional — inspect mics
    voice_record_and_transcribe(8)       # 8-second capture -> MIDI notes
    voice_to_piano_roll(8, bpm=120, scale_root="C", scale="minor")
                                         # one-shot: capture + transcribe
                                         # + snap-to-scale + quantize +
                                         # write into the piano roll
"""

from __future__ import annotations

from typing import Any, Optional

from ..file_bridge import stage_and_run
from ..voice_to_midi import (
    Note,
    SCALE_INTERVALS,
    drop_low_confidence,
    ensure_audio_deps,
    ensure_polyphonic_deps,
    list_input_devices,
    notes_as_piano_roll,
    quantize,
    record_wav,
    snap_to_scale,
    transcribe_monophonic,
    transcribe_polyphonic,
    transpose,
)
from ._meta import ToolRegistrar


def _check_deps(polyphonic: bool, need_mic: bool = False) -> str | None:
    """Return a dep-missing message (or None). Recording always needs the audio
    extra; transcription needs basic-pitch when polyphonic, else the audio extra."""
    if need_mic:
        err = ensure_audio_deps(need_mic=True)
        if err:
            return err
    return ensure_polyphonic_deps() if polyphonic else ensure_audio_deps()


def _transcribe(path, polyphonic: bool, min_note_sec: float,
                fmin_hz: float, fmax_hz: float, min_confidence: float = 0.0) -> list[Note]:
    """Dispatch to the polyphonic (Basic Pitch) or monophonic (pyin) engine."""
    if polyphonic:
        return transcribe_polyphonic(
            path, min_note_sec=min_note_sec, min_confidence=min_confidence,
            fmin_hz=fmin_hz or None, fmax_hz=fmax_hz or None,
        )
    return transcribe_monophonic(
        path, fmin_hz=fmin_hz, fmax_hz=fmax_hz, min_note_sec=min_note_sec,
    )


def _notes_to_dicts(notes: list[Note]) -> list[dict]:
    return [{
        "midi": n.midi,
        "start_sec": n.start_sec,
        "duration_sec": n.duration_sec,
        "velocity": n.velocity,
        "confidence": n.confidence,
    } for n in notes]


def register(tools: ToolRegistrar) -> None:
    @tools.write("Open Voice Transcription Window")
    def voice_open_gui() -> dict[str, Any]:
        """Launch the interactive Voice-to-MIDI GUI (Dear PyGui window).

        Starts a subprocess so the MCP server doesn't block. The window has
        live waveform, live pitch detection, post-processing controls
        (scale snap / quantize / transpose / confidence filter) and a 'Send
        to FL Studio' button.
        """
        err = ensure_audio_deps(need_mic=True)
        if err:
            return {"ok": False, "error": err}
        try:
            import dearpygui  # noqa: F401
        except Exception:
            return {"ok": False,
                    "error": "Dear PyGui not installed. Install with: "
                             "pip install \"fl-studio-mcp[gui]\" (or [audio])."}
        import subprocess
        import sys as _sys
        try:
            proc = subprocess.Popen(
                [_sys.executable, "-m", "fl_studio_mcp.gui_voice"],
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                if _sys.platform == "win32" else 0,
            )
            return {"ok": True, "pid": proc.pid,
                    "hint": "GUI window opened in a separate process."}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @tools.read("List Microphone Devices")
    def voice_list_devices() -> dict[str, Any]:
        """List available microphones (indexes + names + default flag)."""
        err = ensure_audio_deps(need_mic=True)
        if err:
            return {"ok": False, "error": err, "devices": []}
        return {"devices": list_input_devices()}

    @tools.write("Record and Transcribe Microphone")
    def voice_record_and_transcribe(
        duration_sec: float = 8.0,
        device: int | None = None,
        min_note_sec: float = 0.08,
        fmin_hz: float = 65.0,
        fmax_hz: float = 1200.0,
        polyphonic: bool = False,
    ) -> dict[str, Any]:
        """Record the mic for `duration_sec` seconds and transcribe what you played.

        Returns raw notes + the temp WAV path. Use this when you want to inspect
        the transcription before touching the piano roll. 3 short + 1 long
        beep plays at start, 1 beep at stop.

        `polyphonic=True` uses Spotify Basic Pitch (chords / overlapping notes —
        guitar strums, piano, etc.); needs the `polyphonic` extra. Default is the
        lightweight monophonic pyin engine (one note at a time — humming).
        """
        err = _check_deps(polyphonic, need_mic=True)
        if err:
            return {"ok": False, "error": err}
        wav = record_wav(duration_sec, device=device)
        notes = _transcribe(wav, polyphonic, min_note_sec, fmin_hz, fmax_hz)
        return {
            "wav_path": str(wav),
            "notes": _notes_to_dicts(notes),
            "count": len(notes),
            "engine": "basic_pitch (polyphonic)" if polyphonic else "pyin (monophonic)",
        }

    @tools.read("Transcribe Audio File to Notes")
    def voice_transcribe_file(
        audio_path: str,
        min_note_sec: float = 0.08,
        fmin_hz: float = 65.0,
        fmax_hz: float = 1200.0,
        polyphonic: bool = False,
    ) -> dict[str, Any]:
        """Transcribe an existing audio file (wav / flac / mp3 / ogg) into MIDI notes.

        `polyphonic=True` uses Spotify Basic Pitch (chords / full-mix content);
        needs the `polyphonic` extra. Default is the monophonic pyin engine.
        """
        err = _check_deps(polyphonic, need_mic=False)
        if err:
            return {"ok": False, "error": err}
        notes = _transcribe(audio_path, polyphonic, min_note_sec, fmin_hz, fmax_hz)
        return {
            "audio_path": audio_path,
            "notes": _notes_to_dicts(notes),
            "count": len(notes),
            "engine": "basic_pitch (polyphonic)" if polyphonic else "pyin (monophonic)",
        }

    @tools.write("Record Melody to Piano Roll", destructive=True)
    def voice_to_piano_roll(
        duration_sec: float = 8.0,
        bpm: float = 120.0,
        device: int | None = None,
        scale_root: Optional[str] = None,
        scale: Optional[str] = None,
        transpose_semitones: int = 0,
        quantize_grid_sec: Optional[float] = None,
        min_confidence: float = 0.35,
        min_note_sec: float = 0.08,
        polyphonic: bool = False,
        clear_first: bool = True,
    ) -> dict[str, Any]:
        """Hum/play a melody into the mic, get it written straight into FL's piano roll.

        Args:
            duration_sec:   how long to record.
            bpm:            used to convert seconds -> bars. Match your FL tempo.
            device:         mic index (see voice_list_devices); None = default.
            scale_root:     e.g. "C", "F#", "Bb". If given, notes are snapped.
            scale:          e.g. "minor", "major", "dorian". One of
                            gen_list_scales. Used only when scale_root is given.
            transpose_semitones: shift all notes up/down after transcription.
            quantize_grid_sec: snap note starts to this grid (e.g. 0.125 = 1/16
                            at 120 BPM, 0.25 = 1/8, 0.5 = 1/4). None = off.
            min_confidence: drop notes where the engine was uncertain (0..1).
            min_note_sec:   drop notes shorter than this many seconds.
            polyphonic:     use Spotify Basic Pitch (chords / overlapping notes —
                            needs the `polyphonic` extra). Default = monophonic pyin.
            clear_first:    clear the open piano roll before writing.

        Returns:
            {ok, notes_written, piano_roll_state, wav_path, transcription_count}
        """
        import time as _t

        err = _check_deps(polyphonic, need_mic=True)
        if err:
            return {"ok": False, "error": err}
        t0 = _t.monotonic()
        wav = record_wav(duration_sec, device=device)
        t_rec = _t.monotonic() - t0

        t0 = _t.monotonic()
        notes = _transcribe(wav, polyphonic, min_note_sec, 65.0, 1200.0)
        t_trans = _t.monotonic() - t0

        notes = drop_low_confidence(notes, min_conf=min_confidence)

        if transpose_semitones:
            notes = transpose(notes, transpose_semitones)

        if scale_root and scale:
            if scale not in SCALE_INTERVALS:
                return {"ok": False,
                        "error": f"unknown scale '{scale}'; choose from "
                                 f"{list(SCALE_INTERVALS.keys())}"}
            notes = snap_to_scale(notes, root=scale_root, scale=scale)

        if quantize_grid_sec is not None and quantize_grid_sec > 0:
            notes = quantize(notes, grid_sec=quantize_grid_sec, strength=1.0)

        pr_notes = notes_as_piano_roll(notes, bpm=bpm)

        if not pr_notes:
            return {
                "ok": False,
                "notes_written": 0,
                "reason": "no valid notes transcribed — try humming louder / "
                          "check voice_list_devices, or lower min_confidence.",
                "wav_path": str(wav),
                "record_sec": round(t_rec, 2),
                "transcribe_sec": round(t_trans, 2),
            }

        actions = []
        if clear_first:
            actions.append({"action": "clear"})
        # Convert piano-roll bars to quarter notes (pyscript expects quarters)
        qn_notes = []
        for n in pr_notes:
            qn_notes.append({
                "midi": n["midi"],
                "time": n["time_bars"] * 4,
                "duration": n["duration_bars"] * 4,
                "velocity": n["velocity"],
            })
        actions.append({"action": "add_notes", "notes": qn_notes})

        result = stage_and_run(actions, wait_sec=8.0)
        return {
            "ok": result.get("ok", False),
            "notes_written": len(pr_notes),
            "transcription_count": len(notes),
            "wav_path": str(wav),
            "bpm": bpm,
            "scale": f"{scale_root} {scale}" if scale_root and scale else None,
            "piano_roll_state": result.get("state"),
            "record_sec": round(t_rec, 2),
            "transcribe_sec": round(t_trans, 2),
            "note_preview": pr_notes[:8],
            "hint": result.get("note"),
        }

    @tools.write("Write Transcribed Notes to Piano Roll", destructive=True)
    def voice_notes_to_piano_roll(
        notes: list[dict],
        bpm: float = 120.0,
        scale_root: Optional[str] = None,
        scale: Optional[str] = None,
        transpose_semitones: int = 0,
        quantize_grid_sec: Optional[float] = None,
        clear_first: bool = True,
    ) -> dict[str, Any]:
        """Push an already-transcribed note list (from voice_record_and_transcribe)
        into the piano roll with optional scale snapping / transposition / quantize.

        Useful when you want Claude to inspect the raw transcription first,
        tweak it, then send a cleaned-up version. This tool is pure-Python and
        does NOT require the audio extras.
        """
        if scale_root and scale and scale not in SCALE_INTERVALS:
            return {"ok": False,
                    "error": f"unknown scale '{scale}'; choose from "
                             f"{list(SCALE_INTERVALS.keys())}"}
        typed = [Note(midi=int(n["midi"]),
                      start_sec=float(n["start_sec"]),
                      duration_sec=float(n["duration_sec"]),
                      velocity=float(n.get("velocity", 0.8)),
                      confidence=float(n.get("confidence", 1.0)))
                 for n in notes]

        if transpose_semitones:
            typed = transpose(typed, transpose_semitones)
        if scale_root and scale:
            typed = snap_to_scale(typed, root=scale_root, scale=scale)
        if quantize_grid_sec is not None and quantize_grid_sec > 0:
            typed = quantize(typed, grid_sec=quantize_grid_sec, strength=1.0)

        pr_notes = notes_as_piano_roll(typed, bpm=bpm)
        qn_notes = [{"midi": n["midi"], "time": n["time_bars"] * 4,
                     "duration": n["duration_bars"] * 4, "velocity": n["velocity"]}
                    for n in pr_notes]
        actions = ([{"action": "clear"}] if clear_first else []) + \
                  [{"action": "add_notes", "notes": qn_notes}]
        result = stage_and_run(actions, wait_sec=8.0)
        return {
            "ok": result.get("ok", False),
            "notes_written": len(pr_notes),
            "piano_roll_state": result.get("state"),
        }
