"""Subconscious memory thought injection for PostToolUse hook.

Checks ExistenceFilter for topic relevance, retrieves memories via
BM25 + RRF fusion, and returns <thought> blocks via additionalContext.

Rate-limited via sliding window: every WINDOW_SIZE tool calls, extracts
topic keywords from the current window plus previous windows in the buffer.

All operations are wrapped in try/except — memory system failures must
never crash or slow down the agent.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from config.memory_defaults import (
    BLOOM_MIN_HITS,
    NOVEL_TERRITORY_KEYWORD_THRESHOLD,
    RRF_MIN_SCORE,
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

# Sentinel value used by the hooks layer when input_data["session_id"]
# is absent. We refuse to read data/sessions/unknown/memory_buffer.json
# because that path would be shared across every malformed-payload
# session, causing cross-session contamination of the de-dup set.
_UNKNOWN_CLAUDE_UUID = "unknown"

# Project root resolution mirrors the hooks-side _get_sidecar_dir(). The
# memory_bridge sidecar lives at <project_root>/data/sessions/{claude_uuid}/.
_PROJECT_ROOT_FOR_SIDECAR = Path(__file__).resolve().parent.parent


def _load_hooks_sidecar_injected_ids(claude_uuid: str | None) -> set[str]:
    """Load the hooks-side sidecar injected[] memory_id values for de-dup.

    The hooks-side sidecar is keyed by Claude Code's session UUID
    (input_data["session_id"]), which is distinct from the SDK-side
    AGENT_SESSION_ID env var. The watchdog hook passes its
    claude_uuid through so this loader can find the right file.

    Guarded against:
      - missing claude_uuid (None / empty / "unknown" sentinel)
      - missing sidecar file
      - corrupt JSON / unexpected shape
      - any I/O exception

    Fail-silent: returns an empty set on any failure so the SDK-side
    de-dup degrades to "no exclusion" (worst case: one duplicate
    thought per cycle, harmless).
    """
    if not claude_uuid or claude_uuid == _UNKNOWN_CLAUDE_UUID:
        return set()
    try:
        sidecar_path = (
            _PROJECT_ROOT_FOR_SIDECAR / "data" / "sessions" / claude_uuid / "memory_buffer.json"
        )
        if not sidecar_path.exists():
            return set()
        with open(sidecar_path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return set()
        injected = data.get("injected", [])
        if not isinstance(injected, list):
            return set()
        ids: set[str] = set()
        for item in injected:
            if isinstance(item, dict):
                mid = item.get("memory_id")
                if mid:
                    ids.add(str(mid))
        return ids
    except Exception:
        return set()


def check_and_inject(
    session_id: str,
    tool_name: str,
    tool_input: Any,
    project_key: str | None = None,
    claude_uuid: str | None = None,
) -> str | None:
    """Check bloom filter and inject thoughts if relevant memories exist.

    Called from watchdog_hook() on every tool call. Uses sliding window
    rate limiting to avoid flooding the context.

    De-dup contract: when claude_uuid is provided (and not the
    "unknown" sentinel), reads the hooks-side sidecar at
    data/sessions/{claude_uuid}/memory_buffer.json and excludes any
    memory_ids already in its injected[] list. This prevents the SDK
    side from re-surfacing memories that the UserPromptSubmit prefetch
    already showed. When claude_uuid is None / empty / "unknown", the
    sidecar read is skipped (direct-CLI paths and malformed payloads
    fall back to today's behavior with no de-dup coordination).

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
        # signals "novel territory" -- pay attention to what works here.
        # PRESERVED unchanged: the new BLOOM_MIN_HITS gate only catches
        # the 1 <= bloom_hits < BLOOM_MIN_HITS middle band.
        if bloom_hits == 0:
            if len(unique_keywords) >= NOVEL_TERRITORY_KEYWORD_THRESHOLD:
                return (
                    "<thought>This is new territory -- "
                    "I should pay attention to what works here.</thought>"
                )
            return None

        # Tightened bloom gate: a single token hit is high-noise -- require
        # BLOOM_MIN_HITS distinct token hits before BM25 + RRF runs. No
        # deja-vu emission for this band -- it's "weak signal," not
        # "novel territory."
        if bloom_hits < BLOOM_MIN_HITS:
            return None

        # Multi-query decomposition — cluster keywords and retrieve via BM25 + RRF.
        # Pass min_rrf_score=RRF_MIN_SCORE so the recall path defaults the
        # post-fusion relevance gate ON (CLI defaults it OFF for back-compat).
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
                min_rrf_score=RRF_MIN_SCORE,
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

        # Build the de-dup exclude set: union of process-local
        # _injected_thoughts (this SDK process's prior cycles) and the
        # hooks-side sidecar injected[] (UserPromptSubmit prefetch + the
        # parallel hooks-side recall path).
        process_local_exclude = {str(k) for (k, _v) in _injected_thoughts.get(session_id, [])}
        process_local_exclude.discard("")
        sidecar_exclude = _load_hooks_sidecar_injected_ids(claude_uuid)
        exclude_ids = process_local_exclude | sidecar_exclude

        # Format as <thought> blocks (skip records already shown).
        thoughts: list[str] = []
        session_thoughts = _injected_thoughts.setdefault(session_id, [])

        for record in all_records:
            if len(thoughts) >= MAX_THOUGHTS:
                break
            memory_id = str(getattr(record, "memory_id", "") or "")
            if memory_id and memory_id in exclude_ids:
                continue
            content = getattr(record, "content", "")
            if content:
                thoughts.append(f"<thought>{content}</thought>")
                # Track for outcome detection
                session_thoughts.append((memory_id, content))
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
