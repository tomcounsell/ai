"""Tests for the write_synthesis PydanticAI tool."""

import logging
from unittest.mock import MagicMock, patch

from apps.podcast.services.write_synthesis import (
    ReportSection,
    SynthesisReport,
    write_synthesis,
)


class TestWriteSynthesis:
    """Tests for write_synthesis service function."""

    def _make_mock_result(self, report: SynthesisReport) -> MagicMock:
        """Build a mock AgentRunResult with the given report."""
        mock_usage = MagicMock()
        mock_usage.input_tokens = 5000
        mock_usage.output_tokens = 3000

        mock_result = MagicMock()
        mock_result.output = report
        mock_result.usage.return_value = mock_usage
        return mock_result

    def _make_sample_report(self) -> SynthesisReport:
        return SynthesisReport(
            title="The Science of Sleep: What Research Really Shows",
            sections=[
                ReportSection(
                    heading="The Sleep-Memory Connection",
                    content=(
                        "According to a 2024 meta-analysis of 47 trials "
                        "(Smith et al.), sleep consolidation is critical."
                    ),
                    listener_implications=(
                        "If you want to retain new information, prioritize "
                        "7-9 hours of sleep within 24 hours of learning."
                    ),
                    stories_used=["The Stanford sleep lab story"],
                ),
                ReportSection(
                    heading="Practical Sleep Protocols",
                    content=(
                        "Research from the National Sleep Foundation shows "
                        "that consistent bedtimes improve sleep quality."
                    ),
                    listener_implications=(
                        "Set a consistent bedtime within a 30-minute window "
                        "every night, including weekends."
                    ),
                    stories_used=[],
                ),
            ],
            word_count=6500,
            core_takeaways=[
                "Sleep is the single most impactful health behavior.",
                "Consistency matters more than duration.",
            ],
            sources_cited=[
                "Smith et al. 2024",
                "National Sleep Foundation 2023",
            ],
        )

    def test_returns_synthesis_report(self):
        mock_report = self._make_sample_report()
        mock_result = self._make_mock_result(mock_report)

        with patch("apps.podcast.services.write_synthesis.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-opus-4-6"
            result = write_synthesis(
                "Master briefing text",
                {"perplexity": "Research from perplexity"},
                "Sleep Science Episode",
            )

        assert isinstance(result, SynthesisReport)
        assert len(result.sections) == 2
        assert result.sections[0].heading == "The Sleep-Memory Connection"
        assert result.word_count == 6500
        assert len(result.core_takeaways) == 2
        assert len(result.sources_cited) == 2

    def test_passes_correct_prompt(self):
        mock_result = self._make_mock_result(self._make_sample_report())

        with patch("apps.podcast.services.write_synthesis.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-opus-4-6"
            write_synthesis(
                "My briefing content",
                {"gemini": "Gemini research output"},
                "My Episode Title",
            )

            call_args = mock_agent.run_sync.call_args[0][0]
            assert "My Episode Title" in call_args
            assert "My briefing content" in call_args
            assert "MASTER BRIEFING" in call_args
            assert "ADDITIONAL RESEARCH CONTEXT" in call_args
            assert "gemini" in call_args
            assert "Gemini research output" in call_args

    def test_formats_multiple_sources(self):
        mock_result = self._make_mock_result(self._make_sample_report())

        with patch("apps.podcast.services.write_synthesis.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-opus-4-6"
            write_synthesis(
                "Briefing",
                {
                    "perplexity": "Perplexity output",
                    "gemini": "Gemini output",
                    "grok": "Grok output",
                },
                "Episode",
            )

            call_args = mock_agent.run_sync.call_args[0][0]
            assert "--- Source: perplexity ---" in call_args
            assert "--- Source: gemini ---" in call_args
            assert "--- Source: grok ---" in call_args
            assert "--- End Source ---" in call_args

    def test_logs_usage(self, caplog):
        mock_result = self._make_mock_result(self._make_sample_report())

        with patch("apps.podcast.services.write_synthesis.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-opus-4-6"

            with caplog.at_level(logging.INFO):
                write_synthesis("briefing", {"tool": "text"}, "Episode")

        assert "write_synthesis" in caplog.text
        assert "input_tokens=5000" in caplog.text
        assert "output_tokens=3000" in caplog.text

    def test_uses_opus_model(self):
        from apps.podcast.services.write_synthesis import agent as real_agent

        assert "opus" in str(real_agent.model).lower()

    def test_empty_report(self):
        empty_report = SynthesisReport(
            title="Empty Report",
            sections=[],
            word_count=0,
            core_takeaways=[],
            sources_cited=[],
        )
        mock_result = self._make_mock_result(empty_report)

        with patch("apps.podcast.services.write_synthesis.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-opus-4-6"
            result = write_synthesis("briefing", {}, "Episode")

        assert isinstance(result, SynthesisReport)
        assert len(result.sections) == 0
        assert result.word_count == 0
