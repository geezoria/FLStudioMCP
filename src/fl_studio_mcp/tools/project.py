"""Project-level: new / open / save / render / undo / metadata."""

from __future__ import annotations

from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP

from ..bridge_client import get_client


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def project_metadata() -> dict:
        """Project snapshot: tempo, signature, tracks, channels, patterns, selected, title, path."""
        return get_client().call("project.metadata")

    @mcp.tool()
    def project_new(template: Optional[str] = None) -> dict:
        """Start a new project (optionally from a template path)."""
        return get_client().call("project.new", template=template)

    @mcp.tool()
    def project_open(path: str) -> dict:
        """Open an existing .flp project."""
        return get_client().call("project.open", path=path)

    @mcp.tool()
    def project_save() -> dict:
        """Save the project."""
        return get_client().call("project.save")

    @mcp.tool()
    def project_save_as(path: str) -> dict:
        """Save the active project to a new, explicit .flp path (Windows)."""
        from ..keystroke import save_project_as_windows
        return save_project_as_windows(path)

    @mcp.tool()
    def project_undo() -> dict:
        """Undo last action."""
        return get_client().call("project.undo")

    @mcp.tool()
    def project_redo() -> dict:
        """Redo last undone action."""
        return get_client().call("project.redo")

    @mcp.tool()
    def project_undo_history() -> dict:
        """Return undo history stack."""
        return get_client().call("project.undoHistory")

    @mcp.tool()
    def project_save_undo(name: str, flags: int = 0) -> dict:
        """Push a named entry onto the undo stack before a batch edit."""
        return get_client().call("project.saveUndo", name=name, flags=flags)

    @mcp.tool()
    def project_render(path: str,
                       format: Literal["wav", "mp3", "flac", "ogg"] = "wav",
                       mode: Literal["song", "pattern"] = "song") -> dict:
        """Trigger render to disk (uses FL's render dialog automation; blocks until complete)."""
        return get_client().call("project.render", path=path, format=format, mode=mode)

    @mcp.tool()
    def project_version() -> dict:
        """Return FL Studio version string."""
        return get_client().call("project.version")
