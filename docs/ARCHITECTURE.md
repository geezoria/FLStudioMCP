# fLMCP — Architecture

## Components

1. **`fl-studio-mcp` (Python package)** — FastMCP server process launched by
   the MCP host (Claude Desktop / Claude Code). Stdio transport.
2. **FL bridge script** — `device_FLStudioMCP.py` loaded by FL Studio as the
   controller type for `loopMIDI Port`. It performs file RPC from `OnMidiMsg`
   on FL's main thread and does not create threads.
3. **Piano-roll pyscript** — `ComposeWithLLM.pyscript` installed in FL
   Studio's `Settings/Piano roll scripts/` folder. The only way to reach
   `flpianoroll.Note / score.addNote / score.deleteNote` from the Python
   environment.
4. **Keystroke bridge** — `src/fl_studio_mcp/keystroke.py` sends Ctrl+Alt+Y to
   FL via Win32 `SendInput` after the MCP server stages a piano-roll request
   file. This is how the device bridge reaches `flpianoroll` — it cannot
   import it directly.

## Controller RPC transport

FL Studio 2025 runs controller scripts in a Python 3.12 sub-interpreter that
does not allow new threads. A TCP listener therefore cannot run inside the
controller script. fLMCP exchanges these files beside the installed script:

```
mcp_command.json   {"id": int, "action": "mod.name", "params": {...}}
mcp_response.json  {"id": int, "ok": bool, "result": any, "error": str|null}
```

The MCP process writes a temporary file and atomically replaces the command
file, then sends CC 119/value 1 on MIDI channel 16 through `loopMIDI Port`.
MIDI only wakes the bridge; it never carries the JSON payload. Non-matching
MIDI events pass through untouched.

## Request lifecycle

```
 MCP tool ─► BridgeClient.call(action, **params)
          ─► atomically replace mcp_command.json
          ─► send the reserved loopMIDI CC
                 │
                 ▼
          FL Studio OnMidiMsg (main thread)
          ─► validate the wake signature
          ─► read request; ignore an already-processed id
          ─► execute the existing _HANDLERS entry
          ─► overwrite mcp_response.json
                 │
                 ▼
 MCP process polls the matching response id and returns the result
```

## Piano-roll lifecycle

```
 MCP tool piano_roll_add_notes(channel, notes, pattern)
     │
     ▼
 bridge.pianoroll.addNotes:
     - patterns.jumpToPattern(pattern)
     - channels.selectOneChannel(channel)
     - ui.showWindow(widPianoRoll); ui.setFocused(widPianoRoll)
     - append JSON request to fLMCP_request.json
     - respond {"staged": True, "needs_keystroke": True}
     │
     ▼
 MCP tool:
     - keystroke.clear_state()
     - keystroke.send_hotkey_windows()
            │
            ▼  (SendInput Ctrl+Alt+Y to FL window)
     - FL fires ComposeWithLLM.pyscript.apply()
         - reads fLMCP_request.json
         - edits notes via flpianoroll
         - writes fLMCP_state.json
     - keystroke.wait_for_state()  ──► returns parsed state to Claude
```

## Callback and concurrency notes

- On FL Studio 2025 Producer 25.1.5 (MIDI scripting API 38), live
  instrumentation showed `OnInit`, `OnMidiIn`, and `OnMidiMsg` working while
  `OnIdle` and `OnRefresh` remained silent during UI and transport interaction.
- `OnMidiMsg` is the primary trigger. `OnIdle` polls the same file only as a
  compatibility fallback for FL versions that emit it.
- All handlers execute on FL's main thread. The client serializes concurrent
  calls because the transport exposes one command slot and one response slot.
- The client retries the wake-up every 100 ms until a response or timeout.

## Color handling

- Bridge accepts: `"#RRGGBB"`, `"rgb(r,g,b)"`, `[r,g,b]`, integer.
- Bridge returns: always `"#RRGGBB"`.
- FL Studio internal: `0xRRGGBB` integer (red in the high byte), verified
  against `utils.RGBToColor(255, 0, 0) == 0xFF0000`.
  - Note: fLMCP ≤ 0.1.0 used `0xBBGGRR` here, which swapped red and blue in FL.
    Fixed in 0.2.0; see `_color_to_int` / `_int_to_color_hex` and
    `tests/test_bridge_helpers.py`.

## Automation (non-blocking)

`automation_record_*` records live automation clips without sleeping on FL's
main thread. Each point is scheduled against an absolute monotonic deadline.
Because FL 2025 may not emit `OnIdle`, an async result starts a short-lived wake
pump in the external MCP process. Its reserved CC calls `OnMidiMsg` about every
20 ms; each callback applies due points, and the final one stops/disarms the
transport. The background thread lives outside FL's restricted sub-interpreter.
