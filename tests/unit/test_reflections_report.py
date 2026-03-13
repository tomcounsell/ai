"""Tests for reflections GitHub issue reporting."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from scripts.reflections_report import (
    create_reflections_issue,
    format_report_body,
    issue_exists_for_date,
    reset_dedup_guard,
)


class TestFormatReportBody:
    """Tests for report body formatting."""

    def test_formats_findings_by_category(self):
        findings = {
            "legacy_code": ["Found 5 TODO comments"],
            "log_review": ["bridge.log: 3 errors in recent logs"],
        }
        body = format_report_body(findings, "2026-02-17")
        assert "## Legacy Code" in body
        assert "Found 5 TODO comments" in body
        assert "## Log Review" in body
        assert "bridge.log: 3 errors in recent logs" in body

    def test_empty_findings_returns_minimal_body(self):
        body = format_report_body({}, "2026-02-17")
        assert "No significant findings" in body

    def test_includes_date_in_body(self):
        body = format_report_body({"test": ["item"]}, "2026-02-17")
        assert "2026-02-17" in body

    def test_skips_empty_categories(self):
        findings = {
            "legacy_code": ["Found stuff"],
            "empty_cat": [],
        }
        body = format_report_body(findings, "2026-02-17")
        assert "## Legacy Code" in body
        assert "Empty Cat" not in body


class TestIssueExistsForDate:
    """Tests for checking if reflections issue already exists."""

    @patch("scripts.reflections_report.subprocess.run")
    def test_returns_true_when_issue_found(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="123\tReflections Report - 2026-02-17\topen\n",
        )
        assert issue_exists_for_date("2026-02-17") is True

    @patch("scripts.reflections_report.subprocess.run")
    def test_returns_false_when_no_issue(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="\n",
        )
        assert issue_exists_for_date("2026-02-17") is False

    @patch("scripts.reflections_report.subprocess.run")
    def test_returns_false_on_empty_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
        )
        assert issue_exists_for_date("2026-02-17") is False

    @patch("scripts.reflections_report.subprocess.run")
    def test_returns_false_on_error(self, mock_run):
        mock_run.side_effect = subprocess.SubprocessError("gh not found")
        assert issue_exists_for_date("2026-02-17") is False

    @patch("scripts.reflections_report.subprocess.run")
    def test_passes_cwd_to_subprocess(self, mock_run):
        """issue_exists_for_date passes cwd to subprocess.run for correct repo targeting."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        issue_exists_for_date("2026-02-17", cwd="/tmp/myproject")
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs.get("cwd") == "/tmp/myproject"

    @patch("scripts.reflections_report.subprocess.run")
    def test_cwd_defaults_to_none(self, mock_run):
        """Without cwd argument, subprocess.run gets cwd=None (default repo)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        issue_exists_for_date("2026-02-17")
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs.get("cwd") is None


class TestCreateReflectionsIssue:
    """Tests for GitHub issue creation."""

    def setup_method(self):
        """Reset dedup guard before each test."""
        reset_dedup_guard()

    @patch("scripts.reflections_report.subprocess.run")
    @patch("scripts.reflections_report.issue_exists_for_date", return_value=False)
    def test_creates_issue_with_findings(self, mock_exists, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/org/repo/issues/42\n"
        )
        findings = {"legacy_code": ["Found 5 TODOs"]}
        result = create_reflections_issue(findings, "2026-02-17")
        # Returns the issue URL string on success
        assert result == "https://github.com/org/repo/issues/42"
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "gh" in call_args
        assert "issue" in call_args
        assert "create" in call_args

    @patch("scripts.reflections_report.issue_exists_for_date", return_value=True)
    def test_skips_when_issue_exists(self, mock_exists):
        findings = {"legacy_code": ["Found 5 TODOs"]}
        result = create_reflections_issue(findings, "2026-02-17")
        assert result is False

    @patch("scripts.reflections_report.issue_exists_for_date", return_value=False)
    def test_skips_when_no_findings(self, mock_exists):
        result = create_reflections_issue({}, "2026-02-17")
        assert result is False

    @patch("scripts.reflections_report.issue_exists_for_date", return_value=False)
    def test_skips_when_all_categories_empty(self, mock_exists):
        result = create_reflections_issue({"a": [], "b": []}, "2026-02-17")
        assert result is False

    @patch("scripts.reflections_report.subprocess.run")
    @patch("scripts.reflections_report.issue_exists_for_date", return_value=False)
    def test_returns_false_on_gh_failure(self, mock_exists, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="auth error")
        findings = {"test": ["finding"]}
        result = create_reflections_issue(findings, "2026-02-17")
        assert result is False

    @patch("scripts.reflections_report.subprocess.run")
    @patch("scripts.reflections_report.issue_exists_for_date", return_value=False)
    def test_passes_cwd_to_issue_exists_check(self, mock_exists, mock_run):
        """create_reflections_issue forwards cwd to issue_exists_for_date."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/org/repo/issues/1\n"
        )
        findings = {"test": ["finding"]}
        create_reflections_issue(findings, "2026-02-17", cwd="/tmp/proj")
        mock_exists.assert_called_once_with("2026-02-17", cwd="/tmp/proj")


class TestDedupGuard:
    """Tests for in-memory dedup guard preventing race condition duplicates."""

    def setup_method(self):
        """Reset dedup guard before each test."""
        reset_dedup_guard()

    @patch("scripts.reflections_report.subprocess.run")
    @patch("scripts.reflections_report.issue_exists_for_date", return_value=False)
    def test_second_create_same_date_cwd_is_skipped(self, mock_exists, mock_run):
        """After creating an issue, a second call with same date+cwd is skipped."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/org/repo/issues/1\n"
        )
        findings = {"test": ["finding"]}

        # First call succeeds
        result1 = create_reflections_issue(findings, "2026-02-17", cwd="/tmp/proj")
        assert isinstance(result1, str)

        # Second call with same date+cwd is skipped by dedup guard
        result2 = create_reflections_issue(findings, "2026-02-17", cwd="/tmp/proj")
        assert result2 is False

        # subprocess.run (for gh issue create) should only be called once
        assert mock_run.call_count == 1

    @patch("scripts.reflections_report.subprocess.run")
    @patch("scripts.reflections_report.issue_exists_for_date", return_value=False)
    def test_different_cwd_is_not_blocked(self, mock_exists, mock_run):
        """Different cwd values create separate issues (different repos)."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/org/repo/issues/1\n"
        )
        findings = {"test": ["finding"]}

        result1 = create_reflections_issue(findings, "2026-02-17", cwd="/tmp/proj-a")
        result2 = create_reflections_issue(findings, "2026-02-17", cwd="/tmp/proj-b")

        assert isinstance(result1, str)
        assert isinstance(result2, str)
        assert mock_run.call_count == 2

    def test_reset_clears_guard(self):
        """reset_dedup_guard clears all tracked entries."""
        from scripts.reflections_report import _created_this_run

        _created_this_run.add(("2026-02-17", "/tmp/proj"))
        assert len(_created_this_run) == 1

        reset_dedup_guard()
        assert len(_created_this_run) == 0

    @patch("scripts.reflections_report.subprocess.run")
    @patch("scripts.reflections_report.issue_exists_for_date", return_value=False)
    def test_failed_create_does_not_add_to_guard(self, mock_exists, mock_run):
        """Failed gh issue create does not add to dedup guard, allowing retry."""
        mock_run.return_value = MagicMock(returncode=1, stderr="auth error")
        findings = {"test": ["finding"]}

        result = create_reflections_issue(findings, "2026-02-17", cwd="/tmp/proj")
        assert result is False

        # Guard should be empty since creation failed
        from scripts.reflections_report import _created_this_run

        assert ("2026-02-17", "/tmp/proj") not in _created_this_run
