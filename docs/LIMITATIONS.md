# fLMCP — Known limitations

All limits below come from FL Studio's public Python API ([FL Studio API Stubs
reference](https://il-group.github.io/FL-Studio-API-Stubs/)), not from fLMCP's
architecture. For each, the bridge returns a helpful error rather than
silently failing.

## Tool verification status

Tools are validated against the public API stubs. Legend:

- ✅ **verified** — function exists in the public stubs, signature matches,
  the bridge calls it correctly.
- ⚠️ **limited** — works, but only partially (e.g. toggle-only, no read-back).
- ❌ **not exposed** — the FL Python API does not provide this; the bridge
  returns a structured error explaining the workaround.

The tool *still appears* in the MCP catalog regardless, because Claude can use
the error response to fall back gracefully.

## Per-area limits

### Patterns
- ✅ create (`setPatternName` on new index), rename, select, list, color,
  clone, find-by-name, next/prev
- ❌ `pattern_delete` — FL has no `deletePattern()`. Bridge soft-deletes by
  emptying the name; pattern remains in the pool.
- ✅ `pattern_set_length` (bars, beats or steps) and `pattern_clear` — **FL Studio 2026
  (26.1 / 26.1.3) or newer**, via `patterns.setPatternLength` /
  `patterns.clearPattern`. Older FL returns `ok: false` with a hint; there,
  length still follows the notes / step grid.
- ⚠️ `pattern_clear` cleared notes and steps at once on FL 26.1.7. An earlier
  check saw FL show a **Confirm dialog** first. FL (and the bridge) then wait
  until the user answers it, and the tool reports `pending_confirmation`.
- ✅ `pattern_clone` into a chosen pattern (`dest_index`) — FL 2026.1.1+.

### Channels
- ✅ name / volume / pan / color / mute / solo / route-to-mixer / step grid.
- ⚠️ `channel_set_pitch` — FL's `setChannelPitch` mode 2 ("semitones") is
  **broken** in the public API, and mode 0 is a factor of the bend range, not
  semitones. fLMCP therefore sets pitch in **cents** (mode 1, `semitones × 100`)
  and reports back `pitch_semitones` + `pitch_cents`. The result is clamped to
  the channel's configured pitch range, so large offsets may not fully apply.
- ⚠️ `channel_solo` — FL's `soloChannel` has no value argument (toggle only);
  fLMCP honours an explicit `solo=true/false` by toggling only when the current
  state differs.
- ⚠️ Step-sequencer get/set/clear for a **non-current** pattern — FL's grid-bit
  API only reaches the selected pattern, so fLMCP briefly jumps to the requested
  `pattern` and restores the previous selection afterwards.

### Playlist
- ✅ track vol/pan/mute/solo/name/color
- ❌ `playlist_place_pattern`, `playlist_delete_clip`, `playlist_list_clips`
  — FL's public API has no clip-level accessors.
- ❌ `playlist_delete_marker` — not exposed.
- ⚠️ `playlist_add_marker` — works via `arrangement.addAutoTimeMarker`.
- ⚠️ `playlist_list_markers` — names only (FL exposes no marker positions);
  use `arrangement_jump_marker(+1/-1)` to step through them.
- ⚠️ `playlist_refresh` — no general "repaint the playlist" function is exposed
  (FL repaints automatically); the tool is effectively a no-op that nudges
  performance-mode live clips where available.

### Arrangement
- ⚠️ `arrangement_jump_marker(direction)` — works (single function: `jumpToMarker`).
- ❌ `arrangement_select`, `arrangement_list`, `arrangement_current` —
  multi-arrangement switching is **not** exposed in the current stubs.

### Project
- ❌ `project_new`, `project_open`, `project_render` — require UI interaction.
- ✅ `project_save`, `project_save_as` — via `transport.globalTransport(FPT_Save)`.
- ✅ `project_undo` / `project_redo` via `general.undoUp/Down`.
- ⚠️ `project_undo_history` — returns `count / position / last / hint`
  (per-index entry names are not exposed by FL).
- ✅ `project_metadata`, `project_version` — version returned as both int and
  `major.minor.patch` string.

### Mixer EQ
- ✅ `mixer_get_eq`, `mixer_set_eq_band` — use the real API (`getEqGain`,
  `setEqGain`, `getEqFrequency`, `setEqFrequency`, `getEqBandwidth`).
- ✅ `mixer_route`, `mixer_set_send_level` — use `setRouteTo`,
  `setRouteToLevel`, `getRouteToLevel`, `afterRoutingChanged`.
- ✅ `mixer_select` — `setActiveTrack` (exclusive selection).

### Plugins
- ✅ `plugin_is_valid / name / params / get_param / set_param / show_editor` —
  all map to the public API on already-loaded plugins. `setParamValue` takes
  `pickupMode` before `useGlobalIndex`. `getParamValueString` takes 5 arguments
  in the stubs but at most 4 on FL 26.1.7, and the bridge handles both. On a
  sampler or an empty slot the tools return `no plugin at …` with a hint.
- ✅ Preset navigation: `plugin_preset_count / next_preset / prev_preset`.
- ❌ `plugin_set_preset` (jump to a numeric preset) — `plugins.setPreset` is not
  exposed by FL's public Python API. The tool returns a structured error
  pointing at `plugin_next_preset` / `plugin_prev_preset`.
- ✅ **Loading effects into a mixer slot** — `mixer_load_effect` finds the
  plugin in FL's Plugin database (`plugin_search` lists it) and loads its .fst
  preset with `ui.launchAudioEditor`, which puts it in the track's next free
  slot. The Plugin Manager must have scanned third-party plugins first. Verified
  on FL 26.1.7. `mixer_set_slot` turns a slot on or off and sets its mix.
- ❌ **Loading instruments (generators) into the channel rack** — no API. On
  FL Studio 2026, `ui_show_picker("plugin")` opens the plugin picker so the
  user can choose one in a click; after that fLMCP fully automates the
  parameters.

### Piano roll
- ✅ add / delete / clear / read / quantize / transpose / humanize /
  duplicate — all via `flpianoroll.score` (verified live on FL 26.1.7).
- ✅ On Windows the server runs `ComposeWithLLM` from the piano roll's *Tools*
  menu with posted window messages: no setup, no focus change, and it doesn't
  touch the mouse or keyboard. A piano roll must be visible; tools given a
  `channel` open it. The result's `trigger` says `tools_menu`, or `hotkey` when
  it fell back to `Ctrl+Alt+Y`.
- ⚠️ The menu position is computed from the `.pyscript` files in FL's and your
  *Piano roll scripts* folders. Scripts kept in subfolders aren't counted; if
  FL lists them in the menu, the server can pick the wrong script.
- ⚠️ If the script doesn't answer within the wait, nothing changes and the
  queue is emptied (`ok: false` with a `note`), so retrying is safe.
- ⚠️ macOS: the server can't run the script. Edits stay queued until you run
  *Tools → Scripts → ComposeWithLLM* (or `Cmd+Opt+Y` after the first run).

### Transport constants
The bridge gracefully degrades if specific `midi.FPT_*` / `midi.REC_*`
constants are missing in your FL build. Affected tools return `{ok: false,
error: "...not available"}` rather than raising.

- `transport_tap_tempo` needs `FPT_TapTempo`
- `transport_toggle_metronome` needs `FPT_Metronome`
- `transport_toggle_countdown_before_recording` needs `FPT_CountDown*`
- `transport_jog` needs `FPT_Jog`
- `transport_set_time_signature` needs `REC_MainTimeSigNum/Den`
- `project_save` needs `FPT_Save` (FL 20+ has this)

### Automation
- `automation_record_*` record **live** automation clips: the bridge arms +
  plays the transport and writes values at the right musical times, counted
  from when playback starts. This is
  **asynchronous and non-blocking** — the tool returns immediately
  (`{"mode": "async", "scheduled": N}`) and the clip finishes on its own a few
  seconds later. FL's UI stays responsive throughout (earlier versions slept
  inside `OnIdle`, freezing FL for the clip's duration).
- Timing uses a tempo snapshot taken at call time; if you change tempo mid-clip
  the later points may drift slightly.

### Colors
- ✅ Set/read on patterns, channels, mixer and playlist tracks. FL stores colors
  as `0xRRGGBB`; fLMCP accepts `#RRGGBB` / `rgb(r,g,b)` / `[r,g,b]` / int and
  always returns `#RRGGBB`. (Versions ≤ 0.1.0 swapped red and blue — fixed.)

### Voice / audio transcription
- ✅ Monophonic (default): `librosa` pyin — light, no model, great for humming /
  single melodic lines.
- ✅ Polyphonic (`polyphonic=True`): Spotify Basic Pitch — chords, strums,
  full-mix content. Optional `polyphonic` extra.
- ⚠️ **Basic Pitch install on Python 3.12:** `pip install basic-pitch` fails —
  it pins `tensorflow<2.15.1`, which has no Python 3.12 build. fLMCP installs it
  with `--no-deps` and runs inference on the **ONNX** runtime (no TensorFlow).
  Use `install_windows.ps1 -Polyphonic`, or:
  `pip install "flmcp-studio[polyphonic]"` then `pip install basic-pitch --no-deps`.
  When Basic Pitch is absent, `polyphonic=True` returns a structured install
  hint and the monophonic engine still works.

### File transport
- One command at a time. Calls from every MCP client on the machine are
  serialized through `mcp_command.lock`, which keeps FL's main thread
  predictable. Use batched actions (the server does this internally where it
  matters) rather than many tiny calls.
- If the bridge's heartbeat is older than 5 s the server refuses to queue a
  command and returns `bridge unavailable` — nothing is left waiting to fire when
  FL comes back.
- Large payloads (full channel rack / mixer dumps) are fine; commands are capped
  at 16 MiB.

### Latency
With the `fLMCP` loopMIDI wake port, a round-trip is typically a few tens of
milliseconds (FL 2026.1.7: median 42 ms, p95 56 ms). FL 2026 does not call the
script's `OnIdle`, so without the wake port the bridge never sees commands.
Older FL versions that call `OnIdle` answer without it, at FL's idle rate.

### Modal dialogs
Some FL functions open a modal dialog (arming Record asks what to record, and
`pattern_clear` has been seen to ask for confirmation). While it is open FL's UI thread, and with it the bridge, waits.
Calls time out until the user answers it in FL.

### Automation recording follows FL's recording filter
The `automation_record_*` tools arm Record and play the song, then send the
values live. FL records what its recording filter allows (right-click the
Record button). *Automation* must be on. If *Audio, into Edison* is also on, FL
opens Edison on the selected mixer track and records audio as well. The
Python API can't read or change the filter. In Song mode with an empty
playlist, FL loops the first bar ("Song jump") until the recording ends.
In Pattern mode the values go into the current pattern, which loops, so keep
the points within the pattern's length.

Arming Record makes FL ask "What would you like to record?" unless *Don't show
this in the future* was ticked. On Windows the tools answer *Notes and
automation* themselves. On macOS, answer it in FL within 20 s, or the
recording is cancelled. Ticking that box removes the prompt for good.
