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
python -m fl_studio_mcp install --command python --no-clients   # push the working-tree bridge into FL
```

On Windows use `.venv/Scripts/python.exe`. Run all of ruff, pytest and the TOOLS.md check
before calling work done.

## Adding or changing a tool

1. **Bridge handler.** Write `h_<group>_<name>(params)` in `device_FLStudioMCP.py`
   and register `"<group>.<name>"` in `_HANDLERS`. Validate and clamp params
   and return plain JSON types. Gate APIs newer than FL 2024 with
   `hasattr(module, "fn")` or `general.getVersion()`, and return a structured
   `{"ok": False, "error": ..., "hint": ...}` when they are missing. Don't raise
   for that.
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
  Check the stubs and don't guess.
- **Colors:** FL stores them as `0xRRGGBB`. Use the bridge's color helpers.
- **`channels.setChannelPitch` mode 2 is broken.** Use mode 1 (cents).
- **Edits should be undoable.** Prefer `general.processRECEvent(..., REC_UpdateValue | REC_UpdateControl ...)`
  or `general.saveUndo` around multi-step edits.
- **FL 2026 additions (API 45)** are listed in `docs/FL_API_2026.md`: pattern
  length/clear/duplicate/move/clone, channel swing, float tempo, project
  title/author/genre, mixer track count, plugin mute and more. FL 2024/2025
  lack them, so gate them.
- **Not scriptable at all:** placing or deleting playlist clips, loading
  plugins into slots, opening or creating projects, rendering without UI. See
  `docs/LIMITATIONS.md`. Don't promise these.
- `meta.introspect` (via `fl_call_raw`) dumps every callable and docstring the
  running FL exposes. Use it as ground truth when the stubs and reality
  disagree.

## Conventions

- Python 3.10 syntax on the server side (CI tests 3.10/3.12/3.13 on
  Windows/macOS/Linux). The bridge must compile on 3.12.
- Heavy dependencies (numpy, librosa, sounddevice, dearpygui, basic-pitch) are
  imported **lazily** inside the voice/audio modules. `tests/test_lazy_imports.py`
  enforces this.
- The distribution is named `flmcp`. The import package stays `fl_studio_mcp`.
  The `fl-studio-mcp` console script is a legacy alias.
- Keep `BRIDGE_VERSION` in the bridge equal to `__version__`. `test_cli.py`
  checks this.
- Don't commit runtime files (`mcp_*.json`, `fLMCP_*.json`, `fLMCP_log.txt`).

## Releasing

1. Bump `version` in `pyproject.toml`, `__version__` in
   `src/fl_studio_mcp/__init__.py`, `BRIDGE_VERSION` in the bridge, and both
   version fields in `server.json`.
2. Move *Unreleased* to a dated section in `CHANGELOG.md`.
3. Tag `vX.Y.Z`. Publishing to PyPI and the MCP Registry is done by the
   maintainer.
