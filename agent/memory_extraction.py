"""Post-session memory extraction and outcome detection.

Extracts novel observations from agent response text via Haiku,
saves them as Memory records with InteractionWeight.AGENT importance.

Detects outcomes by comparing injected thoughts against response
content using bigram overlap, feeds results into ObservationProtocol.

All operations are async, wrapped in try/except — failures must never
crash the agent or block session completion.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

# Extraction prompt for Haiku
EXTRACTION_PROMPT = """Extract novel observations from this agent session response. Focus on:
1. Decisions made (what was chosen and why)
2. Surprises found (unexpected findings)
3. Corrections received (errors caught, assumptions invalidated)
4. Patterns noticed (recurring themes, conventions)

Return only genuinely novel, specific observations. Skip generic statements.
Return one observation per line, plain text. No numbering, no bullets.
If there are no novel observations, return NONE."""


async def extract_observations_async(
    session_id: str,
    response_text: str,
    project_key: str | None = None,
) -> list[dict]:
    """Extract novel observations from agent response via Haiku.

    Calls Haiku to identify decisions, surprises, corrections, and patterns.
    Saves each as a Memory record with InteractionWeight.AGENT (1.0) importance.

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

        # Parse observations (one per line)
        observations = [
            line.strip() for line in raw_text.split("\n") if line.strip() and len(line.strip()) > 10
        ]

        if not observations:
            return []

        # Save each observation as Memory
        from popoto import InteractionWeight

        from models.memory import Memory

        if not project_key:
            project_key = os.environ.get("VALOR_PROJECT_KEY", "dm")

        saved = []
        for obs in observations[:10]:  # cap at 10 observations
            m = Memory.safe_save(
                agent_id=f"extraction-{session_id}",
                project_key=project_key,
                content=obs[:500],
                importance=InteractionWeight.AGENT,
                source="agent",
            )
            if m:
                saved.append(
                    {
                        "content": obs[:500],
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


def _extract_bigrams(text: str) -> set[tuple[str, ...]]:
    """Extract unigrams and bigrams from text for overlap detection.

    Filters out words shorter than 4 chars to reduce noise.
    """
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    unigrams = {(w,) for w in words}
    bigrams = {(words[i], words[i + 1]) for i in range(len(words) - 1)}
    return unigrams | bigrams


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

        # Clean up session state
        from agent.memory_hook import clear_session

        clear_session(session_id)

    except Exception as e:
        logger.warning(f"[memory_extraction] Post-session extraction failed (non-fatal): {e}")
