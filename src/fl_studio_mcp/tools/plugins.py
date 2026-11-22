"""Plugin introspection + parameter control for both channel generators and mixer FX slots."""

from __future__ import annotations

from typing import Any, Literal, Optional

from ..bridge_client import get_client
from ._meta import ToolRegistrar


def register(tools: ToolRegistrar) -> None:
    @tools.read("Search Installed Plugins")
    def plugin_search(
        query: str = "", kind: Literal["effect", "generator", "any"] = "effect", limit: int = 50,
    ) -> dict[str, Any]:
        """Search FL's Plugin database (Fruity plugins plus every VST/VST3/AU the Plugin Manager scanned)
        by name or category words, e.g. "reverb", "limiter", "fabfilter". Pass a returned `name` to
        `mixer_load_effect`. Reads files on this computer; FL doesn't need to answer."""
        from ..plugin_database import search

        found = search(query, None if kind == "any" else kind)
        return {"count": len(found), "plugins": [e.as_dict() for e in found[:max(1, limit)]]}

    @tools.read("Check Plugin Slot")
    def plugin_is_valid(index: int, slot: int = -1, location: Literal["channel", "mixer"] = "channel") -> dict[str, Any]:
        """Check if a plugin slot has a valid plugin loaded."""
        return get_client().call("plugins.isValid", index=index, slot=slot, location=location)

    @tools.read("Get Plugin Name")
    def plugin_name(index: int, slot: int = -1, location: Literal["channel", "mixer"] = "channel") -> dict[str, Any]:
        """Plugin display name."""
        return get_client().call("plugins.name", index=index, slot=slot, location=location)

    @tools.read("Count Plugin Parameters")
    def plugin_param_count(index: int, slot: int = -1, location: Literal["channel", "mixer"] = "channel") -> dict[str, Any]:
        """Return number of automatable parameters."""
        return get_client().call("plugins.paramCount", index=index, slot=slot, location=location)

    @tools.read("List Plugin Parameters")
    def plugin_params(index: int,
                      slot: int = -1,
                      location: Literal["channel", "mixer"] = "channel",
                      limit: int = 128,
                      offset: int = 0) -> dict[str, Any]:
        """List parameter indices, names, normalized values and display strings. Offset is zero-based and limit sets page size."""
        return get_client().call("plugins.params",
                                 index=index, slot=slot, location=location,
                                 limit=limit, offset=offset)

    @tools.read("Read Plugin Parameter")
    def plugin_get_param(index: int, param: int,
                         slot: int = -1, location: Literal["channel", "mixer"] = "channel") -> dict[str, Any]:
        """Read a plugin parameter by 0-based index. Returns its normalized value and display string."""
        return get_client().call("plugins.getParam",
                                 index=index, param=param, slot=slot, location=location)

    @tools.write("Set Plugin Parameter", idempotent=True)
    def plugin_set_param(index: int, param: int, value: float,
                         slot: int = -1, location: Literal["channel", "mixer"] = "channel") -> dict[str, Any]:
        """Set a single parameter (0.0..1.0 normalised)."""
        return get_client().call("plugins.setParam",
                                 index=index, param=param, value=value,
                                 slot=slot, location=location)

    @tools.read("Find Plugin Parameters by Name")
    def plugin_find_param(index: int, name_contains: str,
                          slot: int = -1, location: Literal["channel", "mixer"] = "channel") -> dict[str, Any]:
        """Find a parameter by substring (case-insensitive)."""
        return get_client().call("plugins.findParam",
                                 index=index, name_contains=name_contains,
                                 slot=slot, location=location)

    @tools.read("Count Plugin Presets")
    def plugin_preset_count(index: int, slot: int = -1, location: Literal["channel", "mixer"] = "channel") -> dict[str, Any]:
        """Number of built-in presets for plugin."""
        return get_client().call("plugins.presetCount", index=index, slot=slot, location=location)

    @tools.write("Load Next Plugin Preset", destructive=True)
    def plugin_next_preset(index: int, slot: int = -1, location: Literal["channel", "mixer"] = "channel") -> dict[str, Any]:
        """Advance to the next preset."""
        return get_client().call("plugins.nextPreset", index=index, slot=slot, location=location)

    @tools.write("Load Previous Plugin Preset", destructive=True)
    def plugin_prev_preset(index: int, slot: int = -1, location: Literal["channel", "mixer"] = "channel") -> dict[str, Any]:
        """Go back to the previous preset."""
        return get_client().call("plugins.prevPreset", index=index, slot=slot, location=location)

    @tools.write("Load Plugin Preset by Index", destructive=True, idempotent=True)
    def plugin_set_preset(index: int, preset: int,
                          slot: int = -1, location: Literal["channel", "mixer"] = "channel") -> dict[str, Any]:
        """Request a preset by numeric index. If unsupported, use plugin_next_preset or plugin_prev_preset."""
        return get_client().call("plugins.setPreset",
                                 index=index, preset=preset, slot=slot, location=location)

    @tools.write("Show or Hide Plugin Editor")
    def plugin_show_editor(index: int, slot: int = -1, location: Literal["channel", "mixer"] = "channel",
                           show: Optional[bool] = None) -> dict[str, Any]:
        """Show/hide the plugin editor window (None = toggle)."""
        return get_client().call("plugins.showEditor",
                                 index=index, slot=slot, location=location, show=show)

    @tools.read("List Mixer Track Plugins")
    def plugin_list_mixer_track(track: int) -> dict[str, Any]:
        """List all loaded plugins in every FX slot of a mixer track."""
        return get_client().call("plugins.listMixerTrack", track=track)
