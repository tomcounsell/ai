"""Generate chapter markers from a podcast transcript."""

import logging
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent

logger = logging.getLogger(__name__)


# --- Output schema ---


class Chapter(BaseModel):
    title: str
    start_time: str  # "MM:SS"
    summary: str


class ChapterList(BaseModel):
    chapters: list[Chapter]


# --- Agent ---

_PROMPT_FILE = Path(__file__).parent / "prompts" / "generate_chapters.md"
_SYSTEM_PROMPT = _PROMPT_FILE.read_text()

agent = Agent(
    "anthropic:claude-sonnet-4-5-20250929",
    output_type=ChapterList,
    system_prompt=_SYSTEM_PROMPT,
    defer_model_check=True,
)


# --- Public interface ---


def generate_chapters(transcript: str, episode_title: str) -> ChapterList:
    """Generate chapter markers from a transcript.

    Args:
        transcript: Full episode transcript with timestamps.
        episode_title: Title of the episode for context.

    Returns:
        ChapterList with 10-15 chapters.
    """
    result = agent.run_sync(f"Episode: {episode_title}\n\nTranscript:\n{transcript}")
    logger.info(
        "generate_chapters: model=%s input_tokens=%d output_tokens=%d",
        agent.model,
        result.usage().input_tokens,
        result.usage().output_tokens,
    )
    return result.output
