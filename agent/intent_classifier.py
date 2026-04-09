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
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Classification threshold: only route to Teammate if confidence exceeds this
TEAMMATE_CONFIDENCE_THRESHOLD = 0.90

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

Respond with EXACTLY one line in the format:
INTENT confidence REASONING

Where INTENT is "teammate", "collaboration", "other", or "work", confidence \
is a float between 0.0 and 1.0, and REASONING is a brief explanation.

Example response: teammate 0.97 User is asking for information about system architecture"""


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


def _parse_classifier_response(raw: str) -> IntentResult:
    """Parse the classifier's single-line response into an IntentResult.

    Accepts four intents: teammate, collaboration, other, work.
    Unknown intents fall through to work with confidence 0.0.
    """
    raw = raw.strip()
    parts = raw.split(None, 2)
    if len(parts) < 2:
        logger.warning(f"[intent_classifier] Unparseable response: {raw!r}")
        return IntentResult(intent="work", confidence=0.0, reasoning="unparseable response")

    intent_str = parts[0].lower().strip()
    if intent_str not in ("teammate", "collaboration", "other", "work"):
        logger.warning(f"[intent_classifier] Unknown intent: {intent_str!r}")
        return IntentResult(
            intent="work", confidence=0.0, reasoning=f"unknown intent: {intent_str}"
        )

    try:
        confidence = float(parts[1])
    except ValueError:
        logger.warning(f"[intent_classifier] Bad confidence value: {parts[1]!r}")
        return IntentResult(intent="work", confidence=0.0, reasoning="bad confidence value")

    reasoning = parts[2] if len(parts) > 2 else ""
    return IntentResult(intent=intent_str, confidence=confidence, reasoning=reasoning)


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
        import anthropic

        from config.models import MODEL_FAST
        from utils.api_keys import get_anthropic_api_key

        api_key = get_anthropic_api_key()
        if not api_key:
            logger.warning("[intent_classifier] No API key, defaulting to work")
            return IntentResult(intent="work", confidence=0.0, reasoning="no api key")

        # Build the user message with optional context
        user_content = ""
        if context and context.get("recent_messages"):
            recent = context["recent_messages"][-3:]  # Last 3 messages
            user_content += "Recent conversation:\n"
            for msg in recent:
                user_content += f"- {msg}\n"
            user_content += "\n"
        user_content += f"Classify this message:\n{message}"

        client = anthropic.Anthropic(api_key=api_key)

        def _call_api():
            return client.messages.create(
                model=MODEL_FAST,
                max_tokens=100,
                messages=[{"role": "user", "content": user_content}],
                system=CLASSIFIER_PROMPT,
            )

        response = await asyncio.to_thread(_call_api)
        raw_text = response.content[0].text.strip()
        result = _parse_classifier_response(raw_text)

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
