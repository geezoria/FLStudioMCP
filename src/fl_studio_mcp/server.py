"""fLMCP MCP server.

Run with:
    python -m fl_studio_mcp
or:
    flmcp
"""

from __future__ import annotations

import logging
import os
import sys

from anyio import Lock
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver import MCPServer

from . import __version__, prompts
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
        "flmcp",
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
            "Piano-roll tools edit the piano roll that is open in FL; pass channel (and "
            "pattern) to open the right one first. If a result has ok=false, its note says "
            "why the edit did not run and nothing was changed. Piano-roll bar units use four "
            "quarter notes per bar. Prefer high-level generators for drum grooves, chord "
            "progressions and basslines. Read the target state back after writes. "
            "Use project_undo when an edit needs to be reverted."
        ),
        middleware=[serialize_fl_calls],
    )

    _toolsets.register(mcp)
    r_project.register(mcp)
    prompts.register(mcp)

    log.info("flmcp %s initialised", __version__)
    return mcp


def main() -> None:
    app = build_app()
    app.run()


if __name__ == "__main__":
    main()
