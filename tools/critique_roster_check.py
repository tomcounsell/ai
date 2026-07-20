"""Roster membership gate for the /do-plan-critique war-room barrier (issue #1690).

A critique run must NOT record a verdict until every expected critic has written
a result artifact to the filesystem. This helper is that gate: it reads the frozen
roster manifest (`_roster.json`) for a run and checks, for each named roster member,
whether `{name}.result.md` exists AND carries a **terminal two-line completion fence**.
It then prints a JSON gate decision and exits 0 (complete) or non-zero (incomplete /
bad manifest), so the calling skill can branch on either the exit code or the JSON.

The barrier is verifiable from outside the LLM run — a human, this helper, or a test
can create/omit result files and observe the gate behave. It does not depend on the
driver "choosing" to await its background critics.

Terminal two-line completion fence
-----------------------------------
A member counts as "completed" IFF, looking at the file's last two **non-empty** lines
(after stripping trailing whitespace and dropping trailing blank lines), in order:

    <<<CRITIQUE-RESULT-COMPLETE>>>      <- penultimate non-empty line (the delimiter)
    STATUS: COMPLETED                   <- last non-empty line

Why a two-line *terminal* fence rather than a bare substring or a first-line marker:

1. Truncation guard. A marker written on line 1 (before the findings body) would let a
   critic that writes line 1 then crashes/truncates pass the gate with an empty or
   garbage body. Requiring the fence in **terminal** position means "fence present" ⇔
   "body fully written, then the critic deliberately stamped the fence as its final act."

2. Token-collision guard. The bare line `STATUS: COMPLETED` is forgeable: critics in
   this skill routinely *quote* that exact string while reviewing prose (this very plan
   contains it many times). A critic whose findings body merely ends on that quoted
   token — without the deliberate preceding delimiter — would pass a single-line gate
   with a partial body. The delimiter `<<<CRITIQUE-RESULT-COMPLETE>>>` is a token no
   critic emits in ordinary findings prose, so requiring it as the penultimate line
   makes the fence impossible to forge by quoted output.

Failure handling
----------------
The helper never crashes with an uncaught exception that could be mistaken for
"complete", and never prints `complete: true` on a bad manifest:

- Missing or unparseable `_roster.json` -> `{"complete": false, "error": "..."}`, exit 2.
- An empty roster (count 0 / empty list) -> `complete: false`, `roster_count: 0`,
  exit 1 (never a vacuous "0 of 0, proceed").
- Stray `{name}.result.md.tmp` files (an atomic write in progress) are ignored — only
  the canonical `{name}.result.md` is read.
- A result file for a name NOT in the manifest is ignored — it cannot inflate completion.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

# The two lines of the terminal completion fence, in order (penultimate, then last).
FENCE_DELIMITER = "<<<CRITIQUE-RESULT-COMPLETE>>>"
FENCE_STATUS = "STATUS: COMPLETED"

# WS-A grounding leg (issue #2124). A critic result file is "grounded" iff it
# quotes the real plan: it must share either a verbatim normalized substring of
# at least this many characters with the plan text, OR a plan section header.
# Provisional/tunable — bias LOW (accept real critiques) rather than high (which
# would false-refuse a critic that paraphrases). Override via env for tuning.
DEFAULT_MIN_GROUNDING_QUOTE_LEN = 24


def _min_grounding_quote_len() -> int:
    """Return the minimum verbatim-quote length for the grounding check.

    Env-overridable (`MIN_GROUNDING_QUOTE_LEN`); falls back to the provisional
    default on any unparseable value. Never returns < 1.
    """
    raw = os.environ.get("MIN_GROUNDING_QUOTE_LEN")
    if raw is None:
        return DEFAULT_MIN_GROUNDING_QUOTE_LEN
    try:
        return max(1, int(raw))
    except (ValueError, TypeError):
        return DEFAULT_MIN_GROUNDING_QUOTE_LEN


def _normalize(text: str) -> str:
    """Collapse runs of whitespace to a single space and casefold.

    Normalization makes the grounding substring/header match insensitive to
    reflowed whitespace and case, so a genuine quote survives copy/paste
    reformatting while a fabricated critique of a *different* plan still cannot
    collide with the real plan bytes.
    """
    return re.sub(r"\s+", " ", text).strip().casefold()


def _plan_section_headers(plan_text: str) -> set[str]:
    """Extract normalized markdown section-header texts (``## Header``) from the plan."""
    headers: set[str] = set()
    for line in plan_text.splitlines():
        m = re.match(r"^#{1,6}\s+(.*\S)\s*$", line)
        if m:
            norm = _normalize(m.group(1))
            if norm:
                headers.add(norm)
    return headers


def _strip_fence_lines(text: str) -> str:
    """Drop the two terminal fence lines so the fence itself can't count as grounding.

    Every critic appends `<<<CRITIQUE-RESULT-COMPLETE>>>` / `STATUS: COMPLETED`
    regardless of whether it read the plan. If a plan's own prose happens to
    contain those tokens (this very family of plans discusses them), a fabricated
    critique could otherwise "ground" itself purely on the fence. Removing the
    fence lines before the grounding check closes that loophole.
    """
    keep = [ln for ln in text.splitlines() if ln.rstrip() not in (FENCE_DELIMITER, FENCE_STATUS)]
    return "\n".join(keep)


def _is_grounded(result_text: str, plan_text: str, min_len: int | None = None) -> bool:
    """Return True iff ``result_text`` verifiably cites ``plan_text``.

    Grounded iff, after normalization and stripping the terminal fence lines, the
    result shares with the plan EITHER a section-header text OR a verbatim
    substring of length >= ``min_len``. A fork that reviewed a nonexistent plan
    cannot produce a substring that collides with the real plan bytes.

    Fails toward *ungrounded* (refusal) on empty/missing input — an empty plan or
    empty result is never silently "grounded".
    """
    if min_len is None:
        min_len = _min_grounding_quote_len()

    norm_plan = _normalize(plan_text)
    norm_result = _normalize(_strip_fence_lines(result_text))
    if not norm_plan or not norm_result:
        return False

    # Section-header citation (cheap, and robust to short but distinctive headers).
    for header in _plan_section_headers(plan_text):
        if len(header) >= 3 and header in norm_result:
            return True

    # Verbatim substring of length >= min_len, via a rolling n-gram set of the
    # plan (O(len_plan + len_result), not O(len_plan * len_result)).
    if len(norm_result) < min_len or len(norm_plan) < min_len:
        return False
    plan_ngrams = {norm_plan[i : i + min_len] for i in range(len(norm_plan) - min_len + 1)}
    for i in range(len(norm_result) - min_len + 1):
        if norm_result[i : i + min_len] in plan_ngrams:
            return True
    return False


def _last_two_nonempty_lines(text: str) -> list[str]:
    """Return the last two non-empty lines of ``text``.

    Each line has its trailing whitespace stripped; trailing empty lines are dropped
    before the last two are taken. Returns 0, 1, or 2 entries (fewer than two non-empty
    lines means the fence cannot be present).
    """
    nonempty = [line.rstrip() for line in text.splitlines() if line.rstrip()]
    return nonempty[-2:]


def _has_terminal_fence(text: str) -> bool:
    """True IFF the last two non-empty lines are the fence delimiter then the status line."""
    last_two = _last_two_nonempty_lines(text)
    return last_two == [FENCE_DELIMITER, FENCE_STATUS]


def _load_roster(run_dir: str) -> list[str]:
    """Load the frozen roster of expected critic names from ``_roster.json``.

    Tolerates either the canonical object shape ``{"roster": [...], "count": N}`` or a
    bare JSON list ``[...]`` (defensive). Raises ``ValueError`` on a missing file,
    unparseable JSON, or an unrecognized shape — the caller turns that into a loud,
    non-zero "incomplete" decision rather than a crash.
    """
    manifest_path = os.path.join(run_dir, "_roster.json")
    if not os.path.isfile(manifest_path):
        raise ValueError(f"roster manifest not found: {manifest_path}")
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"could not parse roster manifest {manifest_path}: {exc}") from exc

    if isinstance(data, dict):
        roster = data.get("roster")
        if roster is None:
            raise ValueError(f"roster manifest {manifest_path} has no 'roster' key")
    elif isinstance(data, list):
        roster = data
    else:
        raise ValueError(
            f"roster manifest {manifest_path} has unexpected shape: {type(data).__name__}"
        )

    if not isinstance(roster, list) or not all(isinstance(name, str) for name in roster):
        raise ValueError(f"roster in {manifest_path} must be a list of strings")
    return roster


def _member_status(run_dir: str, name: str, plan_text: str | None) -> tuple[bool, bool]:
    """Return ``(fenced, grounded)`` for ``{name}.result.md`` in ``run_dir``.

    ``fenced`` is True IFF the file exists and passes the terminal two-line fence.
    ``grounded`` is True IFF ``plan_text`` is None (grounding leg disabled — legacy
    behavior) OR the file's body verifiably cites the plan. When the file is
    missing/unreadable, both are False.
    """
    result_path = os.path.join(run_dir, f"{name}.result.md")
    if not os.path.isfile(result_path):
        return False, False
    try:
        with open(result_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False, False
    fenced = _has_terminal_fence(text)
    grounded = True if plan_text is None else _is_grounded(text, plan_text)
    return fenced, grounded


def _resolve_plan_text(plan_path: str | None, plan_text: str | None) -> str | None:
    """Resolve the plan text for the grounding leg.

    Returns None when the grounding leg is disabled (neither ``plan_path`` nor
    ``plan_text`` supplied) — legacy byte-identical behavior. Otherwise returns the
    plan text, or an EMPTY string when a supplied ``plan_path`` cannot be read: an
    empty plan grounds nothing, so every member fails the grounding check (the
    refusal direction), never a false "complete".
    """
    if plan_text is not None:
        return plan_text
    if plan_path is None:
        return None
    try:
        with open(plan_path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        # Plan unreadable -> empty text -> all members ungrounded (fail closed).
        return ""


def evaluate(
    run_dir: str,
    plan_path: str | None = None,
    plan_text: str | None = None,
) -> tuple[dict, int]:
    """Evaluate the roster gate for ``run_dir``.

    When ``plan_path`` or ``plan_text`` is supplied (WS-A, issue #2124), a member is
    "complete" only if it passes BOTH the terminal fence AND the grounding check
    (its body verifiably quotes the plan). An ungrounded-but-fenced member is
    treated exactly like a missing critic — reported in ``missing`` (and separately
    in ``ungrounded`` for diagnostics). When neither is supplied, behavior is
    byte-identical to the pre-#2124 fence-only gate (generic/foreign-repo safety).

    Returns ``(decision, exit_code)`` where ``decision`` is the JSON-serializable gate
    dict and ``exit_code`` is 0 (complete), 1 (incomplete roster), or 2 (bad manifest).
    """
    try:
        roster = _load_roster(run_dir)
    except ValueError as exc:
        return (
            {
                "complete": False,
                "missing": [],
                "present": [],
                "roster_count": 0,
                "completed_count": 0,
                "error": str(exc),
            },
            2,
        )

    resolved_plan = _resolve_plan_text(plan_path, plan_text)

    present: list[str] = []
    missing: list[str] = []
    ungrounded: list[str] = []
    for name in roster:
        fenced, grounded = _member_status(run_dir, name, resolved_plan)
        if fenced and grounded:
            present.append(name)
        else:
            missing.append(name)
            # Distinguish "fenced but did not cite the plan" for forensics: this is
            # the fabricated-critique signal WS-A exists to catch.
            if fenced and not grounded:
                ungrounded.append(name)

    roster_count = len(roster)
    completed_count = len(present)
    # Complete only when the roster is non-empty AND every member reported. An empty
    # roster is never a vacuous "0 of 0, proceed".
    complete = roster_count > 0 and completed_count == roster_count

    decision = {
        "complete": complete,
        "missing": missing,
        "present": present,
        "roster_count": roster_count,
        "completed_count": completed_count,
    }
    if resolved_plan is not None:
        decision["ungrounded"] = ungrounded
    return decision, (0 if complete else 1)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Prints the JSON gate decision to stdout and returns the exit code."""
    parser = argparse.ArgumentParser(
        prog="critique-roster-check",
        description=(
            "Roster membership gate for the /do-plan-critique war-room barrier. "
            "Checks every roster member in <run-dir>/_roster.json wrote a "
            "{name}.result.md ending in the terminal two-line completion fence."
        ),
    )
    parser.add_argument(
        "--run-dir",
        required=True,
        help="Path to the per-run critique directory containing _roster.json and result files.",
    )
    parser.add_argument(
        "--plan-path",
        default=None,
        help=(
            "Path to the plan document (WS-A, issue #2124). When supplied, each roster "
            "member must also carry a verifiable plan citation to count as complete — a "
            "fabricated critique that never quotes the real plan is treated as an "
            "incomplete member. Omit for the legacy fence-only gate."
        ),
    )
    args = parser.parse_args(argv)

    decision, exit_code = evaluate(args.run_dir, plan_path=args.plan_path)
    print(json.dumps(decision))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
