"""
Image Tagging Tool

Tag and categorize images with AI for organization and search.
Tries Anthropic API first (direct), falls back to OpenRouter.
"""

import base64
import json
import os
from pathlib import Path

import requests

from config.models import MODEL_VISION, SONNET

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Vision tasks - Anthropic API (primary), OpenRouter (fallback)
DEFAULT_MODEL = SONNET
DEFAULT_MODEL_OPENROUTER = MODEL_VISION


class ImageTaggingError(Exception):
    """Image tagging operation failed."""

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
        header, data = image_source.split(",", 1)
        media_type = header.split(":")[1].split(";")[0]
        return data, media_type

    if image_source.startswith(("http://", "https://")):
        response = requests.get(image_source, timeout=30)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "image/jpeg")
        media_type = content_type.split(";")[0]
        return base64.b64encode(response.content).decode(), media_type

    path = Path(image_source)
    if not path.exists():
        raise ImageTaggingError(f"Image file not found: {image_source}", "validation")

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


def tag_image(
    image_source: str,
    tag_categories: list[str] | None = None,
    max_tags: int = 10,
    confidence_threshold: float = 0.5,
    custom_taxonomy: list[str] | None = None,
    model: str | None = None,
) -> dict:
    """
    Generate tags for an image.

    Tries Anthropic API first, falls back to OpenRouter if no Anthropic key.

    Args:
        image_source: File path, URL, or base64 encoded image
        tag_categories: Categories to include (default: all)
        max_tags: Maximum tags per category (default: 10)
        confidence_threshold: Minimum confidence (0-1)
        custom_taxonomy: Custom tag vocabulary
        model: Model to use (auto-selects based on available API keys)

    Returns:
        dict with:
            - tags: List of tags with confidence scores
            - categories: Detected categories
            - dominant_colors: Color palette
            - image_type: Photo, illustration, screenshot, etc.
    """
    # Try Anthropic first, fall back to OpenRouter
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    use_anthropic = bool(api_key)

    if not use_anthropic:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            return {"error": "ANTHROPIC_API_KEY or OPENROUTER_API_KEY required"}

    if tag_categories is None:
        tag_categories = ["objects", "scene", "activity", "style", "mood"]

    # Build prompt
    prompt_parts = [
        "Analyze this image and generate tags for categorization.",
        "",
        f"Categories to tag: {', '.join(tag_categories)}",
        f"Maximum tags per category: {max_tags}",
    ]

    if custom_taxonomy:
        prompt_parts.append(f"Preferred tags: {', '.join(custom_taxonomy)}")

    prompt_parts.extend(
        [
            "",
            "Also identify:",
            "- Dominant colors (up to 5)",
            "- Image type (photo, illustration, screenshot, diagram, etc.)",
            "",
            "Respond in this JSON format:",
            "{",
            '  "tags": [{"tag": "string", "category": "string", "confidence": 0.0-1.0}],',
            '  "dominant_colors": ["color1", "color2"],',
            '  "image_type": "string"',
            "}",
        ]
    )

    prompt = "\n".join(prompt_parts)

    try:
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
                    "max_tokens": 1024,
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
                                {"type": "text", "text": prompt},
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
                    "X-Title": "Valor Image Tagging",
                },
                json={
                    "model": model or DEFAULT_MODEL_OPENROUTER,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{media_type};base64,{image_data}"
                                    },
                                },
                            ],
                        }
                    ],
                    "max_tokens": 1024,
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

        # Clean up response
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
        if content.endswith("```"):
            content = content.rsplit("\n", 1)[0]
        content = content.strip()

        try:
            parsed = json.loads(content)

            # Filter by confidence threshold
            tags = [
                t
                for t in parsed.get("tags", [])
                if t.get("confidence", 0) >= confidence_threshold
            ]

            # Group by category
            categories = {}
            for tag in tags:
                cat = tag.get("category", "other")
                if cat not in categories:
                    categories[cat] = []
                categories[cat].append(tag)

            return {
                "image_source": image_source,
                "tags": tags[: max_tags * len(tag_categories)],
                "categories": categories,
                "dominant_colors": parsed.get("dominant_colors", []),
                "image_type": parsed.get("image_type", "unknown"),
                "tag_count": len(tags),
            }
        except json.JSONDecodeError:
            return {
                "error": "Failed to parse AI response",
                "raw_response": content,
            }

    except ImageTaggingError as e:
        return {"error": e.message, "image_source": image_source}
    except requests.exceptions.Timeout:
        return {"error": "Tagging request timed out", "image_source": image_source}
    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {str(e)}", "image_source": image_source}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}", "image_source": image_source}


def batch_tag_images(
    image_sources: list[str],
    tag_categories: list[str] | None = None,
    max_tags: int = 10,
) -> dict:
    """
    Tag multiple images.

    Args:
        image_sources: List of image paths or URLs
        tag_categories: Categories to include
        max_tags: Maximum tags per category

    Returns:
        dict with results for each image
    """
    results = []
    for source in image_sources:
        result = tag_image(
            source,
            tag_categories=tag_categories,
            max_tags=max_tags,
        )
        results.append(result)

    successful = sum(1 for r in results if "error" not in r)

    return {
        "results": results,
        "total": len(image_sources),
        "successful": successful,
        "failed": len(image_sources) - successful,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m tools.image_tagging 'path/to/image.jpg'")
        sys.exit(1)

    image_path = sys.argv[1]
    print(f"Tagging: {image_path}")

    result = tag_image(image_path)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    else:
        print(f"\nImage type: {result['image_type']}")
        print(f"Colors: {', '.join(result['dominant_colors'])}")
        print(f"\nTags ({result['tag_count']}):")
        for tag in result["tags"]:
            print(f"  - {tag['tag']} ({tag['category']}) [{tag['confidence']:.2f}]")
