"""Tests for the plan_episode PydanticAI tool."""

import logging
from unittest.mock import MagicMock, patch

from apps.podcast.services.plan_episode import (
    CounterpointMoment,
    DepthBudgetEntry,
    EpisodeArc,
    EpisodeMetadataBlock,
    EpisodePlan,
    KeyTerm,
    ModeDefinition,
    NotebookLMGuidance,
    SignpostingLanguage,
    StructureEntry,
    ToolkitSelections,
    plan_episode,
)


class TestPlanEpisode:
    """Tests for plan_episode service function."""

    def _make_mock_result(self, plan: EpisodePlan) -> MagicMock:
        """Build a mock AgentRunResult with the given plan."""
        mock_usage = MagicMock()
        mock_usage.input_tokens = 8000
        mock_usage.output_tokens = 4000

        mock_result = MagicMock()
        mock_result.output = plan
        mock_result.usage.return_value = mock_usage
        return mock_result

    def _make_sample_plan(self) -> EpisodePlan:
        return EpisodePlan(
            metadata=EpisodeMetadataBlock(
                series="Yudame Research",
                position="standalone",
                core_question="How does sleep affect cognitive performance?",
                evidence_status="consensus",
                content_density="balanced",
            ),
            toolkit_selections=ToolkitSelections(
                hook_type="provocative_question",
                hook_content="What if everything you know about sleep is wrong?",
                takeaway_structure="numbered_list",
                contradiction_handling="host_debate",
            ),
            structure_map=[
                StructureEntry(
                    section="Opening",
                    primary_mode="storytelling",
                    duration="3-5 min",
                    purpose="Hook and frame the episode",
                    key_elements=["Sleep myth", "Core question"],
                ),
                StructureEntry(
                    section="Evidence Deep Dive",
                    primary_mode="research",
                    duration="15-20 min",
                    purpose="Present key findings",
                    key_elements=["Meta-analysis", "RCT results"],
                ),
            ],
            mode_switching=[
                ModeDefinition(
                    mode="research",
                    language_markers=[
                        "The data shows...",
                        "According to the study...",
                    ],
                    duration_allocation="40%",
                ),
                ModeDefinition(
                    mode="practical",
                    language_markers=[
                        "So what does this look like...",
                        "Here's what you can actually do...",
                    ],
                    duration_allocation="30%",
                ),
            ],
            signposting=SignpostingLanguage(
                opening_preview=("Today we're covering three big ideas about sleep."),
                transitions=[
                    "Now let's shift from the why to the how.",
                    "Building on that research...",
                ],
                progress_markers=[
                    "That's the first of our three big ideas.",
                    "We're about halfway through.",
                ],
                mode_switch_signals=[
                    "Let's zoom out for a moment.",
                    "Let's get practical.",
                ],
            ),
            depth_budget=[
                DepthBudgetEntry(
                    theme="Sleep and memory",
                    importance="primary",
                    duration="12 min",
                    percentage=35,
                    notes="Core topic, deepest coverage",
                ),
                DepthBudgetEntry(
                    theme="Sleep hygiene protocols",
                    importance="primary",
                    duration="10 min",
                    percentage=30,
                    notes="Actionable takeaways",
                ),
            ],
            counterpoint_moments=[
                CounterpointMoment(
                    topic="Optimal sleep duration",
                    timing="15:00",
                    speaker_a_position="8 hours is non-negotiable",
                    speaker_b_position="Individual variation matters more",
                    tension_type="nuance",
                    language_templates=[
                        "Wait, but doesn't that contradict...",
                        "I see it differently because...",
                    ],
                ),
            ],
            episode_arc=EpisodeArc(
                opening=(
                    "Hook with surprising sleep statistic, frame the core "
                    "question, preview the three sections."
                ),
                middle=(
                    "Progressive deep dive: sleep biology, memory research, "
                    "then practical protocols with counterpoint debate."
                ),
                closing=(
                    "Synthesize the three takeaways, callback to opening "
                    "statistic, CTA to try a 7-day sleep protocol."
                ),
            ),
            notebooklm_guidance=NotebookLMGuidance(
                opening_instructions=(
                    "Start with the shocking statistic about sleep deprivation."
                ),
                key_terms=[
                    KeyTerm(
                        term="Sleep consolidation",
                        definition="The process by which memories are stabilized during sleep",
                        pronunciation="",
                    ),
                    KeyTerm(
                        term="Circadian rhythm",
                        definition="The body's internal 24-hour clock",
                        pronunciation="sir-KAY-dee-un",
                    ),
                ],
                studies_to_emphasize=[
                    "Smith et al. 2024 meta-analysis (47 trials)",
                ],
                stories_to_feature=[
                    "The Stanford sleep lab story",
                ],
                counterpoint_execution=[
                    "Host A firmly argues for 8-hour minimum, Host B pushes back with individual variation data",
                ],
                closing_callback=("Return to the opening statistic and reframe it."),
                call_to_action=("Try the 7-day sleep consistency challenge."),
            ),
        )

    def test_returns_episode_plan(self):
        mock_plan = self._make_sample_plan()
        mock_result = self._make_mock_result(mock_plan)

        with patch("apps.podcast.services.plan_episode.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-opus-4-6"
            result = plan_episode(
                "Synthesis report text",
                "Master briefing text",
                "Sleep Science Episode",
            )

        assert isinstance(result, EpisodePlan)
        assert result.metadata.position == "standalone"
        assert result.metadata.evidence_status == "consensus"
        assert len(result.structure_map) == 2
        assert len(result.mode_switching) == 2
        assert len(result.counterpoint_moments) == 1
        assert len(result.depth_budget) == 2
        assert len(result.notebooklm_guidance.key_terms) == 2

    def test_passes_correct_prompt(self):
        mock_result = self._make_mock_result(self._make_sample_plan())

        with patch("apps.podcast.services.plan_episode.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-opus-4-6"
            plan_episode(
                "My report content",
                "My briefing content",
                "My Episode Title",
            )

            call_args = mock_agent.run_sync.call_args[0][0]
            assert "My Episode Title" in call_args
            assert "My report content" in call_args
            assert "My briefing content" in call_args
            assert "SYNTHESIS REPORT" in call_args
            assert "MASTER BRIEFING" in call_args

    def test_passes_series_name(self):
        mock_result = self._make_mock_result(self._make_sample_plan())

        with patch("apps.podcast.services.plan_episode.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-opus-4-6"
            plan_episode(
                "Report",
                "Briefing",
                "Episode",
                series_name="Yudame Research",
            )

            call_args = mock_agent.run_sync.call_args[0][0]
            assert "Series: Yudame Research" in call_args

    def test_omits_series_line_when_empty(self):
        mock_result = self._make_mock_result(self._make_sample_plan())

        with patch("apps.podcast.services.plan_episode.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-opus-4-6"
            plan_episode("Report", "Briefing", "Episode")

            call_args = mock_agent.run_sync.call_args[0][0]
            assert "Series:" not in call_args

    def test_logs_usage(self, caplog):
        mock_result = self._make_mock_result(self._make_sample_plan())

        with patch("apps.podcast.services.plan_episode.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-opus-4-6"

            with caplog.at_level(logging.INFO):
                plan_episode("report", "briefing", "Episode")

        assert "plan_episode" in caplog.text
        assert "input_tokens=8000" in caplog.text
        assert "output_tokens=4000" in caplog.text

    def test_uses_opus_model(self):
        from apps.podcast.services.plan_episode import agent as real_agent

        assert "opus" in str(real_agent.model).lower()

    def test_empty_plan(self):
        empty_plan = EpisodePlan(
            metadata=EpisodeMetadataBlock(
                series="",
                position="standalone",
                core_question="",
                evidence_status="consensus",
                content_density="balanced",
            ),
            toolkit_selections=ToolkitSelections(
                hook_type="",
                hook_content="",
                takeaway_structure="",
                contradiction_handling="",
            ),
            structure_map=[],
            mode_switching=[],
            signposting=SignpostingLanguage(
                opening_preview="",
                transitions=[],
                progress_markers=[],
                mode_switch_signals=[],
            ),
            depth_budget=[],
            counterpoint_moments=[],
            episode_arc=EpisodeArc(
                opening="",
                middle="",
                closing="",
            ),
            notebooklm_guidance=NotebookLMGuidance(
                opening_instructions="",
                key_terms=[],
                studies_to_emphasize=[],
                stories_to_feature=[],
                counterpoint_execution=[],
                closing_callback="",
                call_to_action="",
            ),
        )
        mock_result = self._make_mock_result(empty_plan)

        with patch("apps.podcast.services.plan_episode.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-opus-4-6"
            result = plan_episode("report", "briefing", "Episode")

        assert isinstance(result, EpisodePlan)
        assert len(result.structure_map) == 0
        assert len(result.counterpoint_moments) == 0
