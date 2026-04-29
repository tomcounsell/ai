"""Memory bridge for Claude Code hooks.

Wraps Memory model imports and BM25+RRF retrieval calls for use from
hook scripts. Hooks are standalone scripts that run in fresh processes;
this module handles sys.path setup and import boilerplate.

Public API:
  - recall(session_id, tool_name, tool_input, cwd) -- PostToolUse path,
    accumulates a sliding window of tool calls and queries memory every
    WINDOW_SIZE calls.
  - prefetch(session_id, prompt, cwd) -- UserPromptSubmit path, queries
    memory directly against the user's prompt on the first turn so the
    agent has context before any tool runs.
  - ingest(content, cwd) -- save a user prompt as a Memory record.
  - extract(session_id, transcript_path, cwd) -- post-session Haiku
    extraction and outcome detection.
  - post_merge_extract(pr_number, cwd) -- post-merge learning extraction.

All functions are wrapped in try/except and fail silently -- memory
system failures must never crash or slow down hook execution.

State is persisted to JSON sidecar files in data/sessions/{session_id}/
since hooks cannot maintain in-memory state across invocations. The
sidecar `injected[]` list is the source of truth for "already shown"
memory IDs across both call sites within a session.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
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
        NOVEL_TERRITORY_KEYWORD_THRESHOLD,
    )
except Exception:
    NOVEL_TERRITORY_KEYWORD_THRESHOLD = 7

# ---------------------------------------------------------------------------
# Latency budget for the user-facing prefetch path. When prefetch() exceeds
# this wall-clock budget, a warning is logged so operators can spot silent
# regressions via log grep. The PostToolUse multi-cluster path uses a 15ms
# budget; prefetch is a single-call user-facing query that runs once per
# UserPromptSubmit, so a more generous budget is appropriate.
# ---------------------------------------------------------------------------
try:
    from config.memory_defaults import PREFETCH_LATENCY_WARN_MS
except Exception:
    PREFETCH_LATENCY_WARN_MS = 200

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

# Sentinel value used by Claude Code when input_data["session_id"] is absent.
# Treated as "unknown" -- the SDK-side de-dup loader skips sidecar reads when
# claude_uuid equals this value to avoid cross-session contamination at
# data/sessions/unknown/memory_buffer.json.
_UNKNOWN_CLAUDE_UUID = "unknown"

# Regex matching the worker-spawned PM/Teammate prompt boilerplate. The
# bridge wraps user messages with FROM:/SCOPE:/MESSAGE: framing before
# enqueuing them to the SDK subprocess. For BM25 ranking we only want the
# substring after MESSAGE:. Multiline + DOTALL so SCOPE: can span lines.
_PM_BOILERPLATE_RE = re.compile(
    r"^FROM:.*?\nSCOPE:.*?\nMESSAGE:\s*",
    re.DOTALL,
)


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
        return "default"


def _strip_pm_boilerplate(prompt: str) -> str:
    """Strip worker-spawned PM/Teammate FROM:/SCOPE:/MESSAGE: boilerplate.

    Worker subprocesses receive prompts wrapped with a routing header.
    Only the MESSAGE: payload carries the user's actual intent, so for
    BM25 ranking we drop the boilerplate prefix. If the pattern does not
    match, the prompt is returned unchanged.
    """
    if not isinstance(prompt, str) or not prompt:
        return prompt
    match = _PM_BOILERPLATE_RE.match(prompt)
    if match:
        return prompt[match.end() :].strip()
    return prompt


def _format_thought_blocks(
    records: list[Any],
    exclude_ids: set[str] | None = None,
    max_results: int = MAX_THOUGHTS,
) -> tuple[list[str], list[dict]]:
    """Format Memory records as <thought>{content}</thought> blocks.

    Skips records whose memory_id is in exclude_ids. Returns a tuple of
    (thought_strings, injected_entries) where injected_entries is the
    list of {"memory_id": str, "content": str} dicts to append to the
    sidecar's injected[] list.

    Calls record.confirm_access() best-effort to mark memories as
    accessed for outcome tracking.
    """
    exclude_ids = exclude_ids or set()
    thoughts: list[str] = []
    new_entries: list[dict] = []

    for record in records:
        if len(thoughts) >= max_results:
            break
        memory_id = str(getattr(record, "memory_id", "") or "")
        if memory_id and memory_id in exclude_ids:
            continue
        content = getattr(record, "content", "")
        if not content:
            continue
        thoughts.append(f"<thought>{content}</thought>")
        new_entries.append({"memory_id": memory_id, "content": content})
        try:
            record.confirm_access()
        except Exception:
            pass

    return thoughts, new_entries


def _recall_with_query(
    query: str,
    project_key: str,
    exclude_ids: set[str] | None = None,
    *,
    bloom_check: bool = True,
    bloom_check_emit_dejavu: bool = True,
    max_results: int = MAX_THOUGHTS,
) -> list[Any] | str:
    """Pure retrieval: BM25 + RRF + category re-ranking against an explicit query.

    No sidecar I/O, no <thought> formatting. Both recall() (tool-buffer
    caller) and prefetch() (prompt caller) wrap this helper.

    Args:
        query: Free-text query string to search against. The Memory
            model's BM25Field tokenizes on whitespace and punctuation,
            so multi-word queries are valid.
        project_key: Project partition key for retrieval scoping.
        exclude_ids: Memory IDs to skip in the result set (e.g. already
            injected this session).
        bloom_check: When True, runs the bloom pre-filter before BM25.
            When False, skips the gate entirely.
        bloom_check_emit_dejavu: When True (the recall() default), zero
            bloom hits with >= NOVEL_TERRITORY_KEYWORD_THRESHOLD unique
            tokens returns the deja vu thought string. When False (the
            prefetch() default), returns [] instead -- novel-territory
            thoughts on the user-visible first turn are pure noise per
            issue #627.
        max_results: Cap on returned Memory records (post-RRF, post-
            re-rank, post-exclude).

    Returns:
        list[Memory] of records ranked by RRF + category weights, with
        exclude_ids filtered out. May return the deja vu string when
        bloom_check_emit_dejavu is True and conditions are met. Returns
        [] when no strong results.
    """
    exclude_ids = exclude_ids or set()
    if not query or not isinstance(query, str):
        return []

    try:
        from models.memory import Memory
    except Exception as e:
        logger.warning(f"[memory_bridge] _recall_with_query: Memory import failed: {e}")
        return []

    # Bloom pre-check (optional). When skipped, BM25 runs unconditionally.
    if bloom_check:
        bloom_field = Memory._meta.fields.get("bloom")
        if not bloom_field:
            return []

        # Coarse tokenization -- whitespace split, drop noise words.
        try:
            from utils.keyword_extraction import _NOISE_WORDS  # noqa: PLC0415
        except Exception:
            _NOISE_WORDS: set = set()  # noqa: N806 -- mirror import name

        tokens = [t.strip(".,;:!?\"'()[]{}").lower() for t in query.split()]
        unique_tokens = list(dict.fromkeys(t for t in tokens if t and t not in _NOISE_WORDS))[:15]

        bloom_hits = 0
        for token in unique_tokens:
            try:
                if bloom_field.might_exist(Memory, token):
                    bloom_hits += 1
            except Exception:
                continue

        if bloom_hits == 0:
            if bloom_check_emit_dejavu and len(unique_tokens) >= NOVEL_TERRITORY_KEYWORD_THRESHOLD:
                return (
                    "<thought>This is new territory -- "
                    "I should pay attention to what works here.</thought>"
                )
            return []

    # BM25 + RRF retrieval against the prompt as a single query.
    try:
        from agent.memory_retrieval import retrieve_memories

        records = retrieve_memories(
            query_text=query,
            project_key=project_key,
            limit=max_results * 3,  # over-fetch to allow exclude filtering
        )
    except Exception as e:
        logger.warning(f"[memory_bridge] _recall_with_query: retrieve_memories failed: {e}")
        return []

    if not records:
        return []

    # Filter excluded IDs
    if exclude_ids:
        records = [r for r in records if str(getattr(r, "memory_id", "") or "") not in exclude_ids]

    if not records:
        return []

    # Category re-ranking (corrections / decisions surface higher).
    try:
        from utils.keyword_extraction import _apply_category_weights

        records = _apply_category_weights(records)
    except Exception:
        pass  # fail silent -- use unranked order

    return records[:max_results]


def recall(
    session_id: str,
    tool_name: str,
    tool_input: Any,
    cwd: str | None = None,
) -> str | None:
    """Query memory and return thought blocks for injection.

    Accumulates tool calls in a JSON sidecar file. Every WINDOW_SIZE
    calls, extracts keywords, checks bloom filter, and queries
    via BM25+RRF fusion. Returns additionalContext string or None.

    Multi-cluster: tool-call keywords are decomposed via
    _cluster_keywords() and each cluster runs its own
    _recall_with_query() pass. Records already in the sidecar's
    injected[] list (e.g. from prefetch()) are filtered out so the
    same memory is never surfaced twice in a session.

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
        from utils.keyword_extraction import extract_topic_keywords

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

        # Bloom pre-check (multi-keyword variant -- tested per-keyword for
        # historical parity, not joined into a single query string).
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

        # Deja vu: no bloom hits but significant keyword count.
        # Preserved here in recall() (tool-buffer path) for backward
        # compatibility -- prefetch() suppresses this fallback.
        if bloom_hits == 0:
            _save_sidecar(session_id, state)
            if len(unique_keywords) >= NOVEL_TERRITORY_KEYWORD_THRESHOLD:
                return (
                    "<thought>This is new territory -- "
                    "I should pay attention to what works here.</thought>"
                )
            return None

        # Multi-query decomposition -- cluster keywords and retrieve via
        # _recall_with_query() per cluster (bloom_check=False inside each
        # call since we already passed the multi-keyword bloom gate above).
        from utils.keyword_extraction import _cluster_keywords

        project_key = _get_project_key(cwd)
        existing_injected_ids = {
            str(item.get("memory_id", "") or "")
            for item in state.get("injected", [])
            if isinstance(item, dict)
        }
        existing_injected_ids.discard("")

        clusters = _cluster_keywords(unique_keywords)
        all_records: list[Any] = []
        seen_ids: set[str] = set(existing_injected_ids)

        query_start = time.monotonic()
        for cluster in clusters:
            cluster_query = " ".join(cluster[:5])
            cluster_records = _recall_with_query(
                query=cluster_query,
                project_key=project_key,
                exclude_ids=seen_ids,
                bloom_check=False,  # already gated above
                bloom_check_emit_dejavu=False,
                max_results=MAX_THOUGHTS,
            )
            if isinstance(cluster_records, str):
                # Defensive: _recall_with_query never returns the deja vu
                # string when bloom_check=False, but guard anyway.
                continue
            for record in cluster_records:
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

        # No strong results -- return None (deja vu fallback removed:
        # vague "encountered something related" thoughts waste context tokens)
        if not all_records:
            _save_sidecar(session_id, state)
            return None

        # Format as thought blocks via shared helper. exclude_ids was
        # already applied inside _recall_with_query, but pass again as
        # belt-and-suspenders against accidental duplicates.
        thoughts, new_entries = _format_thought_blocks(
            all_records,
            exclude_ids=existing_injected_ids,
            max_results=MAX_THOUGHTS,
        )

        if new_entries:
            injected = state.get("injected", [])
            injected.extend(new_entries)
            state["injected"] = injected

        _save_sidecar(session_id, state)

        if not thoughts:
            return None

        return "\n".join(thoughts)

    except Exception as e:
        logger.warning(f"[memory_bridge] recall failed (non-fatal): {e}")
        return None


def prefetch(
    session_id: str,
    prompt: str,
    cwd: str | None = None,
) -> str | None:
    """Prefetch memory thought blocks for the user's prompt on UserPromptSubmit.

    Runs once per UserPromptSubmit hook invocation. Unlike recall()
    (which buffers tool calls and queries every WINDOW_SIZE), prefetch
    runs immediately so the agent gets memory context before any tool
    fires. The prompt itself is the BM25 query; no clustering.

    Gates short / trivial prompts via MIN_PROMPT_LENGTH and
    TRIVIAL_PATTERNS. Strips PM-style FROM:/SCOPE:/MESSAGE: boilerplate
    so worker-spawned subprocesses query against the actual user
    payload, not the routing template. Suppresses the deja vu fallback
    (bloom_check_emit_dejavu=False) -- per issue #627, novel-territory
    thoughts on the user-visible first turn are noise.

    Sidecar discipline: load-modify-saves the same
    data/sessions/{session_id}/memory_buffer.json file that recall()
    owns. Only mutates injected[]; preserves count and buffer verbatim
    so prefetch and recall can interleave without clobbering each
    other's state.

    Args:
        session_id: Claude Code session UUID (input_data["session_id"]).
        prompt: The raw user prompt as received by UserPromptSubmit.
        cwd: Working directory for project_key resolution.

    Returns:
        A newline-joined string of <thought> blocks, or None when the
        prompt is gated, no records match, or any error occurs. Never
        propagates exceptions -- failure must not block prompt submission.
    """
    try:
        if not prompt or not isinstance(prompt, str):
            return None

        # Strip PM boilerplate first so length / triviality checks apply
        # to the actual user payload, not the routing template.
        stripped = _strip_pm_boilerplate(prompt).strip()

        if len(stripped) < MIN_PROMPT_LENGTH:
            return None

        normalized = stripped.lower().rstrip("!?.,")
        if normalized in TRIVIAL_PATTERNS:
            return None

        project_key = _get_project_key(cwd)

        # Load sidecar to get current injected[] for de-dup.
        state = _load_sidecar(session_id)
        existing_injected_ids = {
            str(item.get("memory_id", "") or "")
            for item in state.get("injected", [])
            if isinstance(item, dict)
        }
        existing_injected_ids.discard("")

        start = time.monotonic()
        records = _recall_with_query(
            query=stripped,
            project_key=project_key,
            exclude_ids=existing_injected_ids,
            bloom_check=True,
            bloom_check_emit_dejavu=False,
            max_results=MAX_THOUGHTS,
        )
        elapsed_ms = (time.monotonic() - start) * 1000

        if elapsed_ms > PREFETCH_LATENCY_WARN_MS:
            logger.warning(
                f"[memory_bridge] prefetch took {elapsed_ms:.1f}ms "
                f"(budget: {PREFETCH_LATENCY_WARN_MS}ms)"
            )

        # _recall_with_query may return the deja vu string when
        # bloom_check_emit_dejavu=True; we passed False so this branch
        # should never fire, but guard against future changes.
        if isinstance(records, str):
            return None

        if not records:
            return None

        thoughts, new_entries = _format_thought_blocks(
            records,
            exclude_ids=existing_injected_ids,
            max_results=MAX_THOUGHTS,
        )

        # Load-modify-save: mutate only injected[], preserve count/buffer.
        if new_entries:
            try:
                # Re-load to minimize the window where a concurrent recall()
                # write could be lost. Atomic rename guarantees we read a
                # complete file, even if it's slightly stale.
                fresh_state = _load_sidecar(session_id)
                fresh_injected = fresh_state.get("injected", [])
                if not isinstance(fresh_injected, list):
                    fresh_injected = []
                fresh_injected.extend(new_entries)
                fresh_state["injected"] = fresh_injected
                # Preserve count and buffer owned by recall().
                fresh_state["count"] = fresh_state.get("count", 0)
                fresh_state["buffer"] = fresh_state.get("buffer", [])
                _save_sidecar(session_id, fresh_state)
            except Exception as e:
                # Best-effort write -- losing de-dup state is degraded,
                # not broken. Still return the thoughts.
                logger.warning(f"[memory_bridge] prefetch sidecar save failed: {e}")

        if not thoughts:
            return None

        return "\n".join(thoughts)

    except Exception as e:
        logger.warning(f"[memory_bridge] prefetch failed (non-fatal): {e}")
        return None


def ingest(content: str, cwd: str | None = None) -> bool:
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

        project_key = _get_project_key(cwd)

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


def extract(session_id: str, transcript_path: str | None, cwd: str | None = None) -> None:
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
        project_key = _get_project_key(cwd)
        asyncio.run(extract_observations_async(session_id, truncated_text, project_key=project_key))

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


def post_merge_extract(pr_number: str | int | None = None, cwd: str | None = None) -> None:
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

        project_key = _get_project_key(cwd)
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

    Returns a dict that may contain 'agent_session_id' and
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
    Stores agent_session_id and other cross-hook state.
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
