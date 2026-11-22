# Changelog

All notable changes to fLMCP are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [0.4.0] — Unreleased

Works on FL Studio 2025 and 2026 again, on MCP SDK 2.x, and installs with one
command.

### Breaking

- **The PyPI distribution is now `flmcp`.** `fl-studio-mcp` on PyPI belongs to a
  different project. The import package (`fl_studio_mcp`) and the legacy
  `fl-studio-mcp` console script stay. The MCP client entry is renamed to
  `flmcp`, and `flmcp install` removes the old `fl-studio-mcp` entries.
- **The TCP bridge is gone.** FL Studio 2025/2026 run controller scripts in a
  sandboxed sub-interpreter without threads or sockets (#1). The bridge now
  uses a file transport (protocol 2). Re-run `flmcp install` so FL gets the new
  `device_FLStudioMCP.py`.
- **Requires `mcp>=2.3,<3`.** The server moved from the removed
  `mcp.server.fastmcp` to `mcp.server.mcpserver` (#3).

### Added

- **File + MIDI-wake transport (protocol 2).** Atomic command and response
  files, a once-a-second heartbeat with bridge/FL/API versions, a
  cross-process lock, `time_ns` ids that survive clock rollback, stale-command
  suppression on FL start, batched actions, and fail-fast "bridge unavailable"
  when FL isn't listening. An optional CC 119 / ch 16 wake through a loopMIDI
  port named `fLMCP` brings round-trips down to tens of milliseconds.
  Thanks to @cgradusov (#2) and @marcostolosa (#4, #5), whose PRs first proved
  a file-based transport inside FL 2025's sandbox.
- **`flmcp` CLI.**
  - `flmcp install` copies the FL scripts into FL's real user data folder,
    creates the loopMIDI port, assigns the controller, and registers the server
    with Claude Desktop, Claude Code, Cursor and VS Code, backing up configs
    first.
  - `flmcp doctor` checks files, outdated scripts, the MIDI port and controller
    assignment, FL process, heartbeat, live ping and version mismatch.
  - `flmcp config` prints per-client snippets, including Codex.
  - `flmcp uninstall` removes everything again.
  - Running `flmcp` with no arguments starts the server, so `uvx flmcp` just
    works.
- **The FL scripts ship inside the wheel**, so installing from PyPI/uvx needs no
  git clone.
- **MCP 2 features.** Every tool has a title and read-only / destructive /
  idempotent annotations, and returns structured output.
- **6 guided prompts:** *Make a Beat*, *Write a Chord Progression*, *Hum to
  Melody*, *Flip Audio to Drum & Bass*, *Review a Mix*, *Project Overview*.
- **Toolsets:** `FLMCP_TOOLSETS` (`all`, `core`, `lean` or any group list) and
  `FLMCP_DISABLE_TOOLS`. The new `fl_list_toolsets` tool lists them.
- **FL Studio 2026 API support** (gated, so FL 2024/2025 keep working):
  - `pattern_set_length`, which really sets the length now (in bars, beats or
    steps; FL's unit turned out to be steps, not beats as documented).
  - New `pattern_clear` tool. FL asks the user to confirm, and the tool says so.
  - `pattern_clone` can copy into a chosen pattern (`dest_index`).
  - New `channel_get_swing` / `channel_set_swing` tools for the Channel rack swing.
  - New `ui_show_picker` tool, which opens the plugin or project picker.
  - `project_metadata` reports title, author and genre.
  - `mixer_count` reports `insert_count`.
- `fl_introspect` tool, backed by the new `meta.introspect` bridge action. It
  lists which scripting functions the running FL build exposes, with optional
  docstrings.
- `fl_ping` warns when the bridge script inside FL is older than the server and
  tells you how to update it.
- `server.json` for the official MCP Registry (`io.github.geezoria/flmcp`).
- CI on Windows/macOS/Linux × Python 3.10/3.12/3.13 with a wheel smoke test,
  issue and PR templates, `CONTRIBUTING.md`, `SECURITY.md`, `AGENTS.md`, and a
  generated `docs/TOOLS.md` (`scripts/gen_tools_doc.py`).
- `docs/FL_API_2026.md`: FL 2026 scripting API changes (API 45).

### Changed

- `scripts/install_windows.ps1` / `uninstall_windows.ps1` are thin wrappers
  around `flmcp install` / `flmcp uninstall`.
- Bridge, CLI and FL user data folder detection honour FL's *Shared data*
  registry setting and `FLMCP_BRIDGE_DIR`.
- Rewrote README, ARCHITECTURE and LIMITATIONS for the new transport.

### Fixed

- Pattern tools no longer treat `patterns.patternCount()` as the highest
  pattern index. It is 0 in a fresh project even though *Pattern 1* exists,
  so `pattern_set_length` and friends rejected valid patterns, and
  `pattern_create` / `pattern_list` could miss or overwrite patterns after a
  gap. Indices are now checked against `patternMax()`, and "in use" means
  having content or a custom name.
- `pattern_clone` reports the index FL really cloned into.
- `flmcp doctor` no longer reports a working bridge as broken on FL 2026.
  There, FL never calls the script's `OnIdle`, so the heartbeat only refreshes
  on MIDI wakes. Doctor now pings through a wake, and it treats a missing
  loopMIDI port as a real problem.
- Transport tools now act on the song while a plugin window has focus. FL
  sends `transport.start/stop/record` to a focused plugin that has its own
  transport, such as Edison, so `transport_stop` could leave the song playing.
  The bridge first moves focus to the Playlist and reports `refocused_from`.
  The automation recorder does the same.
- Automation recording works on FL 2026. Arming Record opens FL's "What would
  you like to record?" prompt. The recorder used to start playback in the same
  callback, which closed the prompt and cancelled the arm. Its points then
  failed with "Operation unsafe at current time", and the end of the run could
  leave Record armed. The bridge now runs the recording as a state machine over
  ticks. It arms, waits for the prompt to be answered, starts playback, and
  counts point times from there. It retries a point that hits "unsafe", then
  stops and disarms without ever arming Record again. On Windows the server
  answers the prompt with *Notes and automation*, and the result says so.
  Verified on FL 26.1.7: a mixer-volume ramp recorded into the pattern and
  played back. The heartbeat reports the recorder's state, and the client
  keeps waking FL until the run ends.
- "Operation unsafe at current time" errors now name the FL dialog that is in
  the way (Windows). `transport_record` reports when the record prompt opened,
  and `ui_focused_window` returns `focused_form_caption`.
- `flmcp doctor` reports when more than one FL Studio instance is running.
  They share the bridge folder, so they race for commands and a single call
  can run in both.
- `flmcp install` now really assigns the controller. It also writes the MIDI
  input's `Type="-3"`, which marks a script controller. Without it FL ignored
  `ScriptFolder`, and users had to pick *fLMCP Bridge* in MIDI Settings by
  hand. `flmcp doctor` warns when an input names the bridge with another Type.
  Verified end to end on FL 26.1.7: with FL closed, the registry was reset to a
  fresh state, then install ran and FL started. The bridge answered without
  any clicks.

## [0.3.0] — 2026-06-17

### Added — polyphonic transcription (Spotify Basic Pitch)

- **`polyphonic=True` flag** on `voice_record_and_transcribe`,
  `voice_transcribe_file`, `voice_to_piano_roll`, `audio_analyze`,
  `audio_melody_to_piano_roll` and `song_to_dnb_flip`. When set, transcription
  uses Spotify Basic Pitch (ML, polyphonic) instead of the default monophonic
  pyin engine — so chords, guitar strums, piano and full-mix melodic content
  come through with overlapping notes preserved. Default stays monophonic
  (lightweight, no extra deps). This finally makes `song_to_dnb_flip`'s melody
  extraction faithful to chordal sources.
- New engine functions `transcribe_polyphonic()` and the pure, unit-tested
  converter `_basic_pitch_to_notes()` in `voice_to_midi.py`; `analyze_audio()`
  gained a `polyphonic` parameter. Verified end-to-end against the real Basic
  Pitch library (a C-major triad transcribes to 3 simultaneous notes).
- New `[polyphonic]` extra with the Basic Pitch runtime deps, and a
  `-Polyphonic` switch on the Windows installer.
  - **Note:** `pip install basic-pitch` is broken on Python 3.12 (it pins
    `tensorflow<2.15.1`, which has no 3.12 build). fLMCP installs Basic Pitch
    with `--no-deps` and runs inference on the **ONNX** runtime instead (no
    TensorFlow). The installer's `-Polyphonic` switch does this automatically;
    manual users run `pip install "fl-studio-mcp[polyphonic]"` (now `flmcp[polyphonic]`) then
    `pip install basic-pitch --no-deps`.
- Tools now report which `engine` produced the notes.

## [0.2.0] — 2026-06-17

A correctness, robustness and testability pass. Every change below was verified
against the official [FL Studio API stubs](https://pypi.org/project/fl-studio-api-stubs/)
where it touches the FL API, and the offline test suite grew from 15 to 63 tests.

### Fixed — correctness bugs

- **Colors were red/blue swapped.** FL Studio stores colors as `0xRRGGBB`
  (verified via `utils.RGBToColor(255,0,0) == 0xFF0000`), but the bridge encoded
  and decoded them as `0xBBGGRR`. Every color set through fLMCP showed up with
  red and blue swapped in FL. `_color_to_int` / `_int_to_color_hex` now use the
  correct byte order, accept `[r,g,b]` lists, and mask the high byte on read.
  Covered by `tests/test_bridge_helpers.py`.
- **`channel_set_pitch` set the wrong thing.** It used `setChannelPitch` mode 0,
  which is a *factor of the pitch-bend range* (`-1..1`), not semitones — so a
  request for "12 semitones" clamped to max bend. It now uses mode 1 (cents,
  `semitones × 100`), the only non-broken semitone path in FL's API, and reports
  `pitch_semitones` + `pitch_cents`.
- **`channel_solo` / `mixer_arm` ignored the requested state.** Both FL functions
  are toggle-only, so passing `solo=true` / `armed=false` did nothing meaningful.
  They now read the current state and toggle only when it differs.
- **`playlist_mute_track` / `playlist_solo_track` ignored the requested state.**
  Now use FL's value argument (`-1` toggle, `0/1` set) to honor `muted` / `solo`.
- **`channel_*_step_sequence(pattern=…)` ignored the pattern.** FL's grid-bit API
  only reaches the selected pattern; the bridge now jumps to the requested
  pattern and restores the previous selection afterwards.
- **`ui_focused_window` returned garbage.** It called `ui.getFocused(-1)`, but
  that function reports whether a *specific* widget is focused. It now scans the
  known windows and returns the focused window name + `focused_form_id`.
- **`channel_mute` used inconsistent index space.** Reads used the global channel
  index while the write used the local index; both now use the global index, and
  the write uses FL's direct value argument.
- **`song_to_dnb_flip` sub-bass octave** comment/code/hint disagreed; aligned to
  MIDI 24–35 (C1–B1).

  The next group were all the same class of error — FL functions have a
  `mode` / `pickupMode` argument *before* `useGlobalIndex`, and the bridge was
  passing `True` (intending "global index") into that earlier slot:

  - **`channel_info.volume` was wrong.** `getChannelVolume(i, True)` set
    `mode=True`, returning the value in **decibels** from the local index, not
    the normalized 0..1 global value. Now `getChannelVolume(i, False, True)`,
    with `volume_db` reported separately.
  - **`channel_set_volume` / `channel_set_pan` set `pickupMode=1`** instead of
    using the global index. Now pass `(…, 0, True)` (pickup off, global index).
    Same fix in the channel-volume / channel-pan automation paths.
  - **`plugin_set_param` and the value-string read set `pickupMode`** instead of
    `useGlobalIndex` — channel-plugin parameters were read/written against the
    wrong index space. Now pass the explicit `pickupMode=0` slot.
  - **`channel_*_grid_bit` / step-sequencer ops used the local index**; now use
    the global index to match the rest of the channel API.
  - **`channel_quick_quantize` called `quickQuantize()` with no arguments**, but
    `index` is required — it would have raised `TypeError` in FL. Now passes the
    channel index explicitly.
- **`plugin_set_preset` / `playlist_refresh` called non-existent FL functions**
  (`plugins.setPreset`, `playlist.refresh`) — they would have raised
  `AttributeError`. Both now degrade gracefully with a clear note.
- **`ui_show_window("plugin")` used a bogus window id.** `midi.widPlugin` does
  not exist; FL has `widPluginEffect` (6) and `widPluginGenerator` (7). The
  window map now uses the real constants and adds `plugin_generator` /
  `plugin_effect` names.

  Every FL API call the bridge makes (224 references; 105 distinct real calls)
  was cross-checked against the official API stubs as part of this pass.

### Fixed — robustness

- **The server no longer needs the audio extras to start.** `numpy` / `librosa` /
  `sounddevice` / `soundfile` were imported at module load, so a base install
  (without the — previously non-existent — `[audio]` extra) failed to boot the
  *entire* server, killing transport/mixer/everything. These are now imported
  lazily, only when a voice/audio tool actually runs. Guarded by
  `tests/test_lazy_imports.py`.
- **Missing `[audio]` extra.** The README told users to `pip install
  "fl-studio-mcp[audio]"`, but `pyproject.toml` defined no such group. Added
  `audio`, `gui` and `dev` optional-dependency groups; the Windows installer now
  installs `[audio,gui]` by default (`-SkipAudio` to opt out).
- **Friendly errors when audio deps are missing.** Voice/audio tools return a
  structured `{ok: false, error: "...pip install fl-studio-mcp[audio]..."}`
  instead of an `ImportError` traceback.
- **Automation no longer freezes FL.** `automation_record_*` previously slept
  between points *inside `OnIdle`*, blocking FL's main thread and freezing the UI
  for the whole clip. Rewritten as a non-blocking scheduler that applies points
  by monotonic deadline across idle ticks; tools return immediately with
  `{"mode": "async"}`.
- **The FL bridge is now importable offline.** Its FL-only imports are guarded,
  so the pure helpers (color, position mapping, the handler table) can be unit
  tested without FL Studio.

### Added — features

- **Generators honor `channel` / `pattern`.** `gen_emit_chord_progression`,
  `gen_emit_melody`, `gen_emit_bassline` and `gen_emit_arpeggio` accepted a
  `channel` argument that was silently ignored. When the TCP bridge is online,
  the target channel's piano roll is now opened automatically before the notes
  are written.
- **Piano-roll tools accept optional `channel` / `pattern`** for the same
  auto-routing, matching the documented signatures.
- **`voice_notes_to_piano_roll` gained `quantize_grid_sec`** and validates the
  scale name.

### Removed

- **`fl_bridge/piano_roll/fLMCP_bridge.pyscript`** — dead, never installed, and a
  footgun: it tried to bind a second TCP server on the same port (`9876`) as the
  device script. Nothing referenced it.

### Tests / tooling

- New: `test_voice_to_midi.py`, `test_audio_analysis.py`, `test_bridge_helpers.py`,
  `test_audio_dnb.py`, `test_file_bridge.py`, `test_lazy_imports.py`; expanded
  `test_generators.py` and `test_server_build.py`. 15 → 63 tests, all offline.
- `pyproject.toml`: project URLs, classifiers, keywords, `[tool.pytest.ini_options]`,
  version bumped to `0.2.0`. Added a top-level `LICENSE` file (MIT).

## [0.1.0] — 2026-04-22

- Initial release: 159 MCP tools + 7 resources covering transport, patterns,
  channels, mixer, plugins, piano roll, playlist, arrangement, automation,
  project, UI, high-level generators, voice-to-MIDI and audio-file analysis.
