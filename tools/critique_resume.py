"""Find a reusable incomplete critique run directory for a given plan.

Reuse contract
--------------
A critique run directory is **reusable** when ALL of the following hold:

1. Its ``.plan_hash`` file matches ``compute_plan_hash(plan_path)`` — the plan
   has not changed since that run was started.
2. The roster gate (``evaluate(run_dir)``) reports ``complete=False`` — the run
   is still in progress, not finished.
3. No ``"error"`` key in the gate decision — the roster manifest is valid.

Stale-guard
-----------
If a candidate directory's ``.plan_hash`` does *not* match the current plan hash,
it is stale (the plan changed).  Its path is printed to stderr so the calling
skill can remove it.  It is never returned as a reusable candidate.

Complete-guard
--------------
If ``evaluate()`` reports ``complete=True``, the run is finished and must NOT be
resumed.  It is silently skipped.

When ``compute_plan_hash`` returns ``None`` (unreadable plan file), every
candidate is treated as non-matching and the probe exits 1 without crashing.

Exit codes
----------
- **0** — a reusable directory was found; its path is printed to stdout.
- **1** — no reusable directory found; stdout is empty.

The probe never crashes on malformed files, missing directories, or partial
write states — all such cases map to "not reusable".
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from tools.critique_roster_check import evaluate
from tools.sdlc_verdict import compute_plan_hash


def _parse_timestamp_suffix(dir_name: str) -> str:
    """Return the trailing timestamp token from a run-dir name for sorting.

    Run dirs are named ``{issue-or-slug}-{timestamp}`` where the timestamp is
    typically an ISO-8601 string like ``20260101T120000``.  We extract the part
    after the first ``-`` and use it as a lexicographic sort key (newest =
    largest string when timestamps are fixed-width ISO).

    If the directory name has no ``-``, return the full name so the sort is at
    least deterministic.
    """
    idx = dir_name.find("-")
    if idx == -1:
        return dir_name
    return dir_name[idx + 1 :]


def find_reusable_run(
    plan_path: str,
    prefix: str,
    base_dir: str = ".critique-runs",
) -> str | None:
    """Return the path to the newest reusable run dir, or None.

    Stale candidates (plan_hash mismatch) are printed to stderr.

    Args:
        plan_path: Path to the plan file whose hash we compare against.
        prefix: The ``{issue-or-slug}`` prefix that run dirs start with.
        base_dir: Directory containing all per-run subdirectories.

    Returns:
        Absolute (or as-given) path string of a reusable dir, or None.
    """
    want_hash = compute_plan_hash(plan_path)

    base = Path(base_dir)
    if not base.is_dir():
        return None

    # Gather candidates: dirs starting with prefix + "-"
    pattern = re.compile(r"^" + re.escape(prefix) + r"-")
    candidates = [d for d in base.iterdir() if d.is_dir() and pattern.match(d.name)]

    # Sort newest-first by the trailing timestamp suffix (lexicographic desc)
    candidates.sort(key=lambda d: _parse_timestamp_suffix(d.name), reverse=True)

    for candidate in candidates:
        run_dir = str(candidate)

        # --- Read .plan_hash ---
        hash_file = candidate / ".plan_hash"
        try:
            stored_hash = hash_file.read_text(encoding="utf-8").strip()
        except Exception:
            # Missing or unreadable .plan_hash → skip (not stale, just unknown)
            continue

        # --- Hash comparison ---
        if want_hash is None or stored_hash != want_hash:
            # Stale: plan changed (or we can't compute the current hash)
            print(run_dir, file=sys.stderr)
            continue

        # --- Gate check ---
        try:
            decision, _rc = evaluate(run_dir)
        except Exception:
            # evaluate() should never raise, but be defensive
            continue

        if decision.get("error"):
            # Malformed roster — not safely reusable
            continue

        if decision.get("complete"):
            # Already finished — don't resume a completed run
            continue

        # Passes all guards → reusable
        return run_dir

    return None


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``critique-resume-probe``.

    Exits 0 and prints the reusable run dir to stdout if found.
    Exits 1 with empty stdout if no reusable run exists.
    """
    parser = argparse.ArgumentParser(
        prog="critique-resume-probe",
        description=(
            "Find a reusable incomplete critique run directory for a given plan. "
            "Prints the directory path to stdout and exits 0 if found; "
            "exits 1 with empty stdout if no reusable run exists. "
            "Stale dirs (plan changed) are printed to stderr for GC by the caller."
        ),
    )
    parser.add_argument(
        "--plan",
        required=True,
        metavar="PATH",
        help="Path to the plan file to hash-match against stored run dirs.",
    )

    id_group = parser.add_mutually_exclusive_group(required=True)
    id_group.add_argument(
        "--issue",
        metavar="N",
        help="GitHub issue number that prefixes the run directory names.",
    )
    id_group.add_argument(
        "--slug",
        metavar="S",
        help="Plan slug that prefixes the run directory names.",
    )

    parser.add_argument(
        "--base-dir",
        default=".critique-runs",
        metavar="DIR",
        help="Directory containing all per-run critique subdirectories (default: .critique-runs).",
    )

    args = parser.parse_args(argv)

    prefix = args.issue if args.issue is not None else args.slug

    result = find_reusable_run(
        plan_path=args.plan,
        prefix=str(prefix),
        base_dir=args.base_dir,
    )

    if result is not None:
        print(result)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
