"""Post-session memory extraction and outcome detection.

Extracts novel observations from agent response text via Haiku,
saves them as Memory records with category-based importance levels.

Detects outcomes by comparing injected thoughts against response
content using bigram overlap, feeds results into ObservationProtocol.

All operations are async, wrapped in try/except — failures must never
crash the agent or block session completion.
"""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# Extraction prompt for Haiku — structured JSON output
EXTRACTION_PROMPT = (
    "Extract novel observations from this agent session response.\n"
    "Return a JSON array of objects, each with:\n"
    '  "category": one of "correction", "decision", "pattern", "surprise"\n'
    '  "observation": the observation text (one sentence, specific)\n'
    '  "file_paths": list of file paths referenced (empty list if none)\n'
    '  "tags": list of domain tags (1-3 short keywords)\n'
    "\n"
    "Only include genuinely novel, specific observations.\n"
    "If none, return: []\n"
    "\n"
    "Example:\n"
    '[{"category": "decision", "observation": "chose blue-green deployment over rolling updates",'
    ' "file_paths": ["deploy/config.yaml"], "tags": ["deployment", "infrastructure"]}]'
)

# Importance levels for categorized extraction
CATEGORY_IMPORTANCE = {
    "correction": 4.0,
    "decision": 4.0,
    "pattern": 1.0,
    "surprise": 1.0,
}
DEFAULT_CATEGORY_IMPORTANCE = 1.0  # fallback for uncategorized

# Post-merge extraction prompt
POST_MERGE_EXTRACTION_PROMPT = (
    "You are reviewing a merged pull request. Extract the single most"
    " important project-level takeaway — knowledge that would help a"
    " developer working on this codebase in the future.\n"
    "\n"
    "Focus on architectural decisions, design patterns chosen, or"
    " conventions established. Skip implementation details.\n"
    "\n"
    "Return a single sentence. If there is no meaningful project-level"
    " takeaway, return NONE.\n"
    "\n"
    "PR Title: {title}\n"
    "PR Description: {body}\n"
    "Diff Summary: {diff_summary}"
)


async def extract_observations_async(
    session_id: str,
    response_text: str,
    project_key: str | None = None,
) -> list[dict]:
    """Extract novel observations from agent response via Haiku.

    Calls Haiku to identify decisions, surprises, corrections, and patterns.
    Saves each as a Memory record with category-based importance (4.0 for
    corrections/decisions, 1.0 for patterns/surprises).

    Returns list of dicts with keys: content, memory_id.
    """
    if not response_text or len(response_text.strip()) < 50:
        return []

    try:
        import anthropic

        from config.models import MODEL_FAST
        from utils.api_keys import get_anthropic_api_key

        api_key = get_anthropic_api_key()
        if not api_key:
            logger.warning("[memory_extraction] No Anthropic API key, skipping extraction")
            return []

        client = anthropic.Anthropic(api_key=api_key)

        # Truncate response to avoid token limits
        truncated = response_text[:8000]

        message = client.messages.create(
            model=MODEL_FAST,
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": f"{EXTRACTION_PROMPT}\n\n---\n\n{truncated}",
                }
            ],
        )

        raw_text = message.content[0].text.strip()

        if raw_text.upper() == "NONE" or not raw_text:
            logger.debug("[memory_extraction] No novel observations found")
            return []

        # Parse observations with category-aware importance
        parsed = _parse_categorized_observations(raw_text)

        if not parsed:
            return []

        # Save each observation as Memory
        from models.memory import SOURCE_AGENT, Memory

        if not project_key:
            from config.memory_defaults import DEFAULT_PROJECT_KEY

            project_key = os.environ.get("VALOR_PROJECT_KEY", DEFAULT_PROJECT_KEY)

        saved = []
        for obs_content, importance, metadata in parsed[:10]:  # cap at 10 observations
            m = Memory.safe_save(
                agent_id=f"extraction-{session_id}",
                project_key=project_key,
                content=obs_content[:500],
                importance=importance,
                source=SOURCE_AGENT,
                metadata=metadata,
            )
            if m:
                saved.append(
                    {
                        "content": obs_content[:500],
                        "memory_id": getattr(m, "memory_id", ""),
                    }
                )

        logger.info(
            f"[memory_extraction] Extracted {len(saved)} observations from session {session_id}"
        )
        return saved

    except Exception as e:
        logger.warning(f"[memory_extraction] Extraction failed (non-fatal): {e}")
        return []


def _parse_categorized_observations(raw_text: str) -> list[tuple[str, float, dict]]:
    """Parse Haiku output into (content, importance, metadata) tuples.

    Tries JSON parsing first (structured output). Falls back to line-based
    CATEGORY: text format. Returns empty metadata dict for line-based results.

    Returns list of (content_string, importance_float, metadata_dict) tuples.
    """
    # Try JSON first
    try:
        data = json.loads(raw_text)
        # Handle bare dict (single observation) — wrap in list
        if isinstance(data, dict):
            data = [data]
        if isinstance(data, list):
            results: list[tuple[str, float, dict]] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                category = item.get("category", "").lower()
                observation = item.get("observation", "")
                if not observation or len(observation) < 10:
                    continue
                importance = CATEGORY_IMPORTANCE.get(category, DEFAULT_CATEGORY_IMPORTANCE)
                metadata = {
                    "category": category,
                    "file_paths": item.get("file_paths", []),
                    "tags": item.get("tags", []),
                }
                results.append((observation, importance, metadata))
            if results:
                return results
    except (json.JSONDecodeError, TypeError):
        pass  # Fall through to line-based parser

    # Fallback: line-based parser (returns empty metadata)
    lines = [
        line.strip() for line in raw_text.split("\n") if line.strip() and len(line.strip()) > 10
    ]
    if not lines:
        return []

    categorized: list[tuple[str, float, dict]] = []
    uncategorized: list[str] = []

    for line in lines:
        matched = False
        for category in CATEGORY_IMPORTANCE:
            prefix = f"{category}:"
            if line.lower().startswith(prefix):
                content = line[len(prefix) :].strip()
                if content and len(content) > 10:
                    categorized.append((content, CATEGORY_IMPORTANCE[category], {}))
                matched = True
                break
        if not matched:
            uncategorized.append(line)

    if categorized:
        return categorized

    return [(line, DEFAULT_CATEGORY_IMPORTANCE, {}) for line in uncategorized]


async def extract_post_merge_learning(
    pr_title: str,
    pr_body: str,
    diff_summary: str,
    project_key: str | None = None,
) -> dict | None:
    """Extract and save a project-level takeaway from a merged PR.

    Calls Haiku to distill the single most important learning from a merged
    pull request, then saves it as a Memory with importance=7.0.

    Args:
        pr_title: The pull request title.
        pr_body: The pull request body/description.
        diff_summary: A summary of the code changes (e.g., filenames changed).
        project_key: Project partition key. Resolved from env if not provided.

    Returns:
        Dict with memory_id and content if saved, or None if nothing to save.
    """
    if not pr_title:
        return None

    try:
        import anthropic

        from config.models import MODEL_FAST
        from utils.api_keys import get_anthropic_api_key

        api_key = get_anthropic_api_key()
        if not api_key:
            logger.warning(
                "[memory_extraction] No Anthropic API key, skipping post-merge extraction"
            )
            return None

        client = anthropic.Anthropic(api_key=api_key)

        prompt = POST_MERGE_EXTRACTION_PROMPT.format(
            title=pr_title,
            body=(pr_body or "")[:4000],
            diff_summary=(diff_summary or "")[:4000],
        )

        message = client.messages.create(
            model=MODEL_FAST,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = message.content[0].text.strip()

        # Check if response indicates no takeaway (NONE at start, empty, or too short)
        first_line = raw_text.split("\n")[0].strip()
        if first_line.upper() == "NONE" or not raw_text or len(raw_text) < 20:
            logger.debug("[memory_extraction] No post-merge learning extracted")
            return None

        # Save the learning as a memory
        from models.memory import SOURCE_AGENT, Memory

        if not project_key:
            from config.memory_defaults import DEFAULT_PROJECT_KEY

            project_key = os.environ.get("VALOR_PROJECT_KEY", DEFAULT_PROJECT_KEY)

        m = Memory.safe_save(
            agent_id="post-merge",
            project_key=project_key,
            content=raw_text[:500],
            importance=7.0,
            source=SOURCE_AGENT,
        )

        if m:
            logger.info(f"[memory_extraction] Post-merge learning saved: {raw_text[:100]}")
            return {
                "content": raw_text[:500],
                "memory_id": getattr(m, "memory_id", ""),
            }

        return None

    except Exception as e:
        logger.warning(f"[memory_extraction] Post-merge extraction failed (non-fatal): {e}")
        return None


def _extract_bigrams(text: str) -> set[tuple[str, ...]]:
    """Extract unigrams and bigrams from text for overlap detection.

    Filters out words shorter than 4 chars to reduce noise.
    """
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    unigrams = {(w,) for w in words}
    bigrams = {(words[i], words[i + 1]) for i in range(len(words) - 1)}
    return unigrams | bigrams


def _persist_outcome_metadata(
    memories: list,
    outcome_map: dict[str, str],
) -> None:
    """Persist dismissal/acted outcome data in Memory metadata.

    Updates dismissal_count and last_outcome in each memory's metadata dict.
    When dismissal_count reaches the threshold, decays importance.
    Resets dismissal_count on "acted" outcomes.

    Runs after ObservationProtocol to avoid conflicting saves.
    All exceptions are caught per-record -- one failure does not block others.
    """
    from config.memory_defaults import (
        DISMISSAL_DECAY_THRESHOLD,
        DISMISSAL_IMPORTANCE_DECAY,
        MIN_IMPORTANCE_FLOOR,
    )

    for m in memories:
        mid = getattr(m, "memory_id", "")
        if mid not in outcome_map:
            continue
        outcome = outcome_map[mid]
        try:
            meta = getattr(m, "metadata", None) or {}
            if not isinstance(meta, dict):
                meta = {}

            if outcome == "dismissed":
                meta["dismissal_count"] = meta.get("dismissal_count", 0) + 1
                meta["last_outcome"] = "dismissed"
                # Check threshold for importance decay
                if meta["dismissal_count"] >= DISMISSAL_DECAY_THRESHOLD:
                    current_importance = getattr(m, "importance", 1.0)
                    new_importance = max(
                        current_importance * DISMISSAL_IMPORTANCE_DECAY,
                        MIN_IMPORTANCE_FLOOR,
                    )
                    m.importance = new_importance
                    meta["dismissal_count"] = 0  # reset after decay
                    logger.debug(
                        f"[memory_extraction] Decayed importance for {mid}: "
                        f"{current_importance} -> {new_importance}"
                    )
            elif outcome == "acted":
                meta["dismissal_count"] = 0  # reset on positive signal
                meta["last_outcome"] = "acted"

            m.metadata = meta
            m.save()
        except Exception:
            continue  # fail-silent per record


async def detect_outcomes_async(
    injected_thoughts: list[tuple[str, str]],
    response_text: str,
) -> dict[str, str]:
    """Compare injected thoughts against response content.

    Uses bigram (1-2 word phrase) overlap for v1.
    Non-empty overlap -> "acted", empty -> "dismissed".

    Feeds results into ObservationProtocol.on_context_used().

    Returns dict of {memory_key: "acted"|"dismissed"}.
    """
    if not injected_thoughts or not response_text:
        return {}

    try:
        response_bigrams = _extract_bigrams(response_text)
        outcome_map: dict[str, str] = {}
        memory_keys: list[str] = []

        for memory_key, thought_content in injected_thoughts:
            thought_bigrams = _extract_bigrams(thought_content)
            overlap = thought_bigrams & response_bigrams

            if overlap:
                outcome_map[memory_key] = "acted"
            else:
                outcome_map[memory_key] = "dismissed"

            memory_keys.append(memory_key)

        # Feed into ObservationProtocol
        try:
            from popoto import ObservationProtocol

            from models.memory import Memory

            # Load memory instances by key
            memories = []
            for key in memory_keys:
                if key:
                    try:
                        results = Memory.query.filter(memory_id=key)
                        if results:
                            memories.append(results[0])
                    except Exception:
                        continue

            if memories:
                # Build outcome map keyed by redis_key
                redis_outcome_map = {}
                for m in memories:
                    mid = getattr(m, "memory_id", "")
                    if mid in outcome_map:
                        redis_key = getattr(m.db_key, "redis_key", "")
                        if redis_key:
                            redis_outcome_map[redis_key] = outcome_map[mid]

                if redis_outcome_map:
                    ObservationProtocol.on_context_used(memories, redis_outcome_map)
                    acted = sum(1 for v in redis_outcome_map.values() if v == "acted")
                    dismissed = len(redis_outcome_map) - acted
                    logger.info(
                        f"[memory_extraction] Outcome detection: "
                        f"{acted} acted, {dismissed} dismissed"
                    )

                # Persist dismissal/acted data in metadata
                # Done after ObservationProtocol to avoid conflicting saves
                _persist_outcome_metadata(memories, outcome_map)

        except Exception as e:
            logger.warning(f"[memory_extraction] ObservationProtocol failed (non-fatal): {e}")

        return outcome_map

    except Exception as e:
        logger.warning(f"[memory_extraction] Outcome detection failed (non-fatal): {e}")
        return {}


async def run_post_session_extraction(
    session_id: str,
    response_text: str,
    project_key: str | None = None,
) -> None:
    """Run full post-session extraction pipeline.

    1. Extract novel observations from response via Haiku
    2. Detect outcomes for injected thoughts
    3. Clean up session state

    Called from BackgroundTask._run_work() after session completes.
    """
    try:
        # Extract observations
        await extract_observations_async(session_id, response_text, project_key)

        # Detect outcomes for injected thoughts
        from agent.memory_hook import get_injected_thoughts

        injected = get_injected_thoughts(session_id)
        if injected:
            await detect_outcomes_async(injected, response_text)

    except Exception as e:
        logger.warning(f"[memory_extraction] Post-session extraction failed (non-fatal): {e}")
    finally:
        # Always clean up session state, even if extraction/detection fails
        try:
            from agent.memory_hook import clear_session

            clear_session(session_id)
        except Exception as e:
            logger.warning(f"[memory_extraction] Session cleanup failed: {e}")
