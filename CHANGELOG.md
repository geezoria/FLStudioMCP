# Changelog

All notable changes to fLMCP are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Live mixer peak metering through `mixer_get_peaks`.
- Human-readable `type_name` values in channel information while preserving the
  raw FL channel type number.
- Punch, overdub and loop-record toggles plus previous/next marker jumps.
- Window visibility/focus inspection and focused-form captions.

### Fixed

- Windows foreground activation now attaches input queues before sending piano-
  roll and Save As shortcuts, so calls from background MCP hosts reach FL Studio
  consistently.
- The polyphonic extra now installs Basic Pitch's runtime-only `resampy` and
  compatible `setuptools` requirements, and dependency checks import the real
  inference entry point instead of accepting a partial namespace install.

### Verified

- The additions were exercised against FL Studio 2025 Producer 25.1.5: all 11
  live checks passed, including non-zero master peaks from a sampler preview. Toggle
  controls were called in pairs and transport, pattern, loop mode and playback
  state were restored after the run.

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
    manual users run `pip install "fl-studio-mcp[polyphonic]"` then
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
