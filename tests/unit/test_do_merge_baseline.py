"""Unit tests for ``scripts/baseline_gate.py`` (the Full Suite Gate logic).

Covers legacy-shape load, schema-v2 load, new-regression detection, flaky
pass-through, ``hung`` pass-through, staleness warnings (14-day-old
``generated_at``, ``bootstrap: true``, ``-dirty`` commit suffix), and the
PR #1054/#1070 count-coincident regression scenario.

See ``docs/plans/merge-gate-baseline-refresh.md`` for motivation.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.baseline_gate import (
    STALE_COMMIT_DISTANCE,
    commits_behind_head,
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
    # Issue #2066: the actionable remediation is the timeout-safe detached launcher,
    # since a foreground refresh is killed at the 10-min bash cap.
    assert "refresh_baseline_detached.sh" in warning


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


def test_staleness_warning_fires_on_commit_distance() -> None:
    """A time-fresh baseline that is many commits behind HEAD must still warn.

    This is the #1965 blind spot: the incident baseline was only 7 days old
    (under the 14-day time threshold) but 425 commits behind HEAD, so it
    silently produced a wall of false-positive regressions. Commit-distance
    is an independent trigger that catches high-velocity drift the wall-clock
    check misses.
    """
    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = {
        "schema_version": 2,
        "generated_at": now.isoformat(),  # brand new -> time check silent
        "commit": "abc1234",
        "tests": {},
    }
    warning = format_staleness_warning(baseline, now=now, commits_behind=STALE_COMMIT_DISTANCE + 50)
    assert warning is not None
    assert "commits behind HEAD" in warning
    assert "refresh_test_baseline.py" in warning


def test_staleness_warning_silent_under_commit_distance() -> None:
    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = {
        "schema_version": 2,
        "generated_at": now.isoformat(),
        "commit": "abc1234",
        "tests": {},
    }
    assert (
        format_staleness_warning(baseline, now=now, commits_behind=STALE_COMMIT_DISTANCE - 1)
        is None
    )


def test_staleness_warning_ignores_unknown_commit_distance() -> None:
    """``commits_behind=None`` (git unavailable/unknown commit) must not warn."""
    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = {
        "schema_version": 2,
        "generated_at": now.isoformat(),
        "commit": "abc1234",
        "tests": {},
    }
    assert format_staleness_warning(baseline, now=now, commits_behind=None) is None


# ---------------------------------------------------------------------------
# commits_behind_head (git-backed helper)
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")


def test_commits_behind_head_counts_distance(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    (repo / "f").write_text("0")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "c0")
    base_sha = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, text=True
    ).strip()
    for i in range(1, 4):
        (repo / "f").write_text(str(i))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", f"c{i}")
    assert commits_behind_head(base_sha, repo) == 3
    # HEAD is zero commits behind itself.
    head = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, text=True
    ).strip()
    assert commits_behind_head(head, repo) == 0


def test_commits_behind_head_returns_none_on_unknown_commit(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    (repo / "f").write_text("0")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "c0")
    # A SHA that does not exist -> None, never raises.
    assert commits_behind_head("deadbeef", repo) is None


def test_commits_behind_head_strips_dirty_suffix(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    (repo / "f").write_text("0")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "c0")
    sha = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, text=True
    ).strip()
    (repo / "f2").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "c1")
    # The recorded commit may carry a "-dirty" suffix; it must be stripped.
    assert commits_behind_head(f"{sha}-dirty", repo) == 1


def test_commits_behind_head_returns_none_for_missing_commit_field(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    (repo / "f").write_text("0")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "c0")
    assert commits_behind_head(None, repo) is None
    assert commits_behind_head("", repo) is None
    assert commits_behind_head("unknown", repo) is None


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


# ---------------------------------------------------------------------------
# ArtifactEnvelope: defensive envelope reads (issue #2004, T1.3)
# ---------------------------------------------------------------------------


def test_read_envelope_reads_all_five_fields() -> None:
    from scripts._baseline_common import read_envelope

    artifact = {
        "generated_at": "2026-07-01T00:00:00+00:00",
        "commit": "abc1234",
        "generated_by": "python scripts/refresh_test_baseline.py --runs 3",
        "runs": 3,
        "degraded": False,
        "tests": {},
    }
    env = read_envelope(artifact)
    assert env.generated_at == "2026-07-01T00:00:00+00:00"
    assert env.commit == "abc1234"
    assert env.generated_by == "python scripts/refresh_test_baseline.py --runs 3"
    assert env.runs == 3
    assert env.degraded is False
    assert env.is_legacy is False


def test_read_envelope_absent_fields_is_legacy_never_crashes() -> None:
    """A pre-envelope artifact (no runs/degraded/generated_at) reads as legacy."""
    from scripts._baseline_common import read_envelope

    env = read_envelope({"schema_version": 2, "tests": {}})
    assert env.is_legacy is True
    assert env.runs is None
    assert env.degraded is False


def test_read_envelope_non_dict_and_malformed_types_never_crash() -> None:
    from scripts._baseline_common import read_envelope

    assert read_envelope(None).is_legacy is True
    assert read_envelope(["not", "a", "dict"]).is_legacy is True
    env = read_envelope({"runs": "three", "generated_at": 12345, "degraded": "yes"})
    assert env.runs is None
    assert env.generated_at is None
    assert env.degraded is False  # only literal True counts


def test_envelope_carries_no_threshold_fields() -> None:
    """Thresholds live in baseline_gate module constants, never in the envelope."""
    import dataclasses

    from scripts._baseline_common import ArtifactEnvelope

    field_names = {f.name for f in dataclasses.fields(ArtifactEnvelope)}
    assert field_names == {"generated_at", "commit", "generated_by", "runs", "degraded"}


# ---------------------------------------------------------------------------
# shared staleness(): one definition for gate and reflection
# ---------------------------------------------------------------------------


def test_staleness_empty_for_fresh_envelope() -> None:
    from scripts._baseline_common import read_envelope, staleness

    now = datetime(2026, 4, 20, tzinfo=UTC)
    env = read_envelope(
        {"generated_at": now.isoformat(), "commit": "abc1234", "runs": 3, "degraded": False}
    )
    assert staleness(env, now=now, commits_behind=0) == []


def test_staleness_reports_age_past_gate_threshold() -> None:
    from scripts._baseline_common import read_envelope, staleness

    now = datetime(2026, 4, 20, tzinfo=UTC)
    env = read_envelope(
        {
            "generated_at": (now - timedelta(days=20)).isoformat(),
            "commit": "abc1234",
            "runs": 3,
        }
    )
    reasons = staleness(env, now=now, commits_behind=0)
    assert any("20 days old" in r for r in reasons)


def test_staleness_reports_dirty_commit_and_commit_distance() -> None:
    from scripts._baseline_common import read_envelope, staleness

    now = datetime(2026, 4, 20, tzinfo=UTC)
    env = read_envelope({"generated_at": now.isoformat(), "commit": "abc1234-dirty", "runs": 3})
    reasons = staleness(env, now=now, commits_behind=STALE_COMMIT_DISTANCE + 50)
    assert any("dirty tree" in r for r in reasons)
    assert any("commits behind HEAD" in r for r in reasons)


def test_staleness_reads_thresholds_from_gate_module_constants(monkeypatch) -> None:
    """staleness() must read the gate's live module constants, not envelope fields."""
    import scripts.baseline_gate as gate_mod
    from scripts._baseline_common import read_envelope, staleness

    now = datetime(2026, 4, 20, tzinfo=UTC)
    env = read_envelope(
        {"generated_at": (now - timedelta(days=2)).isoformat(), "commit": "abc1234", "runs": 3}
    )
    # 2 days old is fresh under the real 14-day threshold...
    assert staleness(env, now=now, commits_behind=0) == []
    # ...but stale once the gate constant is tightened to 1 day.
    monkeypatch.setattr(gate_mod, "STALENESS_THRESHOLD", timedelta(days=1))
    assert staleness(env, now=now, commits_behind=0) != []


def test_staleness_none_commits_behind_skips_distance_trigger() -> None:
    from scripts._baseline_common import read_envelope, staleness

    now = datetime(2026, 4, 20, tzinfo=UTC)
    env = read_envelope({"generated_at": now.isoformat(), "commit": "abc1234", "runs": 3})
    assert staleness(env, now=now, commits_behind=None) == []


# ---------------------------------------------------------------------------
# flaky decay: stale envelopes expire flaky allowances (never ride forever)
# ---------------------------------------------------------------------------


def _flaky_baseline(generated_at: datetime) -> dict:
    return {
        "schema_version": 2,
        "generated_at": generated_at.isoformat(),
        "commit": "abc1234",
        "runs": 3,
        "degraded": False,
        "tests": {
            "tests/unit/test_a.py::test_flaky": {
                "category": "flaky",
                "fail_rate": 0.33,
                "hung_count": 0,
            },
            "tests/unit/test_a.py::test_real": {
                "category": "real",
                "fail_rate": 1.0,
                "hung_count": 0,
            },
        },
    }


def test_expire_stale_flaky_entries_drops_flaky_when_envelope_stale() -> None:
    from scripts._baseline_common import expire_stale_flaky_entries

    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = _flaky_baseline(now - timedelta(days=20))
    new_baseline, expired = expire_stale_flaky_entries(baseline, now=now, commits_behind=0)
    assert expired == ["tests/unit/test_a.py::test_flaky"]
    assert "tests/unit/test_a.py::test_flaky" not in new_baseline["tests"]
    # Non-flaky categories are untouched by flaky decay.
    assert "tests/unit/test_a.py::test_real" in new_baseline["tests"]
    # Input is not mutated.
    assert "tests/unit/test_a.py::test_flaky" in baseline["tests"]


def test_expire_stale_flaky_entries_keeps_flaky_when_fresh() -> None:
    from scripts._baseline_common import expire_stale_flaky_entries

    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = _flaky_baseline(now)
    new_baseline, expired = expire_stale_flaky_entries(baseline, now=now, commits_behind=0)
    assert expired == []
    assert "tests/unit/test_a.py::test_flaky" in new_baseline["tests"]


def test_expire_stale_flaky_entries_legacy_envelope_is_noop() -> None:
    """No envelope (legacy artifact) => no expiry signal => keep entries."""
    from scripts._baseline_common import expire_stale_flaky_entries

    baseline = {
        "schema_version": 2,
        "tests": {"tests/unit/test_a.py::test_flaky": {"category": "flaky"}},
    }
    new_baseline, expired = expire_stale_flaky_entries(baseline, commits_behind=None)
    assert expired == []
    assert "tests/unit/test_a.py::test_flaky" in new_baseline["tests"]


def _write_passing_pr_xml(tmp_path: Path, name: str = "pr.xml") -> Path:
    pr_xml = tmp_path / name
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
    return pr_xml


def _write_failing_pr_xml(tmp_path: Path, node_name: str = "test_flaky") -> Path:
    pr_xml = tmp_path / "pr_fail.xml"
    pr_xml.write_text(
        textwrap.dedent(
            f"""\
            <?xml version="1.0" encoding="utf-8"?>
            <testsuites>
              <testsuite name="pytest">
                <testcase classname="tests.unit.test_a" name="{node_name}">
                  <failure message="assert failed"/>
                </testcase>
              </testsuite>
            </testsuites>
            """
        )
    )
    return pr_xml


def test_main_expired_flaky_failure_becomes_blocking(tmp_path: Path, capsys) -> None:
    """A flaky allowance on a stale envelope no longer suppresses the failure."""
    from scripts.baseline_gate import main

    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(_flaky_baseline(now - timedelta(days=20))))
    pr_xml = _write_failing_pr_xml(tmp_path, "test_flaky")

    exit_code = main(
        ["--pr-junitxml", str(pr_xml), "--baseline", str(baseline_path), "--now", now.isoformat()]
    )
    assert exit_code == 1
    verdict = json.loads(capsys.readouterr().out)
    assert "tests/unit/test_a.py::test_flaky" in verdict["new_blocking_regressions"]
    assert "tests/unit/test_a.py::test_flaky" in verdict["expired_flaky_entries"]


def test_main_fresh_flaky_failure_stays_non_blocking(tmp_path: Path, capsys) -> None:
    from scripts.baseline_gate import main

    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(_flaky_baseline(now)))
    pr_xml = _write_failing_pr_xml(tmp_path, "test_flaky")

    exit_code = main(
        ["--pr-junitxml", str(pr_xml), "--baseline", str(baseline_path), "--now", now.isoformat()]
    )
    assert exit_code == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["new_flaky_occurrences"] == ["tests/unit/test_a.py::test_flaky"]


# ---------------------------------------------------------------------------
# --strict-freshness: refuse-to-gate exit path (selectable via `-k strict`)
# ---------------------------------------------------------------------------


def _fresh_envelope_baseline(now: datetime, **overrides) -> dict:
    baseline = {
        "schema_version": 2,
        "generated_at": now.isoformat(),
        "commit": "abc1234",
        "generated_by": "python scripts/refresh_test_baseline.py --runs 3",
        "runs": 3,
        "degraded": False,
        "tests": {},
    }
    baseline.update(overrides)
    return baseline


def _run_strict_main(tmp_path: Path, baseline: dict, now: datetime, extra_args=()) -> int:
    from scripts.baseline_gate import main

    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline))
    pr_xml = _write_passing_pr_xml(tmp_path)
    return main(
        [
            "--pr-junitxml",
            str(pr_xml),
            "--baseline",
            str(baseline_path),
            "--now",
            now.isoformat(),
            "--strict-freshness",
            *extra_args,
        ]
    )


def test_strict_fresh_envelope_proceeds_to_normal_verdict(tmp_path: Path, capsys) -> None:
    now = datetime(2026, 4, 20, tzinfo=UTC)
    exit_code = _run_strict_main(tmp_path, _fresh_envelope_baseline(now), now)
    assert exit_code == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["new_blocking_regressions"] == []


def test_strict_refuses_on_degraded_envelope(tmp_path: Path, capsys) -> None:
    from scripts.baseline_gate import EXIT_STRICT_REFUSAL

    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = _fresh_envelope_baseline(now, degraded=True, runs=1)
    exit_code = _run_strict_main(tmp_path, baseline, now)
    assert exit_code == EXIT_STRICT_REFUSAL
    captured = capsys.readouterr()
    verdict = json.loads(captured.out)
    assert verdict["strict_freshness_refused"] is True
    assert verdict["reasons"]
    # Never a false pre-existing/regression verdict on refusal.
    assert "new_blocking_regressions" not in verdict
    # The refusal prints the exact regen command.
    assert "refresh_test_baseline.py" in captured.err


def test_strict_refuses_on_runs_below_two(tmp_path: Path, capsys) -> None:
    from scripts.baseline_gate import EXIT_STRICT_REFUSAL

    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = _fresh_envelope_baseline(now, runs=1)
    exit_code = _run_strict_main(tmp_path, baseline, now)
    assert exit_code == EXIT_STRICT_REFUSAL
    verdict = json.loads(capsys.readouterr().out)
    assert any("run" in r for r in verdict["reasons"])


def test_strict_refuses_on_stale_envelope(tmp_path: Path, capsys) -> None:
    from scripts.baseline_gate import EXIT_STRICT_REFUSAL

    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = _fresh_envelope_baseline(now)
    baseline["generated_at"] = (now - timedelta(days=20)).isoformat()
    exit_code = _run_strict_main(tmp_path, baseline, now)
    assert exit_code == EXIT_STRICT_REFUSAL
    verdict = json.loads(capsys.readouterr().out)
    assert any("days old" in r for r in verdict["reasons"])


def test_strict_legacy_envelope_refuses_with_warning_not_crash(tmp_path: Path, capsys) -> None:
    """Absent envelope fields => defensive read (warn), fail-closed under strict."""
    from scripts.baseline_gate import EXIT_STRICT_REFUSAL

    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = {"schema_version": 2, "tests": {}}  # no envelope fields at all
    exit_code = _run_strict_main(tmp_path, baseline, now)
    assert exit_code == EXIT_STRICT_REFUSAL
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["strict_freshness_refused"] is True


def test_nonstrict_legacy_envelope_warns_never_crashes(tmp_path: Path, capsys) -> None:
    """Without --strict-freshness a legacy artifact keeps the old warn-only path."""
    from scripts.baseline_gate import main

    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({"schema_version": 2, "tests": {}}))
    pr_xml = _write_passing_pr_xml(tmp_path)
    exit_code = main(["--pr-junitxml", str(pr_xml), "--baseline", str(baseline_path)])
    assert exit_code == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["new_blocking_regressions"] == []


def test_strict_break_glass_sentinel_skips_refusal(tmp_path: Path, capsys) -> None:
    """data/merge_authorized_{pr} parity: operator authorization beats the refusal."""
    now = datetime(2026, 4, 20, tzinfo=UTC)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "merge_authorized_42").write_text("authorized\n")
    baseline = _fresh_envelope_baseline(now, degraded=True, runs=1)

    exit_code = _run_strict_main(
        tmp_path,
        baseline,
        now,
        extra_args=["--pr-number", "42", "--data-dir", str(data_dir)],
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    verdict = json.loads(captured.out)
    assert verdict["new_blocking_regressions"] == []
    assert "merge_authorized_42" in captured.err


def test_strict_without_sentinel_still_refuses_with_pr_number(tmp_path: Path, capsys) -> None:
    from scripts.baseline_gate import EXIT_STRICT_REFUSAL

    now = datetime(2026, 4, 20, tzinfo=UTC)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    baseline = _fresh_envelope_baseline(now, degraded=True, runs=1)

    exit_code = _run_strict_main(
        tmp_path,
        baseline,
        now,
        extra_args=["--pr-number", "42", "--data-dir", str(data_dir)],
    )
    assert exit_code == EXIT_STRICT_REFUSAL


def test_strict_refusal_exit_code_is_distinct_from_regression_exit() -> None:
    from scripts.baseline_gate import EXIT_STRICT_REFUSAL

    assert EXIT_STRICT_REFUSAL not in (0, 1, 2)  # 2 is argparse's own error exit


# ---------------------------------------------------------------------------
# import_error fast-expiry: a tighter window than the general staleness rule
# (3 days / 30 commits, gate-module constants; issue #2004 Task 4). An
# import_error baseline entry on an out-of-window envelope must NEVER classify
# a PR failure as pre-existing.
# ---------------------------------------------------------------------------


def _import_error_baseline(generated_at: datetime) -> dict:
    return {
        "schema_version": 2,
        "generated_at": generated_at.isoformat(),
        "commit": "abc1234",
        "runs": 3,
        "degraded": False,
        "tests": {
            "tests/unit/test_a.py::test_broken_import": {
                "category": "import_error",
                "fail_rate": 1.0,
                "hung_count": 0,
            },
            "tests/unit/test_a.py::test_real": {
                "category": "real",
                "fail_rate": 1.0,
                "hung_count": 0,
            },
        },
    }


def test_import_error_thresholds_live_in_gate_module_not_artifact() -> None:
    from scripts import baseline_gate

    assert baseline_gate.IMPORT_ERROR_MAX_AGE == timedelta(days=3)
    assert baseline_gate.IMPORT_ERROR_MAX_COMMIT_DISTANCE == 30


def test_expire_stale_import_error_entries_drops_past_max_age() -> None:
    from scripts._baseline_common import expire_stale_import_error_entries

    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = _import_error_baseline(now - timedelta(days=4))
    new_baseline, expired = expire_stale_import_error_entries(baseline, now=now, commits_behind=0)
    assert expired == ["tests/unit/test_a.py::test_broken_import"]
    assert "tests/unit/test_a.py::test_broken_import" not in new_baseline["tests"]
    # Other categories are untouched by import-error expiry.
    assert "tests/unit/test_a.py::test_real" in new_baseline["tests"]
    # Input is not mutated.
    assert "tests/unit/test_a.py::test_broken_import" in baseline["tests"]


def test_expire_stale_import_error_entries_drops_past_commit_distance() -> None:
    """Time-fresh but >30 commits behind still expires (velocity blind spot)."""
    from scripts._baseline_common import expire_stale_import_error_entries

    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = _import_error_baseline(now - timedelta(hours=6))
    new_baseline, expired = expire_stale_import_error_entries(baseline, now=now, commits_behind=31)
    assert expired == ["tests/unit/test_a.py::test_broken_import"]
    assert "tests/unit/test_a.py::test_broken_import" not in new_baseline["tests"]


def test_expire_stale_import_error_entries_keeps_within_window() -> None:
    from scripts._baseline_common import expire_stale_import_error_entries

    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = _import_error_baseline(now - timedelta(days=2))
    new_baseline, expired = expire_stale_import_error_entries(baseline, now=now, commits_behind=30)
    assert expired == []
    assert "tests/unit/test_a.py::test_broken_import" in new_baseline["tests"]


def test_expire_stale_import_error_entries_none_commits_behind_skips_distance() -> None:
    """Git unavailable / unknown commit skips the distance trigger, not the age one."""
    from scripts._baseline_common import expire_stale_import_error_entries

    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline = _import_error_baseline(now - timedelta(days=1))
    new_baseline, expired = expire_stale_import_error_entries(
        baseline, now=now, commits_behind=None
    )
    assert expired == []
    assert "tests/unit/test_a.py::test_broken_import" in new_baseline["tests"]


def test_expire_stale_import_error_entries_legacy_envelope_is_noop() -> None:
    """No envelope (legacy artifact) => existing behavior: entries kept."""
    from scripts._baseline_common import expire_stale_import_error_entries

    baseline = {
        "schema_version": 2,
        "tests": {"tests/unit/test_a.py::test_broken_import": {"category": "import_error"}},
    }
    new_baseline, expired = expire_stale_import_error_entries(baseline, commits_behind=None)
    assert expired == []
    assert "tests/unit/test_a.py::test_broken_import" in new_baseline["tests"]


def test_main_expired_import_error_failure_becomes_blocking(tmp_path: Path, capsys) -> None:
    """An import_error allowance past the window no longer reads as pre-existing."""
    from scripts.baseline_gate import main

    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(_import_error_baseline(now - timedelta(days=4))))
    pr_xml = _write_failing_pr_xml(tmp_path, "test_broken_import")

    exit_code = main(
        ["--pr-junitxml", str(pr_xml), "--baseline", str(baseline_path), "--now", now.isoformat()]
    )
    assert exit_code == 1
    verdict = json.loads(capsys.readouterr().out)
    assert "tests/unit/test_a.py::test_broken_import" in verdict["new_blocking_regressions"]
    assert "tests/unit/test_a.py::test_broken_import" in verdict["expired_import_error_entries"]


def test_main_fresh_import_error_failure_stays_preexisting(tmp_path: Path, capsys) -> None:
    from scripts.baseline_gate import main

    now = datetime(2026, 4, 20, tzinfo=UTC)
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(_import_error_baseline(now - timedelta(days=1))))
    pr_xml = _write_failing_pr_xml(tmp_path, "test_broken_import")

    exit_code = main(
        ["--pr-junitxml", str(pr_xml), "--baseline", str(baseline_path), "--now", now.isoformat()]
    )
    assert exit_code == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["new_blocking_regressions"] == []
    assert "tests/unit/test_a.py::test_broken_import" in verdict["preexisting_failures"]
    assert verdict["expired_import_error_entries"] == []
