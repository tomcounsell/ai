"""
Response summarization for Telegram delivery.

Long agent responses are summarized into concise PM-facing messages
using Haiku (primary) or local Ollama (fallback). Key artifacts
(commit hashes, URLs, PRs) are extracted and preserved.

For very long responses, the full output is saved as a .txt file
for attachment.
"""

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
import ollama as ollama_pkg

from config.models import MODEL_FAST

logger = logging.getLogger(__name__)

# Thresholds
SUMMARIZE_THRESHOLD = 500  # ~3 sentences; anything longer gets summarized
FILE_ATTACH_THRESHOLD = 3000  # Attach full output as file above this
MAX_SUMMARY_CHARS = 400  # Target: 3 concise sentences + links
SAFETY_TRUNCATE = 4096  # Telegram hard limit

# Ollama config — model can be overridden via env var
OLLAMA_MODEL = os.environ.get("OLLAMA_SUMMARIZER_MODEL", "qwen3:4b")


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
    commit_pat = r"(?:commit|pushed|merged|created)\s+([a-f0-9]{7,40})"
    commits = re.findall(commit_pat, text, re.IGNORECASE)
    # Also match standalone short hashes in common git output patterns
    commits += re.findall(r"\b([a-f0-9]{7,12})\b(?=\s)", text)
    if commits:
        # dedupe preserving order
        artifacts["commits"] = list(dict.fromkeys(commits))

    # URLs (http/https)
    urls = re.findall(r'https?://[^\s\)>\]"\']+', text)
    if urls:
        artifacts["urls"] = list(dict.fromkeys(urls))

    # Files changed (common git diff output patterns)
    file_pat = r"(?:modified|created|deleted|renamed|changed):\s*" r"(.+?)(?:\n|$)"
    files_changed = re.findall(file_pat, text, re.IGNORECASE)
    files_changed += re.findall(r"^\s*[MADR]\s+(\S+)", text, re.MULTILINE)
    if files_changed:
        artifacts["files_changed"] = list(
            dict.fromkeys(f.strip() for f in files_changed)
        )

    # Test results
    test_pat = r"(\d+\s+passed" r"(?:,\s*\d+\s+(?:failed|error|warning|skipped))*)"
    test_matches = re.findall(test_pat, text, re.IGNORECASE)
    if test_matches:
        artifacts["test_results"] = test_matches

    # Error indicators
    errors = re.findall(
        r"(?:error|exception|failed|failure):\s*(.+?)(?:\n|$)",
        text,
        re.IGNORECASE,
    )
    if errors:
        artifacts["errors"] = errors[:5]  # Cap at 5

    return artifacts


def _build_summary_prompt(text: str, artifacts: dict[str, list[str]]) -> str:
    """Build the summarization prompt.

    Creates a concise status update from agent output.
    No roleplay framing - just straightforward summarization.
    """
    artifact_section = ""
    if artifacts:
        parts = []
        for key, values in artifacts.items():
            parts.append(f"- {key}: {', '.join(values[:10])}")
        artifact_section = "\n\nThese artifacts MUST appear verbatim:\n" + "\n".join(
            parts
        )

    return f"""/no_think
Summarize this AI agent output into a brief status update for \
delivery via Telegram.

Rules:
- Maximum {MAX_SUMMARY_CHARS} characters total
- Write 1-3 short sentences: what was done, outcome, any blockers
- Include commit hashes and URLs so they can be clicked
- If tests failed or errors occurred, lead with that
- No play-by-play of steps taken, files read, or tools used
- No preamble, no sign-off
- Tone: direct, professional, like a Slack status update
- Preserve the voice and perspective of the original text{artifact_section}

Examples of good output:
- "Fixed the payment webhook race condition. Tests passing. \
`abc1234` https://github.com/org/repo/pull/42"
- "Analyzed the image and generated a new proposal. Saved to \
generated_images/new_photo.jpg"
- "Investigated the auth timeout — root cause is session expiry. \
Fix ready but need your call on TTL (currently 24h)."

Agent output to summarize:
{text}"""


def _write_full_output_file(text: str) -> Path:
    """Write full agent output to a temp file for attachment."""
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="valor_full_output_")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return Path(path)


async def _summarize_with_haiku(prompt: str) -> str | None:
    """Try summarization via Anthropic Haiku API."""
    try:
        from utils.api_keys import get_anthropic_api_key

        api_key = get_anthropic_api_key()
        if not api_key:
            logger.warning("No Anthropic API key found for summarization")
            return None
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=MODEL_FAST,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        logger.warning(f"Haiku summarization failed: {e}")
        return None


async def _summarize_with_ollama(prompt: str) -> str | None:
    """Fallback: summarize via local Ollama model."""
    try:
        client = ollama_pkg.AsyncClient()
        response = await client.generate(
            model=OLLAMA_MODEL,
            prompt=prompt,
            options={"num_predict": 512},
        )
        return response.get("response", "").strip() or None
    except Exception as e:
        logger.warning(f"Ollama summarization failed: {e}")
        return None


async def summarize_response(
    raw_response: str,
) -> SummarizedResponse:
    """
    Summarize an agent response for Telegram delivery.

    - Responses <= SUMMARIZE_THRESHOLD chars: returned as-is
    - Longer responses: summarized via Haiku, then Ollama fallback
    - Very long responses (> FILE_ATTACH_THRESHOLD): full output
      attached as file

    Falls back to safety truncation if all summarization fails.
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

    # Build prompt once, try multiple backends
    prompt = _build_summary_prompt(raw_response, artifacts)

    # Try Haiku first, then Ollama
    summary_text = await _summarize_with_haiku(prompt)
    if summary_text is None:
        logger.info("Falling back to Ollama for summarization")
        summary_text = await _summarize_with_ollama(prompt)

    if summary_text is not None:
        # Safety: if summary is somehow longer than original, truncate
        if len(summary_text) >= len(raw_response):
            logger.warning("Summary longer than original, using truncated original")
            if len(raw_response) > SAFETY_TRUNCATE:
                summary_text = raw_response[: SAFETY_TRUNCATE - 3] + "..."
            else:
                summary_text = raw_response

        return SummarizedResponse(
            text=summary_text,
            full_output_file=full_output_file,
            was_summarized=True,
            artifacts=artifacts,
        )

    # All backends failed — truncate as last resort
    logger.error("All summarization backends failed, truncating")
    truncated = raw_response
    if len(truncated) > SAFETY_TRUNCATE:
        truncated = truncated[: SAFETY_TRUNCATE - 3] + "..."

    return SummarizedResponse(
        text=truncated,
        full_output_file=full_output_file,
        was_summarized=False,
        artifacts=artifacts,
    )
