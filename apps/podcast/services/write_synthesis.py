"""Transform research materials into a narrative podcast report."""

import logging
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent

logger = logging.getLogger(__name__)


# --- Output schema ---


class ReportSection(BaseModel):
    heading: str
    content: str  # narrative prose with citations
    listener_implications: str  # "What does this mean for listeners?"
    stories_used: list[str] = []


class SynthesisReport(BaseModel):
    title: str
    sections: list[ReportSection]
    word_count: int
    core_takeaways: list[str]  # 1-3 explicit takeaways
    sources_cited: list[str]


# --- Agent ---

_PROMPT_FILE = Path(__file__).parent / "prompts" / "write_synthesis.md"
_SYSTEM_PROMPT = _PROMPT_FILE.read_text()

agent = Agent(
    "anthropic:claude-opus-4-6",
    output_type=SynthesisReport,
    system_prompt=_SYSTEM_PROMPT,
    defer_model_check=True,
)


# --- Public interface ---


def write_synthesis(
    briefing: str, research_texts: dict[str, str], episode_title: str
) -> SynthesisReport:
    """Transform research materials into a narrative podcast report.

    Args:
        briefing: Master briefing text (p3-briefing.md).
        research_texts: Mapping of tool name to research text for
            additional context.
        episode_title: Title of the episode.

    Returns:
        SynthesisReport with narrative sections, takeaways, and citations.
    """
    source_sections = []
    for tool_name, text in research_texts.items():
        source_sections.append(
            f"--- Source: {tool_name} ---\n{text}\n--- End Source ---"
        )
    prompt = (
        f"Episode: {episode_title}\n\n"
        f"=== MASTER BRIEFING ===\n{briefing}\n\n"
        f"=== ADDITIONAL RESEARCH CONTEXT ===\n" + "\n\n".join(source_sections)
    )

    result = agent.run_sync(prompt)
    logger.info(
        "write_synthesis: model=%s input_tokens=%d output_tokens=%d",
        agent.model,
        result.usage().input_tokens,
        result.usage().output_tokens,
    )
    return result.output
