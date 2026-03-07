"""
Response summarization for Telegram delivery.

Long agent responses are summarized into concise PM-facing messages
using Haiku (primary) or OpenRouter (fallback). Key artifacts
(commit hashes, URLs, PRs) are extracted and preserved.

For very long responses, the full output is saved as a .txt file
for attachment.

Output classification determines whether agent output needs human
input (question/blocker) or can auto-continue (status/completion).

Anti-fabrication rule: The summarizer must NEVER fabricate questions
that are not verbatim present in the raw agent output. Only explicit
questions (sentences ending in "?" directed at the human) may appear
in the "?" section or set the expectations field. Declarative statements
like "I will do X" must never be reframed as questions.
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
import httpx

from config.models import MODEL_FAST, OPENROUTER_HAIKU

logger = logging.getLogger(__name__)

# Thresholds
FILE_ATTACH_THRESHOLD = 3000  # Attach full output as file above this
SAFETY_TRUNCATE = 4096  # Telegram hard limit

# OpenRouter config
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Classification confidence threshold — below this, default to QUESTION
# (conservative: pauses for human input rather than auto-continuing)
CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.80

# Process narration patterns — stripped before summarization
_PROCESS_NARRATION_PATTERNS = [
    re.compile(r"^Let me (check|look|read|examine|review|investigate|search|explore)"),
    re.compile(r"^Now let me (check|look|read|examine|review|investigate|search)"),
    re.compile(r"^I'll (start|begin|proceed|continue|check|look|read|examine) "),
    re.compile(r"^First,? (?:let me|I'll) (check|look|read|examine|review)"),
    re.compile(r"^Good\.$"),
    re.compile(r"^Now I (need to |will |'ll )?(check|look|read|examine|review)"),
    re.compile(r"^Looking at (the |this |that )"),
    re.compile(r"^Alright,? (let me|I'll)"),
    re.compile(r"^Sure,? (let me|I'll)"),
    re.compile(r"^OK,? (let me|I'll)"),
]


def _extract_open_questions(text: str) -> list[str]:
    """Extract questions from '## Open Questions' sections in agent output.

    Scans the text for a markdown '## Open Questions' heading and extracts
    substantive question items from the content below it. Returns an empty
    list if no section is found, the section is empty, or it contains only
    placeholder text.

    Only numbered/bulleted list items with substantive text are treated as
    questions. The heading itself is the signal -- items under it are questions
    regardless of punctuation (per plan design decision).

    Args:
        text: Raw agent output text to scan.

    Returns:
        List of verbatim question strings, or empty list if none found.
    """
    if not text:
        return []

    # Find the ## Open Questions section
    # Match "## Open Questions" with optional trailing text (e.g., "(Resolved)")
    pattern = r"^## Open Questions.*$"
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        return []

    # Extract content after the heading until the next ## heading or end of text
    section_start = match.end()
    next_heading = re.search(r"^## ", text[section_start:], re.MULTILINE)
    if next_heading:
        section_content = text[section_start : section_start + next_heading.start()]
    else:
        section_content = text[section_start:]

    # Extract list items (numbered or bulleted)
    questions = []
    # Match lines starting with number+period, dash, asterisk, or bullet
    list_item_pattern = re.compile(r"^\s*(?:\d+[\.\)]\s*|[-*+]\s*|\u2022\s*)(.*)", re.MULTILINE)
    for item_match in list_item_pattern.finditer(section_content):
        item_text = item_match.group(1).strip()
        # Skip empty, whitespace-only, or placeholder items
        if not item_text:
            continue
        # Skip obvious placeholders
        placeholder_patterns = [
            r"^TBD\.?$",
            r"^TODO\.?$",
            r"^N/?A\.?$",
            r"^None\.?$",
            r"^\?+$",
            r"^\.+$",
        ]
        if any(re.match(p, item_text, re.IGNORECASE) for p in placeholder_patterns):
            continue
        questions.append(item_text)

    return questions


def _strip_process_narration(text: str) -> str:
    """Strip process narration lines from agent output before summarization.

    Removes lines like "Let me check...", "Now let me read..." that are
    process noise, not meaningful content. Only strips if meaningful content
    remains after filtering.
    """
    lines = text.split("\n")
    filtered = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            filtered.append(line)
            continue
        is_narration = any(p.match(stripped) for p in _PROCESS_NARRATION_PATTERNS)
        if not is_narration:
            filtered.append(line)

    result = "\n".join(filtered).strip()
    # Don't return empty string if everything was stripped
    return result if result else text


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
    was_rejected_completion: bool = False  # True when COMPLETION → STATUS_UPDATE downgrade
    coaching_message: str | None = None  # LLM-generated coaching for rejected completions
    has_workarounds: bool = False  # True when agent encountered problems it worked around


@dataclass
class StructuredSummary:
    """Structured output from the summarizer's tool_use call.

    Contains the three routing fields produced by the structured_summary tool:
    - context_summary: one-sentence session topic for routing
    - response: the Telegram-formatted message text
    - expectations: what the agent needs from the human, or None
    """

    context_summary: str
    response: str
    expectations: str | None


# Tool schema for structured summarizer output via tool_use
STRUCTURED_SUMMARY_TOOL = {
    "name": "structured_summary",
    "description": "Produce a structured summary of the developer session output.",
    "input_schema": {
        "type": "object",
        "properties": {
            "context_summary": {
                "type": "string",
                "description": (
                    "One sentence: what this session is about (for routing). "
                    "Be specific about the topic and scope, not vague."
                ),
            },
            "response": {
                "type": "string",
                "description": ("The Telegram message. Follow format rules from system prompt."),
            },
            "expectations": {
                "type": ["string", "null"],
                "description": (
                    "Set ONLY when the raw output contains an explicit question directed "
                    "at the human (a sentence ending in '?'). Copy it verbatim. "
                    "Null when no explicit questions exist — declarative plans are NOT questions."
                ),
            },
        },
        "required": ["context_summary", "response", "expectations"],
    },
}


@dataclass
class SummarizedResponse:
    """Result of summarizing an agent response."""

    text: str
    full_output_file: Path | None = None
    was_summarized: bool = False
    artifacts: dict[str, list[str]] = field(default_factory=dict)
    context_summary: str | None = None
    expectations: str | None = None


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
        artifacts["files_changed"] = list(dict.fromkeys(f.strip() for f in files_changed))

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
"confidence": 0.95, "reason": "brief explanation", "coaching_message": null, \
"has_workarounds": false}

The coaching_message field is REQUIRED in your response. Set it to a helpful string \
when you classify as "status" and the output LOOKS like a completion attempt but \
lacks evidence (i.e., you are downgrading a completion to status). The coaching \
message should explain what was missing and what the agent should include next time. \
Set it to null for all other classifications.

The has_workarounds field detects when the agent encountered problems, errors, or \
obstacles during work that it had to work around but didn't fully resolve. Set to true when:
- The agent mentions errors/failures it encountered and worked around
- Something broke or was unavailable and the agent found an alternative
- The agent explicitly mentions a workaround, fallback, or "skipped" step
- There are warnings or issues discovered that should be tracked as GitHub issues
Set to false when work completed cleanly without encountering problems.

Classification rules:

QUESTION — The agent is directly asking the human for a decision or input.
  Examples: "Should I proceed?", "Which approach do you prefer?", "Do you want me to...?"
  Key signals: direct question marks aimed at the user, "should I", "would you like", "which do you"
  IMPORTANT: Messages seeking permission or approval are QUESTION, not STATUS_UPDATE.
  Examples of approval gates (classify as QUESTION):
  - "Ready to build when approved"
  - "Waiting for your go-ahead"
  - "Shall I proceed with the implementation?"
  - "Awaiting approval before merging"

  NOT a question (classify as STATUS_UPDATE instead):
  - Rhetorical questions in status reports ("What could cause this? Let me investigate...")
  - "Should I fix this?" when it's obviously a bug the agent should fix
  - Questions about implementation details the agent should decide itself
  - Asking permission for things clearly within the agent's authority
  - Self-directed questions like "Let me check if..." or "I wonder if..."

STATUS_UPDATE — Progress report with no question. The agent is still working.
  Examples: "Running tests...", "Found 3 issues, fixing now", "Analyzing the codebase"
  Key signals: present tense activity, no question directed at human, intermediate progress

COMPLETION — The work is done AND evidence is provided, OR the user's question has been answered.
  Two paths to COMPLETION:

  Path A — SDLC/work completion (evidence required):
  REQUIRES at least one of:
  - Command output showing test results (N passed, 0 failed)
  - Specific numbers (test counts, error counts, line counts)
  - Command exit codes or verification output
  - File paths confirmed to exist
  - PR/commit URLs with passing status
  Examples: "All 42 tests passed, committed abc1234", \
"PR created: https://... — CI green", "ruff check: 0 errors, black: reformatted 3 files"
  Key signals: specific numbers, command output pasted, exit codes mentioned

  Path B — Conversational/Q&A completion (no evidence needed):
  When the user asked a question and the agent answered it with factual, substantive content.
  DOES NOT require test output, numbers, or URLs — the answer itself IS the deliverable.
  Examples: "The summarizer works by...", "Here's how the routing system handles...", \
"The bridge uses Telethon to...", "There are 3 main components: ..."
  Key signals: explanatory prose, factual descriptions, architecture explanations, \
direct answers to "how does X work?" or "what is X?" questions
  IMPORTANT: If the output explains a system, answers a question, or provides information \
the user asked for — that is COMPLETION, not STATUS_UPDATE. The user asked, the agent answered.

  NOT completion (classify as STATUS_UPDATE instead, and provide a coaching_message):
  - "Done" or "Complete" without any evidence (for work tasks)
  - "Should work now" (hedging = not verified)
  - "Committed and pushed" without test results
  - Hedging language: "should", "probably", "seems to", "looks like", "I think", "I believe"
  - Claims without proof: "Fixed the bug" without reproduction test output

BLOCKER — The agent is stuck and needs human help to proceed.
  Examples: "I don't have access to...", "This requires permissions I don't have", "Blocked on..."
  Key signals: inability to proceed, missing access/permissions, explicit "blocked"

ERROR — Something failed or broke.
  Examples: "Error: ModuleNotFoundError", "Build failed with exit code 1", "Tests failing: 3 errors"
  Key signals: "error:", "failed:", exception names, non-zero exit codes

Few-shot examples of coaching_message for downgraded completions:

Input: "I think the bug is fixed now. Should work."
Output: {"type": "status", "confidence": 0.92, "reason": "Hedging language without verification", \
"coaching_message": "You used hedging language ('I think', 'should work') which signals \
uncertainty. Run the reproduction steps or tests and share the actual output to confirm the fix."}

Input: "All tests pass. Task complete."
(but no test output shown)
Output: {"type": "status", "confidence": 0.90, "reason": "Claims tests pass but shows no output", \
"coaching_message": "You claimed tests pass but didn't include the test output. Run pytest and \
paste the results showing pass/fail counts so completion can be verified."}

Input: "Fixed the issue and committed. I believe everything is working correctly."
Output: {"type": "status", "confidence": 0.91, \
"reason": "Completion claim with hedging, no evidence", \
"coaching_message": "You said 'I believe everything is working' — \
that's hedging, not evidence. Show the commit hash, test output, \
and any verification commands you ran."}

Input: "I'll implement a fix for this by adding a check in the hook..."
Output: {"type": "status", "confidence": 0.93, \
"reason": "Agent plans to write code outside SDLC pipeline", \
"coaching_message": "Implementation work should go through /sdlc to ensure proper branch, \
testing, and review. Use /sdlc to create an issue and start the pipeline instead of \
writing code directly."}

Few-shot examples of Q&A completions (Path B — no evidence needed):

Input: "The summarizer works by classifying agent output into types (question, status, \
completion, blocker, error) using an LLM call. Status updates are auto-continued while \
completions and questions are delivered to Telegram. The structured format uses bullet \
points with stage progress lines for SDLC work."
Output: {"type": "completion", "confidence": 0.92, \
"reason": "Factual answer to user question about system architecture", \
"coaching_message": null, "has_workarounds": false}

Input: "There are two root causes: the classifier misclassifies Q&A answers as status \
updates because they lack evidence like test output, and the shared Claude Code session \
causes context to leak between concurrent conversations."
Output: {"type": "completion", "confidence": 0.93, \
"reason": "Direct analysis answering user's question about a bug", \
"coaching_message": null, "has_workarounds": false}

Input: "Let me investigate the logs to find out what happened..."
Output: {"type": "status", "confidence": 0.90, \
"reason": "Agent describing planned investigation, not yet answering", \
"coaching_message": null, "has_workarounds": false}"""

# False question detection explained:
# Many agent outputs contain question-like text that should NOT pause for human input:
# 1. Rhetorical questions - the agent is thinking aloud, not asking the human
# 2. Obvious bugs - "Should I fix this obvious bug?" is not a real question
# 3. Implementation details - the agent should make these decisions autonomously
# 4. Permission-seeking for routine tasks - the agent has authority to proceed
# Misclassifying these as QUESTION causes premature stopping and unnecessary pauses.


def _detect_workarounds(text_lower: str) -> bool:
    """Detect if the agent encountered problems it worked around."""
    workaround_patterns = [
        r"\bwork(?:ed)?\s*around\b",
        r"\bworkaround\b",
        r"\bfallback\b",
        r"\bskipp(?:ed|ing)\b.*\b(?:step|phase|sync|check)\b",
        r"\bunavailable\b",
        r"\bcould not fetch\b",
        r"\bfailed.*\busing\b.*\binstead\b",
        r"\bhad to\b.*\b(?:instead|alternative|manually)\b",
        r"\b(?:warning|warn)\b.*\b(?:found|discovered|detected)\b",
    ]
    return any(re.search(p, text_lower) for p in workaround_patterns)


def _classify_with_heuristics(text: str) -> ClassificationResult:
    """Fallback keyword-based classification when LLM is unavailable.

    Uses pattern matching to make a best-effort classification.
    Conservative: defaults to QUESTION when uncertain, so the bridge
    pauses for human review rather than auto-continuing incorrectly.
    """
    text_lower = text.lower().strip()

    # Check for approval gate patterns (permission-seeking language)
    approval_patterns = [
        r"\bwhen approved\b",
        r"\bready to build\b.*\bapproved\b",
        r"\bwaiting for.*\bgo-ahead\b",
        r"\blet me know when\b",
        r"\bshall i proceed\b",
        r"\bawaiting.*\bapproval\b",
        r"\bready to (?:proceed|start|begin)\b.*\bapproved\b",
        r"\bwaiting for your\b.*\b(?:approval|confirmation|go-ahead)\b",
    ]
    for pattern in approval_patterns:
        if re.search(pattern, text_lower):
            return ClassificationResult(
                output_type=OutputType.QUESTION,
                confidence=0.85,
                reason="Detected approval gate pattern — agent seeking permission",
            )

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
                has_workarounds=_detect_workarounds(text_lower),
            )

    # Default: QUESTION (conservative — show to user rather than silently auto-continue)
    # When no pattern matches, it's safer to pause for human review than to
    # auto-continue what might be a question the heuristics didn't catch.
    # Confidence is set at the threshold (0.80) so the confidence gate in
    # classify_output() passes this through without redundant re-conversion.
    result = ClassificationResult(
        output_type=OutputType.QUESTION,
        confidence=CLASSIFICATION_CONFIDENCE_THRESHOLD,
        reason="No strong signal detected — defaulting to show user",
    )
    result.has_workarounds = _detect_workarounds(text_lower)
    return result


def _apply_heuristic_confidence_gate(
    result: ClassificationResult,
) -> ClassificationResult:
    """Apply the same confidence threshold to heuristic results as the LLM path.

    When heuristic confidence is below CLASSIFICATION_CONFIDENCE_THRESHOLD,
    default to QUESTION (conservative). This closes the asymmetry where a
    heuristic result at 0.60 would be returned as STATUS_UPDATE, while an
    LLM result at 0.60 would become QUESTION.
    """
    if result.confidence < CLASSIFICATION_CONFIDENCE_THRESHOLD:
        logger.info(
            f"Heuristic confidence {result.confidence:.2f} below "
            f"threshold {CLASSIFICATION_CONFIDENCE_THRESHOLD}, "
            f"defaulting to QUESTION"
        )
        return ClassificationResult(
            output_type=OutputType.QUESTION,
            confidence=result.confidence,
            reason=f"Low heuristic confidence ({result.confidence:.2f}): {result.reason}",
        )
    return result


# Classification audit log — lightweight JSONL observability
_AUDIT_LOG_PATH = Path(__file__).parent.parent / "logs" / "classification_audit.jsonl"
_AUDIT_LOG_MAX_SIZE = 10 * 1024 * 1024  # 10 MB


def _write_classification_audit(text: str, result: ClassificationResult, source: str) -> None:
    """Append a JSONL entry to the classification audit log.

    Provides structured observability for every classify_output() call.
    Uses append mode, no locking needed (single writer). Rotates by
    renaming to .1 when file exceeds 10 MB.
    """
    try:
        from datetime import UTC, datetime

        _AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Size-based rotation
        if _AUDIT_LOG_PATH.exists() and _AUDIT_LOG_PATH.stat().st_size > _AUDIT_LOG_MAX_SIZE:
            rotated = _AUDIT_LOG_PATH.with_suffix(".jsonl.1")
            _AUDIT_LOG_PATH.rename(rotated)

        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "text_preview": text[:200] if text else "",
            "result": result.output_type.value,
            "confidence": round(result.confidence, 3),
            "reason": result.reason,
            "source": source,
        }
        with open(_AUDIT_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.debug(f"Classification audit log write failed (non-fatal): {e}")


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
        result = ClassificationResult(
            output_type=OutputType.STATUS_UPDATE,
            confidence=1.0,
            reason="Empty output",
        )
        _write_classification_audit(text or "", result, source="empty")
        return result

    # Try LLM-based classification first
    try:
        from utils.api_keys import get_anthropic_api_key

        api_key = get_anthropic_api_key()
        if not api_key:
            logger.warning("No API key for classification, using heuristics")
            result = _classify_with_heuristics(text)
            result = _apply_heuristic_confidence_gate(result)
            _write_classification_audit(text, result, source="heuristic")
            return result

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
                result = ClassificationResult(
                    output_type=OutputType.QUESTION,
                    confidence=result.confidence,
                    reason=f"Low confidence ({result.confidence:.2f}): {result.reason}",
                )
            _write_classification_audit(text, result, source="llm")
            return result

        # LLM returned unparseable response — fall through to heuristics
        logger.warning(f"Could not parse classification response: {raw_response[:200]}")

    except Exception as e:
        logger.warning(f"LLM classification failed: {e}")

    # Fallback to heuristic classification
    # Apply the same confidence threshold as the LLM path (Item 5)
    result = _classify_with_heuristics(text)
    result = _apply_heuristic_confidence_gate(result)
    _write_classification_audit(text, result, source="heuristic")
    return result


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

    # Extract optional coaching_message from LLM response
    coaching_message = data.get("coaching_message")
    if coaching_message is not None:
        coaching_message = str(coaching_message)
        # Treat JSON null / empty string as None
        if not coaching_message or coaching_message == "null":
            coaching_message = None

    # Extract has_workarounds flag
    has_workarounds = bool(data.get("has_workarounds", False))

    result = ClassificationResult(
        output_type=output_type,
        confidence=confidence,
        reason=str(reason),
        coaching_message=coaching_message,
        has_workarounds=has_workarounds,
    )

    # Detect rejected completions: LLM provided a coaching_message on a
    # STATUS_UPDATE, meaning it downgraded a completion attempt
    if result.output_type == OutputType.STATUS_UPDATE and result.coaching_message:
        result.was_rejected_completion = True

    return result


def _render_stage_progress(session) -> str | None:
    """Render SDLC stage progress line from session history.

    Returns two lines like:
        ISSUE 243 → ☑ PLAN → ▶ BUILD
        ☐ TEST → ☐ REVIEW → ☐ DOCS
    ISSUE has no checkbox (just label + number). Other stages show ☑ when completed,
    ▶ when in-progress, ☐ when pending. Returns None if no stage data is available.
    """
    if not session:
        return None

    progress = session.get_stage_progress()

    # Only render if at least one stage has progressed
    if all(v == "pending" for v in progress.values()):
        return None

    from models.agent_session import SDLC_STAGES

    # Extract issue number from session links for the ISSUE stage label
    issue_number = None
    links = session.get_links() if hasattr(session, "get_links") else {}
    if links and "issue" in links:
        match = re.search(r"/issues/(\d+)", links["issue"])
        if match:
            issue_number = match.group(1)

    parts = []
    for stage in SDLC_STAGES:
        status = progress.get(stage, "pending")
        # Build the label — ISSUE stage gets the issue number appended
        if stage == "ISSUE" and issue_number:
            label = f"ISSUE {issue_number}"
        else:
            label = stage

        if stage == "ISSUE":
            # ISSUE has no checkbox — just label (with optional number)
            if status == "in_progress":
                parts.append(f"▶ {label}")
            else:
                parts.append(label)
        elif status == "completed":
            parts.append(f"☑ {label}")
        elif status == "in_progress":
            parts.append(f"▶ {label}")
        else:
            parts.append(f"☐ {label}")

    line1 = " → ".join(parts[:3])
    line2 = " → ".join(parts[3:])
    return f"{line1}\n{line2}"


def _render_link_footer(session) -> str | None:
    """Render link footer from session's tracked links.

    Returns a line like: Issue #168 | PR #176
    with markdown links. Plan links are excluded — only issue and PR links
    are rendered. Returns None if no links exist.
    """
    if not session:
        return None

    links = session.get_links()
    if not links:
        return None

    parts = []
    for kind, url in links.items():
        if kind == "issue":
            # Extract issue number from URL
            match = re.search(r"/issues/(\d+)", url)
            label = f"Issue #{match.group(1)}" if match else "Issue"
            parts.append(f"[{label}]({url})")
        elif kind == "pr":
            match = re.search(r"/pull/(\d+)", url)
            label = f"PR #{match.group(1)}" if match else "PR"
            parts.append(f"[{label}]({url})")
        # Plan links are intentionally excluded

    return " | ".join(parts) if parts else None


def _get_status_emoji(session, is_completion: bool = True) -> str:
    """Get the status emoji prefix based on completion flag and session state.

    The is_completion flag takes priority for running/active sessions because
    the session status hasn't been updated yet when the summary is composed.
    Only hard terminal states (completed, failed) override the flag.
    """
    if not session:
        return "✅" if is_completion else "⏳"

    status = session.status
    if status in ("failed",):
        return "❌"
    elif status in ("completed",):
        return "✅"
    else:
        # For running/active/pending — trust the is_completion flag
        return "✅" if is_completion else "⏳"


def _build_summary_prompt(
    text: str,
    artifacts: dict[str, list[str]],
    session=None,
) -> str:
    """Build the summarization prompt.

    The system prompt handles format rules. This provides the content,
    extracted artifacts, and optional session context for enrichment.
    """
    artifact_section = ""
    if artifacts:
        parts = []
        for key, values in artifacts.items():
            parts.append(f"- {key}: {', '.join(values[:10])}")
        artifact_section = "\n\nPreserve these artifacts verbatim:\n" + "\n".join(parts) + "\n"

    context_section = ""
    if session:
        context_parts = []
        if session.message_text:
            context_parts.append(f"Original request: {(session.message_text or '')[:200]}")
        if session.classification_type:
            context_parts.append(f"Work type: {session.classification_type}")
        if session.branch_name:
            context_parts.append(f"Branch: {session.branch_name}")
        if hasattr(session, "work_item_slug") and session.work_item_slug:
            context_parts.append(f"Work item: {session.work_item_slug}")
        # Include tracked links for context
        if hasattr(session, "get_links"):
            links = session.get_links()
            if links.get("issue"):
                context_parts.append(f"Issue: {links['issue']}")
            if links.get("plan"):
                context_parts.append(f"Plan: {links['plan']}")
            if links.get("pr"):
                context_parts.append(f"PR: {links['pr']}")
        # Include recent history for context
        if hasattr(session, "_get_history_list"):
            history = session._get_history_list()
            if history:
                recent = history[-5:]  # Last 5 entries
                context_parts.append("Recent history: " + " | ".join(str(e) for e in recent))
        if context_parts:
            context_section = "\n\nSession context:\n" + "\n".join(context_parts) + "\n"

    return f"""/no_think
Summarize this developer session output:{artifact_section}{context_section}

{text}"""


def _write_full_output_file(text: str) -> Path:
    """Write full agent output to a temp file for attachment."""
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="valor_full_output_")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return Path(path)


SUMMARIZER_SYSTEM_PROMPT = """\
You condense a developer's messages into Telegram-length updates for a project manager.

You produce STRUCTURED OUTPUT with three fields via the structured_summary tool:

1. **context_summary**: One sentence describing what this session is about. Be specific \
about the topic and scope (e.g., "Implementing semantic session routing for unthreaded \
Telegram messages") — NOT vague (e.g., "Working on a feature").

2. **response**: The Telegram message text. Follow the FORMAT RULES below.

3. **expectations**: Set ONLY when the raw output contains an explicit question directed at \
the human (a sentence ending in "?" that asks for a decision, approval, or input). Copy the \
question verbatim. Set to null when no explicit questions exist — even if the agent describes \
plans or next steps. Declarative statements are NOT questions. \
Examples of non-null: "Should we use approach A or B?", "Approve the PR for merge?", \
"Is the confidence threshold of 0.80 acceptable?".

Input: ANY output from an autonomous developer — work summaries, conversational replies, \
design discussions, Q&A answers, status updates, or technical analysis. May include \
terminal output, commit messages, file diffs, opinions, or free-form notes.

CRITICAL: Never reject, editorialize, or add meta-commentary about the input. \
Your job is to condense, not to judge whether the content is "valid". \
If the input is a conversational reply or opinion, condense it faithfully.

FORMAT RULES for the **response** field (adaptive based on content type):

1. SIMPLE COMPLETIONS: Just "Done ✅" or "Yes" or "No"

2. CONVERSATIONAL REPLIES: Condense while preserving tone and key points. Prose format.

3. QUESTIONS DIRECTED AT PM: Preserve the question exactly (never rewrite or drop questions)

4. SDLC / DEVELOPMENT WORK (when session context is provided):
   Output ONLY the bullet points — 2-4 bullets max, each starting with "• ".
   The stage progress line and link footer are added automatically — do NOT include them.
   Do NOT include any emoji status prefix (✅, ⏳, etc.) — that is added automatically.
   Do NOT include any issue/PR URLs — those are rendered from session data automatically.
   Focus on WHAT was accomplished, not process details.

   If the output contains EXPLICIT questions directed at the human (sentences that literally \
   end with "?" and ask the human to decide or provide input), list them AFTER the bullets, \
   separated by "---" on its own line. Prefix each with "? ":

   NEVER fabricate questions. NEVER reframe declarative statements as questions. \
   If the agent says "I will do X", that is NOT a question — it is a plan. \
   Only surface questions that are VERBATIM in the raw output.

   Example:
   • Built auth token rotation with retry
   • 12 tests passing
   ---
   ? Should we use exponential backoff or fixed intervals?
   ? 2 nits found in review — skip or patch?

   WRONG — do NOT do this:
   Raw: "I will add sdlc to classifier categories"
   Output: ? Should classifier be updated to output 'sdlc'?   <-- FABRICATED, WRONG

   RIGHT:
   Raw: "I will add sdlc to classifier categories"
   Output: • Added sdlc to classifier categories   <-- No question, no "---"

   If there are no explicit questions in the raw output, do NOT include the "---" separator.

5. STATUS UPDATES / WORK WITH CONTEXT: 2-4 bullet points starting with "• "

GENERAL RULES:
- Lead with the outcome, not the process
- Preserve commit hashes inline (e.g., `abc1234`)
- Flag with ⚠️ ONLY for genuinely external blockers (missing credentials, need third-party \
access, policy decisions). Do NOT flag: implementation choices, internal obstacles, things \
the agent could resolve with its tools
- Tone: direct, no preamble, no filler
- Do NOT include bare URLs at the end — link rendering is handled separately
- OMIT obvious process bullets that describe routine agent activity rather than outcomes. \
Examples of what to OMIT: "Analyzed the codebase", "Read through the plan", \
"Created execution plan", "Examined the existing code", "Reviewed the implementation", \
"Investigated the issue". These are process noise — the PM only cares about WHAT was \
accomplished, not THAT you read files or analyzed code."""

# Blocker flag logic explained:
# The ⚠️ flag is meant to alert the PM only when human intervention is truly required.
# Genuine blockers: missing API keys, need admin access to a service, policy/legal decisions,
# waiting on external team, need credentials the agent cannot obtain.
# NOT blockers: code bugs (agent can fix), test failures (agent can debug), implementation
# decisions (agent should decide), finding the right approach (agent's job).


async def _summarize_with_haiku(prompt: str) -> StructuredSummary | None:
    """Try structured summarization via Anthropic Haiku API using tool_use.

    Returns a StructuredSummary with context_summary, response, and expectations
    fields extracted via the structured_summary tool. Falls back to text-only
    Haiku if tool_use fails, wrapping the result in a StructuredSummary with
    empty routing fields.
    """
    try:
        from utils.api_keys import get_anthropic_api_key

        api_key = get_anthropic_api_key()
        if not api_key:
            logger.warning("No Anthropic API key found for summarization")
            return None

        client = anthropic.AsyncAnthropic(api_key=api_key)

        # Try tool_use for structured output
        try:
            response = await client.messages.create(
                model=MODEL_FAST,
                max_tokens=1024,
                system=SUMMARIZER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                tools=[STRUCTURED_SUMMARY_TOOL],
                tool_choice={"type": "tool", "name": "structured_summary"},
            )

            # Parse tool_use response — tool_use returns the input dict directly
            for block in response.content:
                if block.type == "tool_use" and block.name == "structured_summary":
                    tool_input = block.input
                    return StructuredSummary(
                        context_summary=tool_input.get("context_summary", ""),
                        response=tool_input.get("response", ""),
                        expectations=tool_input.get("expectations"),
                    )

            logger.warning("Haiku tool_use returned no tool_use block, falling back to text")
        except Exception as e:
            logger.warning(f"Haiku tool_use failed, falling back to text-only: {e}")

        # Fallback: text-only Haiku (no structured routing fields)
        response = await client.messages.create(
            model=MODEL_FAST,
            max_tokens=512,
            system=SUMMARIZER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text_result = response.content[0].text
        return StructuredSummary(
            context_summary="",
            response=text_result,
            expectations=None,
        )
    except Exception as e:
        logger.warning(f"Haiku summarization failed: {e}")
        return None


async def _summarize_with_openrouter(prompt: str) -> StructuredSummary | None:
    """Fallback: summarize via OpenRouter API (Haiku model).

    Uses the OpenRouter chat completions endpoint with tool_use for structured
    output. Falls back to text-only if tool_use fails. Requires OPENROUTER_API_KEY
    environment variable.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("No OPENROUTER_API_KEY found for summarization fallback")
        return None

    try:
        # OpenRouter uses OpenAI-compatible format for tools
        openrouter_tool = {
            "type": "function",
            "function": {
                "name": STRUCTURED_SUMMARY_TOOL["name"],
                "description": STRUCTURED_SUMMARY_TOOL["description"],
                "parameters": STRUCTURED_SUMMARY_TOOL["input_schema"],
            },
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Try with tool_use first
        try:
            payload = {
                "model": OPENROUTER_HAIKU,
                "messages": [
                    {"role": "system", "content": SUMMARIZER_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "tools": [openrouter_tool],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "structured_summary"},
                },
                "max_tokens": 1024,
            }

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()

            # Parse tool call from response
            message = data.get("choices", [{}])[0].get("message", {})
            tool_calls = message.get("tool_calls", [])
            for tc in tool_calls:
                if tc.get("function", {}).get("name") == "structured_summary":
                    args = json.loads(tc["function"]["arguments"])
                    return StructuredSummary(
                        context_summary=args.get("context_summary", ""),
                        response=args.get("response", ""),
                        expectations=args.get("expectations"),
                    )

            logger.warning("OpenRouter tool_use returned no tool call, falling back to text")
        except Exception as e:
            logger.warning(f"OpenRouter tool_use failed, falling back to text-only: {e}")

        # Fallback: text-only via OpenRouter
        payload = {
            "model": OPENROUTER_HAIKU,
            "messages": [
                {"role": "system", "content": SUMMARIZER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 512,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        text_result = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if text_result:
            return StructuredSummary(
                context_summary="",
                response=text_result,
                expectations=None,
            )
        return None
    except Exception as e:
        logger.warning(f"OpenRouter summarization failed: {e}")
        return None


def _parse_summary_and_questions(summary_text: str) -> tuple[str, str | None]:
    """Parse LLM summary output into bullets and optional questions.

    The LLM may produce:
        • Bullet 1
        • Bullet 2
        ---
        ? Question 1
        ? Question 2

    Returns (bullets, questions) where questions is None if no --- separator found.
    """
    if "\n---\n" in summary_text:
        bullets, questions = summary_text.split("\n---\n", 1)
        questions = questions.strip()
        if questions:
            return bullets.strip(), questions
        return bullets.strip(), None
    # Also handle --- at the very start (edge case)
    if summary_text.strip().startswith("---"):
        return "", summary_text.strip().lstrip("-").strip() or None
    return summary_text, None


def _compose_structured_summary(summary_text: str, session=None, is_completion: bool = True) -> str:
    """Compose the full structured summary with emoji, stage line, bullets, questions, and links.

    Two modes:

    Chat (non-SDLC):
        ✅
        • Bullet point 1
        • Bullet point 2

        ? Question needing input

    SDLC:
        ⏳
        ISSUE 243 → PLAN → ▶ BUILD → TEST → REVIEW → DOCS
        • Bullet point 1
        • Bullet point 2

        ? Question needing input
        Issue #243 | PR #250
    """
    # Re-read session from Redis to pick up stage data written during execution.
    # The session object passed in may have been loaded before session_progress.py
    # wrote [stage] entries and link URLs — re-reading ensures we get fresh data.
    if session and hasattr(session, "session_id") and session.session_id:
        try:
            from models.agent_session import AgentSession

            fresh_sessions = list(AgentSession.query.filter(session_id=session.session_id))
            if fresh_sessions:
                session = fresh_sessions[0]
                logger.debug(f"Refreshed session {session.session_id} for structured summary")
        except Exception as e:
            logger.debug(f"Could not refresh session for summary: {e}")

    # Parse questions from LLM output
    bullets, questions = _parse_summary_and_questions(summary_text)

    parts = []

    # Status emoji prefix (no message echo — Telegram reply-to provides context)
    emoji = _get_status_emoji(session, is_completion)
    parts.append(emoji)

    # Stage progress line — mandatory for SDLC, optional for others
    stage_line = _render_stage_progress(session)
    if stage_line:
        parts.append(stage_line)
        logger.info(
            f"Rendered stage progress for session "
            f"{session.session_id if session else 'N/A'}: {stage_line}"
        )
    elif session and hasattr(session, "is_sdlc_job") and session.is_sdlc_job():
        logger.warning(f"SDLC session {session.session_id} has no stage progress to render")

    # Summary text (bullets or prose)
    parts.append(bullets.strip())

    # Questions section (if any)
    if questions:
        parts.append("")  # blank line separator
        parts.append(questions)

    # Link footer — mandatory for SDLC jobs
    link_footer = _render_link_footer(session)
    if link_footer:
        parts.append(link_footer)

    return "\n".join(parts)


async def summarize_response(
    raw_response: str,
    session=None,
) -> SummarizedResponse:
    """Summarize an agent response for Telegram delivery.

    Uses structured tool_use output to extract context_summary, response,
    and expectations fields. Fallback chain: Haiku tool_use -> Haiku text ->
    OpenRouter tool_use -> OpenRouter text -> raw truncation.

    - All non-empty responses: summarized via Haiku, then OpenRouter fallback
    - Very long responses (> FILE_ATTACH_THRESHOLD): full output
      attached as file
    - SDLC sessions: structured template with stage progress + link footer

    Args:
        raw_response: The raw agent output text.
        session: Optional AgentSession for context enrichment.

    Falls back to safety truncation if all summarization fails.
    """
    if not raw_response or not raw_response.strip():
        # Even with empty response, render SDLC progress if available
        if session:
            fallback = _compose_structured_summary("", session=session, is_completion=True)
            if fallback.strip():
                return SummarizedResponse(text=fallback, was_summarized=True)
        return SummarizedResponse(text=raw_response or "", was_summarized=False)

    artifacts = extract_artifacts(raw_response)

    # Write full output file for very long responses
    full_output_file = None
    if len(raw_response) > FILE_ATTACH_THRESHOLD:
        try:
            full_output_file = _write_full_output_file(raw_response)
        except Exception as e:
            logger.warning(f"Failed to write full output file: {e}")

    # Strip process narration before summarization
    cleaned_response = _strip_process_narration(raw_response)

    # Build prompt once, try multiple backends
    prompt = _build_summary_prompt(cleaned_response, artifacts, session=session)

    # Try Haiku first, then OpenRouter
    structured = await _summarize_with_haiku(prompt)
    if structured is None:
        logger.info("Falling back to OpenRouter for summarization")
        structured = await _summarize_with_openrouter(prompt)

    if structured is not None:
        summary_text = structured.response

        # Safety: if summary is somehow longer than original, truncate
        if len(summary_text) >= len(raw_response):
            logger.warning("Summary longer than original, using truncated original")
            if len(raw_response) > SAFETY_TRUNCATE:
                summary_text = raw_response[: SAFETY_TRUNCATE - 3] + "..."
            else:
                summary_text = raw_response

        # Open question extraction: if the raw output contains an
        # ## Open Questions section with substantive questions, populate
        # expectations with the extracted questions. This bypasses the
        # anti-fabrication filter because questions are extracted verbatim
        # from structured document sections, not fabricated by the LLM.
        # LLM-detected expectations take priority (if already set).
        expectations = structured.expectations
        if not expectations:
            open_questions = _extract_open_questions(raw_response)
            if open_questions:
                expectations = "\n".join(f"? {q}" for q in open_questions)
                logger.info(
                    f"Extracted {len(open_questions)} open questions from "
                    f"## Open Questions section"
                )

        # Compose structured output with stage progress and links
        summary_text = _compose_structured_summary(
            summary_text, session=session, is_completion=True
        )

        return SummarizedResponse(
            text=summary_text,
            full_output_file=full_output_file,
            was_summarized=True,
            artifacts=artifacts,
            context_summary=structured.context_summary or None,
            expectations=expectations,
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
