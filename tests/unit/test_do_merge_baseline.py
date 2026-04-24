"""Unit tests for ``scripts/baseline_gate.py`` (the Full Suite Gate logic).

Covers legacy-shape load, schema-v2 load, new-regression detection, flaky
pass-through, ``hung`` pass-through, staleness warnings (14-day-old
``generated_at``, ``bootstrap: true``, ``-dirty`` commit suffix), and the
PR #1054/#1070 count-coincident regression scenario.

See ``docs/plans/merge-gate-baseline-refresh.md`` for motivation.
"""

from __future__ import annotations

import json
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.baseline_gate import (
    compute_gate_verdict,
    format_staleness_warning,
    load_baseline,
    parse_pr_failures,
)

# ---------------------------------------------------------------------------
# load_baseline: legacy and v2 shapes
# ---------------------------------------------------------------------------


def test_load_baseline_promotes_legacy_flat_list_in_memory(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(
            {
                "failing_tests": [
                    "tests/unit/test_a.py::test_alpha",
                    "tests/unit/test_b.py::test_beta",
                ]
            }
        )
    )
    baseline = load_baseline(path)
    assert baseline["schema_version"] == 2
    assert baseline.get("legacy_migrated") is True
    tests = baseline["tests"]
    assert set(tests.keys()) == {
        "tests/unit/test_a.py::test_alpha",
        "tests/unit/test_b.py::test_beta",
    }
    for record in tests.values():
        assert record["category"] == "real"
        assert record["fail_rate"] == 1.0
        assert record["hung_count"] == 0


def test_load_baseline_returns_empty_on_missing_file(tmp_path: Path) -> None:
    baseline = load_baseline(tmp_path / "no-such.json")
    assert baseline["tests"] == {}


def test_load_baseline_returns_empty_on_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text("not json {")
    baseline = load_baseline(path)
    assert baseline["tests"] == {}


def test_load_baseline_strips_unknown_categories(tmp_path: Path) -> None:
    payload = {
        "schema_version": 2,
        "tests": {
            "tests/unit/test_a.py::test_alpha": {
                "category": "real",
                "fail_rate": 1.0,
                "hung_count": 0,
            },
            "tests/unit/test_a.py::test_garbage": {
                "category": "bogus",
                "fail_rate": 0.5,
                "hung_count": 0,
            },
        },
    }
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(payload))
    baseline = load_baseline(path)
    assert "tests/unit/test_a.py::test_alpha" in baseline["tests"]
    assert "tests/unit/test_a.py::test_garbage" not in baseline["tests"]


def test_load_baseline_schema_v2_passes_through(tmp_path: Path) -> None:
    payload = {
        "schema_version": 2,
        "generated_at": "2026-04-01T00:00:00+00:00",
        "runs": 3,
        "commit": "abc123",
        "tests": {
            "tests/unit/test_a.py::test_alpha": {
                "category": "flaky",
                "fail_rate": 0.33,
                "hung_count": 0,
            },
        },
    }
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(payload))
    baseline = load_baseline(path)
    assert baseline["schema_version"] == 2
    assert "legacy_migrated" not in baseline
    assert baseline["tests"]["tests/unit/test_a.py::test_alpha"]["category"] == "flaky"


# ---------------------------------------------------------------------------
# compute_gate_verdict
# ---------------------------------------------------------------------------


def _baseline(tests: dict[str, str]) -> dict:
    """Tiny fixture helper: ``{node_id: category}`` -> schema-v2 baseline."""
    return {
        "schema_version": 2,
        "tests": {
            node_id: {"category": cat, "fail_rate": 1.0, "hung_count": 0}
            for node_id, cat in tests.items()
        },
    }


def test_new_failure_not_in_baseline_is_blocking() -> None:
    baseline = _baseline({})
    verdict = compute_gate_verdict(baseline, {"tests/unit/test_a.py::test_new"})
    assert verdict["new_blocking_regressions"] == ["tests/unit/test_a.py::test_new"]


def test_flaky_baseline_entry_is_non_blocking() -> None:
    baseline = _baseline({"tests/unit/test_a.py::test_flaky": "flaky"})
    verdict = compute_gate_verdict(baseline, {"tests/unit/test_a.py::test_flaky"})
    assert verdict["new_blocking_regressions"] == []
    assert verdict["new_flaky_occurrences"] == ["tests/unit/test_a.py::test_flaky"]


def test_real_baseline_entry_is_preexisting_not_blocking() -> None:
    baseline = _baseline({"tests/unit/test_a.py::test_real": "real"})
    verdict = compute_gate_verdict(baseline, {"tests/unit/test_a.py::test_real"})
    assert verdict["new_blocking_regressions"] == []
    assert verdict["preexisting_failures_present"] == 1


def test_hung_baseline_entry_is_preexisting_not_blocking() -> None:
    """The ``hung`` category is treated as pre-existing (BLOCKER resolution verified)."""
    baseline = _baseline({"tests/integration/test_wedge.py::test_deadlock": "hung"})
    verdict = compute_gate_verdict(baseline, {"tests/integration/test_wedge.py::test_deadlock"})
    assert verdict["new_blocking_regressions"] == []
    assert verdict["preexisting_failures_present"] == 1


def test_import_error_baseline_entry_is_preexisting() -> None:
    baseline = _baseline({"tests/unit/test_broken.py": "import_error"})
    verdict = compute_gate_verdict(baseline, {"tests/unit/test_broken.py"})
    assert verdict["new_blocking_regressions"] == []
    assert verdict["preexisting_failures_present"] == 1


def test_count_coincident_regression_is_blocked() -> None:
    """Simulates the PR #1054/#1070 scenario.

    Baseline has two failures: one ``flaky``, one ``real``.  The PR failing
    set has the same count (2) but contains one brand-new node ID.
    ``comm -23`` on sorted name lists would have falsely passed this scenario
    because the SIZE of both sets is identical -- but the new gate compares
    identity per category and blocks the new node ID.
    """
    baseline = _baseline(
        {
            "tests/unit/test_flaky.py::test_A": "flaky",
            "tests/unit/test_real.py::test_B": "real",
        }
    )
    pr_failures = {
        "tests/unit/test_real.py::test_B",  # pre-existing real, allowed
        "tests/unit/test_new.py::test_C",  # brand-new regression, must block
    }
    verdict = compute_gate_verdict(baseline, pr_failures)
    assert verdict["new_blocking_regressions"] == ["tests/unit/test_new.py::test_C"]
    assert verdict["preexisting_failures_present"] == 1


def test_baseline_keys_no_longer_failing_flagged_as_advisory() -> None:
    baseline = _baseline(
        {
            "tests/unit/test_a.py::test_real_gone": "real",
            "tests/unit/test_a.py::test_real_still": "real",
        }
    )
    pr_failures = {"tests/unit/test_a.py::test_real_still"}
    verdict = compute_gate_verdict(baseline, pr_failures)
    assert verdict["baseline_keys_no_longer_failing"] == ["tests/unit/test_a.py::test_real_gone"]
    assert verdict["new_blocking_regressions"] == []


def test_multiple_flakies_and_one_new_regression_blocks_only_new() -> None:
    baseline = _baseline(
        {
            "tests/unit/test_a.py::test_flaky1": "flaky",
            "tests/unit/test_a.py::test_flaky2": "flaky",
            "tests/unit/test_a.py::test_real1": "real",
        }
    )
    pr_failures = {
        "tests/unit/test_a.py::test_flaky1",
        "tests/unit/test_a.py::test_real1",
        "tests/unit/test_a.py::test_new_regression",
    }
    verdict = compute_gate_verdict(baseline, pr_failures)
    assert verdict["new_blocking_regressions"] == ["tests/unit/test_a.py::test_new_regression"]
    assert verdict["new_flaky_occurrences"] == ["tests/unit/test_a.py::test_flaky1"]


# ---------------------------------------------------------------------------
# format_staleness_warning
# ---------------------------------------------------------------------------


def test_staleness_warning_fires_on_old_generated_at() -> None:
    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = {
        "schema_version": 2,
        "generated_at": (now - timedelta(days=20)).isoformat(),
        "commit": "abc1234",
        "tests": {},
    }
    warning = format_staleness_warning(baseline, now=now)
    assert warning is not None
    assert "20 days old" in warning
    assert "refresh_test_baseline.py" in warning


def test_staleness_warning_silent_under_threshold() -> None:
    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = {
        "schema_version": 2,
        "generated_at": (now - timedelta(days=3)).isoformat(),
        "commit": "abc1234",
        "tests": {},
    }
    assert format_staleness_warning(baseline, now=now) is None


def test_staleness_warning_fires_on_bootstrap_flag() -> None:
    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = {
        "schema_version": 2,
        "generated_at": now.isoformat(),
        "bootstrap": True,
        "commit": "abc1234",
        "tests": {},
    }
    warning = format_staleness_warning(baseline, now=now)
    assert warning is not None
    assert "bootstrap" in warning


def test_staleness_warning_fires_on_dirty_commit() -> None:
    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = {
        "schema_version": 2,
        "generated_at": now.isoformat(),
        "commit": "abc1234-dirty",
        "tests": {},
    }
    warning = format_staleness_warning(baseline, now=now)
    assert warning is not None
    assert "dirty tree" in warning


# ---------------------------------------------------------------------------
# parse_pr_failures (integration with parse_junitxml)
# ---------------------------------------------------------------------------


def test_main_normalises_naive_now_to_utc(tmp_path: Path) -> None:
    """A naive ``--now`` ISO string must not crash on tz-aware subtraction.

    Before the fix: ``datetime.fromisoformat("2026-04-20T00:00:00")`` is tz-naive;
    ``format_staleness_warning`` then compared it to a tz-aware ``generated_at``
    and raised ``TypeError: can't subtract offset-naive and offset-aware
    datetimes``.  After the fix ``main()`` attaches UTC to the naive value.
    """
    from scripts.baseline_gate import main

    # Build a baseline whose generated_at is 20 days before the naive --now.
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": "2026-04-01T00:00:00+00:00",
                "commit": "abc1234",
                "tests": {},
            }
        )
    )

    # Create an empty junitxml with no failures.
    pr_xml = tmp_path / "pr.xml"
    pr_xml.write_text(
        textwrap.dedent(
            """\
            <?xml version="1.0" encoding="utf-8"?>
            <testsuites>
              <testsuite name="pytest">
                <testcase classname="tests.unit.test_a" name="test_ok"/>
              </testsuite>
            </testsuites>
            """
        )
    )

    # The argument is deliberately tz-naive (no offset suffix).  Before the
    # fix this path raised TypeError inside format_staleness_warning.
    exit_code = main(
        [
            "--pr-junitxml",
            str(pr_xml),
            "--baseline",
            str(baseline_path),
            "--now",
            "2026-04-21T00:00:00",  # naive
        ]
    )
    assert exit_code == 0


def test_parse_pr_failures_returns_only_non_pass(tmp_path: Path) -> None:
    xml = textwrap.dedent(
        """\
        <?xml version="1.0" encoding="utf-8"?>
        <testsuites>
          <testsuite name="pytest">
            <testcase classname="tests.unit.test_a" name="test_ok"/>
            <testcase classname="tests.unit.test_a" name="test_bad">
              <failure message="AssertionError"/>
            </testcase>
          </testsuite>
        </testsuites>
        """
    )
    path = tmp_path / "pr.xml"
    path.write_text(xml)
    failing = parse_pr_failures(path)
    assert failing == {"tests/unit/test_a.py::test_bad"}


# ---------------------------------------------------------------------------
# apply_decay / update_flake_tracker / format_quarantine_hints (item 4 of sdlc-1155)
# ---------------------------------------------------------------------------

from scripts.baseline_gate import (  # noqa: E402
    DEFAULT_DECAY_THRESHOLD,
    DEFAULT_FLAKE_THRESHOLD,
    apply_decay,
    format_quarantine_hints,
    update_flake_tracker,
)


def _baseline_with(tests, decay_tracker=None, flake_tracker=None):
    return {
        "schema_version": 2,
        "tests": tests,
        **({"_decay_tracker": decay_tracker} if decay_tracker is not None else {}),
        **({"_flake_tracker": flake_tracker} if flake_tracker is not None else {}),
    }


def test_apply_decay_increments_counter_on_clean_merge():
    baseline = _baseline_with({"tests/a::t": {"category": "real"}})
    out = apply_decay(baseline, pr_failures=set(), threshold=5)
    assert out["_decay_tracker"]["tests/a::t"]["recent_pass_count"] == 1


def test_apply_decay_removes_entry_at_threshold():
    baseline = _baseline_with(
        {"tests/a::t": {"category": "real"}},
        decay_tracker={"tests/a::t": {"recent_pass_count": 4}},
    )
    out = apply_decay(baseline, pr_failures=set(), threshold=5)
    assert "tests/a::t" not in out["tests"]
    assert "_decay_tracker" not in out


def test_apply_decay_resets_counter_when_test_fails_again():
    baseline = _baseline_with(
        {"tests/a::t": {"category": "real"}},
        decay_tracker={"tests/a::t": {"recent_pass_count": 3}},
    )
    out = apply_decay(baseline, pr_failures={"tests/a::t"}, threshold=5)
    assert out["_decay_tracker"]["tests/a::t"]["recent_pass_count"] == 0


def test_update_flake_tracker_increments_on_repeat_occurrence():
    tracker = update_flake_tracker(
        {"tests/b::t": {"consecutive_flake_runs": 2}},
        pr_flaky_occurrences=["tests/b::t"],
    )
    assert tracker["tests/b::t"]["consecutive_flake_runs"] == 3


def test_update_flake_tracker_resets_when_absent():
    tracker = update_flake_tracker(
        {"tests/b::t": {"consecutive_flake_runs": 3}},
        pr_flaky_occurrences=[],
    )
    # Absent => removed (compact)
    assert "tests/b::t" not in tracker


def test_format_quarantine_hints_emits_at_threshold():
    hints = format_quarantine_hints(
        {"tests/b::t": {"consecutive_flake_runs": 3}},
        pr_flaky_occurrences=["tests/b::t"],
        threshold=3,
    )
    assert len(hints) == 1
    assert "QUARANTINE_HINT: tests/b::t flaked 3/3" in hints[0]


def test_format_quarantine_hints_below_threshold_emits_nothing():
    hints = format_quarantine_hints(
        {"tests/b::t": {"consecutive_flake_runs": 2}},
        pr_flaky_occurrences=["tests/b::t"],
        threshold=3,
    )
    assert hints == []


def test_format_quarantine_hints_malformed_tracker_returns_empty():
    assert format_quarantine_hints(None, ["any"], threshold=3) == []
    assert format_quarantine_hints({"x": "not-a-dict"}, ["x"], threshold=3) == []


def test_missing_decay_tracker_treated_as_fresh():
    baseline = _baseline_with({"tests/a::t": {"category": "real"}})
    out = apply_decay(baseline, pr_failures=set(), threshold=DEFAULT_DECAY_THRESHOLD)
    assert out["_decay_tracker"]["tests/a::t"]["recent_pass_count"] == 1


def test_orphan_tracker_entries_collected():
    """_decay_tracker entries whose test_id is absent from the baseline are
    dropped on the next apply_decay call (GC rule from the plan)."""
    baseline = _baseline_with(
        {"tests/still_here::t": {"category": "real"}},
        decay_tracker={
            "tests/still_here::t": {"recent_pass_count": 0},
            "tests/deleted::t": {"recent_pass_count": 2},
        },
        flake_tracker={
            "tests/deleted::t": {"consecutive_flake_runs": 5},
        },
    )
    out = apply_decay(baseline, pr_failures=set(), threshold=DEFAULT_DECAY_THRESHOLD)
    assert "tests/deleted::t" not in out.get("_decay_tracker", {})
    # _flake_tracker must also drop the orphan.
    assert "tests/deleted::t" not in out.get("_flake_tracker", {})


def test_threshold_defaults_are_documented():
    assert DEFAULT_DECAY_THRESHOLD == 5
    assert DEFAULT_FLAKE_THRESHOLD == 3
