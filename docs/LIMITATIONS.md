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
- ❌ `pattern_set_length` — not exposed; length is derived from notes / steps.

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
- ⚠️ `playlist_list_markers` — cannot enumerate; use
  `arrangement_jump_marker(+1/-1)` to step through.
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
  all map to the public API on already-loaded plugins. Parameter set/get and the
  value-string read correctly account for the `pickupMode` argument that sits
  before `useGlobalIndex` in FL's signatures.
- ✅ Preset navigation: `plugin_preset_count / next_preset / prev_preset`.
- ❌ `plugin_set_preset` (jump to a numeric preset) — `plugins.setPreset` is not
  exposed by FL's public Python API. The tool returns a structured error
  pointing at `plugin_next_preset` / `plugin_prev_preset`.
- ❌ **Loading new plugins into a slot** — Browser drag-drop only. Ask the
  user to load once, then fLMCP fully automates the parameters.

### Piano roll
- ✅ add / delete / clear / read / quantize / transpose / humanize /
  duplicate — all via `flpianoroll.score`.
- ⚠️ Requires FL Studio to accept foreground input. The bridge does a
  best-effort `SetForegroundWindow`, tries `Ctrl+Alt+Y`, and automatically
  selects **ComposeWithLLM** through the piano-roll menu if no state comes
  back. Results expose both `hotkey_sent` and `script_bootstrapped` so clients
  can distinguish the fast path from first-run recovery.

### Transport constants
The bridge gracefully degrades if specific `midi.FPT_*` / `midi.REC_*`
constants are missing in your FL build. Affected tools return `{ok: false,
error: "...not available"}` rather than raising.

- `transport_tap_tempo` needs `FPT_TapTempo`
- `transport_toggle_metronome` needs `FPT_Metronome`
- `transport_toggle_countdown_before_recording` needs `FPT_CountDown*`
- `transport_jog` needs `FPT_Jog`
- `transport_toggle_punch` needs `FPT_Punch`
- `transport_toggle_overdub` needs `FPT_Overdub`
- `transport_toggle_loop_recording` needs `FPT_LoopRecord`
- `transport_jump_marker` needs `FPT_MarkerJumpJog`
- `transport_set_time_signature` needs `REC_MainTimeSigNum/Den`
- `project_save` needs `FPT_Save` (FL 20+ has this)

### Automation
- `automation_record_*` record **live** automation clips: the bridge arms +
  plays the transport and writes values at the right musical times. This is
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
  `pip install "fl-studio-mcp[polyphonic]"` then `pip install basic-pitch --no-deps`.
  When Basic Pitch is absent, `polyphonic=True` returns a structured install
  hint and the monophonic engine still works.

### File/MIDI transport
- FL Studio 2025 controller scripts cannot start background threads, so the
  bridge uses one atomic command file, one response file, and a reserved MIDI
  CC wake-up through loopMIDI.
- Calls are serialized across one request/response slot. The client retries a
  wake every 100 ms and reports a timeout with the expected MIDI port name.
- The default Windows output name is `loopMIDI Port`; override it with
  `FL_STUDIO_MCP_MIDI_PORT` when using a different name.
- The wake signature is CC 119/value 1 on channel 16. Other MIDI messages are
  not consumed by the bridge.

### Callback availability
On FL Studio 2025 Producer 25.1.5, `OnMidiMsg` was observed reliably while
`OnIdle` and `OnRefresh` were not emitted for the controller. `OnIdle` is only
a fallback. Asynchronous automation is advanced by a temporary wake pump in
the external MCP process.

### Reloading controller changes

Toggling **Enable** calls the already-imported module's lifecycle again but does
not necessarily re-read `device_FLStudioMCP.py`. After updating that file, set
the MIDI input's **Controller type** to `(generic controller)` and then back to
`fLMCP Bridge`. This was verified on FL Studio 2025 Producer 25.1.5.
