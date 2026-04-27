"""Unit tests for ``scripts/refresh_test_baseline.py`` and the baseline common module.

Covers the classification precedence, the exact-prefix ``pytest-timeout``
match, the ParseError-safe junitxml aggregator, and the dirty-tree commit
capture.  See ``docs/plans/merge-gate-baseline-refresh.md`` for the plan
that motivates each assertion.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts._baseline_common import (
    CATEGORY_FLAKY,
    CATEGORY_HUNG,
    CATEGORY_IMPORT_ERROR,
    CATEGORY_REAL,
    JunitxmlParseError,
    parse_junitxml,
)
from scripts.refresh_test_baseline import (
    aggregate_outcomes,
    build_baseline,
    capture_commit,
    classify,
    load_existing_notes,
    resolve_output_path,
    run_pytest_once,
)

# ---------------------------------------------------------------------------
# junitxml parsing
# ---------------------------------------------------------------------------


def _write_xml(tmp_path: Path, content: str, name: str = "junit.xml") -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


def test_parse_junitxml_classifies_pass_fail_timeout_and_error(tmp_path: Path) -> None:
    xml = textwrap.dedent(
        """\
        <?xml version="1.0" encoding="utf-8"?>
        <testsuites>
          <testsuite name="pytest">
            <testcase classname="tests.unit.test_foo" name="test_ok"/>
            <testcase classname="tests.unit.test_foo" name="test_real_fail">
              <failure message="AssertionError: 1 != 2"/>
            </testcase>
            <testcase classname="tests.unit.test_foo" name="test_hung">
              <failure message="Failed: Timeout &gt;60.0s"/>
            </testcase>
            <testcase classname="tests.unit.test_foo" name="test_import_busted">
              <error message="collection error"/>
            </testcase>
          </testsuite>
        </testsuites>
        """
    )
    path = _write_xml(tmp_path, xml)
    outcomes = parse_junitxml(path)
    assert outcomes == {
        "tests/unit/test_foo.py::test_ok": "pass",
        "tests/unit/test_foo.py::test_real_fail": "fail",
        "tests/unit/test_foo.py::test_hung": "timeout",
        "tests/unit/test_foo.py::test_import_busted": "collection_error",
    }


def test_loose_timeout_substring_not_misclassified(tmp_path: Path) -> None:
    """A failure message that merely mentions "Timeout" must stay as fail, not hung."""
    xml = textwrap.dedent(
        """\
        <?xml version="1.0" encoding="utf-8"?>
        <testsuites>
          <testsuite name="pytest">
            <testcase classname="tests.unit.test_foo" name="test_loose">
              <failure message="AssertionError: expected 'Timeout' in output"/>
            </testcase>
            <testcase classname="tests.unit.test_foo" name="test_strict">
              <failure message="Failed: Timeout &gt;60.0s"/>
            </testcase>
          </testsuite>
        </testsuites>
        """
    )
    path = _write_xml(tmp_path, xml)
    outcomes = parse_junitxml(path)
    assert outcomes["tests/unit/test_foo.py::test_loose"] == "fail"
    assert outcomes["tests/unit/test_foo.py::test_strict"] == "timeout"

    # And the downstream classifier treats them differently:
    loose = classify(["fail", "fail", "pass"])
    assert loose is not None
    assert loose[0] == CATEGORY_FLAKY
    strict = classify(["timeout", "fail", "fail"])
    assert strict is not None
    assert strict[0] == CATEGORY_HUNG


def test_truncated_junitxml_raises_parse_error(tmp_path: Path) -> None:
    """Truncated/malformed junitxml must raise JunitxmlParseError, not a bare ValueError."""
    truncated = '<?xml version="1.0"?><testsuites><testsuite name="pytest"><testcase'
    path = _write_xml(tmp_path, truncated)
    with pytest.raises(JunitxmlParseError):
        parse_junitxml(path)


def test_missing_junitxml_raises_parse_error(tmp_path: Path) -> None:
    with pytest.raises(JunitxmlParseError):
        parse_junitxml(tmp_path / "does-not-exist.xml")


def test_testcase_with_no_name_is_flagged(tmp_path: Path) -> None:
    xml = textwrap.dedent(
        """\
        <?xml version="1.0" encoding="utf-8"?>
        <testsuites>
          <testsuite name="pytest">
            <testcase classname="tests.unit.test_foo"/>
          </testsuite>
        </testsuites>
        """
    )
    path = _write_xml(tmp_path, xml)
    with pytest.raises(JunitxmlParseError):
        parse_junitxml(path)


# ---------------------------------------------------------------------------
# classifier precedence
# ---------------------------------------------------------------------------


def test_classify_skips_all_passing_tests() -> None:
    assert classify(["pass", "pass", "pass"]) is None


def test_classify_real_means_all_runs_failed() -> None:
    category, fail_rate, hung_count = classify(["fail", "fail", "fail"])
    assert category == CATEGORY_REAL
    assert fail_rate == 1.0
    assert hung_count == 0


def test_classify_flaky_is_partial_failure() -> None:
    category, fail_rate, hung_count = classify(["pass", "fail", "pass"])
    assert category == CATEGORY_FLAKY
    assert pytest.approx(fail_rate, rel=1e-6) == 1 / 3
    assert hung_count == 0


def test_classify_hung_beats_flaky() -> None:
    """2 fails + 1 timeout across 3 runs must classify as hung, not flaky."""
    category, fail_rate, hung_count = classify(["fail", "fail", "timeout"])
    assert category == CATEGORY_HUNG
    assert pytest.approx(fail_rate, rel=1e-6) == 1.0
    assert hung_count == 1


def test_classify_import_error_beats_hung() -> None:
    category, fail_rate, hung_count = classify(["collection_error", "timeout", "fail"])
    assert category == CATEGORY_IMPORT_ERROR
    assert fail_rate == 1.0
    # hung_count still reflects timeouts even though the category is import_error,
    # because a reader may want to see the structural problem.
    assert hung_count == 1


# ---------------------------------------------------------------------------
# aggregator
# ---------------------------------------------------------------------------


def test_aggregate_outcomes_combines_runs() -> None:
    runs = [
        {"a": "pass", "b": "fail"},
        {"a": "pass", "b": "pass"},
        {"a": "fail", "b": "fail"},
    ]
    aggregated = aggregate_outcomes(runs)
    assert aggregated["a"] == ["pass", "pass", "fail"]
    assert aggregated["b"] == ["fail", "pass", "fail"]


def test_aggregate_outcomes_handles_missing_tests() -> None:
    """A test present in run 2 but not in run 1 is only counted for the runs that saw it."""
    runs = [
        {"a": "pass"},
        {"a": "pass", "b": "fail"},
    ]
    aggregated = aggregate_outcomes(runs)
    assert aggregated["a"] == ["pass", "pass"]
    assert aggregated["b"] == ["fail"]


# ---------------------------------------------------------------------------
# commit capture (dirty-tree suffix)
# ---------------------------------------------------------------------------


def _init_tmp_repo(tmp_path: Path) -> Path:
    """Initialise a fresh git repo in ``tmp_path`` with a single commit."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_path, check=True)
    seed = tmp_path / "seed.txt"
    seed.write_text("hello\n")
    subprocess.run(["git", "add", "seed.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed", "--no-gpg-sign"],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def test_dirty_tree_commit_suffix(tmp_path: Path) -> None:
    repo = _init_tmp_repo(tmp_path)
    clean_sha = capture_commit(repo)
    assert not clean_sha.endswith("-dirty"), clean_sha

    # Unstaged change -> -dirty suffix.
    (repo / "seed.txt").write_text("hello world\n")
    dirty_sha = capture_commit(repo)
    assert dirty_sha.endswith("-dirty"), dirty_sha
    assert dirty_sha.removesuffix("-dirty") == clean_sha

    # Staged but uncommitted -> still -dirty.
    subprocess.run(["git", "add", "seed.txt"], cwd=repo, check=True)
    dirty_staged_sha = capture_commit(repo)
    assert dirty_staged_sha.endswith("-dirty"), dirty_staged_sha

    # Commit -> clean again.
    subprocess.run(
        ["git", "commit", "-q", "-m", "change", "--no-gpg-sign"],
        cwd=repo,
        check=True,
    )
    clean_after = capture_commit(repo)
    assert not clean_after.endswith("-dirty"), clean_after


# ---------------------------------------------------------------------------
# build_baseline shape
# ---------------------------------------------------------------------------


def test_build_baseline_emits_schema_v2_with_commit_and_tests(tmp_path: Path) -> None:
    repo = _init_tmp_repo(tmp_path)
    aggregated = {
        "tests/unit/test_a.py::test_real": ["fail", "fail", "fail"],
        "tests/unit/test_a.py::test_flaky": ["fail", "pass", "pass"],
        "tests/unit/test_a.py::test_hung": ["timeout", "pass", "pass"],
        "tests/unit/test_a.py::test_ok": ["pass", "pass", "pass"],
    }
    baseline = build_baseline(
        aggregated=aggregated,
        runs=3,
        repo_root=repo,
        argv=["scripts/refresh_test_baseline.py", "--runs", "3"],
    )
    assert baseline["schema_version"] == 2
    assert baseline["runs"] == 3
    assert baseline["commit"]  # non-empty
    assert isinstance(baseline["generated_at"], str)
    tests = baseline["tests"]
    # Passing test is excluded.
    assert "tests/unit/test_a.py::test_ok" not in tests
    assert tests["tests/unit/test_a.py::test_real"]["category"] == CATEGORY_REAL
    assert tests["tests/unit/test_a.py::test_flaky"]["category"] == CATEGORY_FLAKY
    assert tests["tests/unit/test_a.py::test_hung"]["category"] == CATEGORY_HUNG


def test_load_existing_notes_preserves_note_field(tmp_path: Path) -> None:
    payload = {
        "schema_version": 2,
        "generated_at": "2026-04-01T00:00:00+00:00",
        "runs": 3,
        "commit": "abc123",
        "tests": {
            "tests/unit/test_x.py::test_noted": {
                "category": "flaky",
                "fail_rate": 0.33,
                "hung_count": 0,
                "note": "LLM-as-judge -- see issue #1084",
            },
            "tests/unit/test_x.py::test_plain": {
                "category": "real",
                "fail_rate": 1.0,
                "hung_count": 0,
            },
        },
    }
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(payload))
    notes = load_existing_notes(path)
    assert notes == {
        "tests/unit/test_x.py::test_noted": "LLM-as-judge -- see issue #1084",
    }


def test_build_baseline_preserves_notes_when_merged(tmp_path: Path) -> None:
    repo = _init_tmp_repo(tmp_path)
    aggregated = {"tests/unit/test_x.py::test_noted": ["fail", "pass", "pass"]}
    baseline = build_baseline(
        aggregated=aggregated,
        runs=3,
        repo_root=repo,
        argv=[],
        preserved_notes={"tests/unit/test_x.py::test_noted": "keep me"},
    )
    assert baseline["tests"]["tests/unit/test_x.py::test_noted"]["note"] == "keep me"


# ---------------------------------------------------------------------------
# output path resolution
# ---------------------------------------------------------------------------


class _NS:
    """Tiny argparse-like stand-in."""

    def __init__(self, output: str | None, dry_run: bool) -> None:
        self.output = output
        self.dry_run = dry_run


def test_resolve_output_path_dry_run_defaults_to_stdout() -> None:
    assert resolve_output_path(_NS(output=None, dry_run=True)) == "-"


def test_resolve_output_path_normal_defaults_to_baseline_path() -> None:
    result = resolve_output_path(_NS(output=None, dry_run=False))
    assert result.endswith("data/main_test_baseline.json")


def test_resolve_output_path_explicit_override_wins() -> None:
    assert resolve_output_path(_NS(output="/tmp/custom.json", dry_run=True)) == "/tmp/custom.json"
    assert resolve_output_path(_NS(output="/tmp/custom.json", dry_run=False)) == "/tmp/custom.json"


# ---------------------------------------------------------------------------
# run_pytest_once subprocess boundary
#
# Historically the ``run_pytest_once`` subprocess boundary had zero unit-test
# coverage -- every other function was tested but this one was "mostly
# plumbing" and skipped.  That's how the ``-p pytest_timeout`` blocker
# (PR #1154 review) landed invisible: the command would raise
# ``ValueError: Plugin already registered`` at startup, pytest exited before
# writing junitxml, ``run_pytest_once`` returned True on completion, and the
# downstream ``JunitxmlParseError`` path swallowed every run.  Tests below
# close that gap by exercising (a) a real pytest invocation against a minimal
# fixture tree and (b) a mocked startup-failure that never writes junitxml.
# ---------------------------------------------------------------------------


def test_run_pytest_once_writes_junitxml_for_real_minimal_tree(
    tmp_path: Path,
) -> None:
    """A minimal tests/ tree makes run_pytest_once write junitxml and return True.

    This verifies the entire subprocess boundary end-to-end: pytest is actually
    invoked against a single-test tree and must produce a parseable junitxml
    with the expected outcome.  If the BLOCKER regresses (e.g. by re-adding
    ``-p pytest_timeout``), this test fails because pytest never writes the
    file -- we assert ``True`` AND ``junitxml_path.exists()`` AND that the
    parsed outcomes include the seeded test.
    """
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_seed.py").write_text(
        textwrap.dedent(
            """
            def test_passing():
                assert 1 == 1
            """
        ).strip()
        + "\n"
    )
    junitxml_path = tmp_path / "junit.xml"

    # Override PROJECT_DIR so pytest picks up our tiny fixture tree, not the
    # real repo's sprawling tests/.  We patch on the module the function is
    # imported from so the patched value is the one run_pytest_once reads.
    import scripts.refresh_test_baseline as refresh_module

    with patch.object(refresh_module, "PROJECT_DIR", tmp_path):
        ok = run_pytest_once(
            run_index=0,
            junitxml_path=junitxml_path,
            test_timeout=30,
            global_timeout=120,
            pytest_args=None,
            verbose=False,
        )

    assert ok is True, "run_pytest_once must return True on a completed run"
    assert junitxml_path.exists(), (
        "pytest produced no junitxml -- a startup-time crash is hiding. "
        "Check that no second pytest-timeout registration was re-introduced."
    )

    outcomes = parse_junitxml(junitxml_path)
    assert outcomes == {"tests/test_seed.py::test_passing": "pass"}


def test_run_pytest_once_returns_false_when_pytest_crashes_before_junitxml(
    tmp_path: Path,
) -> None:
    """A pytest startup crash must surface as False, not silently pass.

    Before the fix, ``run_pytest_once`` returned True on any completed
    subprocess call -- including plugin-registration errors where pytest
    exits before writing junitxml.  The downstream ``JunitxmlParseError``
    path masked this as "all N runs failed" with no signal of why.
    """
    junitxml_path = tmp_path / "junit.xml"

    # Simulate a subprocess that exits non-zero WITHOUT writing junitxml.
    fake_result = subprocess.CompletedProcess(
        args=["pytest", "..."],
        returncode=4,  # pytest's "internal error" exit code
        stdout="",
        stderr=(
            "ValueError: Plugin already registered under a different name: "
            "timeout=<module 'pytest_timeout'>"
        ),
    )

    with patch("scripts.refresh_test_baseline.subprocess.run", return_value=fake_result):
        ok = run_pytest_once(
            run_index=0,
            junitxml_path=junitxml_path,
            test_timeout=30,
            global_timeout=120,
            pytest_args=None,
            verbose=False,
        )

    assert ok is False, (
        "A pytest startup crash must return False so the caller discards the "
        "run; returning True would let the downstream JunitxmlParseError path "
        "silently mask the root cause."
    )
    assert not junitxml_path.exists()


def test_run_pytest_once_returns_false_on_outer_timeout(tmp_path: Path) -> None:
    """The --global-timeout safety net must still return False."""
    junitxml_path = tmp_path / "junit.xml"

    with patch(
        "scripts.refresh_test_baseline.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["pytest"], timeout=1),
    ):
        ok = run_pytest_once(
            run_index=0,
            junitxml_path=junitxml_path,
            test_timeout=30,
            global_timeout=1,
            pytest_args=None,
            verbose=False,
        )

    assert ok is False


def test_run_pytest_once_logs_stderr_tail_on_startup_crash(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Captured stderr must appear in the warning so the operator sees why pytest crashed."""
    junitxml_path = tmp_path / "junit.xml"
    tell_tale_stderr = (
        "ValueError: Plugin already registered under a different name: "
        "timeout=<module 'pytest_timeout'>"
    )
    fake_result = subprocess.CompletedProcess(
        args=["pytest"],
        returncode=4,
        stdout="",
        stderr=tell_tale_stderr,
    )

    import logging

    with (
        caplog.at_level(logging.WARNING, logger="scripts.refresh_test_baseline"),
        patch(
            "scripts.refresh_test_baseline.subprocess.run",
            return_value=fake_result,
        ),
    ):
        ok = run_pytest_once(
            run_index=0,
            junitxml_path=junitxml_path,
            test_timeout=30,
            global_timeout=120,
            pytest_args=None,
            verbose=False,
        )

    assert ok is False
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "Plugin already registered" in joined, (
        "stderr contents must appear in the warning so the operator can see "
        "what caused the crash -- otherwise the signal is just 'all N runs "
        "failed' again."
    )
