"""Tests for Gemini Deep Research error detection and handling.

Tests cover:
- GeminiQuotaError raised on HTTP 429 in submit_research()
- submit_research() returns None on other HTTP errors (e.g., 500)
- submit_research() returns response JSON on success (200)
- Service layer creates skip artifact with reason "quota_exceeded" on GeminiQuotaError
- Service layer creates skip artifact with generic reason when tool returns None
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

    def test_raises_quota_error_on_429(self):
        """submit_research raises GeminiQuotaError on HTTP 429."""
        from apps.podcast.tools.gemini_deep_research import (
            GeminiQuotaError,
            submit_research,
        )

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

        with (
            patch("requests.post", return_value=mock_response),
            pytest.raises(GeminiQuotaError, match="quota"),
        ):
            submit_research("fake-key", "test prompt")

    def test_returns_none_on_500(self):
        """submit_research returns None on HTTP 500 (server error)."""
        from apps.podcast.tools.gemini_deep_research import submit_research

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {
            "error": {"message": "Internal Server Error"}
        }

        with patch("requests.post", return_value=mock_response):
            result = submit_research("fake-key", "test prompt")

        assert result is None

    def test_returns_none_on_403(self):
        """submit_research returns None on HTTP 403 (non-quota client error)."""
        from apps.podcast.tools.gemini_deep_research import submit_research

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.json.return_value = {"error": {"message": "Forbidden"}}

        with patch("requests.post", return_value=mock_response):
            result = submit_research("fake-key", "test prompt")

        assert result is None

    def test_returns_json_on_200(self):
        """submit_research returns response JSON on HTTP 200."""
        from apps.podcast.tools.gemini_deep_research import submit_research

        expected = {"id": "interaction-123", "status": "in_progress"}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = expected

        with patch("requests.post", return_value=mock_response):
            result = submit_research("fake-key", "test prompt")

        assert result == expected


# ---------------------------------------------------------------------------
# Service layer — run_gemini_research()
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGeminiResearchService:
    """Integration tests for services/research.py::run_gemini_research."""

    def test_quota_error_creates_skip_artifact(self, episode):
        """Service creates skip artifact with reason 'quota_exceeded' on GeminiQuotaError."""
        from apps.podcast.tools.gemini_deep_research import GeminiQuotaError

        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}),
            (
                patch(
                    "apps.podcast.services.research.run_gemini_research.__wrapped__",
                    side_effect=None,
                )
                if False
                else patch(
                    "apps.podcast.tools.gemini_deep_research.run_gemini_research",
                    side_effect=GeminiQuotaError("quota exceeded"),
                )
            ),
        ):
            from apps.podcast.services.research import run_gemini_research

            artifact = run_gemini_research(episode.id, "test research prompt")

        assert artifact.title == "p2-gemini"
        assert artifact.metadata["skipped"] is True
        assert artifact.metadata["reason"] == "quota_exceeded"
        assert "quota" in artifact.content.lower()
        assert "aistudio.google.com" in artifact.content

    def test_none_response_creates_generic_skip_artifact(self, episode):
        """Service creates skip artifact with reason 'api_error_or_empty' when tool returns None."""
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.gemini_deep_research.run_gemini_research",
                return_value=None,
            ),
        ):
            from apps.podcast.services.research import run_gemini_research

            artifact = run_gemini_research(episode.id, "test research prompt")

        assert artifact.title == "p2-gemini"
        assert artifact.metadata["skipped"] is True
        assert artifact.metadata["reason"] == "api_error_or_empty"

    def test_success_creates_normal_artifact(self, episode):
        """Service creates a normal artifact with research content on success."""
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.gemini_deep_research.run_gemini_research",
                return_value="Research results about quantum computing.",
            ),
        ):
            from apps.podcast.services.research import run_gemini_research

            artifact = run_gemini_research(episode.id, "test research prompt")

        assert artifact.title == "p2-gemini"
        assert artifact.content == "Research results about quantum computing."
        assert (
            "skipped" not in artifact.metadata
            or artifact.metadata.get("skipped") is not True
        )

    def test_missing_api_key_creates_skip_artifact(self, episode):
        """Service creates skip artifact when GEMINI_API_KEY is missing."""
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}, clear=False):
            from apps.podcast.services.research import run_gemini_research

            artifact = run_gemini_research(episode.id, "test research prompt")

        assert artifact.title == "p2-gemini"
        assert artifact.metadata["skipped"] is True
        assert "API key" in artifact.metadata["reason"]
