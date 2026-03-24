"""Tests for Finding model (models/finding.py).

Verifies model definition, safe_save, WriteFilter gating,
bloom checks, and query_by_slug.
"""

from unittest.mock import MagicMock, patch


class TestFindingModel:
    """Finding model basic operations."""

    def test_model_has_expected_fields(self):
        """Finding model should have all planned fields."""
        from models.finding import Finding

        # Check field names exist on the model class
        assert hasattr(Finding, "finding_id")
        assert hasattr(Finding, "slug")
        assert hasattr(Finding, "project_key")
        assert hasattr(Finding, "session_id")
        assert hasattr(Finding, "stage")
        assert hasattr(Finding, "category")
        assert hasattr(Finding, "content")
        assert hasattr(Finding, "file_paths")
        assert hasattr(Finding, "importance")
        assert hasattr(Finding, "relevance")
        assert hasattr(Finding, "confidence")
        assert hasattr(Finding, "bloom")
        assert hasattr(Finding, "associations")

    def test_valid_categories_defined(self):
        """Valid category constants should be defined."""
        from models.finding import (
            CATEGORY_ARTIFACT_PRODUCED,
            CATEGORY_DECISION_MADE,
            CATEGORY_DEPENDENCY_DISCOVERED,
            CATEGORY_FILE_EXAMINED,
            CATEGORY_PATTERN_FOUND,
            VALID_CATEGORIES,
        )

        assert CATEGORY_FILE_EXAMINED in VALID_CATEGORIES
        assert CATEGORY_PATTERN_FOUND in VALID_CATEGORIES
        assert CATEGORY_DECISION_MADE in VALID_CATEGORIES
        assert CATEGORY_ARTIFACT_PRODUCED in VALID_CATEGORIES
        assert CATEGORY_DEPENDENCY_DISCOVERED in VALID_CATEGORIES

    def test_compute_filter_score_returns_importance(self):
        """compute_filter_score should return the importance value."""
        from models.finding import Finding

        f = Finding.__new__(Finding)
        f.importance = 5.0
        assert f.compute_filter_score() == 5.0

    def test_compute_filter_score_handles_none(self):
        """compute_filter_score should return 0.0 for None importance."""
        from models.finding import Finding

        f = Finding.__new__(Finding)
        f.importance = None
        assert f.compute_filter_score() == 0.0


class TestFindingSafeSave:
    """Finding.safe_save() error handling."""

    @patch("models.finding.Finding.save")
    def test_safe_save_returns_instance_on_success(self, mock_save):
        """safe_save should return a Finding instance when save succeeds."""
        from models.finding import Finding

        mock_save.return_value = True
        result = Finding.safe_save(
            slug="test-slug",
            project_key="test",
            session_id="sess-1",
            content="test finding",
            importance=5.0,
        )
        # Should return a Finding (not None)
        assert result is not None

    @patch("models.finding.Finding.save")
    def test_safe_save_returns_none_when_filtered(self, mock_save):
        """safe_save should return None when WriteFilter rejects the save."""
        from models.finding import Finding

        mock_save.return_value = False
        result = Finding.safe_save(
            slug="test-slug",
            project_key="test",
            session_id="sess-1",
            content="trivial",
            importance=0.01,
        )
        assert result is None

    def test_safe_save_returns_none_on_exception(self):
        """safe_save should catch exceptions and return None."""
        from models.finding import Finding

        with patch("models.finding.Finding.save", side_effect=Exception("Redis down")):
            result = Finding.safe_save(
                slug="test-slug",
                project_key="test",
                session_id="sess-1",
                content="test",
            )
            assert result is None


class TestFindingQueryBySlug:
    """Finding.query_by_slug() behavior."""

    @patch("models.finding.Finding.query")
    def test_returns_findings_for_slug(self, mock_query):
        """query_by_slug should return findings filtered by slug."""
        from models.finding import Finding

        mock_f1 = MagicMock()
        mock_f2 = MagicMock()
        mock_query.filter.return_value = [mock_f1, mock_f2]

        results = Finding.query_by_slug("my-slug", limit=10)
        assert len(results) == 2
        mock_query.filter.assert_called_once_with(slug="my-slug")

    @patch("models.finding.Finding.query")
    def test_returns_empty_on_error(self, mock_query):
        """query_by_slug should return empty list on error."""
        from models.finding import Finding

        mock_query.filter.side_effect = Exception("Redis down")
        results = Finding.query_by_slug("my-slug")
        assert results == []

    @patch("models.finding.Finding.query")
    def test_respects_limit(self, mock_query):
        """query_by_slug should respect the limit parameter."""
        from models.finding import Finding

        mock_query.filter.return_value = [MagicMock() for _ in range(20)]
        results = Finding.query_by_slug("my-slug", limit=5)
        assert len(results) == 5
