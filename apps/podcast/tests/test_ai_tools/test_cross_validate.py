"""Tests for the cross_validate PydanticAI tool."""

import logging
from unittest.mock import MagicMock, patch

from apps.podcast.services.cross_validate import (
    Claim,
    Conflict,
    ConflictPosition,
    CoverageEntry,
    CrossValidation,
    SourceAssessment,
    cross_validate,
)


class TestCrossValidate:
    """Tests for cross_validate service function."""

    def _make_mock_result(self, validation: CrossValidation) -> MagicMock:
        """Build a mock AgentRunResult with the given validation."""
        mock_usage = MagicMock()
        mock_usage.input_tokens = 2000
        mock_usage.output_tokens = 800

        mock_result = MagicMock()
        mock_result.output = validation
        mock_result.usage.return_value = mock_usage
        return mock_result

    def _make_sample_validation(self) -> CrossValidation:
        """Build a sample CrossValidation for testing."""
        return CrossValidation(
            verified_claims=[
                Claim(
                    claim="Exercise improves mood",
                    sources=["perplexity", "gemini"],
                    confidence="medium",
                ),
            ],
            single_source_claims=[
                Claim(
                    claim="New study on dopamine",
                    sources=["chatgpt"],
                    confidence="low",
                ),
            ],
            conflicting_claims=[
                Conflict(
                    topic="Optimal duration",
                    positions=[
                        ConflictPosition(
                            source="perplexity",
                            position="30 minutes",
                        ),
                        ConflictPosition(
                            source="gemini",
                            position="45 minutes",
                        ),
                    ],
                    resolution_suggestion="Depends on intensity",
                ),
            ],
            source_quality=[
                SourceAssessment(
                    source="perplexity",
                    strengths=["Academic sources"],
                    weaknesses=["Limited recency"],
                    unique_contributions=["Meta-analysis data"],
                ),
            ],
            coverage_map=[
                CoverageEntry(
                    topic="Exercise benefits",
                    sources_covering=["perplexity", "gemini"],
                    depth="deep",
                ),
            ],
            summary="Two sources largely agree with minor conflicts.",
        )

    def test_returns_cross_validation(self):
        sample = self._make_sample_validation()
        mock_result = self._make_mock_result(sample)

        with patch("apps.podcast.services.cross_validate.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"
            result = cross_validate(
                {"perplexity": "Research A", "gemini": "Research B"},
                "Exercise and Mental Health",
            )

        assert isinstance(result, CrossValidation)
        assert len(result.verified_claims) == 1
        assert result.verified_claims[0].claim == "Exercise improves mood"
        assert len(result.conflicting_claims) == 1
        assert result.summary == "Two sources largely agree with minor conflicts."

    def test_all_source_texts_in_prompt(self):
        sample = self._make_sample_validation()
        mock_result = self._make_mock_result(sample)

        sources = {
            "perplexity": "Perplexity findings here",
            "chatgpt": "ChatGPT findings here",
            "gemini": "Gemini findings here",
        }

        with patch("apps.podcast.services.cross_validate.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"
            cross_validate(sources, "Test Topic")

            call_args = mock_agent.run_sync.call_args[0][0]
            assert "Test Topic" in call_args
            assert "Perplexity findings here" in call_args
            assert "ChatGPT findings here" in call_args
            assert "Gemini findings here" in call_args
            assert "--- Source: perplexity ---" in call_args
            assert "--- Source: chatgpt ---" in call_args
            assert "--- Source: gemini ---" in call_args

    def test_logs_usage(self, caplog):
        sample = self._make_sample_validation()
        mock_result = self._make_mock_result(sample)

        with patch("apps.podcast.services.cross_validate.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"

            with caplog.at_level(logging.INFO):
                cross_validate({"perplexity": "data"}, "Topic")

        assert "cross_validate" in caplog.text
        assert "input_tokens=2000" in caplog.text
        assert "output_tokens=800" in caplog.text

    def test_empty_validation(self):
        empty = CrossValidation(
            verified_claims=[],
            single_source_claims=[],
            conflicting_claims=[],
            source_quality=[],
            coverage_map=[],
            summary="No overlapping claims found.",
        )
        mock_result = self._make_mock_result(empty)

        with patch("apps.podcast.services.cross_validate.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"
            result = cross_validate({"perplexity": "minimal data"}, "Sparse Topic")

        assert isinstance(result, CrossValidation)
        assert len(result.verified_claims) == 0
        assert len(result.conflicting_claims) == 0
