"""Create an episode content plan that guides NotebookLM audio generation."""

import logging
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent

logger = logging.getLogger(__name__)


# --- Output schema ---


class EpisodeMetadataBlock(BaseModel):
    series: str
    position: str  # "opener", "middle", "closer", "standalone"
    core_question: str
    evidence_status: str  # "consensus", "minor conflict", "major conflict"
    content_density: str  # "concept-heavy", "protocol-heavy", "balanced"


class ToolkitSelections(BaseModel):
    hook_type: str
    hook_content: str
    takeaway_structure: str
    contradiction_handling: str


class StructureEntry(BaseModel):
    section: str
    primary_mode: str
    duration: str
    purpose: str
    key_elements: list[str]


class ModeDefinition(BaseModel):
    mode: str  # "philosophy", "research", "storytelling", "practical", "landing"
    language_markers: list[str]
    duration_allocation: str


class SignpostingLanguage(BaseModel):
    opening_preview: str
    transitions: list[str]
    progress_markers: list[str]
    mode_switch_signals: list[str]


class DepthBudgetEntry(BaseModel):
    theme: str
    importance: str
    duration: str
    percentage: int
    notes: str


class CounterpointMoment(BaseModel):
    topic: str
    timing: str
    speaker_a_position: str
    speaker_b_position: str
    tension_type: str
    language_templates: list[str]


class EpisodeArc(BaseModel):
    opening: str  # 3-5 min description
    middle: str  # 20-30 min description
    closing: str  # 3-5 min description


class KeyTerm(BaseModel):
    term: str
    definition: str
    pronunciation: str = ""


class NotebookLMGuidance(BaseModel):
    opening_instructions: str
    key_terms: list[KeyTerm]
    studies_to_emphasize: list[str]
    stories_to_feature: list[str]
    counterpoint_execution: list[str]
    closing_callback: str
    call_to_action: str


class EpisodePlan(BaseModel):
    metadata: EpisodeMetadataBlock
    toolkit_selections: ToolkitSelections
    structure_map: list[StructureEntry]
    mode_switching: list[ModeDefinition]
    signposting: SignpostingLanguage
    depth_budget: list[DepthBudgetEntry]
    counterpoint_moments: list[CounterpointMoment]
    episode_arc: EpisodeArc
    notebooklm_guidance: NotebookLMGuidance


# --- Agent ---

_PROMPT_FILE = Path(__file__).parent / "prompts" / "plan_episode.md"
_SYSTEM_PROMPT = _PROMPT_FILE.read_text()

agent = Agent(
    "anthropic:claude-opus-4-6",
    output_type=EpisodePlan,
    system_prompt=_SYSTEM_PROMPT,
    defer_model_check=True,
)


# --- Public interface ---


def plan_episode(
    report: str, briefing: str, episode_title: str, series_name: str = ""
) -> EpisodePlan:
    """Create an episode content plan that guides NotebookLM audio generation.

    Args:
        report: The synthesis report text.
        briefing: Master briefing text.
        episode_title: Title of the episode.
        series_name: Optional series name for context.

    Returns:
        EpisodePlan with structure, modes, depth budget, and NotebookLM guidance.
    """
    series_line = f"Series: {series_name}\n" if series_name else ""
    prompt = (
        f"Episode: {episode_title}\n"
        f"{series_line}\n"
        f"=== SYNTHESIS REPORT ===\n{report}\n\n"
        f"=== MASTER BRIEFING ===\n{briefing}"
    )

    result = agent.run_sync(prompt)
    logger.info(
        "plan_episode: model=%s input_tokens=%d output_tokens=%d",
        agent.model,
        result.usage().input_tokens,
        result.usage().output_tokens,
    )
    return result.output
