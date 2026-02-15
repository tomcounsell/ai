"""Generate episode publishing metadata from report and transcript."""

import logging
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent

logger = logging.getLogger(__name__)


# --- Output schema ---


class Timestamp(BaseModel):
    time: str  # "MM:SS"
    description: str


class Resource(BaseModel):
    title: str
    url: str
    category: str  # "research", "tools", "reading"
    description: str


class EpisodeMetadata(BaseModel):
    description: str  # 1-2 sentences plain text
    what_youll_learn: list[str]  # 3-5 verb-led bullets
    key_timestamps: list[Timestamp]  # 5-7 major sections
    keywords: list[str]  # 5-10 episode-specific terms
    resources: list[Resource]  # 5-10 sources
    primary_cta: str
    voiced_cta: str


# --- Agent ---

_PROMPT_FILE = Path(__file__).parent / "prompts" / "write_metadata.md"
_SYSTEM_PROMPT = _PROMPT_FILE.read_text()

agent = Agent(
    "anthropic:claude-sonnet-4-5-20250929",
    output_type=EpisodeMetadata,
    system_prompt=_SYSTEM_PROMPT,
    defer_model_check=True,
)


# --- Public interface ---


def write_metadata(
    report: str, transcript: str, chapters_json: str, episode_title: str
) -> EpisodeMetadata:
    """Generate episode publishing metadata.

    Args:
        report: Episode report text.
        transcript: Full episode transcript.
        chapters_json: Chapter markers as JSON string.
        episode_title: Title of the episode.

    Returns:
        EpisodeMetadata with description, timestamps, keywords, and CTAs.
    """
    result = agent.run_sync(
        f"Episode: {episode_title}\n\n"
        f"Report:\n{report}\n\n"
        f"Transcript:\n{transcript}\n\n"
        f"Chapters:\n{chapters_json}"
    )
    logger.info(
        "write_metadata: model=%s input_tokens=%d output_tokens=%d",
        agent.model,
        result.usage().input_tokens,
        result.usage().output_tokens,
    )
    return result.output
