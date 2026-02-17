"""Craft focused, topic-specific research prompts for the podcast pipeline."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent

logger = logging.getLogger(__name__)


# --- Output schemas ---


class ResearchPrompt(BaseModel):
    prompt: str


class TargetedResearchPrompts(BaseModel):
    gpt_prompt: str
    gemini_prompt: str
    together_prompt: str


# --- Agents ---

_PROMPT_FILE = Path(__file__).parent / "prompts" / "craft_research_prompt.md"
_SYSTEM_PROMPT = _PROMPT_FILE.read_text()

single_agent = Agent(
    "anthropic:claude-sonnet-4-5-20250929",
    output_type=ResearchPrompt,
    system_prompt=_SYSTEM_PROMPT,
    defer_model_check=True,
)

targeted_agent = Agent(
    "anthropic:claude-sonnet-4-5-20250929",
    output_type=TargetedResearchPrompts,
    system_prompt=_SYSTEM_PROMPT,
    defer_model_check=True,
)


# --- Public interface ---


def craft_research_prompt(
    episode_brief: str,
    episode_title: str,
    research_type: str,
) -> ResearchPrompt:
    """Craft a single research prompt for the given research type.

    Used for Perplexity prompts in Phase 2 (initial academic research).

    Args:
        episode_brief: Content of the p1-brief artifact.
        episode_title: Title of the episode (also used as topic).
        research_type: One of ``"perplexity"``, ``"gpt"``, ``"gemini"``, or ``"together"``.

    Returns:
        ResearchPrompt with the crafted prompt string.
    """
    prompt = (
        f"Episode: {episode_title}\n"
        f"Research type: {research_type}\n\n"
        f"Episode Brief:\n{episode_brief}"
    )

    result = single_agent.run_sync(prompt)
    logger.info(
        "craft_research_prompt: type=%s model=%s input_tokens=%d output_tokens=%d",
        research_type,
        single_agent.model,
        result.usage().input_tokens,
        result.usage().output_tokens,
    )
    return result.output


def craft_targeted_prompts(
    episode_brief: str,
    question_discovery: str,
    episode_title: str,
) -> TargetedResearchPrompts:
    """Craft GPT-Researcher, Gemini, and Together prompts in a single call.

    Used after question discovery in Phase 3 to generate targeted
    research prompts for the parallel research sub-steps.

    Args:
        episode_brief: Content of the p1-brief artifact.
        question_discovery: Content of the question-discovery artifact.
        episode_title: Title of the episode (also used as topic).

    Returns:
        TargetedResearchPrompts with gpt_prompt, gemini_prompt, and together_prompt strings.
    """
    prompt = (
        f"Episode: {episode_title}\n"
        f"Research type: batch (generate GPT-Researcher, Gemini, and Together prompts)\n\n"
        f"Episode Brief:\n{episode_brief}\n\n"
        f"Question Discovery Analysis:\n{question_discovery}"
    )

    result = targeted_agent.run_sync(prompt)
    logger.info(
        "craft_targeted_prompts: model=%s input_tokens=%d output_tokens=%d",
        targeted_agent.model,
        result.usage().input_tokens,
        result.usage().output_tokens,
    )
    return result.output
