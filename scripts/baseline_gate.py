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
    failing_node_ids,
    parse_junitxml,
)

logger = logging.getLogger(__name__)

STALENESS_THRESHOLD = timedelta(days=14)

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


def format_staleness_warning(baseline: dict, now: datetime | None = None) -> str | None:
    """Return a warning string if the baseline is stale, or ``None``.

    Three triggers:
    - ``bootstrap: true``                   -- always warn
    - ``commit`` ends with ``-dirty``       -- always warn
    - ``generated_at`` more than 14 days    -- warn with age in days
    """
    now = now or datetime.now(UTC)
    reasons: list[str] = []

    if baseline.get("bootstrap") is True:
        reasons.append("baseline is a bootstrap (single-run heuristic)")

    commit = baseline.get("commit")
    if isinstance(commit, str) and commit.endswith("-dirty"):
        reasons.append(f"baseline captured against a dirty tree ({commit})")

    generated_at_raw = baseline.get("generated_at")
    if isinstance(generated_at_raw, str):
        try:
            generated_at = datetime.fromisoformat(generated_at_raw)
        except ValueError:
            generated_at = None
        if generated_at is not None:
            if generated_at.tzinfo is None:
                generated_at = generated_at.replace(tzinfo=UTC)
            age = now - generated_at
            if age > STALENESS_THRESHOLD:
                days = age.days
                reasons.append(f"generated_at is {days} days old (> 14)")

    if not reasons:
        return None

    detail = "; ".join(reasons)
    return (
        f"WARNING: baseline is stale ({detail}). Consider running "
        "`python scripts/refresh_test_baseline.py`."
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    baseline = load_baseline(args.baseline)

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
    if args.now:
        now = datetime.fromisoformat(args.now)
        # Normalise a naive ISO string (e.g. "2026-04-24T12:00:00") to UTC.
        # Without this, ``format_staleness_warning`` compares a naive ``now``
        # against a tz-aware ``generated_at`` and raises ``TypeError: can't
        # subtract offset-naive and offset-aware datetimes``.
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
    else:
        now = datetime.now(UTC)
    warning = format_staleness_warning(baseline, now=now)
    if warning is not None:
        verdict["staleness_warning"] = warning
        sys.stderr.write(warning + "\n")

    sys.stdout.write(json.dumps(verdict, indent=2, sort_keys=True) + "\n")

    if verdict["new_blocking_regressions"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
