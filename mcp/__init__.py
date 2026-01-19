"""MCP (Model Context Protocol) integration module.

This module provides tools for managing MCP servers, including:
- Server catalog management
- Capability-based server selection
- Authentication status tracking
- Minimal server set selection for tasks

Example usage:
    from mcp import MCPLibrary, AuthStatus

    library = MCPLibrary()
    library.load_catalog()

    # Find servers for a task
    servers = library.select_for_task(["code", "issues", "deployment"])

    # Check what's available
    ready = library.get_ready_servers()
    print(f"{len(ready)} servers ready to use")
"""

from mcp.library import AuthStatus, MCPLibrary, MCPServer

__all__ = ["AuthStatus", "MCPLibrary", "MCPServer"]
