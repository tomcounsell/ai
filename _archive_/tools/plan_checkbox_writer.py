"""Plan-checkbox writer: tick / untick / status helper for plan documents.

This module provides a CLI for safely flipping checkboxes inside a plan's
acceptance/success criteria section. It is invoked from /do-pr-review (when
emitting an Approved verdict) and /do-patch (when fixing a review blocker)
so the tick lives in the same commit as the substantive work — closing the
oscillation loop where a separate "tick off completed plan items" commit
invalidated the prior PR approval.

Why a helper instead of inline regex in each skill?
- Both /do-pr-review and /do-patch need identical match semantics. A shared
  helper guarantees that.
- The whitespace-normalization-then-exact-match contract is unit-testable
  here in a way that prompt-embedded regex is not.
- Failure modes (MATCH_AMBIGUOUS, MATCH_NOT_FOUND, MATCH_AMBIGUOUS_SECTION,
  NO_CRITERIA_SECTION, EMPTY_CRITERION, MISSING_FILE, MALFORMED_PLAN) return
  distinct non-zero exit codes so callers can route the appropriate
  manual-review comment without re-parsing stderr.

CLI usage:
    python -m tools.plan_checkbox_writer tick   <plan_path> --criterion "<exact text>"
    python -m tools.plan_checkbox_writer untick <plan_path> --criterion "<exact text>"
    python -m tools.plan_checkbox_writer status <plan_path>

Match contract (case-sensitive; whitespace-normalized but otherwise exact):
1. Find the criteria section by `^##\\s+(Acceptance Criteria|Success Criteria)\\s*$`.
   Both headings are accepted (138 plans use Success vs 1 plan uses Acceptance
   in this repo at plan time). If both headings are present, exit
   MATCH_AMBIGUOUS_SECTION; if neither, exit NO_CRITERIA_SECTION.
2. Section ends at the next `^##\\s` heading or end-of-file.
3. Within the section, extract every line matching `^[ \\t]*- \\[[ x]\\] (.+)$`.
4. Normalize each criterion's text: ``re.sub(r'\\s+', ' ', text.strip())``.
   Normalize the input --criterion the same way.
5. Compare normalized strings with case-sensitive equality.
   - Exactly one match -> rewrite that line's checkbox. Exit 0.
   - Zero matches -> exit MATCH_NOT_FOUND.
   - 2+ matches -> exit MATCH_AMBIGUOUS with all matching line numbers.

NO word-level fuzziness, NO punctuation stripping, NO substring matching.
Plans frequently have near-duplicate criteria differing only by punctuation
or a single character (e.g., "Tests pass" vs "Tests pass."); fuzzy matching
would silently rewrite the wrong line and the LLM caller would never know.

Idempotent: ticking an already-ticked criterion is a no-op exit 0.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path

# Exit codes (also used as stderr tags for caller routing)
EXIT_OK = 0
EXIT_GENERAL = 1
EXIT_MATCH_FAILURE = 2

# Failure mode tags echoed to stderr (caller greps these to pick the right
# manual-review comment template).
TAG_MATCH_AMBIGUOUS = "MATCH_AMBIGUOUS"
TAG_MATCH_NOT_FOUND = "MATCH_NOT_FOUND"
TAG_MATCH_AMBIGUOUS_SECTION = "MATCH_AMBIGUOUS_SECTION"
TAG_NO_CRITERIA_SECTION = "NO_CRITERIA_SECTION"
TAG_MISSING_FILE = "MISSING_FILE"
TAG_EMPTY_CRITERION = "EMPTY_CRITERION"
TAG_MALFORMED_PLAN = "MALFORMED_PLAN"

SECTION_HEADING_RE = re.compile(r"^##\s+(Acceptance Criteria|Success Criteria)\s*$")
NEXT_SECTION_RE = re.compile(r"^##\s")
CHECKBOX_LINE_RE = re.compile(r"^([ \t]*-\s\[)([ x])(\]\s)(.+)$")


def _normalize(text: str) -> str:
    """Collapse runs of whitespace and strip leading/trailing whitespace.

    The match contract is whitespace-normalized but case-sensitive and
    otherwise exact. NO word-level fuzziness, NO punctuation stripping.
    """
    return re.sub(r"\s+", " ", text.strip())


def _find_criteria_section(lines: list[str]) -> tuple[int, int, str] | tuple[None, None, str]:
    """Locate the criteria section.

    Returns ``(start_idx, end_idx, matched_heading)`` where ``start_idx`` is the
    index of the ``##`` heading line and ``end_idx`` is one past the last line of
    the section (exclusive).

    On failure returns ``(None, None, tag)`` where ``tag`` is one of
    ``MATCH_AMBIGUOUS_SECTION`` (both headings present) or
    ``NO_CRITERIA_SECTION`` (neither heading present).
    """
    matches: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = SECTION_HEADING_RE.match(line)
        if m:
            matches.append((i, m.group(1)))

    if not matches:
        return None, None, TAG_NO_CRITERIA_SECTION

    if len(matches) > 1:
        # Both headings — caller must disambiguate manually.
        return None, None, TAG_MATCH_AMBIGUOUS_SECTION

    start_idx, heading = matches[0]
    # Find next ## heading after start_idx
    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        if NEXT_SECTION_RE.match(lines[j]):
            end_idx = j
            break
    return start_idx, end_idx, heading


def _extract_criteria(lines: list[str], start: int, end: int) -> list[tuple[int, str, bool]]:
    """Extract checkbox items in the criteria section.

    Returns a list of ``(line_index, original_text, checked)`` tuples.
    The text portion is the literal text after the ``] `` delimiter (NOT yet
    normalized — normalization happens at compare time so we preserve the
    original line exactly when we do not rewrite it).
    """
    items: list[tuple[int, str, bool]] = []
    for idx in range(start + 1, end):
        m = CHECKBOX_LINE_RE.match(lines[idx])
        if not m:
            continue
        checked = m.group(2) == "x"
        text = m.group(4)
        items.append((idx, text, checked))
    return items


def _flip_checkbox(line: str, target_checked: bool) -> str:
    """Return ``line`` with its checkbox state set to ``target_checked``.

    Caller has already verified ``line`` matches CHECKBOX_LINE_RE.
    """
    m = CHECKBOX_LINE_RE.match(line)
    if not m:  # defensive — caller pre-checks
        return line
    new_state = "x" if target_checked else " "
    return f"{m.group(1)}{new_state}{m.group(3)}{m.group(4)}"


def _emit(tag: str, message: str) -> None:
    """Emit a tagged failure message to stderr."""
    sys.stderr.write(f"{tag}: {message}\n")


def cmd_status(plan_path: Path) -> int:
    """Emit JSON describing the criteria section's current state."""
    if not plan_path.is_file():
        _emit(TAG_MISSING_FILE, f"plan file not found: {plan_path}")
        return EXIT_MATCH_FAILURE

    text = plan_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    start, end, heading_or_tag = _find_criteria_section(lines)
    if start is None:
        # heading_or_tag is one of NO_CRITERIA_SECTION / MATCH_AMBIGUOUS_SECTION
        if heading_or_tag == TAG_MATCH_AMBIGUOUS_SECTION:
            # Re-scan to surface line numbers for both headings.
            ambiguous = [i + 1 for i, ln in enumerate(lines) if SECTION_HEADING_RE.match(ln)]
            _emit(
                TAG_MATCH_AMBIGUOUS_SECTION,
                f"both Acceptance and Success Criteria headings present at lines {ambiguous}",
            )
        else:
            _emit(TAG_NO_CRITERIA_SECTION, "no Acceptance Criteria or Success Criteria section")
        return EXIT_MATCH_FAILURE

    items = _extract_criteria(lines, start, end)
    payload = {
        "matched_heading": heading_or_tag,
        "criteria": [
            {"criterion": _normalize(t), "checked": checked, "line": idx + 1}
            for (idx, t, checked) in items
        ],
    }
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    return EXIT_OK


def _write_checkbox(plan_path: Path, criterion: str, target_checked: bool) -> int:
    """Implementation shared by `tick` and `untick` subcommands."""
    if not plan_path.is_file():
        _emit(TAG_MISSING_FILE, f"plan file not found: {plan_path}")
        return EXIT_MATCH_FAILURE

    if not criterion or not criterion.strip():
        _emit(TAG_EMPTY_CRITERION, "criterion text is empty or whitespace")
        return EXIT_MATCH_FAILURE

    try:
        text = plan_path.read_text(encoding="utf-8")
    except OSError as exc:
        _emit(TAG_MALFORMED_PLAN, f"could not read plan file: {exc}")
        return EXIT_MATCH_FAILURE

    lines = text.splitlines(keepends=False)
    # Preserve the original final-newline state.
    has_trailing_newline = text.endswith("\n")

    start, end, heading_or_tag = _find_criteria_section(lines)
    if start is None:
        if heading_or_tag == TAG_MATCH_AMBIGUOUS_SECTION:
            ambiguous = [i + 1 for i, ln in enumerate(lines) if SECTION_HEADING_RE.match(ln)]
            _emit(
                TAG_MATCH_AMBIGUOUS_SECTION,
                f"both Acceptance and Success Criteria headings present at lines {ambiguous}",
            )
        else:
            _emit(
                TAG_NO_CRITERIA_SECTION,
                "no Acceptance Criteria or Success Criteria section in plan",
            )
        return EXIT_MATCH_FAILURE

    items = _extract_criteria(lines, start, end)
    if not items:
        # Empty criteria section is a no-op success — same as already-in-target state.
        sys.stderr.write("INFO: criteria section is empty; no-op\n")
        return EXIT_OK

    target_norm = _normalize(criterion)
    matches: list[int] = [idx for (idx, raw, _checked) in items if _normalize(raw) == target_norm]

    if len(matches) == 0:
        _emit(TAG_MATCH_NOT_FOUND, f'no criterion matches: "{criterion}"')
        return EXIT_MATCH_FAILURE

    if len(matches) > 1:
        line_numbers = [idx + 1 for idx in matches]
        _emit(
            TAG_MATCH_AMBIGUOUS,
            f'multiple criteria match "{criterion}" at lines {line_numbers}',
        )
        return EXIT_MATCH_FAILURE

    target_idx = matches[0]
    new_line = _flip_checkbox(lines[target_idx], target_checked)
    if new_line == lines[target_idx]:
        # Already in target state — idempotent success.
        return EXIT_OK

    lines[target_idx] = new_line
    new_text = "\n".join(lines) + ("\n" if has_trailing_newline else "")
    plan_path.write_text(new_text, encoding="utf-8")
    return EXIT_OK


def cmd_tick(plan_path: Path, criterion: str) -> int:
    return _write_checkbox(plan_path, criterion, target_checked=True)


def cmd_untick(plan_path: Path, criterion: str) -> int:
    return _write_checkbox(plan_path, criterion, target_checked=False)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.plan_checkbox_writer",
        description=(
            "Tick / untick a single criterion in a plan's Acceptance Criteria or "
            "Success Criteria section. Used by /do-pr-review and /do-patch."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_tick = sub.add_parser("tick", help="Mark criterion as [x]")
    p_tick.add_argument("plan", type=Path)
    p_tick.add_argument("--criterion", required=True)

    p_untick = sub.add_parser("untick", help="Mark criterion as [ ]")
    p_untick.add_argument("plan", type=Path)
    p_untick.add_argument("--criterion", required=True)

    p_status = sub.add_parser("status", help="Print JSON of all criteria")
    p_status.add_argument("plan", type=Path)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "tick":
        return cmd_tick(args.plan, args.criterion)
    if args.command == "untick":
        return cmd_untick(args.plan, args.criterion)
    if args.command == "status":
        return cmd_status(args.plan)
    parser.error(f"unknown command: {args.command}")
    return EXIT_GENERAL  # unreachable but keeps type checkers happy


if __name__ == "__main__":
    raise SystemExit(main())
