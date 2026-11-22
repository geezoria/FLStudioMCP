"""Register enabled tools with explicit MCP behavior hints."""

from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

_Tool = TypeVar("_Tool", bound=Callable[..., Any])


class ToolRegistrar:
    def __init__(self, server: MCPServer, include: Callable[[str], bool]) -> None:
        self.server = server
        self.include = include

    def read(self, title: str) -> Callable[[_Tool], _Tool]:
        return self._tool(title, read_only=True, idempotent=True, destructive=False)

    def write(
        self, title: str, *, idempotent: bool = False, destructive: bool = False,
    ) -> Callable[[_Tool], _Tool]:
        return self._tool(title, read_only=False, idempotent=idempotent, destructive=destructive)

    def _tool(
        self, title: str, *, read_only: bool, idempotent: bool, destructive: bool,
    ) -> Callable[[_Tool], _Tool]:
        def register(fn: _Tool) -> _Tool:
            if self.include(fn.__name__):
                self.server.add_tool(
                    fn,
                    title=title,
                    annotations=ToolAnnotations(
                        read_only_hint=read_only,
                        idempotent_hint=idempotent,
                        destructive_hint=destructive,
                        open_world_hint=False,
                    ),
                )
            return fn
        return register
