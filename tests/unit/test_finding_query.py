"""Tests for finding query (agent/finding_query.py).

Verifies composite scoring, format functions, and error handling.
"""

from unittest.mock import MagicMock, patch


class TestQueryFindings:
    """query_findings() behavior."""

    def test_returns_empty_for_empty_slug(self):
        """Should return empty list when slug is empty."""
        from agent.finding_query import query_findings

        assert query_findings("") == []
        assert query_findings(None) == []

    @patch("models.finding.Finding.query_by_slug")
    def test_returns_scored_findings(self, mock_query):
        """Should return findings sorted by composite score."""
        from agent.finding_query import query_findings

        f1 = MagicMock()
        f1.importance = 8.0
        f1.confidence = 0.8
        f1._at_access_count = 5
        f1.content = "high importance finding about authentication"
        f1.file_paths = "auth/main.py"

        f2 = MagicMock()
        f2.importance = 2.0
        f2.confidence = 0.3
        f2._at_access_count = 0
        f2.content = "low importance finding"
        f2.file_paths = ""

        mock_query.return_value = [f2, f1]  # Reversed to test sorting

        result = query_findings("test-slug", limit=10)
        assert len(result) == 2
        # Higher importance should come first
        assert result[0].importance == 8.0

    @patch("models.finding.Finding.query_by_slug")
    def test_respects_limit(self, mock_query):
        """Should return at most 'limit' findings."""
        from agent.finding_query import query_findings

        findings = []
        for i in range(10):
            f = MagicMock()
            f.importance = float(10 - i)
            f.confidence = 0.5
            f._at_access_count = 0
            f.content = f"finding {i}"
            f.file_paths = ""
            findings.append(f)
        mock_query.return_value = findings

        result = query_findings("test-slug", limit=3)
        assert len(result) == 3

    @patch("models.finding.Finding.query_by_slug")
    def test_handles_query_error(self, mock_query):
        """Should return empty list on query error."""
        from agent.finding_query import query_findings

        mock_query.side_effect = Exception("Redis down")
        result = query_findings("test-slug")
        assert result == []


class TestFormatFindingsForInjection:
    """format_findings_for_injection() formatting."""

    def test_returns_none_for_empty_list(self):
        """Should return None when no findings provided."""
        from agent.finding_query import format_findings_for_injection

        assert format_findings_for_injection([]) is None

    def test_formats_findings_with_metadata(self):
        """Should format findings with stage, category, and file paths."""
        from agent.finding_query import format_findings_for_injection

        f1 = MagicMock()
        f1.content = "Auth uses JWT RS256"
        f1.stage = "BUILD"
        f1.category = "pattern_found"
        f1.file_paths = "auth/jwt.py"

        result = format_findings_for_injection([f1])
        assert result is not None
        assert "Prior Findings" in result
        assert "BUILD" in result
        assert "pattern_found" in result
        assert "auth/jwt.py" in result
        assert "Auth uses JWT RS256" in result

    def test_respects_token_budget(self):
        """Should stop adding findings when token budget is exceeded."""
        from agent.finding_query import format_findings_for_injection

        findings = []
        for i in range(100):
            f = MagicMock()
            f.content = "x" * 200
            f.stage = "BUILD"
            f.category = "pattern_found"
            f.file_paths = ""
            findings.append(f)

        result = format_findings_for_injection(findings, max_tokens=100)
        # With 100 tokens (~400 chars), should include very few findings
        assert result is not None
        assert len(result) < 500


class TestCompositeScore:
    """_composite_score() scoring logic."""

    def test_higher_importance_gets_higher_score(self):
        """Higher importance findings should score higher."""
        from agent.finding_query import _composite_score

        f_high = MagicMock()
        f_high.importance = 9.0
        f_high.confidence = 0.5
        f_high._at_access_count = 0
        f_high.content = "test"
        f_high.file_paths = ""

        f_low = MagicMock()
        f_low.importance = 1.0
        f_low.confidence = 0.5
        f_low._at_access_count = 0
        f_low.content = "test"
        f_low.file_paths = ""

        assert _composite_score(f_high) > _composite_score(f_low)

    def test_topic_match_boosts_score(self):
        """Finding matching topic keywords should score higher."""
        from agent.finding_query import _composite_score

        f = MagicMock()
        f.importance = 5.0
        f.confidence = 0.5
        f._at_access_count = 0
        f.content = "authentication module uses JWT"
        f.file_paths = "auth/jwt.py"

        score_with_topic = _composite_score(f, topics=["auth", "jwt"])
        score_without_topic = _composite_score(f, topics=["database", "migration"])

        assert score_with_topic > score_without_topic
