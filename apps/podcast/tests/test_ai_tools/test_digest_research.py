"""Tests for the digest_research PydanticAI tool."""

import logging
from unittest.mock import MagicMock, patch

from apps.podcast.services.digest_research import (
    KeyFinding,
    ResearchDigest,
    Source,
    digest_research,
)


class TestDigestResearch:
    """Tests for digest_research service function."""

    def _make_mock_result(self, digest: ResearchDigest) -> MagicMock:
        """Build a mock AgentRunResult with the given digest."""
        mock_usage = MagicMock()
        mock_usage.input_tokens = 1000
        mock_usage.output_tokens = 200

        mock_result = MagicMock()
        mock_result.output = digest
        mock_result.usage.return_value = mock_usage
        return mock_result

    def _make_sample_digest(self) -> ResearchDigest:
        return ResearchDigest(
            table_of_contents=["Introduction", "Key Findings"],
            key_findings=[
                KeyFinding(
                    finding="Sleep improves memory consolidation",
                    confidence="high",
                    source="Walker et al. 2017",
                ),
            ],
            statistics=["8 hours is optimal for 95% of adults"],
            sources=[
                Source(
                    citation="Walker et al. 2017",
                    tier="tier1",
                    url="https://example.com",
                ),
            ],
            topics=["sleep", "memory", "consolidation"],
            questions_answered=["How does sleep affect memory?"],
            questions_unanswered=["What is the minimum effective sleep?"],
            contradictions=["Some studies suggest 6 hours is sufficient"],
        )

    def test_returns_research_digest(self):
        mock_digest = self._make_sample_digest()
        mock_result = self._make_mock_result(mock_digest)

        with patch("apps.podcast.services.digest_research.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"
            result = digest_research("Some research text", "Sleep Science")

        assert isinstance(result, ResearchDigest)
        assert len(result.key_findings) == 1
        assert result.key_findings[0].finding == "Sleep improves memory consolidation"
        assert result.key_findings[0].confidence == "high"
        assert len(result.sources) == 1
        assert result.sources[0].tier == "tier1"

    def test_passes_correct_prompt(self):
        mock_result = self._make_mock_result(self._make_sample_digest())

        with patch("apps.podcast.services.digest_research.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"
            digest_research("My research text", "My Topic")

            call_args = mock_agent.run_sync.call_args[0][0]
            assert "My research text" in call_args
            assert "My Topic" in call_args

    def test_passes_research_text_without_topic(self):
        mock_result = self._make_mock_result(self._make_sample_digest())

        with patch("apps.podcast.services.digest_research.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"
            digest_research("My research text")

            call_args = mock_agent.run_sync.call_args[0][0]
            assert "My research text" in call_args

    def test_logs_usage(self, caplog):
        mock_result = self._make_mock_result(self._make_sample_digest())

        with patch("apps.podcast.services.digest_research.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"

            with caplog.at_level(logging.INFO):
                digest_research("research text", "Topic")

        assert "digest_research" in caplog.text
        assert "input_tokens=1000" in caplog.text
        assert "output_tokens=200" in caplog.text

    def test_empty_digest(self):
        empty_digest = ResearchDigest(
            table_of_contents=[],
            key_findings=[],
            statistics=[],
            sources=[],
            topics=[],
            questions_answered=[],
            questions_unanswered=[],
            contradictions=[],
        )
        mock_result = self._make_mock_result(empty_digest)

        with patch("apps.podcast.services.digest_research.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"
            result = digest_research("Short text")

        assert isinstance(result, ResearchDigest)
        assert len(result.key_findings) == 0
