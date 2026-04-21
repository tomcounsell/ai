"""Unit tests for `agent/pipeline_complete.py` (issue #1058).

Pure-function tests with no Redis/GitHub dependencies. The subprocess helper
`_check_pr_open` is exercised via `monkeypatch` to simulate success, empty
result, non-zero exit, timeout, and malformed output.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from agent.pipeline_complete import _check_pr_open, is_pipeline_complete


# -----------------------------------------------------------------------------
# Predicate truth table
# -----------------------------------------------------------------------------


class TestIsPipelineComplete:
    def test_merge_completed_with_success_returns_true_merge_success(self):
        states = {"MERGE": "completed", "DOCS": "completed"}
        is_complete, reason = is_pipeline_complete(states, "success")
        assert is_complete is True
        assert reason == "merge_success"

    def test_merge_completed_ignores_pr_open_value(self):
        # MERGE-success path does NOT consult pr_open.
        states = {"MERGE": "completed"}
        for pr_open in (True, False, None):
            is_complete, reason = is_pipeline_complete(states, "success", pr_open=pr_open)
            assert is_complete is True
            assert reason == "merge_success"

    def test_docs_completed_pr_closed_returns_true(self):
        states = {"DOCS": "completed"}
        is_complete, reason = is_pipeline_complete(states, "success", pr_open=False)
        assert is_complete is True
        assert reason == "docs_success_no_pr"

    def test_docs_completed_pr_open_returns_false(self):
        states = {"DOCS": "completed"}
        is_complete, reason = is_pipeline_complete(states, "success", pr_open=True)
        assert is_complete is False
        assert reason == "pr_still_open"

    def test_docs_completed_pr_unknown_returns_false_conservative(self):
        states = {"DOCS": "completed"}
        is_complete, reason = is_pipeline_complete(states, "success", pr_open=None)
        assert is_complete is False
        assert reason == "pr_state_unavailable"

    def test_outcome_not_success_returns_false(self):
        states = {"MERGE": "completed"}
        is_complete, reason = is_pipeline_complete(states, "fail")
        assert is_complete is False
        assert reason == "outcome_not_success"

    def test_non_terminal_stage_returns_false(self):
        states = {"BUILD": "completed", "TEST": "in_progress"}
        is_complete, reason = is_pipeline_complete(states, "success")
        assert is_complete is False
        assert reason == "stage_not_terminal"

    def test_empty_states_returns_false(self):
        is_complete, reason = is_pipeline_complete({}, "success")
        assert is_complete is False
        assert reason == "stage_not_terminal"

    def test_docs_not_completed_and_no_merge_returns_false(self):
        states = {"DOCS": "in_progress"}
        is_complete, reason = is_pipeline_complete(states, "success", pr_open=False)
        assert is_complete is False
        assert reason == "stage_not_terminal"


# -----------------------------------------------------------------------------
# _check_pr_open subprocess harness
# -----------------------------------------------------------------------------


def _mk_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Build a subprocess.CompletedProcess stand-in."""
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class TestCheckPrOpen:
    def test_returns_true_when_pr_present(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _mk_completed(stdout='[{"number": 42}]'),
        )
        assert _check_pr_open(1058) is True

    def test_returns_false_when_pr_list_empty(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _mk_completed(stdout="[]"),
        )
        assert _check_pr_open(1058) is False

    def test_returns_false_on_empty_stdout(self, monkeypatch):
        # Empty stdout (no JSON at all) is treated as "no open PRs".
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _mk_completed(stdout=""),
        )
        assert _check_pr_open(1058) is False

    def test_returns_none_on_non_zero_exit(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _mk_completed(stdout="", stderr="gh: not authorized", returncode=2),
        )
        assert _check_pr_open(1058) is None

    def test_returns_none_on_timeout(self, monkeypatch):
        def _raise_timeout(*a, **kw):
            raise subprocess.TimeoutExpired(cmd=["gh"], timeout=5.0)

        monkeypatch.setattr(subprocess, "run", _raise_timeout)
        assert _check_pr_open(1058) is None

    def test_returns_none_on_filenotfound(self, monkeypatch):
        def _raise_fnf(*a, **kw):
            raise FileNotFoundError("gh not on PATH")

        monkeypatch.setattr(subprocess, "run", _raise_fnf)
        assert _check_pr_open(1058) is None

    def test_returns_none_on_malformed_json(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _mk_completed(stdout="not json"),
        )
        assert _check_pr_open(1058) is None

    def test_returns_none_on_non_list_json(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _mk_completed(stdout='{"number": 42}'),
        )
        assert _check_pr_open(1058) is None

    def test_returns_none_when_issue_number_missing(self):
        assert _check_pr_open(0) is None
        assert _check_pr_open(None) is None  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# Integration of predicate + PR check gating
# -----------------------------------------------------------------------------


class TestPredicateWithPrCheckGating:
    """Verify call-site gating pattern: predicate is only consulted for
    DOCS-no-MERGE with a caller-resolved pr_open flag. This mirrors how
    `_handle_dev_session_completion` and `_agent_session_hierarchy_health_check`
    must use the predicate."""

    def test_gating_skips_pr_check_for_merge_success(self, monkeypatch):
        # If pr check were called, it would explode.
        def _boom(*a, **kw):
            raise AssertionError("pr check should not be called on MERGE path")

        monkeypatch.setattr(subprocess, "run", _boom)
        # Callers pass pr_open=None for merge path; predicate never reads it.
        states = {"MERGE": "completed"}
        is_complete, reason = is_pipeline_complete(states, "success", pr_open=None)
        assert is_complete is True
        assert reason == "merge_success"

    def test_gating_pr_check_used_only_for_docs_path(self, monkeypatch):
        call_count = {"n": 0}

        def _fake_run(*a, **kw):
            call_count["n"] += 1
            return _mk_completed(stdout="[]")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        # Caller resolves pr_open exactly once for DOCS-no-MERGE path.
        pr_open = _check_pr_open(1058)
        assert pr_open is False
        assert call_count["n"] == 1
        states = {"DOCS": "completed"}
        is_complete, reason = is_pipeline_complete(states, "success", pr_open=pr_open)
        assert is_complete is True
        assert reason == "docs_success_no_pr"


@pytest.mark.parametrize(
    "states,outcome,pr_open,expected",
    [
        # MERGE completed + success = terminal (ignore pr_open)
        ({"MERGE": "completed"}, "success", None, (True, "merge_success")),
        ({"MERGE": "completed"}, "success", True, (True, "merge_success")),
        # MERGE completed but outcome fail -> not terminal
        ({"MERGE": "completed"}, "fail", None, (False, "outcome_not_success")),
        # DOCS completed + no PR -> terminal
        ({"DOCS": "completed"}, "success", False, (True, "docs_success_no_pr")),
        # DOCS completed + PR open -> not terminal
        ({"DOCS": "completed"}, "success", True, (False, "pr_still_open")),
        # DOCS completed + unknown -> not terminal (conservative)
        ({"DOCS": "completed"}, "success", None, (False, "pr_state_unavailable")),
        # DOCS in_progress -> not terminal
        ({"DOCS": "in_progress"}, "success", False, (False, "stage_not_terminal")),
        # Empty
        ({}, "success", None, (False, "stage_not_terminal")),
    ],
)
def test_truth_table(states, outcome, pr_open, expected):
    assert is_pipeline_complete(states, outcome, pr_open=pr_open) == expected
