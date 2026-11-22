"""Inspector entry point: `mcp dev scripts/mcp_dev.py --with-editable .`."""

from fl_studio_mcp.server import build_app

mcp = build_app()
