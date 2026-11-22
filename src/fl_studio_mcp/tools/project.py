"""Project-level: new / open / save / render / undo / metadata."""

from __future__ import annotations

from typing import Any, Literal, Optional

from ..bridge_client import get_client
from ._meta import ToolRegistrar


def register(tools: ToolRegistrar) -> None:
    @tools.read("Read Project Overview")
    def project_metadata() -> dict[str, Any]:
        """Read tempo, timing resolution, track/channel/pattern counts, selection and transport state."""
        return get_client().call("project.metadata")

    @tools.write("Create New Project", destructive=True)
    def project_new(template: Optional[str] = None) -> dict[str, Any]:
        """Request a new project, optionally from a template. FL currently requires File > New in the UI."""
        return get_client().call("project.new", template=template)

    @tools.write("Open Project File", destructive=True)
    def project_open(path: str) -> dict[str, Any]:
        """Request opening an existing .flp file. FL currently requires File > Open in the UI."""
        return get_client().call("project.open", path=path)

    @tools.write("Save Current Project", destructive=True, idempotent=True)
    def project_save() -> dict[str, Any]:
        """Trigger FL Studio's Save command for the current project."""
        return get_client().call("project.save")

    @tools.write("Save Project As", destructive=True)
    def project_save_as(path: str) -> dict[str, Any]:
        """Trigger FL Studio's Save As dialog. Confirm the requested path in FL Studio."""
        return get_client().call("project.saveAs", path=path)

    @tools.write("Undo Project Edit", destructive=True)
    def project_undo() -> dict[str, Any]:
        """Undo last action."""
        return get_client().call("project.undo")

    @tools.write("Redo Project Edit", destructive=True)
    def project_redo() -> dict[str, Any]:
        """Redo last undone action."""
        return get_client().call("project.redo")

    @tools.read("Read Project Undo History")
    def project_undo_history() -> dict[str, Any]:
        """Read undo entry count, current position and the latest entry or hint where available."""
        return get_client().call("project.undoHistory")

    @tools.write("Create Undo Checkpoint")
    def project_save_undo(name: str, flags: int = 0) -> dict[str, Any]:
        """Push a named entry onto the undo stack before a batch edit."""
        return get_client().call("project.saveUndo", name=name, flags=flags)

    @tools.write("Render Project Audio", destructive=True)
    def project_render(path: str,
                       format: Literal["wav", "mp3", "flac", "ogg"] = "wav",
                       mode: Literal["song", "pattern"] = "song") -> dict[str, Any]:
        """Request song or pattern rendering to the given path. FL currently requires its render dialog."""
        return get_client().call("project.render", path=path, format=format, mode=mode)

    @tools.read("Get FL Studio Version")
    def project_version() -> dict[str, Any]:
        """Return FL Studio version string."""
        return get_client().call("project.version")
