# name=fLMCP Bridge
# supportedDevices=fLMCP Bridge
# url=https://github.com/your-handle/fLMCP
# receiveFrom=fLMCP Bridge

"""
fLMCP Bridge — the FL Studio side of the fl-studio-mcp Model Context Protocol server.

How it works
------------
This script runs inside FL Studio as a MIDI controller bound to a loopback MIDI
input. The MCP server writes one JSON request file and sends a dedicated MIDI CC
as a wake-up signal. FL delivers that signal to OnMidiMsg on its main thread;
the callback reads the request, executes the existing handler, and writes the JSON
response. OnIdle polls the same file as a fallback on FL versions that emit it.

Piano-roll edits are deferred: we stage them into `piano_roll_requests.json` inside
this script's directory, and the fl-studio-mcp server is responsible for opening the
piano-roll window and triggering the companion `ComposeWithLLM.pyscript` via
Ctrl+Alt+Y. After the pyscript runs, it writes `piano_roll_state.json` which the
MCP server reads back.

Transport files
---------------
Request:  <script dir>/mcp_command.json
Response: <script dir>/mcp_response.json
Envelope: {"id": int, "action": str, "params": {...}}

The wake-up message is Control Change 119, value 1, on MIDI channel 16. It is
marked handled so it does not reach instruments or alter the project.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

# FL Studio API — available when running inside FL Studio. Wrapped so this file
# can also be imported outside FL (e.g. by the offline unit tests, which only
# exercise the pure helper functions like colour / version / position mapping).
try:
    import arrangement
    import channels
    import device
    import general
    import midi
    import mixer
    import patterns
    import playlist
    import plugins
    import transport
    import ui
    _IN_FL_STUDIO = True
except ImportError:  # pragma: no cover - exercised only outside FL Studio
    arrangement = channels = device = general = midi = None
    mixer = patterns = playlist = plugins = transport = ui = None
    _IN_FL_STUDIO = False


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

BRIDGE_VERSION = "0.3.0-file-midi"
WAKE_STATUS = 0xBF  # Control Change on MIDI channel 16
WAKE_DATA1 = 119
WAKE_DATA2 = 1


def _script_dir():
    if sys.platform == "darwin":
        base = Path.home() / "Documents" / "Image-Line" / "FL Studio" / "Settings"
    elif sys.platform == "win32":
        userprofile = os.environ.get("USERPROFILE", str(Path.home()))
        base = Path(userprofile) / "Documents" / "Image-Line" / "FL Studio" / "Settings"
    else:
        base = Path.home() / "Documents" / "Image-Line" / "FL Studio" / "Settings"
    return base / "Hardware" / "fLMCP Bridge"


SCRIPT_DIR = _script_dir()
PIANO_ROLL_DIR_NAME = "Piano roll scripts"
PR_REQUEST = Path(SCRIPT_DIR).parent.parent / PIANO_ROLL_DIR_NAME / "fLMCP_request.json"
PR_STATE = Path(SCRIPT_DIR).parent.parent / PIANO_ROLL_DIR_NAME / "fLMCP_state.json"


# ----------------------------------------------------------------------------
# Global state
# ----------------------------------------------------------------------------

_started_at = time.monotonic()
_idle_tick = 0
_last_request_id = 0
_auto_jobs = []          # pending non-blocking automation jobs (see automation section)
_auto_recording = False  # True while an automation clip is being recorded live
_note_off_jobs = []      # pending preview-note releases

COMMAND_FILE = os.path.join(str(SCRIPT_DIR), "mcp_command.json")
RESPONSE_FILE = os.path.join(str(SCRIPT_DIR), "mcp_response.json")


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _log(msg):
    try:
        print("[fLMCP] " + msg)
    except Exception:
        pass


def _color_to_int(color):
    """Convert '#RRGGBB', 'rgb(r,g,b)', [r,g,b] or int to FL's colour integer.

    FL Studio stores colours as 0xRRGGBB (red in the high byte) — verified
    against `utils.RGBToColor(255, 0, 0) == 0xFF0000`. Earlier versions of this
    bridge used 0xBBGGRR, which swapped red and blue in FL.
    """
    if isinstance(color, bool):
        return 0
    if isinstance(color, int):
        return color
    if isinstance(color, (list, tuple)) and len(color) >= 3:
        r, g, b = int(color[0]), int(color[1]), int(color[2])
        return (r << 16) | (g << 8) | b
    s = str(color).strip()
    if s.startswith("#"):
        s = s[1:]
        if len(s) == 6:
            r = int(s[0:2], 16); g = int(s[2:4], 16); b = int(s[4:6], 16)
            return (r << 16) | (g << 8) | b
    if s.lower().startswith("rgb"):
        parts = s[s.find("(")+1:s.find(")")].split(",")
        r, g, b = [int(p.strip()) for p in parts[:3]]
        return (r << 16) | (g << 8) | b
    try:
        return int(s, 0)
    except Exception:
        return 0


def _int_to_color_hex(color):
    """Convert FL's 0xRRGGBB colour integer to '#RRGGBB'."""
    color = int(color) & 0xFFFFFF
    r = (color >> 16) & 0xFF
    g = (color >> 8) & 0xFF
    b = color & 0xFF
    return "#%02X%02X%02X" % (r, g, b)


def _bool_int(value):
    return 1 if value else 0


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception:
        return None


# ----------------------------------------------------------------------------
# File + MIDI transport
# ----------------------------------------------------------------------------

def _read_json_file(path):
    """Read JSON using only open/read, which works in FL's sub-interpreter."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
        return json.loads(raw) if raw.strip() else None
    except Exception:
        return None


def _write_response(response):
    """Overwrite the response file; no rename/delete calls run inside FL."""
    try:
        with open(RESPONSE_FILE, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
            handle.flush()
        return True
    except Exception as e:
        _log("response write failed: %s" % e)
        return False


def _process_request_file():
    """Execute at most one new request on FL's main thread."""
    global _last_request_id
    request = _read_json_file(COMMAND_FILE)
    if not isinstance(request, dict):
        return False
    request_id = request.get("id")
    # Skip only the exact id we just handled (dedupe repeated MIDI wakes for the
    # same command). Using '==' instead of '<=' keeps the bridge usable when
    # multiple clients, each starting its id counter from its own time.time_ns(),
    # interleave: a later client would otherwise raise the watermark and make an
    # earlier client's (lower-id) commands be ignored forever.
    if not isinstance(request_id, int) or request_id == _last_request_id:
        return False

    _last_request_id = request_id
    action = request.get("action", "")
    params = request.get("params", {}) or {}
    try:
        result = _execute(action, params)
        response = {"id": request_id, "ok": True, "result": result}
    except Exception as e:
        response = {
            "id": request_id,
            "ok": False,
            "error": "%s: %s" % (type(e).__name__, e),
            "traceback": traceback.format_exc(limit=3),
        }
        _log("action %s error: %s" % (action, e))
    _write_response(response)
    return True


# ----------------------------------------------------------------------------
# Handlers — executed on FL's main thread (from OnMidiMsg / OnIdle)
# ----------------------------------------------------------------------------

def _execute(action, params):
    """Dispatch action -> result dict. Raises on error."""
    h = _HANDLERS.get(action)
    if h is None:
        raise ValueError("unknown action: %s" % action)
    return h(params or {})


# ---- meta -------------------------------------------------------------------

def h_meta_ping(_):
    return {
        "ok": True,
        "bridge_version": BRIDGE_VERSION,
        "fl_version": _safe(general.getVersion) or "unknown",
        "uptime_sec": round(time.monotonic() - _started_at, 1),
        "script_dir": str(SCRIPT_DIR),
    }


def h_meta_info(_):
    return {
        "bridge_version": BRIDGE_VERSION,
        "fl_version": _safe(general.getVersion) or "unknown",
        "api_modules": ["transport","mixer","channels","patterns","playlist",
                        "plugins","arrangement","ui","general","device","midi"],
        "script_dir": str(SCRIPT_DIR),
        "transport": "file-midi",
        "command_file": COMMAND_FILE,
        "response_file": RESPONSE_FILE,
        "wake_midi": {"status": WAKE_STATUS, "data1": WAKE_DATA1, "data2": WAKE_DATA2},
        "last_request_id": _last_request_id,
    }


# ---- transport --------------------------------------------------------------

def _position_unit(unit):
    # FL SONGLENGTH_* constants: 0=MS, 1=S, 2=ABSTICKS, 3=BARS, 4=STEPS, 5=TICKS
    return {"ms": 0, "seconds": 1, "ticks": 2, "bars": 3, "steps": 4}.get(unit, 3)


def h_transport_start(_):
    transport.start()
    return {"is_playing": transport.isPlaying() == 1}


def h_transport_stop(_):
    transport.stop()
    return {"stopped": True}


def h_transport_record(_):
    transport.record()
    return {"is_recording": transport.isRecording() == 1}


def h_transport_status(_):
    try:
        tempo = mixer.getCurrentTempo() / 1000.0
    except Exception:
        tempo = None
    return {
        "is_playing": transport.isPlaying() == 1,
        "is_recording": transport.isRecording() == 1,
        "position_ticks": transport.getSongPos(2),
        "position_bars": transport.getSongPos(3),
        "position_seconds": transport.getSongPos(1),
        "loop_mode": "song" if transport.getLoopMode() == 1 else "pattern",
        "bpm": tempo,
    }


def h_transport_set_position(p):
    unit = p.get("unit", "bars")
    transport.setSongPos(p.get("position", 0), _position_unit(unit))
    return {"position_bars": transport.getSongPos(3)}


def h_transport_length(_):
    return {
        "ticks": transport.getSongLength(2),
        "seconds": transport.getSongLength(1),
        "ms": transport.getSongLength(0),
        "bars": transport.getSongLength(3),
        "steps": transport.getSongLength(4),
    }


def h_transport_set_loop_mode(p):
    mode = p.get("mode", "pattern")
    target = 1 if mode == "song" else 0
    if transport.getLoopMode() != target:
        transport.setLoopMode()
    return {"mode": mode}


def h_transport_set_playback_speed(p):
    speed = float(p.get("speed", 1.0))
    transport.setPlaybackSpeed(speed)
    return {"speed": speed}


def h_transport_set_tempo(p):
    bpm = float(p.get("bpm", 140.0))
    general.processRECEvent(
        midi.REC_Tempo,
        int(round(bpm * 1000)),
        midi.REC_Control | midi.REC_UpdateControl,
    )
    return {"bpm": mixer.getCurrentTempo() / 1000.0}


def _fpt(name):
    """Resolve an FPT_* constant if it exists; return None otherwise."""
    return getattr(midi, name, None)


def h_transport_tap_tempo(_):
    fpt = _fpt("FPT_TapTempo")
    if fpt is None:
        return {"ok": False, "error": "midi.FPT_TapTempo not available in this FL version"}
    transport.globalTransport(fpt, 1)
    return {"ok": True}


def h_transport_set_time_signature(p):
    num = int(p.get("numerator", 4))
    den = int(p.get("denominator", 4))
    rec_num = getattr(midi, "REC_MainTimeSigNum", None)
    rec_den = getattr(midi, "REC_MainTimeSigDen", None)
    if rec_num is None or rec_den is None:
        return {"ok": False, "error": "REC_MainTimeSig* not available; set via UI or the General Settings.",
                "numerator": num, "denominator": den}
    flags = midi.REC_Control | midi.REC_UpdateControl
    general.processRECEvent(rec_num, num, flags)
    general.processRECEvent(rec_den, den, flags)
    return {"numerator": num, "denominator": den}


def h_transport_toggle_metronome(_):
    fpt = _fpt("FPT_Metronome")
    if fpt is None:
        return {"ok": False, "error": "midi.FPT_Metronome not available"}
    transport.globalTransport(fpt, 1)
    return {"ok": True}


def h_transport_toggle_countdown(_):
    fpt = _fpt("FPT_CountDown") or _fpt("FPT_CountDownBeforeRecording")
    if fpt is None:
        return {"ok": False, "error": "midi.FPT_CountDown* not available"}
    transport.globalTransport(fpt, 1)
    return {"ok": True}


def h_transport_jog(p):
    steps = int(p.get("steps", 0))
    fpt = _fpt("FPT_Jog")
    if fpt is None:
        return {"ok": False, "error": "midi.FPT_Jog not available"}
    for _ in range(abs(steps)):
        transport.globalTransport(fpt, 1 if steps > 0 else -1)
    return {"steps": steps}


def _transport_toggle_command(name):
    fpt = _fpt(name)
    if fpt is None:
        return {"ok": False, "error": "midi.%s not available" % name}
    result = transport.globalTransport(fpt, 1)
    return {"ok": True, "command": name, "result": result}


def h_transport_toggle_punch(_):
    return _transport_toggle_command("FPT_Punch")


def h_transport_toggle_overdub(_):
    return _transport_toggle_command("FPT_Overdub")


def h_transport_toggle_loop_recording(_):
    return _transport_toggle_command("FPT_LoopRecord")


def h_transport_jump_marker(p):
    direction = 1 if int(p.get("direction", 1)) >= 0 else -1
    fpt = _fpt("FPT_MarkerJumpJog")
    if fpt is None:
        return {"ok": False, "error": "midi.FPT_MarkerJumpJog not available"}
    result = transport.globalTransport(fpt, direction)
    return {
        "ok": True,
        "direction": direction,
        "result": result,
        "position_bars": transport.getSongPos(3),
    }


# ---- patterns ---------------------------------------------------------------

def h_patterns_count(_):
    return {"count": patterns.patternCount()}


def h_patterns_current(_):
    idx = patterns.patternNumber()
    return {"index": idx, "name": patterns.getPatternName(idx)}


def h_patterns_list(_):
    out = []
    for i in range(1, patterns.patternCount() + 1):
        out.append({
            "index": i,
            "name": patterns.getPatternName(i),
            "color": _int_to_color_hex(patterns.getPatternColor(i)),
            "length_steps": _safe(patterns.getPatternLength, i),
        })
    return {"patterns": out}


def h_patterns_select(p):
    idx = int(p.get("index", 1))
    patterns.jumpToPattern(idx)
    return {"selected": idx, "name": patterns.getPatternName(idx)}


def h_patterns_create(p):
    name = p.get("name", "")
    # FL appends a new pattern when you setPatternName on patternCount()+1
    new_idx = patterns.patternCount() + 1
    patterns.setPatternName(new_idx, name or ("Pattern %d" % new_idx))
    return {"index": new_idx, "name": patterns.getPatternName(new_idx)}


def h_patterns_rename(p):
    idx = int(p["index"]); name = p.get("name", "")
    patterns.setPatternName(idx, name)
    return {"index": idx, "name": patterns.getPatternName(idx)}


def h_patterns_set_color(p):
    idx = int(p["index"]); color = _color_to_int(p.get("color", "#888888"))
    patterns.setPatternColor(idx, color)
    return {"index": idx, "color": _int_to_color_hex(patterns.getPatternColor(idx))}


def h_patterns_delete(p):
    """FL's public Python API has no deletePattern(). Workaround: rename to empty
    and mark as unused — the pattern stays in the pool but is effectively hidden."""
    idx = int(p["index"])
    patterns.setPatternName(idx, "")
    return {"ok": False,
            "soft_deleted": idx,
            "note": "FL Python API does not expose deletePattern; pattern was renamed to empty."}


def h_patterns_clone(p):
    """clonePattern(index=None) clones the current pattern. We jump first, then clone."""
    src = int(p["index"])
    new_name = p.get("new_name", "") or (patterns.getPatternName(src) + " (copy)")
    patterns.jumpToPattern(src)
    patterns.clonePattern()
    new_idx = patterns.patternCount()
    patterns.setPatternName(new_idx, new_name)
    return {"new_index": new_idx, "name": patterns.getPatternName(new_idx)}


def h_patterns_set_length(p):
    """FL's API does not expose a programmatic pattern-length setter (length is
    derived from notes / step grid)."""
    return {"ok": False,
            "note": "FL Python API does not expose setPatternLength; adjust length by placing notes or step bits instead."}


def h_patterns_find_by_name(p):
    target = (p.get("name") or "").lower().strip()
    for i in range(1, patterns.patternCount() + 1):
        if patterns.getPatternName(i).lower() == target:
            return {"index": i, "name": patterns.getPatternName(i)}
    return {"index": None, "name": None}


def h_patterns_jump_next(_):
    patterns.jumpToPattern(min(patterns.patternNumber() + 1, patterns.patternCount()))
    return h_patterns_current({})


def h_patterns_jump_prev(_):
    patterns.jumpToPattern(max(patterns.patternNumber() - 1, 1))
    return h_patterns_current({})


# ---- channels ---------------------------------------------------------------

def _ch_info(i):
    use_global = True
    # getChannelPitch(index, mode=1) returns the offset in CENTS (the API docs
    # mislabel it "semitones"); divide by 100 for a true semitone value.
    pitch_cents = _safe(channels.getChannelPitch, i, 1, use_global)
    # getChannelVolume(index, mode, useGlobalIndex): mode=False -> normalized
    # 0..1 (what setVolume expects), mode=True -> dB. The index arg comes THIRD.
    channel_type = _safe(channels.getChannelType, i, use_global)
    type_names = {
        getattr(midi, "CT_Sampler", 0): "sampler",
        getattr(midi, "CT_Hybrid", 1): "hybrid",
        getattr(midi, "CT_GenPlug", 2): "generator_plugin",
        getattr(midi, "CT_Layer", 3): "layer",
        getattr(midi, "CT_AudioClip", 4): "audio_clip",
        getattr(midi, "CT_AutoClip", 5): "automation_clip",
    }
    return {
        "index": i,
        "name": channels.getChannelName(i, use_global),
        "color": _int_to_color_hex(channels.getChannelColor(i, use_global)),
        "volume": channels.getChannelVolume(i, False, use_global),
        "volume_db": _safe(channels.getChannelVolume, i, True, use_global),
        "pan": channels.getChannelPan(i, use_global),
        "pitch_semitones": round(pitch_cents / 100.0, 4) if pitch_cents is not None else None,
        "pitch_cents": pitch_cents,
        "is_muted": channels.isChannelMuted(i, use_global) == 1,
        "is_solo": channels.isChannelSolo(i, use_global) == 1,
        "is_selected": channels.isChannelSelected(i, use_global) == 1,
        "fx_track": channels.getTargetFxTrack(i, use_global),
        "type": channel_type,
        "type_name": type_names.get(channel_type, "unknown"),
    }


def h_channels_count(p):
    return {"count": channels.channelCount(bool(p.get("global_count", True)))}


def h_channels_info(p):
    return _ch_info(int(p["index"]))


def h_channels_all(_):
    out = []
    for i in range(channels.channelCount(True)):
        out.append(_ch_info(i))
    return {"channels": out}


def h_channels_selected(_):
    idx = channels.selectedChannel(canBeNone=True, indexGlobal=True)
    if idx is None or idx < 0:
        return {"channel": None}
    return {"channel": _ch_info(idx)}


def h_channels_select(p):
    idx = int(p["index"])
    if p.get("exclusive", True):
        channels.selectOneChannel(idx, True)
    else:
        channels.selectChannel(idx, 1, True)
    return {"selected": idx, "name": channels.getChannelName(idx, True)}


def h_channels_set_volume(p):
    # setChannelVolume(index, volume, pickupMode, useGlobalIndex): keep pickup
    # off (0) and use the GLOBAL index — the 3rd positional arg is pickupMode,
    # NOT useGlobalIndex, which earlier versions got wrong.
    channels.setChannelVolume(int(p["index"]), float(p["volume"]), 0, True)
    return _ch_info(int(p["index"]))


def h_channels_set_pan(p):
    channels.setChannelPan(int(p["index"]), float(p["pan"]), 0, True)
    return _ch_info(int(p["index"]))


def h_channels_set_pitch(p):
    idx = int(p["index"])
    semitones = float(p["semitones"])
    # mode 1 = cents. (mode 0 is a factor of the bend range; mode 2 is broken.)
    # The result is clamped to the channel's configured pitch range.
    channels.setChannelPitch(idx, semitones * 100.0, 1, 0, True)
    return _ch_info(idx)


def h_channels_mute(p):
    idx = int(p["index"]); muted = p.get("muted")
    # muteChannel(index, value=-1, useGlobalIndex): value -1 toggles, 0/1 set.
    if muted is None:
        channels.muteChannel(idx, -1, True)
    else:
        channels.muteChannel(idx, 1 if muted else 0, True)
    return {"index": idx, "is_muted": channels.isChannelMuted(idx, True) == 1}


def h_channels_solo(p):
    idx = int(p["index"]); solo = p.get("solo")
    # soloChannel has no value arg (toggle only), so honour an explicit desired
    # state by toggling only when it differs from the current state.
    is_solo = channels.isChannelSolo(idx, True) == 1
    if solo is None or bool(solo) != is_solo:
        channels.soloChannel(idx, True)
    return {"index": idx, "is_solo": channels.isChannelSolo(idx, True) == 1}


def h_channels_set_name(p):
    idx = int(p["index"]); name = p.get("name", "")
    channels.setChannelName(idx, name, True)
    return _ch_info(idx)


def h_channels_set_color(p):
    idx = int(p["index"])
    channels.setChannelColor(idx, _color_to_int(p["color"]), True)
    return _ch_info(idx)


def h_channels_route_to_mixer(p):
    idx = int(p["index"]); tr = int(p["mixer_track"])
    channels.setTargetFxTrack(idx, tr, True)
    return {"index": idx, "mixer_track": channels.getTargetFxTrack(idx, True)}


def h_channels_trigger_note(p):
    idx = int(p["index"]); note = int(p.get("note", 60))
    vel = int(p.get("velocity", 100))
    midi_ch = int(p.get("midi_channel", -1))
    channels.midiNoteOn(idx, note, vel, midi_ch)
    if vel <= 0:
        return {"triggered": False, "released": True, "note": note, "duration_ms": 0}

    duration_ms = int(p.get("duration_ms", 250) or 250)
    duration_ms = max(1, duration_ms)
    _note_off_jobs.append({
        "deadline": time.monotonic() + duration_ms / 1000.0,
        "index": idx,
        "note": note,
        "midi_channel": midi_ch,
    })
    return {
        "triggered": True,
        "note": note,
        "duration_ms": duration_ms,
        "mode": "async",
        "approx_duration_sec": duration_ms / 1000.0,
    }


def _process_note_offs():
    """Release due preview notes without blocking FL's main thread."""
    if not _note_off_jobs:
        return
    now = time.monotonic()
    pending = []
    for job in _note_off_jobs:
        if job["deadline"] <= now:
            try:
                channels.midiNoteOn(job["index"], job["note"], 0, job["midi_channel"])
            except Exception as e:
                _log("note-off failed: %s" % e)
        else:
            pending.append(job)
    _note_off_jobs[:] = pending


def h_channels_get_grid_bit(p):
    idx = int(p["index"]); pos = int(p["position"])
    return {"value": channels.getGridBit(idx, pos, True) == 1}


def h_channels_set_grid_bit(p):
    idx = int(p["index"]); pos = int(p["position"]); v = bool(p["value"])
    channels.setGridBit(idx, pos, 1 if v else 0, True)
    return {"value": channels.getGridBit(idx, pos, True) == 1}


def _enter_pattern(pattern):
    """If `pattern` is given, jump to it and return the previous pattern index
    (to restore afterwards); otherwise return None. Grid-bit access always
    targets the currently-selected pattern, so this is how we reach another."""
    if pattern is None:
        return None
    prev = patterns.patternNumber()
    patterns.jumpToPattern(int(pattern))
    return prev


def _restore_pattern(prev):
    if prev is not None:
        patterns.jumpToPattern(int(prev))


def h_channels_get_step_sequence(p):
    idx = int(p["index"])
    prev = _enter_pattern(p.get("pattern"))
    try:
        cur = patterns.patternNumber()
        steps = _safe(patterns.getPatternLength, cur) or 16
        seq = [1 if channels.getGridBit(idx, s, True) else 0 for s in range(steps)]
        return {"steps": seq, "length": steps, "pattern": cur}
    finally:
        _restore_pattern(prev)


def h_channels_set_step_sequence(p):
    idx = int(p["index"]); steps = p.get("steps", [])
    prev = _enter_pattern(p.get("pattern"))
    try:
        for s, v in enumerate(steps):
            channels.setGridBit(idx, s, 1 if v else 0, True)
        return {"written": len(steps), "pattern": patterns.patternNumber()}
    finally:
        _restore_pattern(prev)


def h_channels_clear_step_sequence(p):
    idx = int(p["index"])
    prev = _enter_pattern(p.get("pattern"))
    try:
        steps = _safe(patterns.getPatternLength, patterns.patternNumber()) or 16
        for s in range(steps):
            channels.setGridBit(idx, s, 0, True)
        return {"cleared": steps, "pattern": patterns.patternNumber()}
    finally:
        _restore_pattern(prev)


def h_channels_quick_quantize(p):
    idx = int(p["index"])
    channels.selectOneChannel(idx, True)
    # quickQuantize(index, startOnly=1, useGlobalIndex): index is REQUIRED.
    channels.quickQuantize(idx, 1, True)
    return {"ok": True}


# ---- mixer -----------------------------------------------------------------

def _mx_info(i):
    return {
        "index": i,
        "name": mixer.getTrackName(i) or ("Master" if i == 0 else ""),
        "volume": mixer.getTrackVolume(i),
        "volume_db": _safe(mixer.getTrackVolume, i, 1),
        "pan": mixer.getTrackPan(i),
        "stereo_separation": mixer.getTrackStereoSep(i),
        "is_muted": mixer.isTrackMuted(i) == 1,
        "is_solo": mixer.isTrackSolo(i) == 1,
        "is_armed": mixer.isTrackArmed(i) == 1,
        "color": _int_to_color_hex(mixer.getTrackColor(i)),
    }


def h_mixer_count(_):
    return {"count": mixer.trackCount()}


def h_mixer_track_info(p):
    tr = int(p["track"])
    info = _mx_info(tr)
    # fx slots
    slots = []
    for s in range(10):
        try:
            pid = mixer.getTrackPluginId(tr, s)
            valid = plugins.isValid(tr, s, False)
            slots.append({"slot": s, "plugin_id": pid, "valid": bool(valid),
                          "name": plugins.getPluginName(tr, s, 0, False) if valid else None})
        except Exception:
            slots.append({"slot": s, "plugin_id": -1, "valid": False, "name": None})
    info["fx_slots"] = slots
    return info


def h_mixer_peaks(p):
    """Return live normalized left/right VU levels for one mixer track."""
    tr = int(p["track"])
    if not hasattr(mixer, "getTrackPeaks"):
        return {"ok": False, "error": "mixer.getTrackPeaks not available"}
    left = float(mixer.getTrackPeaks(tr, getattr(midi, "PEAK_L", 0)))
    right = float(mixer.getTrackPeaks(tr, getattr(midi, "PEAK_R", 1)))
    return {
        "track": tr,
        "left": left,
        "right": right,
        "peak": max(left, right),
    }


def h_mixer_all_tracks(p):
    include_empty = bool(p.get("include_empty", False))
    out = []
    for i in range(mixer.trackCount()):
        name = mixer.getTrackName(i)
        if not include_empty and (not name or name.startswith("Insert ")) and i != 0:
            continue
        out.append(_mx_info(i))
    return {"tracks": out}


def h_mixer_set_volume(p):
    mixer.setTrackVolume(int(p["track"]), float(p["volume"]))
    return _mx_info(int(p["track"]))


def h_mixer_set_pan(p):
    mixer.setTrackPan(int(p["track"]), float(p["pan"]))
    return _mx_info(int(p["track"]))


def h_mixer_mute(p):
    tr = int(p["track"]); muted = p.get("muted")
    if muted is None:
        mixer.muteTrack(tr, -1)
    else:
        mixer.muteTrack(tr, 1 if muted else 0)
    return _mx_info(tr)


def h_mixer_solo(p):
    tr = int(p["track"]); solo = p.get("solo")
    mode = int(p.get("mode", 3))
    if solo is None:
        mixer.soloTrack(tr, -1, mode)
    else:
        mixer.soloTrack(tr, 1 if solo else 0, mode)
    return _mx_info(tr)


def h_mixer_arm(p):
    tr = int(p["track"]); armed = p.get("armed")
    # armTrack(index) is toggle-only (no value arg), so honour an explicit
    # desired state by toggling only when it differs from the current state.
    is_armed = mixer.isTrackArmed(tr) == 1
    if armed is None or bool(armed) != is_armed:
        mixer.armTrack(tr)
    return _mx_info(tr)


def h_mixer_set_name(p):
    mixer.setTrackName(int(p["track"]), p.get("name", ""))
    return _mx_info(int(p["track"]))


def h_mixer_set_color(p):
    mixer.setTrackColor(int(p["track"]), _color_to_int(p["color"]))
    return _mx_info(int(p["track"]))


def h_mixer_set_stereo_sep(p):
    mixer.setTrackStereoSep(int(p["track"]), float(p["separation"]))
    return _mx_info(int(p["track"]))


def h_mixer_set_send_level(p):
    src = int(p["src_track"]); dst = int(p["dst_track"]); lvl = float(p["level"])
    # ensure the route exists, then set its level
    mixer.setRouteTo(src, dst, True, False)
    if hasattr(mixer, "setRouteToLevel"):
        mixer.setRouteToLevel(src, dst, lvl)
    mixer.afterRoutingChanged()
    return {"src": src, "dst": dst,
            "level": mixer.getRouteToLevel(src, dst) if hasattr(mixer, "getRouteToLevel") else lvl}


def h_mixer_route(p):
    enabled = bool(p.get("enabled", True))
    mixer.setRouteTo(int(p["src_track"]), int(p["dst_track"]), enabled, False)
    mixer.afterRoutingChanged()
    return {"enabled": enabled,
            "active": mixer.getRouteSendActive(int(p["src_track"]), int(p["dst_track"]))
            if hasattr(mixer, "getRouteSendActive") else None}


def h_mixer_fx_slots(p):
    tr = int(p["track"])
    slots = []
    for s in range(10):
        try:
            pid = mixer.getTrackPluginId(tr, s)
            valid = plugins.isValid(tr, s, False)
            slots.append({"slot": s, "plugin_id": pid, "valid": bool(valid),
                          "name": plugins.getPluginName(tr, s, 0, False) if valid else None})
        except Exception:
            slots.append({"slot": s, "plugin_id": -1, "valid": False, "name": None})
    return {"slots": slots}


def h_mixer_select(p):
    mixer.setActiveTrack(int(p["track"]))
    return {"selected": int(p["track"])}


def h_mixer_get_eq(p):
    tr = int(p["track"])
    band_count = mixer.getEqBandCount() if hasattr(mixer, "getEqBandCount") else 3
    bands = []
    for b in range(band_count):
        bands.append({
            "band": b,
            "gain": _safe(mixer.getEqGain, tr, b),
            "frequency": _safe(mixer.getEqFrequency, tr, b),
            "bandwidth": _safe(mixer.getEqBandwidth, tr, b),
        })
    return {"bands": bands, "band_count": band_count}


def h_mixer_set_eq_band(p):
    tr = int(p["track"]); band = int(p["band"])
    if p.get("gain") is not None:
        _safe(mixer.setEqGain, tr, band, float(p["gain"]))
    if p.get("frequency") is not None:
        _safe(mixer.setEqFrequency, tr, band, float(p["frequency"]))
    if p.get("bandwidth") is not None and hasattr(mixer, "setEqBandwidth"):
        _safe(mixer.setEqBandwidth, tr, band, float(p["bandwidth"]))
    return h_mixer_get_eq({"track": tr})


def h_mixer_link_channel(p):
    """Link a channel to a mixer track. `mode` legacy param is kept for API compat."""
    ch = int(p["channel"]); tr = int(p["track"])
    # Route via the global-index API so audio-clip channels (whose rack/group
    # index differs from the global index) link correctly instead of raising
    # "Index out of range".
    channels.setTargetFxTrack(ch, tr, True)
    return {"channel": ch, "track": tr}


# ---- plugins ---------------------------------------------------------------

def _resolve_plugin_loc(p):
    location = p.get("location", "channel")
    slot = int(p.get("slot", -1))
    index = int(p["index"])
    # plugins.* API: (index, slot, useGlobalIndex)
    useGlobal = (location == "channel")
    return index, slot, useGlobal


def _plugin_param_value_string(param, index, slot, use_global):
    """Handle the FL API 38 four-argument signature and older five-argument builds."""
    try:
        return plugins.getParamValueString(param, index, slot, use_global)
    except TypeError:
        return plugins.getParamValueString(param, index, slot, 0, use_global)


def h_plugins_is_valid(p):
    idx, slot, ug = _resolve_plugin_loc(p)
    return {"valid": plugins.isValid(idx, slot, ug) == 1}


def h_plugins_name(p):
    idx, slot, ug = _resolve_plugin_loc(p)
    return {"name": plugins.getPluginName(idx, slot, 0, ug)}


def h_plugins_param_count(p):
    idx, slot, ug = _resolve_plugin_loc(p)
    return {"count": plugins.getParamCount(idx, slot, ug)}


def h_plugins_params(p):
    idx, slot, ug = _resolve_plugin_loc(p)
    limit = int(p.get("limit", 128))
    offset = int(p.get("offset", 0))
    n = plugins.getParamCount(idx, slot, ug)
    out = []
    for i in range(offset, min(offset + limit, n)):
        try:
            out.append({
                "idx": i,
                "name": plugins.getParamName(i, idx, slot, ug),
                "value": plugins.getParamValue(i, idx, slot, ug),
                "value_string": _plugin_param_value_string(i, idx, slot, ug),
            })
        except Exception:
            pass
    return {"total": n, "params": out}


def h_plugins_get_param(p):
    idx, slot, ug = _resolve_plugin_loc(p)
    pid = int(p["param"])
    return {
        "value": plugins.getParamValue(pid, idx, slot, ug),
        "value_string": _plugin_param_value_string(pid, idx, slot, ug),
    }


def h_plugins_set_param(p):
    idx, slot, ug = _resolve_plugin_loc(p)
    pid = int(p["param"]); v = float(p["value"])
    # setParamValue(value, paramIndex, index, slotIndex, pickupMode, useGlobalIndex)
    # — pickupMode comes BEFORE useGlobalIndex; keep pickup off (0).
    plugins.setParamValue(v, pid, idx, slot, 0, ug)
    return {
        "value": plugins.getParamValue(pid, idx, slot, ug),
        "value_string": _plugin_param_value_string(pid, idx, slot, ug),
    }


def h_plugins_find_param(p):
    idx, slot, ug = _resolve_plugin_loc(p)
    needle = (p.get("name_contains") or "").lower()
    n = plugins.getParamCount(idx, slot, ug)
    hits = []
    for i in range(n):
        try:
            nm = plugins.getParamName(i, idx, slot, ug)
            if needle in nm.lower():
                hits.append({"idx": i, "name": nm,
                             "value": plugins.getParamValue(i, idx, slot, ug)})
        except Exception:
            pass
    return {"matches": hits}


def h_plugins_preset_count(p):
    idx, slot, ug = _resolve_plugin_loc(p)
    return {"count": plugins.getPresetCount(idx, slot, ug)}


def h_plugins_next_preset(p):
    idx, slot, ug = _resolve_plugin_loc(p)
    plugins.nextPreset(idx, slot, ug)
    return {"ok": True}


def h_plugins_prev_preset(p):
    idx, slot, ug = _resolve_plugin_loc(p)
    plugins.prevPreset(idx, slot, ug)
    return {"ok": True}


def h_plugins_set_preset(p):
    idx, slot, ug = _resolve_plugin_loc(p)
    preset = int(p["preset"])
    if not hasattr(plugins, "setPreset"):
        return {"ok": False,
                "error": "plugins.setPreset is not exposed by FL's Python API; "
                         "step presets with plugin_next_preset / plugin_prev_preset."}
    plugins.setPreset(preset, idx, slot, ug)
    return {"ok": True, "preset": preset}


def h_plugins_show_editor(p):
    idx, slot, ug = _resolve_plugin_loc(p)
    show = p.get("show")
    try:
        # showEditor(index, value=-1, useGlobalIndex): value -1 toggles.
        channels.showEditor(idx, -1 if show is None else (1 if show else 0), ug)
    except Exception:
        pass
    return {"ok": True}


def h_plugins_list_mixer_track(p):
    tr = int(p["track"])
    out = []
    for s in range(10):
        try:
            valid = plugins.isValid(tr, s, False) == 1
            out.append({
                "slot": s,
                "valid": valid,
                "name": plugins.getPluginName(tr, s, 0, False) if valid else None,
                "param_count": plugins.getParamCount(tr, s, False) if valid else 0,
            })
        except Exception:
            out.append({"slot": s, "valid": False, "name": None, "param_count": 0})
    return {"slots": out}


# ---- playlist --------------------------------------------------------------

def _pl_info(i):
    # getTrackHeight is NOT in FL's public API; use getattr so a missing
    # attribute doesn't blow up the whole call (the name is evaluated eagerly
    # as an argument, so `_safe(playlist.getTrackHeight, i)` would still raise).
    get_height = getattr(playlist, "getTrackHeight", None)
    return {
        "index": i,
        "name": _safe(playlist.getTrackName, i) or "",
        "color": _int_to_color_hex(_safe(playlist.getTrackColor, i) or 0),
        "is_muted": _safe(playlist.isTrackMuted, i) == 1,
        "is_solo": _safe(playlist.isTrackSolo, i) == 1,
        "height": _safe(get_height, i) if get_height else None,
    }


def h_playlist_count(_):
    return {"count": playlist.trackCount()}


def h_playlist_track_info(p):
    return _pl_info(int(p["track"]))


def h_playlist_all_tracks(p):
    include_empty = bool(p.get("include_empty", False))
    out = []
    for i in range(1, playlist.trackCount() + 1):
        info = _pl_info(i)
        if not include_empty and not info["name"]:
            continue
        out.append(info)
    return {"tracks": out}


def h_playlist_set_track_name(p):
    tr = int(p["track"]); name = p.get("name", "")
    playlist.setTrackName(tr, name)
    return _pl_info(tr)


def h_playlist_set_track_color(p):
    tr = int(p["track"])
    playlist.setTrackColor(tr, _color_to_int(p["color"]))
    return _pl_info(tr)


def h_playlist_mute_track(p):
    tr = int(p["track"]); muted = p.get("muted")
    # muteTrack(index, value=-1): -1 toggles, 0/1 set explicitly.
    playlist.muteTrack(tr, -1 if muted is None else (1 if muted else 0))
    return _pl_info(tr)


def h_playlist_solo_track(p):
    tr = int(p["track"]); solo = p.get("solo")
    # soloTrack(index, value=-1, inGroup=False): -1 toggles, 0/1 set explicitly.
    playlist.soloTrack(tr, -1 if solo is None else (1 if solo else 0))
    return _pl_info(tr)


def h_playlist_list_clips(p):
    track = p.get("track")
    clips = []
    # FL API: no direct clip enumeration for playlist exists; best effort via liveRange
    try:
        for t in (range(1, playlist.trackCount() + 1) if track is None else [int(track)]):
            # we cannot enumerate clips on a playlist track via the public Python API
            # so expose the track info only and mark this limitation
            pass
    except Exception:
        pass
    return {"clips": clips, "note": "Playlist clip enumeration is not exposed by the FL Python API. Use resource fl://project for pattern usage."}


def h_playlist_place_pattern(p):
    # There is no direct "place a pattern on a playlist track" API in FL.
    # Workaround: use the channel rack's paint on playlist via keystroke,
    # or expose via launchMapPages — not reliable. We document the limitation.
    return {"ok": False, "error": "playlist.placePattern is not yet supported by FL's Python API; use ui.showWindow('playlist') + manual placement or arrangement jumps."}


def h_playlist_delete_clip(p):
    return {"ok": False, "error": "playlist.deleteClip is not exposed by FL's Python API."}


def h_playlist_refresh(_):
    # FL's public API has no general "repaint the playlist" call (it repaints
    # automatically). refreshLiveClips only touches performance-mode clips.
    if hasattr(playlist, "refreshLiveClips"):
        _safe(playlist.refreshLiveClips, 0)
    return {"ok": True,
            "note": "FL repaints the playlist automatically; no general refresh "
                    "function is exposed by the public Python API."}


def h_playlist_list_markers(_):
    """FL's public API cannot enumerate existing markers — only jumpToMarker steps through them."""
    return {"markers": [],
            "ok": False,
            "note": "arrangement.* does not expose marker enumeration; use playlist_jump_marker instead."}


def h_playlist_add_marker(p):
    """Add an auto-time marker at `position_bars`."""
    pos_bars = float(p.get("position_bars", 0))
    name = p.get("name", "")
    try:
        ppq = general.getRecPPQ()
        ticks = int(round(pos_bars * ppq * 4))
        if hasattr(arrangement, "addAutoTimeMarker"):
            arrangement.addAutoTimeMarker(ticks, name)
            return {"ok": True, "position_bars": pos_bars, "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "arrangement.addAutoTimeMarker not available"}


def h_playlist_delete_marker(p):
    return {"ok": False, "error": "arrangement.* does not expose deleteMarker."}


# ---- arrangement -----------------------------------------------------------

def h_arr_current(_):
    """FL's public API doesn't enumerate arrangements; return what we can."""
    return {"index": 0, "name": "current",
            "note": "FL's Python API doesn't expose arrangement switching."}


def h_arr_list(_):
    return {"arrangements": [{"index": 0, "name": "current"}],
            "note": "FL's Python API doesn't expose arrangement enumeration."}


def h_arr_select(p):
    return {"ok": False, "error": "arrangement switching is not exposed by FL's Python API."}


def h_arr_jump_marker(p):
    direction = int(p.get("direction", 1))
    if hasattr(arrangement, "jumpToMarker"):
        arrangement.jumpToMarker(direction, False)
        return {"direction": direction}
    return {"ok": False, "error": "arrangement.jumpToMarker not available"}


def h_arr_play_time(_):
    try:
        return {
            "position_ticks": transport.getSongPos(2),
            "position_bars": transport.getSongPos(3),
            "position_seconds": transport.getSongPos(1),
        }
    except Exception:
        return {}


# ---- automation -------------------------------------------------------------
#
# Automation is recorded live: arm + play the transport, then write parameter
# values at the right musical times so FL captures an automation clip. The naive
# approach (time.sleep between points) would block FL's main thread inside a
# callback and FREEZE the whole UI for the duration. Instead we schedule each
# point as an absolute monotonic deadline and apply it from OnMidiMsg or OnIdle
# without ever sleeping — FL stays fully responsive while the clip records.

def _seconds_per_bar():
    bpm = mixer.getCurrentTempo() / 1000.0
    return (60.0 / max(1e-6, bpm)) * 4.0


def _rec_tempo(bpm):
    general.processRECEvent(
        midi.REC_Tempo,
        int(round(bpm * 1000)),
        midi.REC_Control | midi.REC_UpdateControl,
    )


def _enqueue_automation(points, apply_value, value_key="value", label=""):
    """Schedule `points` ([{time_bars, <value_key>}]) for callback processing.
    `apply_value(float)` writes one value. Returns immediately."""
    global _auto_recording
    if not points:
        return {"ok": False, "error": "no points"}
    try:
        spb = _seconds_per_bar()
        start = time.monotonic()
        sched = []
        for pt in points:
            t_bars = max(0.0, float(pt.get("time_bars", 0.0)))
            val = float(pt[value_key])
            sched.append((start + t_bars * spb, val))
        sched.sort(key=lambda x: x[0])

        if not _auto_recording:
            transport.record()   # arm
            transport.start()    # roll
            _auto_recording = True
        _auto_jobs.append({"sched": sched, "idx": 0, "apply": apply_value, "label": label})
        total_bars = max((float(pt.get("time_bars", 0.0)) for pt in points), default=0.0)
        return {
            "ok": True,
            "scheduled": len(sched),
            "label": label,
            "approx_duration_sec": round(total_bars * spb, 2),
            "mode": "async",
            "note": "Recording live without blocking FL's UI; the clip finishes "
                    "automatically after the last point.",
        }
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def _process_automation():
    """Apply any due automation points and stop recording when all jobs finish.
    Called from OnMidiMsg or OnIdle on FL's main thread. Never blocks."""
    global _auto_recording
    if not _auto_jobs:
        return
    now = time.monotonic()
    for job in _auto_jobs:
        sched = job["sched"]
        apply_value = job["apply"]
        while job["idx"] < len(sched) and sched[job["idx"]][0] <= now:
            try:
                apply_value(sched[job["idx"]][1])
            except Exception as e:
                _log("automation apply (%s) failed: %s" % (job.get("label"), e))
            job["idx"] += 1
    # Drop finished jobs.
    _auto_jobs[:] = [j for j in _auto_jobs if j["idx"] < len(j["sched"])]
    if not _auto_jobs and _auto_recording:
        try:
            transport.stop()
            transport.record()  # disarm
        except Exception:
            pass
        _auto_recording = False


def h_automation_record_tempo(p):
    return _enqueue_automation(p.get("points", []), _rec_tempo,
                               value_key="bpm", label="tempo")


def h_automation_record_channel_volume(p):
    ch = int(p["channel"])
    return _enqueue_automation(
        p.get("points", []),
        lambda v: channels.setChannelVolume(ch, v, 0, True),
        label="channel_volume")


def h_automation_record_channel_pan(p):
    ch = int(p["channel"])
    return _enqueue_automation(
        p.get("points", []),
        lambda v: channels.setChannelPan(ch, v, 0, True),
        label="channel_pan")


def h_automation_record_mixer_volume(p):
    tr = int(p["track"])
    return _enqueue_automation(
        p.get("points", []),
        lambda v: mixer.setTrackVolume(tr, v),
        label="mixer_volume")


def h_automation_record_plugin_param(p):
    idx = int(p["channel"]); slot = int(p.get("slot", -1))
    ug = (p.get("location", "channel") == "channel")
    param = int(p["param"])
    return _enqueue_automation(
        p.get("points", []),
        lambda v: plugins.setParamValue(v, param, idx, slot, 0, ug),
        label="plugin_param")


# ---- project ---------------------------------------------------------------

def h_project_metadata(_):
    return {
        "version": _safe(general.getVersion),
        "tempo": mixer.getCurrentTempo() / 1000.0,
        "ppq": _safe(general.getRecPPQ),
        "ppb": _safe(general.getRecPPB) if hasattr(general, "getRecPPB") else None,
        "channel_count": channels.channelCount(True),
        "mixer_tracks": mixer.trackCount(),
        "pattern_count": patterns.patternCount(),
        "selected_pattern": patterns.patternNumber(),
        "selected_channel": channels.selectedChannel(canBeNone=True, indexGlobal=True),
        "is_playing": transport.isPlaying() == 1,
        "is_recording": transport.isRecording() == 1,
        "loop_mode": "song" if transport.getLoopMode() == 1 else "pattern",
        "metronome": _safe(general.getUseMetronome) if hasattr(general, "getUseMetronome") else None,
        "has_unsaved_changes": bool(_safe(general.getChangedFlag)) if hasattr(general, "getChangedFlag") else None,
    }


def h_project_new(p):
    return {"ok": False, "error": "project.new requires UI interaction (File > New); not exposed by the Python API."}


def h_project_open(p):
    return {"ok": False, "error": "opening files requires UI interaction; not supported via API."}


def h_project_save(_):
    fpt = _fpt("FPT_Save")
    if fpt is None:
        return {"ok": False, "error": "midi.FPT_Save not available — user must press Ctrl+S."}
    transport.globalTransport(fpt, 1)
    return {"ok": True}


def h_project_save_as(p):
    fpt = _fpt("FPT_SaveNew")
    if fpt is None:
        return {"ok": False, "error": "midi.FPT_SaveNew not available."}
    transport.globalTransport(fpt, 1)
    return {"ok": True, "note": "FL will prompt for a filename"}


def h_project_undo(_):
    general.undoUp()
    return {"ok": True}


def h_project_redo(_):
    general.undoDown()
    return {"ok": True}


def h_project_undo_history(_):
    """FL's API exposes count + current position + a hint for the topmost entry,
    but NOT per-index names. Return what we can."""
    try:
        count = general.getUndoHistoryCount()
        pos = general.getUndoHistoryPos() if hasattr(general, "getUndoHistoryPos") else None
        last = general.getUndoHistoryLast() if hasattr(general, "getUndoHistoryLast") else None
        hint = general.getUndoLevelHint() if hasattr(general, "getUndoLevelHint") else None
        return {"count": count, "position": pos, "last": last, "hint": hint}
    except Exception as e:
        return {"count": 0, "error": str(e)}


def h_project_save_undo(p):
    general.saveUndo(p.get("name", "fLMCP edit"), int(p.get("flags", 0)))
    return {"ok": True}


def h_project_render(p):
    return {"ok": False, "error": "Rendering requires FL's render dialog; call ui.showWindow('playlist') then user triggers Ctrl+R."}


def h_project_version(_):
    """FL's getVersion() returns an int; convert to x.y.z string for convenience."""
    v = _safe(general.getVersion)
    if isinstance(v, int):
        return {"version_int": v,
                "version": "%d.%d.%d" % ((v >> 24) & 0xFF, (v >> 16) & 0xFF, v & 0xFFFF)}
    return {"version": v or "unknown"}


# ---- ui --------------------------------------------------------------------

# FL exposes widPluginEffect / widPluginGenerator (there is no single
# `widPlugin`). "plugin" defaults to the generator (instrument) editor, which is
# the usual intent for a channel plugin; the two specific names are also exposed.
_WIN_IDS = {
    "mixer": getattr(midi, "widMixer", 0),
    "channel_rack": getattr(midi, "widChannelRack", 1),
    "playlist": getattr(midi, "widPlaylist", 2),
    "piano_roll": getattr(midi, "widPianoRoll", 3),
    "browser": getattr(midi, "widBrowser", 4),
    "plugin": getattr(midi, "widPluginGenerator", 7),
    "plugin_generator": getattr(midi, "widPluginGenerator", 7),
    "plugin_effect": getattr(midi, "widPluginEffect", 6),
}


def h_ui_focused(_):
    # ui.getFocused(widget) returns whether THAT widget is focused, so scan the
    # known windows to find which one currently has focus.
    focused_name = None
    focused_id = None
    for name, wid in _WIN_IDS.items():
        try:
            if ui.getFocused(wid) == 1:
                focused_name = name
                focused_id = wid
                break
        except Exception:
            pass
    return {
        "focused_window": focused_name,
        "window_id": focused_id,
        "focused_form_id": _safe(ui.getFocusedFormID),
        "focused_form_caption": _safe(ui.getFocusedFormCaption),
        "in_popup_menu": bool(_safe(ui.isInPopupMenu) == 1),
    }


def h_ui_window_status(p):
    requested = p.get("name")
    names = [requested] if requested else list(_WIN_IDS.keys())
    windows = []
    for name in names:
        if name not in _WIN_IDS:
            continue
        wid = _WIN_IDS[name]
        windows.append({
            "name": name,
            "window_id": wid,
            "visible": bool(_safe(ui.getVisible, wid)),
            "focused": bool(_safe(ui.getFocused, wid)),
        })
    if requested and windows:
        return windows[0]
    return {"windows": windows}


def h_ui_show_window(p):
    wid = _WIN_IDS.get(p.get("name", "channel_rack"), 1)
    ui.showWindow(wid)
    if p.get("focus", True):
        try:
            ui.setFocused(wid)
        except Exception:
            pass
    return {"shown": p.get("name")}


def h_ui_hide_window(p):
    wid = _WIN_IDS.get(p.get("name", "channel_rack"), 1)
    try:
        ui.hideWindow(wid)
    except Exception:
        pass
    return {"hidden": p.get("name")}


def h_ui_hint(p):
    ui.setHintMsg(p.get("message", ""))
    return {"ok": True}


def h_ui_open_piano_roll(p):
    ch = int(p["channel"])
    if p.get("pattern") is not None:
        patterns.jumpToPattern(int(p["pattern"]))
    channels.selectOneChannel(ch, True)
    ui.showWindow(_WIN_IDS["piano_roll"])
    try:
        ui.setFocused(_WIN_IDS["piano_roll"])
    except Exception:
        pass
    return {"ok": True, "channel": ch}


def h_ui_selected_channel(_):
    idx = channels.selectedChannel(canBeNone=True, indexGlobal=True)
    if idx is None or idx < 0:
        return {"channel": None}
    return {"channel": _ch_info(idx)}


def h_ui_scroll_to_channel(p):
    # Best effort — there's no direct 'scrollTo' API for channel rack.
    channels.selectOneChannel(int(p["channel"]), True)
    return {"ok": True}


# ---- piano roll (staging only — real edit happens in pyscript via keystroke) ----

def _stage_piano_roll_request(request):
    # FL 2025's sub-interpreter has a broken os.mkdir that returns NULL (raising
    # SystemError) even when the directory already exists. The "Piano roll
    # scripts" folder always exists, so only attempt mkdir when it is missing,
    # and never let a mkdir quirk abort staging.
    try:
        if not PR_REQUEST.parent.exists():
            PR_REQUEST.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, SystemError):
        pass
    existing = []
    if PR_REQUEST.exists():
        try:
            data = json.loads(PR_REQUEST.read_text(encoding="utf-8"))
            if isinstance(data, list):
                existing = data
        except Exception:
            existing = []
    existing.append(request)
    PR_REQUEST.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def _prepare_piano_roll(channel, pattern):
    """Make sure the right channel's piano roll is open before keystroke is sent."""
    if pattern is not None:
        patterns.jumpToPattern(int(pattern))
    channels.selectOneChannel(int(channel), True)
    ui.showWindow(_WIN_IDS["piano_roll"])
    try:
        ui.setFocused(_WIN_IDS["piano_roll"])
    except Exception:
        pass


def _read_piano_roll_state():
    if not PR_STATE.exists():
        return None
    try:
        return json.loads(PR_STATE.read_text(encoding="utf-8"))
    except Exception:
        return None


def h_pianoroll_add_notes(p):
    _prepare_piano_roll(p["channel"], p.get("pattern"))
    if p.get("clear_first"):
        _stage_piano_roll_request({"action": "clear"})
    _stage_piano_roll_request({"action": "add_notes", "notes": p.get("notes", [])})
    return {"staged": True, "needs_keystroke": True, "request_file": str(PR_REQUEST)}


def h_pianoroll_add_chord(p):
    _prepare_piano_roll(p["channel"], p.get("pattern"))
    _stage_piano_roll_request({
        "action": "add_chord",
        "time": float(p.get("time_bars", 0.0)) * 4,
        "duration": float(p.get("duration_bars", 1.0)) * 4,
        "notes": [{"midi": n, "velocity": float(p.get("velocity", 0.8))} for n in p["midi_notes"]],
    })
    return {"staged": True, "needs_keystroke": True, "request_file": str(PR_REQUEST)}


def h_pianoroll_add_arpeggio(p):
    """Expand arpeggio to linear notes before staging."""
    notes_midi = list(p["midi_notes"])
    direction = p.get("direction", "up")
    if direction == "down":
        notes_midi.reverse()
    elif direction == "updown":
        notes_midi = notes_midi + notes_midi[-2:0:-1]
    elif direction == "random":
        import random
        random.shuffle(notes_midi)

    step_bars = float(p.get("step_bars", 0.25))
    dur_bars = float(p.get("note_duration_bars", 0.25))
    start_bars = float(p.get("time_bars", 0.0))
    repeats = int(p.get("repeats", 1))

    total_notes = len(notes_midi) * repeats
    out = []
    for i in range(total_notes):
        out.append({
            "midi": notes_midi[i % len(notes_midi)],
            "time": (start_bars + i * step_bars) * 4,  # quarter-notes for pyscript
            "duration": dur_bars * 4,
            "velocity": float(p.get("velocity", 0.8)),
        })
    _prepare_piano_roll(p["channel"], p.get("pattern"))
    _stage_piano_roll_request({"action": "add_notes", "notes": out})
    return {"staged": True, "needs_keystroke": True, "notes": len(out)}


def h_pianoroll_delete_notes(p):
    _prepare_piano_roll(p["channel"], p.get("pattern"))
    # pyscript expects time in quarter notes
    norm = [{"midi": n["midi"], "time": float(n["time_bars"]) * 4} for n in p.get("notes", [])]
    _stage_piano_roll_request({"action": "delete_notes", "notes": norm})
    return {"staged": True, "needs_keystroke": True}


def h_pianoroll_clear(p):
    _prepare_piano_roll(p["channel"], p.get("pattern"))
    _stage_piano_roll_request({"action": "clear"})
    return {"staged": True, "needs_keystroke": True}


def h_pianoroll_read(p):
    """The pyscript writes the current state file on every run. We return the last known state."""
    _prepare_piano_roll(p["channel"], p.get("pattern"))
    # Staging a no-op action so the pyscript refreshes the state file
    _stage_piano_roll_request({"action": "export_only"})
    state = _read_piano_roll_state()
    return {"staged": True, "needs_keystroke": True, "last_state": state}


def h_pianoroll_quantize(p):
    _prepare_piano_roll(p["channel"], p.get("pattern"))
    _stage_piano_roll_request({
        "action": "quantize",
        "grid": float(p.get("grid_bars", 0.25)) * 4,
        "strength": float(p.get("strength", 1.0)),
    })
    return {"staged": True, "needs_keystroke": True}


def h_pianoroll_transpose(p):
    _prepare_piano_roll(p["channel"], p.get("pattern"))
    _stage_piano_roll_request({"action": "transpose", "semitones": int(p.get("semitones", 0))})
    return {"staged": True, "needs_keystroke": True}


def h_pianoroll_humanize(p):
    _prepare_piano_roll(p["channel"], p.get("pattern"))
    _stage_piano_roll_request({
        "action": "humanize",
        "timing_jitter": float(p.get("timing_jitter_bars", 0.02)) * 4,
        "velocity_jitter": float(p.get("velocity_jitter", 0.1)),
    })
    return {"staged": True, "needs_keystroke": True}


def h_pianoroll_duplicate(p):
    _prepare_piano_roll(p["channel"], p.get("pattern"))
    _stage_piano_roll_request({
        "action": "duplicate",
        "source_time": float(p["source_time_bars"]) * 4,
        "length": float(p["length_bars"]) * 4,
        "dest_time": float(p["dest_time_bars"]) * 4,
    })
    return {"staged": True, "needs_keystroke": True}


# ----------------------------------------------------------------------------
# Handler table
# ----------------------------------------------------------------------------

_HANDLERS = {
    # meta
    "meta.ping": h_meta_ping,
    "meta.info": h_meta_info,
    # transport
    "transport.start": h_transport_start,
    "transport.stop": h_transport_stop,
    "transport.record": h_transport_record,
    "transport.status": h_transport_status,
    "transport.setPosition": h_transport_set_position,
    "transport.length": h_transport_length,
    "transport.setLoopMode": h_transport_set_loop_mode,
    "transport.setPlaybackSpeed": h_transport_set_playback_speed,
    "transport.setTempo": h_transport_set_tempo,
    "transport.tapTempo": h_transport_tap_tempo,
    "transport.setTimeSignature": h_transport_set_time_signature,
    "transport.toggleMetronome": h_transport_toggle_metronome,
    "transport.toggleCountdownBeforeRec": h_transport_toggle_countdown,
    "transport.jog": h_transport_jog,
    "transport.togglePunch": h_transport_toggle_punch,
    "transport.toggleOverdub": h_transport_toggle_overdub,
    "transport.toggleLoopRecording": h_transport_toggle_loop_recording,
    "transport.jumpMarker": h_transport_jump_marker,
    # patterns
    "patterns.count": h_patterns_count,
    "patterns.current": h_patterns_current,
    "patterns.list": h_patterns_list,
    "patterns.select": h_patterns_select,
    "patterns.create": h_patterns_create,
    "patterns.rename": h_patterns_rename,
    "patterns.setColor": h_patterns_set_color,
    "patterns.delete": h_patterns_delete,
    "patterns.clone": h_patterns_clone,
    "patterns.setLength": h_patterns_set_length,
    "patterns.findByName": h_patterns_find_by_name,
    "patterns.jumpNext": h_patterns_jump_next,
    "patterns.jumpPrev": h_patterns_jump_prev,
    # channels
    "channels.count": h_channels_count,
    "channels.info": h_channels_info,
    "channels.all": h_channels_all,
    "channels.selected": h_channels_selected,
    "channels.select": h_channels_select,
    "channels.setVolume": h_channels_set_volume,
    "channels.setPan": h_channels_set_pan,
    "channels.setPitch": h_channels_set_pitch,
    "channels.mute": h_channels_mute,
    "channels.solo": h_channels_solo,
    "channels.setName": h_channels_set_name,
    "channels.setColor": h_channels_set_color,
    "channels.routeToMixer": h_channels_route_to_mixer,
    "channels.triggerNote": h_channels_trigger_note,
    "channels.getGridBit": h_channels_get_grid_bit,
    "channels.setGridBit": h_channels_set_grid_bit,
    "channels.getStepSequence": h_channels_get_step_sequence,
    "channels.setStepSequence": h_channels_set_step_sequence,
    "channels.clearStepSequence": h_channels_clear_step_sequence,
    "channels.quickQuantize": h_channels_quick_quantize,
    # mixer
    "mixer.count": h_mixer_count,
    "mixer.trackInfo": h_mixer_track_info,
    "mixer.peaks": h_mixer_peaks,
    "mixer.allTracks": h_mixer_all_tracks,
    "mixer.setVolume": h_mixer_set_volume,
    "mixer.setPan": h_mixer_set_pan,
    "mixer.mute": h_mixer_mute,
    "mixer.solo": h_mixer_solo,
    "mixer.arm": h_mixer_arm,
    "mixer.setName": h_mixer_set_name,
    "mixer.setColor": h_mixer_set_color,
    "mixer.setStereoSep": h_mixer_set_stereo_sep,
    "mixer.setSendLevel": h_mixer_set_send_level,
    "mixer.route": h_mixer_route,
    "mixer.fxSlots": h_mixer_fx_slots,
    "mixer.select": h_mixer_select,
    "mixer.getEQ": h_mixer_get_eq,
    "mixer.setEQBand": h_mixer_set_eq_band,
    "mixer.linkChannelToTrack": h_mixer_link_channel,
    # plugins
    "plugins.isValid": h_plugins_is_valid,
    "plugins.name": h_plugins_name,
    "plugins.paramCount": h_plugins_param_count,
    "plugins.params": h_plugins_params,
    "plugins.getParam": h_plugins_get_param,
    "plugins.setParam": h_plugins_set_param,
    "plugins.findParam": h_plugins_find_param,
    "plugins.presetCount": h_plugins_preset_count,
    "plugins.nextPreset": h_plugins_next_preset,
    "plugins.prevPreset": h_plugins_prev_preset,
    "plugins.setPreset": h_plugins_set_preset,
    "plugins.showEditor": h_plugins_show_editor,
    "plugins.listMixerTrack": h_plugins_list_mixer_track,
    # playlist
    "playlist.trackCount": h_playlist_count,
    "playlist.trackInfo": h_playlist_track_info,
    "playlist.allTracks": h_playlist_all_tracks,
    "playlist.setTrackName": h_playlist_set_track_name,
    "playlist.setTrackColor": h_playlist_set_track_color,
    "playlist.muteTrack": h_playlist_mute_track,
    "playlist.soloTrack": h_playlist_solo_track,
    "playlist.listClips": h_playlist_list_clips,
    "playlist.placePattern": h_playlist_place_pattern,
    "playlist.deleteClip": h_playlist_delete_clip,
    "playlist.refresh": h_playlist_refresh,
    "playlist.listMarkers": h_playlist_list_markers,
    "playlist.addMarker": h_playlist_add_marker,
    "playlist.deleteMarker": h_playlist_delete_marker,
    # arrangement
    "arrangement.current": h_arr_current,
    "arrangement.list": h_arr_list,
    "arrangement.select": h_arr_select,
    "arrangement.jumpMarker": h_arr_jump_marker,
    "arrangement.playTime": h_arr_play_time,
    # automation
    "automation.recordTempo": h_automation_record_tempo,
    "automation.recordChannelVolume": h_automation_record_channel_volume,
    "automation.recordChannelPan": h_automation_record_channel_pan,
    "automation.recordMixerVolume": h_automation_record_mixer_volume,
    "automation.recordPluginParam": h_automation_record_plugin_param,
    # project
    "project.metadata": h_project_metadata,
    "project.new": h_project_new,
    "project.open": h_project_open,
    "project.save": h_project_save,
    "project.saveAs": h_project_save_as,
    "project.undo": h_project_undo,
    "project.redo": h_project_redo,
    "project.undoHistory": h_project_undo_history,
    "project.saveUndo": h_project_save_undo,
    "project.render": h_project_render,
    "project.version": h_project_version,
    # ui
    "ui.focusedWindow": h_ui_focused,
    "ui.windowStatus": h_ui_window_status,
    "ui.showWindow": h_ui_show_window,
    "ui.hideWindow": h_ui_hide_window,
    "ui.hint": h_ui_hint,
    "ui.openPianoRoll": h_ui_open_piano_roll,
    "ui.selectedChannel": h_ui_selected_channel,
    "ui.scrollToChannel": h_ui_scroll_to_channel,
    # piano roll (stage)
    "pianoroll.addNotes": h_pianoroll_add_notes,
    "pianoroll.addChord": h_pianoroll_add_chord,
    "pianoroll.addArpeggio": h_pianoroll_add_arpeggio,
    "pianoroll.deleteNotes": h_pianoroll_delete_notes,
    "pianoroll.clear": h_pianoroll_clear,
    "pianoroll.read": h_pianoroll_read,
    "pianoroll.quantize": h_pianoroll_quantize,
    "pianoroll.transpose": h_pianoroll_transpose,
    "pianoroll.humanize": h_pianoroll_humanize,
    "pianoroll.duplicate": h_pianoroll_duplicate,
}


# ----------------------------------------------------------------------------
# FL Studio callbacks
# ----------------------------------------------------------------------------

def OnInit():
    global _last_request_id
    _log("initializing — script dir: %s" % SCRIPT_DIR)
    _log("FL version: %s" % _safe(general.getVersion))
    old_request = _read_json_file(COMMAND_FILE)
    if isinstance(old_request, dict) and isinstance(old_request.get("id"), int):
        _last_request_id = old_request["id"]
    _log("file+MIDI bridge ready; last_request_id=%d" % _last_request_id)


def OnDeInit():
    _log("deinit")


def OnIdle():
    """Fallback poll for FL versions that still emit OnIdle."""
    global _idle_tick
    _idle_tick += 1
    _process_request_file()
    try:
        _process_automation()
    except Exception as e:
        _log("automation processing error: %s" % e)
    try:
        _process_note_offs()
    except Exception as e:
        _log("note-off processing error: %s" % e)


def OnMidiIn(event):
    event.handled = False


def OnMidiMsg(event):
    is_wake = (
        getattr(event, "status", None) == WAKE_STATUS
        and getattr(event, "data1", None) == WAKE_DATA1
        and getattr(event, "data2", None) == WAKE_DATA2
    )
    if is_wake:
        _process_request_file()
        try:
            _process_automation()
        except Exception as e:
            _log("automation processing error: %s" % e)
        try:
            _process_note_offs()
        except Exception as e:
            _log("note-off processing error: %s" % e)
        event.handled = True
    else:
        event.handled = False


def OnRefresh(flags):
    pass


def OnProjectLoad(status):
    pass
