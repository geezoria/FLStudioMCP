"""
File-based bridge for piano roll operations.

Works without loopMIDI / any MIDI device — the MCP server writes a request
file that the `ComposeWithLLM.pyscript` (piano roll pyscript) picks up when
it runs. The server triggers the pyscript by synthesising Ctrl+Alt+Y into the
FL Studio window. The pyscript then writes a state file we read back.

This complements `bridge_client.py` (JSON files + MIDI wake-up through the
controller script).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("fl_studio_mcp.file_bridge")


def _piano_roll_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.path.expandvars("%USERPROFILE%")) / "Documents" / "Image-Line" / "FL Studio" / "Settings"
    else:
        base = Path.home() / "Documents" / "Image-Line" / "FL Studio" / "Settings"
    return base / "Piano roll scripts"


PR_DIR = _piano_roll_dir()
REQUEST_FILE = PR_DIR / "fLMCP_request.json"
STATE_FILE = PR_DIR / "fLMCP_state.json"
RESPONSE_FILE = PR_DIR / "fLMCP_response.json"


def is_installed() -> bool:
    """True if the ComposeWithLLM pyscript is present on disk."""
    return (PR_DIR / "ComposeWithLLM.pyscript").exists()


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_request(action: dict) -> None:
    existing = _read_json(REQUEST_FILE)
    if not isinstance(existing, list):
        existing = []
    existing.append(action)
    _write_json(REQUEST_FILE, existing)


def clear_request_queue() -> None:
    _write_json(REQUEST_FILE, [])


def clear_state() -> None:
    try:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
    except Exception:
        pass


def wait_for_state(deadline_sec: float = 3.0) -> dict | None:
    end = time.monotonic() + deadline_sec
    while time.monotonic() < end:
        data = _read_json(STATE_FILE)
        if data is not None:
            return data
        time.sleep(0.05)
    return None


def open_piano_roll(channel: int | None = None, pattern: int | None = None) -> dict:
    """Best-effort: ask the file/MIDI controller bridge to open the given channel's piano
    roll, optionally switching pattern first, so subsequently-staged edits land
    on the intended channel.

    If the controller bridge is offline (no `fLMCP Bridge` MIDI device enabled), this
    is a no-op and edits target whatever piano roll is currently open in FL.
    Requires a channel — FL needs a channel to know which piano roll to show.
    """
    if channel is None:
        return {"opened": False, "reason": "no channel specified — using the currently-open piano roll"}
    try:
        from .bridge_client import get_client
        get_client().call("ui.openPianoRoll", channel=int(channel), pattern=pattern)
        return {"opened": True, "channel": int(channel), "pattern": pattern}
    except Exception as e:
        return {"opened": False,
                "reason": f"MIDI bridge offline ({e}); edits target the currently-open piano roll. "
                          "Open the target channel's piano roll manually for correct routing."}


def stage_and_run(actions: list[dict], wait_sec: float = 3.0,
                  channel: int | None = None, pattern: int | None = None) -> dict:
    """Queue one or more piano-roll actions, fire the hotkey, wait for state.

    If `channel` is given and the controller bridge is online, the correct channel's
    piano roll is opened first (best-effort) so the edits route to it.
    """
    from .keystroke import send_hotkey_windows, select_compose_script_windows

    if not is_installed():
        return {
            "ok": False,
            "error": (
                "ComposeWithLLM.pyscript is not installed. Run the installer "
                "or copy fl_bridge/piano_roll/ComposeWithLLM.pyscript into "
                f"{PR_DIR}"
            ),
        }

    open_status = open_piano_roll(channel, pattern) if channel is not None else None

    clear_state()
    for a in actions:
        _append_request(a)

    started = time.monotonic()
    fired = send_hotkey_windows()
    # A normal hotkey round-trip is effectively immediate.  Do not consume the
    # full tool timeout before repairing the common fresh-install state where
    # ComposeWithLLM has never been selected in the piano-roll Script menu.
    first_wait = min(max(wait_sec, 0.0), 0.8)
    state = wait_for_state(first_wait) if fired else None
    bootstrapped = False
    if state is None and sys.platform == "win32":
        bootstrapped = select_compose_script_windows()
        remaining = max(0.0, wait_sec - (time.monotonic() - started))
        if bootstrapped and remaining > 0:
            state = wait_for_state(remaining)

    result = {
        "ok": bool(state is not None and (fired or bootstrapped)),
        "hotkey_sent": fired,
        "script_bootstrapped": bootstrapped,
        "staged_actions": len(actions),
        "state": state,
        "note": None if state is not None else
            "FL Studio did not produce piano-roll state after Ctrl+Alt+Y and "
            "the automatic ComposeWithLLM menu bootstrap.",
    }
    if open_status is not None:
        result["open_status"] = open_status
    return result


def read_state() -> dict | None:
    """Return the most recent state file without running anything."""
    return _read_json(STATE_FILE)
