"""Integration tests for the memory MCP server.

Spawns ``python -m mcp_servers.memory_server`` as a stdio subprocess and
calls the ``memory_get`` and ``memory_search`` tools via the official
MCP client. Asserts response shape, error paths, and cold-start latency
budget (<500ms per cycle-3 C4).

These tests touch the real Memory model through the MCP server's
imports — but the tools themselves are wrapped in try/except, so a
missing Redis still yields ``{"error": "..."}`` responses (not
exceptions). The tests assert the contract, not Redis presence.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest


def _project_root() -> str:
    return str(Path(__file__).resolve().parent.parent.parent)


def _server_params():
    from mcp import StdioServerParameters

    env = {
        "PYTHONPATH": _project_root(),
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_servers.memory_server"],
        env=env,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_lists_memory_tools():
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert "memory_get" in names
            assert "memory_search" in names


@pytest.mark.integration
@pytest.mark.asyncio
async def test_memory_get_empty_id_returns_error():
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("memory_get", {"memory_id": ""})

    # Tool result content carries the structured response.
    text_blobs = [c.text for c in result.content if hasattr(c, "text")]
    joined = "\n".join(text_blobs)
    assert "memory_id required" in joined or "error" in joined


@pytest.mark.integration
@pytest.mark.asyncio
async def test_memory_search_empty_query_returns_empty_results():
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("memory_search", {"query": ""})

    text_blobs = [c.text for c in result.content if hasattr(c, "text")]
    joined = "\n".join(text_blobs)
    # FastMCP serializes the dict as JSON in the text content.
    assert '"results"' in joined
    assert '"error": null' in joined or '"error":null' in joined


@pytest.mark.integration
def test_cold_start_latency():
    """Cold-start: spawn → first tool response < 500ms (cycle-3 C4).

    Uses a synchronous subprocess invocation with the dry-run gate
    (``MCP_MEMORY_DRY_RUN=1``) so the test does not depend on the MCP
    protocol session itself — it just measures import + server-init
    cost. Allows one retry to absorb CI jitter.
    """
    import subprocess

    env = {
        "PYTHONPATH": _project_root(),
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "MCP_MEMORY_DRY_RUN": "1",
    }
    cmd = [sys.executable, "-m", "mcp_servers.memory_server"]

    best = None
    for _ in range(2):
        start = time.perf_counter()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=env)
        elapsed_ms = (time.perf_counter() - start) * 1000
        if result.returncode == 0 and "memory MCP ready" in result.stdout:
            best = elapsed_ms if best is None else min(best, elapsed_ms)

    assert best is not None, "MCP server did not start successfully"
    assert best < 500, f"cold start exceeded 500ms budget: {best:.0f}ms"


@pytest.mark.integration
def test_fresh_shell_import_resolution():
    """Stripped-env smoke check (cycle-3 C5).

    The registered MCP command runs in whatever environment Claude Code
    spawns its subprocesses in. Failures here mean the registered
    ``python`` binary cannot resolve project modules with only
    ``PYTHONPATH`` set — and Claude Code sessions would silently break.

    Note: ``PATH`` is set to include /opt/homebrew/bin so the spawned
    ``python3`` resolves to a Python that has the ``mcp`` SDK
    installed. ``/usr/bin/python3`` does not (it is the system Python
    on Darwin).
    """
    import subprocess

    env = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
        "PYTHONPATH": _project_root(),
    }
    result = subprocess.run(
        ["python3", "-m", "mcp_servers.memory_server", "--help"],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert result.returncode == 0, f"fresh-shell import failed: {result.stderr or result.stdout}"
    assert "memory_get" in (result.stderr + result.stdout)
