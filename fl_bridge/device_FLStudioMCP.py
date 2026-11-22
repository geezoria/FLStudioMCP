# name=fLMCP Bridge
# supportedDevices=fLMCP Bridge
# url=https://github.com/geezoria/FLStudioMCP
# receiveFrom=fLMCP Bridge

"""FL Studio controller for the fLMCP protocol 2 file transport.

FL executes commands and advances automation from its controller callbacks.
OnIdle polls the command file, and a reserved MIDI CC can also wake the bridge.
The companion ComposeWithLLM.pyscript handles piano-roll file requests separately.
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

BRIDGE_VERSION = "0.4.0"
PROTOCOL = 2
MAX_COMMAND_BYTES = 16 * 1024 * 1024
MAX_BATCH_ACTIONS = 32
MAX_LOG_BYTES = 64 * 1024
WAKE_STATUS = 0xBF
WAKE_CONTROLLER = 119


def _script_dir():
    script_file = globals().get("__file__")
    if script_file:
        try:
            path = Path(script_file)
            return path.parent if path.is_absolute() else Path(os.path.abspath(path)).parent
        except Exception:
            pass
    try:
        home = Path.home()
    except Exception:
        home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
    return home / "Documents" / "Image-Line" / "FL Studio" / "Settings" / "Hardware" / "fLMCP Bridge"


SCRIPT_DIR = _script_dir()
COMMAND_FILE = SCRIPT_DIR / "mcp_command.json"
RESPONSE_FILE = SCRIPT_DIR / "mcp_response.json"
HEARTBEAT_FILE = SCRIPT_DIR / "mcp_heartbeat.json"
LOG_FILE = SCRIPT_DIR / "fLMCP_log.txt"
PR_REQUEST = SCRIPT_DIR.parent.parent / "Piano roll scripts" / "fLMCP_request.json"
PR_STATE = SCRIPT_DIR.parent.parent / "Piano roll scripts" / "fLMCP_state.json"


# ----------------------------------------------------------------------------
# Global state
# ----------------------------------------------------------------------------

_started_at = time.monotonic()
_idle_calls = 0
_midi_wakes = 0
_last_id = 0
_last_heartbeat = float("-inf")
_last_command = None
_processing = False
_pending_response = None
_auto_jobs = []          # pending non-blocking automation jobs (see automation section)
_auto_recording = False  # True while an automation clip is being recorded live


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _log(msg):
    try:
        line = ("[fLMCP] %s\n" % msg).encode("utf-8", errors="replace")[:MAX_LOG_BYTES]
        try:
            with open(LOG_FILE, "rb") as log_file:
                length = len(log_file.read(MAX_LOG_BYTES))
        except Exception:
            length = 0
        mode = "wb" if length + len(line) > MAX_LOG_BYTES else "ab"
        with open(LOG_FILE, mode) as log_file:
            log_file.write(line)
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


def _api_version():
    version = _safe(general.getVersion)
    return version if isinstance(version, int) else 0


# ----------------------------------------------------------------------------
# File transport (standalone because FL loads this script directly)
# ----------------------------------------------------------------------------

def _read_command():
    global _last_command
    try:
        with open(COMMAND_FILE, "rb") as command_file:
            raw = command_file.read(MAX_COMMAND_BYTES + 1)
        if raw and raw != _last_command and len(raw) <= MAX_COMMAND_BYTES:
            request = json.loads(raw.decode("utf-8"))
            if isinstance(request, dict):
                _last_command = raw
                return request
    except Exception:
        pass
    return {}


def _write_json(path, data):
    body = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    with open(path, "w", encoding="utf-8") as output:
        output.write(body)


def _bridge_status():
    fl_version = _safe(lambda: ui.getVersion())
    return {
        "bridge_version": BRIDGE_VERSION,
        "protocol": PROTOCOL,
        "api_version": _safe(lambda: general.getVersion()),
        "fl_version": fl_version if isinstance(fl_version, str) and fl_version else "unknown",
        "idle_calls": _idle_calls,
        "midi_wakes": _midi_wakes,
    }


def _write_heartbeat(*, force=False, stopped=False):
    global _last_heartbeat
    now = time.monotonic()
    if not force and now - _last_heartbeat < 1.0:
        return
    _last_heartbeat = now
    try:
        _write_json(HEARTBEAT_FILE, {
            **_bridge_status(),
            "pid": _safe(lambda: os.getpid()),
            "last_id": _last_id,
            "ts": 0 if stopped else time.time(),
        })
    except Exception as e:
        _log("heartbeat write failed: %s" % e)


def _action_response(request, *, include_traceback=True):
    try:
        if not isinstance(request, dict):
            raise ValueError("action must be an object")
        action = request.get("action")
        params = request.get("params", {})
        if not isinstance(action, str) or not isinstance(params, dict):
            raise ValueError("expected a string action and an object params")
        return {"ok": True, "result": _execute(action, params)}
    except Exception as e:
        _log("action failed: %s" % e)
        response = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        if include_traceback:
            response["traceback"] = traceback.format_exc(limit=3)
        return response


def _poll_command():
    global _last_id, _pending_response
    request = _read_command()
    request_id = request.get("id")
    if type(request_id) is not int or request_id <= _last_id:
        return
    _last_id = request_id  # Record before dispatch, even if writing the response fails.
    start = time.monotonic()
    if request.get("protocol") != PROTOCOL:
        response = {"ok": False, "error": "expected protocol 2"}
    elif "batch" in request:
        batch = request["batch"]
        if not isinstance(batch, list) or len(batch) > MAX_BATCH_ACTIONS:
            response = {"ok": False, "error": "expected a batch of at most %d actions" % MAX_BATCH_ACTIONS}
        else:
            response = {"ok": True, "results": [
                _action_response(item, include_traceback=False) for item in batch
            ]}
    else:
        response = _action_response(request)
    elapsed_ms = round((time.monotonic() - start) * 1000, 3)
    response["id"] = request_id
    if "results" not in response:
        response["elapsed_ms"] = elapsed_ms
    try:
        _pending_response = json.dumps(response, ensure_ascii=True, separators=(",", ":"))
    except Exception as e:
        _pending_response = json.dumps({
            "id": request_id, "ok": False, "error": "cannot serialize response: %s" % e,
            "elapsed_ms": elapsed_ms,
        })


def _run_bridge_tick():
    global _processing, _pending_response
    if _processing:
        return
    _processing = True
    try:
        _poll_command()
        if _pending_response is not None:
            try:
                with open(RESPONSE_FILE, "w", encoding="utf-8") as response_file:
                    response_file.write(_pending_response)
                _pending_response = None
            except Exception as e:
                _log("response write failed: %s" % e)
        _process_automation()
    except Exception as e:
        _log("bridge tick failed: %s" % e)
    finally:
        try:
            _write_heartbeat()
        finally:
            _processing = False


# ----------------------------------------------------------------------------
# Handlers — executed on FL's main thread
# ----------------------------------------------------------------------------

def _execute(action, params):
    """Dispatch action -> result dict. Raises on error."""
    h = _HANDLERS.get(action)
    if h is None:
        raise ValueError("unknown action: %s" % action)
    return h(params or {})


# ---- meta -------------------------------------------------------------------

_API_MODULES = (
    "arrangement", "channels", "device", "general", "midi", "mixer",
    "patterns", "playlist", "plugins", "transport", "ui",
)
_OPTIONAL_API_MODULES = ("screen", "launchMapPages", "utils", "enveditor")


def h_meta_ping(_):
    return {
        "ok": True,
        **_bridge_status(),
        "uptime_sec": round(time.monotonic() - _started_at, 1),
        "script_dir": str(SCRIPT_DIR),
    }


def h_meta_info(_):
    return {
        **_bridge_status(),
        "api_modules": list(_API_MODULES),
        "script_dir": str(SCRIPT_DIR),
        "transport": "file",
        "command_file": str(COMMAND_FILE),
        "response_file": str(RESPONSE_FILE),
        "heartbeat_file": str(HEARTBEAT_FILE),
    }


def h_meta_introspect(params):
    requested = params.get("modules")
    if requested is not None and (
        not isinstance(requested, list) or any(not isinstance(name, str) for name in requested)
    ):
        raise ValueError("modules must be a list of module names")
    modules = {}
    for name in _API_MODULES + _OPTIONAL_API_MODULES:
        if requested is not None and name not in requested:
            continue
        module = globals().get(name) if name in _API_MODULES else _safe(__import__, name)
        if module is None:
            continue
        callables = []
        constants = {}
        for attribute in dir(module):
            if attribute.startswith("_"):
                continue
            value = _safe(getattr, module, attribute)
            if callable(value):
                doc = _safe(getattr, value, "__doc__")
                callables.append({"name": attribute, "doc": doc[:400] if isinstance(doc, str) else ""})
            elif name == "midi" and type(value) is int:
                constants[attribute] = value
        modules[name] = {"callables": callables}
        if name == "midi":
            modules[name]["constants"] = constants
    return {**_bridge_status(), "python_version": sys.version, "modules": modules}


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


# ---- patterns ---------------------------------------------------------------

def _pattern_max():
    return int(_safe(patterns.patternMax) or 999) if hasattr(patterns, "patternMax") else 999


def _check_pattern_index(index):
    top = _pattern_max()
    if not 1 <= index <= top:
        raise ValueError("pattern index %d out of range (1-%d, 1-based)" % (index, top))
    return index


def _pattern_in_use(index):
    # patternCount() is not an index bound: it is 0 in a fresh project even
    # though "Pattern 1" exists. A pattern is in use once it has content
    # (isPatternDefault, API 23+) or a non-default name.
    if patterns.getPatternName(index) != "Pattern %d" % index:
        return True
    return not patterns.isPatternDefault(index)


def _used_patterns():
    """1-based indices of patterns with content or a custom name, ascending."""
    if not hasattr(patterns, "isPatternDefault"):
        return list(range(1, patterns.patternCount() + 1))
    return [i for i in range(1, _pattern_max() + 1) if _pattern_in_use(i)]


def h_patterns_count(_):
    used = _used_patterns()
    return {"count": len(used), "last_index": used[-1] if used else 0}


def h_patterns_current(_):
    idx = patterns.patternNumber()
    return {"index": idx, "name": patterns.getPatternName(idx)}


def h_patterns_list(_):
    indices = sorted(set(_used_patterns()) | {patterns.patternNumber()})
    out = []
    for i in indices:
        out.append({
            "index": i,
            "name": patterns.getPatternName(i),
            "color": _int_to_color_hex(patterns.getPatternColor(i)),
            "length_steps": _safe(patterns.getPatternLength, i),
        })
    return {"patterns": out}


def h_patterns_select(p):
    idx = _check_pattern_index(int(p.get("index", 1)))
    patterns.jumpToPattern(idx)
    return {"selected": idx, "name": patterns.getPatternName(idx)}


def h_patterns_create(p):
    name = p.get("name", "")
    used = _used_patterns()
    new_idx = (used[-1] if used else 0) + 1
    _check_pattern_index(new_idx)
    patterns.setPatternName(new_idx, name or ("Pattern %d" % new_idx))
    return {"index": new_idx, "name": patterns.getPatternName(new_idx)}


def h_patterns_rename(p):
    idx = _check_pattern_index(int(p["index"])); name = p.get("name", "")
    patterns.setPatternName(idx, name)
    return {"index": idx, "name": patterns.getPatternName(idx)}


def h_patterns_set_color(p):
    idx = _check_pattern_index(int(p["index"])); color = _color_to_int(p.get("color", "#888888"))
    patterns.setPatternColor(idx, color)
    return {"index": idx, "color": _int_to_color_hex(patterns.getPatternColor(idx))}


def h_patterns_delete(p):
    """FL's public Python API has no deletePattern(). Workaround: rename to empty
    and mark as unused — the pattern stays in the pool but is effectively hidden."""
    idx = _check_pattern_index(int(p["index"]))
    patterns.setPatternName(idx, "")
    return {"ok": False,
            "soft_deleted": idx,
            "note": "FL Python API does not expose deletePattern; pattern was renamed to empty."}


def h_patterns_clone(p):
    """clonePattern(index) copies into the next free slot and selects the copy;
    FL 2026 (API 43+) also accepts a destination index."""
    src = _check_pattern_index(int(p["index"]))
    dest = p.get("dest_index")
    before = patterns.patternNumber()
    if dest is not None:
        dest = _check_pattern_index(int(dest))
        if _api_version() < 43:
            return _unsupported("Cloning into a chosen pattern", "FL Studio 2026 (26.1.1+)")
        patterns.clonePattern(src, dest)
        new_idx = dest
    else:
        patterns.clonePattern(src)
        new_idx = patterns.patternNumber()
        if new_idx in (before, src):
            new_idx = _used_patterns()[-1]
    patterns.setPatternName(new_idx, p.get("new_name") or (patterns.getPatternName(src) + " (copy)"))
    return {"source_index": src, "new_index": new_idx, "name": patterns.getPatternName(new_idx)}


STEPS_PER_BEAT = 4  # an FL step is a 16th note


def _beats_per_bar():
    ppq = _safe(general.getRecPPQ)
    ppb = _safe(general.getRecPPB) if hasattr(general, "getRecPPB") else None
    if ppq and ppb:
        return max(1, int(round(ppb / ppq)))
    return 4


def _unsupported(feature, needs):
    return {"ok": False, "error": feature + " is not available in this FL Studio version",
            "hint": "Requires " + needs + ". Use fl_introspect to see what this FL build exposes."}


def h_patterns_set_length(p):
    """patterns.setPatternLength(index, steps) exists from FL 2026 (26.1).

    Verified live on 26.1.7: the unit is steps (16 = one 4/4 bar), not beats as
    the API stubs claim.
    """
    index = _check_pattern_index(int(p["index"]))
    if not hasattr(patterns, "setPatternLength"):
        return _unsupported("Setting pattern length", "FL Studio 2026 (26.1+)")
    per_bar = _beats_per_bar() * STEPS_PER_BEAT
    if p.get("steps") is not None:
        steps = float(p["steps"])
    elif p.get("beats") is not None:
        steps = float(p["beats"]) * STEPS_PER_BEAT
    else:
        steps = float(p["bars"]) * per_bar
    steps = int(round(steps))
    if steps < 1:
        raise ValueError("length must be at least one step")
    patterns.setPatternLength(index, steps)
    length = _safe(patterns.getPatternLength, index)
    return {"ok": True, "index": index, "requested_steps": steps, "length_steps": length,
            "length_beats": round(length / STEPS_PER_BEAT, 3) if length else None,
            "length_bars": round(length / per_bar, 3) if length else None,
            "steps_per_bar": per_bar}


def h_patterns_clear(p):
    """patterns.clearPattern(index) exists from FL 2026 (26.1.3).

    FL shows a modal Confirm dialog and this call returns only once the user
    answers it, so the server reports a pending confirmation on timeout.
    """
    index = _check_pattern_index(int(p["index"]))
    if not hasattr(patterns, "clearPattern"):
        return _unsupported("Clearing a pattern", "FL Studio 2026 (26.1.3+)")
    patterns.clearPattern(index)
    return {"ok": True, "index": index, "name": patterns.getPatternName(index),
            "cleared": bool(_safe(patterns.isPatternDefault, index))
            if hasattr(patterns, "isPatternDefault") else None}


def h_patterns_find_by_name(p):
    target = (p.get("name") or "").lower().strip()
    for i in _used_patterns():
        if patterns.getPatternName(i).lower() == target:
            return {"index": i, "name": patterns.getPatternName(i)}
    return {"index": None, "name": None}


def h_patterns_jump_next(_):
    patterns.jumpToPattern(min(patterns.patternNumber() + 1, _pattern_max()))
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
        "type": _safe(channels.getChannelType, i, use_global),
    }


def h_channels_count(p):
    return {"count": channels.channelCount(bool(p.get("global_count", True)))}


def h_channels_get_swing(_):
    """channels.getSwing() (FL 2026, API 42): the Channel rack's global swing, 0..1."""
    if not hasattr(channels, "getSwing"):
        return _unsupported("Channel rack swing", "FL Studio 2026 (26.1+)")
    return {"ok": True, "swing": round(float(channels.getSwing()), 4)}


def h_channels_set_swing(p):
    if not hasattr(channels, "setSwing"):
        return _unsupported("Channel rack swing", "FL Studio 2026 (26.1+)")
    amount = max(0.0, min(1.0, float(p["amount"])))
    channels.setSwing(amount)
    return {"ok": True, "swing": round(float(channels.getSwing()), 4)}


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
    channels.setChannelName(idx, name)
    return _ch_info(idx)


def h_channels_set_color(p):
    idx = int(p["index"])
    channels.setChannelColor(idx, _color_to_int(p["color"]))
    return _ch_info(idx)


def h_channels_route_to_mixer(p):
    idx = int(p["index"]); tr = int(p["mixer_track"])
    channels.setTargetFxTrack(idx, tr)
    return {"index": idx, "mixer_track": channels.getTargetFxTrack(idx, True)}


def h_channels_trigger_note(p):
    idx = int(p["index"]); note = int(p.get("note", 60))
    vel = int(p.get("velocity", 100))
    midi_ch = int(p.get("midi_channel", -1))
    channels.midiNoteOn(idx, note, vel, midi_ch)
    return {"triggered": True, "note": note}


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
    # trackCount() includes Master and the hidden "Current" track; FL 2025.1+
    # getTrackCount() returns only the inserts.
    result = {"count": mixer.trackCount()}
    if hasattr(mixer, "getTrackCount"):
        result["insert_count"] = _safe(mixer.getTrackCount)
    return result


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
    select = bool(p.get("select", False))
    if hasattr(mixer, "linkChannelToTrack"):
        mixer.linkChannelToTrack(ch, tr, select)
    else:
        channels.setTargetFxTrack(ch, tr)
    return {"channel": ch, "track": tr}


# ---- plugins ---------------------------------------------------------------

def _resolve_plugin_loc(p):
    location = p.get("location", "channel")
    slot = int(p.get("slot", -1))
    index = int(p["index"])
    # plugins.* API: (index, slot, useGlobalIndex)
    useGlobal = (location == "channel")
    return index, slot, useGlobal


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
                # getParamValueString has a pickupMode arg BEFORE useGlobalIndex.
                "value_string": plugins.getParamValueString(i, idx, slot, 0, ug),
            })
        except Exception:
            pass
    return {"total": n, "params": out}


def h_plugins_get_param(p):
    idx, slot, ug = _resolve_plugin_loc(p)
    pid = int(p["param"])
    return {
        "value": plugins.getParamValue(pid, idx, slot, ug),
        "value_string": plugins.getParamValueString(pid, idx, slot, 0, ug),
    }


def h_plugins_set_param(p):
    idx, slot, ug = _resolve_plugin_loc(p)
    pid = int(p["param"]); v = float(p["value"])
    # setParamValue(value, paramIndex, index, slotIndex, pickupMode, useGlobalIndex)
    # — pickupMode comes BEFORE useGlobalIndex; keep pickup off (0).
    plugins.setParamValue(v, pid, idx, slot, 0, ug)
    return {
        "value": plugins.getParamValue(pid, idx, slot, ug),
        "value_string": plugins.getParamValueString(pid, idx, slot, 0, ug),
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
    for i in range(playlist.trackCount()):
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
        for t in (range(playlist.trackCount()) if track is None else [int(track)]):
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
# approach (time.sleep between points) would block FL's main thread inside
# OnIdle and FREEZE the whole UI for the duration. Instead we schedule each
# point as an absolute monotonic deadline and apply it from a callback without
# sleeping — FL stays fully responsive while the clip records.

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
    Called from idle and MIDI wake callbacks. Never sleeps."""
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
        **{key: _safe(getattr(general, fn)) for key, fn in (
            ("title", "getProjectTitle"), ("author", "getProjectAuthor"), ("genre", "getProjectGenre"))
           if hasattr(general, fn)},
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
        "in_popup_menu": bool(_safe(ui.isInPopupMenu) == 1),
    }


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


def h_ui_show_picker(p):
    """ui.showPicker(mode, category) exists from FL 2026 (26.1): 0 = plugins, 1 = project."""
    if not hasattr(ui, "showPicker"):
        return _unsupported("The picker panel", "FL Studio 2026 (26.1+)")
    modes = {"plugin": 0, "plugins": 0, "project": 1}
    mode = modes.get(str(p.get("mode", "plugin")).lower())
    if mode is None:
        raise ValueError("mode must be 'plugin' or 'project'")
    ui.showPicker(mode, int(p.get("category", -1)))
    return {"ok": True, "shown": "plugin" if mode == 0 else "project"}


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
    try:
        with open(PR_REQUEST, "r", encoding="utf-8") as request_file:
            existing = json.load(request_file)
    except Exception:
        existing = []
    if not isinstance(existing, list):
        existing = []
    existing.append(request)
    _write_json(PR_REQUEST, existing)


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
    try:
        with open(PR_STATE, "r", encoding="utf-8") as state_file:
            return json.load(state_file)
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
    "meta.introspect": h_meta_introspect,
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
    "patterns.clear": h_patterns_clear,
    "patterns.findByName": h_patterns_find_by_name,
    "patterns.jumpNext": h_patterns_jump_next,
    "patterns.jumpPrev": h_patterns_jump_prev,
    # channels
    "channels.count": h_channels_count,
    "channels.getSwing": h_channels_get_swing,
    "channels.setSwing": h_channels_set_swing,
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
    "ui.showWindow": h_ui_show_window,
    "ui.hideWindow": h_ui_hide_window,
    "ui.hint": h_ui_hint,
    "ui.showPicker": h_ui_show_picker,
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
    global _started_at, _idle_calls, _midi_wakes, _last_id, _pending_response
    try:
        _started_at = time.monotonic()
        _idle_calls = _midi_wakes = 0
        _pending_response = None
        # Suppress any command left by a previous controller session.
        old_id = _read_command().get("id")
        if type(old_id) is int:
            _last_id = max(_last_id, old_id)
        _write_heartbeat(force=True)
        _log("bridge %s ready in %s" % (BRIDGE_VERSION, SCRIPT_DIR))
    except Exception as e:
        _log("initialization failed: %s" % e)


def OnDeInit():
    try:
        _write_heartbeat(force=True, stopped=True)
        _log("bridge stopped")
    except Exception as e:
        _log("deinitialization failed: %s" % e)


def OnIdle():
    global _idle_calls
    try:
        _idle_calls += 1
        _run_bridge_tick()
    except Exception as e:
        _log("idle callback failed: %s" % e)


def OnMidiMsg(event):
    global _midi_wakes
    try:
        if event.status == WAKE_STATUS and event.data1 == WAKE_CONTROLLER and 1 <= event.data2 <= 127:
            event.handled = True
            _midi_wakes += 1
            _run_bridge_tick()
    except Exception as e:
        _log("MIDI callback failed: %s" % e)
