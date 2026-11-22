"""MCP resources surfacing live FL Studio state as read-only URIs."""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from ..bridge_client import get_client


def register(mcp: MCPServer) -> None:
    @mcp.resource("fl://status", title="FL Studio Bridge Status", mime_type="application/json")
    def status() -> str:
        """Bridge + transport quick status."""
        try:
            info = get_client().ping()
            t = get_client().call("transport.status")
            return json.dumps({"bridge": info, "transport": t}, indent=2)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}, indent=2)

    @mcp.resource("fl://project", title="FL Studio Project", mime_type="application/json")
    def project() -> str:
        """Project snapshot with BPM, timing resolution, counts and selection."""
        try:
            return json.dumps(get_client().call("project.metadata"), indent=2)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}, indent=2)

    @mcp.resource("fl://transport", title="FL Studio Transport", mime_type="application/json")
    def transport() -> str:
        """Live transport state (is_playing, position, tempo, loop mode)."""
        try:
            return json.dumps(get_client().call("transport.status"), indent=2)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}, indent=2)

    @mcp.resource("fl://channels", title="FL Studio Channel Rack", mime_type="application/json")
    def channels() -> str:
        """Full channel rack as JSON."""
        try:
            return json.dumps(get_client().call("channels.all"), indent=2)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}, indent=2)

    @mcp.resource("fl://mixer", title="FL Studio Mixer", mime_type="application/json")
    def mixer() -> str:
        """All mixer tracks as JSON."""
        try:
            return json.dumps(get_client().call("mixer.allTracks", include_empty=False), indent=2)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}, indent=2)

    @mcp.resource("fl://patterns", title="FL Studio Patterns", mime_type="application/json")
    def patterns() -> str:
        """All patterns in the project."""
        try:
            return json.dumps(get_client().call("patterns.list"), indent=2)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}, indent=2)

    @mcp.resource("fl://playlist", title="FL Studio Playlist", mime_type="application/json")
    def playlist() -> str:
        """Playlist tracks and any clip or marker data the controller bridge can provide."""
        try:
            client = get_client()
            tracks = client.call("playlist.allTracks", include_empty=False)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}, indent=2)

        data = {"tracks": tracks}
        for key, action, params in (
            ("clips", "playlist.listClips", {"track": None}),
            ("markers", "playlist.listMarkers", {}),
        ):
            try:
                data[key] = client.call(action, **params)
            except Exception as e:
                data[key] = {"ok": False, "error": str(e)}
        return json.dumps(data, indent=2)
