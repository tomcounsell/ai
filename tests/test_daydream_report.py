"""Tests for daydream GitHub issue reporting."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from scripts.daydream_report import (
    create_daydream_issue,
    format_report_body,
    issue_exists_for_date,
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
    """Tests for checking if daydream issue already exists."""

    @patch("scripts.daydream_report.subprocess.run")
    def test_returns_true_when_issue_found(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="123\tDaydream Report - 2026-02-17\topen\n",
        )
        assert issue_exists_for_date("2026-02-17") is True

    @patch("scripts.daydream_report.subprocess.run")
    def test_returns_false_when_no_issue(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="\n",
        )
        assert issue_exists_for_date("2026-02-17") is False

    @patch("scripts.daydream_report.subprocess.run")
    def test_returns_false_on_empty_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
        )
        assert issue_exists_for_date("2026-02-17") is False

    @patch("scripts.daydream_report.subprocess.run")
    def test_returns_false_on_error(self, mock_run):
        mock_run.side_effect = subprocess.SubprocessError("gh not found")
        assert issue_exists_for_date("2026-02-17") is False


class TestCreateDaydreamIssue:
    """Tests for GitHub issue creation."""

    @patch("scripts.daydream_report.subprocess.run")
    @patch("scripts.daydream_report.issue_exists_for_date", return_value=False)
    def test_creates_issue_with_findings(self, mock_exists, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/org/repo/issues/42\n"
        )
        findings = {"legacy_code": ["Found 5 TODOs"]}
        result = create_daydream_issue(findings, "2026-02-17")
        assert result is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "gh" in call_args
        assert "issue" in call_args
        assert "create" in call_args

    @patch("scripts.daydream_report.issue_exists_for_date", return_value=True)
    def test_skips_when_issue_exists(self, mock_exists):
        findings = {"legacy_code": ["Found 5 TODOs"]}
        result = create_daydream_issue(findings, "2026-02-17")
        assert result is False

    @patch("scripts.daydream_report.issue_exists_for_date", return_value=False)
    def test_skips_when_no_findings(self, mock_exists):
        result = create_daydream_issue({}, "2026-02-17")
        assert result is False

    @patch("scripts.daydream_report.issue_exists_for_date", return_value=False)
    def test_skips_when_all_categories_empty(self, mock_exists):
        result = create_daydream_issue({"a": [], "b": []}, "2026-02-17")
        assert result is False

    @patch("scripts.daydream_report.subprocess.run")
    @patch("scripts.daydream_report.issue_exists_for_date", return_value=False)
    def test_returns_false_on_gh_failure(self, mock_exists, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="auth error")
        findings = {"test": ["finding"]}
        result = create_daydream_issue(findings, "2026-02-17")
        assert result is False
