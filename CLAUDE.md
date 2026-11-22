@AGENTS.md

## Claude Code notes

- The maintainer works on Windows: use `.venv/Scripts/python.exe`, and quote
  paths that contain spaces (`Documents/Image-Line/FL Studio/...`).
- FL Studio must be **closed** before editing its registry values
  (`HKCU\Software\Image-Line\FL Studio <N>\Devices\...`). FL rewrites them on exit.
- For live checks, use `python -m fl_studio_mcp doctor` and the bridge log
  (`fLMCP_log.txt` in the bridge folder) rather than screenshots.
