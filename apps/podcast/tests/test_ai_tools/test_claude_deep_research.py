"""Tests for the Claude deep research orchestrator (multi-agent pipeline)."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from apps.podcast.services.claude_deep_research.orchestrate import (
    _format_findings_for_synthesis,
    deep_research,
)
from apps.podcast.services.claude_deep_research.planner import (
    ResearchPlan,
    ResearchSubtask,
    plan_research,
)
from apps.podcast.services.claude_deep_research.researcher import (
    SubagentFindings,
    _create_researcher_agent,
    research_subtask,
)
from apps.podcast.services.claude_deep_research.synthesizer import (
    DeepResearchReport,
    synthesize_findings,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _mock_plan():
    return ResearchPlan(
        subtasks=[
            ResearchSubtask(
                focus="Academic research on topic X",
                search_strategy="Search academic databases",
                key_questions=["Q1?", "Q2?", "Q3?"],
                allowed_domains=[".edu"],
            ),
            ResearchSubtask(
                focus="Industry applications of topic X",
                search_strategy="Search industry reports",
                key_questions=["Q4?", "Q5?"],
            ),
        ],
        synthesis_guidance="Lead with academic evidence, then show industry applications.",
    )


def _mock_findings():
    return SubagentFindings(
        focus="Academic research on topic X",
        findings="Detailed academic findings...",
        sources=["https://example.edu/paper1"],
        key_data_points=["Finding A: 85% effectiveness"],
        confidence="high",
        gaps_identified=["No long-term studies available"],
    )


def _mock_findings_2():
    return SubagentFindings(
        focus="Industry applications of topic X",
        findings="Industry applications reveal widespread adoption...",
        sources=["https://example.com/report1"],
        key_data_points=["Market size: $5B"],
        confidence="medium",
        gaps_identified=[],
    )


def _mock_report():
    return DeepResearchReport(
        content="# Comprehensive Report\n\nDetailed research...",
        sources_cited=["https://example.edu/paper1"],
        key_findings=["Topic X is 85% effective"],
        confidence_assessment="High confidence overall",
        gaps_remaining=["Long-term data needed"],
    )


def _make_mock_result(output):
    """Build a mock AgentRunResult with the given output model."""
    mock_usage = MagicMock()
    mock_usage.input_tokens = 1000
    mock_usage.output_tokens = 500

    mock_result = MagicMock()
    mock_result.output = output
    mock_result.usage.return_value = mock_usage
    return mock_result


# ---------------------------------------------------------------------------
# Stage 1: Planner
# ---------------------------------------------------------------------------


class TestPlanResearch:
    """Tests for plan_research (Stage 1)."""

    @patch("apps.podcast.services.claude_deep_research.planner.planner_agent")
    def test_returns_research_plan(self, mock_agent):
        plan = _mock_plan()
        mock_agent.run_sync.return_value = _make_mock_result(plan)
        mock_agent.model = "anthropic:claude-opus-4-6"

        result = plan_research("Investigate topic X")

        assert isinstance(result, ResearchPlan)
        assert len(result.subtasks) == 2
        assert result.subtasks[0].focus == "Academic research on topic X"
        assert len(result.subtasks[0].key_questions) == 3
        assert result.synthesis_guidance.startswith("Lead with")

    @patch("apps.podcast.services.claude_deep_research.planner.planner_agent")
    def test_passes_command_to_agent(self, mock_agent):
        mock_agent.run_sync.return_value = _make_mock_result(_mock_plan())
        mock_agent.model = "anthropic:claude-opus-4-6"

        plan_research("My research command")

        mock_agent.run_sync.assert_called_once_with("My research command")

    @patch("apps.podcast.services.claude_deep_research.planner.planner_agent")
    def test_logs_usage(self, mock_agent, caplog):
        mock_agent.run_sync.return_value = _make_mock_result(_mock_plan())
        mock_agent.model = "anthropic:claude-opus-4-6"

        with caplog.at_level(logging.INFO):
            plan_research("Topic X")

        assert "plan_research" in caplog.text
        assert "subtasks=2" in caplog.text


# ---------------------------------------------------------------------------
# Stage 2: Researcher
# ---------------------------------------------------------------------------


class TestResearchSubtask:
    """Tests for research_subtask (Stage 2)."""

    @patch(
        "apps.podcast.services.claude_deep_research.researcher._create_researcher_agent"
    )
    def test_returns_subagent_findings(self, mock_factory):
        findings = _mock_findings()
        mock_agent = MagicMock()
        mock_agent.run_sync.return_value = _make_mock_result(findings)
        mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"
        mock_factory.return_value = mock_agent

        result = research_subtask(
            focus="Academic research on topic X",
            search_strategy="Search academic databases",
            key_questions=["Q1?", "Q2?", "Q3?"],
            allowed_domains=[".edu"],
        )

        assert isinstance(result, SubagentFindings)
        assert result.focus == "Academic research on topic X"
        assert result.confidence == "high"
        assert len(result.sources) == 1
        assert len(result.gaps_identified) == 1

    @patch(
        "apps.podcast.services.claude_deep_research.researcher._create_researcher_agent"
    )
    def test_passes_allowed_domains(self, mock_factory):
        mock_agent = MagicMock()
        mock_agent.run_sync.return_value = _make_mock_result(_mock_findings())
        mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"
        mock_factory.return_value = mock_agent

        research_subtask(
            focus="Test",
            search_strategy="Search",
            key_questions=["Q?"],
            allowed_domains=[".edu", ".gov"],
        )

        mock_factory.assert_called_once_with(
            max_searches=10,
            allowed_domains=[".edu", ".gov"],
        )

    @patch(
        "apps.podcast.services.claude_deep_research.researcher._create_researcher_agent"
    )
    def test_none_domains_when_empty(self, mock_factory):
        mock_agent = MagicMock()
        mock_agent.run_sync.return_value = _make_mock_result(_mock_findings())
        mock_agent.model = "anthropic:claude-sonnet-4-5-20250929"
        mock_factory.return_value = mock_agent

        research_subtask(
            focus="Test",
            search_strategy="Search",
            key_questions=["Q?"],
        )

        mock_factory.assert_called_once_with(
            max_searches=10,
            allowed_domains=None,
        )


class TestResearcherAgentFactory:
    """Tests for _create_researcher_agent factory."""

    def test_creates_agent_with_fetch_page_tool(self):
        agent = _create_researcher_agent(max_searches=5)
        # The agent should have a registered tool named fetch_page
        tool_names = list(agent._function_toolset.tools.keys())
        assert "fetch_page" in tool_names

    def test_creates_agent_with_web_search(self):
        agent = _create_researcher_agent(max_searches=5)
        # Should have builtin_tools configured (WebSearchTool)
        assert len(agent._builtin_tools) > 0


# ---------------------------------------------------------------------------
# Stage 3: Synthesizer
# ---------------------------------------------------------------------------


class TestSynthesizeFindings:
    """Tests for synthesize_findings (Stage 3)."""

    @patch("apps.podcast.services.claude_deep_research.synthesizer.synthesizer_agent")
    def test_returns_deep_research_report(self, mock_agent):
        report = _mock_report()
        mock_agent.run_sync.return_value = _make_mock_result(report)
        mock_agent.model = "anthropic:claude-opus-4-6"

        result = synthesize_findings("Plan summary", "Findings text")

        assert isinstance(result, DeepResearchReport)
        assert "Comprehensive Report" in result.content
        assert len(result.sources_cited) == 1
        assert len(result.key_findings) == 1
        assert result.confidence_assessment == "High confidence overall"

    @patch("apps.podcast.services.claude_deep_research.synthesizer.synthesizer_agent")
    def test_passes_plan_and_findings_to_agent(self, mock_agent):
        mock_agent.run_sync.return_value = _make_mock_result(_mock_report())
        mock_agent.model = "anthropic:claude-opus-4-6"

        synthesize_findings("My plan summary", "My findings text")

        call_args = mock_agent.run_sync.call_args[0][0]
        assert "My plan summary" in call_args
        assert "My findings text" in call_args

    @patch("apps.podcast.services.claude_deep_research.synthesizer.synthesizer_agent")
    def test_logs_usage(self, mock_agent, caplog):
        mock_agent.run_sync.return_value = _make_mock_result(_mock_report())
        mock_agent.model = "anthropic:claude-opus-4-6"

        with caplog.at_level(logging.INFO):
            synthesize_findings("Plan", "Findings")

        assert "synthesize_findings" in caplog.text
        assert "input_tokens=1000" in caplog.text


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class TestFormatFindingsForSynthesis:
    """Tests for _format_findings_for_synthesis helper."""

    def test_formats_plan_summary(self):
        plan = _mock_plan()
        findings = [_mock_findings()]
        plan_summary, _ = _format_findings_for_synthesis(plan, findings)

        assert "2 subtasks" in plan_summary
        assert "Lead with academic evidence" in plan_summary

    def test_formats_findings_text(self):
        plan = _mock_plan()
        findings = [_mock_findings(), _mock_findings_2()]
        _, findings_text = _format_findings_for_synthesis(plan, findings)

        assert "Academic research on topic X" in findings_text
        assert "Industry applications" in findings_text
        assert "85% effectiveness" in findings_text
        assert "---" in findings_text  # separator between sections
        assert "No long-term studies available" in findings_text

    def test_omits_gaps_section_when_empty(self):
        plan = _mock_plan()
        findings = [_mock_findings_2()]  # has empty gaps_identified
        _, findings_text = _format_findings_for_synthesis(plan, findings)

        assert "**Gaps:**" not in findings_text


class TestDeepResearch:
    """Tests for deep_research orchestrator (end-to-end with mocks)."""

    @patch("apps.podcast.services.claude_deep_research.orchestrate.synthesize_findings")
    @patch("apps.podcast.services.claude_deep_research.orchestrate.research_subtask")
    @patch("apps.podcast.services.claude_deep_research.orchestrate.plan_research")
    def test_full_pipeline(self, mock_plan, mock_research, mock_synthesize):
        mock_plan.return_value = _mock_plan()
        mock_research.side_effect = [_mock_findings(), _mock_findings_2()]
        mock_synthesize.return_value = _mock_report()

        result = deep_research("Research topic X")

        assert isinstance(result, DeepResearchReport)
        assert "Comprehensive Report" in result.content
        mock_plan.assert_called_once_with("Research topic X")
        assert mock_research.call_count == 2
        mock_synthesize.assert_called_once()

    @patch("apps.podcast.services.claude_deep_research.orchestrate.synthesize_findings")
    @patch("apps.podcast.services.claude_deep_research.orchestrate.research_subtask")
    @patch("apps.podcast.services.claude_deep_research.orchestrate.plan_research")
    def test_partial_subagent_failure(self, mock_plan, mock_research, mock_synthesize):
        """When one subagent fails, the orchestrator continues with the rest."""
        mock_plan.return_value = _mock_plan()
        mock_research.side_effect = [
            RuntimeError("API error"),
            _mock_findings_2(),
        ]
        mock_synthesize.return_value = _mock_report()

        result = deep_research("Research topic X")

        assert isinstance(result, DeepResearchReport)
        # research_subtask called twice (one fails, one succeeds)
        assert mock_research.call_count == 2
        # synthesize called with the one successful finding
        mock_synthesize.assert_called_once()

    @patch("apps.podcast.services.claude_deep_research.orchestrate.research_subtask")
    @patch("apps.podcast.services.claude_deep_research.orchestrate.plan_research")
    def test_all_subagents_fail_raises_runtime_error(self, mock_plan, mock_research):
        """When ALL subagents fail, RuntimeError is raised."""
        mock_plan.return_value = _mock_plan()
        mock_research.side_effect = RuntimeError("API error")

        with pytest.raises(RuntimeError, match="All subagents failed"):
            deep_research("Research topic X")

    @patch("apps.podcast.services.claude_deep_research.orchestrate.synthesize_findings")
    @patch("apps.podcast.services.claude_deep_research.orchestrate.research_subtask")
    @patch("apps.podcast.services.claude_deep_research.orchestrate.plan_research")
    def test_passes_subtask_fields_to_researcher(
        self, mock_plan, mock_research, mock_synthesize
    ):
        plan = _mock_plan()
        mock_plan.return_value = plan
        mock_research.return_value = _mock_findings()
        mock_synthesize.return_value = _mock_report()

        deep_research("Research topic X")

        # Check first call received correct arguments
        first_call = mock_research.call_args_list[0]
        assert first_call.kwargs["focus"] == "Academic research on topic X"
        assert first_call.kwargs["search_strategy"] == "Search academic databases"
        assert first_call.kwargs["key_questions"] == ["Q1?", "Q2?", "Q3?"]
        assert first_call.kwargs["allowed_domains"] == [".edu"]

        # Second call has no allowed_domains (empty list -> None)
        second_call = mock_research.call_args_list[1]
        assert second_call.kwargs["allowed_domains"] is None


# ---------------------------------------------------------------------------
# Service layer integration: run_claude_research
# ---------------------------------------------------------------------------


def _create_test_episode():
    """Create a Podcast + Episode for DB tests."""
    from apps.podcast.models import Episode, Podcast

    podcast = Podcast.objects.create(
        title="Test Podcast",
        slug="test-podcast-dr",
        description="desc",
        author_name="Author",
        author_email="a@b.com",
    )
    episode = Episode.objects.create(
        podcast=podcast,
        title="Test Episode",
        slug="test-episode-dr",
        description="Test desc",
    )
    return episode


@pytest.mark.django_db
@patch("apps.podcast.services.claude_deep_research.orchestrate.plan_research")
@patch("apps.podcast.services.claude_deep_research.orchestrate.research_subtask")
@patch("apps.podcast.services.claude_deep_research.orchestrate.synthesize_findings")
def test_run_claude_research(mock_synthesize, mock_research, mock_plan):
    from apps.podcast.models import EpisodeArtifact
    from apps.podcast.services.research import run_claude_research

    episode = _create_test_episode()

    mock_plan.return_value = _mock_plan()
    mock_research.return_value = _mock_findings()
    mock_synthesize.return_value = _mock_report()

    artifact = run_claude_research(episode.id, prompt="Test prompt")

    assert artifact.title == "p2-claude"
    assert "Comprehensive Report" in artifact.content
    assert "Key Findings" in artifact.content
    assert "Gaps Remaining" in artifact.content
    assert "Confidence Assessment" in artifact.content
    assert artifact.metadata["key_findings"] == ["Topic X is 85% effective"]
    assert artifact.metadata["sources_cited"] == ["https://example.edu/paper1"]
    assert artifact.metadata["confidence_assessment"] == "High confidence overall"
    assert artifact.description == "Claude multi-agent deep research output."
    assert artifact.workflow_context == "Research Gathering"

    # Verify artifact is persisted
    saved = EpisodeArtifact.objects.get(episode=episode, title="p2-claude")
    assert saved.id == artifact.id


@pytest.mark.django_db
@patch("apps.podcast.services.claude_deep_research.orchestrate.plan_research")
@patch("apps.podcast.services.claude_deep_research.orchestrate.research_subtask")
@patch("apps.podcast.services.claude_deep_research.orchestrate.synthesize_findings")
def test_run_claude_research_updates_existing(
    mock_synthesize, mock_research, mock_plan
):
    from apps.podcast.models import EpisodeArtifact
    from apps.podcast.services.research import run_claude_research

    episode = _create_test_episode()

    # Create initial artifact
    EpisodeArtifact.objects.create(
        episode=episode,
        title="p2-claude",
        content="old content",
        description="placeholder",
    )

    mock_plan.return_value = _mock_plan()
    mock_research.return_value = _mock_findings()
    mock_synthesize.return_value = _mock_report()
    artifact = run_claude_research(episode.id, prompt="Test prompt")

    assert "Comprehensive Report" in artifact.content
    assert (
        EpisodeArtifact.objects.filter(episode=episode, title="p2-claude").count() == 1
    )
