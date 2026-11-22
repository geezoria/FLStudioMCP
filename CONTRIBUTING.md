# Contributing to fLMCP

Thanks for helping make FL Studio + AI better! Bug reports, new tools, docs and
"I tried it on FL 21/2024/2025/2026 and here is what happened" reports are all
welcome.

## Layout

| Path | What runs where |
|---|---|
| `fl_bridge/device_FLStudioMCP.py` | Runs **inside FL Studio** (sandboxed Python 3.12). Only `open()` file I/O, no threads, no sockets. |
| `fl_bridge/piano_roll/ComposeWithLLM.pyscript` | Runs inside FL's piano roll when you trigger it (Ctrl+Alt+Y). |
| `src/fl_studio_mcp/` | The MCP server (normal Python ≥ 3.10) that your AI client starts. |
| `src/fl_studio_mcp/tools/` | One module per toolset; each tool maps to one bridge action. |
| `tests/` | Offline tests. The bridge is imported with `fl-studio-api-stubs`, so no FL is needed. |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the file protocol and
[docs/FL_API_2026.md](docs/FL_API_2026.md) for the newest FL scripting API.

## Dev setup

```bash
git clone https://github.com/geezoria/FLStudioMCP && cd FLStudioMCP
python -m venv .venv && .venv/Scripts/activate      # macOS: source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && pytest
flmcp install --dev        # copy the bridge into FL's user data folder (re-run after editing it)
flmcp doctor               # check that FL sees the bridge
```

After editing the FL-side script, reload it in FL (*Options → MIDI settings →
select the fLMCP device → Update scripts* or restart FL).

## Adding a tool

1. Add a handler `h_<group>_<name>(params)` in `fl_bridge/device_FLStudioMCP.py`
   and register it in `_HANDLERS` as `"<group>.<name>"`. Validate params, use
   the FL argument order from the manual (mode/pickup *before* `useGlobalIndex`),
   and gate new APIs on `general.getVersion()`.
2. Add the MCP tool in `src/fl_studio_mcp/tools/<group>.py` with a clear
   `title`, correct annotations (read-only / destructive / idempotent) and a
   description that states units, ranges and index bases.
3. Add a test in `tests/test_bridge_handlers.py` (and a server-side test if the
   tool has logic of its own).
4. Note it in `CHANGELOG.md` under *Unreleased*.

## Pull requests

Keep PRs focused, run `ruff check .` and `pytest`, and say in the PR which FL
Studio version you tested with (or that you could not test live).
