"""
Response summarization for Telegram delivery.

Long agent responses are summarized into concise PM-facing messages
using Haiku (primary) or local Ollama (fallback). Key artifacts
(commit hashes, URLs, PRs) are extracted and preserved.

For very long responses, the full output is saved as a .txt file
for attachment.

Output classification determines whether agent output needs human
input (question/blocker) or can auto-continue (status/completion).
"""

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import anthropic
import ollama as ollama_pkg

from config.models import MODEL_FAST

logger = logging.getLogger(__name__)

# Thresholds
SUMMARIZE_THRESHOLD = 500  # Anything longer gets summarized
FILE_ATTACH_THRESHOLD = 3000  # Attach full output as file above this
SAFETY_TRUNCATE = 4096  # Telegram hard limit

# Ollama config — model can be overridden via env var
OLLAMA_MODEL = os.environ.get("OLLAMA_SUMMARIZER_MODEL", "qwen3:4b")

# Classification confidence threshold — below this, default to QUESTION
# (conservative: pauses for human input rather than auto-continuing)
CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.80


class OutputType(Enum):
    """Classification of agent output for routing decisions.

    Used by the bridge to determine whether to pause for human input
    or auto-continue the agent session.
    """

    QUESTION = "question"  # Needs human input
    STATUS_UPDATE = "status"  # Progress report, no input needed
    COMPLETION = "completion"  # Work finished
    BLOCKER = "blocker"  # Stuck, needs help
    ERROR = "error"  # Something failed


@dataclass
class ClassificationResult:
    """Result of classifying agent output.

    Attributes:
        output_type: The classified type of the output.
        confidence: How confident the classifier is (0.0-1.0).
        reason: Brief explanation of the classification decision.
    """

    output_type: OutputType
    confidence: float  # 0.0-1.0
    reason: str  # Brief explanation


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


CLASSIFIER_SYSTEM_PROMPT = """\
You classify developer agent output to determine if human input is needed.

Respond with ONLY a JSON object — no markdown, no explanation, no extra text:
{"type": "question|status|completion|blocker|error", \
"confidence": 0.95, "reason": "brief explanation"}

Classification rules:

QUESTION — The agent is directly asking the human for a decision or input.
  Examples: "Should I proceed?", "Which approach do you prefer?", "Do you want me to...?"
  Key signals: direct question marks aimed at the user, "should I", "would you like", "which do you"

  NOT a question (classify as STATUS_UPDATE instead):
  - Rhetorical questions in status reports ("What could cause this? Let me investigate...")
  - "Should I fix this?" when it's obviously a bug the agent should fix
  - Questions about implementation details the agent should decide itself
  - Asking permission for things clearly within the agent's authority
  - Self-directed questions like "Let me check if..." or "I wonder if..."

STATUS_UPDATE — Progress report with no question. The agent is still working.
  Examples: "Running tests...", "Found 3 issues, fixing now", "Analyzing the codebase"
  Key signals: present tense activity, no question directed at human, intermediate progress

COMPLETION — The work is done AND evidence is provided.
  REQUIRES at least one of:
  - Command output showing test results (N passed, 0 failed)
  - Specific numbers (test counts, error counts, line counts)
  - Command exit codes or verification output
  - File paths confirmed to exist
  - PR/commit URLs with passing status
  Examples: "All 42 tests passed, committed abc1234", \
"PR created: https://... — CI green", "ruff check: 0 errors, black: reformatted 3 files"
  Key signals: specific numbers, command output pasted, exit codes mentioned

  NOT completion (classify as STATUS_UPDATE instead):
  - "Done" or "Complete" without any evidence
  - "Should work now" (hedging = not verified)
  - "Committed and pushed" without test results
  - Hedging language: "should", "probably", "seems to", "looks like", "I think", "I believe"
  - Claims without proof: "Fixed the bug" without reproduction test output

BLOCKER — The agent is stuck and needs human help to proceed.
  Examples: "I don't have access to...", "This requires permissions I don't have", "Blocked on..."
  Key signals: inability to proceed, missing access/permissions, explicit "blocked"

ERROR — Something failed or broke.
  Examples: "Error: ModuleNotFoundError", "Build failed with exit code 1", "Tests failing: 3 errors"
  Key signals: "error:", "failed:", exception names, non-zero exit codes"""

# False question detection explained:
# Many agent outputs contain question-like text that should NOT pause for human input:
# 1. Rhetorical questions - the agent is thinking aloud, not asking the human
# 2. Obvious bugs - "Should I fix this obvious bug?" is not a real question
# 3. Implementation details - the agent should make these decisions autonomously
# 4. Permission-seeking for routine tasks - the agent has authority to proceed
# Misclassifying these as QUESTION causes premature stopping and unnecessary pauses.


def _classify_with_heuristics(text: str) -> ClassificationResult:
    """Fallback keyword-based classification when LLM is unavailable.

    Uses pattern matching to make a best-effort classification.
    Conservative: defaults to QUESTION when uncertain, so the bridge
    pauses for human review rather than auto-continuing incorrectly.
    """
    text_lower = text.lower().strip()

    # Check for direct questions aimed at the user
    question_patterns = [
        r"\bshould i\b.*\?",
        r"\bdo you want\b.*\?",
        r"\bwould you like\b.*\?",
        r"\bwhich\b.*\bdo you prefer\b",
        r"\bwhich\b.*\bshould\b.*\?",
        r"\bwhat\b.*\bshould\b.*\?",
        r"\bcan you\b.*\?",
        r"\bplease\s+(?:confirm|choose|decide|let me know)\b",
        r"\bwhat do you think\b",
        r"\bhow would you like\b",
    ]
    for pattern in question_patterns:
        if re.search(pattern, text_lower):
            return ClassificationResult(
                output_type=OutputType.QUESTION,
                confidence=0.85,
                reason="Detected direct question pattern",
            )

    # Check for error indicators
    error_patterns = [
        r"\berror:\s",
        r"\bfailed:\s",
        r"\bexception:\s",
        r"\btraceback\b.*\bcall\b",
        r"\bexit code [1-9]",
        r"\bfailed with\b",
        r"\bcrash\b",
        r"\bpanic\b",
    ]
    for pattern in error_patterns:
        if re.search(pattern, text_lower):
            return ClassificationResult(
                output_type=OutputType.ERROR,
                confidence=0.85,
                reason="Detected error/failure pattern",
            )

    # Check for blocker indicators
    blocker_patterns = [
        r"\bblocked\b",
        r"\bblocking\b",
        r"\bdon'?t have access\b",
        r"\bpermission denied\b",
        r"\bcannot proceed\b",
        r"\bunable to continue\b",
        r"\bneed.{0,20}permission\b",
    ]
    for pattern in blocker_patterns:
        if re.search(pattern, text_lower):
            return ClassificationResult(
                output_type=OutputType.BLOCKER,
                confidence=0.80,
                reason="Detected blocker/access pattern",
            )

    # Check for completion indicators
    completion_patterns = [
        r"\bdone\b",
        r"\bcomplete[d]?\b",
        r"\bfinished\b",
        r"\bpushed\b.*\b(?:to|origin|main|master)\b",
        r"\bcommitted\b",
        r"\bmerged\b",
        r"\bpr created\b",
        r"\bpull request\b.*\bcreated\b",
        r"https?://github\.com/.+/pull/\d+",
    ]
    for pattern in completion_patterns:
        if re.search(pattern, text_lower):
            return ClassificationResult(
                output_type=OutputType.COMPLETION,
                confidence=0.80,
                reason="Detected completion pattern",
            )

    # Default: STATUS_UPDATE (conservative for auto-continue)
    return ClassificationResult(
        output_type=OutputType.STATUS_UPDATE,
        confidence=0.60,
        reason="No strong signal detected, defaulting to status update",
    )


async def classify_output(text: str) -> ClassificationResult:
    """Classify agent output to determine if human input is needed.

    Uses Haiku (MODEL_FAST) for intelligent classification with a
    keyword heuristic fallback. If the LLM confidence is below
    CLASSIFICATION_CONFIDENCE_THRESHOLD, defaults to QUESTION to
    conservatively pause for human review.

    Args:
        text: The agent output text to classify.

    Returns:
        ClassificationResult with the output type, confidence, and reason.
    """
    if not text or not text.strip():
        return ClassificationResult(
            output_type=OutputType.STATUS_UPDATE,
            confidence=1.0,
            reason="Empty output",
        )

    # Try LLM-based classification first
    try:
        from utils.api_keys import get_anthropic_api_key

        api_key = get_anthropic_api_key()
        if not api_key:
            logger.warning("No API key for classification, using heuristics")
            return _classify_with_heuristics(text)

        client = anthropic.AsyncAnthropic(api_key=api_key)

        # Truncate very long text to save tokens — classification
        # only needs the beginning and end of the output
        classify_text = text
        if len(text) > 2000:
            classify_text = text[:1000] + "\n\n[...truncated...]\n\n" + text[-1000:]

        response = await client.messages.create(
            model=MODEL_FAST,
            max_tokens=256,
            system=CLASSIFIER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": classify_text}],
        )

        raw_response = response.content[0].text.strip()
        result = _parse_classification_response(raw_response)
        if result is not None:
            # If confidence is below threshold, default to QUESTION
            if result.confidence < CLASSIFICATION_CONFIDENCE_THRESHOLD:
                logger.info(
                    f"Classification confidence {result.confidence:.2f} below "
                    f"threshold {CLASSIFICATION_CONFIDENCE_THRESHOLD}, "
                    f"defaulting to QUESTION"
                )
                return ClassificationResult(
                    output_type=OutputType.QUESTION,
                    confidence=result.confidence,
                    reason=f"Low confidence ({result.confidence:.2f}): {result.reason}",
                )
            return result

        # LLM returned unparseable response — fall through to heuristics
        logger.warning(f"Could not parse classification response: {raw_response[:200]}")

    except Exception as e:
        logger.warning(f"LLM classification failed: {e}")

    # Fallback to heuristic classification
    return _classify_with_heuristics(text)


def _parse_classification_response(raw: str) -> ClassificationResult | None:
    """Parse the LLM's JSON classification response.

    Handles common issues like markdown code fences around JSON,
    extra whitespace, and invalid type values.

    Returns None if the response cannot be parsed.
    """
    # Strip markdown code fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # Remove opening fence (with optional language tag)
        cleaned = re.sub(r"^```\w*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None

    # Validate required fields
    if not isinstance(data, dict):
        return None

    type_str = data.get("type", "")
    confidence = data.get("confidence", 0.0)
    reason = data.get("reason", "")

    # Map type string to OutputType enum
    type_map = {
        "question": OutputType.QUESTION,
        "status": OutputType.STATUS_UPDATE,
        "completion": OutputType.COMPLETION,
        "blocker": OutputType.BLOCKER,
        "error": OutputType.ERROR,
    }

    output_type = type_map.get(type_str)
    if output_type is None:
        return None

    # Clamp confidence to valid range
    try:
        confidence = float(confidence)
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    return ClassificationResult(
        output_type=output_type,
        confidence=confidence,
        reason=str(reason),
    )


def _build_summary_prompt(text: str, artifacts: dict[str, list[str]]) -> str:
    """Build the summarization prompt.

    The system prompt handles format rules. This just provides the content
    and any extracted artifacts that must be preserved.
    """
    artifact_section = ""
    if artifacts:
        parts = []
        for key, values in artifacts.items():
            parts.append(f"- {key}: {', '.join(values[:10])}")
        artifact_section = (
            "\n\nPreserve these artifacts verbatim:\n" + "\n".join(parts) + "\n"
        )

    return f"""/no_think
Summarize this developer session output:{artifact_section}

{text}"""


def _write_full_output_file(text: str) -> Path:
    """Write full agent output to a temp file for attachment."""
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="valor_full_output_")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return Path(path)


SUMMARIZER_SYSTEM_PROMPT = """\
You condense a developer's messages into Telegram-length updates for a project manager.

Input: ANY output from an autonomous developer — work summaries, conversational replies, \
design discussions, Q&A answers, status updates, or technical analysis. May include \
terminal output, commit messages, file diffs, opinions, or free-form notes.

CRITICAL: Never reject, editorialize, or add meta-commentary about the input. \
Your job is to condense, not to judge whether the content is "valid". \
If the input is a conversational reply or opinion, condense it faithfully.

Output rules:
- For simple completions: respond with just "Done ✅" or "Yes" or "No"
- For conversational replies: condense while preserving tone and key points
- For questions directed at the PM: preserve the question exactly (never rewrite or drop questions)
- For work needing context: 2-4 sentences max
- Lead with the outcome, not the process
- Preserve commit hashes and URLs inline (e.g., `abc1234`, https://github.com/org/repo/pull/42)
- Flag with ⚠️ ONLY for genuinely external blockers (missing credentials, need third-party \
access, policy decisions). Do NOT flag: implementation choices, internal obstacles, things \
the agent could resolve with its tools
- Tone: direct, no preamble, no filler"""

# Blocker flag logic explained:
# The ⚠️ flag is meant to alert the PM only when human intervention is truly required.
# Genuine blockers: missing API keys, need admin access to a service, policy/legal decisions,
# waiting on external team, need credentials the agent cannot obtain.
# NOT blockers: code bugs (agent can fix), test failures (agent can debug), implementation
# decisions (agent should decide), finding the right approach (agent's job).


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
            system=SUMMARIZER_SYSTEM_PROMPT,
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
