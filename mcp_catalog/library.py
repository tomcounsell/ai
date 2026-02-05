"""MCP Library management system.

Provides intelligent MCP server selection based on task requirements and authentication status.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class AuthStatus(Enum):
    """Authentication status for MCP servers."""

    READY = "ready"
    NEEDS_SETUP = "needs_setup"
    ERROR = "error"


@dataclass
class MCPServer:
    """Represents an MCP server in the catalog."""

    mcp_id: str
    name: str
    category: str
    capabilities: list[str]
    auth_status: AuthStatus
    auth_type: str
    env_var: str | None
    tools: list[str]
    description: str = ""
    setup_instructions: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MCPServer:
        """Create an MCPServer from a dictionary."""
        return cls(
            mcp_id=data["mcp_id"],
            name=data["name"],
            category=data["category"],
            capabilities=data["capabilities"],
            auth_status=AuthStatus(data.get("auth_status", "needs_setup")),
            auth_type=data.get("auth_type", "none"),
            env_var=data.get("env_var"),
            tools=data.get("tools", []),
            description=data.get("description", ""),
            setup_instructions=data.get("setup_instructions", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "mcp_id": self.mcp_id,
            "name": self.name,
            "category": self.category,
            "capabilities": self.capabilities,
            "auth_status": self.auth_status.value,
            "auth_type": self.auth_type,
            "env_var": self.env_var,
            "tools": self.tools,
            "description": self.description,
            "setup_instructions": self.setup_instructions,
        }


class MCPLibrary:
    """MCP Library for managing server catalog and selection."""

    def __init__(self, config_path: Path | None = None):
        """Initialize the MCP Library.

        Args:
            config_path: Path to the config file. Defaults to config/mcp_library.json
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config" / "mcp_library.json"
        self.config_path = config_path
        self.servers: dict[str, MCPServer] = {}
        self.categories: dict[str, list[str]] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Ensure the catalog is loaded before access."""
        if not self._loaded:
            self.load_catalog()

    def load_catalog(self) -> None:
        """Load the MCP server catalog from the config file."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"MCP library config not found: {self.config_path}")

        with open(self.config_path) as f:
            data = json.load(f)

        self.servers = {}
        for server_data in data.get("servers", []):
            server = MCPServer.from_dict(server_data)
            self.servers[server.mcp_id] = server

        self.categories = data.get("categories", {})
        self._loaded = True

    def get_server(self, mcp_id: str) -> MCPServer | None:
        """Get a server by its ID.

        Args:
            mcp_id: The unique identifier of the server.

        Returns:
            The MCPServer if found, None otherwise.
        """
        self._ensure_loaded()
        return self.servers.get(mcp_id)

    def get_by_capability(self, capability: str) -> list[MCPServer]:
        """Find all servers that have a specific capability.

        Args:
            capability: The capability to search for.

        Returns:
            List of servers with the specified capability.
        """
        self._ensure_loaded()
        return [
            server
            for server in self.servers.values()
            if capability in server.capabilities
        ]

    def get_by_category(self, category: str) -> list[MCPServer]:
        """Get all servers in a category.

        Args:
            category: The category name.

        Returns:
            List of servers in the category.
        """
        self._ensure_loaded()
        server_ids = self.categories.get(category, [])
        return [self.servers[sid] for sid in server_ids if sid in self.servers]

    def get_ready_servers(self) -> list[MCPServer]:
        """Get all servers that are authenticated and ready.

        Returns:
            List of servers with auth_status='ready' and valid env vars.
        """
        self._ensure_loaded()
        ready = []
        for server in self.servers.values():
            if self.check_auth_status(server.mcp_id) == AuthStatus.READY:
                ready.append(server)
        return ready

    def check_auth_status(self, mcp_id: str) -> AuthStatus:
        """Check the current authentication status of a server.

        Args:
            mcp_id: The unique identifier of the server.

        Returns:
            The current AuthStatus.

        Raises:
            ValueError: If the server ID is not found.
        """
        self._ensure_loaded()
        server = self.servers.get(mcp_id)
        if not server:
            raise ValueError(f"Unknown server: {mcp_id}")

        # If no auth required, always ready
        if server.auth_type == "none":
            return AuthStatus.READY

        # Check if env var is set
        if server.env_var and not os.environ.get(server.env_var):
            return AuthStatus.NEEDS_SETUP

        # Use catalog status
        return server.auth_status

    def select_for_task(self, capabilities: list[str]) -> list[MCPServer]:
        """Select the minimal set of servers to cover required capabilities.

        Uses a greedy set cover algorithm, preferring authenticated servers.

        Args:
            capabilities: List of required capabilities.

        Returns:
            Minimal list of servers covering all capabilities.
        """
        self._ensure_loaded()
        if not capabilities:
            return []

        remaining = set(capabilities)
        selected: list[MCPServer] = []

        # Sort servers: ready first, then by capability coverage
        all_servers = list(self.servers.values())

        def server_score(s: MCPServer) -> tuple[int, int]:
            is_ready = 1 if self.check_auth_status(s.mcp_id) == AuthStatus.READY else 0
            coverage = len(set(s.capabilities) & remaining)
            return (is_ready, coverage)

        while remaining:
            # Find server with best coverage of remaining capabilities
            best_server = None
            best_coverage: set[str] = set()

            for server in all_servers:
                coverage = set(server.capabilities) & remaining
                if len(coverage) > len(best_coverage):
                    best_server = server
                    best_coverage = coverage
                elif len(coverage) == len(best_coverage) and len(coverage) > 0:
                    # Prefer authenticated servers
                    if self.check_auth_status(server.mcp_id) == AuthStatus.READY:
                        best_server = server
                        best_coverage = coverage

            if best_server is None or not best_coverage:
                break

            selected.append(best_server)
            remaining -= best_coverage

        return selected

    def get_auth_instructions(self, mcp_id: str) -> str:
        """Get setup instructions for authenticating a server.

        Args:
            mcp_id: The unique identifier of the server.

        Returns:
            Setup instructions string.

        Raises:
            ValueError: If the server ID is not found.
        """
        self._ensure_loaded()
        server = self.servers.get(mcp_id)
        if not server:
            raise ValueError(f"Unknown server: {mcp_id}")

        if server.setup_instructions:
            return server.setup_instructions

        if server.auth_type == "none":
            return f"{server.name} requires no authentication setup."

        if server.env_var:
            return f"Set the {server.env_var} environment variable with your {server.name} credentials."

        return f"Please configure authentication for {server.name}."

    def get_all_capabilities(self) -> set[str]:
        """Get all unique capabilities across all servers.

        Returns:
            Set of all capability strings.
        """
        self._ensure_loaded()
        capabilities: set[str] = set()
        for server in self.servers.values():
            capabilities.update(server.capabilities)
        return capabilities

    def get_servers_needing_setup(self) -> list[MCPServer]:
        """Get all servers that need authentication setup.

        Returns:
            List of servers with auth_status != 'ready'.
        """
        self._ensure_loaded()
        return [
            server
            for server in self.servers.values()
            if self.check_auth_status(server.mcp_id) != AuthStatus.READY
        ]

    def list_all_servers(self) -> list[MCPServer]:
        """Get all servers in the catalog.

        Returns:
            List of all MCPServer instances.
        """
        self._ensure_loaded()
        return list(self.servers.values())

    def find_server_for_tool(self, tool_name: str) -> MCPServer | None:
        """Find which server provides a specific tool.

        Args:
            tool_name: The name of the tool to find.

        Returns:
            The MCPServer that provides the tool, or None if not found.
        """
        self._ensure_loaded()
        for server in self.servers.values():
            if tool_name in server.tools:
                return server
        return None

    def get_tool_to_server_map(self) -> dict[str, str]:
        """Create a mapping of tool names to server IDs.

        Returns:
            Dictionary mapping tool names to their server's mcp_id.
        """
        self._ensure_loaded()
        tool_map: dict[str, str] = {}
        for server in self.servers.values():
            for tool in server.tools:
                tool_map[tool] = server.mcp_id
        return tool_map
