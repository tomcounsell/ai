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
import sys

# The two lines of the terminal completion fence, in order (penultimate, then last).
FENCE_DELIMITER = "<<<CRITIQUE-RESULT-COMPLETE>>>"
FENCE_STATUS = "STATUS: COMPLETED"


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


def _is_member_completed(run_dir: str, name: str) -> bool:
    """True IFF ``{name}.result.md`` exists in ``run_dir`` and passes the terminal fence."""
    result_path = os.path.join(run_dir, f"{name}.result.md")
    if not os.path.isfile(result_path):
        return False
    try:
        with open(result_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False
    return _has_terminal_fence(text)


def evaluate(run_dir: str) -> tuple[dict, int]:
    """Evaluate the roster gate for ``run_dir``.

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

    present: list[str] = []
    missing: list[str] = []
    for name in roster:
        if _is_member_completed(run_dir, name):
            present.append(name)
        else:
            missing.append(name)

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
    args = parser.parse_args(argv)

    decision, exit_code = evaluate(args.run_dir)
    print(json.dumps(decision))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
