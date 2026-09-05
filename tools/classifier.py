"""Work request and message intent classification using Haiku.

Classifies incoming work requests as bug, feature, chore, or sdlc using
the fast Haiku model (~$0.0001/request). Returns structured JSON
with type, confidence score, and reasoning.

Also provides intake classification for message intent routing:
classifies messages as interjection or new_work to support bridge-level
message routing (#320). The intent classifier runs on the LOCAL granite
model via PydanticAI (``agent.llm.run_typed_local`` — durability plan
#2494 Task 13); work-request classification stays on Haiku.

Usage:
    from tools.classifier import classify_request

    result = classify_request("Fix the broken login button")
    # Returns: {"type": "bug", "confidence": 0.95, "reason": "Reports broken functionality"}

    result = classify_request("SDLC issue 274")
    # Returns: {"type": "sdlc", "confidence": 0.95, "reason": "References SDLC pipeline work"}

    from tools.classifier import classify_message_intent_async

    result = await classify_message_intent_async(
        "Actually, make the button blue instead",
        session_context="Working on UI redesign",
    )
    # Returns: {"intent": "interjection", "confidence": 0.92,
    #           "reason": "Course correction for active work"}
"""

import json
import logging
import os
from typing import Literal

import anthropic
from pydantic import BaseModel

from agent.anthropic_client import anthropic_slot
from config.models import MODEL_FAST
from utils.api_keys import get_anthropic_api_key

logger = logging.getLogger(__name__)

CLASSIFICATION_PROMPT = """Classify this work request into exactly one category:
- bug: Something broken that previously worked
- feature: New functionality or capability
- chore: Maintenance, refactoring, documentation, dependencies
- sdlc: References the SDLC pipeline, mentions "/do-sdlc", "issue #N", \
"run the pipeline", or asks for pipeline/build/plan execution

Request: {message}

Additional context: {context}

Respond with JSON only:
{{"type": "bug"|"feature"|"chore"|"sdlc", "confidence": 0.0-1.0, "reason": "brief explanation"}}"""


def classify_request(message: str, context: str = "") -> dict:
    """Classify a work request using Haiku.

    Args:
        message: The work request to classify
        context: Optional additional context about the request

    Returns:
        Dict with keys:
        - type: "bug"|"feature"|"chore"|"sdlc"
        - confidence: float between 0.0 and 1.0
        - reason: brief explanation of classification

    Raises:
        Exception: If classification fails (API error, invalid response, etc.)
    """
    try:
        api_key = get_anthropic_api_key()
        if not api_key:
            raise ValueError("No Anthropic API key found for classification")

        # Build prompt
        prompt = CLASSIFICATION_PROMPT.format(
            message=message,
            context=context if context else "(none provided)",
        )

        # Call Haiku
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=MODEL_FAST,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract and parse JSON response
        content = response.content[0].text.strip()

        # Handle markdown code blocks if present
        if content.startswith("```"):
            # Extract content between code fence markers
            lines = content.split("\n")
            content = "\n".join(lines[1:-1])  # Skip first and last line
            # If it's labeled as json, skip that line too
            if content.startswith("json"):
                content = "\n".join(content.split("\n")[1:])

        result = json.loads(content)

        # Validate response structure
        if "type" not in result or "confidence" not in result or "reason" not in result:
            raise ValueError(f"Invalid classification response structure: {result}")

        if result["type"] not in ["bug", "feature", "chore", "sdlc"]:
            raise ValueError(f"Invalid classification type: {result['type']}")

        if not isinstance(result["confidence"], int | float) or not (
            0.0 <= result["confidence"] <= 1.0
        ):
            raise ValueError(f"Invalid confidence value: {result['confidence']}")

        return result

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse classification JSON: {e}, content: {content}")
        raise
    except Exception as e:
        logger.error(f"Classification failed (sync): {e}")
        raise


def _parse_json_response(content: str) -> dict:
    """Parse a JSON response, handling markdown code blocks.

    Args:
        content: Raw text response from the API.

    Returns:
        Parsed dict from JSON content.

    Raises:
        json.JSONDecodeError: If the content cannot be parsed as JSON.
    """
    content = content.strip()

    # Handle markdown code blocks if present
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1])
        if content.startswith("json"):
            content = "\n".join(content.split("\n")[1:])

    return json.loads(content)


async def classify_request_async(message: str, context: str = "") -> dict:
    """Async version of classify_request.

    Args:
        message: The work request to classify
        context: Optional additional context about the request

    Returns:
        Dict with keys:
        - type: "bug"|"feature"|"chore"|"sdlc"
        - confidence: float between 0.0 and 1.0
        - reason: brief explanation of classification

    Raises:
        Exception: If classification fails (API error, invalid response, etc.)
    """
    try:
        api_key = get_anthropic_api_key()
        if not api_key:
            logger.warning(
                "Anthropic API key missing — skipping async classification, "
                "message preserved with sentinel default"
            )
            return {
                "type": None,
                "confidence": 0.0,
                "reason": "no anthropic api key — classification skipped",
            }

        # Build prompt
        prompt = CLASSIFICATION_PROMPT.format(
            message=message,
            context=context if context else "(none provided)",
        )

        # Call Haiku via shared semaphore-gated client (#1111)
        async with anthropic_slot() as client:
            response = await client.messages.create(
                model=MODEL_FAST,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )

        # Extract and parse JSON response
        content = response.content[0].text.strip()

        # Handle markdown code blocks if present
        if content.startswith("```"):
            # Extract content between code fence markers
            lines = content.split("\n")
            content = "\n".join(lines[1:-1])  # Skip first and last line
            # If it's labeled as json, skip that line too
            if content.startswith("json"):
                content = "\n".join(content.split("\n")[1:])

        result = json.loads(content)

        # Validate response structure
        if "type" not in result or "confidence" not in result or "reason" not in result:
            raise ValueError(f"Invalid classification response structure: {result}")

        if result["type"] not in ["bug", "feature", "chore", "sdlc"]:
            raise ValueError(f"Invalid classification type: {result['type']}")

        if not isinstance(result["confidence"], int | float) or not (
            0.0 <= result["confidence"] <= 1.0
        ):
            raise ValueError(f"Invalid confidence value: {result['confidence']}")

        return result

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse classification JSON: {e}, content: {content}")
        raise
    except Exception as e:
        logger.error(f"Classification failed (async): {e}")
        raise


# =============================================================================
# INTAKE MESSAGE INTENT CLASSIFICATION
# Classifies incoming messages as interjection or new_work for bridge-level
# routing (#320), on the local granite model via PydanticAI (#2494).
# =============================================================================

VALID_INTENTS = ("interjection", "new_work")

# Confidence threshold below which an interjection verdict defaults to
# new_work. GRAIN OF SALT: provisional/tunable via env — inherited from the
# Haiku-era intake classifier and revalidated by spike-3's granite replay
# (84.2% raw / ~95% behavioral agreement vs the prior classifier).
INTENT_CONFIDENCE_THRESHOLD = float(os.environ.get("INTENT_CONFIDENCE_THRESHOLD", "0.80"))

INTENT_CLASSIFICATION_PROMPT = """/no_think
Classify this incoming message's intent for routing.

You are a message intent classifier for a Telegram-based AI assistant. The human sends messages
that need to be routed: either to an active work session (interjection) or to a new work queue
(new_work).

## Categories

- **interjection**: The message is a follow-up to active work. It provides additional context,
  answers a question the agent asked, gives a course correction, or adds information to the
  current task. Examples: "Actually make it blue instead", "Here's the file I mentioned",
  "Yes, that approach works", "Also add error handling".

- **new_work**: The message is a new task, question, or request unrelated to the active session.
  Examples: "Fix the login bug", "How does the auth system work?", "Add dark mode",
  "What time is my next meeting?". This is the default when uncertain.

## Context

Message: {message}

Active session summary: {session_context}

Session status: {session_status}

## Rules
1. If no active session context is provided, classify as new_work.
2. Default to new_work when uncertain.
3. Keep the reason concise.

Return intent, confidence (0.0-1.0), and reason."""

# Appended to INTENT_CLASSIFICATION_PROMPT only when the context-recall flag is
# enabled (#2694). Kept as a separate constant so prompt and output schema can
# never disagree: the extended prompt and IntentDecisionWithRecall are selected
# together, and the base prompt is paired with the base IntentDecision.
#
# The test is a question about REFERENTS, never a keyword allowlist
# (Development Principle 3).
CONTEXT_RECALL_PROMPT_SECTION = """

## Context recall

Also judge, independently of the intent above: reading ONLY the message text
and knowing nothing about the conversation before it, do you know what thing
the message is talking about?

Set context_recall_advised = true when the message leans on something named
earlier that is not in the message itself -- a bare approval or refusal, a
pronoun or ordinal with no antecedent, a correction with no stated target.

Set context_recall_advised = false when the message names its own subject well
enough to act on without reading anything earlier.

Give a concise context_recall_reason either way."""


class IntentDecision(BaseModel):
    """Strict structured output for the intake intent classifier.

    Durability plan #2494 Task 13: the intake classifier runs on the local
    granite model via PydanticAI (``agent.llm.run_typed_local``) with this
    schema-validated output instead of the prior hand-parsed Haiku JSON.
    The ``acknowledgment`` class retired with ``AgentSession.expectations``:
    Jobs — never hard-closed, revived by any steer — replace session-level
    acknowledgment semantics, so the taxonomy is two-class.
    """

    intent: Literal["interjection", "new_work"]
    confidence: float
    reason: str


class IntentDecisionWithRecall(IntentDecision):
    """``IntentDecision`` plus the context-recall judgment (#2694).

    A separate model rather than two optional fields on ``IntentDecision`` so
    the prompt and the schema are selected together — with the kill switch off,
    the classifier sends the unextended prompt AND validates against the
    unextended schema, which is the exact pair spike-3 measured as
    regression-free. Extended schema + unextended prompt is a combination no
    spike covered and it must never reach the hot path.

    Both fields carry defaults so a granite response that omits them still
    validates. Without the defaults, an omission would raise into
    ``classify_message_intent_async``'s fail-open ``except`` and silently flip
    every message to ``new_work``, killing shipped interjection routing.
    """

    context_recall_advised: bool = False
    context_recall_reason: str = ""


async def classify_message_intent_async(
    message: str,
    session_context: str = "",
    session_status: str = "",
) -> dict:
    """Classify a message's intent for bridge routing, on local granite.

    Determines whether an incoming message is an interjection into an active
    session or a new work request. Used by the bridge intake classifier
    (#320) to route messages before enqueueing. Fail-open: any classifier
    failure (granite unreachable, schema exhaustion, bad confidence) returns
    the conservative ``new_work`` default — the message is already durable
    in the Room inbox before this runs, so a failure here costs routing
    precision, never the message.

    Args:
        message: The incoming message text to classify.
        session_context: Summary of the active session (context_summary field).
        session_status: Current session status (running/active/dormant).

    Returns:
        Dict with keys:
        - intent: "interjection"|"new_work"
        - confidence: float between 0.0 and 1.0
        - reason: brief explanation of classification
        - context_recall_advised: bool, True when the message leans on
          conversation history the session cannot see (#2694). Always False
          while ``CONTEXT_RECALL_INBOUND_ENABLED`` is off (the default).
        - context_recall_reason: brief explanation of the recall judgment
    """
    # Empty messages default to new_work
    if not message or not message.strip():
        return {
            "intent": "new_work",
            "confidence": 1.0,
            "reason": "Empty message",
            "context_recall_advised": False,
            "context_recall_reason": "",
        }

    # No session context means no active session to interject into.
    # NOTE (#2694): this hard return precedes any model call, so a session with
    # an empty context_summary yields no context-recall verdict either. That is
    # the accepted scope boundary (plan decision D4), not an oversight.
    if not session_context:
        return {
            "intent": "new_work",
            "confidence": 1.0,
            "reason": "No active session context",
            "context_recall_advised": False,
            "context_recall_reason": "",
        }

    try:
        from agent.llm import run_typed_local
        from bridge.context_recall import inbound_enabled

        # Prompt and schema are chosen together — never one without the other.
        recall_on = inbound_enabled()
        prompt_template = (
            INTENT_CLASSIFICATION_PROMPT + CONTEXT_RECALL_PROMPT_SECTION
            if recall_on
            else INTENT_CLASSIFICATION_PROMPT
        )
        output_model = IntentDecisionWithRecall if recall_on else IntentDecision

        prompt = prompt_template.format(
            message=message,
            session_context=session_context,
            session_status=session_status if session_status else "(unknown)",
        )

        decision = await run_typed_local(prompt, output_model)

        if not (0.0 <= decision.confidence <= 1.0):
            raise ValueError(f"Invalid confidence value: {decision.confidence}")

        result = {
            "intent": decision.intent,
            "confidence": decision.confidence,
            "reason": decision.reason,
            "context_recall_advised": bool(getattr(decision, "context_recall_advised", False)),
            "context_recall_reason": getattr(decision, "context_recall_reason", "") or "",
        }

        # Apply confidence threshold: below it, default to new_work.
        # Touches `intent` and `reason` only — the context-recall keys are an
        # independent judgment and must survive the clamp untouched (#2694).
        if result["intent"] != "new_work" and result["confidence"] < INTENT_CONFIDENCE_THRESHOLD:
            logger.info(
                f"Intent {result['intent']} below threshold "
                f"(confidence={result['confidence']:.2f}), defaulting to new_work"
            )
            result["intent"] = "new_work"
            result["reason"] = f"Below confidence threshold: {result['reason']}"

        return result

    except Exception as e:
        logger.warning(f"Intent classification failed, defaulting to new_work: {e}")
        return {
            "intent": "new_work",
            "confidence": 0.0,
            "reason": f"Classification failed: {e}",
            "context_recall_advised": False,
            "context_recall_reason": "",
        }
