"""Semantic session routing for unthreaded Telegram messages.

When a message arrives without reply-to threading, this module checks
for active/dormant sessions in the same chat that have declared
expectations (i.e., are waiting for specific human input). If a
high-confidence match is found, the message is routed to that session
instead of creating a new one.

Feature-flagged via SEMANTIC_ROUTING env var (default: disabled).
Confidence threshold: >= 0.80 for auto-routing.
"""

import json
import logging
import os

import anthropic

from config.models import MODEL_FAST
from utils.api_keys import get_anthropic_api_key

logger = logging.getLogger(__name__)

# Confidence threshold for auto-routing to a matched session.
# Below this, a new session is created (current behavior).
# Medium-confidence disambiguation (0.50-0.80) is deferred to Phase 3.
ROUTING_CONFIDENCE_THRESHOLD = 0.80


def is_semantic_routing_enabled() -> bool:
    """Check if semantic routing is enabled via feature flag.

    Controlled by SEMANTIC_ROUTING env var. Defaults to disabled.
    Accepts: 'true', '1', 'yes' (case-insensitive).
    """
    val = os.environ.get("SEMANTIC_ROUTING", "").lower().strip()
    return val in ("true", "1", "yes")


async def find_matching_session(
    chat_id: str,
    message_text: str,
    project_key: str,
) -> tuple[str | None, float]:
    """Find an active/dormant session that semantically matches this message.

    Queries AgentSession for sessions in the same chat with non-null
    expectations, then uses Haiku to classify whether the incoming
    message is responding to one of those sessions.

    Args:
        chat_id: The Telegram chat ID (as string).
        message_text: The incoming message text.
        project_key: The project key for the chat.

    Returns:
        Tuple of (session_id, confidence) if a high-confidence match is found.
        Returns (None, 0.0) if no match, no candidates, or any error occurs.
        All failures degrade gracefully to new session creation.
    """
    try:
        from models.agent_session import AgentSession

        # Query for candidate sessions: same chat, non-null expectations,
        # active or dormant status
        all_sessions = list(AgentSession.query.filter(chat_id=chat_id))

        candidates = []
        for s in all_sessions:
            if s.status not in ("active", "dormant"):
                continue
            if not s.expectations:
                continue
            candidates.append(s)

        # Zero candidates = zero cost (no LLM call)
        if not candidates:
            logger.debug(
                f"Semantic routing: no candidate sessions with expectations in chat {chat_id}"
            )
            return (None, 0.0)

        # Cap at 5 most recent by last_activity
        candidates.sort(
            key=lambda s: s.last_activity or s.created_at or 0,
            reverse=True,
        )
        candidates = candidates[:5]

        logger.info(
            f"Semantic routing: {len(candidates)} candidate session(s) "
            f"with expectations in chat {chat_id}"
        )

        # Build multiple-choice prompt for Haiku
        choices = []
        for i, s in enumerate(candidates, 1):
            context = s.context_summary or "(no context)"
            expectations = s.expectations or "(no expectations)"
            choices.append(
                f"Session {i} (ID: {s.session_id}):\n"
                f"  Context: {context}\n"
                f"  Expecting: {expectations}"
            )

        choices_text = "\n\n".join(choices)

        classifier_prompt = f"""/no_think
You are a message routing classifier. A new message arrived in a Telegram chat
WITHOUT reply-to threading. Determine if this message is responding to one of
the active sessions listed below.

Active sessions with expectations:

{choices_text}

New message: "{message_text[:500]}"

Respond with ONLY a JSON object:
{{"match": "session_id_string_or_null", "confidence": 0.0-1.0, \
"reason": "brief explanation"}}

Rules:
- Match if the message clearly addresses the expectations of a session
- confidence >= 0.80 means you're highly confident this is a response to that session
- confidence < 0.80 means uncertain — return null for match
- If the message could match multiple sessions, pick the best one
- If the message is a new topic unrelated to any session, return null
- NEVER match on vague similarity — only on clear topical relevance"""

        api_key = get_anthropic_api_key()
        if not api_key:
            logger.warning("No API key for semantic routing, skipping")
            return (None, 0.0)

        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=MODEL_FAST,
            max_tokens=256,
            messages=[{"role": "user", "content": classifier_prompt}],
        )

        raw = response.content[0].text.strip()

        # Parse response JSON
        try:
            # Strip markdown code fences if present
            cleaned = raw
            if cleaned.startswith("```"):
                import re

                cleaned = re.sub(r"^```\w*\n?", "", cleaned)
                cleaned = re.sub(r"\n?```$", "", cleaned)
                cleaned = cleaned.strip()

            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning(f"Semantic routing: could not parse classifier response: {raw[:200]}")
            return (None, 0.0)

        matched_id = data.get("match")
        confidence = float(data.get("confidence", 0.0))
        reason = data.get("reason", "")

        # Validate the matched session ID exists in our candidates
        if matched_id:
            valid_ids = {s.session_id for s in candidates}
            if matched_id not in valid_ids:
                logger.warning(
                    f"Semantic routing: classifier returned invalid session ID "
                    f"'{matched_id}', ignoring"
                )
                return (None, 0.0)

        # Apply confidence threshold
        if matched_id and confidence >= ROUTING_CONFIDENCE_THRESHOLD:
            logger.info(
                f"Semantic routing: matched session {matched_id} "
                f"with confidence {confidence:.2f} — {reason}"
            )
            return (matched_id, confidence)

        logger.info(
            f"Semantic routing: no high-confidence match "
            f"(best: {matched_id}, confidence: {confidence:.2f}, reason: {reason})"
        )
        return (None, 0.0)

    except Exception as e:
        # All failures degrade to new session creation
        logger.warning(f"Semantic routing failed (non-fatal): {e}")
        return (None, 0.0)
