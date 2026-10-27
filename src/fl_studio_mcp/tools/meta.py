"""Connection + introspection tools for the bridge itself.

Two independent bridges exist:
  * MIDI bridge — JSON file exchange woken through loopMIDI, handled by the
    FL Studio MIDI controller script on the main thread.
  * Piano-roll file bridge — works without MIDI, limited to piano-roll
    operations, via the `ComposeWithLLM.pyscript`.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ..bridge_client import get_client
from ..file_bridge import is_installed as pr_installed, read_state as pr_state, PR_DIR


def _midi_status() -> dict:
    try:
        info = get_client().ping()
        return {"online": True, **info}
    except Exception as e:
        return {
            "online": False,
            "error": str(e),
            "hint": (
                "MIDI bridge needs the 'fLMCP Bridge' device script enabled on the "
                "same loopMIDI input that the MCP server uses as its output. Check "
                "FL Studio: Options > MIDI > Input and FL_STUDIO_MCP_MIDI_PORT."
            ),
        }


def _piano_roll_status() -> dict:
    return {
        "installed": pr_installed(),
        "pyscript_dir": str(PR_DIR),
        "last_state": pr_state(),
        "hint": (
            "ComposeWithLLM is selected automatically on the first edit if "
            "Ctrl+Alt+Y has no active piano-roll script."
        ),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def fl_ping() -> dict:
        """Report health of BOTH bridges.

        Returns:
            {
              "midi_bridge":   {"online": bool, ...},
              "piano_roll":    {"installed": bool, ...},
              "available_capabilities": [...],
            }
        """
        midi = _midi_status()
        pr = _piano_roll_status()
        caps = []
        if midi.get("online"):
            caps.extend(["transport", "mixer", "channels", "patterns",
                         "plugins", "playlist", "arrangement", "automation",
                         "project", "ui"])
        if pr.get("installed"):
            caps.extend(["piano_roll", "generators(piano-roll emit)"])
        return {
            "midi_bridge": midi,
            "piano_roll": pr,
            "available_capabilities": sorted(set(caps)),
        }

    @mcp.tool()
    def fl_reconnect() -> dict:
        """Reset cached bridge status, then run a fresh file/MIDI ping."""
        get_client().close()
        return _midi_status()

    @mcp.tool()
    def fl_bridge_info() -> dict:
        """Detailed info about the MIDI bridge (fails if it's offline)."""
        try:
            return get_client().call("meta.info")
        except Exception as e:
            return {"ok": False, "error": str(e),
                    "hint": "MIDI bridge offline. fl_ping shows piano-roll fallback status."}

    @mcp.tool()
    def fl_call_raw(action: str, params: dict | None = None) -> dict:
        """Escape hatch: invoke any action the MIDI bridge accepts with arbitrary params."""
        return get_client().call(action, **(params or {}))
