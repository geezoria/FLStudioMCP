"""
File-based bridge for piano roll operations.

Works without loopMIDI / any MIDI device — the MCP server writes a request
file that the `ComposeWithLLM.pyscript` (piano roll pyscript) picks up when
it runs. The server triggers the pyscript by synthesising Ctrl+Alt+Y into the
FL Studio window. The pyscript then writes a state file we read back.

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
    """Queue one or more piano-roll actions, fire the hotkey, wait for state.

    If `channel` is given and the controller bridge is online, the correct channel's
    piano roll is opened first (best-effort) so the edits route to it.
    """
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

    fired = send_hotkey_windows()
    state = wait_for_state(wait_sec) if fired else None

    result = {
        "ok": bool(fired and state is not None),
        "hotkey_sent": fired,
        "staged_actions": len(actions),
        "state": state,
        "note": None if fired else
            "Could not auto-press Ctrl+Alt+Y. Make sure FL Studio is in the "
            "foreground and ComposeWithLLM is the active piano-roll script, "
            "then press Ctrl+Alt+Y manually.",
    }
    if open_status is not None:
        result["open_status"] = open_status
    return result


def read_state() -> dict | None:
    """Return the most recent state file without running anything."""
    return _read_json(STATE_FILE)
