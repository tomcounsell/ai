"""Tests for the write_briefing PydanticAI tool."""

import logging
from unittest.mock import MagicMock, patch

from apps.podcast.services.write_briefing import (
    Counterpoint,
    DepthEntry,
    Evidence,
    MasterBriefing,
    PracticalStep,
    SourceInventory,
    Story,
    TopicFindings,
    write_briefing,
)


class TestWriteBriefing:
    """Tests for write_briefing service function."""

    def _make_mock_result(self, briefing: MasterBriefing) -> MagicMock:
        """Build a mock AgentRunResult with the given briefing."""
        mock_usage = MagicMock()
        mock_usage.input_tokens = 3000
        mock_usage.output_tokens = 1500

        mock_result = MagicMock()
        mock_result.output = briefing
        mock_result.usage.return_value = mock_usage
        return mock_result

    def _make_sample_briefing(self) -> MasterBriefing:
        """Build a sample MasterBriefing for testing."""
        return MasterBriefing(
            verified_findings=[
                TopicFindings(
                    topic="Sleep quality",
                    main_finding="Consistent sleep schedule improves outcomes",
                    evidence=[
                        Evidence(
                            finding="Regular bedtime linked to better health",
                            source="perplexity",
                            quality="meta-analysis",
                            sample_size="50000",
                        ),
                    ],
                    contradictions=["Napping benefit disputed"],
                    source_quality_notes=["Strong evidence base"],
                ),
            ],
            depth_distribution=[
                DepthEntry(
                    topic="Sleep quality",
                    depth_rating="deep",
                    recommendation="Sufficient for episode",
                ),
            ],
            practical_audit=[
                PracticalStep(
                    finding="Consistent sleep schedule",
                    implementation="Set fixed wake time 7 days/week",
                    parameters="Within 30-minute window, 7-9 hours total",
                ),
            ],
            story_bank=[
                Story(
                    title="The Shift Worker Study",
                    narrative="A hospital system restructured shifts...",
                    memorability="high",
                    emotional_resonance="Relatable to many workers",
                    integration_opportunity="Open segment 2",
                ),
            ],
            counterpoints=[
                Counterpoint(
                    topic="Napping",
                    position_a="Short naps improve alertness",
                    position_b="Naps disrupt nighttime sleep",
                    dialogue_opportunity="Host debate on nap strategies",
                ),
            ],
            research_gaps=["Long-term effects of polyphasic sleep"],
            source_inventory=SourceInventory(
                tier1=["Walker 2017 meta-analysis"],
                tier2=["Johnson 2022 RCT"],
                tier3=["Smith 2023 case report"],
            ),
            synthesis_notes="Focus on actionable sleep hygiene tips.",
        )

    def test_returns_master_briefing(self):
        sample = self._make_sample_briefing()
        mock_result = self._make_mock_result(sample)

        with patch("apps.podcast.services.write_briefing.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"
            result = write_briefing(
                cross_validation="Cross-validation JSON here",
                research_digests={"perplexity": "Digest A"},
                episode_title="Better Sleep Tonight",
            )

        assert isinstance(result, MasterBriefing)
        assert len(result.verified_findings) == 1
        assert result.verified_findings[0].topic == "Sleep quality"
        assert len(result.story_bank) == 1
        assert result.story_bank[0].memorability == "high"
        assert result.synthesis_notes == "Focus on actionable sleep hygiene tips."

    def test_cross_validation_and_digests_in_prompt(self):
        sample = self._make_sample_briefing()
        mock_result = self._make_mock_result(sample)

        digests = {
            "perplexity": "Perplexity digest content",
            "gemini": "Gemini digest content",
        }

        with patch("apps.podcast.services.write_briefing.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"
            write_briefing(
                cross_validation="Validated claims data",
                research_digests=digests,
                episode_title="Episode Title",
            )

            call_args = mock_agent.run_sync.call_args[0][0]
            assert "Validated claims data" in call_args
            assert "Perplexity digest content" in call_args
            assert "Gemini digest content" in call_args
            assert "--- Digest: perplexity ---" in call_args
            assert "--- Digest: gemini ---" in call_args
            assert "Episode Title" in call_args

    def test_logs_usage(self, caplog):
        sample = self._make_sample_briefing()
        mock_result = self._make_mock_result(sample)

        with patch("apps.podcast.services.write_briefing.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"

            with caplog.at_level(logging.INFO):
                write_briefing(
                    cross_validation="data",
                    research_digests={"tool": "digest"},
                    episode_title="Title",
                )

        assert "write_briefing" in caplog.text
        assert "input_tokens=3000" in caplog.text
        assert "output_tokens=1500" in caplog.text

    def test_empty_briefing(self):
        empty = MasterBriefing(
            verified_findings=[],
            depth_distribution=[],
            practical_audit=[],
            story_bank=[],
            counterpoints=[],
            research_gaps=[],
            source_inventory=SourceInventory(tier1=[], tier2=[], tier3=[]),
            synthesis_notes="Insufficient data for synthesis.",
        )
        mock_result = self._make_mock_result(empty)

        with patch("apps.podcast.services.write_briefing.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"
            result = write_briefing(
                cross_validation="minimal",
                research_digests={},
                episode_title="Sparse Episode",
            )

        assert isinstance(result, MasterBriefing)
        assert len(result.verified_findings) == 0
        assert len(result.story_bank) == 0
        assert result.synthesis_notes == "Insufficient data for synthesis."
