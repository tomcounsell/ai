"""Cross-validate research findings across multiple sources."""

import logging
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent

logger = logging.getLogger(__name__)


# --- Output schema ---


class ConflictPosition(BaseModel):
    source: str
    position: str


class Claim(BaseModel):
    claim: str
    sources: list[str]
    confidence: str  # "high", "medium", "low"


class Conflict(BaseModel):
    topic: str
    positions: list[ConflictPosition]
    resolution_suggestion: str


class SourceAssessment(BaseModel):
    source: str
    strengths: list[str]
    weaknesses: list[str]
    unique_contributions: list[str]


class CoverageEntry(BaseModel):
    topic: str
    sources_covering: list[str]
    depth: str  # "deep", "moderate", "shallow"


class CrossValidation(BaseModel):
    verified_claims: list[Claim]  # confirmed by 2+ sources
    single_source_claims: list[Claim]  # only one source
    conflicting_claims: list[Conflict]
    source_quality: list[SourceAssessment]
    coverage_map: list[CoverageEntry]
    summary: str


# --- Agent ---

_PROMPT_FILE = Path(__file__).parent / "prompts" / "cross_validate.md"
_SYSTEM_PROMPT = _PROMPT_FILE.read_text()

agent = Agent(
    "anthropic:claude-sonnet-4-5-20250929",
    output_type=CrossValidation,
    system_prompt=_SYSTEM_PROMPT,
    defer_model_check=True,
)


# --- Public interface ---


def cross_validate(
    research_texts: dict[str, str], episode_topic: str
) -> CrossValidation:
    """Cross-validate research findings across multiple sources.

    Args:
        research_texts: Mapping of tool name to research text
            (e.g. {"perplexity": "...", "chatgpt": "..."}).
        episode_topic: Topic of the episode for context.

    Returns:
        CrossValidation with verified, single-source, and conflicting claims.
    """
    sections = []
    for tool_name, text in research_texts.items():
        sections.append(f"--- Source: {tool_name} ---\n{text}\n--- End Source ---")
    prompt = f"Episode: {episode_topic}\n\n" + "\n\n".join(sections)

    result = agent.run_sync(prompt)
    logger.info(
        "cross_validate: model=%s input_tokens=%d output_tokens=%d",
        agent.model,
        result.usage().input_tokens,
        result.usage().output_tokens,
    )
    return result.output
