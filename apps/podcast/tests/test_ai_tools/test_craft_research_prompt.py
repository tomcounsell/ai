"""Tests for the craft_research_prompt PydanticAI tool."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from apps.podcast.services.craft_research_prompt import (
    ResearchPrompt,
    TargetedResearchPrompts,
    craft_research_prompt,
    craft_targeted_prompts,
)


class TestCraftResearchPrompt:
    """Tests for craft_research_prompt Named AI Tool."""

    def _make_mock_result(self, output: ResearchPrompt) -> MagicMock:
        mock_usage = MagicMock()
        mock_usage.input_tokens = 500
        mock_usage.output_tokens = 300

        mock_result = MagicMock()
        mock_result.output = output
        mock_result.usage.return_value = mock_usage
        return mock_result

    def test_returns_research_prompt(self):
        expected = ResearchPrompt(prompt="Research sleep deprivation effects...")
        mock_result = self._make_mock_result(expected)

        with patch(
            "apps.podcast.services.craft_research_prompt.single_agent"
        ) as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-6"
            result = craft_research_prompt(
                episode_brief="Brief about sleep science",
                episode_title="Sleep Science Deep Dive",
                research_type="perplexity",
            )

        assert isinstance(result, ResearchPrompt)
        assert result.prompt == "Research sleep deprivation effects..."

    def test_prompt_includes_episode_context(self):
        expected = ResearchPrompt(prompt="crafted prompt")
        mock_result = self._make_mock_result(expected)

        with patch(
            "apps.podcast.services.craft_research_prompt.single_agent"
        ) as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-6"
            craft_research_prompt(
                episode_brief="A brief about AI regulation",
                episode_title="AI Regulation Today",
                research_type="gemini",
            )

            call_args = mock_agent.run_sync.call_args[0][0]
            assert "AI Regulation Today" in call_args
            assert "gemini" in call_args
            assert "A brief about AI regulation" in call_args

    def test_logs_usage(self, caplog):
        expected = ResearchPrompt(prompt="prompt text")
        mock_result = self._make_mock_result(expected)

        with patch(
            "apps.podcast.services.craft_research_prompt.single_agent"
        ) as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-6"

            with caplog.at_level(logging.INFO):
                craft_research_prompt(
                    episode_brief="brief",
                    episode_title="Title",
                    research_type="perplexity",
                )

        assert "craft_research_prompt" in caplog.text
        assert "input_tokens=500" in caplog.text
        assert "output_tokens=300" in caplog.text


class TestCraftTargetedPrompts:
    """Tests for craft_targeted_prompts Named AI Tool."""

    def _make_mock_result(self, output: TargetedResearchPrompts) -> MagicMock:
        mock_usage = MagicMock()
        mock_usage.input_tokens = 800
        mock_usage.output_tokens = 600

        mock_result = MagicMock()
        mock_result.output = output
        mock_result.usage.return_value = mock_usage
        return mock_result

    def test_returns_targeted_prompts(self):
        expected = TargetedResearchPrompts(
            gpt_prompt="GPT: investigate industry adoption...",
            gemini_prompt="Gemini: analyze policy frameworks...",
            together_prompt="Together: explore adjacent topics...",
            claude_prompt="Claude: deep dive into technical foundations...",
        )
        mock_result = self._make_mock_result(expected)

        with patch(
            "apps.podcast.services.craft_research_prompt.targeted_agent"
        ) as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-6"
            result = craft_targeted_prompts(
                episode_brief="Brief about renewable energy",
                question_discovery="Gaps: grid storage economics",
                episode_title="Renewable Energy Revolution",
            )

        assert isinstance(result, TargetedResearchPrompts)
        assert "industry adoption" in result.gpt_prompt
        assert "policy frameworks" in result.gemini_prompt
        assert "adjacent topics" in result.together_prompt

    def test_prompt_includes_question_discovery(self):
        expected = TargetedResearchPrompts(
            gpt_prompt="gpt prompt",
            gemini_prompt="gemini prompt",
            together_prompt="together prompt",
            claude_prompt="claude prompt",
        )
        mock_result = self._make_mock_result(expected)

        with patch(
            "apps.podcast.services.craft_research_prompt.targeted_agent"
        ) as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-6"
            craft_targeted_prompts(
                episode_brief="Brief content",
                question_discovery="Gap: missing data on long-term outcomes",
                episode_title="Health Outcomes",
            )

            call_args = mock_agent.run_sync.call_args[0][0]
            assert "Health Outcomes" in call_args
            assert "Brief content" in call_args
            assert "missing data on long-term outcomes" in call_args
            assert "batch" in call_args

    def test_logs_usage(self, caplog):
        expected = TargetedResearchPrompts(
            gpt_prompt="gpt",
            gemini_prompt="gemini",
            together_prompt="together",
            claude_prompt="claude",
        )
        mock_result = self._make_mock_result(expected)

        with patch(
            "apps.podcast.services.craft_research_prompt.targeted_agent"
        ) as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-6"

            with caplog.at_level(logging.INFO):
                craft_targeted_prompts(
                    episode_brief="brief",
                    question_discovery="questions",
                    episode_title="Title",
                )

        assert "craft_targeted_prompts" in caplog.text
        assert "input_tokens=800" in caplog.text
        assert "output_tokens=600" in caplog.text
