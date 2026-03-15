"""Tests for MiroFish swarm intelligence research integration.

Tests the tool wrapper (HTTP client), the research service function, and
the graceful-skip patterns when MiroFish is unavailable.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.podcast.models import Episode, EpisodeArtifact, Podcast


class TestMirofishToolWrapper:
    """Tests for the MiroFish HTTP client wrapper."""

    def test_get_api_url_default(self, monkeypatch):
        """Default API URL when env var is not set."""
        monkeypatch.delenv("MIROFISH_API_URL", raising=False)
        from apps.podcast.tools.mirofish_research import get_api_url

        assert get_api_url() == "http://localhost:5001"

    def test_get_api_url_from_env(self, monkeypatch):
        """API URL from environment variable."""
        monkeypatch.setenv("MIROFISH_API_URL", "http://mirofish.local:8080")
        from apps.podcast.tools.mirofish_research import get_api_url

        assert get_api_url() == "http://mirofish.local:8080"

    @patch("apps.podcast.tools.mirofish_research.httpx.get")
    def test_check_health_success(self, mock_get):
        """Health check returns True when service responds 200."""
        mock_get.return_value = MagicMock(status_code=200)
        from apps.podcast.tools.mirofish_research import check_health

        assert check_health("http://localhost:5001") is True

    @patch("apps.podcast.tools.mirofish_research.httpx.get")
    def test_check_health_failure(self, mock_get):
        """Health check returns False when service is down."""
        import httpx

        mock_get.side_effect = httpx.ConnectError("Connection refused")
        from apps.podcast.tools.mirofish_research import check_health

        assert check_health("http://localhost:5001") is False

    @patch("apps.podcast.tools.mirofish_research.httpx.post")
    def test_run_simulation_success(self, mock_post):
        """Successful simulation returns content and metadata."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "report": "Stakeholders reacted with mixed responses...",
            "predictions": ["Prediction 1", "Prediction 2"],
            "agents": [{"name": "Expert A"}, {"name": "Critic B"}],
            "confidence": 0.85,
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        from apps.podcast.tools.mirofish_research import run_mirofish_simulation

        content, metadata = run_mirofish_simulation(
            "Test prompt", api_url="http://localhost:5001"
        )

        assert content == "Stakeholders reacted with mixed responses..."
        assert metadata["predictions"] == ["Prediction 1", "Prediction 2"]
        assert metadata["agents"] == [{"name": "Expert A"}, {"name": "Critic B"}]
        assert metadata["confidence"] == 0.85

    @patch("apps.podcast.tools.mirofish_research.httpx.post")
    def test_run_simulation_connection_error(self, mock_post):
        """Connection error returns None content with error metadata."""
        import httpx

        mock_post.side_effect = httpx.ConnectError("Connection refused")

        from apps.podcast.tools.mirofish_research import run_mirofish_simulation

        content, metadata = run_mirofish_simulation(
            "Test prompt", api_url="http://localhost:5001"
        )

        assert content is None
        assert metadata["error"] == "connection_error"

    @patch("apps.podcast.tools.mirofish_research.httpx.post")
    def test_run_simulation_timeout(self, mock_post):
        """Timeout returns None content with timeout metadata."""
        import httpx

        mock_post.side_effect = httpx.TimeoutException("Timed out")

        from apps.podcast.tools.mirofish_research import run_mirofish_simulation

        content, metadata = run_mirofish_simulation(
            "Test prompt", api_url="http://localhost:5001"
        )

        assert content is None
        assert metadata["error"] == "timeout"

    @patch("apps.podcast.tools.mirofish_research.httpx.post")
    def test_run_simulation_malformed_json(self, mock_post):
        """Malformed JSON response returns None with parse error."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.text = "not-json-data"
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        from apps.podcast.tools.mirofish_research import run_mirofish_simulation

        content, metadata = run_mirofish_simulation(
            "Test prompt", api_url="http://localhost:5001"
        )

        assert content is None
        assert metadata["error"] == "parse_error"

    @patch("apps.podcast.tools.mirofish_research.httpx.post")
    def test_run_simulation_empty_report(self, mock_post):
        """Empty report field returns None content."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"report": None}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        from apps.podcast.tools.mirofish_research import run_mirofish_simulation

        content, metadata = run_mirofish_simulation(
            "Test prompt", api_url="http://localhost:5001"
        )

        assert content is None


class TestMirofishResearchService(TestCase):
    """Tests for run_mirofish_research service function."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast-mirofish",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            description="Test episode description",
        )

    @patch.dict("os.environ", {}, clear=False)
    def test_skip_when_api_url_not_configured(self):
        """Creates [SKIPPED] artifact when MIROFISH_API_URL is not set."""
        import os

        os.environ.pop("MIROFISH_API_URL", None)

        from apps.podcast.services.research import run_mirofish_research

        artifact = run_mirofish_research(self.episode.pk, "test prompt")

        assert artifact.title == "p2-mirofish"
        assert "[SKIPPED" in artifact.content
        assert artifact.metadata["skipped"] is True
        assert "API URL not configured" in artifact.metadata["reason"]

    @patch.dict("os.environ", {"MIROFISH_API_URL": "http://localhost:5001"})
    @patch("apps.podcast.tools.mirofish_research.check_health", return_value=False)
    def test_skip_when_service_unreachable(self, mock_health):
        """Creates [SKIPPED] artifact when MiroFish service is down."""
        from apps.podcast.services.research import run_mirofish_research

        artifact = run_mirofish_research(self.episode.pk, "test prompt")

        assert artifact.title == "p2-mirofish"
        assert "[SKIPPED" in artifact.content
        assert artifact.metadata["skipped"] is True
        assert "unreachable" in artifact.metadata["reason"].lower()

    @patch.dict("os.environ", {"MIROFISH_API_URL": "http://localhost:5001"})
    @patch("apps.podcast.tools.mirofish_research.check_health", return_value=True)
    @patch("apps.podcast.tools.mirofish_research.run_mirofish_simulation")
    def test_success_creates_artifact(self, mock_sim, mock_health):
        """Successful simulation creates p2-mirofish artifact with content."""
        mock_sim.return_value = (
            "Multi-agent simulation report...",
            {"agents": [{"name": "Expert"}], "confidence": 0.9},
        )

        from apps.podcast.services.research import run_mirofish_research

        artifact = run_mirofish_research(self.episode.pk, "test prompt")

        assert artifact.title == "p2-mirofish"
        assert artifact.content == "Multi-agent simulation report..."
        assert artifact.metadata["skipped"] is False
        assert artifact.metadata["confidence"] == 0.9
        assert artifact.workflow_context == "Research Gathering"

    @patch.dict("os.environ", {"MIROFISH_API_URL": "http://localhost:5001"})
    @patch("apps.podcast.tools.mirofish_research.check_health", return_value=True)
    @patch("apps.podcast.tools.mirofish_research.run_mirofish_simulation")
    def test_skip_when_empty_content(self, mock_sim, mock_health):
        """Creates [SKIPPED] artifact when simulation returns empty content."""
        mock_sim.return_value = (None, {"error": "empty response"})

        from apps.podcast.services.research import run_mirofish_research

        artifact = run_mirofish_research(self.episode.pk, "test prompt")

        assert artifact.title == "p2-mirofish"
        assert "[SKIPPED" in artifact.content
        assert artifact.metadata["skipped"] is True

    @patch.dict("os.environ", {"MIROFISH_API_URL": "http://localhost:5001"})
    @patch("apps.podcast.tools.mirofish_research.check_health", return_value=True)
    @patch(
        "apps.podcast.tools.mirofish_research.run_mirofish_simulation",
        side_effect=RuntimeError("Unexpected crash"),
    )
    def test_skip_on_exception(self, mock_sim, mock_health):
        """Creates [SKIPPED] artifact when simulation raises an exception."""
        from apps.podcast.services.research import run_mirofish_research

        artifact = run_mirofish_research(self.episode.pk, "test prompt")

        assert artifact.title == "p2-mirofish"
        assert "[SKIPPED" in artifact.content
        assert artifact.metadata["skipped"] is True
        assert "RuntimeError" in artifact.metadata["error_type"]

    @patch.dict("os.environ", {"MIROFISH_API_URL": "http://localhost:5001"})
    @patch("apps.podcast.tools.mirofish_research.check_health", return_value=True)
    @patch("apps.podcast.tools.mirofish_research.run_mirofish_simulation")
    def test_prompt_contains_simulation_directive(self, mock_sim, mock_health):
        """The prompt sent to MiroFish includes perspective simulation directives."""
        mock_sim.return_value = ("Report", {"source": "mirofish"})

        from apps.podcast.services.research import run_mirofish_research

        run_mirofish_research(self.episode.pk, "test prompt")

        # Check that the prompt passed to run_mirofish_simulation contains
        # the simulation directive
        call_args = mock_sim.call_args
        prompt_sent = call_args[1].get("prompt") or call_args[0][0]
        assert "SIMULATION DIRECTIVE" in prompt_sent
        assert "stakeholder" in prompt_sent.lower()

    @patch.dict("os.environ", {"MIROFISH_API_URL": "http://localhost:5001"})
    @patch("apps.podcast.tools.mirofish_research.check_health", return_value=True)
    @patch("apps.podcast.tools.mirofish_research.run_mirofish_simulation")
    def test_uses_episode_context(self, mock_sim, mock_health):
        """Prompt includes episode title and description context."""
        mock_sim.return_value = ("Report", {})

        # Create a question-discovery artifact for richer context
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="question-discovery",
            content="Rich context from question discovery",
        )

        from apps.podcast.services.research import run_mirofish_research

        run_mirofish_research(self.episode.pk, "test prompt")

        call_args = mock_sim.call_args
        prompt_sent = call_args[1].get("prompt") or call_args[0][0]
        assert "Test Episode" in prompt_sent
        assert "Rich context from question discovery" in prompt_sent
