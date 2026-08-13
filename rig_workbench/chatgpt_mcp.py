"""Compatibility import for the former ChatGPT-specific module name.

The server is client-neutral now.  Keep this module silent because warnings on
stdout/stderr can corrupt stdio MCP sessions.
"""
from __future__ import annotations

from .remote_mcp import RigGateway, RigMcpError, create_server, main

__all__ = ["RigGateway", "RigMcpError", "create_server", "main"]


if __name__ == "__main__":
    main()
