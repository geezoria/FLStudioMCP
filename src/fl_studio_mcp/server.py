"""fl-studio-mcp entry point.

Run with:
    python -m fl_studio_mcp
or:
    fl-studio-mcp
"""

from __future__ import annotations

import logging
import os
import sys

from anyio import Lock
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver import MCPServer

from . import __version__
from .resources import project as r_project
from .tools import _toolsets

LOG_LEVEL = os.environ.get("FL_MCP_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)

log = logging.getLogger("fl_studio_mcp")


def build_app() -> MCPServer:
    fl_state_lock = Lock()

    async def serialize_fl_calls(ctx: ServerRequestContext, call_next: CallNext) -> HandlerResult:
        # MCP 2 runs sync handlers in worker threads. The piano-roll request and
        # state files, selected channel/pattern and FL window focus are shared.
        if ctx.method in {"tools/call", "resources/read"}:
            async with fl_state_lock:
                return await call_next(ctx)
        return await call_next(ctx)

    mcp = MCPServer(
        "fl-studio-mcp",
        title="fLMCP — FL Studio MCP",
        description="Compose, edit, mix and inspect FL Studio projects through MCP.",
        website_url="https://github.com/geezoria/FLStudioMCP",
        version=__version__,
        instructions=(
            "Call fl_ping first to check bridge health. The fLMCP Bridge controller script must "
            "be enabled in FL Studio (Options > MIDI Settings, controller type 'fLMCP Bridge'). "
            "Use fl_list_toolsets to discover enabled tools and how to enable more.\n\n"
            "Channel indices are global and zero-based. Pattern indices are one-based. "
            "Mixer track 0 is Master, inserts start at 1, and FX slots are zero-based. "
            "Use channel_all, pattern_list and mixer_all_tracks to resolve targets by name.\n\n"
            "For piano-roll edits, keep FL Studio in the foreground with the target piano roll "
            "open and ComposeWithLLM selected in its scripts menu. Channel/pattern routing "
            "through the controller bridge is best effort. Piano-roll bar units use four "
            "quarter notes per bar. Prefer high-level generators for drum grooves, chord "
            "progressions and basslines. Read the target state back after writes. "
            "Use project_undo when an edit needs to be reverted."
        ),
        middleware=[serialize_fl_calls],
    )

    _toolsets.register(mcp)
    r_project.register(mcp)

    log.info("fl-studio-mcp initialised")
    return mcp


def main() -> None:
    app = build_app()
    app.run()


if __name__ == "__main__":
    main()
