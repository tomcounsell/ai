"""Tests for finding extraction (agent/finding_extraction.py).

Verifies Haiku-based extraction, deduplication, and error handling.
"""

import json
from unittest.mock import MagicMock, patch


class TestExtractFindingsFromOutput:
    """extract_findings_from_output() behavior."""

    def test_returns_empty_on_no_output(self):
        """Should return empty list when output is empty."""
        from agent.finding_extraction import extract_findings_from_output

        result = extract_findings_from_output(
            output="",
            slug="test-slug",
            stage="BUILD",
            session_id="sess-1",
            project_key="test",
        )
        assert result == []

    def test_returns_empty_on_no_slug(self):
        """Should return empty list when slug is empty."""
        from agent.finding_extraction import extract_findings_from_output

        result = extract_findings_from_output(
            output="some output",
            slug="",
            stage="BUILD",
            session_id="sess-1",
            project_key="test",
        )
        assert result == []

    @patch("agent.finding_extraction._call_haiku_extraction")
    @patch("agent.finding_extraction._deduplicate_and_save")
    @patch("agent.finding_extraction._update_co_occurrences")
    def test_extracts_and_saves_findings(self, mock_cooc, mock_dedup, mock_haiku):
        """Should extract findings via Haiku and save each one."""
        from agent.finding_extraction import extract_findings_from_output

        mock_haiku.return_value = [
            {
                "category": "pattern_found",
                "content": "Auth uses JWT RS256",
                "file_paths": "auth/jwt.py",
                "importance": 5.0,
            },
            {
                "category": "file_examined",
                "content": "Config loaded from env vars",
                "file_paths": "config/settings.py",
                "importance": 3.0,
            },
        ]
        mock_dedup.side_effect = [
            {"finding_id": "f1", "category": "pattern_found", "content": "Auth uses JWT RS256"},
            {
                "finding_id": "f2",
                "category": "file_examined",
                "content": "Config loaded from env vars",
            },
        ]

        result = extract_findings_from_output(
            output="Built auth module with JWT signing",
            slug="auth-feature",
            stage="BUILD",
            session_id="sess-1",
            project_key="test",
        )

        assert len(result) == 2
        assert mock_haiku.called
        assert mock_dedup.call_count == 2
        mock_cooc.assert_called_once()

    @patch("agent.finding_extraction._call_haiku_extraction")
    def test_handles_haiku_failure_gracefully(self, mock_haiku):
        """Should return empty list when Haiku fails."""
        from agent.finding_extraction import extract_findings_from_output

        mock_haiku.side_effect = Exception("API error")

        result = extract_findings_from_output(
            output="some output",
            slug="test-slug",
            stage="BUILD",
            session_id="sess-1",
            project_key="test",
        )
        assert result == []


class TestCallHaikuExtraction:
    """_call_haiku_extraction() behavior."""

    @patch("utils.api_keys.get_anthropic_api_key")
    def test_returns_empty_on_no_api_key(self, mock_key):
        """Should return empty list when no API key is available."""
        from agent.finding_extraction import _call_haiku_extraction

        mock_key.return_value = None
        result = _call_haiku_extraction("output", "slug", "BUILD", "test")
        assert result == []

    @patch("utils.api_keys.get_anthropic_api_key")
    @patch("anthropic.Anthropic")
    def test_parses_json_response(self, mock_anthropic_cls, mock_key):
        """Should parse JSON array from Haiku response."""
        from agent.finding_extraction import _call_haiku_extraction

        mock_key.return_value = "test-key"
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        findings_json = json.dumps(
            [{"category": "pattern_found", "content": "Test finding", "importance": 5.0}]
        )
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=findings_json)]
        mock_client.messages.create.return_value = mock_response

        result = _call_haiku_extraction("output", "slug", "BUILD", "test")
        assert len(result) == 1
        assert result[0]["category"] == "pattern_found"

    @patch("utils.api_keys.get_anthropic_api_key")
    @patch("anthropic.Anthropic")
    def test_handles_malformed_json(self, mock_anthropic_cls, mock_key):
        """Should return empty list on malformed JSON response."""
        from agent.finding_extraction import _call_haiku_extraction

        mock_key.return_value = "test-key"
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="not valid json")]
        mock_client.messages.create.return_value = mock_response

        result = _call_haiku_extraction("output", "slug", "BUILD", "test")
        assert result == []


class TestDeduplicateAndSave:
    """_deduplicate_and_save() deduplication behavior."""

    @patch("agent.finding_extraction._find_duplicate")
    @patch("models.finding.Finding.safe_save")
    def test_saves_new_finding_when_no_duplicate(self, mock_save, mock_find_dup):
        """Should save when no duplicate exists."""
        from agent.finding_extraction import _deduplicate_and_save

        mock_find_dup.return_value = None
        mock_saved = MagicMock()
        mock_saved.finding_id = "new-f1"
        mock_save.return_value = mock_saved

        result = _deduplicate_and_save(
            finding_data={
                "category": "pattern_found",
                "content": "A substantial finding about authentication patterns",
                "file_paths": "auth/main.py",
                "importance": 5.0,
            },
            slug="test-slug",
            stage="BUILD",
            session_id="sess-1",
            project_key="test",
        )
        assert result is not None
        assert result["finding_id"] == "new-f1"

    @patch("agent.finding_extraction._find_duplicate")
    def test_reinforces_duplicate_instead_of_saving(self, mock_find_dup):
        """Should reinforce existing finding when duplicate found."""
        from agent.finding_extraction import _deduplicate_and_save

        mock_existing = MagicMock()
        mock_existing.confidence = MagicMock()
        mock_find_dup.return_value = mock_existing

        result = _deduplicate_and_save(
            finding_data={
                "category": "pattern_found",
                "content": "A substantial finding about authentication patterns",
                "importance": 5.0,
            },
            slug="test-slug",
            stage="BUILD",
            session_id="sess-1",
            project_key="test",
        )
        assert result is None
        mock_existing.confidence.update.assert_called_once_with(positive=True)
        mock_existing.confirm_access.assert_called_once()

    def test_rejects_short_content(self):
        """Should reject findings with content shorter than 10 chars."""
        from agent.finding_extraction import _deduplicate_and_save

        result = _deduplicate_and_save(
            finding_data={"content": "short", "importance": 5.0},
            slug="test-slug",
            stage="BUILD",
            session_id="sess-1",
            project_key="test",
        )
        assert result is None

    def test_clamps_importance(self):
        """Should clamp importance to 0.5-10.0 range."""
        from agent.finding_extraction import _deduplicate_and_save

        with (
            patch("agent.finding_extraction._find_duplicate", return_value=None),
            patch("models.finding.Finding.safe_save") as mock_save,
        ):
            mock_saved = MagicMock()
            mock_saved.finding_id = "f1"
            mock_save.return_value = mock_saved

            result = _deduplicate_and_save(
                finding_data={
                    "content": "A finding with extreme importance value",
                    "importance": 99.0,
                },
                slug="test-slug",
                stage="BUILD",
                session_id="sess-1",
                project_key="test",
            )
            # Importance should be clamped to 10.0
            assert result is not None
            call_kwargs = mock_save.call_args[1]
            assert call_kwargs["importance"] == 10.0


class TestFindDuplicate:
    """_find_duplicate() dedup detection."""

    @patch("models.finding.Finding.query_by_slug")
    @patch("models.finding.Finding._meta")
    def test_returns_none_when_bloom_says_no(self, mock_meta, mock_query):
        """Should return None when bloom filter says not present."""
        from agent.finding_extraction import _find_duplicate

        mock_bloom = MagicMock()
        mock_bloom.might_exist.return_value = False
        mock_meta.fields = {"bloom": mock_bloom}

        result = _find_duplicate("test-slug", "some content here")
        assert result is None
        # Should not even query
        mock_query.assert_not_called()

    @patch("models.finding.Finding.query_by_slug")
    @patch("models.finding.Finding._meta")
    def test_finds_exact_duplicate(self, mock_meta, mock_query):
        """Should find exact content match."""
        from agent.finding_extraction import _find_duplicate

        mock_bloom = MagicMock()
        mock_bloom.might_exist.return_value = True
        mock_meta.fields = {"bloom": mock_bloom}

        mock_finding = MagicMock()
        mock_finding.content = "auth module uses JWT with RS256"
        mock_query.return_value = [mock_finding]

        result = _find_duplicate("test-slug", "auth module uses JWT with RS256")
        assert result is mock_finding
