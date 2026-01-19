"""
Documentation Tool

Generate documentation from code including docstrings, README files, and API docs.
"""

import os
from pathlib import Path
from typing import Literal

import requests

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "claude-3-5-sonnet-20241022"


class DocumentationError(Exception):
    """Documentation generation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


def generate_docs(
    source: str,
    doc_type: Literal["docstring", "readme", "api", "changelog"] = "docstring",
    style: Literal["google", "numpy", "sphinx", "markdown"] = "google",
    detail_level: Literal["minimal", "standard", "comprehensive"] = "standard",
    include_examples: bool = True,
) -> dict:
    """
    Generate documentation from code or description.

    Args:
        source: Code or description to document
        doc_type: Type of documentation to generate
        style: Documentation style
        detail_level: Level of detail
        include_examples: Include usage examples

    Returns:
        dict with:
            - documentation: Generated documentation
            - format: Output format used
    """
    # Try Anthropic first, fall back to OpenRouter
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    use_anthropic = bool(api_key)

    if not use_anthropic:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            return {"error": "ANTHROPIC_API_KEY or OPENROUTER_API_KEY required"}

    # If source looks like a file path, try to read it
    if source and len(source) < 500 and not "\n" in source:
        path = Path(source)
        if path.exists() and path.is_file():
            try:
                source = path.read_text(encoding="utf-8")
            except Exception as e:
                return {"error": f"Failed to read file: {str(e)}"}

    if not source or not source.strip():
        return {"error": "Source cannot be empty"}

    # Build prompt based on doc type
    type_prompts = {
        "docstring": _build_docstring_prompt(source, style, detail_level, include_examples),
        "readme": _build_readme_prompt(source, detail_level, include_examples),
        "api": _build_api_prompt(source, style, detail_level),
        "changelog": _build_changelog_prompt(source),
    }

    prompt = type_prompts.get(doc_type, type_prompts["docstring"])

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
                    "max_tokens": 4096,
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
                    "max_tokens": 4096,
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

        return {
            "documentation": text.strip(),
            "doc_type": doc_type,
            "style": style,
            "format": "markdown" if doc_type in ("readme", "api", "changelog") else style,
        }

    except requests.exceptions.Timeout:
        return {"error": "Documentation request timed out"}
    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {str(e)}"}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}


def _build_docstring_prompt(
    source: str, style: str, detail_level: str, include_examples: bool
) -> str:
    """Build prompt for docstring generation."""
    style_descriptions = {
        "google": "Google style docstrings with Args, Returns, Raises sections",
        "numpy": "NumPy style docstrings with Parameters, Returns sections",
        "sphinx": "Sphinx/reStructuredText style with :param: and :return:",
        "markdown": "Markdown-friendly docstrings",
    }

    detail_descriptions = {
        "minimal": "Brief, one-line descriptions",
        "standard": "Clear descriptions with all parameters documented",
        "comprehensive": "Detailed descriptions with types, examples, and edge cases",
    }

    parts = [
        f"Generate a {style_descriptions.get(style, 'Google style')} docstring for this code.",
        f"Detail level: {detail_descriptions.get(detail_level, 'standard')}",
    ]

    if include_examples:
        parts.append("Include a usage example in the docstring.")

    parts.extend([
        "",
        "Code:",
        source[:8000],
        "",
        "Output only the docstring, no explanation.",
    ])

    return "\n".join(parts)


def _build_readme_prompt(source: str, detail_level: str, include_examples: bool) -> str:
    """Build prompt for README generation."""
    parts = [
        "Generate a README.md file for this code.",
        "",
        "Include these sections:",
        "- Title and brief description",
        "- Installation instructions",
        "- Quick start guide",
    ]

    if detail_level in ("standard", "comprehensive"):
        parts.append("- API reference")

    if detail_level == "comprehensive":
        parts.extend([
            "- Configuration options",
            "- Contributing guidelines",
        ])

    if include_examples:
        parts.append("- Usage examples")

    parts.extend([
        "",
        "Code:",
        source[:8000],
        "",
        "Output only the README content in Markdown format.",
    ])

    return "\n".join(parts)


def _build_api_prompt(source: str, style: str, detail_level: str) -> str:
    """Build prompt for API documentation generation."""
    return f"""Generate API documentation for this code.

Style: {style}
Detail level: {detail_level}

Include:
- All public functions, classes, and methods
- Parameters with types and descriptions
- Return values
- Exceptions that may be raised

Code:
{source[:8000]}

Output the documentation in Markdown format."""


def _build_changelog_prompt(source: str) -> str:
    """Build prompt for changelog generation."""
    return f"""Generate a changelog entry based on this code diff or description.

Follow the Keep a Changelog format:
- Added: new features
- Changed: changes in existing functionality
- Deprecated: soon-to-be removed features
- Removed: removed features
- Fixed: bug fixes
- Security: vulnerability fixes

Input:
{source[:8000]}

Output only the changelog entry in Markdown format."""


def generate_docstring(
    code: str,
    style: Literal["google", "numpy", "sphinx", "markdown"] = "google",
    include_examples: bool = True,
) -> dict:
    """
    Generate a docstring for code.

    Args:
        code: Function or class code
        style: Docstring style
        include_examples: Include usage example

    Returns:
        dict with generated docstring
    """
    return generate_docs(
        code,
        doc_type="docstring",
        style=style,
        include_examples=include_examples,
    )


def generate_readme(
    code: str,
    detail_level: Literal["minimal", "standard", "comprehensive"] = "standard",
) -> dict:
    """
    Generate a README for code.

    Args:
        code: Project code
        detail_level: Level of detail

    Returns:
        dict with generated README
    """
    return generate_docs(
        code,
        doc_type="readme",
        detail_level=detail_level,
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m tools.documentation 'code or file path' [doc_type]")
        sys.exit(1)

    source = sys.argv[1]
    doc_type = sys.argv[2] if len(sys.argv) > 2 else "docstring"

    print(f"Generating {doc_type}...")

    result = generate_docs(source, doc_type=doc_type)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    else:
        print(result["documentation"])
