"""Live check: drive fLMCP's tools against a running FL Studio, through the real MCP server.

    python scripts/live_check.py            # read-only tools only
    python scripts/live_check.py --write    # also edits, each one restored afterwards

Run it on a scratch project. With --write it changes channel 0, mixer
insert 1 and the last two inserts, playlist track 1, the tempo and the swing, then puts
them back. It also adds an empty "fLMCP check" pattern, writes notes into it
through the piano roll of --channel (default: the first plugin channel, else
0), and soft-deletes it again. FL can't delete patterns, so the empty pattern
slot stays.

Exit status: 0 when every check passed, 1 on a failure, 2 when FL isn't reachable.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from typing import Any

from anyio import run
from mcp.client import Client

from fl_studio_mcp.server import build_app

SKIP_PREFIXES = ("voice_", "audio_", "song_")  # need a microphone or audio files
SKIP_READS = {"piano_roll_read",  # needs a channel; checked in piano_roll()
              "mixer_peak_watch"}  # plays for 8 s by default; checked with 1 s in reads()
NOT_SUPPORTED = ("not exposed", "not available", "not supported", "requires fl studio",
                 "fl studio 2026", "unsupported")


class Check:
    def __init__(self, client: Client) -> None:
        self.client = client
        self.results: list[tuple[str, str, str]] = []

    async def call(self, tool: str, /, **args: Any) -> dict[str, Any]:
        """Call a tool; returns its result dict, or {"_error": text} when it raised."""
        res = await self.client.call_tool(tool, args)
        text = res.content[0].text if res.content else ""
        if res.is_error:
            return {"_error": text}
        data = res.structured_content
        if not isinstance(data, dict):
            try:
                data = json.loads(text)
            except ValueError:
                return {"_text": text}
        return data.get("result", data) if isinstance(data, dict) else {"_value": data}

    def record(self, name: str, ok: bool, detail: str = "", status: str | None = None) -> None:
        status = status or ("PASS" if ok else "FAIL")
        self.results.append((status, name, detail))
        print(f"{status:4}  {name:34} {detail}"[:160], flush=True)

    async def expect(self, tool: str, check=None, /, label: str | None = None, **args: Any) -> dict:
        """Call `tool`; it passes unless it raised, returned ok=false or failed `check`."""
        data = await self.call(tool, **args)
        label = label or tool
        if "_error" in data:
            self.record(label, False, data["_error"].splitlines()[0])
        elif isinstance(data, dict) and data.get("ok") is False:
            reason = str(data.get("error") or data.get("note") or data)
            unsupported = any(s in reason.lower() for s in NOT_SUPPORTED)
            self.record(label, False, reason, "N/A" if unsupported else None)
        elif check is not None and not check(data):
            self.record(label, False, "unexpected result: " + json.dumps(data)[:120])
        else:
            self.record(label, True)
        return data


def _notes(data: dict) -> list[tuple[int, float]]:
    state = data.get("state") or {}
    return sorted((n["midi"], round(n["time_bars"], 2)) for n in state.get("notes", []))


async def reads(c: Check) -> dict[str, dict]:
    tools = (await c.client.list_tools()).tools
    seen: dict[str, dict] = {}
    for tool in tools:
        hints = tool.annotations
        required = (tool.input_schema or {}).get("required") or []
        skipped = tool.name.startswith(SKIP_PREFIXES) or tool.name in SKIP_READS
        if not (hints and hints.read_only_hint) or required or skipped:
            continue
        seen[tool.name] = await c.expect(tool.name)
    for name, args in [
        ("channel_info", {"index": 0}), ("channel_get_step_sequence", {"index": 0}),
        ("channel_get_grid_bit", {"index": 0, "position": 0}),
        ("mixer_peak_watch", {"seconds": 1}),
        ("mixer_track_info", {"track": 1}), ("mixer_get_eq", {"track": 1}),
        ("mixer_fx_slots", {"track": 0}), ("plugin_list_mixer_track", {"track": 0}),
        ("playlist_track_info", {"track": 1}), ("pattern_find_by_name", {"name": "Pattern 1"}),
    ]:
        seen[name] = await c.expect(name, **args)
    return seen


async def restored(c: Check, tool: str, change: dict, restore: dict, verify=None) -> None:
    """Apply `change`, check it with `verify` (an async read returning bool), then restore."""
    data = await c.call(tool, **change)
    ok = "_error" not in data and data.get("ok") is not False
    if ok and verify is not None:
        ok = await verify()
    back = await c.call(tool, **restore)
    ok = ok and "_error" not in back and back.get("ok") is not False
    c.record(tool, ok, "" if ok else json.dumps(data)[:120])


async def writes(c: Check, seen: dict[str, dict]) -> None:
    ch = seen["channel_info"]
    mx = seen["mixer_track_info"]
    top = seen["mixer_count"]["count"] - 2  # last insert; the count includes Master and "Current"
    pl = seen["playlist_track_info"]
    bpm = seen["transport_status"].get("bpm", 130.0)
    swing = (seen.get("channel_get_swing") or {}).get("swing")

    async def channel(field):
        return (await c.call("channel_info", index=0)).get(field)

    async def mixer(field):
        return (await c.call("mixer_track_info", track=1)).get(field)

    await restored(c, "transport_set_tempo", {"bpm": 128.5}, {"bpm": bpm},
                   lambda: _eq(c.call("transport_status"), "bpm", 128.5))
    await restored(c, "transport_set_position", {"position": 2, "unit": "bars"},
                   {"position": 0, "unit": "bars"})
    await restored(c, "channel_set_volume", {"index": 0, "volume": 0.5},
                   {"index": 0, "volume": ch["volume"]}, lambda: _near(channel("volume"), 0.5))
    await restored(c, "channel_set_pan", {"index": 0, "pan": 0.3}, {"index": 0, "pan": ch["pan"]},
                   lambda: _near(channel("pan"), 0.3))
    await restored(c, "channel_set_pitch", {"index": 0, "semitones": 2},
                   {"index": 0, "semitones": ch["pitch_semitones"]})
    await restored(c, "channel_set_name", {"index": 0, "name": "fLMCP check"},
                   {"index": 0, "name": ch["name"]}, lambda: _same(channel("name"), "fLMCP check"))
    await restored(c, "channel_set_color", {"index": 0, "color": "#FF8800"},
                   {"index": 0, "color": ch["color"]}, lambda: _color_near(channel("color"), "#FF8800"))
    await restored(c, "channel_mute", {"index": 0, "muted": not ch["is_muted"]},
                   {"index": 0, "muted": ch["is_muted"]}, lambda: _same(channel("is_muted"), not ch["is_muted"]))
    await restored(c, "channel_solo", {"index": 0, "solo": not ch["is_solo"]},
                   {"index": 0, "solo": ch["is_solo"]})
    mutes = [x["is_muted"] for x in seen["channel_all"]["channels"]]
    now = [x["is_muted"] for x in (await c.call("channel_all"))["channels"]]
    c.record("channel_solo (mutes restored)", now == mutes, f"before {mutes}, after {now}")
    await restored(c, "channel_route_to_mixer", {"index": 0, "mixer_track": top},
                   {"index": 0, "mixer_track": ch["fx_track"]}, lambda: _same(channel("fx_track"), top))
    out_of_range = await c.call("channel_route_to_mixer", index=0, mixer_track=top + 1)
    c.record("mixer track range check", "_error" in out_of_range, json.dumps(out_of_range)[:100])
    if swing is not None:
        await restored(c, "channel_set_swing", {"amount": 0.4}, {"amount": swing})
    steps = seen["channel_get_step_sequence"].get("steps")
    if steps is not None:
        await restored(c, "channel_set_step_sequence", {"index": 0, "steps": [1, 0] * 8},
                       {"index": 0, "steps": steps})

    # mixer_set_volume runs next and puts the fader back exactly.
    await restored(c, "mixer_set_volume_db", {"track": 1, "db": -6.0}, {"track": 1, "db": 0.0},
                   lambda: _near(mixer("volume_db"), -6.0, 0.05))
    await restored(c, "mixer_set_volume", {"track": 1, "volume": 0.6},
                   {"track": 1, "volume": mx["volume"]}, lambda: _near(mixer("volume"), 0.6))
    await restored(c, "mixer_set_pan", {"track": 1, "pan": -0.4}, {"track": 1, "pan": mx["pan"]})
    await restored(c, "mixer_set_name", {"track": 1, "name": "fLMCP check"}, {"track": 1, "name": mx["name"]})
    await restored(c, "mixer_set_color", {"track": 1, "color": "#3366FF"}, {"track": 1, "color": mx["color"]},
                   lambda: _same(mixer("color"), "#3366FF"))
    await restored(c, "mixer_mute", {"track": 1, "muted": not mx["is_muted"]}, {"track": 1, "muted": mx["is_muted"]},
                   lambda: _same(mixer("is_muted"), not mx["is_muted"]))
    await restored(c, "mixer_solo", {"track": 1, "solo": not mx["is_solo"]}, {"track": 1, "solo": mx["is_solo"]})
    await restored(c, "mixer_arm", {"track": 1, "armed": not mx["is_armed"]}, {"track": 1, "armed": mx["is_armed"]})
    await restored(c, "mixer_set_stereo_separation", {"track": 1, "separation": 0.5},
                   {"track": 1, "separation": mx["stereo_separation"]})
    await restored(c, "mixer_route", {"src_track": top - 1, "dst_track": top, "enabled": True},
                   {"src_track": top - 1, "dst_track": top, "enabled": False})
    await restored(c, "mixer_set_send_level", {"src_track": top - 1, "dst_track": top, "level": 0.5},
                   {"src_track": top - 1, "dst_track": top, "level": 0.0})
    band = (seen["mixer_get_eq"].get("bands") or [None])[0]
    if band:
        await restored(c, "mixer_set_eq_band", {"track": 1, "band": 0, "gain": 0.7, "frequency": 0.2},
                       {"track": 1, "band": 0, "gain": band["gain"], "frequency": band["frequency"]})

    await restored(c, "playlist_set_track_name", {"track": 1, "name": "fLMCP check"},
                   {"track": 1, "name": pl["name"]})
    await restored(c, "playlist_set_track_color", {"track": 1, "color": "#22AA44"},
                   {"track": 1, "color": pl["color"]})
    await restored(c, "playlist_mute_track", {"track": 1, "muted": not pl["is_muted"]},
                   {"track": 1, "muted": pl["is_muted"]})
    await restored(c, "playlist_solo_track", {"track": 1, "solo": not pl["is_solo"]},
                   {"track": 1, "solo": pl["is_solo"]})

    rec = seen.get("transport_recording_options", {}).get("options") or {}
    if rec.get("metronome") is not None:
        await restored(c, "transport_set_recording_options", {"metronome": not rec["metronome"]},
                       {"metronome": rec["metronome"]})
    slots = await c.call("mixer_fx_slots", track=1)
    if slots.get("slots_enabled") is not None:
        await restored(c, "mixer_set_slots_enabled", {"track": 1, "enabled": not slots["slots_enabled"]},
                       {"track": 1, "enabled": slots["slots_enabled"]})
    used = next((s for s in slots.get("slots", []) if s.get("enabled") is not None), None)
    if used is not None:
        await restored(c, "mixer_set_slot", {"track": 1, "slot": used["slot"], "enabled": not used["enabled"]},
                       {"track": 1, "slot": used["slot"], "enabled": used["enabled"]})

    await c.expect("project_save_undo", name="fLMCP live check")
    await c.expect("project_undo")
    await c.expect("project_redo")
    await c.expect("ui_hint", message="fLMCP live check")
    await c.expect("ui_show_window", name="mixer")
    await plugins(c, seen)


async def plugins(c: Check, seen: dict[str, dict]) -> None:
    plugin = next((x for x in seen["channel_all"].get("channels", []) if x.get("type") == 2), None)
    if plugin is None:
        c.record("plugin_*", True, "skipped: no plugin channel in this project", "N/A")
        return
    idx = plugin["index"]
    await c.expect("plugin_name", index=idx)
    params = await c.expect("plugin_params", index=idx, limit=5)
    await c.expect("plugin_find_param", index=idx, name_contains="a")
    await c.expect("plugin_preset_count", index=idx)
    first = (params.get("params") or [None])[0]
    if first:
        await restored(c, "plugin_set_param", {"index": idx, "param": first["idx"], "value": 0.25},
                       {"index": idx, "param": first["idx"], "value": first["value"]})


async def piano_roll(c: Check, channel: int) -> None:
    before = await c.call("pattern_current")
    created = await c.expect("pattern_create", name="fLMCP check")
    pattern = created.get("index")
    if not pattern:
        return
    await c.expect("pattern_set_color", index=pattern, color="#AA33CC")
    await c.expect("pattern_set_length", index=pattern, bars=2)
    pr = {"channel": channel, "pattern": pattern}
    await c.expect("piano_roll_add_notes", lambda d: _notes(d) == [(60, 0.0), (64, 0.27)],
                   notes=[{"midi": 60, "time_bars": 0, "duration_bars": 0.25},
                          {"midi": 64, "time_bars": 0.27, "duration_bars": 0.25}], clear_first=True, **pr)
    await c.expect("piano_roll_quantize", lambda d: (64, 0.25) in _notes(d), grid_bars=0.25, **pr)
    await c.expect("piano_roll_add_chord", lambda d: len(_notes(d)) == 5,
                   midi_notes=[48, 52, 55], time_bars=1, duration_bars=1, **pr)
    await c.expect("piano_roll_add_arpeggio", lambda d: len(_notes(d)) == 8,
                   midi_notes=[72, 76, 79], time_bars=1.5, step_bars=0.125, **pr)
    await c.expect("piano_roll_transpose", lambda d: _notes(d)[0][0] == 50, semitones=2, **pr)
    await c.expect("piano_roll_duplicate", lambda d: len(_notes(d)) == 11,
                   source_time_bars=1, length_bars=0.5, dest_time_bars=3, **pr)
    await c.expect("piano_roll_delete_notes", lambda d: len(_notes(d)) == 10,
                   notes=[{"midi": 50, "time_bars": 3}], **pr)
    await c.expect("piano_roll_humanize", lambda d: len(_notes(d)) == 10,
                   timing_jitter_bars=0.005, velocity_jitter=0.1, **pr)
    await c.expect("piano_roll_read", lambda d: len(_notes(d)) == 10, **pr)
    await c.expect("gen_emit_chord_progression", lambda d: len(_notes(d)) > 0,
                   channel=channel, pattern=pattern, clear_first=True)
    await c.expect("piano_roll_clear", lambda d: _notes(d) == [], **pr)
    await c.expect("channel_set_step_sequence", index=channel, steps=[1, 0, 0, 0] * 4, pattern=pattern)
    await c.expect("pattern_clear", index=pattern)
    steps = (await c.call("channel_get_step_sequence", index=channel, pattern=pattern)).get("steps") or []
    c.record("pattern_clear (steps gone)", not any(steps[:16]))
    clone = await c.expect("pattern_clone", index=pattern, new_name="fLMCP check copy")
    for index in (clone.get("new_index"), pattern):
        if index:  # FL can't delete patterns; the tool clears the name and says so
            gone = await c.call("pattern_delete", index=index)
            c.record("pattern_delete", gone.get("soft_deleted") == index, json.dumps(gone)[:100])
    if before.get("index"):
        await c.expect("pattern_select", index=before["index"])


async def _eq(coro, field, value) -> bool:
    return (await coro).get(field) == value


async def _near(coro, value, tol=0.02) -> bool:
    got = await coro
    return isinstance(got, (int, float)) and abs(got - value) <= tol


async def _color_near(coro, value: str, tol: int = 0x20) -> bool:
    got = await coro  # FL may adjust channel colors slightly (#FF8800 reads back as #FF8814)
    if not isinstance(got, str):
        return False
    a, b = int(got.lstrip("#"), 16), int(value.lstrip("#"), 16)
    return all(abs((a >> s & 0xFF) - (b >> s & 0xFF)) <= tol for s in (0, 8, 16))


async def _same(coro, value) -> bool:
    got = await coro
    return (got.upper() == value.upper()) if isinstance(got, str) and isinstance(value, str) else got == value


async def main(args: argparse.Namespace) -> int:
    async with Client(build_app(), mode="legacy") as client:
        c = Check(client)
        ping = await c.call("fl_ping")
        if "_error" in ping or ping.get("ok") is False:
            print("FL Studio isn't reachable:", ping.get("_error") or ping)
            print("Start FL with the fLMCP Bridge enabled; `python -m fl_studio_mcp doctor` explains why.")
            return 2
        started = time.monotonic()
        seen = await reads(c)
        if args.write:
            await writes(c, seen)
            channel = args.channel
            if channel is None:
                plugin = next((x for x in seen["channel_all"].get("channels", []) if x.get("type") == 2), None)
                channel = plugin["index"] if plugin else 0
            await piano_roll(c, channel)

    counts = {s: sum(1 for r in c.results if r[0] == s) for s in ("PASS", "FAIL", "N/A")}
    print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['N/A']} not supported by this FL "
          f"in {time.monotonic() - started:.0f} s")
    for status, name, detail in c.results:
        if status == "FAIL":
            print(f"  FAIL {name}: {detail}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="also run edits (restored afterwards)")
    parser.add_argument("--channel", type=int, help="0-based channel for the piano-roll checks")
    parser.add_argument("--verbose", action="store_true", help="show the server's log, tracebacks included")
    options = parser.parse_args()
    # One check provokes an error on purpose; keep its traceback out of the report.
    if not options.verbose:
        logging.disable(logging.ERROR)
    sys.exit(run(main, options))
