"""Tests for the issue poller and dedup engine."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from scripts.issue_dedup import (
    DUPLICATE_THRESHOLD,
    RELATED_THRESHOLD,
    classify_similarity,
    compare_issues,
)
from scripts.issue_poller import (
    AGENT_D_SIGNATURE,
    filter_new_issues,
    has_sufficient_context,
    load_projects,
    process_issue,
)

# --- Dedup Engine Tests ---


class TestClassifySimilarity:
    """Test similarity classification thresholds."""

    def test_duplicate_threshold(self):
        assert classify_similarity(0.9) == "duplicate"
        assert classify_similarity(0.8) == "duplicate"
        assert classify_similarity(1.0) == "duplicate"

    def test_related_threshold(self):
        assert classify_similarity(0.7) == "related"
        assert classify_similarity(0.5) == "related"
        assert classify_similarity(0.6) == "related"

    def test_unique_threshold(self):
        assert classify_similarity(0.4) == "unique"
        assert classify_similarity(0.0) == "unique"
        assert classify_similarity(0.49) == "unique"

    def test_boundary_values(self):
        assert classify_similarity(DUPLICATE_THRESHOLD) == "duplicate"
        assert classify_similarity(RELATED_THRESHOLD) == "related"
        assert classify_similarity(RELATED_THRESHOLD - 0.01) == "unique"


class TestCompareIssues:
    """Test issue comparison logic."""

    @patch("scripts.issue_dedup.score_similarity")
    def test_no_existing_issues(self, mock_score):
        result = compare_issues("title", "body", [])
        assert result is None
        mock_score.assert_not_called()

    @patch("scripts.issue_dedup.score_similarity")
    def test_unique_result_returns_none(self, mock_score):
        mock_score.return_value = 0.2
        result = compare_issues(
            "New feature",
            "Add dark mode",
            [{"number": 1, "title": "Fix bug", "body": "Fix login bug"}],
        )
        assert result is None

    @patch("scripts.issue_dedup.score_similarity")
    def test_duplicate_detected(self, mock_score):
        mock_score.return_value = 0.9
        result = compare_issues(
            "Add dark mode",
            "Implement dark mode toggle",
            [{"number": 5, "title": "Dark mode", "body": "We need dark mode"}],
        )
        assert result is not None
        assert result["classification"] == "duplicate"
        assert result["match_number"] == 5
        assert result["score"] == 0.9

    @patch("scripts.issue_dedup.score_similarity")
    def test_related_detected(self, mock_score):
        mock_score.return_value = 0.6
        result = compare_issues(
            "Improve theme support",
            "Add more themes",
            [{"number": 5, "title": "Dark mode", "body": "We need dark mode"}],
        )
        assert result is not None
        assert result["classification"] == "related"

    @patch("scripts.issue_dedup.score_similarity")
    def test_best_match_selected(self, mock_score):
        mock_score.side_effect = [0.3, 0.9, 0.5]
        result = compare_issues(
            "Test issue",
            "Test body",
            [
                {"number": 1, "title": "A", "body": "a"},
                {"number": 2, "title": "B", "body": "b"},
                {"number": 3, "title": "C", "body": "c"},
            ],
        )
        assert result is not None
        assert result["match_number"] == 2
        assert result["score"] == 0.9

    @patch("scripts.issue_dedup.score_similarity")
    def test_api_failure_skips_comparison(self, mock_score):
        mock_score.side_effect = Exception("API error")
        result = compare_issues(
            "Test",
            "Body",
            [{"number": 1, "title": "A", "body": "a"}],
        )
        assert result is None


# --- Poller Tests ---


class TestHasSufficientContext:
    """Test issue context validation."""

    def test_good_issue(self):
        assert has_sufficient_context(
            {
                "title": "Add dark mode",
                "body": "We need a dark mode toggle in settings to support user preference.",
            }
        )

    def test_empty_body(self):
        assert not has_sufficient_context({"title": "Add dark mode", "body": ""})

    def test_none_body(self):
        assert not has_sufficient_context({"title": "Add dark mode", "body": None})

    def test_short_body(self):
        assert not has_sufficient_context({"title": "Fix bug", "body": "Fix it"})

    def test_no_title(self):
        assert not has_sufficient_context(
            {"title": "", "body": "Long description here that should be enough"}
        )

    def test_body_at_threshold(self):
        # Exactly 20 chars
        assert has_sufficient_context({"title": "Test", "body": "12345678901234567890"})

    def test_body_below_threshold(self):
        assert not has_sufficient_context({"title": "Test", "body": "1234567890123456789"})


class TestFilterNewIssues:
    """Test seen-issue filtering."""

    def test_filters_seen_issues(self):
        r = MagicMock()
        r.sismember.side_effect = lambda key, num: num == "1"

        issues = [
            {"number": 1, "title": "Old"},
            {"number": 2, "title": "New"},
            {"number": 3, "title": "Also New"},
        ]
        result = filter_new_issues(r, "org", "repo", issues)
        assert len(result) == 2
        assert result[0]["number"] == 2
        assert result[1]["number"] == 3

    def test_all_seen(self):
        r = MagicMock()
        r.sismember.return_value = True

        issues = [{"number": 1}, {"number": 2}]
        result = filter_new_issues(r, "org", "repo", issues)
        assert len(result) == 0

    def test_none_seen(self):
        r = MagicMock()
        r.sismember.return_value = False

        issues = [{"number": 1}, {"number": 2}]
        result = filter_new_issues(r, "org", "repo", issues)
        assert len(result) == 2


class TestLoadProjects:
    """Test project configuration loading."""

    def test_loads_projects_with_github(self, tmp_path):
        config = {
            "projects": {
                "proj1": {
                    "name": "Project 1",
                    "github": {"org": "myorg", "repo": "myrepo"},
                    "telegram": {"groups": ["Dev: Proj1"]},
                },
                "proj2": {
                    "name": "Project 2",
                    # No github key - should be excluded
                    "telegram": {"groups": []},
                },
            }
        }
        config_file = tmp_path / "projects.json"
        config_file.write_text(json.dumps(config))

        projects = load_projects(config_file)
        assert len(projects) == 1
        assert projects[0]["org"] == "myorg"
        assert projects[0]["repo"] == "myrepo"
        assert projects[0]["telegram_groups"] == ["Dev: Proj1"]


class TestProcessIssue:
    """Test the main issue processing logic."""

    @patch("scripts.issue_poller.dispatch_plan_creation")
    @patch("scripts.issue_poller.apply_label")
    @patch("scripts.issue_poller.send_telegram_notification")
    @patch("scripts.issue_poller.compare_issues")
    @patch("scripts.issue_poller.check_existing_plan")
    @patch("scripts.issue_poller.get_latest_comment_id")
    def test_plans_new_unique_issue(
        self,
        mock_comment_id,
        mock_existing,
        mock_compare,
        mock_notify,
        mock_label,
        mock_dispatch,
    ):
        r = MagicMock()
        mock_existing.return_value = False
        mock_compare.return_value = None  # unique
        mock_comment_id.return_value = "12345"
        mock_dispatch.return_value = True

        result = process_issue(
            r,
            "org",
            "repo",
            {
                "number": 1,
                "title": "New feature",
                "body": "Detailed description of the new feature we need",
            },
            [],
            [],
        )
        assert result == "planned"
        mock_dispatch.assert_called_once()
        r.sadd.assert_called()

    @patch("scripts.issue_poller.add_comment")
    @patch("scripts.issue_poller.apply_label")
    @patch("scripts.issue_poller.send_telegram_notification")
    @patch("scripts.issue_poller.check_existing_plan")
    def test_flags_insufficient_context(self, mock_existing, mock_notify, mock_label, mock_comment):
        r = MagicMock()
        mock_existing.return_value = False

        result = process_issue(
            r,
            "org",
            "repo",
            {"number": 1, "title": "Bug", "body": "Fix it"},
            [],
            [],
        )
        assert result == "needs-review"
        mock_label.assert_called_with("org", "repo", 1, "needs-review")

    @patch("scripts.issue_poller.check_existing_plan")
    def test_skips_existing_plan(self, mock_existing):
        r = MagicMock()
        mock_existing.return_value = True

        result = process_issue(
            r, "org", "repo", {"number": 1, "title": "Test", "body": "Test body text"}, [], []
        )
        assert result == "skipped"


class TestAgentDFiltering:
    """Test that Agent D automated comments are properly filtered."""

    def test_agent_d_signature_constant(self):
        """Verify the Agent D signature matches expected format."""
        assert "_Auto-posted by /do-docs cascade_" in AGENT_D_SIGNATURE
