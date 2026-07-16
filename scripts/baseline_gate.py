#!/usr/bin/env python3
"""Categorised merge-gate verdict for the Full Suite Gate in ``/do-merge``.

Given a PR junitxml file and the local ``data/main_test_baseline.json``,
compare PR failures against baseline categories and decide whether the
merge should be blocked.  The script prints a JSON verdict on stdout and
exits with:

- ``0`` when every PR failure is either absent from the baseline's blocking
  categories or already tracked as pre-existing
- ``1`` when at least one ``new_blocking_regression`` exists

The legacy flat shape ``{"failing_tests": [...]}`` is promoted to schema v2
in memory (every entry becomes ``category="real"``) so baselines written by
an older ``/do-merge`` continue to work.

See ``docs/features/merge-gate-baseline.md`` for the contract.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts._baseline_common import (
    CATEGORY_FLAKY,
    CATEGORY_HUNG,
    CATEGORY_IMPORT_ERROR,
    CATEGORY_REAL,
    SCHEMA_VERSION,
    VALID_CATEGORIES,
    JunitxmlParseError,
    expire_stale_flaky_entries,
    expire_stale_import_error_entries,
    failing_node_ids,
    parse_junitxml,
    read_envelope,
    staleness,
)

logger = logging.getLogger(__name__)

STALENESS_THRESHOLD = timedelta(days=14)

# Distinct exit code for a --strict-freshness refusal: the gate declines to
# produce a verdict at all (never a false pre-existing/regression verdict).
# 0 = clean, 1 = regression/parse failure, 2 = argparse's own error exit.
EXIT_STRICT_REFUSAL = 3

# The exact regen command printed on a strict refusal.
REGEN_COMMAND = "python scripts/refresh_test_baseline.py --runs 3"

# Minimum usable runs a strict-mode artifact must have been built from.
# Mirrors refresh_test_baseline.MIN_USABLE_RUNS_FOR_FLAKY_DETECTION without
# importing it (that module shells out to pytest-collection helpers).
STRICT_MIN_RUNS = 2

# Commit-distance staleness trigger (issue #1965). The 14-day wall-clock
# threshold missed a real incident: a baseline only 7 days old but 425 commits
# behind HEAD silently produced 38 false-positive "new regression" flags,
# because failing tests accrue on ``main`` per-commit, not per-day. A baseline
# more than this many commits behind HEAD is flagged as stale regardless of
# its wall-clock age. Chosen conservatively: at this project's observed commit
# velocity (~60 commits/day) ~100 commits is well under two days of drift, and
# the post-merge baseline reset keeps a healthy baseline at/near HEAD so this
# does not fire on normal operation.
STALE_COMMIT_DISTANCE = 100

# Import-error fast-expiry window (issue #2004 Task 4). An import_error is a
# whole-module outage: either it is fixed within days or it masks every
# regression in that module. Much tighter than the general staleness rule
# (STALENESS_THRESHOLD / STALE_COMMIT_DISTANCE): past EITHER bound, the gate
# must never classify a failure as pre-existing via an import_error baseline
# entry. Thresholds live HERE (module constants, read lazily by
# scripts._baseline_common.expire_stale_import_error_entries) and are never
# stored in the artifact.
IMPORT_ERROR_MAX_AGE = timedelta(days=3)
IMPORT_ERROR_MAX_COMMIT_DISTANCE = 30

# Categories that block a merge when a PR introduces a node ID in them that
# is NOT in the baseline (or is in the baseline but as a *different* category).
BLOCKING_CATEGORIES = frozenset({CATEGORY_REAL, CATEGORY_HUNG, CATEGORY_IMPORT_ERROR})


def load_baseline(path: str | Path) -> dict:
    """Load and normalise a baseline file to schema-v2 shape.

    Legacy shape (``{"failing_tests": [...]}`` with no ``schema_version``) is
    promoted in memory: each entry becomes ``{"category": "real",
    "fail_rate": 1.0, "hung_count": 0}``.  An empty file, ``{}``, or malformed
    JSON is treated as "no baseline" -- the caller (or ``/do-merge``'s
    bootstrap path) handles that case.

    Unrecognised ``schema_version`` values log a warning and return an empty
    baseline so the bootstrap path fires.
    """
    path_obj = Path(path)
    if not path_obj.exists():
        return {"schema_version": SCHEMA_VERSION, "tests": {}}

    try:
        raw = path_obj.read_text()
    except OSError as exc:
        logger.warning("[baseline_gate] could not read %s: %s", path_obj, exc)
        return {"schema_version": SCHEMA_VERSION, "tests": {}}

    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        logger.warning("[baseline_gate] malformed JSON in %s: %s", path_obj, exc)
        return {"schema_version": SCHEMA_VERSION, "tests": {}}

    if not isinstance(data, dict) or not data:
        return {"schema_version": SCHEMA_VERSION, "tests": {}}

    # Schema v2 -- already in canonical shape.
    if data.get("schema_version") == SCHEMA_VERSION and isinstance(data.get("tests"), dict):
        # Defensive: strip tests whose category is not valid.
        valid_tests: dict[str, dict] = {}
        for node_id, record in data["tests"].items():
            if isinstance(record, dict) and record.get("category") in VALID_CATEGORIES:
                valid_tests[node_id] = record
        data["tests"] = valid_tests
        return data

    # Legacy v1 shape -- promote in memory.
    if "failing_tests" in data and "schema_version" not in data:
        failing = data.get("failing_tests") or []
        return {
            "schema_version": SCHEMA_VERSION,
            "legacy_migrated": True,
            "tests": {
                node_id: {"category": CATEGORY_REAL, "fail_rate": 1.0, "hung_count": 0}
                for node_id in failing
            },
        }

    # Unknown shape (newer schema_version, corrupt, etc.) -- bootstrap.
    logger.warning(
        "[baseline_gate] unrecognised baseline shape in %s (schema_version=%r); treating as empty",
        path_obj,
        data.get("schema_version"),
    )
    return {"schema_version": SCHEMA_VERSION, "tests": {}}


def parse_pr_failures(junitxml_path: str | Path) -> set[str]:
    """Extract failing node IDs from a PR junitxml file.

    Raises :class:`JunitxmlParseError` if the file is missing or truncated --
    the caller treats this as a gate failure (safer than silently passing).
    """
    outcomes = parse_junitxml(junitxml_path)
    return failing_node_ids(outcomes)


def compute_gate_verdict(baseline: dict, pr_failures: set[str]) -> dict:
    """Return the structured verdict consumed by ``/do-merge``.

    Keys in the returned dict:
    - ``new_blocking_regressions``: node IDs not in baseline OR in a
      non-blocking baseline category but now failing outside that category
      (any PR failure whose node ID is NOT in the baseline at all is a new
      blocking regression; node IDs in the ``flaky`` bucket are allowed).
    - ``new_flaky_occurrences``: PR failures whose baseline category is
      ``flaky`` (reported, not blocked).
    - ``preexisting_failures_present``: count of PR failures in
      ``real``/``hung``/``import_error`` categories of the baseline.
    - ``baseline_keys_no_longer_failing``: advisory list of baseline-``real``
      node IDs that did NOT fail on the PR (suggests baseline refresh).
    """
    tests = baseline.get("tests", {})

    new_blocking_regressions: list[str] = []
    new_flaky_occurrences: list[str] = []
    preexisting_present: list[str] = []

    for node_id in sorted(pr_failures):
        record = tests.get(node_id)
        if record is None:
            new_blocking_regressions.append(node_id)
            continue
        category = record.get("category")
        if category == CATEGORY_FLAKY:
            new_flaky_occurrences.append(node_id)
        elif category in BLOCKING_CATEGORIES:
            preexisting_present.append(node_id)
        else:
            # Unknown category -- treat as new regression to fail closed.
            new_blocking_regressions.append(node_id)

    # Advisory: real-category baseline keys the PR did NOT fail -- hint to refresh.
    baseline_reals = {
        node_id
        for node_id, record in tests.items()
        if isinstance(record, dict) and record.get("category") == CATEGORY_REAL
    }
    no_longer_failing = sorted(baseline_reals - pr_failures)

    return {
        "new_blocking_regressions": new_blocking_regressions,
        "new_flaky_occurrences": new_flaky_occurrences,
        "preexisting_failures_present": len(preexisting_present),
        "preexisting_failures": preexisting_present,
        "baseline_keys_no_longer_failing": no_longer_failing,
    }


def commits_behind_head(
    baseline_commit: str | None,
    repo_root: str | Path | None = None,
) -> int | None:
    """Return how many commits ``HEAD`` is ahead of ``baseline_commit``.

    Best-effort and never raises: returns ``None`` when the answer cannot be
    determined (git unavailable, the recorded commit is missing/unknown, or it
    is not an ancestor reachable from ``HEAD``). Any ``-dirty`` suffix written
    by ``refresh_test_baseline.capture_commit`` is stripped before comparison.

    The count comes from ``git rev-list --count <commit>..HEAD``, i.e. the
    number of commits on ``HEAD`` not reachable from the baseline's commit.
    ``0`` means the baseline is at (or newer than) ``HEAD``.
    """
    if not isinstance(baseline_commit, str):
        return None
    sha = baseline_commit.strip()
    if sha.endswith("-dirty"):
        sha = sha[: -len("-dirty")]
    # ``capture_commit`` writes the literal "unknown" when git is unavailable;
    # treat that (and empty) as "no usable commit".
    if not sha or sha == "unknown":
        return None

    cwd = str(repo_root) if repo_root is not None else None
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{sha}..HEAD"],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, ValueError):
        return None
    if result.returncode != 0:
        # Unknown commit, not a git repo, etc.
        return None
    out = result.stdout.strip()
    try:
        return int(out)
    except ValueError:
        return None


def format_staleness_warning(
    baseline: dict,
    now: datetime | None = None,
    commits_behind: int | None = None,
) -> str | None:
    """Return a warning string if the baseline is stale, or ``None``.

    Four triggers:
    - ``bootstrap: true``                        -- always warn
    - ``commit`` ends with ``-dirty``            -- always warn
    - ``generated_at`` more than 14 days         -- warn with age in days
    - ``commits_behind`` past ``STALE_COMMIT_DISTANCE`` -- warn with distance

    ``commits_behind`` is the caller-supplied commit distance between the
    baseline's recorded commit and current ``HEAD`` (see
    :func:`commits_behind_head`). It is a separate axis from wall-clock age:
    a baseline can be time-fresh yet many commits behind on a high-velocity
    day, which is the exact blind spot the age check missed in issue #1965.
    ``None`` (git unavailable / unknown commit) means "skip this trigger".
    """
    reasons: list[str] = []

    if baseline.get("bootstrap") is True:
        reasons.append("baseline is a bootstrap (single-run heuristic)")

    # Age / dirty-commit / commit-distance triggers come from the ONE shared
    # staleness definition in _baseline_common (also used by the weekly
    # reflection), which reads this module's threshold constants at call time.
    reasons.extend(staleness(read_envelope(baseline), now=now, commits_behind=commits_behind))

    if not reasons:
        return None

    detail = "; ".join(reasons)
    return (
        f"WARNING: baseline is stale ({detail}). Refresh it with the timeout-safe launcher "
        "`scripts/refresh_baseline_detached.sh` (wraps `refresh_test_baseline.py`; a foreground "
        "run is killed at the 10-min bash cap — issue #2066)."
    )


# ---------------------------------------------------------------------------
# Decay + flake-tracking helpers (item 4 of sdlc-1155)
#
# Consumes the advisory ``baseline_keys_no_longer_failing`` list already
# emitted by ``compute_gate_verdict`` to age out stale ``real`` entries
# across multiple clean merges, and emits a quarantine hint when the same
# test appears in ``new_flaky_occurrences`` across several gate runs.
# Both trackers live inside ``main_test_baseline.json`` as optional
# top-level fields (``_decay_tracker`` and ``_flake_tracker``) so existing
# baselines without the fields keep working (treated as fresh).
# ---------------------------------------------------------------------------

DEFAULT_DECAY_THRESHOLD = 5
DEFAULT_FLAKE_THRESHOLD = 3


def _coerce_tracker(raw: object) -> dict[str, dict]:
    """Coerce an arbitrary value to a ``{test_id: {...}}`` dict.

    Malformed input (non-dict, dict with non-dict values) collapses to an
    empty tracker. Never raises.
    """
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, dict] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        if not isinstance(value, dict):
            continue
        cleaned[key] = dict(value)
    return cleaned


def apply_decay(
    baseline: dict,
    pr_failures: set | list,
    threshold: int = DEFAULT_DECAY_THRESHOLD,
) -> dict:
    """Return a new baseline dict with decay-tracker counters applied.

    Increments ``_decay_tracker[test_id].recent_pass_count`` for every
    ``real``-category baseline entry NOT present in ``pr_failures``. Entries
    whose counter reaches ``threshold`` are removed from ``tests`` and from
    ``_decay_tracker``. Tests that fail on the PR have their counter reset
    to 0. Orphan tracker entries (IDs that no longer exist in ``tests``)
    are garbage-collected on every call. The same GC rule applies to
    ``_flake_tracker`` so both trackers stay in sync with ``tests``.

    The function returns a NEW dict; it does not mutate ``baseline``.
    """
    if not isinstance(baseline, dict):
        return {}
    failures = set(pr_failures or [])
    threshold = (
        int(threshold) if isinstance(threshold, int) and threshold > 0 else DEFAULT_DECAY_THRESHOLD
    )

    tests_in = baseline.get("tests") or {}
    if not isinstance(tests_in, dict):
        tests_in = {}

    tracker = _coerce_tracker(baseline.get("_decay_tracker"))
    flake_tracker = _coerce_tracker(baseline.get("_flake_tracker"))

    new_tests: dict[str, dict] = {}
    new_tracker: dict[str, dict] = {}
    for node_id, record in tests_in.items():
        if not isinstance(record, dict):
            new_tests[node_id] = record
            continue
        category = record.get("category")
        if category != CATEGORY_REAL:
            new_tests[node_id] = record
            # Preserve trackers for non-real entries only if they're present
            if node_id in tracker:
                new_tracker[node_id] = dict(tracker[node_id])
            continue
        entry = dict(tracker.get(node_id, {}))
        if node_id in failures:
            entry["recent_pass_count"] = 0
            new_tests[node_id] = record
            new_tracker[node_id] = entry
            continue
        count = int(entry.get("recent_pass_count", 0) or 0) + 1
        entry["recent_pass_count"] = count
        if count >= threshold:
            # Decay out — drop from both tests AND tracker.
            continue
        new_tests[node_id] = record
        new_tracker[node_id] = entry

    # Orphan GC for _flake_tracker: drop entries whose test_id is no longer in tests.
    new_flake_tracker = {
        node_id: dict(entry) for node_id, entry in flake_tracker.items() if node_id in new_tests
    }

    result = dict(baseline)
    result["tests"] = new_tests
    if new_tracker:
        result["_decay_tracker"] = new_tracker
    else:
        result.pop("_decay_tracker", None)
    if new_flake_tracker:
        result["_flake_tracker"] = new_flake_tracker
    else:
        result.pop("_flake_tracker", None)
    return result


def update_flake_tracker(
    flake_tracker: dict | None,
    pr_flaky_occurrences: list | set,
) -> dict:
    """Return a new ``_flake_tracker`` dict with consecutive-run counters updated.

    Tests present in ``pr_flaky_occurrences`` have their counter incremented;
    tests absent have their counter reset to 0 (and may be dropped entirely
    once the count is 0 to keep the tracker compact). Never mutates input.
    """
    tracker = _coerce_tracker(flake_tracker)
    occurrences = set(pr_flaky_occurrences or [])
    new_tracker: dict[str, dict] = {}
    for node_id, entry in tracker.items():
        if node_id in occurrences:
            count = int(entry.get("consecutive_flake_runs", 0) or 0) + 1
            new_tracker[node_id] = {**entry, "consecutive_flake_runs": count}
        else:
            # Reset counter to 0 — drop the entry entirely to keep it compact.
            continue
    for node_id in occurrences:
        if node_id not in new_tracker:
            # New occurrence or reset-then-reappear: start at 1
            existing = tracker.get(node_id, {})
            new_tracker[node_id] = {**existing, "consecutive_flake_runs": 1}
    return new_tracker


def format_quarantine_hints(
    flake_tracker: dict | None,
    pr_flaky_occurrences: list | set,
    threshold: int = DEFAULT_FLAKE_THRESHOLD,
) -> list[str]:
    """Return a list of quarantine-hint stderr strings.

    The tracker is expected to be the POST-update state (the caller should
    invoke :func:`update_flake_tracker` first). For each entry whose
    ``consecutive_flake_runs`` has reached ``threshold`` AND which appears
    in the current PR run (``pr_flaky_occurrences``), emit a deterministic
    greppable hint line. Malformed tracker entries are silently skipped.
    """
    tracker = _coerce_tracker(flake_tracker)
    occurrences = set(pr_flaky_occurrences or [])
    threshold = (
        int(threshold) if isinstance(threshold, int) and threshold > 0 else DEFAULT_FLAKE_THRESHOLD
    )
    hints: list[str] = []
    for node_id in sorted(occurrences):
        entry = tracker.get(node_id) or {}
        count = int(entry.get("consecutive_flake_runs", 0) or 0)
        if count >= threshold:
            hints.append(
                f"QUARANTINE_HINT: {node_id} flaked {count}/{threshold} consecutive runs; "
                f"consider @pytest.mark.flaky or file an issue."
            )
    return hints


def strict_freshness_reasons(
    baseline: dict,
    *,
    now: datetime | None = None,
    commits_behind: int | None = None,
) -> list[str]:
    """Return the reasons a --strict-freshness gate must refuse, or ``[]``.

    Strict refusal formula: ``envelope.degraded or envelope.runs < 2 or
    staleness(envelope)``.  Envelope fields are read defensively -- an
    artifact predating envelope stamping (absent fields) logs a legacy-mode
    warning and fails closed (``runs`` absent counts as 0), never crashes.
    """
    envelope = read_envelope(baseline)
    reasons: list[str] = []

    if envelope.is_legacy:
        logger.warning(
            "[baseline_gate] artifact has no envelope fields (legacy, pre-#2004); "
            "strict freshness treats missing runs as 0"
        )

    if envelope.degraded:
        reasons.append(f"artifact is stamped degraded (runs={envelope.runs})")

    runs = envelope.runs if isinstance(envelope.runs, int) else 0
    if runs < STRICT_MIN_RUNS:
        reasons.append(f"artifact was built from {runs} usable run(s) (< {STRICT_MIN_RUNS})")

    reasons.extend(staleness(envelope, now=now, commits_behind=commits_behind))
    return reasons


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare PR junitxml against the merge-gate baseline.",
    )
    parser.add_argument(
        "--pr-junitxml",
        required=True,
        help="Path to pytest junitxml from the PR branch run.",
    )
    parser.add_argument(
        "--baseline",
        default="data/main_test_baseline.json",
        help="Path to main_test_baseline.json (default: data/main_test_baseline.json).",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="ISO-8601 timestamp for staleness comparison (testing hook).",
    )
    parser.add_argument(
        "--strict-freshness",
        action="store_true",
        help=(
            "Refuse to gate (exit 3) when the baseline envelope is degraded, "
            "built from fewer than 2 usable runs, or stale -- instead of "
            "producing a possibly-false verdict. Off by default."
        ),
    )
    parser.add_argument(
        "--pr-number",
        type=int,
        default=None,
        help=(
            "PR number for the strict-freshness break-glass check: refusal is "
            "skipped when data/merge_authorized_{pr_number} exists."
        ),
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory holding merge_authorized_{N} sentinels (default: data).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    baseline = load_baseline(args.baseline)

    if args.now:
        now = datetime.fromisoformat(args.now)
        # Normalise a naive ISO string (e.g. "2026-04-24T12:00:00") to UTC.
        # Without this, staleness comparison subtracts a naive ``now`` from a
        # tz-aware ``generated_at`` and raises ``TypeError: can't subtract
        # offset-naive and offset-aware datetimes``.
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
    else:
        now = datetime.now(UTC)
    commits_behind = commits_behind_head(baseline.get("commit"))

    if args.strict_freshness:
        refusal_reasons = strict_freshness_reasons(baseline, now=now, commits_behind=commits_behind)
        if refusal_reasons:
            sentinel = (
                Path(args.data_dir) / f"merge_authorized_{args.pr_number}"
                if args.pr_number is not None
                else None
            )
            if sentinel is not None and sentinel.exists():
                sys.stderr.write(
                    f"break-glass sentinel {sentinel} present; skipping strict-freshness refusal\n"
                )
            else:
                sys.stdout.write(
                    json.dumps(
                        {
                            "strict_freshness_refused": True,
                            "reasons": refusal_reasons,
                            "regen_command": REGEN_COMMAND,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                )
                sys.stderr.write(
                    "REFUSED: baseline fails strict freshness ("
                    + "; ".join(refusal_reasons)
                    + f"). Regenerate with: {REGEN_COMMAND}\n"
                )
                return EXIT_STRICT_REFUSAL

    # Flaky decay: a stale envelope expires flaky allowances so they never
    # ride in the baseline forever. Legacy artifacts (no envelope) keep
    # their entries -- there is no freshness signal to expire against.
    baseline, expired_flaky = expire_stale_flaky_entries(
        baseline, now=now, commits_behind=commits_behind
    )
    if expired_flaky:
        sys.stderr.write(
            f"WARNING: expired {len(expired_flaky)} stale flaky baseline entr"
            f"{'y' if len(expired_flaky) == 1 else 'ies'} "
            f"(envelope is stale). Regenerate with: {REGEN_COMMAND}\n"
        )

    # Import-error fast-expiry: past 3 days / 30 commits (module constants
    # above), an import_error entry must never classify a failure as
    # pre-existing. Legacy artifacts (no envelope) keep existing behavior.
    baseline, expired_import_errors = expire_stale_import_error_entries(
        baseline, now=now, commits_behind=commits_behind
    )
    if expired_import_errors:
        sys.stderr.write(
            f"WARNING: expired {len(expired_import_errors)} stale import_error baseline entr"
            f"{'y' if len(expired_import_errors) == 1 else 'ies'} "
            f"(envelope past {IMPORT_ERROR_MAX_AGE.days} days / "
            f"{IMPORT_ERROR_MAX_COMMIT_DISTANCE} commits). "
            f"Regenerate with: {REGEN_COMMAND}\n"
        )

    try:
        pr_failures = parse_pr_failures(args.pr_junitxml)
    except JunitxmlParseError as exc:
        logger.error("[baseline_gate] cannot parse PR junitxml: %s", exc)
        # Fail closed -- if we cannot read the PR's failures we cannot vouch
        # for the gate.
        sys.stdout.write(
            json.dumps(
                {
                    "error": str(exc),
                    "new_blocking_regressions": [],
                    "new_flaky_occurrences": [],
                    "preexisting_failures_present": 0,
                    "preexisting_failures": [],
                    "baseline_keys_no_longer_failing": [],
                }
            )
            + "\n"
        )
        return 1

    verdict = compute_gate_verdict(baseline, pr_failures)
    verdict["expired_flaky_entries"] = expired_flaky
    verdict["expired_import_error_entries"] = expired_import_errors
    warning = format_staleness_warning(baseline, now=now, commits_behind=commits_behind)
    if warning is not None:
        verdict["staleness_warning"] = warning
        sys.stderr.write(warning + "\n")

    sys.stdout.write(json.dumps(verdict, indent=2, sort_keys=True) + "\n")

    if verdict["new_blocking_regressions"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
