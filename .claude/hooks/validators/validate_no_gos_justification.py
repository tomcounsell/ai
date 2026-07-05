#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Validate that a plan's ## No-Gos section justifies each deferred item.

Audit (issue #1325) found that "operator step" / "follow-up issue" / "out of
scope" frequently mask work the agent could have done in-plan. Each No-Go must
declare why the agent cannot finish it now, using one of four tags:

  [EXTERNAL]   — needs a human/world action (rotate secret, click third-party UI)
  [ORDERED]    — sequenced deploy/merge dependent on a human-gated event
  [DESTRUCTIVE]— irreversible one-shot where review-before-execute is the safety
  [SEPARATE-SLUG #N] — split off as filed issue N (validator confirms gh issue exists)

Plain "deferred", "v2", "follow-up" without a tag fails. If the agent could do
it in this plan, do it.

Same trigger style as validate_documentation_section.py / validate_test_impact_section.py:
runs on Write of docs/plans/*.md.

Exit codes:
- 0: pass (or not a plan file)
- 2: fail, blocks agent

Usage:
  uv run validate_no_gos_justification.py docs/plans/feature-name.md
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

VALID_TAGS = ("[EXTERNAL]", "[ORDERED]", "[DESTRUCTIVE]", "[SEPARATE-SLUG")

PUNT_PHRASES = [
    r"\bdeferred to (?:a )?follow-?up\b",
    r"\bfollow-?up (?:issue|pr|ticket)\b",
    r"\boperator will\b",
    r"\bhuman will\b",
    r"\bin v2\b",
    r"\bdefer(?:red)? to v\d\b",
    r"\bpunt(?:ed)? to\b",
    # "post-merge" only counts as a punt inside a deferral construction —
    # descriptive uses ("post-merge memory extraction", "runs post-merge")
    # must pass (issue #1900 review noted the bare \bpost-merge\b pattern
    # false-positived on every hyphenated mention).
    r"\b(?:defer(?:red|ring)?|punt(?:ed|ing)?|left|leave)\b[^.;:]{0,40}\bpost-merge\b",
    r"\bpost-merge\s+(?:follow-?up|todo)\b",
    r"\b(?:will|to)\s+(?:be\s+)?(?:do(?:ne)?|handled?|fix(?:ed)?|address(?:ed)?|complete[d]?)\s+post-merge\b",
    r"\btodo after\b",
]

MISSING_SECTION_ERROR = """\
VALIDATION FAILED: Plan '{file}' is missing a ## No-Gos section.

Add one. If nothing is out of scope, write:

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.
"""

UNJUSTIFIED_PUNT_ERROR = """\
VALIDATION FAILED: Plan '{file}' has unjustified deferrals.

The ## No-Gos section (or other plan body) contains language that defers work
without justifying why the agent cannot do it in-plan. Each deferred item must
be tagged with one of:

  [EXTERNAL]            — genuine human/world action required
  [ORDERED]             — sequenced deploy/merge dependent on human-gated event
  [DESTRUCTIVE]         — irreversible one-shot, review-before-execute is the safety
  [SEPARATE-SLUG #NNN]  — filed as issue N (must resolve via `gh issue view N`)

Unjustified lines:
{lines}

If the agent could do this in-plan, do it. Plain "deferred", "v2", or
"follow-up" without a tag is not allowed. See issue #1325 for the audit that
motivated this rule.
"""

BAD_SLUG_ERROR = """\
VALIDATION FAILED: Plan '{file}' references [SEPARATE-SLUG #{n}] but that issue
could not be resolved via `gh issue view {n}`. File the issue first, then
reference it here. Tracking-only promises (no issue filed) are exactly what
this rule blocks.
"""


def find_newest_plan_file(directory: str = "docs/plans") -> str | None:
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
            if status in ("??", "A ", " A", "AM", " M", "M ", "MM") and filepath.endswith(".md"):
                new_files.append(filepath)
        if not new_files:
            return None
        newest = None
        newest_mtime = 0.0
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


def extract_no_gos_section(content: str) -> str | None:
    match = re.search(
        r"^## No-Gos[^\n]*$(.*?)(?=^## |\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else None


def line_is_punt(line: str) -> bool:
    lowered = line.lower()
    if any(re.search(p, lowered) for p in PUNT_PHRASES):
        return True
    return False


def line_is_justified(line: str) -> bool:
    return any(tag in line for tag in VALID_TAGS)


def line_is_explicit_none(line: str) -> bool:
    """Lines that explicitly state nothing-deferred get a pass."""
    lowered = line.lower().strip()
    none_patterns = [
        "nothing deferred",
        "no items deferred",
        "no out-of-scope items",
        "everything in scope",
        "no follow-ups",
    ]
    return any(p in lowered for p in none_patterns)


def gh_issue_exists(num: str) -> bool:
    try:
        result = subprocess.run(
            ["gh", "issue", "view", num, "--json", "number"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # If gh is unreachable, don't block — the validator should fail open
        # on tooling errors so it can't wedge a checkout offline.
        return True


def validate(filepath: str) -> tuple[bool, str]:
    try:
        content = Path(filepath).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return False, f"Failed to read file: {e}"

    section = extract_no_gos_section(content)
    if section is None:
        return False, MISSING_SECTION_ERROR.format(file=filepath)

    # Walk every body line of the entire plan looking for punt phrases —
    # punts hide outside the No-Gos section too.
    bad: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("<!--"):
            continue
        if not line_is_punt(line):
            continue
        if line_is_justified(line):
            continue
        if line_is_explicit_none(line):
            continue
        bad.append(f"  {raw_line.rstrip()}")

    if bad:
        return False, UNJUSTIFIED_PUNT_ERROR.format(file=filepath, lines="\n".join(bad[:10]))

    # Validate every [SEPARATE-SLUG #N] resolves to a real issue
    for match in re.finditer(r"\[SEPARATE-SLUG\s+#(\d+)\]", content):
        num = match.group(1)
        if not gh_issue_exists(num):
            return False, BAD_SLUG_ERROR.format(file=filepath, n=num)

    return True, "No-Gos section is properly justified."


def main():
    parser = argparse.ArgumentParser(description="Validate plan No-Gos justifications")
    parser.add_argument("plan_file", nargs="?", help="Path to plan file")
    args = parser.parse_args()

    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        pass

    plan_file = args.plan_file or find_newest_plan_file()
    if not plan_file:
        sys.exit(0)

    if not Path(plan_file).exists():
        print(f"ERROR: Plan file does not exist: {plan_file}", file=sys.stderr)
        sys.exit(2)

    # Only enforce on plan docs; pass through anything else
    if "docs/plans" not in plan_file.replace("\\", "/"):
        sys.exit(0)

    success, message = validate(plan_file)
    if success:
        print(json.dumps({"result": "continue", "message": message}))
        sys.exit(0)
    print(message, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
