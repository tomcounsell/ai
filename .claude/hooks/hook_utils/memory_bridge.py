"""Memory bridge for Claude Code hooks.

Wraps Memory model imports and BM25+RRF retrieval calls for use from
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

# ---------------------------------------------------------------------------
# Sliding window constants for memory recall timing
#
# WINDOW_SIZE: tool calls between recall queries. Lower = more frequent
#   recall checks (and more latency); higher = fewer checks.
# BUFFER_SIZE: max recent tool calls kept in the sidecar for keyword
#   extraction. Should be >= WINDOW_SIZE to ensure full coverage.
# MAX_THOUGHTS: max <thought> blocks injected per recall cycle.
# ---------------------------------------------------------------------------
WINDOW_SIZE = 3
BUFFER_SIZE = 9
MAX_THOUGHTS = 3

# ---------------------------------------------------------------------------
# Deja vu thresholds -- imported from shared config so both the hooks path
# (this module) and the SDK agent path (agent/memory_hook.py) use the same
# values. Fallback to hardcoded defaults if config import fails.
# ---------------------------------------------------------------------------
try:
    from config.memory_defaults import (
        DEJA_VU_BLOOM_HIT_THRESHOLD,
        NOVEL_TERRITORY_KEYWORD_THRESHOLD,
    )
except Exception:
    DEJA_VU_BLOOM_HIT_THRESHOLD = 3
    NOVEL_TERRITORY_KEYWORD_THRESHOLD = 7

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


def _get_project_key(cwd: str | None = None) -> str:
    """Resolve the project key from environment, cwd path match, or defaults.

    Priority: VALOR_PROJECT_KEY env var > cwd match against projects.json > default.
    """
    env_key = os.environ.get("VALOR_PROJECT_KEY")
    if env_key:
        return env_key

    # Try to match cwd against projects.json working_directory entries
    if cwd:
        try:
            projects_path = Path.home() / "Desktop" / "Valor" / "projects.json"
            if projects_path.exists():
                with open(projects_path) as f:
                    data = json.load(f)
                home = str(Path.home())
                for key, proj in data.get("projects", {}).items():
                    wd = proj.get("working_directory", "")
                    if wd:
                        wd = wd.replace("~", home)
                        if cwd.startswith(wd):
                            return key
        except Exception:
            pass

        # No projects.json match -- derive from directory basename
        return Path(cwd).name

    try:
        from config.memory_defaults import DEFAULT_PROJECT_KEY

        return DEFAULT_PROJECT_KEY
    except Exception:
        return "dm"


def recall(
    session_id: str,
    tool_name: str,
    tool_input: Any,
) -> str | None:
    """Query memory and return thought blocks for injection.

    Accumulates tool calls in a JSON sidecar file. Every WINDOW_SIZE
    calls, extracts keywords, checks bloom filter, and queries
    via BM25+RRF fusion. Returns additionalContext string or None.

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

        # Multi-query decomposition -- cluster keywords and retrieve via BM25+RRF
        import time

        from agent.memory_hook import _cluster_keywords
        from agent.memory_retrieval import retrieve_memories

        project_key = _get_project_key()

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
                f"[memory_bridge] Multi-query took {elapsed_ms:.1f}ms "
                f"(budget: 15ms, clusters: {len(clusters)})"
            )

        # Deja vu: bloom hits but no strong results
        if not all_records:
            _save_sidecar(session_id, state)
            if bloom_hits >= DEJA_VU_BLOOM_HIT_THRESHOLD:
                topic_hint = ", ".join(unique_keywords[:3])
                return (
                    "<thought>I have encountered something related to "
                    f"{topic_hint} before, but the details are unclear.</thought>"
                )
            return None

        # Re-rank by category weights (corrections/decisions surface higher)
        try:
            from agent.memory_hook import _apply_category_weights

            all_records = _apply_category_weights(all_records)
        except Exception:
            pass  # fail silent -- use unranked order

        # Format as thought blocks
        thoughts: list[str] = []
        injected = state.get("injected", [])

        for record in all_records[:MAX_THOUGHTS]:
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
            agent_id=project_key,
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
        for filename in (
            "memory_buffer.json",
            "memory_buffer.json.tmp",
            # NOTE: agent_session.json is intentionally NOT cleaned up here.
            # It must persist across turns so that UserPromptSubmit can reuse
            # the same AgentSession for the entire Claude Code session.
        ):
            filepath = sidecar_dir / filename
            if filepath.exists():
                filepath.unlink()
    except Exception:
        pass  # Cleanup is best-effort


def post_merge_extract(pr_number: str | int | None = None) -> None:
    """Run post-merge learning extraction for a merged PR.

    Wrapper around agent/memory_extraction.extract_post_merge_learning()
    for use from Claude Code hooks. Fetches PR metadata via gh CLI and
    calls the extraction pipeline.

    All exceptions are caught -- merge learning failures never block hooks.
    """
    try:
        import subprocess

        if not pr_number:
            return

        # Fetch PR title and body via gh CLI
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "title,body"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return

        pr_data = json.loads(result.stdout)
        pr_title = pr_data.get("title", "")
        pr_body = pr_data.get("body", "")

        if not pr_title:
            return

        # Get a brief diff summary (filenames changed)
        diff_result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number), "--name-only"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        diff_summary = diff_result.stdout[:2000] if diff_result.returncode == 0 else ""

        import asyncio

        from agent.memory_extraction import extract_post_merge_learning

        project_key = _get_project_key()
        asyncio.run(
            extract_post_merge_learning(
                pr_title=pr_title,
                pr_body=pr_body,
                diff_summary=diff_summary,
                project_key=project_key,
            )
        )

    except Exception as e:
        logger.warning(f"[memory_bridge] post_merge_extract failed (non-fatal): {e}")


def load_agent_session_sidecar(session_id: str) -> dict:
    """Load the agent session sidecar data for a session.

    Returns a dict that may contain 'agent_session_job_id' and
    'merge_detected' among other keys. Returns empty dict if
    the file is missing or corrupt.
    """
    sidecar_path = _get_sidecar_dir(session_id) / "agent_session.json"
    if not sidecar_path.exists():
        return {}
    try:
        with open(sidecar_path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_agent_session_sidecar(session_id: str, data: dict) -> None:
    """Persist agent session sidecar data atomically.

    Uses tmp + rename pattern to avoid partial writes.
    Stores agent_session_job_id and other cross-hook state.
    """
    sidecar_dir = _get_sidecar_dir(session_id)
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = sidecar_dir / "agent_session.json"
    tmp_path = sidecar_path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(data, f)
        tmp_path.rename(sidecar_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
