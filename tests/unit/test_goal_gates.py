"""Tests for agent.goal_gates module.

Tests cover each gate check function, the dispatcher, the summary function,
and error handling / edge cases. All filesystem checks use tmp_path fixtures
and all subprocess calls are mocked to avoid hitting real GitHub APIs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent.goal_gates import (
    GATE_STAGES,
    GateResult,
    check_all_gates,
    check_build_gate,
    check_docs_gate,
    check_gate,
    check_plan_gate,
    check_review_gate,
    check_test_gate,
)

# ---------------------------------------------------------------------------
# GateResult dataclass
# ---------------------------------------------------------------------------


class TestGateResult:
    def test_satisfied_result(self):
        r = GateResult(satisfied=True, evidence="file exists")
        assert r.satisfied is True
        assert r.evidence == "file exists"
        assert r.missing is None

    def test_unsatisfied_result(self):
        r = GateResult(satisfied=False, evidence="not found", missing="plan.md")
        assert r.satisfied is False
        assert r.missing == "plan.md"


# ---------------------------------------------------------------------------
# check_plan_gate
# ---------------------------------------------------------------------------


class TestCheckPlanGate:
    def test_plan_exists(self, tmp_path: Path):
        plan_dir = tmp_path / "docs" / "plans"
        plan_dir.mkdir(parents=True)
        (plan_dir / "my-feature.md").write_text("# Plan")

        result = check_plan_gate("my-feature", tmp_path)
        assert result.satisfied is True
        assert "exists" in result.evidence

    def test_plan_missing(self, tmp_path: Path):
        result = check_plan_gate("nonexistent", tmp_path)
        assert result.satisfied is False
        assert "NOT FOUND" in result.evidence
        assert result.missing is not None

    def test_empty_slug(self, tmp_path: Path):
        result = check_plan_gate("", tmp_path)
        assert result.satisfied is False

    def test_invalid_working_dir(self):
        result = check_plan_gate("slug", "/nonexistent/path/xyz")
        assert result.satisfied is False


# ---------------------------------------------------------------------------
# check_build_gate
# ---------------------------------------------------------------------------


class TestCheckBuildGate:
    @patch("agent.goal_gates._run_gh_command")
    def test_pr_exists(self, mock_gh, tmp_path: Path):
        mock_gh.return_value = "1"
        result = check_build_gate("my-feature", tmp_path)
        assert result.satisfied is True
        assert "PR found" in result.evidence

    @patch("agent.goal_gates._run_gh_command")
    def test_no_pr(self, mock_gh, tmp_path: Path):
        mock_gh.return_value = "0"
        result = check_build_gate("my-feature", tmp_path)
        assert result.satisfied is False
        assert "No PR" in result.evidence

    @patch("agent.goal_gates._run_gh_command")
    def test_empty_output(self, mock_gh, tmp_path: Path):
        mock_gh.return_value = ""
        result = check_build_gate("my-feature", tmp_path)
        assert result.satisfied is False

    @patch("agent.goal_gates._run_gh_command")
    def test_gh_command_failure(self, mock_gh, tmp_path: Path):
        mock_gh.side_effect = subprocess.CalledProcessError(1, "gh")
        result = check_build_gate("my-feature", tmp_path)
        assert result.satisfied is False
        assert "check error" in (result.missing or "")

    @patch("agent.goal_gates._run_gh_command")
    def test_timeout(self, mock_gh, tmp_path: Path):
        mock_gh.side_effect = subprocess.TimeoutExpired("gh", 10)
        result = check_build_gate("my-feature", tmp_path)
        assert result.satisfied is False


# ---------------------------------------------------------------------------
# check_test_gate
# ---------------------------------------------------------------------------


class TestCheckTestGate:
    def test_session_history_has_test_completed(self):
        session = MagicMock()
        session.get_history_list.return_value = [
            "[stage] TEST COMPLETED",
            "Some other entry",
        ]
        result = check_test_gate("slug", session=session)
        assert result.satisfied is True
        assert "session history" in result.evidence

    def test_session_history_case_insensitive(self):
        session = MagicMock()
        session.get_history_list.return_value = [
            "[Stage] test Completed successfully",
        ]
        result = check_test_gate("slug", session=session)
        assert result.satisfied is True

    def test_session_history_no_test(self):
        session = MagicMock()
        session.get_history_list.return_value = [
            "[stage] BUILD COMPLETED",
        ]
        result = check_test_gate("slug", session=session)
        assert result.satisfied is False

    def test_no_session_no_state_file(self):
        result = check_test_gate("nonexistent-slug", session=None)
        assert result.satisfied is False
        assert result.missing is not None

    def test_pipeline_state_fallback(self, tmp_path: Path):
        """Test fallback to pipeline state.json when no session provided."""
        import agent.goal_gates as gg

        # Create state.json relative to the repo root detected by the module
        repo_root = Path(gg.__file__).parent.parent
        state_dir = repo_root / "data" / "pipeline" / "test-state-fallback"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "state.json"
        try:
            state_file.write_text(json.dumps({"completed_stages": ["plan", "build", "test"]}))
            result = check_test_gate("test-state-fallback", session=None)
            assert result.satisfied is True
            assert "pipeline state" in result.evidence
        finally:
            state_file.unlink(missing_ok=True)
            state_dir.rmdir()

    def test_session_history_exception_handled(self):
        session = MagicMock()
        session.get_history_list.side_effect = RuntimeError("Redis down")
        result = check_test_gate("slug", session=session)
        assert result.satisfied is False

    def test_session_history_non_string_entries(self):
        session = MagicMock()
        session.get_history_list.return_value = [
            42,
            None,
            {"key": "value"},
            "[stage] TEST COMPLETED",
        ]
        result = check_test_gate("slug", session=session)
        assert result.satisfied is True


# ---------------------------------------------------------------------------
# check_review_gate
# ---------------------------------------------------------------------------


class TestCheckReviewGate:
    @patch("agent.goal_gates._run_gh_command")
    def test_review_exists(self, mock_gh, tmp_path: Path):
        # Sequence: pr number, repo name, review count, review comment count
        mock_gh.side_effect = ["42", "owner/repo", "1", "0"]
        result = check_review_gate("my-feature", tmp_path)
        assert result.satisfied is True

    @patch("agent.goal_gates._run_gh_command")
    def test_review_comment_exists(self, mock_gh, tmp_path: Path):
        mock_gh.side_effect = ["42", "owner/repo", "0", "1"]
        result = check_review_gate("my-feature", tmp_path)
        assert result.satisfied is True

    @patch("agent.goal_gates._run_gh_command")
    def test_no_review(self, mock_gh, tmp_path: Path):
        mock_gh.side_effect = ["42", "owner/repo", "0", "0"]
        result = check_review_gate("my-feature", tmp_path)
        assert result.satisfied is False
        assert "No review" in result.evidence

    @patch("agent.goal_gates._run_gh_command")
    def test_no_pr_for_review(self, mock_gh, tmp_path: Path):
        mock_gh.return_value = ""
        result = check_review_gate("my-feature", tmp_path)
        assert result.satisfied is False
        assert "No PR" in result.evidence

    @patch("agent.goal_gates._run_gh_command")
    def test_gh_failure(self, mock_gh, tmp_path: Path):
        mock_gh.side_effect = subprocess.CalledProcessError(1, "gh")
        result = check_review_gate("my-feature", tmp_path)
        assert result.satisfied is False


# ---------------------------------------------------------------------------
# check_docs_gate
# ---------------------------------------------------------------------------


class TestCheckDocsGate:
    def test_feature_doc_exists(self, tmp_path: Path):
        docs_dir = tmp_path / "docs" / "features"
        docs_dir.mkdir(parents=True)
        (docs_dir / "my-feature.md").write_text("# Docs")

        result = check_docs_gate("my-feature", tmp_path)
        assert result.satisfied is True
        assert "Feature doc exists" in result.evidence

    def test_underscore_slug_converts_to_hyphens(self, tmp_path: Path):
        docs_dir = tmp_path / "docs" / "features"
        docs_dir.mkdir(parents=True)
        (docs_dir / "my-feature.md").write_text("# Docs")

        result = check_docs_gate("my_feature", tmp_path)
        assert result.satisfied is True

    def test_plan_skips_docs(self, tmp_path: Path):
        plan_dir = tmp_path / "docs" / "plans"
        plan_dir.mkdir(parents=True)
        (plan_dir / "my-feature.md").write_text(
            "## Documentation\nNo documentation changes needed - internal only."
        )

        result = check_docs_gate("my-feature", tmp_path)
        assert result.satisfied is True
        assert "skips docs" in result.evidence

    def test_plan_skips_docs_alternate_phrase(self, tmp_path: Path):
        plan_dir = tmp_path / "docs" / "plans"
        plan_dir.mkdir(parents=True)
        (plan_dir / "my-feature.md").write_text("No docs needed for this change.")

        result = check_docs_gate("my-feature", tmp_path)
        assert result.satisfied is True

    def test_no_docs_no_skip(self, tmp_path: Path):
        result = check_docs_gate("my-feature", tmp_path)
        assert result.satisfied is False
        assert result.missing is not None


# ---------------------------------------------------------------------------
# check_gate dispatcher
# ---------------------------------------------------------------------------


class TestCheckGate:
    def test_dispatches_to_plan(self, tmp_path: Path):
        plan_dir = tmp_path / "docs" / "plans"
        plan_dir.mkdir(parents=True)
        (plan_dir / "slug.md").write_text("plan")

        result = check_gate("PLAN", "slug", tmp_path)
        assert result.satisfied is True

    def test_case_insensitive_stage(self, tmp_path: Path):
        plan_dir = tmp_path / "docs" / "plans"
        plan_dir.mkdir(parents=True)
        (plan_dir / "slug.md").write_text("plan")

        result = check_gate("plan", "slug", tmp_path)
        assert result.satisfied is True

    def test_unknown_stage(self, tmp_path: Path):
        result = check_gate("UNKNOWN", "slug", tmp_path)
        assert result.satisfied is False
        assert "Unknown stage" in result.evidence

    @patch("agent.goal_gates._run_gh_command")
    def test_dispatches_to_build(self, mock_gh, tmp_path: Path):
        mock_gh.return_value = "1"
        result = check_gate("BUILD", "slug", tmp_path)
        assert result.satisfied is True

    def test_dispatches_to_test_with_session(self):
        session = MagicMock()
        session.get_history_list.return_value = ["[stage] TEST COMPLETED"]
        result = check_gate("TEST", "slug", ".", session=session)
        assert result.satisfied is True


# ---------------------------------------------------------------------------
# check_all_gates
# ---------------------------------------------------------------------------


class TestCheckAllGates:
    @patch("agent.goal_gates._run_gh_command")
    def test_returns_all_stages(self, mock_gh, tmp_path: Path):
        mock_gh.return_value = "0"  # All gh commands return 0 (no PRs)
        results = check_all_gates("slug", tmp_path)
        assert set(results.keys()) == set(GATE_STAGES)

    @patch("agent.goal_gates._run_gh_command")
    def test_does_not_short_circuit(self, mock_gh, tmp_path: Path):
        """All gates run even if early ones fail."""
        mock_gh.return_value = "0"
        results = check_all_gates("slug", tmp_path)
        # All should have results (not just the first failure)
        assert len(results) == len(GATE_STAGES)

    @patch("agent.goal_gates._run_gh_command")
    def test_mixed_results(self, mock_gh, tmp_path: Path):
        # Set up plan to pass
        plan_dir = tmp_path / "docs" / "plans"
        plan_dir.mkdir(parents=True)
        (plan_dir / "slug.md").write_text("plan")

        mock_gh.return_value = "0"  # No PRs, no reviews
        results = check_all_gates("slug", tmp_path)

        assert results["PLAN"].satisfied is True
        assert results["BUILD"].satisfied is False

    def test_gate_stages_constant(self):
        """GATE_STAGES contains the expected stages in order."""
        assert GATE_STAGES == ["PLAN", "BUILD", "TEST", "REVIEW", "DOCS"]
