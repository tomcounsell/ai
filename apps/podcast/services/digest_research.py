"""Generate a compact structured digest from raw research output."""

import logging

from pydantic import BaseModel
from pydantic_ai import Agent

logger = logging.getLogger(__name__)


# --- Output schema ---


class KeyFinding(BaseModel):
    finding: str
    confidence: str  # "high", "medium", "low"
    source: str


class Source(BaseModel):
    citation: str
    tier: str  # "tier1", "tier2", "tier3"
    url: str = ""


class ResearchDigest(BaseModel):
    table_of_contents: list[str]
    key_findings: list[KeyFinding]  # priority-ordered
    statistics: list[str]  # notable data points
    sources: list[Source]  # tiered: tier1/tier2/tier3
    topics: list[str]  # searchable keywords
    questions_answered: list[str]
    questions_unanswered: list[str]
    contradictions: list[str]


# --- Agent ---

agent = Agent(
    "anthropic:claude-sonnet-4-5-20250929",
    output_type=ResearchDigest,
    system_prompt=(
        "You are a research analyst. Given raw research output from a deep "
        "research tool, create a compact structured digest. Prioritize findings "
        "by importance, tier sources by quality (tier1: meta-analyses/systematic "
        "reviews, tier2: RCTs/large studies, tier3: case studies/reports), and "
        "identify what questions remain unanswered."
    ),
    defer_model_check=True,
)


# --- Public interface ---


def digest_research(research_text: str, episode_topic: str = "") -> ResearchDigest:
    """Generate a compact digest from raw research output.

    Args:
        research_text: Full content of a research file.
        episode_topic: Optional episode topic for context.

    Returns:
        ResearchDigest with structured findings, sources, and gaps.
    """
    prompt = research_text
    if episode_topic:
        prompt = f"Episode topic: {episode_topic}\n\n{research_text}"
    result = agent.run_sync(prompt)
    logger.info(
        "digest_research: model=%s input_tokens=%d output_tokens=%d",
        agent.model,
        result.usage().input_tokens,
        result.usage().output_tokens,
    )
    return result.output
