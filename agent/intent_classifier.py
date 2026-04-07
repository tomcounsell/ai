"""Binary intent classifier for ChatSession Q&A mode.

Uses Haiku to classify incoming messages as informational queries (Q&A)
or work requests. Conservative threshold (0.90) ensures ambiguous messages
default to the full DevSession pipeline.

All operations are async and wrapped in try/except -- classifier failures
must never prevent normal DevSession processing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Classification threshold: only route to Q&A if confidence exceeds this
QA_CONFIDENCE_THRESHOLD = 0.90

CLASSIFIER_PROMPT = """\
You are a binary intent classifier. Classify the user message as either \
"qa" (informational query) or "work" (action/work request).

RULES:
- "qa" = the user wants information, explanation, status, or lookup
- "work" = the user wants something created, fixed, changed, deployed, or built

EXAMPLES:

qa examples:
- "What's the status of feature X?" -> qa 0.98
- "How does the bridge work?" -> qa 0.97
- "Where is the observer prompt?" -> qa 0.99
- "What's broken in the bridge?" -> qa 0.92
- "Show me the recent PRs" -> qa 0.95
- "What tests are failing?" -> qa 0.93
- "Who worked on the memory system?" -> qa 0.96
- "When was the last deployment?" -> qa 0.97
- "Explain the nudge loop" -> qa 0.98
- "What's in the .env file?" -> qa 0.95
- "How many open issues do we have?" -> qa 0.96
- "What model does the classifier use?" -> qa 0.97

work examples:
- "Fix the bridge" -> work 0.99
- "Add a new endpoint for health checks" -> work 0.98
- "Create an issue for the memory leak" -> work 0.97
- "Deploy the latest changes" -> work 0.99
- "Update the README" -> work 0.96
- "The observer prompt has a bug" -> work 0.88
- "ok fix that" -> work 0.95
- "Merge PR 42" -> work 0.99
- "Make the tests pass" -> work 0.98
- "Refactor the job queue" -> work 0.97
- "Can you update the docs?" -> work 0.93
- "Complete issue 499" -> work 0.99
- "Run the SDLC pipeline on this" -> work 0.99

Respond with EXACTLY one line in the format:
INTENT confidence REASONING

Where INTENT is "qa" or "work", confidence is a float between 0.0 and 1.0, \
and REASONING is a brief explanation.

Example response: qa 0.97 User is asking for information about system architecture"""


@dataclass(frozen=True)
class IntentResult:
    """Result of intent classification."""

    intent: str  # "qa" or "work"
    confidence: float
    reasoning: str

    @property
    def is_qa(self) -> bool:
        return self.intent == "qa" and self.confidence >= QA_CONFIDENCE_THRESHOLD

    @property
    def is_work(self) -> bool:
        return self.intent == "work" or self.confidence < QA_CONFIDENCE_THRESHOLD


def _parse_classifier_response(raw: str) -> IntentResult:
    """Parse the classifier's single-line response into an IntentResult."""
    raw = raw.strip()
    parts = raw.split(None, 2)
    if len(parts) < 2:
        logger.warning(f"[intent_classifier] Unparseable response: {raw!r}")
        return IntentResult(intent="work", confidence=0.0, reasoning="unparseable response")

    intent_str = parts[0].lower().strip()
    if intent_str not in ("qa", "work"):
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
    """Classify a message as Q&A or work request using Haiku.

    Args:
        message: The incoming user message text.
        context: Optional dict with keys like 'sender_name', 'recent_messages'
                 for additional classification context.

    Returns:
        IntentResult with intent, confidence, and reasoning.
        On any failure, returns a work intent (fail-safe to DevSession).
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
        # Fail-safe: default to work (DevSession)
        return IntentResult(intent="work", confidence=0.0, reasoning=f"classifier error: {e}")
