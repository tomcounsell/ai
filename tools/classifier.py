"""Work request classification using Haiku.

Classifies incoming work requests as bug, feature, or chore using
the fast Haiku model (~$0.0001/request). Returns structured JSON
with type, confidence score, and reasoning.

Usage:
    from tools.classifier import classify_request

    result = classify_request("Fix the broken login button")
    # Returns: {"type": "bug", "confidence": 0.95, "reason": "Reports broken functionality"}
"""

import json
import logging

import anthropic

from config.models import MODEL_FAST

logger = logging.getLogger(__name__)

CLASSIFICATION_PROMPT = """Classify this work request into exactly one category:
- bug: Something broken that previously worked
- feature: New functionality or capability
- chore: Maintenance, refactoring, documentation, dependencies

Request: {message}

Additional context: {context}

Respond with JSON only:
{{"type": "bug"|"feature"|"chore", "confidence": 0.0-1.0, "reason": "brief explanation"}}"""


def classify_request(message: str, context: str = "") -> dict:
    """Classify a work request using Haiku.

    Args:
        message: The work request to classify
        context: Optional additional context about the request

    Returns:
        Dict with keys:
        - type: "bug"|"feature"|"chore"
        - confidence: float between 0.0 and 1.0
        - reason: brief explanation of classification

    Raises:
        Exception: If classification fails (API error, invalid response, etc.)
    """
    try:
        from utils.api_keys import get_anthropic_api_key

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

        if result["type"] not in ["bug", "feature", "chore"]:
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
        logger.error(f"Classification failed: {e}")
        raise


async def classify_request_async(message: str, context: str = "") -> dict:
    """Async version of classify_request.

    Args:
        message: The work request to classify
        context: Optional additional context about the request

    Returns:
        Dict with keys:
        - type: "bug"|"feature"|"chore"
        - confidence: float between 0.0 and 1.0
        - reason: brief explanation of classification

    Raises:
        Exception: If classification fails (API error, invalid response, etc.)
    """
    try:
        from utils.api_keys import get_anthropic_api_key

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

        if result["type"] not in ["bug", "feature", "chore"]:
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
        logger.error(f"Classification failed: {e}")
        raise
