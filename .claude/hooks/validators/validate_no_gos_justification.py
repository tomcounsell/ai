#!/usr/bin/env python3
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

# Standalone script — sys.path mutation is safe (never imported as library).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hook_utils.hook_target import in_scope, read_hook_input, target_from_hook_input  # noqa: E402

PLAN_DIRECTORY = "docs/plans"
PLAN_EXTENSION = ".md"

VALID_TAGS = ("[EXTERNAL]", "[ORDERED]", "[DESTRUCTIVE]", "[SEPARATE-SLUG")

PUNT_PHRASES = [
    r"\bdeferred to (?:a )?follow-?up\b",
    r"\bfollow-?up (?:issue|pr|ticket)\b",
    # "operator/human will <do something>" is a punt. "operator will SEE the
    # log line change" is a description of observable behavior, which every
    # plan's impact section is made of (#2682). Only the action sense counts.
    #
    # The optional adverb slot is load-bearing: impact prose says "will *simply*
    # see", "will *then* see", "will *now* observe", and without the slot each
    # of those scores as a punt — a false positive that blocks an unrelated
    # lane's write. The slot must live INSIDE the negative lookahead. Placed
    # before it (``will\s+(?:\w+ly\s+)?(?!see\b|...)``) it does nothing: the
    # group is optional, so when the lookahead fails with the adverb consumed
    # the engine backtracks, matches the group as empty, re-tests the lookahead
    # against the adverb itself ("simply" is not "see"), and the pattern matches
    # anyway. Written as ``(?!<optional adverb><perception verb>)`` the
    # assertion is what we actually mean: what follows is NOT an optionally
    # adverb-prefixed perception verb.
    #
    # ``no longer`` stays a standalone alternative rather than an adverb,
    # because it exempts ANY following verb ("will no longer rotate the key" is
    # not a punt). Demoted into the adverb slot it would only exempt perception
    # verbs, re-introducing a false positive.
    #
    # ``\w+ly`` deliberately over-matches a few verbs ("apply", "supply"), which
    # is harmless: after "apply" the next token is not a perception verb, and
    # "apply" is not one either, so the inner pattern fails and the line stays a
    # punt. Pinned by ``test_action_sense_is_still_a_punt``.
    r"\b(?:operator|human)\s+will\s+"
    r"(?!(?:(?:\w+ly|then|now|also|still|already|just|soon|never)\s+)?"
    r"(?:see|notice|observe|read|find|know|get|receive|experience)\b|no longer\b)",
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


NO_GOS_HEADING = re.compile(r"^## No-Gos[^\n]*$(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
# The full run of fence characters is captured, not just its first three,
# because a fence closes only on a marker of the SAME character at least as
# long as the one that opened it — CommonMark's rule, and the only way to write
# a fenced block that itself contains a fence. A bare open/close toggle treats
# the inner ``` of a ````-wrapped example as a closer and stays inverted for the
# rest of the document, so the code exemption evaporates and a punt phrase
# quoted inside that example blocks an unrelated agent's write (#2682). The
# false-positive direction is the dangerous one, hence the strict rule.
#
# Accepted cost: the strict rule widens the fail-open surface from *unterminated*
# fences to ANY mismatched marker — a ````-opened block "closed" by ``` now
# exempts everything to EOF, and that reach extends into ``## No-Gos``, the one
# section where the quoted-context exemption deliberately does not apply. So a
# typo'd fence anywhere above No-Gos silently disarms the gate for that document.
# We take that trade knowingly: fail-open can never block a lane, and CommonMark
# correctness is worth more than catching a malformed document. Pinned by
# ``test_mismatched_fence_markers_fail_open_into_no_gos``.
#
# The ``^\s*`` allowance (rather than CommonMark's three-space cap) is
# deliberate: plans nest fences inside list items at arbitrary depth.
FENCE = re.compile(r"^\s*(?P<marker>`{3,}|~{3,})")

# A leading pipe or angle bracket marks transcribed material rather than the
# plan's own commitments: critique-findings tables, review verdict logs, and
# "critic's suggested remedy, kept for the record" blocks all quote deferral
# phrasing in order to discuss it. Scanning them means a plan gets *more*
# likely to fail the more carefully it records its own review (#2682).
QUOTED_CONTEXT_PREFIXES = ("|", ">")


def extract_no_gos_section(content: str) -> str | None:
    match = NO_GOS_HEADING.search(content)
    return match.group(1).strip() if match else None


def no_gos_line_span(content: str) -> tuple[int, int]:
    """0-based ``[start, end)`` line range covering the No-Gos section.

    Wider than the section *body* on both ends, deliberately. ``start`` is the
    ``## No-Gos`` heading line itself, and ``end`` is one past the following
    ``## `` heading (or at/past EOF when No-Gos is the last section). Both
    headings land inside the range but cost nothing: the caller skips any line
    beginning with ``#``.

    The ``+ 1`` is what keeps a final *unterminated* line in range. ``end`` is
    derived from a newline count, so when No-Gos is the last section and the
    file has no trailing newline, the closing line contributes no newline and
    would otherwise fall outside the scan — silently disarming the guard on the
    one section where the quoted-context exemption does not apply. Pinned by
    ``test_table_row_inside_no_gos_still_fails``'s no-trailing-newline case.

    ``(-1, -1)`` when there is no such section.
    """
    match = NO_GOS_HEADING.search(content)
    if not match:
        return (-1, -1)
    start = content.count("\n", 0, match.start(1))
    end = content.count("\n", 0, match.end(1))
    return (start, end + 1)


def find_punt_lines(content: str) -> list[str]:
    """Unjustified deferrals in the plan's own voice.

    The whole body is scanned, not just No-Gos, because a punt hides in prose
    anywhere. Two kinds of line are exempt outside the No-Gos section:
    fenced code, and quoted/tabular context. Inside No-Gos nothing is exempt
    but code fences — that section is where commitments are made, so a punt
    written as a table row there must still fail.
    """
    lines = content.splitlines()
    no_gos_start, no_gos_end = no_gos_line_span(content)
    bad: list[str] = []
    open_marker: str | None = None

    for idx, raw_line in enumerate(lines):
        fence = FENCE.match(raw_line)
        if fence:
            marker = fence.group("marker")
            if open_marker is None:
                open_marker = marker
            # A shorter marker, or one of the other character, is fence
            # content — an example block being quoted, not a closer. The
            # ``elif`` is structural: an opener must never be tested against
            # itself, or every fence would close on the line that opened it.
            elif marker[0] == open_marker[0] and len(marker) >= len(open_marker):
                open_marker = None
            continue
        if open_marker is not None:
            continue

        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("<!--"):
            continue

        inside_no_gos = no_gos_start <= idx < no_gos_end
        if not inside_no_gos and line.startswith(QUOTED_CONTEXT_PREFIXES):
            continue

        if not line_is_punt(line):
            continue
        if line_is_justified(line):
            continue
        if line_is_explicit_none(line):
            continue
        bad.append(f"  {raw_line.rstrip()}")

    return bad


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

    bad = find_punt_lines(content)
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

    # An explicit path (CLI / tests) wins and bypasses the scope filter: it was
    # named on purpose. Otherwise take the path the hook's own tool input names.
    # Never guess.
    if args.plan_file:
        plan_file = args.plan_file
        if not Path(plan_file).exists():
            # An explicit CLI argument that names nothing is a user error worth
            # reporting. A hook-derived path we cannot resolve is not: there is
            # no content to judge, so there is no finding to make.
            print(f"ERROR: Plan file does not exist: {plan_file}", file=sys.stderr)
            sys.exit(2)
    else:
        plan_file = target_from_hook_input(read_hook_input())
        if not plan_file:
            sys.exit(0)

        # Only enforce on plan docs; pass through anything else. The path is
        # judged as given — never rewritten against a cwd — and the filter runs
        # before any Path.exists(), so a Write this hook has no business judging
        # is never statted and can never block.
        if not in_scope(plan_file, PLAN_DIRECTORY, PLAN_EXTENSION):
            sys.exit(0)
        if not Path(plan_file).exists():
            sys.exit(0)

    success, message = validate(plan_file)
    if success:
        print(json.dumps({"result": "continue", "message": message}))
        sys.exit(0)
    print(message, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
