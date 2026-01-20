"""
Document Summary Tool

Document summarization with configurable detail levels and key point extraction.
"""

import os
from pathlib import Path
from typing import Literal

import requests

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "claude-sonnet-4-20250514"  # Current Claude model


class DocSummaryError(Exception):
    """Document summary operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


def summarize(
    content: str,
    summary_type: Literal["brief", "standard", "detailed", "bullets"] = "standard",
    max_length: int | None = None,
    focus_areas: list[str] | None = None,
    preserve_quotes: bool = False,
) -> dict:
    """
    Summarize document content.

    Args:
        content: Document content or file path
        summary_type: Type of summary (brief, standard, detailed, bullets)
        max_length: Maximum summary length in words
        focus_areas: Specific topics to emphasize
        preserve_quotes: Keep important quotes

    Returns:
        dict with:
            - summary: Generated summary
            - key_points: List of main points
            - word_count: Summary word count
            - compression_ratio: Original vs summary size
    """
    # Try Anthropic first, fall back to OpenRouter
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    use_anthropic = bool(api_key)

    if not use_anthropic:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            return {"error": "ANTHROPIC_API_KEY or OPENROUTER_API_KEY required"}

    # If content looks like a file path, try to read it
    if content and len(content) < 500 and not "\n" in content:
        path = Path(content)
        if path.exists() and path.is_file():
            try:
                content = path.read_text(encoding="utf-8")
            except Exception as e:
                return {"error": f"Failed to read file: {str(e)}"}

    if not content or not content.strip():
        return {"error": "Content cannot be empty"}

    original_word_count = len(content.split())

    # Build prompt
    type_instructions = {
        "brief": "Provide a very brief summary in 1-2 sentences.",
        "standard": "Provide a clear, concise summary covering main points.",
        "detailed": "Provide a thorough summary covering all key aspects.",
        "bullets": "Provide a bullet-point summary with key takeaways.",
    }

    prompt_parts = [
        f"Summarize the following document.",
        type_instructions.get(summary_type, type_instructions["standard"]),
    ]

    if max_length:
        prompt_parts.append(f"Limit the summary to approximately {max_length} words.")

    if focus_areas:
        prompt_parts.append(f"Focus on these areas: {', '.join(focus_areas)}")

    if preserve_quotes:
        prompt_parts.append("Preserve any important direct quotes.")

    prompt_parts.extend([
        "",
        "Also extract 3-5 key points from the document.",
        "",
        "Document:",
        content[:15000],  # Limit content length
        "",
        "Respond in this format:",
        "SUMMARY:",
        "[Your summary here]",
        "",
        "KEY POINTS:",
        "- [Point 1]",
        "- [Point 2]",
        "- [etc]",
    ])

    prompt = "\n".join(prompt_parts)

    try:
        if use_anthropic:
            response = requests.post(
                ANTHROPIC_URL,
                headers={
                    "x-api-key": api_key,
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": DEFAULT_MODEL,
                    "max_tokens": 2048,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=120,
            )
        else:
            response = requests.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": f"anthropic/{DEFAULT_MODEL}",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 2048,
                },
                timeout=120,
            )

        response.raise_for_status()
        result = response.json()

        # Extract content based on API
        if use_anthropic:
            text = result.get("content", [{}])[0].get("text", "")
        else:
            text = result.get("choices", [{}])[0].get("message", {}).get("content", "")

        if not text:
            return {"error": "No response from AI"}

        # Parse response
        summary = ""
        key_points = []

        if "SUMMARY:" in text:
            parts = text.split("KEY POINTS:")
            summary_part = parts[0].replace("SUMMARY:", "").strip()
            summary = summary_part

            if len(parts) > 1:
                points_text = parts[1].strip()
                for line in points_text.split("\n"):
                    line = line.strip()
                    if line.startswith("-"):
                        key_points.append(line[1:].strip())
        else:
            summary = text
            key_points = []

        summary_word_count = len(summary.split())
        compression_ratio = (
            original_word_count / summary_word_count
            if summary_word_count > 0
            else 0
        )

        return {
            "summary": summary,
            "key_points": key_points,
            "word_count": summary_word_count,
            "original_word_count": original_word_count,
            "compression_ratio": round(compression_ratio, 2),
            "summary_type": summary_type,
        }

    except requests.exceptions.Timeout:
        return {"error": "Summary request timed out"}
    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {str(e)}"}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}


def summarize_file(
    file_path: str,
    summary_type: Literal["brief", "standard", "detailed", "bullets"] = "standard",
    **kwargs,
) -> dict:
    """
    Summarize a file.

    Args:
        file_path: Path to the file
        summary_type: Type of summary
        **kwargs: Additional arguments for summarize()

    Returns:
        dict with summary result
    """
    return summarize(file_path, summary_type=summary_type, **kwargs)


def extract_key_points(content: str, max_points: int = 5) -> dict:
    """
    Extract key points from content.

    Args:
        content: Document content
        max_points: Maximum number of points

    Returns:
        dict with key points
    """
    result = summarize(content, summary_type="bullets")

    if "error" in result:
        return result

    points = result.get("key_points", [])[:max_points]

    return {
        "key_points": points,
        "count": len(points),
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m tools.doc_summary 'content or file path'")
        sys.exit(1)

    content = sys.argv[1]
    if len(sys.argv) > 2:
        summary_type = sys.argv[2]
    else:
        summary_type = "standard"

    print(f"Summarizing ({summary_type})...")

    result = summarize(content, summary_type=summary_type)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    else:
        print(f"\nSummary:\n{result['summary']}")
        print(f"\nKey Points:")
        for point in result.get("key_points", []):
            print(f"  - {point}")
        print(f"\nCompression ratio: {result['compression_ratio']}x")
