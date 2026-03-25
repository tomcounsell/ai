"""Unit tests for utils/issue_comments.py."""

import json
import logging
import subprocess
from unittest.mock import patch

from utils.issue_comments import (
    STAGE_COMMENT_MARKER,
    fetch_stage_comments,
    format_prior_context,
    format_stage_comment,
    post_stage_comment,
)


class TestFormatStageComment:
    """Test format_stage_comment output."""

    def test_basic_format(self):
        body = format_stage_comment("BUILD", "PR opened successfully")
        assert STAGE_COMMENT_MARKER in body
        assert "## Stage: BUILD" in body
        assert "**Outcome:** PR opened successfully" in body

    def test_with_findings(self):
        body = format_stage_comment(
            "TEST",
            "All tests pass",
            findings=["Edge case in auth middleware", "Coverage at 95%"],
        )
        assert "### Key Findings" in body
        assert "- Edge case in auth middleware" in body
        assert "- Coverage at 95%" in body

    def test_empty_findings_shows_placeholder(self):
        body = format_stage_comment("BUILD", "Done")
        assert "No notable findings" in body

    def test_with_files(self):
        body = format_stage_comment(
            "BUILD",
            "Done",
            files=["src/main.py", "tests/test_main.py"],
        )
        assert "### Files Modified" in body
        assert "- `src/main.py`" in body

    def test_with_notes(self):
        body = format_stage_comment(
            "BUILD",
            "Done",
            notes="Watch out for flaky test in CI",
        )
        assert "### Notes for Next Stage" in body
        assert "Watch out for flaky test in CI" in body


class TestFetchStageComments:
    """Test fetch_stage_comments with mocked subprocess."""

    def test_returns_empty_for_no_issue(self):
        result = fetch_stage_comments(0)
        assert result == []

    def test_returns_empty_for_none_issue(self):
        result = fetch_stage_comments(None)
        assert result == []

    @patch.dict("os.environ", {"GH_REPO": "owner/repo"})
    @patch("utils.issue_comments.subprocess.run")
    def test_parses_stage_comments(self, mock_run):
        stage_body = format_stage_comment("BUILD", "PR created")
        comments_json = json.dumps(
            [
                {"body": "Normal comment without marker"},
                {"body": stage_body},
            ]
        )

        # First call (--jq) returns non-empty to pass the check
        mock_jq_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=stage_body, stderr=""
        )
        # Second call (JSON) returns full comment list
        mock_json_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=comments_json, stderr=""
        )
        mock_run.side_effect = [mock_jq_result, mock_json_result]

        result = fetch_stage_comments(42)
        assert len(result) == 1
        assert result[0]["stage"] == "BUILD"
        assert result[0]["outcome"] == "PR created"

    @patch.dict("os.environ", {"GH_REPO": "owner/repo"})
    @patch("utils.issue_comments.subprocess.run")
    def test_handles_gh_failure(self, mock_run, caplog):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="auth error"
        )
        with caplog.at_level(logging.WARNING):
            result = fetch_stage_comments(42)
        assert result == []

    @patch.dict("os.environ", {"GH_REPO": "owner/repo"})
    @patch("utils.issue_comments.subprocess.run")
    def test_handles_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=10)
        result = fetch_stage_comments(42)
        assert result == []

    @patch.dict("os.environ", {}, clear=False)
    def test_returns_empty_without_repo(self, monkeypatch):
        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.delenv("SDLC_REPO", raising=False)
        result = fetch_stage_comments(42)
        assert result == []

    @patch.dict("os.environ", {"GH_REPO": "owner/repo"})
    @patch("utils.issue_comments.subprocess.run")
    def test_returns_empty_for_no_comments(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        result = fetch_stage_comments(42)
        assert result == []


class TestPostStageComment:
    """Test post_stage_comment with mocked subprocess."""

    def test_returns_false_for_no_issue(self):
        assert post_stage_comment(0, "BUILD", "Done") is False

    def test_returns_false_for_none_issue(self):
        assert post_stage_comment(None, "BUILD", "Done") is False

    @patch.dict("os.environ", {"GH_REPO": "owner/repo"})
    @patch("utils.issue_comments.subprocess.run")
    def test_posts_comment_successfully(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        result = post_stage_comment(42, "BUILD", "PR created")
        assert result is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "gh" in call_args
        assert "42" in call_args
        assert "--repo" in call_args

    @patch.dict("os.environ", {"GH_REPO": "owner/repo"})
    @patch("utils.issue_comments.subprocess.run")
    def test_returns_false_on_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error"
        )
        result = post_stage_comment(42, "BUILD", "Done")
        assert result is False

    @patch.dict("os.environ", {"GH_REPO": "owner/repo"})
    @patch("utils.issue_comments.subprocess.run")
    def test_handles_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=10)
        result = post_stage_comment(42, "BUILD", "Done")
        assert result is False

    @patch.dict("os.environ", {}, clear=False)
    def test_returns_false_without_repo(self, monkeypatch):
        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.delenv("SDLC_REPO", raising=False)
        result = post_stage_comment(42, "BUILD", "Done")
        assert result is False


class TestFormatPriorContext:
    """Test format_prior_context helper."""

    def test_empty_comments(self):
        assert format_prior_context([]) == ""

    def test_formats_comments(self):
        comments = [
            {"stage": "PLAN", "outcome": "Plan created", "body": "..."},
            {"stage": "BUILD", "outcome": "PR opened", "body": "..."},
        ]
        result = format_prior_context(comments)
        assert "Prior Stage Findings" in result
        assert "**PLAN**: Plan created" in result
        assert "**BUILD**: PR opened" in result

    def test_limits_to_max_comments(self):
        comments = [
            {"stage": f"STAGE{i}", "outcome": f"Done {i}", "body": "..."} for i in range(10)
        ]
        result = format_prior_context(comments, max_comments=3)
        # Should only include the last 3
        assert "STAGE7" in result
        assert "STAGE8" in result
        assert "STAGE9" in result
        assert "STAGE0" not in result
