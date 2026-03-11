"""Work request and message intent classification using Haiku.

Classifies incoming work requests as bug, feature, chore, or sdlc using
the fast Haiku model (~$0.0001/request). Returns structured JSON
with type, confidence score, and reasoning.

Also provides intake classification for message intent routing:
classifies messages as interjection, new_work, or acknowledgment
to support bridge-level message routing (#320).

Usage:
    from tools.classifier import classify_request

    result = classify_request("Fix the broken login button")
    # Returns: {"type": "bug", "confidence": 0.95, "reason": "Reports broken functionality"}

    result = classify_request("SDLC issue 274")
    # Returns: {"type": "sdlc", "confidence": 0.95, "reason": "References SDLC pipeline work"}

    from tools.classifier import classify_message_intent

    result = classify_message_intent(
        "Actually, make the button blue instead",
        session_context="Working on UI redesign",
        session_expectations="Waiting for color preference",
    )
    # Returns: {"intent": "interjection", "confidence": 0.92,
    #           "reason": "Course correction for active work"}
"""

import json
import logging

import anthropic

from config.models import MODEL_FAST
from utils.api_keys import get_anthropic_api_key

logger = logging.getLogger(__name__)

CLASSIFICATION_PROMPT = """Classify this work request into exactly one category:
- bug: Something broken that previously worked
- feature: New functionality or capability
- chore: Maintenance, refactoring, documentation, dependencies
- sdlc: References the SDLC pipeline, mentions "/sdlc", "issue #N", \
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
            raise ValueError("No Anthropic API key found for classification")

        # Build prompt
        prompt = CLASSIFICATION_PROMPT.format(
            message=message,
            context=context if context else "(none provided)",
        )

        # Call Haiku
        client = anthropic.AsyncAnthropic(api_key=api_key)
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
# Classifies incoming messages as interjection, new_work, or acknowledgment
# for bridge-level routing (#320).
# =============================================================================

VALID_INTENTS = ("interjection", "new_work", "acknowledgment")

INTENT_CLASSIFICATION_PROMPT = """Classify this incoming message's intent for routing.

You are a message intent classifier for a Telegram-based AI assistant. The human sends messages
that need to be routed: either to an active work session (interjection), to a new work queue
(new_work), or as a signal that work is complete (acknowledgment).

## Categories

- **interjection**: The message is a follow-up to active work. It provides additional context,
  answers a question the agent asked, gives a course correction, or adds information to the
  current task. Examples: "Actually make it blue instead", "Here's the file I mentioned",
  "Yes, that approach works", "Also add error handling".

- **new_work**: The message is a new task, question, or request unrelated to the active session.
  Examples: "Fix the login bug", "How does the auth system work?", "Add dark mode",
  "What time is my next meeting?". This is the default when uncertain.

- **acknowledgment**: The message signals that the current work is done/approved. The human
  is satisfied and wants to close out the session. Examples: "Looks good", "Perfect, thanks",
  "Done", "Ship it", "LGTM". Only classify as acknowledgment if the active session is
  waiting for human approval (dormant with expectations).

## Context

Message: {message}

Active session summary: {session_context}

Session expectations (what the agent is waiting for): {session_expectations}

Session status: {session_status}

## Rules
1. If no active session context is provided, classify as new_work.
2. If the session is NOT dormant and the message looks like acknowledgment, classify as new_work
   instead (active sessions can't be acknowledged to completion).
3. Default to new_work when confidence is below 0.80.
4. Keep your response concise.

Respond with JSON only:
{{"intent": "interjection"|"new_work"|"acknowledgment", \
"confidence": 0.0-1.0, "reason": "brief explanation"}}"""


def classify_message_intent(
    message: str,
    session_context: str = "",
    session_expectations: str = "",
    session_status: str = "",
) -> dict:
    """Classify a message's intent for bridge routing.

    Determines whether an incoming message is an interjection into an active session,
    a new work request, or an acknowledgment of completed work. Used by the bridge
    intake classifier (#320) to route messages before enqueueing.

    Args:
        message: The incoming message text to classify.
        session_context: Summary of the active session (context_summary field).
        session_expectations: What the agent is waiting for (expectations field).
        session_status: Current session status (running/active/dormant).

    Returns:
        Dict with keys:
        - intent: "interjection"|"new_work"|"acknowledgment"
        - confidence: float between 0.0 and 1.0
        - reason: brief explanation of classification
    """
    # Empty messages default to new_work
    if not message or not message.strip():
        return {"intent": "new_work", "confidence": 1.0, "reason": "Empty message"}

    # No session context means no active session to interject into
    if not session_context and not session_expectations:
        return {
            "intent": "new_work",
            "confidence": 1.0,
            "reason": "No active session context",
        }

    try:


        api_key = get_anthropic_api_key()
        if not api_key:
            raise ValueError("No Anthropic API key found for classification")

        prompt = INTENT_CLASSIFICATION_PROMPT.format(
            message=message,
            session_context=session_context if session_context else "(no active session)",
            session_expectations=session_expectations if session_expectations else "(none)",
            session_status=session_status if session_status else "(unknown)",
        )

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=MODEL_FAST,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )

        content = response.content[0].text.strip()
        result = _parse_json_response(content)

        # Validate response structure
        if "intent" not in result or "confidence" not in result or "reason" not in result:
            raise ValueError(f"Invalid intent classification structure: {result}")

        if result["intent"] not in VALID_INTENTS:
            raise ValueError(f"Invalid intent type: {result['intent']}")

        if not isinstance(result["confidence"], int | float) or not (
            0.0 <= result["confidence"] <= 1.0
        ):
            raise ValueError(f"Invalid confidence value: {result['confidence']}")

        # Apply confidence threshold: below 0.80 defaults to new_work
        if result["intent"] != "new_work" and result["confidence"] < 0.80:
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
        }


async def classify_message_intent_async(
    message: str,
    session_context: str = "",
    session_expectations: str = "",
    session_status: str = "",
) -> dict:
    """Async version of classify_message_intent.

    Classifies a message's intent for bridge routing using the async Anthropic client.
    Used in the bridge handler where async is required.

    Args:
        message: The incoming message text to classify.
        session_context: Summary of the active session (context_summary field).
        session_expectations: What the agent is waiting for (expectations field).
        session_status: Current session status (running/active/dormant).

    Returns:
        Dict with keys:
        - intent: "interjection"|"new_work"|"acknowledgment"
        - confidence: float between 0.0 and 1.0
        - reason: brief explanation of classification
    """
    # Empty messages default to new_work
    if not message or not message.strip():
        return {"intent": "new_work", "confidence": 1.0, "reason": "Empty message"}

    # No session context means no active session to interject into
    if not session_context and not session_expectations:
        return {
            "intent": "new_work",
            "confidence": 1.0,
            "reason": "No active session context",
        }

    try:


        api_key = get_anthropic_api_key()
        if not api_key:
            raise ValueError("No Anthropic API key found for classification")

        prompt = INTENT_CLASSIFICATION_PROMPT.format(
            message=message,
            session_context=session_context if session_context else "(no active session)",
            session_expectations=session_expectations if session_expectations else "(none)",
            session_status=session_status if session_status else "(unknown)",
        )

        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=MODEL_FAST,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )

        content = response.content[0].text.strip()
        result = _parse_json_response(content)

        # Validate response structure
        if "intent" not in result or "confidence" not in result or "reason" not in result:
            raise ValueError(f"Invalid intent classification structure: {result}")

        if result["intent"] not in VALID_INTENTS:
            raise ValueError(f"Invalid intent type: {result['intent']}")

        if not isinstance(result["confidence"], int | float) or not (
            0.0 <= result["confidence"] <= 1.0
        ):
            raise ValueError(f"Invalid confidence value: {result['confidence']}")

        # Apply confidence threshold: below 0.80 defaults to new_work
        if result["intent"] != "new_work" and result["confidence"] < 0.80:
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
        }
