@AGENTS.md

## Claude Code notes

- The maintainer works on Windows: use `.venv/Scripts/python.exe`, and quote
  paths that contain spaces (`Documents/Image-Line/FL Studio/...`).
- FL Studio must be **closed** before editing its registry values
  (`HKCU\Software\Image-Line\FL Studio <N>\Devices\...`). FL rewrites them on exit.
- For live checks, use `python -m fl_studio_mcp doctor` and the bridge log
  (`fLMCP_log.txt` in the bridge folder) rather than screenshots.
- To load a changed bridge, run `install --command python --no-clients`, then
  restart FL instead of clicking *Update MIDI scripts*. Close it with
  `(Get-Process FL64).CloseMainWindow()` (no dialog on an unmodified project) and
  start `C:\Program Files\Image-Line\FL Studio 2026\FL64.exe`. The bridge answers
  about 50 s after launch.
- Drive live calls with
  `BridgeClient(timeout=5).call("group.action", **params)`. Params are keyword
  arguments, not a dict.
- Steps that only work through FL's GUI (modal dialogs, focusing a plugin window)
  can be delegated to an agent with desktop computer use.
