"""
File-based bridge for piano roll operations.

Works without loopMIDI / any MIDI device — the MCP server writes a request
file that the `ComposeWithLLM.pyscript` (piano roll pyscript) picks up when
it runs. On Windows the server runs the pyscript from the piano roll's Tools
menu with posted window messages (`fl_windows.run_piano_roll_script`), and falls
back to synthesising Ctrl+Alt+Y ("Run last script again"). The pyscript then
writes a state file we read back.

This complements `bridge_client.py` (commands to the MIDI controller script).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .paths import piano_roll_dir

log = logging.getLogger("fl_studio_mcp.file_bridge")

PR_DIR = piano_roll_dir()
REQUEST_FILE = PR_DIR / "fLMCP_request.json"
STATE_FILE = PR_DIR / "fLMCP_state.json"
RESPONSE_FILE = PR_DIR / "fLMCP_response.json"


SCRIPT_NAME = "ComposeWithLLM"


def is_installed() -> bool:
    """True if the ComposeWithLLM pyscript is present on disk."""
    return (PR_DIR / f"{SCRIPT_NAME}.pyscript").exists()


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
    """Best-effort: ask the controller bridge to open the given channel's piano
    roll, optionally switching pattern first, so subsequently-staged edits land
    on the intended channel.

    If the controller bridge is offline, this
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
    """Queue one or more piano-roll actions, run the pyscript, wait for state.

    If `channel` is given and the controller bridge is online, the correct channel's
    piano roll is opened first (best-effort) so the edits route to it.

    When the server triggered the script but no state came back, the queue is
    emptied, so the edits can't run later by surprise (on the next script run).
    """
    from . import fl_windows
    from .keystroke import send_hotkey_windows

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

    run = fl_windows.run_piano_roll_script(SCRIPT_NAME, PR_DIR)
    if run["ok"]:
        trigger = "tools_menu"
    elif send_hotkey_windows():
        trigger = "hotkey"
    else:
        trigger = None
    state = wait_for_state(wait_sec) if trigger else None
    if trigger and state is None:
        clear_request_queue()

    result = {
        "ok": state is not None,
        "trigger": trigger,
        "hotkey_sent": trigger == "hotkey",
        "staged_actions": len(actions),
        "state": state,
        "note": None,
    }
    if trigger is None:
        result["note"] = (
            f"Could not run {SCRIPT_NAME} ({run['reason']}). Open the piano roll and "
            f"run Tools > Scripts > {SCRIPT_NAME}, or press Ctrl+Alt+Y (Cmd+Opt+Y) "
            "if it was the last script run. The edits are queued until then."
        )
    elif state is None:
        result["note"] = (
            f"{SCRIPT_NAME} did not answer within {wait_sec:g} s, so nothing was "
            f"changed and the queue was emptied. Menu: {run.get('reason') or 'opened'}. "
            "Check that a piano roll is open and no dialog is showing, then retry."
        )
    if open_status is not None:
        result["open_status"] = open_status
    return result


def read_state() -> dict | None:
    """Return the most recent state file without running anything."""
    return _read_json(STATE_FILE)
