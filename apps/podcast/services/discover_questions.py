"""Analyze research to discover questions for targeted followup research."""

import logging
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent

logger = logging.getLogger(__name__)


# --- Output schema ---


class Subtopic(BaseModel):
    name: str
    coverage_depth: str  # "extensive", "moderate", "brief"


class ToolRecommendation(BaseModel):
    tool: str  # "gpt-researcher", "gemini", "claude", "grok"
    focus: str
    priority: str  # "high", "medium", "low"


class QuestionDiscovery(BaseModel):
    subtopics_found: list[Subtopic]
    gaps_in_literature: list[str]
    recent_developments_needed: list[str]
    contradictions_to_resolve: list[str]
    industry_questions: list[str]
    policy_questions: list[str]
    practitioner_questions: list[str]
    recommended_tools: list[ToolRecommendation]


# --- Agent ---

_PROMPT_FILE = Path(__file__).parent / "prompts" / "discover_questions.md"
_SYSTEM_PROMPT = _PROMPT_FILE.read_text()

agent = Agent(
    "anthropic:claude-sonnet-4-5-20250929",
    output_type=QuestionDiscovery,
    system_prompt=_SYSTEM_PROMPT,
    defer_model_check=True,
)


# --- Public interface ---


def discover_questions(research_digest: str, episode_topic: str) -> QuestionDiscovery:
    """Analyze research to discover questions for targeted followup.

    Args:
        research_digest: Text of a research digest or raw research.
        episode_topic: Topic of the episode for context.

    Returns:
        QuestionDiscovery with gaps, questions, and tool recommendations.
    """
    result = agent.run_sync(
        f"Episode topic: {episode_topic}\n\nResearch:\n{research_digest}"
    )
    logger.info(
        "discover_questions: model=%s input_tokens=%d output_tokens=%d",
        agent.model,
        result.usage().input_tokens,
        result.usage().output_tokens,
    )
    return result.output
