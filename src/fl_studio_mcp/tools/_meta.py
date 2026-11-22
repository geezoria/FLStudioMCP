"""Register enabled tools with explicit MCP behavior hints."""

import functools
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from ..protocol import RPCError

_Tool = TypeVar("_Tool", bound=Callable[..., Any])
log = logging.getLogger(__name__)


def explain_errors(fn: _Tool) -> _Tool:
    """Let the model read why a tool failed.

    MCP 2 sends the text of a ToolError to the client but replaces any other
    exception with "Error executing tool X". Bridge errors, timeouts and
    argument checks are all things the model can act on, so pass them on.
    """
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as exc:
            log.debug("%s failed", fn.__name__, exc_info=True)
            text = str(exc) if isinstance(exc, RPCError) else f"{type(exc).__name__}: {exc}"
            raise ToolError(text) from exc
    return wrapper  # type: ignore[return-value]


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
                    explain_errors(fn),
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
