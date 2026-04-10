"""Tests for Gemini Deep Research error detection and handling.

Tests cover:
- submit_research() returns (None, error_dict) on HTTP 429 (quota exceeded)
- submit_research() returns (None, error_dict) on other HTTP errors (e.g., 500)
- submit_research() returns (response_json, None) on success (200)
- Service layer creates FAILED artifact with _error_status on quota/API errors
- Service layer creates FAILED artifact with empty content when no content returned
- Service layer creates SKIPPED artifact when GEMINI_API_KEY is missing
"""

from unittest.mock import MagicMock, patch

import pytest

from apps.podcast.models import Episode, EpisodeArtifact, Podcast

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def podcast():
    """Create a test podcast."""
    return Podcast.objects.create(
        title="Test Podcast",
        slug="test-podcast-gemini",
        description="Test podcast for Gemini research",
        author_name="Test Author",
        author_email="test@example.com",
    )


@pytest.fixture
def episode(podcast):
    """Create a test episode with a brief artifact."""
    ep = Episode.objects.create(
        podcast=podcast,
        title="Gemini Test Episode",
        slug="gemini-test-episode",
        description="Episode for testing Gemini research error handling",
    )
    # Create a p1-brief artifact so _get_episode_context has something to find
    EpisodeArtifact.objects.create(
        episode=ep,
        title="p1-brief",
        content="Brief content for testing.",
        description="Episode brief.",
        workflow_context="Setup",
    )
    return ep


# ---------------------------------------------------------------------------
# submit_research() — tool-level unit tests
# ---------------------------------------------------------------------------


class TestSubmitResearch:
    """Unit tests for gemini_deep_research.submit_research()."""

    def test_returns_error_dict_on_429(self):
        """submit_research returns (None, error_dict) on HTTP 429."""
        from apps.podcast.tools.gemini_deep_research import submit_research

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.json.return_value = {
            "error": {
                "message": "You do not have enough quota to make this request.",
                "details": [
                    {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}
                ],
            }
        }

        with patch("requests.post", return_value=mock_response):
            result, error = submit_research("fake-key", "test prompt")

        assert result is None
        assert error["_error_status"] == 429
        assert "_error_message" in error
        assert "_error_body" in error

    def test_returns_error_dict_on_500(self):
        """submit_research returns (None, error_dict) on HTTP 500."""
        from apps.podcast.tools.gemini_deep_research import submit_research

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {
            "error": {"message": "Internal Server Error"}
        }

        with patch("requests.post", return_value=mock_response):
            result, error = submit_research("fake-key", "test prompt")

        assert result is None
        assert error["_error_status"] == 500
        assert "_error_message" in error

    def test_returns_error_dict_on_403(self):
        """submit_research returns (None, error_dict) on HTTP 403."""
        from apps.podcast.tools.gemini_deep_research import submit_research

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.json.return_value = {"error": {"message": "Forbidden"}}

        with patch("requests.post", return_value=mock_response):
            result, error = submit_research("fake-key", "test prompt")

        assert result is None
        assert error["_error_status"] == 403

    def test_returns_json_on_200(self):
        """submit_research returns (response_json, None) on HTTP 200."""
        from apps.podcast.tools.gemini_deep_research import submit_research

        expected = {"id": "interaction-123", "status": "in_progress"}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = expected

        with patch("requests.post", return_value=mock_response):
            result, error = submit_research("fake-key", "test prompt")

        assert result == expected
        assert error is None


# ---------------------------------------------------------------------------
# Service layer — run_gemini_research()
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGeminiResearchService:
    """Integration tests for services/research.py::run_gemini_research."""

    def test_quota_429_creates_failed_artifact(self, episode):
        """Service creates FAILED artifact with API status on 429 quota error."""
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.gemini_deep_research.run_gemini_research",
                return_value=(
                    None,
                    {
                        "_error_status": 429,
                        "_error_message": "quota_exceeded",
                        "_error_body": {"error": {"message": "quota exceeded"}},
                    },
                ),
            ),
        ):
            from apps.podcast.services.research import run_gemini_research

            artifact = run_gemini_research(episode.id, "test research prompt")

        assert artifact.title == "p2-gemini"
        assert artifact.content.startswith("[FAILED: Gemini API 429 -")
        assert "error" in artifact.metadata

    def test_api_500_creates_failed_artifact(self, episode):
        """Service creates FAILED artifact on 500 server error."""
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.gemini_deep_research.run_gemini_research",
                return_value=(
                    None,
                    {
                        "_error_status": 500,
                        "_error_message": "Internal Server Error",
                        "_error_body": {"error": {"message": "server error"}},
                    },
                ),
            ),
        ):
            from apps.podcast.services.research import run_gemini_research

            artifact = run_gemini_research(episode.id, "test research prompt")

        assert artifact.title == "p2-gemini"
        assert artifact.content.startswith("[FAILED: Gemini API 500 -")

    def test_empty_content_creates_failed_artifact(self, episode):
        """Service creates FAILED artifact when tool returns (None, {})."""
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.gemini_deep_research.run_gemini_research",
                return_value=(None, {}),
            ),
        ):
            from apps.podcast.services.research import run_gemini_research

            artifact = run_gemini_research(episode.id, "test research prompt")

        assert artifact.title == "p2-gemini"
        assert artifact.content == "[FAILED: Gemini API returned empty content]"

    def test_success_creates_normal_artifact(self, episode):
        """Service creates a normal artifact with research content on success."""
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.gemini_deep_research.run_gemini_research",
                return_value=("Research results about quantum computing.", {}),
            ),
        ):
            from apps.podcast.services.research import run_gemini_research

            artifact = run_gemini_research(episode.id, "test research prompt")

        assert artifact.title == "p2-gemini"
        assert artifact.content == "Research results about quantum computing."
        assert not artifact.content.startswith("[FAILED:")
        assert not artifact.content.startswith("[SKIPPED:")

    def test_missing_api_key_creates_skip_artifact(self, episode):
        """Service creates SKIPPED artifact when GEMINI_API_KEY is missing."""
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}, clear=False):
            from apps.podcast.services.research import run_gemini_research

            artifact = run_gemini_research(episode.id, "test research prompt")

        assert artifact.title == "p2-gemini"
        assert artifact.content == "[SKIPPED: GEMINI_API_KEY not configured]"
        assert artifact.metadata["skipped"] is True
