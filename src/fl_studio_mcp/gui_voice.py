"""
fLMCP Voice-to-MIDI GUI — interactive recording + live waveform + pitch
preview + piano-roll push.

Built with Dear PyGui (lightweight, GPU-rendered, < 2 MB).

Run with:
    python -m fl_studio_mcp.gui_voice
"""

from __future__ import annotations

import math
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

try:
    import dearpygui.dearpygui as dpg
except Exception as e:
    print(f"Dear PyGui not installed: {e}")
    print("Run: pip install dearpygui")
    sys.exit(1)

try:
    import sounddevice as sd
    import soundfile as sf
except Exception as e:
    print(f"sounddevice/soundfile missing: {e}")
    sys.exit(1)

from .file_bridge import stage_and_run
from .voice_to_midi import (
    SCALE_INTERVALS,
    Note,
    drop_low_confidence,
    notes_as_piano_roll,
    quantize,
    snap_to_scale,
    transcribe_monophonic,
    transpose,
    list_input_devices,
)


# ---------------------------------------------------------------------------
# Audio state shared between threads
# ---------------------------------------------------------------------------

@dataclass
class AudioState:
    samplerate: int = 16000
    record_device: int | None = None
    buffer: list[np.ndarray] | None = None       # all recorded frames
    display_buffer: np.ndarray | None = None      # scrolling waveform (~2 sec)
    display_len: int = 16000 * 2                  # show last 2 sec
    recording: bool = False
    level: float = 0.0                            # 0..1, RMS over last frame
    live_pitch_hz: float | None = None
    stream: sd.InputStream | None = None

    def reset(self):
        self.buffer = []
        self.display_buffer = np.zeros(self.display_len, dtype=np.float32)
        self.level = 0.0
        self.live_pitch_hz = None


state = AudioState()
state.reset()
notes_cache: list[Note] = []


# ---------------------------------------------------------------------------
# Audio callback (runs on sounddevice callback thread)
# ---------------------------------------------------------------------------

def _audio_callback(indata, frames, time_info, status):
    if status:
        pass
    mono = indata[:, 0] if indata.ndim == 2 else indata
    mono = mono.astype(np.float32, copy=False)

    if state.recording and state.buffer is not None:
        state.buffer.append(mono.copy())

    # Scrolling display buffer (always running)
    if state.display_buffer is not None:
        n = len(mono)
        if n >= state.display_len:
            state.display_buffer[:] = mono[-state.display_len:]
        else:
            state.display_buffer = np.roll(state.display_buffer, -n)
            state.display_buffer[-n:] = mono

    # Level meter (RMS)
    rms = float(np.sqrt(np.mean(mono * mono))) if mono.size else 0.0
    state.level = min(1.0, rms * 4.0)

    # Rough live pitch via autocorrelation on 512 samples (very light)
    # For precise: pyin. For realtime vibe: simple autocorrelation.
    if mono.size >= 512 and state.level > 0.02:
        seg = mono[-2048:] if mono.size >= 2048 else mono
        seg = seg - np.mean(seg)
        ac = np.correlate(seg, seg, mode="full")[len(seg) - 1:]
        # find first peak past min-period window
        sr = state.samplerate
        min_lag = int(sr / 1200.0)     # 1200 Hz max
        max_lag = int(sr / 65.0)       # 65 Hz min
        if max_lag < len(ac):
            window = ac[min_lag:max_lag]
            if window.size:
                peak = int(np.argmax(window)) + min_lag
                if peak > 0:
                    f0 = sr / peak
                    # keep if this peak is reasonably strong relative to ac[0]
                    if ac[peak] > 0.3 * ac[0]:
                        state.live_pitch_hz = f0
                    else:
                        state.live_pitch_hz = None
    else:
        state.live_pitch_hz = None


def _start_stream():
    if state.stream is not None:
        try:
            state.stream.close()
        except Exception:
            pass
        state.stream = None
    try:
        state.stream = sd.InputStream(
            samplerate=state.samplerate,
            channels=1,
            dtype="float32",
            blocksize=512,
            device=state.record_device,
            callback=_audio_callback,
        )
        state.stream.start()
    except Exception as e:
        dpg.set_value("status_label", f"mic open failed: {e}")
        state.stream = None


def _stop_stream():
    if state.stream is not None:
        try:
            state.stream.stop()
            state.stream.close()
        except Exception:
            pass
        state.stream = None


# ---------------------------------------------------------------------------
# Recording + transcription
# ---------------------------------------------------------------------------

def _do_record(duration_sec: float):
    state.reset()
    state.recording = True
    dpg.set_value("status_label", f"Recording {duration_sec:.1f} s…")
    start = time.monotonic()
    _start_stream()
    while time.monotonic() - start < duration_sec:
        remaining = duration_sec - (time.monotonic() - start)
        dpg.set_value("countdown_label", f"{remaining:4.1f}s")
        _refresh_waveform_and_level()
        time.sleep(0.02)
    state.recording = False
    dpg.set_value("countdown_label", "0.0s")
    dpg.set_value("status_label", "Transcribing…")

    # Save WAV
    if not state.buffer:
        dpg.set_value("status_label", "no audio captured")
        return
    audio = np.concatenate(state.buffer)
    tmp = Path(tempfile.gettempdir()) / f"fLMCP_gui_{int(time.time())}.wav"
    sf.write(str(tmp), audio, state.samplerate, subtype="PCM_16")
    dpg.set_value("wav_label", f"WAV: {tmp}")

    # Transcribe on worker thread (librosa can be slow on cold start)
    def _run_transcribe():
        global notes_cache
        try:
            notes_cache = transcribe_monophonic(str(tmp))
        except Exception as e:
            dpg.set_value("status_label", f"transcribe error: {e}")
            return
        _render_notes_table(notes_cache)
        dpg.set_value("status_label",
                      f"Transcribed {len(notes_cache)} notes. "
                      f"Review below, tweak scale if needed, then 'Send to FL'.")

    threading.Thread(target=_run_transcribe, daemon=True).start()


# ---------------------------------------------------------------------------
# Visual updates (run on worker threads, touch DPG via set_value — which is
# thread-safe in DPG 2.x)
# ---------------------------------------------------------------------------

def _refresh_waveform_and_level():
    if state.display_buffer is None:
        return
    # Subsample for display — 1000 points
    buf = state.display_buffer
    step = max(1, len(buf) // 1000)
    y = buf[::step][:1000].tolist()
    x = list(range(len(y)))
    try:
        dpg.set_value("wave_series", [x, y])
    except Exception:
        pass

    dpg.set_value("level_bar", float(state.level))
    if state.live_pitch_hz:
        midi = 12 * math.log2(state.live_pitch_hz / 440.0) + 69
        name = _midi_name(int(round(midi)))
        dpg.set_value("live_pitch_label", f"♪ {name}  ({state.live_pitch_hz:.1f} Hz)")
    else:
        dpg.set_value("live_pitch_label", "♪ –")


def _midi_name(midi: int) -> str:
    names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    return f"{names[midi % 12]}{midi // 12 - 1}"


def _render_notes_table(notes: list[Note]):
    # clear old rows
    try:
        children = dpg.get_item_children("notes_table", 1) or []
        for c in children:
            dpg.delete_item(c)
    except Exception:
        pass

    if not notes:
        return
    for i, n in enumerate(notes):
        name = _midi_name(n.midi)
        with dpg.table_row(parent="notes_table"):
            dpg.add_text(f"{i+1}")
            dpg.add_text(f"{n.start_sec:.2f}")
            dpg.add_text(f"{n.duration_sec:.2f}")
            dpg.add_text(f"{n.midi}")
            dpg.add_text(name)
            dpg.add_text(f"{n.velocity:.2f}")
            dpg.add_text(f"{n.confidence:.2f}")


# ---------------------------------------------------------------------------
# Piano-roll push
# ---------------------------------------------------------------------------

def _send_to_fl():
    if not notes_cache:
        dpg.set_value("status_label", "nothing to send — record first")
        return

    bpm = float(dpg.get_value("bpm_input"))
    clear_first = bool(dpg.get_value("clear_check"))
    scale_root = dpg.get_value("scale_root_combo")
    scale = dpg.get_value("scale_combo")
    quant_grid = float(dpg.get_value("quant_input"))
    transpose_sem = int(dpg.get_value("transpose_input"))
    min_conf = float(dpg.get_value("conf_slider"))

    ns = drop_low_confidence(notes_cache, min_conf=min_conf)
    if transpose_sem:
        ns = transpose(ns, transpose_sem)
    if scale_root != "off" and scale != "off":
        ns = snap_to_scale(ns, root=scale_root, scale=scale)
    if quant_grid > 0:
        ns = quantize(ns, grid_sec=quant_grid, strength=1.0)

    pr = notes_as_piano_roll(ns, bpm=bpm)
    if not pr:
        dpg.set_value("status_label", "all notes filtered out — lower confidence or re-record")
        return

    qn = [{"midi": n["midi"], "time": n["time_bars"] * 4,
           "duration": n["duration_bars"] * 4, "velocity": n["velocity"]}
          for n in pr]

    actions = ([{"action": "clear"}] if clear_first else []) + \
              [{"action": "add_notes", "notes": qn}]

    dpg.set_value("status_label", "sending to FL…")

    def _run():
        r = stage_and_run(actions, wait_sec=8.0)
        if r.get("ok"):
            dpg.set_value("status_label",
                          f"✅ {len(pr)} notes written. "
                          f"FL state: {r.get('state', {}).get('noteCount')} total.")
        else:
            dpg.set_value("status_label",
                          f"⚠ {r.get('note', 'no state file')} — click FL window + retry")

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# GUI callbacks
# ---------------------------------------------------------------------------

def _on_device_change(sender, value):
    for d in list_input_devices():
        if d.get("name") == value:
            state.record_device = d.get("index")
            break
    _start_stream()


def _on_record_click():
    duration = float(dpg.get_value("duration_input"))
    threading.Thread(target=_do_record, args=(duration,), daemon=True).start()


# ---------------------------------------------------------------------------
# Build window
# ---------------------------------------------------------------------------

def _build_gui():
    dpg.create_context()
    dpg.create_viewport(title="fLMCP Voice to MIDI", width=980, height=760)

    with dpg.window(label="fLMCP Voice to MIDI", tag="root_win",
                    no_close=True, no_collapse=True, width=960, height=730):

        # Top row — device, duration, bpm
        with dpg.group(horizontal=True):
            dpg.add_text("Mic:")
            devices = list_input_devices()
            names = [d["name"] for d in devices]
            default_name = next((d["name"] for d in devices if d.get("is_default")),
                                names[0] if names else "")
            dpg.add_combo(names, default_value=default_name,
                          width=280, tag="device_combo",
                          callback=_on_device_change)
            # apply default device
            for d in devices:
                if d["name"] == default_name:
                    state.record_device = d.get("index")
                    break

            dpg.add_text("    Duration (s):")
            dpg.add_input_float(default_value=6.0, width=70, min_value=1.0,
                                max_value=60.0, step=0.5, tag="duration_input")

            dpg.add_text("    BPM:")
            dpg.add_input_float(default_value=120.0, width=70, min_value=40.0,
                                max_value=240.0, step=1.0, tag="bpm_input")

        dpg.add_separator()

        # Record button + countdown + live pitch
        with dpg.group(horizontal=True):
            dpg.add_button(label="●  RECORD", width=130, height=50,
                           callback=_on_record_click)
            with dpg.group():
                dpg.add_text("Remaining:", bullet=False)
                dpg.add_text("0.0s", tag="countdown_label")
            with dpg.group():
                dpg.add_text("Live pitch:")
                dpg.add_text("♪ –", tag="live_pitch_label")
            with dpg.group():
                dpg.add_text("Level:")
                dpg.add_progress_bar(tag="level_bar", width=200, default_value=0.0)

        dpg.add_separator()

        # Waveform
        with dpg.plot(label="Waveform (last 2 s)", height=160, width=-1,
                      no_menus=True, no_title=False):
            dpg.add_plot_axis(dpg.mvXAxis, no_gridlines=True, no_tick_labels=True)
            y_axis = dpg.add_plot_axis(dpg.mvYAxis, no_gridlines=True)
            dpg.set_axis_limits(y_axis, -1.0, 1.0)
            dpg.add_line_series(list(range(1000)), [0.0] * 1000,
                                parent=y_axis, tag="wave_series")

        dpg.add_separator()

        # Post-processing controls
        with dpg.group(horizontal=True):
            dpg.add_text("Snap to scale:")
            dpg.add_combo(["off"] + ["C", "C#", "D", "D#", "E", "F", "F#",
                                     "G", "G#", "A", "A#", "B"],
                          default_value="off", width=60, tag="scale_root_combo")
            dpg.add_combo(["off"] + list(SCALE_INTERVALS.keys()),
                          default_value="off", width=150, tag="scale_combo")

            dpg.add_text("    Quantize grid (sec):")
            dpg.add_input_float(default_value=0.0, width=80, min_value=0.0,
                                max_value=2.0, step=0.0625, tag="quant_input")

        with dpg.group(horizontal=True):
            dpg.add_text("Transpose (semitones):")
            dpg.add_input_int(default_value=0, width=70,
                              min_value=-36, max_value=36, tag="transpose_input")

            dpg.add_text("    Min confidence:")
            dpg.add_slider_float(default_value=0.35, min_value=0.0, max_value=1.0,
                                 width=150, tag="conf_slider")

            dpg.add_text("    ")
            dpg.add_checkbox(label="Clear piano roll first",
                             default_value=True, tag="clear_check")

        dpg.add_separator()

        # Send button
        with dpg.group(horizontal=True):
            dpg.add_button(label="↪  SEND TO FL STUDIO", width=200, height=38,
                           callback=_send_to_fl)
            dpg.add_text("(writes into the piano roll open in FL Studio)")

        dpg.add_separator()

        # Notes table
        dpg.add_text("Transcribed notes:")
        with dpg.table(header_row=True, resizable=True, policy=dpg.mvTable_SizingStretchProp,
                       borders_innerH=True, borders_outerH=True,
                       borders_innerV=True, borders_outerV=True,
                       tag="notes_table", height=220):
            dpg.add_table_column(label="#")
            dpg.add_table_column(label="start (s)")
            dpg.add_table_column(label="dur (s)")
            dpg.add_table_column(label="MIDI")
            dpg.add_table_column(label="note")
            dpg.add_table_column(label="vel")
            dpg.add_table_column(label="conf")

        dpg.add_separator()

        # Status bar
        dpg.add_text("Ready. Click RECORD to start.", tag="status_label")
        dpg.add_text("", tag="wav_label")

    dpg.setup_dearpygui()
    dpg.show_viewport()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    _build_gui()
    # start mic stream for live preview immediately
    _start_stream()
    try:
        while dpg.is_dearpygui_running():
            _refresh_waveform_and_level()
            dpg.render_dearpygui_frame()
    finally:
        _stop_stream()
        dpg.destroy_context()


if __name__ == "__main__":
    main()
