"""Memory search tool: search, save, inspect, and forget memories.

Provides a direct interface to the Memory model for querying, creating,
inspecting, and deleting memory records. Usable from agent sessions
(via direct import) and Claude Code sessions (via CLI).

All public functions follow the fail-silent contract: they wrap their
bodies in try/except and return empty results or None on failure.
No function raises exceptions to callers.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_project_key(project_key: str | None = None) -> str:
    """Resolve project_key from argument or environment."""
    if project_key:
        return project_key
    from config.memory_defaults import DEFAULT_PROJECT_KEY

    return os.environ.get("VALOR_PROJECT_KEY", DEFAULT_PROJECT_KEY)


def search(
    query: str,
    project_key: str | None = None,
    limit: int = 10,
    category: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """Search memories by query string using ContextAssembler.

    Args:
        query: The search query text.
        project_key: Project partition key. Resolved from env if not provided.
        limit: Maximum number of results to return.
        category: Filter by metadata category (correction, decision, pattern, surprise).
        tag: Filter by metadata tag.

    Returns:
        Dict with "results" list and "error" key (None if no error).
        Each result has: content, score, confidence, source, access_count, memory_id, metadata.
    """
    try:
        if not query or not query.strip():
            return {"results": [], "error": None}

        project_key = _resolve_project_key(project_key)

        # Bloom pre-check: if ExistenceFilter says "definitely not present", skip
        from models.memory import Memory

        bloom_field = Memory._meta.fields.get("bloom")
        if bloom_field:
            has_relevant = False
            # Extract simple words from query for bloom check
            import re

            words = re.findall(r"[a-zA-Z]{3,}", query)
            for word in words:
                try:
                    if bloom_field.might_exist(Memory, word):
                        has_relevant = True
                        break
                except Exception:
                    # On bloom error, proceed with full query
                    has_relevant = True
                    break
            if not words:
                has_relevant = True
            if not has_relevant:
                return {"results": [], "error": None}

        # Full assembly via ContextAssembler
        from popoto import ContextAssembler

        assembler = ContextAssembler(
            model_class=Memory,
            score_weights={"relevance": 0.6, "confidence": 0.3},
            max_items=limit,
            max_tokens=2000,
        )
        result = assembler.assemble(
            query_cues={"topic": query},
            agent_id=project_key,
            partition_filters={"project_key": project_key},
        )

        if not result.records:
            return {"results": [], "error": None}

        # Post-retrieval metadata filtering
        filtered_records = result.records
        if category:
            filtered_records = [
                r for r in filtered_records
                if (getattr(r, "metadata", None) or {}).get("category") == category
            ]
        if tag:
            filtered_records = [
                r for r in filtered_records
                if tag in (getattr(r, "metadata", None) or {}).get("tags", [])
            ]

        results = []
        for record in filtered_records[:limit]:
            meta = getattr(record, "metadata", None) or {}
            results.append(
                {
                    "content": getattr(record, "content", ""),
                    "score": getattr(record, "relevance", 0.0),
                    "confidence": getattr(record, "confidence", 0.0),
                    "source": getattr(record, "source", ""),
                    "access_count": getattr(record, "access_count", 0),
                    "memory_id": getattr(record, "memory_id", ""),
                    "metadata": meta,
                }
            )

        return {"results": results, "error": None}

    except Exception as e:
        logger.warning(f"[memory_search] search failed (non-fatal): {type(e).__name__}: {e}")
        return {"results": [], "error": None}


def save(
    content: str,
    importance: float | None = None,
    project_key: str | None = None,
    source: str = "human",
) -> dict[str, Any] | None:
    """Save a new memory record.

    Args:
        content: The memory content text.
        importance: Numeric importance score. Defaults to 6.0 (human weight).
        project_key: Project partition key. Resolved from env if not provided.
        source: Origin type - "human", "agent", or "system".

    Returns:
        Dict with memory_id and content if saved, or None if filtered/failed.
    """
    try:
        if not content or not content.strip():
            return None

        if importance is None:
            importance = 6.0  # InteractionWeight.HUMAN

        project_key = _resolve_project_key(project_key)

        from models.memory import Memory

        record = Memory.safe_save(
            content=content,
            importance=importance,
            source=source,
            project_key=project_key,
            agent_id=project_key,
        )

        if record is None:
            return None

        return {
            "memory_id": getattr(record, "memory_id", ""),
            "content": content,
        }

    except Exception as e:
        logger.warning(f"[memory_search] save failed (non-fatal): {e}")
        return None


def inspect(
    memory_id: str | None = None,
    project_key: str | None = None,
    stats: bool = False,
) -> dict[str, Any]:
    """Inspect a specific memory or get aggregate stats.

    Args:
        memory_id: ID of a specific memory to inspect.
        project_key: Project partition key for stats aggregation.
        stats: If True and no memory_id, return aggregate statistics.

    Returns:
        Dict with memory details, stats, or error guidance.
    """
    try:
        from models.memory import Memory

        if memory_id:
            # Direct lookup by memory_id
            try:
                record = Memory.query.filter(memory_id=memory_id).first()
            except Exception:
                record = None

            if not record:
                return {"error": f"Memory not found: {memory_id}"}

            meta = getattr(record, "metadata", None) or {}
            return {
                "memory_id": getattr(record, "memory_id", ""),
                "content": getattr(record, "content", ""),
                "importance": getattr(record, "importance", 0.0),
                "source": getattr(record, "source", ""),
                "confidence": getattr(record, "confidence", 0.0),
                "access_count": getattr(record, "access_count", 0),
                "project_key": getattr(record, "project_key", ""),
                "agent_id": getattr(record, "agent_id", ""),
                "metadata": meta,
            }

        if stats:
            project_key = _resolve_project_key(project_key)
            # Aggregate stats across project
            try:
                all_records = list(Memory.query.filter(project_key=project_key))
            except Exception:
                all_records = []

            if not all_records:
                return {
                    "project_key": project_key,
                    "total": 0,
                    "by_source": {},
                    "avg_confidence": 0.0,
                }

            by_source: dict[str, int] = {}
            total_confidence = 0.0
            for record in all_records:
                src = getattr(record, "source", "unknown")
                by_source[src] = by_source.get(src, 0) + 1
                total_confidence += getattr(record, "confidence", 0.0)

            return {
                "project_key": project_key,
                "total": len(all_records),
                "by_source": by_source,
                "avg_confidence": total_confidence / len(all_records) if all_records else 0.0,
            }

        return {"error": "Provide --id for a specific memory or --stats for aggregate statistics."}

    except Exception as e:
        logger.warning(f"[memory_search] inspect failed (non-fatal): {e}")
        return {"error": str(e)}


def forget(memory_id: str) -> dict[str, Any]:
    """Delete a memory record by ID.

    Args:
        memory_id: The ID of the memory to delete.

    Returns:
        Dict with deleted=True and memory_id, or error message.
    """
    try:
        if not memory_id or not memory_id.strip():
            return {"error": "memory_id is required", "deleted": False}

        from models.memory import Memory

        try:
            record = Memory.query.filter(memory_id=memory_id).first()
        except Exception:
            record = None

        if not record:
            return {"error": f"Memory not found: {memory_id}", "deleted": False}

        try:
            record.delete()
        except Exception as e:
            return {"error": f"Delete failed: {e}", "deleted": False}

        return {"deleted": True, "memory_id": memory_id}

    except Exception as e:
        logger.warning(f"[memory_search] forget failed (non-fatal): {e}")
        return {"error": str(e), "deleted": False}
