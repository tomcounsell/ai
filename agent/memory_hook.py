"""Subconscious memory thought injection for PostToolUse hook.

Checks ExistenceFilter for topic relevance, assembles context via
ContextAssembler, and returns <thought> blocks via additionalContext.

Rate-limited via sliding window: every WINDOW_SIZE tool calls, extracts
topic keywords from the current window plus previous windows in the buffer.

All operations are wrapped in try/except — memory system failures must
never crash or slow down the agent.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# Sliding window configuration (from config/memory_defaults.py)
WINDOW_SIZE = 3
BUFFER_SIZE = 9
MAX_THOUGHTS = 3

# Session-scoped state (in-memory, resets with process)
_tool_buffers: dict[str, list[dict[str, Any]]] = {}
_tool_counts: dict[str, int] = {}
_injected_thoughts: dict[str, list[tuple[str, str]]] = {}


def extract_topic_keywords(tool_name: str, tool_input: Any) -> list[str]:
    """Extract topic keywords from tool name and input.

    Pulls meaningful terms from file paths, grep patterns, command
    snippets, and other tool arguments. Filters out noise words.
    """
    keywords: list[str] = []

    # Add tool name parts
    if tool_name:
        parts = re.split(r"[_\-.]", tool_name.lower())
        keywords.extend(p for p in parts if len(p) > 2)

    if not isinstance(tool_input, dict):
        return keywords

    # Extract from common tool input fields
    for field in ("file_path", "path", "pattern", "command", "query", "content"):
        val = tool_input.get(field)
        if not val or not isinstance(val, str):
            continue

        if field in ("file_path", "path"):
            # Extract meaningful path segments
            segments = re.split(r"[/\\.]", val)
            keywords.extend(s for s in segments if len(s) > 2 and not s.startswith("_"))
        elif field == "pattern":
            # Grep patterns — extract words
            words = re.findall(r"[a-zA-Z]{3,}", val)
            keywords.extend(w.lower() for w in words)
        elif field == "command":
            # Command snippets — extract first few meaningful words
            words = re.findall(r"[a-zA-Z]{3,}", val[:200])
            keywords.extend(w.lower() for w in words[:5])

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for k in keywords:
        kl = k.lower()
        if kl not in seen and kl not in _NOISE_WORDS:
            seen.add(kl)
            unique.append(kl)
    return unique[:10]  # cap at 10 keywords


# Words too generic to be useful as topic keywords
_NOISE_WORDS = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "not",
    "def", "class", "import", "return", "true", "false", "none",
    "self", "src", "tmp", "var", "usr", "bin", "etc", "test",
    "file", "line", "read", "write", "bash", "python", "git",
})


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
            project_key = os.environ.get("VALOR_PROJECT_KEY", "dm")

        # Extract keywords from current window (last WINDOW_SIZE entries)
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

        bloom_field = Memory._meta.fields.get("bloom")
        if not bloom_field:
            return None

        has_relevant = False
        for keyword in unique_keywords:
            try:
                if bloom_field.might_exist(Memory, keyword):
                    has_relevant = True
                    break
            except Exception:
                continue

        if not has_relevant:
            return None

        # Full assembly — Redis-only, ~5-10ms
        from popoto import ContextAssembler

        assembler = ContextAssembler(
            model_class=Memory,
            score_weights={"relevance": 0.6, "confidence": 0.3},
            max_items=MAX_THOUGHTS,
            max_tokens=1000,
        )
        result = assembler.assemble(
            query_cues={"topic": " ".join(unique_keywords[:5])},
            agent_id=project_key,
        )

        if not result.records:
            return None

        # Format as <thought> blocks
        thoughts: list[str] = []
        session_thoughts = _injected_thoughts.setdefault(session_id, [])

        for record in result.records[:MAX_THOUGHTS]:
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
