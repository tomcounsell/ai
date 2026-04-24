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
    if data.get("schema_version") == SCHEMA_VERSION and isinstance(
        data.get("tests"), dict
    ):
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
