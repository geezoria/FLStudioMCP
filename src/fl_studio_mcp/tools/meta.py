"""Health, tool discovery and low-level bridge access."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .. import __version__
from ..bridge_client import get_client
from ..file_bridge import is_installed as pr_installed, read_state as pr_state, PR_DIR
from ._meta import ToolRegistrar


def _midi_status() -> dict:
    try:
        info = get_client().ping()
    except Exception as e:
        return {
            "online": False,
            "error": str(e),
            "hint": (
                "Enable the fLMCP Bridge controller script in FL Studio "
                "(Options > MIDI Settings, controller type 'fLMCP Bridge'). "
                "Run `flmcp doctor` in a terminal for a full diagnosis."
            ),
        }
    status = {"online": True, **info}
    bridge_version = info.get("bridge_version")
    if bridge_version and bridge_version != __version__:
        status["update_hint"] = (
            f"FL is running bridge script {bridge_version} but the server is {__version__}. "
            "Run `flmcp install`, then restart FL Studio (or MIDI Settings > Update scripts)."
        )
    return status


def _piano_roll_status() -> dict:
    return {
        "installed": pr_installed(),
        "pyscript_dir": str(PR_DIR),
        "last_state": pr_state(),
        "hint": (
            "Open any channel's piano roll, pick `ComposeWithLLM` from the "
            "piano-roll scripts dropdown once. No MIDI device required."
        ),
    }


def register(tools: ToolRegistrar, describe_toolsets: Callable[[], dict[str, Any]]) -> None:
    @tools.read("Check FL Studio Bridge Health")
    def fl_ping() -> dict[str, Any]:
        """Check controller bridge connectivity and piano-roll script installation.

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

    @tools.write("Reconnect FL Studio Bridge")
    def fl_reconnect() -> dict[str, Any]:
        """Reset the controller bridge client and return the new health status."""
        get_client().close()
        return _midi_status()

    @tools.read("Get FL Studio Bridge Details")
    def fl_bridge_info() -> dict[str, Any]:
        """Detailed info about the MIDI bridge (fails if it's offline)."""
        try:
            return get_client().call("meta.info")
        except Exception as e:
            return {"ok": False, "error": str(e),
                    "hint": "MIDI bridge offline. fl_ping shows piano-roll fallback status."}

    @tools.write("Call FL Studio Bridge Action", destructive=True)
    def fl_call_raw(action: str, params: dict | None = None) -> dict[str, Any]:
        """Escape hatch: invoke any action the MIDI bridge accepts with arbitrary params."""
        return get_client().call(action, **(params or {}))

    @tools.read("Inspect FL Studio Scripting API")
    def fl_introspect(
        module: str | None = None,
        search: str | None = None,
        include_docs: bool = False,
    ) -> dict[str, Any]:
        """List the functions the running FL Studio actually exposes to scripts.

        Use this when unsure whether this FL version supports something (FL 2024,
        2025 and 2026 differ). `module` limits to one FL module (e.g. "patterns",
        "mixer", "general"); `search` keeps names containing that text
        (case-insensitive); `include_docs` adds FL's docstrings (larger output).
        """
        params: dict[str, Any] = {}
        if module:
            params["modules"] = [module]
        data = get_client().call("meta.introspect", **params)
        needle = (search or "").casefold()
        modules: dict[str, Any] = {}
        for name, info in (data.get("modules") or {}).items():
            items = [c for c in info.get("callables", []) if needle in c["name"].casefold()]
            if needle and not items:
                continue
            modules[name] = ([{"name": c["name"], "doc": c.get("doc", "")} for c in items]
                             if include_docs else [c["name"] for c in items])
        return {
            "fl_version": data.get("fl_version"),
            "api_version": data.get("api_version"),
            "python_version": (data.get("python_version") or "").split(" ")[0],
            "modules": modules,
        }

    @tools.read("List fLMCP Toolsets")
    def fl_list_toolsets() -> dict[str, Any]:
        """Show available groups and presets, enabled tools, and startup configuration."""
        return describe_toolsets()
