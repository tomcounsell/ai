"""
Response summarization for Telegram delivery.

Long agent responses are summarized via a fast LLM call (Haiku)
before sending to Telegram. Key artifacts (commit hashes, URLs,
file paths) are extracted and preserved in the summary.

For very long responses, the full output is also saved as a .txt
file for attachment.
"""

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

# Thresholds
SUMMARIZE_THRESHOLD = 1500  # Summarize responses longer than this
FILE_ATTACH_THRESHOLD = 3000  # Attach full output as file above this
MAX_SUMMARY_CHARS = 1200  # Target max length for summary
SAFETY_TRUNCATE = 4096  # Telegram hard limit


@dataclass
class SummarizedResponse:
    """Result of summarizing an agent response."""

    text: str
    full_output_file: Path | None = None
    was_summarized: bool = False
    artifacts: dict[str, list[str]] = field(default_factory=dict)


def extract_artifacts(text: str) -> dict[str, list[str]]:
    """
    Extract key artifacts from agent output.

    Pulls out commit hashes, URLs, changed files, test results,
    and error indicators so they can be preserved in summaries.
    """
    artifacts: dict[str, list[str]] = {}

    # Git commit hashes (7-40 hex chars preceded by common keywords)
    commit_pat = r'(?:commit|pushed|merged|created)\s+([a-f0-9]{7,40})'
    commits = re.findall(commit_pat, text, re.IGNORECASE)
    # Also match standalone short hashes in common git output patterns
    commits += re.findall(r'\b([a-f0-9]{7,12})\b(?=\s)', text)
    if commits:
        artifacts["commits"] = list(dict.fromkeys(commits))  # dedupe preserving order

    # URLs (http/https)
    urls = re.findall(r'https?://[^\s\)>\]"\']+', text)
    if urls:
        artifacts["urls"] = list(dict.fromkeys(urls))

    # Files changed (common git diff output patterns)
    file_pat = r'(?:modified|created|deleted|renamed|changed):\s*(.+?)(?:\n|$)'
    files_changed = re.findall(file_pat, text, re.IGNORECASE)
    files_changed += re.findall(r'^\s*[MADR]\s+(\S+)', text, re.MULTILINE)
    if files_changed:
        artifacts["files_changed"] = list(dict.fromkeys(f.strip() for f in files_changed))

    # Test results
    test_pat = r'(\d+\s+passed(?:,\s*\d+\s+(?:failed|error|warning|skipped))*)'
    test_matches = re.findall(test_pat, text, re.IGNORECASE)
    if test_matches:
        artifacts["test_results"] = test_matches

    # Error indicators
    errors = re.findall(r'(?:error|exception|failed|failure):\s*(.+?)(?:\n|$)', text, re.IGNORECASE)
    if errors:
        artifacts["errors"] = errors[:5]  # Cap at 5

    return artifacts


def _build_summary_prompt(text: str, artifacts: dict[str, list[str]]) -> str:
    """Build the prompt for Haiku summarization."""
    artifact_section = ""
    if artifacts:
        parts = []
        for key, values in artifacts.items():
            parts.append(f"- {key}: {', '.join(values[:10])}")
        artifact_section = (
            "\n\nIMPORTANT — These artifacts MUST appear verbatim in your summary:\n"
            + "\n".join(parts)
        )

    return f"""Summarize this AI agent's work output into a concise Telegram message.

Rules:
- Maximum {MAX_SUMMARY_CHARS} characters
- Preserve ALL commit hashes, URLs, and error messages exactly as-is
- Use short, direct sentences — no filler words
- Use markdown formatting (bold for emphasis, code blocks for hashes/paths)
- Start with what was done, then key details
- If there were errors or failures, lead with those
- Do NOT include meta-commentary about summarizing{artifact_section}

Agent output to summarize:
{text}"""


def _write_full_output_file(text: str) -> Path:
    """Write full agent output to a temp file for attachment."""
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="valor_full_output_")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return Path(path)


async def summarize_response(raw_response: str) -> SummarizedResponse:
    """
    Summarize an agent response for Telegram delivery.

    - Responses <= SUMMARIZE_THRESHOLD chars: returned as-is
    - Longer responses: summarized via Haiku with artifact preservation
    - Very long responses (> FILE_ATTACH_THRESHOLD): full output attached as file

    Falls back to safety truncation if summarization fails.
    """
    if not raw_response or len(raw_response) <= SUMMARIZE_THRESHOLD:
        return SummarizedResponse(text=raw_response or "", was_summarized=False)

    artifacts = extract_artifacts(raw_response)

    # Write full output file for very long responses
    full_output_file = None
    if len(raw_response) > FILE_ATTACH_THRESHOLD:
        try:
            full_output_file = _write_full_output_file(raw_response)
        except Exception as e:
            logger.warning(f"Failed to write full output file: {e}")

    # Attempt LLM summarization
    try:
        client = anthropic.AsyncAnthropic()
        prompt = _build_summary_prompt(raw_response, artifacts)

        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        summary_text = response.content[0].text

        # Safety check: if summary is somehow longer than original, use original
        if len(summary_text) >= len(raw_response):
            logger.warning("Summary longer than original, using truncated original")
            if len(raw_response) > SAFETY_TRUNCATE:
                summary_text = raw_response[:SAFETY_TRUNCATE - 3] + "..."
            else:
                summary_text = raw_response

        return SummarizedResponse(
            text=summary_text,
            full_output_file=full_output_file,
            was_summarized=True,
            artifacts=artifacts,
        )

    except Exception as e:
        logger.error(f"Summarization failed, falling back to truncation: {e}")
        # Fallback: truncate with ellipsis
        truncated = raw_response
        if len(truncated) > SAFETY_TRUNCATE:
            truncated = truncated[:SAFETY_TRUNCATE - 3] + "..."

        return SummarizedResponse(
            text=truncated,
            full_output_file=full_output_file,
            was_summarized=False,
            artifacts=artifacts,
        )
