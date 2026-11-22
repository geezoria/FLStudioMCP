@AGENTS.md

## Claude Code notes

- The maintainer works on Windows: use `.venv/Scripts/python.exe`, and quote
  paths that contain spaces (`Documents/Image-Line/FL Studio/...`).
- FL Studio must be **closed** before editing its registry values
  (`HKCU\Software\Image-Line\FL Studio <N>\Devices\...`). FL rewrites them on exit.
- For live checks, use `python -m fl_studio_mcp doctor`,
  `scripts/live_check.py --write` (about 10 s on a scratch project) and the
  bridge log (`fLMCP_log.txt` in the bridge folder) rather than screenshots.
- To load a changed bridge, run `install --command python --no-clients`, then
  restart FL instead of clicking *Update MIDI scripts*. Close it with
  `(Get-Process FL64).CloseMainWindow()` (no dialog on an unmodified project) and
  start `C:\Program Files\Image-Line\FL Studio 2026\FL64.exe`. The bridge answers
  about 50 s after launch.
- Drive live calls with
  `BridgeClient(timeout=5).call("group.action", **params)`. Params are keyword
  arguments, not a dict.
- Handle FL's modal dialogs with Win32 messages, not clicks on the screen. List
  them with `fl_windows.open_dialogs()` (class `TMsgForm`). `WM_CLOSE` cancels a
  dialog, and a posted `WM_LBUTTONDOWN/UP` on one of its `TQuickFocusBtn`
  children answers it, even when the window is covered. To see a dialog, capture
  it with `PrintWindow(hwnd, hdc, 2)` rather than the screen.
- On start FL may show a "Welcome to FL Studio" wizard (`TWelcomeWizard`). The
  bridge doesn't load until it closes (`WM_CLOSE`). Closing FL with a modified
  project asks "Save changes…?" in a `Confirm` dialog. Its buttons, left to
  right, are Yes / No / Cancel.
