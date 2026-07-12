"""Four-way intent classifier for PM session routing.

Uses Haiku to classify incoming messages as informational queries (Teammate),
direct collaboration tasks, ambiguous discussions (Other), or work requests.
Conservative threshold (0.90) ensures ambiguous messages default to the full
Dev-session pipeline.

Intents:
- teammate: informational query -> Teammate mode (direct response)
- collaboration: direct task PM can handle without a dev-session
- other: ambiguous task, discussion, brainstorming -> PM uses judgment
- work: action/work request -> SDLC pipeline (dev-session)

All operations are async and wrapped in try/except -- classifier failures
must never prevent normal Dev-session processing.

Non-harness LLM call (#1925): the Haiku classification call routes through
``agent.llm.run_typed`` with a typed ``IntentClassification`` output model
instead of a hand-rolled sync Anthropic client + single-line text parser.
See ``docs/features/nonharness-llm-wrapper.md``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from utils.json_cache import JsonCache, get_or_compute_async

logger = logging.getLogger(__name__)

# Classification threshold: only route to Teammate if confidence exceeds this
TEAMMATE_CONFIDENCE_THRESHOLD = 0.90

# Persistent JSON cache for repeated identical classifier inputs.
#   namespace: data/cache/intent_classifier.json
#   ttl: 7200s (2h) — long enough to absorb status-check repetitions in a session
#   version: bump if CLASSIFIER_PROMPT changes (invalidates all old keys)
#   max_entries: 2000 — ~2.5MB worst case at ~200 bytes/entry
_cache = JsonCache(Path("data/cache/intent_classifier.json"), max_entries=2000)
# v2 (#1925): CLASSIFIER_PROMPT dropped the "respond in one line" format
# instruction now that output is schema-validated via run_typed, not parsed
# from free text -- old v1 cache entries are for a stale prompt shape.
_CACHE_VERSION = "v2"
_CACHE_TTL_SECONDS = 7200

CLASSIFIER_PROMPT = """\
You are an intent classifier. Classify the user message as "teammate", \
"collaboration", "other", or "work".

RULES:
- "teammate" = the user wants information, explanation, status, or lookup
- "collaboration" = the user wants a direct task done that does NOT require code \
changes: save to knowledge base, draft an issue, send a message, write a doc, \
search memory, look something up and act on it
- "other" = ambiguous task, discussion, brainstorming, or open-ended thinking \
that does not clearly fit teammate, collaboration, or work
- "work" = the user wants something created, fixed, changed, deployed, or built \
in the codebase (code changes, PRs, SDLC pipeline)

If in doubt, classify as collaboration.

EXAMPLES:

teammate examples:
- "What's the status of feature X?" -> teammate 0.98
- "How does the bridge work?" -> teammate 0.97
- "Where is the observer prompt?" -> teammate 0.99
- "What's broken in the bridge?" -> teammate 0.92
- "Show me the recent PRs" -> teammate 0.95
- "What tests are failing?" -> teammate 0.93
- "Who worked on the memory system?" -> teammate 0.96
- "When was the last deployment?" -> teammate 0.97
- "Explain the nudge loop" -> teammate 0.98
- "What's in the .env file?" -> teammate 0.95
- "How many open issues do we have?" -> teammate 0.96
- "What model does the classifier use?" -> teammate 0.97

collaboration examples:
- "Add this to the knowledge base" -> collaboration 0.97
- "Draft an issue for X" -> collaboration 0.96
- "Send a status update to the team" -> collaboration 0.95
- "Write a summary doc" -> collaboration 0.94
- "Save this to memory" -> collaboration 0.98
- "Look up the project priorities and send me a summary" -> collaboration 0.93
- "Create a Google Doc with meeting notes" -> collaboration 0.95
- "Check my calendar and tell me what's next" -> collaboration 0.92
- "File a GitHub issue about the flaky test" -> collaboration 0.96

other examples:
- "Let's think about this" -> other 0.94
- "What should we do about the architecture?" -> other 0.92
- "I have an idea for improving the pipeline" -> other 0.93
- "We need to discuss the deployment strategy" -> other 0.91
- "Should we prioritize feature X or bug Y?" -> other 0.95

work examples:
- "Fix the bridge" -> work 0.99
- "Add a new endpoint for health checks" -> work 0.98
- "Deploy the latest changes" -> work 0.99
- "Update the README" -> work 0.96
- "The observer prompt has a bug" -> work 0.88
- "ok fix that" -> work 0.95
- "Merge PR 42" -> work 0.99
- "Make the tests pass" -> work 0.98
- "Refactor the session queue" -> work 0.97
- "Can you update the docs?" -> work 0.93
- "Complete issue 499" -> work 0.99
- "Run the SDLC pipeline on this" -> work 0.99

Classify the message below. confidence is a float between 0.0 and 1.0;
reasoning is a brief explanation."""


@dataclass(frozen=True)
class IntentResult:
    """Result of intent classification."""

    intent: str  # "teammate", "collaboration", "other", or "work"
    confidence: float
    reasoning: str

    @property
    def is_teammate(self) -> bool:
        return self.intent == "teammate" and self.confidence >= TEAMMATE_CONFIDENCE_THRESHOLD

    @property
    def is_collaboration(self) -> bool:
        return self.intent == "collaboration"

    @property
    def is_other(self) -> bool:
        return self.intent == "other"

    @property
    def is_direct_action(self) -> bool:
        """True for collaboration or other -- PM handles directly without dev-session."""
        return self.is_collaboration or self.is_other

    @property
    def is_work(self) -> bool:
        return self.intent == "work"


class IntentClassification(BaseModel):
    """Typed structured-output model for the classifier's ``run_typed`` call.

    Field names mirror :class:`IntentResult` exactly (intent, confidence,
    reasoning) so ``.model_dump()`` produces a dict cacheable and
    reconstructable via ``IntentResult(**cached_dict)`` with no translation
    layer -- the function's public dict-cache shape is unchanged from before
    this migration (#1925). ``intent`` is a ``Literal`` so PydanticAI's
    schema validation rejects an out-of-vocabulary intent outright (with a
    single auto-retry) instead of this module silently coercing it.
    """

    intent: Literal["teammate", "collaboration", "other", "work"]
    confidence: float
    reasoning: str


async def classify_intent(
    message: str,
    context: dict | None = None,
) -> IntentResult:
    """Classify a message into one of four intents using Haiku.

    Returns one of: teammate, collaboration, other, or work.
    Teammate routes to direct response; collaboration/other route to
    PM direct-action mode; work routes to SDLC pipeline (dev-session).

    Args:
        message: The incoming user message text.
        context: Optional dict with keys like 'sender_name', 'recent_messages'
                 for additional classification context.

    Returns:
        IntentResult with intent, confidence, and reasoning.
        On any failure, returns a work intent (fail-safe to dev-session).
    """
    start = time.monotonic()

    try:
        from agent.llm import run_typed
        from config.models import MODEL_FAST
        from utils.api_keys import get_anthropic_api_key

        api_key = get_anthropic_api_key()
        if not api_key:
            logger.warning("[intent_classifier] No API key, defaulting to work")
            return IntentResult(intent="work", confidence=0.0, reasoning="no api key")

        # Build the user message with optional context
        user_content = ""
        recent_window = ""
        if context and context.get("recent_messages"):
            recent = context["recent_messages"][-3:]  # Last 3 messages
            recent_window = "Recent conversation:\n"
            for msg in recent:
                recent_window += f"- {msg}\n"
            recent_window += "\n"
            user_content += recent_window
        user_content += f"Classify this message:\n{message}"

        prompt = f"{CLASSIFIER_PROMPT}\n\n{user_content}"

        async def _call_and_serialize() -> dict:
            parsed = await run_typed(prompt, IntentClassification, model=MODEL_FAST)
            # #1925: parsed is a pydantic BaseModel (IntentClassification), not
            # the old IntentResult dataclass -- dataclasses.asdict would raise
            # TypeError on it. model_dump() is the pydantic equivalent and
            # preserves the same {intent, confidence, reasoning} dict shape.
            return parsed.model_dump()

        # Cache key uses the same formatted recent_window block sent to the API,
        # so any upstream prompt-builder change auto-invalidates the keys.
        #
        # get_or_compute_async (not asyncio.to_thread(get_or_compute, ...)):
        # run_typed acquires agent.anthropic_client's shared, loop-bound
        # semaphore. Running the compute step in a to_thread worker would
        # spin up a nested event loop via asyncio.run() and bind that
        # process-wide semaphore to it, breaking every other call site that
        # shares it. Awaiting in place keeps everyone on the same loop.
        cache_input = f"{message}\n---\n{recent_window}"
        cached_dict = await get_or_compute_async(
            _cache,
            cache_input,
            _call_and_serialize,
            ttl=_CACHE_TTL_SECONDS,
            version=_CACHE_VERSION,
        )
        result = IntentResult(**cached_dict)

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            f"[intent_classifier] {result.intent} (conf={result.confidence:.2f}) "
            f"in {elapsed_ms:.0f}ms: {result.reasoning}"
        )

        return result

    except Exception as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.error(f"[intent_classifier] Classification failed in {elapsed_ms:.0f}ms: {e}")
        # Fail-safe: default to work (dev-session)
        return IntentResult(intent="work", confidence=0.0, reasoning=f"classifier error: {e}")
