"""Tests for the discover_questions PydanticAI tool."""

import logging
from unittest.mock import MagicMock, patch

from apps.podcast.services.discover_questions import (
    QuestionDiscovery,
    Subtopic,
    ToolRecommendation,
    discover_questions,
)


class TestDiscoverQuestions:
    """Tests for discover_questions service function."""

    def _make_mock_result(self, discovery: QuestionDiscovery) -> MagicMock:
        """Build a mock AgentRunResult with the given discovery."""
        mock_usage = MagicMock()
        mock_usage.input_tokens = 1000
        mock_usage.output_tokens = 200

        mock_result = MagicMock()
        mock_result.output = discovery
        mock_result.usage.return_value = mock_usage
        return mock_result

    def _make_sample_discovery(self) -> QuestionDiscovery:
        return QuestionDiscovery(
            subtopics_found=[
                Subtopic(name="Circadian rhythms", coverage_depth="moderate"),
                Subtopic(name="Sleep stages", coverage_depth="extensive"),
            ],
            gaps_in_literature=["Long-term effects of polyphasic sleep"],
            recent_developments_needed=["2024 sleep study updates"],
            contradictions_to_resolve=["Optimal sleep duration varies by study"],
            industry_questions=["How do tech companies handle shift work?"],
            policy_questions=["Should schools start later?"],
            practitioner_questions=["Best practices for sleep clinics"],
            recommended_tools=[
                ToolRecommendation(
                    tool="gpt-researcher",
                    focus="Industry shift work practices",
                    priority="high",
                ),
            ],
        )

    def test_returns_question_discovery(self):
        mock_discovery = self._make_sample_discovery()
        mock_result = self._make_mock_result(mock_discovery)

        with patch("apps.podcast.services.discover_questions.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"
            result = discover_questions("Some research digest", "Sleep Science")

        assert isinstance(result, QuestionDiscovery)
        assert len(result.subtopics_found) == 2
        assert result.subtopics_found[0].name == "Circadian rhythms"
        assert result.subtopics_found[0].coverage_depth == "moderate"
        assert len(result.recommended_tools) == 1
        assert result.recommended_tools[0].tool == "gpt-researcher"

    def test_passes_correct_prompt(self):
        mock_result = self._make_mock_result(self._make_sample_discovery())

        with patch("apps.podcast.services.discover_questions.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"
            discover_questions("My research digest", "My Topic")

            call_args = mock_agent.run_sync.call_args[0][0]
            assert "My research digest" in call_args
            assert "My Topic" in call_args

    def test_logs_usage(self, caplog):
        mock_result = self._make_mock_result(self._make_sample_discovery())

        with patch("apps.podcast.services.discover_questions.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"

            with caplog.at_level(logging.INFO):
                discover_questions("research digest", "Topic")

        assert "discover_questions" in caplog.text
        assert "input_tokens=1000" in caplog.text
        assert "output_tokens=200" in caplog.text

    def test_empty_discovery(self):
        empty_discovery = QuestionDiscovery(
            subtopics_found=[],
            gaps_in_literature=[],
            recent_developments_needed=[],
            contradictions_to_resolve=[],
            industry_questions=[],
            policy_questions=[],
            practitioner_questions=[],
            recommended_tools=[],
        )
        mock_result = self._make_mock_result(empty_discovery)

        with patch("apps.podcast.services.discover_questions.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"
            result = discover_questions("Short digest", "Topic")

        assert isinstance(result, QuestionDiscovery)
        assert len(result.subtopics_found) == 0
        assert len(result.recommended_tools) == 0
