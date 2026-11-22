"""Pattern create / rename / select / clone / delete / length / color."""

from __future__ import annotations

from typing import Any

from ..bridge_client import get_client
from ..protocol import RPCError
from ._meta import ToolRegistrar


def register(tools: ToolRegistrar) -> None:
    @tools.read("Count Patterns")
    def pattern_count() -> dict[str, Any]:
        """Count patterns in use (with content or a custom name) and the highest such index."""
        return get_client().call("patterns.count")

    @tools.read("Get Current Pattern")
    def pattern_current() -> dict[str, Any]:
        """Return index + name of currently selected pattern."""
        return get_client().call("patterns.current")

    @tools.read("List Patterns")
    def pattern_list() -> dict[str, Any]:
        """List patterns in use plus the current one: 1-based index, name, color, length in steps (16 = one 4/4 bar)."""
        return get_client().call("patterns.list")

    @tools.write("Select Pattern", idempotent=True)
    def pattern_select(index: int) -> dict[str, Any]:
        """Jump to pattern by index (1-based in FL)."""
        return get_client().call("patterns.select", index=index)

    @tools.write("Create Pattern")
    def pattern_create(name: str = "") -> dict[str, Any]:
        """Create a new empty pattern. Returns {index, name}."""
        return get_client().call("patterns.create", name=name)

    @tools.write("Rename Pattern", idempotent=True)
    def pattern_rename(index: int, name: str) -> dict[str, Any]:
        """Rename an existing pattern."""
        return get_client().call("patterns.rename", index=index, name=name)

    @tools.write("Set Pattern Color", idempotent=True)
    def pattern_set_color(index: int, color: str) -> dict[str, Any]:
        """Set pattern color. Accepts '#RRGGBB' or 'rgb(r,g,b)'."""
        return get_client().call("patterns.setColor", index=index, color=color)

    @tools.write("Clone Pattern")
    def pattern_clone(index: int, new_name: str = "", dest_index: int | None = None) -> dict[str, Any]:
        """Copy a 1-based pattern (notes and step grid) and select the copy.

        Without dest_index FL uses the next free pattern; dest_index (FL 2026.1.1+)
        overwrites that pattern. Returns {source_index, new_index, name}.
        """
        params: dict[str, Any] = {"index": index, "new_name": new_name}
        if dest_index is not None:
            params["dest_index"] = dest_index
        return get_client().call("patterns.clone", **params)

    @tools.write("Delete Pattern", destructive=True)
    def pattern_delete(index: int) -> dict[str, Any]:
        """Clear the name of a 1-based pattern. The FL scripting API leaves the pattern and notes in place."""
        return get_client().call("patterns.delete", index=index)

    @tools.write("Set Pattern Length", idempotent=True)
    def pattern_set_length(
        index: int, bars: float | None = None, beats: float | None = None, steps: int | None = None,
    ) -> dict[str, Any]:
        """Set a 1-based pattern's length; give exactly one of bars, beats or steps. FL Studio 2026+.

        A step is a 16th note (4 per beat); bars follow the project's time
        signature. Returns the resulting length in steps, beats and bars. Older FL
        versions return ok=false; there, extend notes or the step grid instead.
        """
        given = {k: v for k, v in (("bars", bars), ("beats", beats), ("steps", steps)) if v is not None}
        if len(given) != 1:
            raise ValueError("give exactly one of bars, beats or steps")
        return get_client().call("patterns.setLength", index=index, **given)

    @tools.write("Clear Pattern", destructive=True)
    def pattern_clear(index: int) -> dict[str, Any]:
        """Remove all notes and steps from a 1-based pattern, keeping the pattern. FL Studio 2026+.

        FL asks the user to confirm in a dialog; until they answer, FL (and every
        other tool) waits. If nobody answers in time this returns
        pending_confirmation=true: ask the user to click Yes or No in FL Studio.
        Undo with project_undo.
        """
        try:
            return get_client().call("patterns.clear", index=index)
        except RPCError as e:
            if "execution status unknown" not in str(e):
                raise
            return {"ok": False, "pending_confirmation": True, "index": index,
                    "hint": "FL Studio is showing a Confirm dialog for clearing the pattern. "
                            "Ask the user to answer it in FL; the bridge resumes afterwards."}

    @tools.read("Find Pattern by Name")
    def pattern_find_by_name(name: str) -> dict[str, Any]:
        """Find a pattern by exact name (case-insensitive). Missing matches have null index and name."""
        return get_client().call("patterns.findByName", name=name)

    @tools.write("Select Next Pattern")
    def pattern_jump_to_next() -> dict[str, Any]:
        """Select the next pattern in the list."""
        return get_client().call("patterns.jumpNext")

    @tools.write("Select Previous Pattern")
    def pattern_jump_to_previous() -> dict[str, Any]:
        """Select the previous pattern in the list."""
        return get_client().call("patterns.jumpPrev")
