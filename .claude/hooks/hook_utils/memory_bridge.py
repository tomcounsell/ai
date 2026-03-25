"""Memory bridge for Claude Code hooks.

Wraps Memory model imports and ContextAssembler calls for use from
hook scripts. Hooks are standalone scripts that run in fresh processes;
this module handles sys.path setup and import boilerplate.

All functions are wrapped in try/except and fail silently -- memory
system failures must never crash or slow down hook execution.

State is persisted to JSON sidecar files in data/sessions/{session_id}/
since hooks cannot maintain in-memory state across invocations.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Sliding window constants (match agent/memory_hook.py)
WINDOW_SIZE = 3
BUFFER_SIZE = 9
MAX_THOUGHTS = 3

# Deja vu thresholds
DEJA_VU_BLOOM_HIT_THRESHOLD = 3  # minimum bloom hits for vague recognition
NOVEL_TERRITORY_KEYWORD_THRESHOLD = 7  # minimum keywords with zero bloom hits

# Trivial prompt patterns to skip during ingestion
TRIVIAL_PATTERNS = frozenset(
    {
        "yes",
        "no",
        "ok",
        "okay",
        "continue",
        "go",
        "go ahead",
        "thanks",
        "thank you",
        "done",
        "next",
        "sure",
        "right",
        "correct",
        "y",
        "n",
        "k",
        "yep",
        "nope",
        "got it",
        "sounds good",
        "lgtm",
    }
)

MIN_PROMPT_LENGTH = 50


def _get_sidecar_dir(session_id: str) -> Path:
    """Return the sidecar directory for a session."""
    return _PROJECT_ROOT / "data" / "sessions" / session_id


def _load_sidecar(session_id: str) -> dict:
    """Load the memory buffer sidecar file for a session.

    Returns a dict with keys: count, buffer, injected.
    If the file is missing or corrupt, returns a fresh default.
    """
    sidecar_path = _get_sidecar_dir(session_id) / "memory_buffer.json"
    if not sidecar_path.exists():
        return {"count": 0, "buffer": [], "injected": []}
    try:
        with open(sidecar_path) as f:
            data = json.load(f)
        # Validate structure
        if not isinstance(data, dict):
            return {"count": 0, "buffer": [], "injected": []}
        return {
            "count": data.get("count", 0),
            "buffer": data.get("buffer", []),
            "injected": data.get("injected", []),
        }
    except (json.JSONDecodeError, OSError):
        # Corrupt file -- reset to empty state
        return {"count": 0, "buffer": [], "injected": []}


def _save_sidecar(session_id: str, data: dict) -> None:
    """Persist the memory buffer sidecar file atomically.

    Uses tmp + rename pattern to avoid partial writes.
    """
    sidecar_dir = _get_sidecar_dir(session_id)
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = sidecar_dir / "memory_buffer.json"
    tmp_path = sidecar_path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(data, f)
        tmp_path.rename(sidecar_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def _get_project_key() -> str:
    """Resolve the project key from environment or defaults."""
    try:
        from config.memory_defaults import DEFAULT_PROJECT_KEY

        return os.environ.get("VALOR_PROJECT_KEY", DEFAULT_PROJECT_KEY)
    except Exception:
        return os.environ.get("VALOR_PROJECT_KEY", "dm")


def recall(
    session_id: str,
    tool_name: str,
    tool_input: Any,
) -> str | None:
    """Query memory and return thought blocks for injection.

    Accumulates tool calls in a JSON sidecar file. Every WINDOW_SIZE
    calls, extracts keywords, checks bloom filter, and queries
    ContextAssembler. Returns additionalContext string or None.

    This is the hook-side equivalent of agent/memory_hook.check_and_inject().
    All exceptions are caught -- returns None on any failure.
    """
    try:
        # Load sidecar state
        state = _load_sidecar(session_id)

        # Increment counter and append to buffer
        state["count"] = state.get("count", 0) + 1
        count = state["count"]

        buffer = state.get("buffer", [])
        buffer.append({"tool_name": tool_name, "tool_input": tool_input})
        if len(buffer) > BUFFER_SIZE:
            buffer = buffer[-BUFFER_SIZE:]
        state["buffer"] = buffer

        # Only query every WINDOW_SIZE calls
        if count % WINDOW_SIZE != 0:
            _save_sidecar(session_id, state)
            return None

        # Extract keywords from buffer (lazy import to avoid overhead on non-query calls)
        from agent.memory_hook import extract_topic_keywords

        all_keywords: list[str] = []
        for entry in buffer[-BUFFER_SIZE:]:
            kw = extract_topic_keywords(
                entry.get("tool_name", ""),
                entry.get("tool_input", {}),
            )
            all_keywords.extend(kw)

        if not all_keywords:
            _save_sidecar(session_id, state)
            return None

        # Deduplicate keywords
        unique_keywords = list(dict.fromkeys(all_keywords))[:15]

        # Bloom pre-check
        from models.memory import Memory

        bloom_field = Memory._meta.fields.get("bloom")
        if not bloom_field:
            _save_sidecar(session_id, state)
            return None

        bloom_hits = 0
        for keyword in unique_keywords:
            try:
                if bloom_field.might_exist(Memory, keyword):
                    bloom_hits += 1
            except Exception:
                continue

        # Deja vu: no bloom hits but significant keyword count
        if bloom_hits == 0:
            _save_sidecar(session_id, state)
            if len(unique_keywords) >= NOVEL_TERRITORY_KEYWORD_THRESHOLD:
                return (
                    "<thought>This is new territory -- "
                    "I should pay attention to what works here.</thought>"
                )
            return None

        # Full ContextAssembler query
        project_key = _get_project_key()

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

        # Deja vu: bloom hits but no strong results
        if not result.records:
            _save_sidecar(session_id, state)
            if bloom_hits >= DEJA_VU_BLOOM_HIT_THRESHOLD:
                topic_hint = ", ".join(unique_keywords[:3])
                return (
                    "<thought>I have encountered something related to "
                    f"{topic_hint} before, but the details are unclear.</thought>"
                )
            return None

        # Format as thought blocks
        thoughts: list[str] = []
        injected = state.get("injected", [])

        for record in result.records[:MAX_THOUGHTS]:
            content = getattr(record, "content", "")
            if content:
                thoughts.append(f"<thought>{content}</thought>")
                memory_id = str(getattr(record, "memory_id", "") or "")
                injected.append({"memory_id": memory_id, "content": content})
                # Mark as accessed
                try:
                    record.confirm_access()
                except Exception:
                    pass

        state["injected"] = injected
        _save_sidecar(session_id, state)

        if not thoughts:
            return None

        return "\n".join(thoughts)

    except Exception as e:
        logger.warning(f"[memory_bridge] recall failed (non-fatal): {e}")
        return None


def ingest(content: str) -> bool:
    """Save a user prompt as a Memory record.

    Applies quality filter (length, trivial pattern detection) and
    bloom dedup before saving. Returns True if saved, False otherwise.

    All exceptions are caught -- returns False on any failure.
    """
    try:
        if not content or not isinstance(content, str):
            return False

        # Quality filter: minimum length
        stripped = content.strip()
        if len(stripped) < MIN_PROMPT_LENGTH:
            return False

        # Quality filter: trivial patterns
        normalized = stripped.lower().rstrip("!?.,")
        if normalized in TRIVIAL_PATTERNS:
            return False

        # Bloom dedup check
        from models.memory import Memory

        bloom_field = Memory._meta.fields.get("bloom")
        if bloom_field:
            try:
                if bloom_field.might_exist(Memory, stripped):
                    # Content likely already exists
                    return False
            except Exception:
                pass  # Skip dedup on bloom error, allow save

        # Save as memory
        from models.memory import SOURCE_HUMAN

        project_key = _get_project_key()

        m = Memory.safe_save(
            agent_id="claude-code-user",
            project_key=project_key,
            content=stripped[:500],
            importance=6.0,
            source=SOURCE_HUMAN,
        )

        return m is not None

    except Exception as e:
        logger.warning(f"[memory_bridge] ingest failed (non-fatal): {e}")
        return False


def extract(session_id: str, transcript_path: str | None) -> None:
    """Run post-session extraction and outcome detection.

    Reads the transcript, runs Haiku extraction for novel observations,
    and detects outcomes for previously injected thoughts.

    All exceptions are caught -- session stop completes normally on failure.
    """
    try:
        if not transcript_path:
            return None

        transcript_file = Path(transcript_path)
        if not transcript_file.exists():
            return None

        # Read transcript text
        try:
            raw_text = transcript_file.read_text(errors="replace")
        except Exception:
            return None

        if not raw_text or len(raw_text.strip()) < 50:
            return None

        # Truncate for extraction (Haiku has token limits)
        truncated_text = raw_text[:8000]

        import asyncio

        from agent.memory_extraction import (
            detect_outcomes_async,
            extract_observations_async,
        )

        # Run Haiku extraction
        asyncio.run(extract_observations_async(session_id, truncated_text))

        # Run outcome detection using injected thoughts from sidecar
        state = _load_sidecar(session_id)
        injected = state.get("injected", [])
        if injected:
            # Convert sidecar format to (memory_id, content) tuples
            thought_tuples = [
                (item.get("memory_id", ""), item.get("content", ""))
                for item in injected
                if isinstance(item, dict)
            ]
            if thought_tuples:
                asyncio.run(detect_outcomes_async(thought_tuples, raw_text))

    except Exception as e:
        logger.warning(f"[memory_bridge] extract failed (non-fatal): {e}")

    finally:
        # Cleanup sidecar files
        cleanup_sidecar(session_id)


def cleanup_sidecar(session_id: str) -> None:
    """Remove session sidecar files.

    Removes memory_buffer.json from the session directory.
    Does not remove the directory itself (other hooks may use it).
    """
    try:
        sidecar_dir = _get_sidecar_dir(session_id)
        for filename in ("memory_buffer.json", "memory_buffer.json.tmp"):
            filepath = sidecar_dir / filename
            if filepath.exists():
                filepath.unlink()
    except Exception:
        pass  # Cleanup is best-effort
