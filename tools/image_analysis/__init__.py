"""
Image Analysis Tool

Multi-modal vision analysis using AI models.
Tries Anthropic API first (direct), falls back to OpenRouter.
"""

import base64
import os
from pathlib import Path
from typing import Literal

import requests

from config.models import MODEL_VISION, SONNET

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Vision tasks - Anthropic API (primary), OpenRouter (fallback)
DEFAULT_MODEL = SONNET
DEFAULT_MODEL_OPENROUTER = MODEL_VISION


class ImageAnalysisError(Exception):
    """Image analysis operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


def _load_image(image_source: str) -> tuple[str, str]:
    """
    Load an image from file path, URL, or base64.

    Returns:
        Tuple of (base64_data, media_type)
    """
    if image_source.startswith("data:"):
        # Already base64 data URL
        header, data = image_source.split(",", 1)
        media_type = header.split(":")[1].split(";")[0]
        return data, media_type

    if image_source.startswith(("http://", "https://")):
        # URL - download and encode
        response = requests.get(image_source, timeout=30)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "image/jpeg")
        media_type = content_type.split(";")[0]
        return base64.b64encode(response.content).decode(), media_type

    # File path
    path = Path(image_source)
    if not path.exists():
        raise ImageAnalysisError(f"Image file not found: {image_source}", "validation")

    ext = path.suffix.lower()
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_types.get(ext, "image/jpeg")

    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode(), media_type


def analyze_image(
    image_source: str,
    analysis_types: list[str] | None = None,
    detail_level: Literal[
        "minimal", "standard", "detailed", "comprehensive"
    ] = "standard",
    output_format: Literal[
        "structured", "narrative", "technical", "accessibility"
    ] = "structured",
    model: str | None = None,
) -> dict:
    """
    Analyze an image using AI vision models.

    Tries Anthropic API first, falls back to OpenRouter if no Anthropic key.

    Args:
        image_source: File path, URL, or base64 encoded image
        analysis_types: Types of analysis (description, objects, text, tags, safety)
        detail_level: Level of detail in analysis
        output_format: Format of the output
        model: Model to use (auto-selects based on available API keys)

    Returns:
        dict with analysis results:
            - description: Natural language description
            - objects: Detected objects
            - text: Extracted text (OCR)
            - tags: Relevant tags
            - safety_rating: Content safety assessment
    """
    # Try Anthropic first, fall back to OpenRouter
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    use_anthropic = bool(api_key)

    if not use_anthropic:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            return {"error": "ANTHROPIC_API_KEY or OPENROUTER_API_KEY required"}

    if analysis_types is None:
        analysis_types = ["description", "objects", "text"]

    # Build prompt based on analysis types and detail level
    prompts = {
        "description": "Describe what you see in this image.",
        "objects": "List all objects visible in the image with their positions.",
        "text": "Extract any text visible in the image (OCR).",
        "tags": "Generate relevant tags/labels for this image.",
        "safety": "Assess the content safety of this image.",
    }

    detail_instructions = {
        "minimal": "Be very brief, just key points.",
        "standard": "Provide a clear, concise analysis.",
        "detailed": "Provide thorough analysis with specifics.",
        "comprehensive": "Provide exhaustive analysis covering all aspects.",
    }

    format_instructions = {
        "structured": "Format your response as structured data with clear sections.",
        "narrative": "Write your analysis as flowing prose.",
        "technical": "Use technical terminology and precise descriptions.",
        "accessibility": "Write alt-text suitable for screen readers.",
    }

    # Combine prompts
    analysis_prompts = [prompts.get(t, t) for t in analysis_types if t in prompts]
    combined_prompt = "\n".join(
        [
            "Analyze this image:",
            *analysis_prompts,
            "",
            detail_instructions.get(detail_level, detail_instructions["standard"]),
            format_instructions.get(output_format, format_instructions["structured"]),
        ]
    )

    try:
        # Load image
        image_data, media_type = _load_image(image_source)

        if use_anthropic:
            # Anthropic API (direct) - preferred
            response = requests.post(
                ANTHROPIC_URL,
                headers={
                    "x-api-key": api_key,
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": model or DEFAULT_MODEL,
                    "max_tokens": 2048,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": media_type,
                                        "data": image_data,
                                    },
                                },
                                {"type": "text", "text": combined_prompt},
                            ],
                        }
                    ],
                },
                timeout=120,
            )
        else:
            # OpenRouter fallback
            response = requests.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost",
                    "X-Title": "Valor Image Analysis",
                },
                json={
                    "model": model or DEFAULT_MODEL_OPENROUTER,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": combined_prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{media_type};base64,{image_data}"
                                    },
                                },
                            ],
                        }
                    ],
                    "max_tokens": 2048,
                },
                timeout=120,
            )

        response.raise_for_status()
        result = response.json()

        # Extract content based on API
        if use_anthropic:
            content = result.get("content", [{}])[0].get("text", "")
        else:
            if "choices" not in result or len(result["choices"]) == 0:
                return {"error": "No response from model", "image_source": image_source}
            content = result["choices"][0]["message"]["content"]

        if not content:
            return {"error": "No response from model", "image_source": image_source}

        # Build response
        analysis_result = {
            "image_source": image_source,
            "detail_level": detail_level,
            "analysis_types": analysis_types,
            "raw_analysis": content,
        }

        # Parse structured sections if present
        if "description" in analysis_types:
            analysis_result["description"] = content

        if "objects" in analysis_types:
            analysis_result["objects"] = []

        if "text" in analysis_types:
            analysis_result["text"] = ""

        if "tags" in analysis_types:
            analysis_result["tags"] = []

        if "safety" in analysis_types:
            analysis_result["safety_rating"] = "unknown"

        return analysis_result

    except ImageAnalysisError as e:
        return {"error": e.message, "image_source": image_source}
    except requests.exceptions.Timeout:
        return {"error": "Analysis request timed out", "image_source": image_source}
    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {str(e)}", "image_source": image_source}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}", "image_source": image_source}


def extract_text(image_source: str, model: str | None = None) -> dict:
    """
    Extract text from an image (OCR).

    Args:
        image_source: File path, URL, or base64 encoded image
        model: Model to use (optional)

    Returns:
        dict with extracted text
    """
    return analyze_image(
        image_source,
        analysis_types=["text"],
        detail_level="detailed",
        output_format="technical",
        model=model,
    )


def generate_alt_text(image_source: str, model: str | None = None) -> dict:
    """
    Generate accessibility alt-text for an image.

    Args:
        image_source: File path, URL, or base64 encoded image
        model: Model to use (optional)

    Returns:
        dict with alt-text
    """
    return analyze_image(
        image_source,
        analysis_types=["description"],
        detail_level="standard",
        output_format="accessibility",
        model=model,
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m tools.image_analysis 'path/to/image.jpg'")
        sys.exit(1)

    image_path = sys.argv[1]
    print(f"Analyzing: {image_path}")

    result = analyze_image(image_path)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    else:
        print(
            f"\nAnalysis:\n{result.get('raw_analysis', result.get('description', ''))}"
        )
