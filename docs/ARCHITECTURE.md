# fLMCP — Architecture

## Components

| Component | Runs in | Role |
| --- | --- | --- |
| `flmcp` server (`src/fl_studio_mcp/`) | Your AI app, as a stdio subprocess | MCP SDK 2.x `MCPServer`: 160 tools, 7 resources, 6 prompts. Serializes FL calls, talks to the bridge through files. |
| Controller bridge (`fl_bridge/device_FLStudioMCP.py`) | FL Studio, as a MIDI controller script | Reads commands, runs FL API calls on FL's main thread, writes responses and a heartbeat. |
| Piano-roll script (`fl_bridge/piano_roll/ComposeWithLLM.pyscript`) | FL Studio's piano roll | The only place `flpianoroll` (add / delete notes) is importable. |
| CLI (`src/fl_studio_mcp/cli.py`) | Your terminal | `flmcp install / doctor / config / uninstall`. With no arguments it starts the server. |

## Why a file transport

FL Studio 2025 and 2026 run controller scripts in a **sandboxed
sub-interpreter**. The earlier 0.3 bridge opened a TCP socket on a background
thread, which no longer works: `threading`, `socket` and most of `os`
(`remove`, `replace`, `stat`, `mkdir`) fail or are missing. Built-in `open()`
still works. Controller callbacks (`OnIdle`, `OnMidiMsg`, `OnRefresh`, …) all
run on FL's main thread, which is where FL API calls belong anyway.

So the server and the bridge exchange small JSON files in the bridge's script
folder (`Settings/Hardware/fLMCP Bridge/`, override with `FLMCP_BRIDGE_DIR`):

| File | Written by | Purpose |
| --- | --- | --- |
| `mcp_command.json` | server (atomic temp-file + `os.replace`) | The pending command. |
| `mcp_response.json` | bridge | Result of the last command. |
| `mcp_heartbeat.json` | bridge, ≈ once per second | Liveness and versions. `ts: 0` means FL shut the script down cleanly. |
| `mcp_command.lock` | server | Cross-process lock (`msvcrt` / `fcntl`) so several MCP clients can share one FL. Never deleted. |
| `fLMCP_log.txt` | bridge | Rolling log, capped at 64 KiB. |

## Protocol 2

```jsonc
// mcp_command.json — single action
{"protocol": 2, "id": 1763835600123456789, "action": "mixer.setVolume", "params": {"index": 1, "value": 0.8}}
// or a batch (max 32 actions, executed in order on one tick)
{"protocol": 2, "id": 1763835600123456790, "batch": [{"action": "...", "params": {}}, ...]}

// mcp_response.json
{"id": 1763835600123456789, "ok": true, "result": {...}, "elapsed_ms": 0.4}
{"id": 1763835600123456789, "ok": false, "error": "…", "traceback": "…"}
{"id": 1763835600123456790, "ok": true, "results": [{"ok": true, "result": ...}, ...]}

// mcp_heartbeat.json
{"protocol": 2, "bridge_version": "0.4.0", "api_version": 45, "fl_version": "…",
 "pid": 1234, "last_id": 1763835600123456789, "idle_calls": 812, "midi_wakes": 40, "ts": 1763835601.2}
```

- **Ids** are `time_ns()`-based and strictly increasing: the client takes the
  maximum of the clock, the heartbeat's `last_id`, and the ids already in the
  command/response files, so clock rollback or a restarted MCP process can't
  produce a duplicate id.
- **Stale commands.** On `OnInit` the bridge marks whatever command is already
  on disk as seen, so an edit queued before FL restarted is never replayed.
- **Fail fast.** Before writing a command the client requires a heartbeat newer
  than 5 s (after one short MIDI-wake probe). A dead bridge returns
  `bridge unavailable` immediately instead of leaving a queued edit behind.
- **Timeout.** `FLMCP_TIMEOUT` (default 10 s) per call.
- **Size limit.** Commands are capped at 16 MiB.

## Request lifecycle

```
MCP tools/call ──► serialize_fl_calls middleware (anyio lock: one FL call at a time)
               ──► BridgeClient.call(action, **params)
                     1. fresh heartbeat?          no → wake probe → RPCError "bridge unavailable"
                     2. take mcp_command.lock
                     3. write mcp_command.json   (temp file + os.replace, retried on Windows sharing errors)
                     4. MIDI wake: CC 119 / ch 16 → "fLMCP" or loopMIDI output (if present)
                     5. poll mcp_response.json every 8 ms until id matches or timeout
                                     │
FL Studio main thread ───────────────┼───────────────────────────────────────────
   OnMidiMsg (wake CC) ─┐            ▼
   OnIdle ──────────────┴─► _run_bridge_tick (re-entrancy guarded)
                              read command → dispatch "group.name" to h_group_name(params)
                              → write mcp_response.json → refresh heartbeat
                              → step pending automation recordings
```

The wake message is consumed (`event.handled = True`) and never reaches FL's
MIDI routing. The bridge also ticks from `OnIdle` where FL calls it, but FL
Studio 2026 (26.1.7, verified) never calls `OnIdle` for this script:
`idle_calls` in the heartbeat stays 0 and everything runs on wakes.
`OnUpdateMeters` with `device.setHasMeters()` was tried as an alternative tick
source, and FL doesn't call it either, even during playback. As a
consequence the heartbeat only refreshes when a wake arrives. The client
therefore treats a stale heartbeat as "probe with a wake first", not as
"offline". Async automation recordings are driven the same way: while one
runs, the client sends wakes every 20 ms.

## Piano-roll path

`flpianoroll` is importable only inside piano-roll scripts, and those run only
when the user (or a hotkey) triggers them:

1. A `piano_roll_*` or `gen_emit_*` tool writes `fLMCP_request.json` into
   `Settings/Piano roll scripts/`.
2. `keystroke.py` focuses FL and sends `Ctrl+Alt+Y` (FL's "run last piano-roll
   script") via Win32 `SendInput`.
3. `ComposeWithLLM.pyscript` reads the request, adds / deletes / edits notes
   and writes the full note list to `fLMCP_state.json`, which the tool returns.

The user has to select **ComposeWithLLM** in a piano roll once so the hotkey
targets it. On non-Windows platforms step 2 is manual.

## Server internals

- `server.py` — builds the `MCPServer`, installs the serialization middleware,
  registers tools (`tools/`), resources (`resources/`) and prompts
  (`prompts.py`), and holds the model-facing `instructions` (index conventions,
  workflow hints).
- `tools/_meta.py` — `ToolRegistrar.read(title)` / `.write(title, destructive=)`
  attach MCP tool annotations (`readOnlyHint`, `destructiveHint`,
  `idempotentHint`, `openWorldHint`) to every tool.
- `tools/_toolsets.py` — `FLMCP_TOOLSETS` / `FLMCP_DISABLE_TOOLS` selection;
  `fl_ping` and `fl_list_toolsets` are always registered.
- `paths.py` — resolves FL's user data folder from
  `HKCU\Software\Image-Line\Shared\Paths\Shared data`, falling back to
  `~/Documents/Image-Line/FL Studio/Settings`.
- Audio and voice modules import numpy / librosa / sounddevice lazily, so the
  core server runs without them.

## Bridge internals

- Handlers are plain functions `h_<group>_<name>(params) -> JSON-able` in
  `device_FLStudioMCP.py`; the action name `group.name` maps to them.
- `meta.ping`, `meta.info` and `meta.introspect` report versions and, for
  `introspect`, every callable (with docstrings) the running FL exposes — the
  ground truth for API-version gating.
- FL API version is read from `general.getVersion()`; newer FL 2026 calls are
  guarded by version checks or `hasattr` so the same script runs on FL 2024
  (API 3x) through FL 2026 (API 45).

See [`FL_API_2026.md`](FL_API_2026.md) for the API changes between FL 2025 and
2026, and [`LIMITATIONS.md`](LIMITATIONS.md) for what FL's API cannot do.
