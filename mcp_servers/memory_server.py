"""FastMCP server exposing memory_get and memory_search to Claude Code.

Pairs with the progressive-disclosure recall path: stub `<thought>`
blocks carry only `[category] one-line title` plus an `id="mem_xyz"`
attribute. When the agent decides a stub is relevant, it calls
``memory_get(id)`` here to pull the full body. ``memory_search(query, …)``
provides active mid-task recall.

Both tools wrap their bodies in try/except and return structured
``{"error": "..."}`` dicts on failure (FastMCP serializes these as
valid tool responses — the agent sees a description rather than a
protocol error).

Transport: stdio (default). Cold-start budget: <500ms (asserted in
``tests/integration/test_memory_mcp_server.py::test_cold_start_latency``).
Imports inside tool bodies keep startup minimal.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Project root must be importable when invoked as ``python -m mcp_servers.memory_server``
# from a stripped-environment Claude Code subprocess. ``PYTHONPATH`` registered in
# ``~/.claude.json`` is the canonical mechanism, but be defensive in case the env
# inherits without it.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("memory")


@mcp.tool()
def memory_get(memory_id: str) -> dict:
    """Fetch full content + metadata for a memory by ID.

    Args:
        memory_id: The memory_id from a stub `<thought id="...">` block.

    Returns:
        Dict with the keys: content, title, category, tags, importance,
        source, memory_id. Returns ``{"error": "..."}`` on bad input,
        not-found, or any exception.
    """
    try:
        if not memory_id or not isinstance(memory_id, str):
            return {"error": "memory_id required"}

        from models.memory import Memory

        try:
            record = Memory.query.filter(memory_id=memory_id).first()
        except Exception as e:
            return {"error": f"memory lookup failed: {type(e).__name__}: {e}"}

        if record is None:
            return {"error": f"memory not found: {memory_id}"}

        meta = getattr(record, "metadata", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        category = meta.get("category", "memory")
        tags = meta.get("tags", []) or []

        return {
            "memory_id": getattr(record, "memory_id", memory_id),
            "content": getattr(record, "content", ""),
            "title": getattr(record, "title", "") or "",
            "category": category,
            "tags": list(tags),
            "importance": float(getattr(record, "importance", 0.0) or 0.0),
            "source": getattr(record, "source", ""),
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"memory_get failed: {type(e).__name__}: {e}"}


@mcp.tool()
def memory_search(
    query: str,
    category: str | None = None,
    tag: str | None = None,
    limit: int = 5,
) -> dict:
    """Search memories by query string. Returns compact stubs.

    Stub results carry ``{id, category, title, score}`` only — fetch
    full bodies via ``memory_get(id)`` for the ones you want to use.

    Args:
        query: Free-text BM25 query.
        category: Optional metadata.category filter
            (correction / decision / pattern / surprise).
        tag: Optional metadata.tag filter.
        limit: Max results (default 5).

    Returns:
        Dict with ``results`` (list of stub dicts) and ``error``
        (None on success, string on failure). Empty query returns
        ``{"results": [], "error": null}``.
    """
    try:
        if not query or not isinstance(query, str) or not query.strip():
            return {"results": [], "error": None}

        try:
            limit_int = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            limit_int = 5

        from tools.memory_search import search as _search

        raw = _search(query, category=category, tag=tag, limit=limit_int)
        if not isinstance(raw, dict):
            return {"results": [], "error": "unexpected search shape"}

        if raw.get("error"):
            return {"results": [], "error": str(raw["error"])}

        stubs = []
        for item in raw.get("results", []) or []:
            if not isinstance(item, dict):
                continue
            meta = item.get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {}
            stubs.append(
                {
                    "id": item.get("memory_id", ""),
                    "category": meta.get("category", "memory"),
                    "title": (item.get("title") or "").strip(),
                    "score": float(item.get("score", 0.0) or 0.0),
                }
            )

        return {"results": stubs, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"results": [], "error": f"memory_search failed: {type(e).__name__}: {e}"}


def main() -> None:
    """Entry point for `python -m mcp_servers.memory_server`."""
    if "--help" in sys.argv or "-h" in sys.argv:
        print(
            "FastMCP server: memory\n"
            "Tools: memory_get(memory_id), "
            "memory_search(query, category=None, tag=None, limit=5)\n"
            "Transport: stdio (no args required)",
            file=sys.stderr,
        )
        return
    if os.environ.get("MCP_MEMORY_DRY_RUN") == "1":
        # Used by integration tests / cold-start smoke check to
        # confirm import resolution without binding stdio.
        print("memory MCP ready", flush=True)
        return
    mcp.run()


if __name__ == "__main__":
    main()
