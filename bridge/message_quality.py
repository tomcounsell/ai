"""Message quality filters for the nudge loop delivery path.

Provides heuristics to detect when worker output is non-substantive
(pure process narration, false promises) and should not be delivered
to the user as-is.
"""

import re

# Process narration patterns — shared with bridge/summarizer.py
# These detect "Let me check...", "I'll look at...", etc.
PROCESS_NARRATION_PATTERNS = [
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
    re.compile(r"^Here'?s? (my |the )?(plan|approach|strategy)", re.IGNORECASE),
    re.compile(r"^My (plan|approach|strategy) (is|will be|would be)", re.IGNORECASE),
    re.compile(r"^I('ll| will| am going to| plan to) (approach|tackle|handle|address) "),
    re.compile(r"^(Step|Phase) \d+[.:] ", re.IGNORECASE),
    re.compile(r"^\d+\.\s+(First|Then|Next|Finally|After that)", re.IGNORECASE),
]

# Substantive content markers — if ANY of these are present, the output
# is not pure narration even if it starts with narration phrases.
_CODE_FENCE_RE = re.compile(r"```")
_URL_RE = re.compile(r"https?://")
_FILE_PATH_RE = re.compile(r"(?:^|[\s(])[a-zA-Z_][\w]*(?:/[\w._-]+){1,}")
_TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\)|^\s+File \"", re.MULTILINE)

# Maximum length for narration-only detection. Longer outputs likely
# contain substantive findings even if they start with narration.
_MAX_NARRATION_LENGTH = 500


def is_narration_only(text: str) -> bool:
    """Detect if worker output is entirely process narration with no substance.

    Returns True when ALL of these conditions are met:
    - Text is non-empty
    - Text length is under 500 characters
    - Every non-empty line matches at least one narration pattern
    - No substantive content markers are present (code fences, URLs,
      file paths, tracebacks)

    Examples that return True:
        "Let me check how the routing is configured."
        "Let me look at the code. Now let me examine the tests."

    Examples that return False:
        "Let me check the config. Found the issue in agent/job_queue.py line 42."
        "Let me look at the logs.\n```\nERROR: connection refused\n```"
        "" (empty string)

    Args:
        text: The worker output to analyze.

    Returns:
        True if the output is pure narration without substantive findings.
    """
    if not text or not text.strip():
        return False

    stripped = text.strip()

    # Length gate: long outputs likely contain findings
    if len(stripped) > _MAX_NARRATION_LENGTH:
        return False

    # Substantive content markers: if any present, not pure narration
    if _CODE_FENCE_RE.search(stripped):
        return False
    if _URL_RE.search(stripped):
        return False
    if _FILE_PATH_RE.search(stripped):
        return False
    if _TRACEBACK_RE.search(stripped):
        return False

    # Check that every non-empty line matches a narration pattern
    lines = [line.strip() for line in stripped.split("\n") if line.strip()]
    if not lines:
        return False

    for line in lines:
        if not any(p.match(line) for p in PROCESS_NARRATION_PATTERNS):
            return False

    return True


# Fallback message when worker output is narration-only and auto-continue
# budget is exhausted.
NARRATION_FALLBACK_MESSAGE = (
    "The investigation was incomplete and did not produce substantive results. "
    "Please re-trigger if you'd like me to try again."
)

# Coaching message sent when narration gate triggers an auto-continue
NARRATION_COACHING_MESSAGE = (
    "You announced you would investigate but stopped before producing findings. "
    "Continue the investigation and report actual results."
)
