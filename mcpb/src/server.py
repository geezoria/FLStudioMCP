"""Entry point of the fLMCP Claude Desktop bundle: start the stdio MCP server."""
import sys

from fl_studio_mcp.cli import main

if __name__ == "__main__":
    sys.exit(main(["serve"]))
