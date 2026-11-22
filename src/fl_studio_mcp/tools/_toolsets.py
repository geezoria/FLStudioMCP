"""Startup selection and discovery of fLMCP tool groups."""

from __future__ import annotations

import logging
import os
from functools import partial
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import (
    arrangement, audio, automation, channels, generators, meta, mixer, patterns,
    piano_roll, playlist, plugins, project, transport, ui, voice,
)
from ._meta import ToolRegistrar

log = logging.getLogger(__name__)

GROUPS = (
    ("meta", "Bridge health, reconnection, raw access and tool discovery", meta),
    ("transport", "Playback, recording, tempo and time signature", transport),
    ("patterns", "Pattern selection and management", patterns),
    ("channels", "Channel Rack, channel settings and step sequencer", channels),
    ("mixer", "Mixer inserts, routing, EQ and FX slots", mixer),
    ("plugins", "Plugin parameters, presets and editors", plugins),
    ("playlist", "Playlist tracks, clip capabilities and timeline markers", playlist),
    ("arrangement", "Arrangement and timeline navigation", arrangement),
    ("automation", "Record parameter automation", automation),
    ("project", "Project metadata, saving, undo and rendering", project),
    ("ui", "FL Studio windows and Channel Rack focus", ui),
    ("piano_roll", "Read and edit notes using ComposeWithLLM", piano_roll),
    ("generators", "Scales, chords, melodies, basslines and drum patterns", generators),
    ("voice", "Microphone recording and audio-to-MIDI transcription", voice),
    ("audio", "Audio analysis, slicing and Drum & Bass workflows", audio),
)

PRESETS = {
    "all": "Every tool group (default)",
    "core": "Bridge tools, transport and essential project operations",
    "lean": "Bridge diagnostics and common tools for composing in patterns",
}
DISCOVERY_TOOLS = {"fl_ping", "fl_list_toolsets"}
CORE_PROJECT_TOOLS = {
    "project_metadata", "project_save", "project_undo", "project_redo", "project_undo_history",
}
LEAN_TOOLS = {
    "fl_bridge_info", "fl_reconnect",
    "transport_play", "transport_stop", "transport_status", "transport_set_position",
    "transport_set_tempo", "transport_set_loop_mode", "transport_set_time_signature",
    "pattern_current", "pattern_list", "pattern_create", "pattern_select",
    "channel_all", "channel_info", "channel_select", "channel_set_volume",
    "channel_get_step_sequence", "channel_set_step_sequence",
    "mixer_all_tracks", "mixer_track_info", "mixer_set_volume",
    "piano_roll_status", "piano_roll_read", "piano_roll_add_notes", "piano_roll_add_chord",
    "piano_roll_quantize",
}


def _env_names(key: str) -> set[str]:
    return {name.strip().lower() for name in os.environ.get(key, "").split(",") if name.strip()}


def register(mcp: MCPServer) -> None:
    requested = _env_names("FLMCP_TOOLSETS") or {"all"}
    disabled = _env_names("FLMCP_DISABLE_TOOLS")
    groups = {name: {"description": description, "enabled_tools": [], "tool_count": 0}
              for name, description, _ in GROUPS}
    known_tools: set[str] = set()
    unknown_groups = requested.difference(groups, PRESETS)
    if unknown_groups:
        log.warning("Unknown FLMCP_TOOLSETS entries: %s", ", ".join(sorted(unknown_groups)))

    full_groups = requested | ({"meta", "transport"} if "core" in requested else set())

    def include(group: str, name: str) -> bool:
        known_tools.add(name)
        details = groups[group]
        details["tool_count"] += 1
        enabled = name not in disabled and (
            name in DISCOVERY_TOOLS
            or "all" in requested
            or group in full_groups
            or ("core" in requested and name in CORE_PROJECT_TOOLS)
            or ("lean" in requested and (group == "generators" or name in LEAN_TOOLS))
        )
        if enabled:
            details["enabled_tools"].append(name)
        return enabled

    def describe() -> dict[str, Any]:
        return {
            "selection": sorted(requested),
            "presets": PRESETS,
            "groups": {
                name: {**details, "enabled_count": len(details["enabled_tools"])}
                for name, details in groups.items()
            },
            "disabled_tools": sorted(disabled & known_tools),
            "enabled_tool_count": sum(len(details["enabled_tools"]) for details in groups.values()),
            "configuration": (
                "Set FLMCP_TOOLSETS to a comma-separated list of groups or presets, then restart "
                "the server. Selections combine. fl_ping and fl_list_toolsets are included in every "
                "selection. FLMCP_DISABLE_TOOLS excludes comma-separated tool names from any selection."
            ),
        }

    for group, _, module in GROUPS:
        tools = ToolRegistrar(mcp, partial(include, group))
        if group == "meta":
            meta.register(tools, describe)
        else:
            module.register(tools)

    unknown_tools = disabled - known_tools
    if unknown_tools:
        log.warning("Unknown FLMCP_DISABLE_TOOLS entries: %s", ", ".join(sorted(unknown_tools)))
