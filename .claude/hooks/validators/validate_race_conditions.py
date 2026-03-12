#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Soft validator: warn if a plan touching async code lacks a ## Race Conditions section.

This validator checks whether the plan document references files or patterns
associated with async/concurrent code (bridge/, agent/, asyncio, create_task).
If so, it verifies that a ## Race Conditions section exists with substantive
content. If the section is missing or empty, it prints a warning to stderr
but does NOT block (always exits 0).

Exit codes:
- 0: Always (this is a soft validator -- warns but never blocks)

Usage:
  uv run validate_race_conditions.py docs/plans/feature-name.md
  uv run validate_race_conditions.py  # auto-detects newest plan file
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Patterns that indicate async/concurrent code is involved.
# These are checked in code-context sections (Solution, Technical Approach, etc.)
# to avoid false positives from prose mentioning these terms casually.
ASYNC_CODE_PATTERNS = [
    r"`bridge/",  # backtick-quoted file paths
    r"`agent/",  # backtick-quoted file paths
    r"bridge/\w+\.py",  # file path references like bridge/telegram_bridge.py
    r"agent/\w+\.py",  # file path references like agent/job_queue.py
    r"`asyncio",  # backtick-quoted module references
    r"async\s+def\s",  # async function definitions
    r"create_task\(",  # asyncio.create_task() calls
    r"await\s+\w",  # await expressions
    r"`aiohttp",  # backtick-quoted library references
    r"asyncio\.\w+",  # asyncio.Lock, asyncio.Event, etc.
    r"concurrent\.\w+",  # concurrent.futures references
    r"threading\.\w+",  # threading module references
    r"multiprocessing\.",  # multiprocessing module references
]

# Broader patterns checked without case sensitivity for section headers
# and explicit mentions in Solution/Technical sections
ASYNC_SECTION_INDICATORS = [
    r"##.*async",  # section headers mentioning async
    r"##.*concurren",  # section headers mentioning concurrency
    r"##.*race\s+condition",  # section headers mentioning race conditions
]

WARNING_MESSAGE = """
WARNING: Plan '{file}' appears to involve async/concurrent code but has no
## Race Conditions section.

Plans that modify async code, shared mutable state, or cross-process data flows
should include a Race Conditions section to enumerate timing hazards.

Add a ## Race Conditions section to the plan following this template:

## Race Conditions

[Enumerate timing-dependent bugs, concurrent access patterns, and data/state
prerequisites. For each hazard, specify:]

### Race N: [Description]
**Location:** [File and line range]
**Trigger:** [What sequence of events causes the race]
**Data prerequisite:** [What data must exist/be populated before the dependent operation]
**State prerequisite:** [What system state must hold for correctness]
**Mitigation:** [How the implementation prevents this -- await, lock, re-read, idempotency, etc.]

If no race conditions exist, state "No race conditions identified" with
justification (e.g., "all operations are synchronous and single-threaded").

NOTE: This is a soft warning and does not block plan creation.
"""

INCOMPLETE_WARNING = """
WARNING: Plan '{file}' has a ## Race Conditions section but it appears incomplete.

The section should either:
1. Enumerate specific race conditions with Location, Trigger, Data/State prerequisites,
   and Mitigation for each, OR
2. Explicitly state "No race conditions identified" with justification

NOTE: This is a soft warning and does not block plan creation.
"""


def find_newest_plan_file(directory: str = "docs/plans") -> str | None:
    """Find the most recently created plan file in git."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", f"{directory}/"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None

        new_files = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            status = line[:2]
            filepath = line[3:].strip()
            if status in ("??", "A ", " A", "AM") and filepath.endswith(".md"):
                new_files.append(filepath)

        if not new_files:
            return None

        newest = None
        newest_mtime = 0
        for filepath in new_files:
            path = Path(filepath)
            if path.exists():
                mtime = path.stat().st_mtime
                if mtime > newest_mtime:
                    newest_mtime = mtime
                    newest = str(path)
        return newest
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None


def plan_involves_async_code(content: str) -> bool:
    """Check if the plan content references async/concurrent code patterns.

    Uses precise patterns to avoid false positives from prose that casually
    mentions words like 'await' or 'agent/' in non-code contexts.
    """
    # Check code-specific patterns (case-sensitive for precision)
    for pattern in ASYNC_CODE_PATTERNS:
        if re.search(pattern, content):
            return True
    # Check section-level indicators (case-insensitive)
    for pattern in ASYNC_SECTION_INDICATORS:
        if re.search(pattern, content, re.IGNORECASE):
            return True
    return False


def extract_race_conditions_section(content: str) -> str | None:
    """Extract the ## Race Conditions section from plan content."""
    match = re.search(
        r"^## Race Conditions\s*$(.*?)(?=^## |\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return None


def is_section_substantive(section_content: str) -> bool:
    """Check if the Race Conditions section has substantive content."""
    if not section_content:
        return False

    # Check for explicit "no race conditions" statement
    no_race_patterns = [
        r"no race conditions",
        r"no concurrency concerns",
        r"no timing.*(hazard|issue|concern)",
        r"purely synchronous",
        r"single.threaded",
    ]
    for pattern in no_race_patterns:
        if re.search(pattern, section_content, re.IGNORECASE):
            # Must also include justification (at least some explanation)
            if len(section_content) > 40:
                return True

    # Check for structured race condition entries
    has_location = bool(re.search(r"\*\*Location:\*\*", section_content))
    has_trigger = bool(re.search(r"\*\*Trigger:\*\*", section_content))
    has_mitigation = bool(re.search(r"\*\*Mitigation:\*\*", section_content))

    # At least two of three structural markers present
    if sum([has_location, has_trigger, has_mitigation]) >= 2:
        return True

    # Check for race condition subsections (### Race N:)
    if re.search(r"^###\s+Race\s+\d+", section_content, re.MULTILINE):
        return True

    # Check for common placeholder text
    placeholder_patterns = [
        r"^\[.*\]$",
        r"^TBD\s*$",
        r"^TODO\s*$",
        r"^\.\.\.\s*$",
    ]
    for pattern in placeholder_patterns:
        if re.match(pattern, section_content.strip(), re.IGNORECASE):
            return False

    # Some content but unclear
    if len(section_content) < 40:
        return False

    return True


def validate_race_conditions(filepath: str) -> tuple[bool, str]:
    """
    Validate the plan file's Race Conditions section.

    Returns: (needs_warning, message)
    - needs_warning=False means no warning needed (section is fine or not applicable)
    - needs_warning=True means a warning should be printed
    """
    try:
        content = Path(filepath).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return False, f"Could not read file: {e}"

    # Check if the plan involves async code
    if not plan_involves_async_code(content):
        return False, "Plan does not involve async code -- Race Conditions section not required"

    # Plan involves async code -- check for the section
    section = extract_race_conditions_section(content)
    if section is None:
        return True, WARNING_MESSAGE.format(file=filepath)

    # Section exists -- check if it's substantive
    if not is_section_substantive(section):
        return True, INCOMPLETE_WARNING.format(file=filepath)

    return False, "Race Conditions section is present and substantive"


def main():
    parser = argparse.ArgumentParser(
        description="Soft validator: warn if async plans lack Race Conditions section"
    )
    parser.add_argument(
        "plan_file",
        nargs="?",
        help="Path to plan file (auto-detects if not provided)",
    )
    args = parser.parse_args()

    # Consume stdin if provided (SDK passes context via stdin)
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        pass

    # Determine which file to validate
    plan_file = args.plan_file
    if not plan_file:
        plan_file = find_newest_plan_file()
        if not plan_file:
            # No new plan file detected -- nothing to validate
            print(json.dumps({"result": "continue", "message": "No new plan file detected"}))
            sys.exit(0)

    if not Path(plan_file).exists():
        print(f"Plan file does not exist: {plan_file}", file=sys.stderr)
        # Soft validator -- don't block even on errors
        msg = f"Plan file does not exist: {plan_file}"
        print(json.dumps({"result": "continue", "message": msg}))
        sys.exit(0)

    needs_warning, message = validate_race_conditions(plan_file)

    if needs_warning:
        # Print warning to stderr but exit 0 (soft validator, never blocks)
        print(message, file=sys.stderr)

    # Always exit 0 -- this is a soft validator
    print(json.dumps({"result": "continue", "message": message.strip()[:200]}))
    sys.exit(0)


if __name__ == "__main__":
    main()
