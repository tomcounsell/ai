"""Tests for bridge/formatting.py -- shared formatting utilities."""

from unittest.mock import MagicMock, patch

from bridge.formatting import linkify_references, linkify_references_from_session


class TestLinkifyReferences:
    """Test PR/Issue reference linkification."""

    def test_linkifies_pr_reference(self):
        """Should convert 'PR #42' to a markdown link."""
        mock_config = {"projects": {"ai": {"github": {"org": "tomcounsell", "repo": "ai"}}}}
        with patch("bridge.formatting.load_config", return_value=mock_config):
            result = linkify_references("See PR #42 for details", "ai")

        assert "[PR #42](https://github.com/tomcounsell/ai/pull/42)" in result

    def test_linkifies_issue_reference(self):
        """Should convert 'Issue #100' to a markdown link."""
        mock_config = {"projects": {"ai": {"github": {"org": "tomcounsell", "repo": "ai"}}}}
        with patch("bridge.formatting.load_config", return_value=mock_config):
            result = linkify_references("Fix for Issue #100", "ai")

        assert "[Issue #100](https://github.com/tomcounsell/ai/issues/100)" in result

    def test_no_double_linkify(self):
        """Should not re-link already-linked references."""
        already_linked = "[PR #42](https://github.com/tomcounsell/ai/pull/42)"
        mock_config = {"projects": {"ai": {"github": {"org": "tomcounsell", "repo": "ai"}}}}
        with patch("bridge.formatting.load_config", return_value=mock_config):
            result = linkify_references(already_linked, "ai")

        # Should not contain double brackets
        assert result.count("[PR #42]") == 1

    def test_returns_text_unchanged_without_project_key(self):
        """Should return text as-is when project_key is None."""
        result = linkify_references("See PR #42", None)
        assert result == "See PR #42"

    def test_returns_text_unchanged_without_github_config(self):
        """Should return text as-is when GitHub config missing."""
        mock_config = {"projects": {"ai": {}}}
        with patch("bridge.formatting.load_config", return_value=mock_config):
            result = linkify_references("See PR #42", "ai")

        assert result == "See PR #42"

    def test_handles_empty_text(self):
        """Should return empty text unchanged."""
        result = linkify_references("", "ai")
        assert result == ""

    def test_handles_none_text(self):
        """Should return None unchanged."""
        result = linkify_references(None, "ai")
        assert result is None


class TestLinkifyFromSession:
    """Test the session-based convenience wrapper."""

    def test_extracts_project_key_from_session(self):
        """Should extract project_key from session object."""
        mock_session = MagicMock()
        mock_session.project_key = "ai"

        with patch("bridge.formatting.linkify_references") as mock_linkify:
            mock_linkify.return_value = "linked text"
            result = linkify_references_from_session("some text", mock_session)

        mock_linkify.assert_called_once_with("some text", "ai")
        assert result == "linked text"

    def test_handles_none_session(self):
        """Should return text unchanged when session is None."""
        result = linkify_references_from_session("some text", None)
        assert result == "some text"

    def test_handles_session_without_project_key(self):
        """Should handle session objects without project_key attribute."""
        mock_session = MagicMock(spec=[])  # No attributes
        result = linkify_references_from_session("some text", mock_session)
        # Should call linkify_references with None project_key
        assert result == "some text"
