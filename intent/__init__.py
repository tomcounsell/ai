"""
Intent Recognition Module

Classify user message intents using Ollama or heuristics.
"""

import os
import re
from typing import Any

import requests

# Ollama configuration
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b")

# Intent categories
INTENTS = {
    "search": "Web search or information lookup",
    "code_execution": "Execute or run code",
    "image_generation": "Generate or create images",
    "image_analysis": "Analyze or describe images",
    "file_operation": "Read, write, or manipulate files",
    "git_operation": "Git commands or version control",
    "chat": "General conversation or questions",
    "tool_use": "Use a specific tool",
    "system": "System commands or status checks",
    "unknown": "Cannot determine intent",
}


def classify_intent(
    message: str,
    context: dict | None = None,
    use_ollama: bool = True,
) -> dict:
    """
    Classify user message intent.

    Args:
        message: User message to classify
        context: Optional conversation context
        use_ollama: Whether to try Ollama first

    Returns:
        dict with keys:
            - intent: Classified intent category
            - confidence: Confidence score (0-1)
            - entities: Extracted entities
            - suggested_action: Recommended action
    """
    if not message or not message.strip():
        return {
            "intent": "unknown",
            "confidence": 0.0,
            "entities": {},
            "suggested_action": None,
        }

    # Try Ollama first if available and enabled
    if use_ollama:
        ollama_result = _classify_with_ollama(message, context)
        if ollama_result and "error" not in ollama_result:
            return ollama_result

    # Fallback to heuristics
    return _classify_with_heuristics(message)


def _classify_with_ollama(message: str, context: dict | None = None) -> dict | None:
    """Classify using Ollama."""
    try:
        prompt = f"""Classify the intent of this user message. Respond ONLY with valid JSON.

Message: "{message}"

Categories: {list(INTENTS.keys())}

Respond with JSON in this exact format:
{{"intent": "category", "confidence": 0.9, "entities": {{}}, "suggested_action": "description"}}"""

        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
            },
            timeout=10,
        )

        if response.status_code != 200:
            return None

        result = response.json()
        response_text = result.get("response", "")

        # Parse JSON response
        import json

        try:
            parsed = json.loads(response_text)
            # Validate response structure
            if "intent" in parsed and parsed["intent"] in INTENTS:
                return {
                    "intent": parsed["intent"],
                    "confidence": float(parsed.get("confidence", 0.8)),
                    "entities": parsed.get("entities", {}),
                    "suggested_action": parsed.get("suggested_action"),
                }
        except json.JSONDecodeError:
            pass

        return None

    except requests.exceptions.RequestException:
        return None
    except Exception:
        return None


def _classify_with_heuristics(message: str) -> dict:
    """Classify using pattern matching heuristics."""
    message_lower = message.lower().strip()
    entities = {}
    confidence = 0.7

    # Search patterns
    search_patterns = [
        r"^(search|find|look up|google|what is|who is|how to|when did|where is)",
        r"(search for|look for|find me)",
        r"\?$",  # Questions often need search
    ]
    if any(re.search(p, message_lower) for p in search_patterns):
        return {
            "intent": "search",
            "confidence": confidence,
            "entities": {"query": message},
            "suggested_action": "Execute web search",
        }

    # Code execution patterns
    code_patterns = [
        r"^(run|execute|eval)",
        r"```[\w]*\n",  # Code blocks
        r"(python|javascript|bash|code)",
    ]
    if any(re.search(p, message_lower) for p in code_patterns):
        # Try to extract code
        code_match = re.search(r"```(?:\w*\n)?(.*?)```", message, re.DOTALL)
        if code_match:
            entities["code"] = code_match.group(1)
        return {
            "intent": "code_execution",
            "confidence": confidence,
            "entities": entities,
            "suggested_action": "Execute code in sandbox",
        }

    # Image generation patterns
    image_gen_patterns = [
        r"(generate|create|make|draw).*(image|picture|photo|art)",
        r"(image|picture|photo) of",
        r"^(draw|illustrate|visualize)",
    ]
    if any(re.search(p, message_lower) for p in image_gen_patterns):
        return {
            "intent": "image_generation",
            "confidence": confidence,
            "entities": {"prompt": message},
            "suggested_action": "Generate image with DALL-E",
        }

    # Image analysis patterns
    image_analysis_patterns = [
        r"(analyze|describe|what('s| is) in).*(image|picture|photo|screenshot)",
        r"(look at|check|examine).*(image|picture|photo)",
    ]
    if any(re.search(p, message_lower) for p in image_analysis_patterns):
        return {
            "intent": "image_analysis",
            "confidence": confidence,
            "entities": {},
            "suggested_action": "Analyze image with vision model",
        }

    # File operation patterns
    file_patterns = [
        r"(read|write|create|delete|open|save).*(file|document)",
        r"(show me|display|cat|list).*(file|directory|folder)",
    ]
    if any(re.search(p, message_lower) for p in file_patterns):
        # Try to extract file path
        path_match = re.search(r'["\']?([/\w.-]+\.\w+)["\']?', message)
        if path_match:
            entities["path"] = path_match.group(1)
        return {
            "intent": "file_operation",
            "confidence": confidence,
            "entities": entities,
            "suggested_action": "Perform file operation",
        }

    # Git operation patterns
    git_patterns = [
        r"^(git |commit|push|pull|merge|branch)",
        r"(create|make).*(commit|branch|pr|pull request)",
    ]
    if any(re.search(p, message_lower) for p in git_patterns):
        return {
            "intent": "git_operation",
            "confidence": confidence,
            "entities": {},
            "suggested_action": "Execute git operation",
        }

    # System patterns
    system_patterns = [
        r"^(status|health|restart|stop|start)",
        r"(system|service|daemon).*(status|check|restart)",
    ]
    if any(re.search(p, message_lower) for p in system_patterns):
        return {
            "intent": "system",
            "confidence": confidence,
            "entities": {},
            "suggested_action": "Check or manage system",
        }

    # Default to chat
    return {
        "intent": "chat",
        "confidence": 0.5,
        "entities": {},
        "suggested_action": "Engage in conversation",
    }


def check_ollama_available() -> bool:
    """Check if Ollama is available."""
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        return response.status_code == 200
    except Exception:
        return False


def get_intent_description(intent: str) -> str:
    """Get description for an intent category."""
    return INTENTS.get(intent, "Unknown intent")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m intent 'your message here'")
        sys.exit(1)

    message = " ".join(sys.argv[1:])
    print(f"Classifying: {message}")
    print(f"Ollama available: {check_ollama_available()}")

    result = classify_intent(message)
    print(f"\nResult:")
    print(f"  Intent: {result['intent']}")
    print(f"  Confidence: {result['confidence']:.2f}")
    print(f"  Entities: {result['entities']}")
    print(f"  Suggested action: {result['suggested_action']}")
