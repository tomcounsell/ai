"""Subconscious memory thought injection for PostToolUse hook.

Checks ExistenceFilter for topic relevance, retrieves memories via
BM25 + RRF fusion, and returns <thought> blocks via additionalContext.

Rate-limited via sliding window: every WINDOW_SIZE tool calls, extracts
topic keywords from the current window plus previous windows in the buffer.

All operations are wrapped in try/except — memory system failures must
never crash or slow down the agent.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from config.memory_defaults import (
    NOVEL_TERRITORY_KEYWORD_THRESHOLD,
)
from config.memory_defaults import (
    INJECTION_BUFFER_SIZE as BUFFER_SIZE,
)
from config.memory_defaults import (
    INJECTION_WINDOW_SIZE as WINDOW_SIZE,
)
from config.memory_defaults import (
    MAX_THOUGHTS_PER_INJECTION as MAX_THOUGHTS,
)

# Re-export keyword utilities from lightweight module (no agent deps).
# Agent-side callers (memory_extraction.py, health_check.py) can still
# import from here without breaking.
from utils.keyword_extraction import (  # noqa: F401
    _NOISE_WORDS,
    _apply_category_weights,
    _cluster_keywords,
    extract_topic_keywords,
)

logger = logging.getLogger(__name__)

# Session-scoped state (in-memory, resets with process)
_tool_buffers: dict[str, list[dict[str, Any]]] = {}
_tool_counts: dict[str, int] = {}
_injected_thoughts: dict[str, list[tuple[str, str]]] = {}


def check_and_inject(
    session_id: str,
    tool_name: str,
    tool_input: Any,
    project_key: str | None = None,
) -> str | None:
    """Check bloom filter and inject thoughts if relevant memories exist.

    Called from watchdog_hook() on every tool call. Uses sliding window
    rate limiting to avoid flooding the context.

    Returns:
        additionalContext string with <thought> blocks, or None.
    """
    try:
        # Increment counter
        _tool_counts[session_id] = _tool_counts.get(session_id, 0) + 1
        count = _tool_counts[session_id]

        # Always record tool call in buffer
        buffer = _tool_buffers.setdefault(session_id, [])
        buffer.append({"tool_name": tool_name, "tool_input": tool_input})
        if len(buffer) > BUFFER_SIZE:
            buffer.pop(0)

        # Only inject every WINDOW_SIZE tool calls
        if count % WINDOW_SIZE != 0:
            return None

        # Resolve project_key
        if not project_key:
            from config.memory_defaults import DEFAULT_PROJECT_KEY

            project_key = os.environ.get("VALOR_PROJECT_KEY", DEFAULT_PROJECT_KEY)

        # Extract keywords from full buffer (last BUFFER_SIZE entries)
        all_keywords: list[str] = []
        for entry in buffer[-BUFFER_SIZE:]:
            kw = extract_topic_keywords(entry["tool_name"], entry["tool_input"])
            all_keywords.extend(kw)

        if not all_keywords:
            return None

        # Deduplicate keywords
        unique_keywords = list(dict.fromkeys(all_keywords))[:15]

        # Bloom check — fast O(1) pre-filter
        from models.memory import Memory

        # NOTE: Accesses popoto internal metadata (_meta.fields). If upgrading
        # popoto, verify that BloomFilterField still registers in _meta.fields.
        bloom_field = Memory._meta.fields.get("bloom")
        if not bloom_field:
            return None

        bloom_hits = 0
        for keyword in unique_keywords:
            try:
                if bloom_field.might_exist(Memory, keyword):
                    bloom_hits += 1
            except Exception:
                continue

        # Deja vu: no bloom hits but significant keyword count
        # signals "novel territory" -- pay attention to what works here
        if bloom_hits == 0:
            if len(unique_keywords) >= NOVEL_TERRITORY_KEYWORD_THRESHOLD:
                return (
                    "<thought>This is new territory -- "
                    "I should pay attention to what works here.</thought>"
                )
            return None

        # Multi-query decomposition — cluster keywords and retrieve via BM25 + RRF
        import time

        from agent.memory_retrieval import retrieve_memories

        clusters = _cluster_keywords(unique_keywords)
        all_records = []
        seen_ids: set[str] = set()

        query_start = time.monotonic()
        for cluster in clusters:
            cluster_query = " ".join(cluster[:5])
            records = retrieve_memories(
                query_text=cluster_query,
                project_key=project_key,
                limit=MAX_THOUGHTS,
            )
            for record in records:
                rid = str(getattr(record, "memory_id", "") or "")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    all_records.append(record)

        elapsed_ms = (time.monotonic() - query_start) * 1000
        if elapsed_ms > 15:
            logger.warning(
                f"[memory_hook] Multi-query took {elapsed_ms:.1f}ms "
                f"(budget: 15ms, clusters: {len(clusters)})"
            )

        # No strong results -- return None (deja vu fallback removed:
        # vague "encountered something related" thoughts waste context tokens)
        if not all_records:
            return None

        # Re-rank by category weights (corrections/decisions surface higher)
        all_records = _apply_category_weights(all_records)

        # Format as <thought> blocks
        thoughts: list[str] = []
        session_thoughts = _injected_thoughts.setdefault(session_id, [])

        for record in all_records[:MAX_THOUGHTS]:
            content = getattr(record, "content", "")
            if content:
                thoughts.append(f"<thought>{content}</thought>")
                # Track for outcome detection
                key = getattr(record, "memory_id", "") or ""
                session_thoughts.append((str(key), content))
                # Mark as accessed
                try:
                    record.confirm_access()
                except Exception:
                    pass

        if not thoughts:
            return None

        logger.info(
            f"[memory_hook] Injected {len(thoughts)} thoughts "
            f"for session {session_id} (keywords: {unique_keywords[:5]})"
        )
        return "\n".join(thoughts)

    except Exception as e:
        logger.warning(f"[memory_hook] Injection failed (non-fatal): {e}")
        return None


def get_injected_thoughts(session_id: str) -> list[tuple[str, str]]:
    """Get the list of (memory_key, content) tuples injected in this session.

    Used by the extraction module for outcome detection.
    """
    return _injected_thoughts.get(session_id, [])


def clear_session(session_id: str) -> None:
    """Clean up session-scoped state. Call on session end."""
    _tool_buffers.pop(session_id, None)
    _tool_counts.pop(session_id, None)
    _injected_thoughts.pop(session_id, None)
