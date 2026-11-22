"""Pattern create / rename / select / clone / delete / length / color."""

from __future__ import annotations

from typing import Any

from ..bridge_client import get_client
from ._meta import ToolRegistrar


def register(tools: ToolRegistrar) -> None:
    @tools.read("Count Patterns")
    def pattern_count() -> dict[str, Any]:
        """Return total pattern count."""
        return get_client().call("patterns.count")

    @tools.read("Get Current Pattern")
    def pattern_current() -> dict[str, Any]:
        """Return index + name of currently selected pattern."""
        return get_client().call("patterns.current")

    @tools.read("List Patterns")
    def pattern_list() -> dict[str, Any]:
        """List patterns with 1-based indices, names, colors and lengths in steps."""
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
    def pattern_clone(index: int, new_name: str = "") -> dict[str, Any]:
        """Clone a pattern (copy all notes & channel grid bits)."""
        return get_client().call("patterns.clone", index=index, new_name=new_name)

    @tools.write("Delete Pattern", destructive=True)
    def pattern_delete(index: int) -> dict[str, Any]:
        """Clear the name of a 1-based pattern. The FL scripting API leaves the pattern and notes in place."""
        return get_client().call("patterns.delete", index=index)

    @tools.write("Set Pattern Length", idempotent=True)
    def pattern_set_length(index: int, bars: float) -> dict[str, Any]:
        """Request pattern length in bars. Currently, extend notes or the step grid to change length."""
        return get_client().call("patterns.setLength", index=index, bars=bars)

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
