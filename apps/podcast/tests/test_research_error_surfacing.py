"""Tests for normalized error surfacing across all research tools.

Verifies that each research service function writes the correct artifact
prefix ([FAILED: ...] vs [SKIPPED: ...]) for each error condition.

Reference pattern (established in PR #228 for Perplexity):
- Missing API key  → [SKIPPED: <ENV_VAR> not configured]
- API error        → [FAILED: <ToolName> API {status} - {reason}]
- Empty content    → [FAILED: <ToolName> returned empty content]
- Exception        → [FAILED: <ToolName> {ExcType} - {message}]
"""

from unittest.mock import patch

import pytest

from apps.podcast.models import Episode, EpisodeArtifact, Podcast

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def podcast():
    return Podcast.objects.create(
        title="Error Surfacing Test Podcast",
        slug="error-surfacing-test",
        description="Test podcast",
        author_name="Test Author",
        author_email="test@example.com",
    )


@pytest.fixture
def episode(podcast):
    ep = Episode.objects.create(
        podcast=podcast,
        title="Error Surfacing Test Episode",
        slug="error-surfacing-test-episode",
        description="Episode for testing error surfacing.",
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
# Grok
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGrokErrorSurfacing:
    """Service layer error surfacing for run_grok_research."""

    def test_missing_api_key_writes_skipped_artifact(self, episode):
        """Missing GROK_API_KEY → [SKIPPED: GROK_API_KEY not configured]."""
        with patch.dict("os.environ", {"GROK_API_KEY": ""}, clear=False):
            from apps.podcast.services.research import run_grok_research

            artifact = run_grok_research(episode.id, "test prompt")

        assert artifact.title == "p2-grok"
        assert artifact.content == "[SKIPPED: GROK_API_KEY not configured]"
        assert artifact.metadata["skipped"] is True

    def test_api_401_writes_failed_artifact(self, episode):
        """Grok API 401 → [FAILED: Grok API 401 - ...]."""
        with (
            patch.dict("os.environ", {"GROK_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.grok_deep_research.run_grok_research",
                return_value=(
                    None,
                    {
                        "_error_status": 401,
                        "_error_message": "Invalid API key",
                        "_error_body": {"error": "Unauthorized"},
                    },
                ),
            ),
        ):
            from apps.podcast.services.research import run_grok_research

            artifact = run_grok_research(episode.id, "test prompt")

        assert artifact.title == "p2-grok"
        assert artifact.content.startswith("[FAILED: Grok API 401 -")
        assert "error" in artifact.metadata

    def test_api_429_writes_failed_artifact(self, episode):
        """Grok API 429 → [FAILED: Grok API 429 - ...]."""
        with (
            patch.dict("os.environ", {"GROK_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.grok_deep_research.run_grok_research",
                return_value=(
                    None,
                    {
                        "_error_status": 429,
                        "_error_message": "Rate limit exceeded",
                        "_error_body": {"error": "Too many requests"},
                    },
                ),
            ),
        ):
            from apps.podcast.services.research import run_grok_research

            artifact = run_grok_research(episode.id, "test prompt")

        assert artifact.title == "p2-grok"
        assert artifact.content.startswith("[FAILED: Grok API 429 -")

    def test_empty_content_writes_failed_artifact(self, episode):
        """Grok returns (None, {}) → [FAILED: Grok API returned empty content]."""
        with (
            patch.dict("os.environ", {"GROK_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.grok_deep_research.run_grok_research",
                return_value=(None, {}),
            ),
        ):
            from apps.podcast.services.research import run_grok_research

            artifact = run_grok_research(episode.id, "test prompt")

        assert artifact.title == "p2-grok"
        assert artifact.content == "[FAILED: Grok API returned empty content]"

    def test_success_writes_normal_artifact(self, episode):
        """Successful Grok response → content saved as p2-grok."""
        with (
            patch.dict("os.environ", {"GROK_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.grok_deep_research.run_grok_research",
                return_value=("Research content from Grok.", {"model": "grok-3"}),
            ),
        ):
            from apps.podcast.services.research import run_grok_research

            artifact = run_grok_research(episode.id, "test prompt")

        assert artifact.title == "p2-grok"
        assert artifact.content == "Research content from Grok."
        assert not artifact.content.startswith("[FAILED:")
        assert not artifact.content.startswith("[SKIPPED:")


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGeminiErrorSurfacing:
    """Service layer error surfacing for run_gemini_research."""

    def test_missing_api_key_writes_skipped_artifact(self, episode):
        """Missing GEMINI_API_KEY → [SKIPPED: GEMINI_API_KEY not configured]."""
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}, clear=False):
            from apps.podcast.services.research import run_gemini_research

            artifact = run_gemini_research(episode.id, "test prompt")

        assert artifact.title == "p2-gemini"
        assert artifact.content == "[SKIPPED: GEMINI_API_KEY not configured]"
        assert artifact.metadata["skipped"] is True

    def test_quota_429_writes_failed_artifact(self, episode):
        """Gemini API 429 quota error → [FAILED: Gemini API 429 - quota_exceeded]."""
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

            artifact = run_gemini_research(episode.id, "test prompt")

        assert artifact.title == "p2-gemini"
        assert artifact.content.startswith("[FAILED: Gemini API 429 -")
        assert "quota_exceeded" in artifact.content
        assert "error" in artifact.metadata

    def test_api_500_writes_failed_artifact(self, episode):
        """Gemini API 500 → [FAILED: Gemini API 500 - ...]."""
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

            artifact = run_gemini_research(episode.id, "test prompt")

        assert artifact.title == "p2-gemini"
        assert artifact.content.startswith("[FAILED: Gemini API 500 -")

    def test_empty_content_writes_failed_artifact(self, episode):
        """Gemini returns (None, {}) → [FAILED: Gemini API returned empty content]."""
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.gemini_deep_research.run_gemini_research",
                return_value=(None, {}),
            ),
        ):
            from apps.podcast.services.research import run_gemini_research

            artifact = run_gemini_research(episode.id, "test prompt")

        assert artifact.title == "p2-gemini"
        assert artifact.content == "[FAILED: Gemini API returned empty content]"

    def test_success_writes_normal_artifact(self, episode):
        """Successful Gemini response → content saved as p2-gemini."""
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.gemini_deep_research.run_gemini_research",
                return_value=("Research content from Gemini.", {}),
            ),
        ):
            from apps.podcast.services.research import run_gemini_research

            artifact = run_gemini_research(episode.id, "test prompt")

        assert artifact.title == "p2-gemini"
        assert artifact.content == "Research content from Gemini."
        assert not artifact.content.startswith("[FAILED:")
        assert not artifact.content.startswith("[SKIPPED:")


# ---------------------------------------------------------------------------
# GPT-Researcher
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGptResearcherErrorSurfacing:
    """Service layer error surfacing for run_gpt_researcher."""

    def test_exception_writes_failed_artifact(self, episode):
        """GPT-Researcher exception → [FAILED: GPT-Researcher {ExcType} - {msg}]."""
        with patch(
            "apps.podcast.tools.gpt_researcher_run.run_research",
            return_value=(
                None,
                {
                    "_error_message": "Connection refused",
                    "_error_type": "ConnectionError",
                },
            ),
        ):
            from apps.podcast.services.research import run_gpt_researcher

            artifact = run_gpt_researcher(episode.id, "test prompt")

        assert artifact.title == "p2-chatgpt"
        assert artifact.content.startswith("[FAILED: GPT-Researcher ConnectionError -")
        assert "Connection refused" in artifact.content
        assert "error" in artifact.metadata

    def test_empty_content_writes_failed_artifact(self, episode):
        """GPT-Researcher returns (None, {}) → [FAILED: GPT-Researcher returned empty content]."""
        with patch(
            "apps.podcast.tools.gpt_researcher_run.run_research",
            return_value=(None, {}),
        ):
            from apps.podcast.services.research import run_gpt_researcher

            artifact = run_gpt_researcher(episode.id, "test prompt")

        assert artifact.title == "p2-chatgpt"
        assert artifact.content == "[FAILED: GPT-Researcher returned empty content]"

    def test_success_writes_normal_artifact(self, episode):
        """Successful GPT-Researcher response → content saved as p2-chatgpt."""
        with patch(
            "apps.podcast.tools.gpt_researcher_run.run_research",
            return_value=("GPT-Researcher findings.", {}),
        ):
            from apps.podcast.services.research import run_gpt_researcher

            artifact = run_gpt_researcher(episode.id, "test prompt")

        assert artifact.title == "p2-chatgpt"
        assert artifact.content == "GPT-Researcher findings."
        assert not artifact.content.startswith("[FAILED:")


# ---------------------------------------------------------------------------
# Together
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTogetherErrorSurfacing:
    """Service layer error surfacing for run_together_research."""

    def test_missing_keys_writes_skipped_artifact(self, episode):
        """Missing Together API keys → [SKIPPED: Missing API keys - ...]."""
        with patch.dict(
            "os.environ",
            {
                "TAVILY_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "",
                "OPENROUTER_API_KEY": "",
            },
            clear=False,
        ):
            from apps.podcast.services.research import run_together_research

            artifact = run_together_research(episode.id, "test prompt")

        assert artifact.title == "p2-together"
        assert artifact.content.startswith("[SKIPPED: Missing API keys -")
        assert artifact.metadata["skipped"] is True

    def test_timeout_writes_failed_artifact(self, episode):
        """Together TimeoutError → [FAILED: Together TIMEOUT - ...]."""
        with (
            patch.dict(
                "os.environ",
                {"TAVILY_API_KEY": "fake", "ANTHROPIC_API_KEY": "fake"},
            ),
            patch(
                "apps.podcast.tools.together_deep_research.run_together_research",
                return_value=(
                    None,
                    {
                        "error": "Timed out after 900s",
                        "_error_status": "TIMEOUT",
                        "_error_message": "timed out after 900s",
                        "elapsed_seconds": 900,
                    },
                ),
            ),
        ):
            from apps.podcast.services.research import run_together_research

            artifact = run_together_research(episode.id, "test prompt")

        assert artifact.title == "p2-together"
        assert artifact.content.startswith("[FAILED: Together TIMEOUT -")
        assert "900s" in artifact.content

    def test_exception_writes_failed_artifact(self, episode):
        """Together generic exception → [FAILED: Together {ExcType} - ...]."""
        with (
            patch.dict(
                "os.environ",
                {"TAVILY_API_KEY": "fake", "ANTHROPIC_API_KEY": "fake"},
            ),
            patch(
                "apps.podcast.tools.together_deep_research.run_together_research",
                return_value=(
                    None,
                    {
                        "error": "Network error",
                        "_error_status": "ConnectionError",
                        "_error_message": "Network error",
                        "elapsed_seconds": 5,
                    },
                ),
            ),
        ):
            from apps.podcast.services.research import run_together_research

            artifact = run_together_research(episode.id, "test prompt")

        assert artifact.title == "p2-together"
        assert artifact.content.startswith("[FAILED: Together ConnectionError -")

    def test_empty_content_writes_failed_artifact(self, episode):
        """Together returns (None, {}) → [FAILED: Together returned empty content]."""
        with (
            patch.dict(
                "os.environ",
                {"TAVILY_API_KEY": "fake", "ANTHROPIC_API_KEY": "fake"},
            ),
            patch(
                "apps.podcast.tools.together_deep_research.run_together_research",
                return_value=(None, {}),
            ),
        ):
            from apps.podcast.services.research import run_together_research

            artifact = run_together_research(episode.id, "test prompt")

        assert artifact.title == "p2-together"
        assert artifact.content == "[FAILED: Together returned empty content]"

    def test_success_writes_normal_artifact(self, episode):
        """Successful Together response → content saved as p2-together."""
        with (
            patch.dict(
                "os.environ",
                {"TAVILY_API_KEY": "fake", "ANTHROPIC_API_KEY": "fake"},
            ),
            patch(
                "apps.podcast.tools.together_deep_research.run_together_research",
                return_value=(
                    "Together research findings.",
                    {"tool": "open-deep-research"},
                ),
            ),
        ):
            from apps.podcast.services.research import run_together_research

            artifact = run_together_research(episode.id, "test prompt")

        assert artifact.title == "p2-together"
        assert artifact.content == "Together research findings."
        assert not artifact.content.startswith("[FAILED:")


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestClaudeErrorSurfacing:
    """Service layer error surfacing for run_claude_research."""

    def test_runtime_error_writes_failed_artifact(self, episode):
        """Claude RuntimeError → [FAILED: Claude RuntimeError - {message}]."""
        with patch(
            "apps.podcast.services.claude_deep_research.deep_research",
            side_effect=RuntimeError("All subagents failed"),
        ):
            from apps.podcast.services.research import run_claude_research

            artifact = run_claude_research(episode.id, "test prompt")

        assert artifact.title == "p2-claude"
        assert artifact.content.startswith("[FAILED: Claude RuntimeError -")
        assert "All subagents failed" in artifact.content
        assert artifact.metadata["error_type"] == "RuntimeError"

    def test_generic_exception_writes_failed_artifact(self, episode):
        """Any Claude exception → [FAILED: Claude {ExcType} - {message}]."""
        with patch(
            "apps.podcast.services.claude_deep_research.deep_research",
            side_effect=ValueError("Invalid research command"),
        ):
            from apps.podcast.services.research import run_claude_research

            artifact = run_claude_research(episode.id, "test prompt")

        assert artifact.title == "p2-claude"
        assert artifact.content.startswith("[FAILED: Claude ValueError -")
        assert "Invalid research command" in artifact.content

    def test_none_report_writes_skipped_artifact(self, episode):
        """Claude returns None report → [SKIPPED: Claude research returned no report]."""
        mock_report = None
        with patch(
            "apps.podcast.services.claude_deep_research.deep_research",
            return_value=mock_report,
        ):
            from apps.podcast.services.research import run_claude_research

            artifact = run_claude_research(episode.id, "test prompt")

        assert artifact.title == "p2-claude"
        assert artifact.content == "[SKIPPED: Claude research returned no report]"
        assert artifact.metadata["skipped"] is True

    def test_success_writes_normal_artifact(self, episode):
        """Successful Claude response → content saved as p2-claude."""
        from unittest.mock import MagicMock

        mock_report = MagicMock()
        mock_report.content = "Claude research findings."
        mock_report.key_findings = ["Finding 1"]
        mock_report.gaps_remaining = []
        mock_report.confidence_assessment = "High confidence."
        mock_report.sources_cited = ["source1"]

        with patch(
            "apps.podcast.services.claude_deep_research.deep_research",
            return_value=mock_report,
        ):
            from apps.podcast.services.research import run_claude_research

            artifact = run_claude_research(episode.id, "test prompt")

        assert artifact.title == "p2-claude"
        assert "Claude research findings." in artifact.content
        assert not artifact.content.startswith("[FAILED:")
        assert not artifact.content.startswith("[SKIPPED:")
