"""Stage 1: Opus planner that breaks a research command into focused subtasks."""

import logging
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent

logger = logging.getLogger(__name__)


# --- Output schema ---


class ResearchSubtask(BaseModel):
    focus: str  # what this subagent should investigate
    search_strategy: str  # suggested search approach
    key_questions: list[str]  # 3-5 specific questions to answer
    allowed_domains: list[str] = []  # optional domain hints


class ResearchPlan(BaseModel):
    subtasks: list[ResearchSubtask]  # 3-5 subtasks
    synthesis_guidance: str  # how to merge results


# --- Agent ---

_PROMPT_FILE = Path(__file__).parent / "prompts" / "planner.md"
_SYSTEM_PROMPT = _PROMPT_FILE.read_text()

planner_agent = Agent(
    "anthropic:claude-opus-4-6",
    output_type=ResearchPlan,
    system_prompt=_SYSTEM_PROMPT,
    defer_model_check=True,
    retries=3,
)


# --- Public interface ---


def plan_research(command: str) -> ResearchPlan:
    """Break a research command into 3-5 focused subtasks.

    Args:
        command: Free-text research command describing what to investigate.

    Returns:
        ResearchPlan with structured subtasks and synthesis guidance.
    """
    result = planner_agent.run_sync(command)
    logger.info(
        "plan_research: model=%s input_tokens=%d output_tokens=%d subtasks=%d",
        planner_agent.model,
        result.usage().input_tokens,
        result.usage().output_tokens,
        len(result.output.subtasks),
    )
    return result.output
