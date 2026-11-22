# AGENTS.md — working on fLMCP

Instructions for coding agents (Claude Code, Codex, Cursor, Copilot…) and humans
working in this repository. Keep this file current when the architecture
changes.

## What this is

An MCP server that lets AI clients control FL Studio 2024–2026. Three pieces run
in **three different Python environments**. Know which one you are editing:

| Path | Runs in | Constraints |
| --- | --- | --- |
| `src/fl_studio_mcp/` | The user's Python ≥ 3.10, as a stdio MCP server | Normal Python. Depends on `mcp>=2.3,<3`. **Never print to stdout** — stdout is the MCP channel; log with `logging`. |
| `fl_bridge/device_FLStudioMCP.py` | Inside FL Studio, as a MIDI controller script (Python 3.12, sandboxed) | Only the standard library plus FL modules. **No threads, sockets, subprocess or `os.remove/replace/stat/mkdir`.** Only `open()` I/O. Never block or sleep: every callback runs on FL's UI thread. Must keep working on FL 2024 (API 3x) through FL 2026 (API 45). |
| `fl_bridge/piano_roll/ComposeWithLLM.pyscript` | FL's piano roll, when triggered | The only place `flpianoroll` exists. |
| `mcpb/` | Claude Desktop, as a `.mcpb` bundle run by uv | Only a manifest, an icon and a 3-line `src/server.py` that starts the PyPI `flmcp` package. No code of its own. |

The server and the bridge talk through JSON files (protocol 2). See
`docs/ARCHITECTURE.md` before touching `bridge_client.py`, `protocol.py`,
`midi_wake.py` or the bridge's `_run_bridge_tick`/`OnInit`/`OnIdle`/`OnMidiMsg`.
If you change the wire format, bump `PROTOCOL` on **both** sides.

## Commands

```bash
pip install -e ".[dev]"                  # dev setup (.venv already exists on the maintainer's machine)
ruff check .                             # lint (pyflakes rules)
pytest                                   # offline, ~10 s, no FL needed
python -m py_compile fl_bridge/device_FLStudioMCP.py
python scripts/gen_tools_doc.py          # regenerate docs/TOOLS.md after any tool change (CI checks it)
python -m fl_studio_mcp doctor           # diagnose a live install
python scripts/live_check.py --write     # every tool against a running FL (scratch project; edits are restored)
python -m fl_studio_mcp install --command python --no-clients   # push the working-tree bridge into FL
```

On Windows use `.venv/Scripts/python.exe`. Run all of ruff, pytest and the TOOLS.md check
before calling work done. After a bridge or tool change, push the bridge into FL
and run `live_check.py --write` too: the stubs can't tell you what FL really does.

## Adding or changing a tool

1. **Bridge handler.** Write `h_<group>_<name>(params)` in `device_FLStudioMCP.py`
   and register `"<group>.<name>"` in `_HANDLERS`. Validate and clamp params
   and return plain JSON types. Gate APIs newer than FL 2024 with
   `hasattr(module, "fn")` or `general.getVersion()`, and return a structured
   `{"ok": False, "error": ..., "hint": ...}` when they are missing. Don't raise
   for that. Do raise `ValueError` with a clear message for bad arguments:
   `tools/_meta.py` turns every exception into a `ToolError`, the only kind
   whose text MCP 2 shows the model. Validate mixer tracks with `_mixer_track()`
   and patterns with `_check_pattern_index()`.
2. **MCP tool.** Add it in `src/fl_studio_mcp/tools/<group>.py` with
   `@tools.read("Title")` or `@tools.write("Title", destructive=...)` from
   `tools/_meta.py`. Every tool needs a Title-Case title and correct
   annotations. Return `dict[str, Any]`. The docstring is what the model reads,
   so state units, ranges and index bases.
3. **Toolsets.** New groups go in `GROUPS` in `tools/_toolsets.py`. Consider
   whether the tool belongs in `LEAN_TOOLS`.
4. **Tests.** Add bridge handler tests in `tests/test_bridge_handlers.py`. They
   import the bridge against `fl-studio-api-stubs`, which catches wrong arity
   and wrong argument order. Add server-side tests when the tool has its own
   logic.
5. Run `scripts/gen_tools_doc.py`, then add a `CHANGELOG.md` entry under *Unreleased*.

## FL API pitfalls (verified)

- **Index conventions:** channel indices are global and 0-based; pattern numbers
  are 1-based; mixer track 0 is Master; playlist tracks are 1-based.
- **Argument order:** many getters/setters take `pickupMode` *before*
  `useGlobalIndex` (`plugins.setParamValue(value, param, index, slot, pickupMode, useGlobalIndex)`).
  Check the stubs and don't guess. The stubs can also be wrong:
  `plugins.getParamValueString` takes at most 4 arguments on FL 26.1.7, not the
  stubs' 5. Tests against the stubs can't catch that, so check new plugin and
  mixer calls live.
- **Colors:** FL stores them as `0xRRGGBB`. Use the bridge's color helpers.
- **`channels.setChannelPitch` mode 2 is broken.** Use mode 1 (cents).
- **Edits should be undoable.** Prefer `general.processRECEvent(..., REC_UpdateValue | REC_UpdateControl ...)`
  or `general.saveUndo` around multi-step edits.
- **FL 2026 additions (API 45)** are listed in `docs/FL_API_2026.md`: pattern
  length/clear/duplicate/move/clone, channel swing, float tempo, project
  title/author/genre, mixer track count, plugin mute and more. FL 2024/2025
  lack them, so gate them.
- **Not scriptable at all:** placing or deleting playlist clips, loading
  instruments into the channel rack, opening or creating projects, rendering
  without UI. See
  `docs/LIMITATIONS.md`. Don't promise these.
- **Effects load through `ui.launchAudioEditor`.** `launchAudioEditor(False, "",
  track, preset, "")` loads any .fst (a Plugin database entry) into the track's
  next free FX slot. `preset` is relative to `<FL>/Data/System`. The bridge
  can't stat files, so the server resolves the preset (`plugin_database.py`)
  and the bridge diffs slot names to report what loaded.
- **FX slot switches are REC events.** `mixer.getTrackPluginId(track, slot) +
  midi.REC_Plug_Mute` (the slot's on/off, 1 = on) and `+ midi.REC_Plug_MixLevel`.
  FL's `midi.py` has these constants but the API stubs don't, so look them up
  with `getattr`.
- **Readbacks lag within a callback.** `mixer.getPluginMixLevel` and the
  recording-option getters (`ui.isMetronomeEnabled`, `isLoopRecEnabled`…)
  return the old value until the callback ends. Handlers report the requested
  state instead of reading it back. Overdub has no getter at all.
- **The user data folder moves.** `paths.user_data_dir()` reads
  `HKCU\Software\Image-Line\Shared\Paths\Shared data`, then the real
  Documents folder (OneDrive), and `FLMCP_USER_DATA_DIR` overrides both.
- **No background tick on FL 2026.** FL 26.1.7 calls neither `OnIdle` nor
  `OnUpdateMeters` for the bridge, so commands only run when the MIDI wake (CC 119,
  ch 16 on the `fLMCP` port) fires `OnMidiMsg`. Anything "async" (automation
  recording) advances on wakes, not on a timer.
- **Focused plugins steal the transport.** `transport.start/stop/record` act on a
  focused plugin window that has its own transport (Edison). Call the bridge's
  `_song_transport()` before driving the song transport.
- **Arming Record opens a prompt.** Unless the user ticked "Don't show this in
  the future", `transport.record()` opens FL's modal "Recording" dialog ("What
  would you like to record?"). While it is open, `ui.getFocusedFormCaption()`
  returns `"Recording"`, `isRecording()` reports 1, and every edit raises
  "Operation unsafe at current time". `transport.start()` closes it **and cancels
  the arm**, and so does calling `start()` in the same callback that armed.
  `start()` toggles play/pause, and `isPlaying`/`isRecording` lag within a
  callback. The automation recorder is a state machine over ticks for this
  reason. The server answers the prompt on Windows (`fl_windows.py`).
- **Controller assignment lives in the registry** (Windows):
  `HKCU\Software\Image-Line\FL Studio <N>\Devices\MIDI input\<port>` needs
  `Type="-3"` (script), `ScriptFolder="fLMCP Bridge"`, `Enabled="1"`. With any other
  `Type`, FL ignores `ScriptFolder`.
- **Mixer size varies.** FL 2026 projects can have fewer than 125 inserts
  (the default template has 16). `mixer.trackCount()` counts Master, the
  inserts and a hidden "Current" track. Out-of-range indices raise "Index out
  of range" or, for `channels.setTargetFxTrack`, clamp silently.
- **Piano-roll scripts run from the Tools menu.** There is no API to run one.
  The server posts a click on the piano roll's caption icon, then Up × *n* and
  Enter to FL's `TQuickPopupMenuWindow` (`fl_windows.run_piano_roll_script`).
  The menu ignores posted mouse coordinates but takes posted keys, and lists
  the user's and FL's built-in scripts sorted together, so *n* is counted from
  the end of that list. FL 26.1.7's `patterns.clearPattern` doesn't ask for
  confirmation.
- **One FL at a time.** Two FL processes share the bridge folder, race for
  commands and can both execute one call. `doctor` warns about it.
- `meta.introspect` (via `fl_call_raw`) dumps every callable and docstring the
  running FL exposes. Use it as ground truth when the stubs and reality
  disagree.

## Conventions

- Python 3.10 syntax on the server side (CI tests 3.10/3.12/3.13 on
  Windows/macOS/Linux). The bridge must compile on 3.12.
- Heavy dependencies (numpy, librosa, sounddevice, dearpygui, basic-pitch) are
  imported **lazily** inside the voice/audio modules. `tests/test_lazy_imports.py`
  enforces this.
- The PyPI distribution is named `flmcp-studio` (PyPI rejects `flmcp` as too similar to
  `f1-mcp`). Its main command is `flmcp`, with a `flmcp-studio` alias so `uvx flmcp-studio`
  works. The import package stays `fl_studio_mcp`, and the MCP client entry is `flmcp`.
  The `fl-studio-mcp` console script is a legacy alias.
- Keep `BRIDGE_VERSION` in the bridge equal to `__version__`. `test_cli.py`
  checks this.
- Don't commit runtime files (`mcp_*.json`, `fLMCP_*.json`, `fLMCP_log.txt`).

## Releasing

1. Bump the version everywhere `tests/test_release_metadata.py` checks:
   `version` in `pyproject.toml`, `__version__` in
   `src/fl_studio_mcp/__init__.py`, `BRIDGE_VERSION` in the bridge, both
   version fields in `server.json`, `version` in `mcpb/manifest.json`, and
   `version` plus the `flmcp-studio==` pin in `mcpb/pyproject.toml`.
2. Move *Unreleased* to a dated section in `CHANGELOG.md`.
3. Push the tag `vX.Y.Z`. `.github/workflows/release.yml` checks that it matches
   `pyproject.toml`, publishes to PyPI (trusted publishing, environment `pypi`),
   then to the MCP Registry (GitHub OIDC), and creates the GitHub release with
   the wheel, the sdist and `flmcp-X.Y.Z.mcpb`. Agents push a release tag only
   when the maintainer asks.
