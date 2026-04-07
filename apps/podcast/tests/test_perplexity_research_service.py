"""Tests for Perplexity research error surfacing in services/research.py.

Tests cover:
- 401 response writes [FAILED: Perplexity API 401 - <type>] to artifact
- 429 response writes [FAILED: Perplexity API 429 - <type>] to artifact
- 200 response with empty content writes [FAILED: Perplexity API returned empty content]
- Missing API key writes [SKIPPED: PERPLEXITY_API_KEY not configured] (unchanged)
- Raw error stored in artifact.metadata["error"]

Also covers _handle_error_response() directly:
- Returns dict with _error_status, _error_message, _error_body on non-200
- Falls back gracefully when response.json() raises (malformed body)
"""

from unittest.mock import MagicMock, patch

import pytest

from apps.podcast.models import Episode, EpisodeArtifact, Podcast

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def podcast():
    return Podcast.objects.create(
        title="Perplexity Error Test Podcast",
        slug="test-podcast-perplexity-error",
        description="Test podcast for Perplexity error surfacing",
        author_name="Test Author",
        author_email="test@example.com",
    )


@pytest.fixture
def episode(podcast):
    ep = Episode.objects.create(
        podcast=podcast,
        title="Perplexity Error Test Episode",
        slug="perplexity-error-test-episode",
        description="Episode for testing Perplexity research error handling",
    )
    EpisodeArtifact.objects.create(
        episode=ep,
        title="p1-brief",
        content="Brief content for testing.",
        description="Episode brief.",
        workflow_context="Setup",
    )
    return ep


# ---------------------------------------------------------------------------
# _handle_error_response() — unit tests
# ---------------------------------------------------------------------------


class TestHandleErrorResponse:
    """Unit tests for perplexity_deep_research._handle_error_response()."""

    def _make_response(self, status_code, json_body=None, text_body=""):
        mock = MagicMock()
        mock.status_code = status_code
        if json_body is not None:
            mock.json.return_value = json_body
        else:
            mock.json.side_effect = Exception("not json")
        mock.text = text_body
        return mock

    def test_returns_dict_on_401(self):
        """Returns dict with _error_status=401 and extracted error type."""
        from apps.podcast.tools.perplexity_deep_research import _handle_error_response

        response = self._make_response(
            401,
            json_body={"error": {"type": "insufficient_quota", "message": "No quota"}},
        )
        result = _handle_error_response(response)

        assert isinstance(result, dict)
        assert result["_error_status"] == 401
        assert result["_error_message"] == "insufficient_quota"
        assert "_error_body" in result

    def test_returns_dict_on_429(self):
        """Returns dict with _error_status=429 and extracted error type."""
        from apps.podcast.tools.perplexity_deep_research import _handle_error_response

        response = self._make_response(
            429,
            json_body={
                "error": {"type": "rate_limit_exceeded", "message": "Too many requests"}
            },
        )
        result = _handle_error_response(response)

        assert result["_error_status"] == 429
        assert result["_error_message"] == "rate_limit_exceeded"

    def test_returns_dict_on_500(self):
        """Returns dict with _error_status=500."""
        from apps.podcast.tools.perplexity_deep_research import _handle_error_response

        response = self._make_response(
            500,
            json_body={"detail": "Internal server error"},
        )
        result = _handle_error_response(response)

        assert result["_error_status"] == 500
        assert result["_error_message"] == "Internal server error"

    def test_fallback_when_json_raises(self):
        """Returns dict even when response.json() raises (malformed body)."""
        from apps.podcast.tools.perplexity_deep_research import _handle_error_response

        response = self._make_response(401, json_body=None, text_body="not json body")
        result = _handle_error_response(response)

        assert isinstance(result, dict)
        assert result["_error_status"] == 401
        # Falls back to status code string when body is not parseable JSON
        assert result["_error_message"] == "401"

    def test_fallback_to_status_code_when_no_known_key(self):
        """Falls back to str(status_code) when error body has no known key."""
        from apps.podcast.tools.perplexity_deep_research import _handle_error_response

        response = self._make_response(403, json_body={"unexpected_key": "value"})
        result = _handle_error_response(response)

        assert result["_error_status"] == 403
        assert result["_error_message"] == "403"


# ---------------------------------------------------------------------------
# Service layer — run_perplexity_research() in research.py
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPerplexityResearchService:
    """Integration tests for services/research.py::run_perplexity_research."""

    def test_401_writes_failed_artifact(self, episode):
        """401 error data from tool produces [FAILED: Perplexity API 401 - ...] artifact."""
        error_data = {
            "_error_status": 401,
            "_error_message": "insufficient_quota",
            "_error_body": {"error": {"type": "insufficient_quota"}},
        }
        with (
            patch.dict("os.environ", {"PERPLEXITY_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.perplexity_deep_research.run_perplexity_research",
                return_value=(None, error_data),
            ),
        ):
            from apps.podcast.services.research import run_perplexity_research

            artifact = run_perplexity_research(episode.id, "test prompt")

        assert artifact.title == "p2-perplexity"
        assert artifact.content == "[FAILED: Perplexity API 401 - insufficient_quota]"
        assert "error" in artifact.metadata
        assert artifact.metadata.get("skipped") is not True

    def test_429_writes_failed_artifact(self, episode):
        """429 error data from tool produces [FAILED: Perplexity API 429 - ...] artifact."""
        error_data = {
            "_error_status": 429,
            "_error_message": "rate_limit_exceeded",
            "_error_body": {"error": {"type": "rate_limit_exceeded"}},
        }
        with (
            patch.dict("os.environ", {"PERPLEXITY_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.perplexity_deep_research.run_perplexity_research",
                return_value=(None, error_data),
            ),
        ):
            from apps.podcast.services.research import run_perplexity_research

            artifact = run_perplexity_research(episode.id, "test prompt")

        assert artifact.title == "p2-perplexity"
        assert artifact.content == "[FAILED: Perplexity API 429 - rate_limit_exceeded]"
        assert "error" in artifact.metadata

    def test_empty_200_writes_failed_empty_content_artifact(self, episode):
        """Empty 200 response (no _error_status) writes [FAILED: ...empty content] artifact."""
        with (
            patch.dict("os.environ", {"PERPLEXITY_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.perplexity_deep_research.run_perplexity_research",
                return_value=(None, {}),
            ),
        ):
            from apps.podcast.services.research import run_perplexity_research

            artifact = run_perplexity_research(episode.id, "test prompt")

        assert artifact.title == "p2-perplexity"
        assert artifact.content == "[FAILED: Perplexity API returned empty content]"
        assert "error" in artifact.metadata

    def test_missing_api_key_writes_skipped_artifact(self, episode):
        """Missing PERPLEXITY_API_KEY still writes [SKIPPED: PERPLEXITY_API_KEY not configured]."""
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("PERPLEXITY_API_KEY", None)

            from apps.podcast.services.research import run_perplexity_research

            artifact = run_perplexity_research(episode.id, "test prompt")

        assert artifact.title == "p2-perplexity"
        assert artifact.content == "[SKIPPED: PERPLEXITY_API_KEY not configured]"

    def test_success_creates_normal_artifact(self, episode):
        """Successful response creates a normal artifact with research content."""
        with (
            patch.dict("os.environ", {"PERPLEXITY_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.perplexity_deep_research.run_perplexity_research",
                return_value=("Deep research content here.", {}),
            ),
        ):
            from apps.podcast.services.research import run_perplexity_research

            artifact = run_perplexity_research(episode.id, "test prompt")

        assert artifact.title == "p2-perplexity"
        assert artifact.content == "Deep research content here."
        assert not artifact.content.startswith("[FAILED:")
        assert not artifact.content.startswith("[SKIPPED:")
