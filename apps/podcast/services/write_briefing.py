"""Create the master research briefing from cross-validated research."""

import logging
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent

logger = logging.getLogger(__name__)


# --- Output schema ---


class Evidence(BaseModel):
    finding: str
    source: str
    quality: str  # "meta-analysis", "RCT", "observational", etc.
    sample_size: str = ""


class TopicFindings(BaseModel):
    topic: str
    main_finding: str
    evidence: list[Evidence] = []
    contradictions: list[str] = []
    source_quality_notes: list[str] = []


class DepthEntry(BaseModel):
    topic: str
    depth_rating: str  # "deep", "moderate", "shallow"
    recommendation: str


class PracticalStep(BaseModel):
    finding: str
    implementation: str
    parameters: str  # specific timeframes, thresholds, etc.


class Story(BaseModel):
    title: str
    narrative: str
    memorability: str  # "high", "medium", "low"
    emotional_resonance: str
    integration_opportunity: str


class Counterpoint(BaseModel):
    topic: str
    position_a: str
    position_b: str
    dialogue_opportunity: str


class SourceInventory(BaseModel):
    tier1: list[str] = []  # meta-analyses, systematic reviews
    tier2: list[str] = []  # RCTs, large studies
    tier3: list[str] = []  # case studies, reports


class MasterBriefing(BaseModel):
    verified_findings: list[TopicFindings] = []
    depth_distribution: list[DepthEntry] = []
    practical_audit: list[PracticalStep] = []
    story_bank: list[Story] = []
    counterpoints: list[Counterpoint] = []
    research_gaps: list[str] = []
    source_inventory: SourceInventory
    synthesis_notes: str  # notes for the synthesis agent


# --- Agent ---

_PROMPT_FILE = Path(__file__).parent / "prompts" / "write_briefing.md"
_SYSTEM_PROMPT = _PROMPT_FILE.read_text()

agent = Agent(
    "anthropic:claude-sonnet-4-6",
    output_type=MasterBriefing,
    system_prompt=_SYSTEM_PROMPT,
    defer_model_check=True,
    retries=3,
)


# --- Public interface ---


def write_briefing(
    cross_validation: str,
    research_digests: dict[str, str],
    episode_title: str,
) -> MasterBriefing:
    """Create the master research briefing from cross-validated research.

    Args:
        cross_validation: JSON or text of cross-validation results.
        research_digests: Mapping of tool name to digest text.
        episode_title: Title of the episode.

    Returns:
        MasterBriefing with findings, stories, counterpoints, and gaps.
    """
    digest_sections = []
    for tool_name, text in research_digests.items():
        digest_sections.append(
            f"--- Digest: {tool_name} ---\n{text}\n--- End Digest ---"
        )
    prompt = (
        f"Episode: {episode_title}\n\n"
        f"Cross-Validation Results:\n{cross_validation}\n\n"
        f"Research Digests:\n" + "\n\n".join(digest_sections)
    )

    result = agent.run_sync(prompt)
    logger.info(
        "write_briefing: model=%s input_tokens=%d output_tokens=%d",
        agent.model,
        result.usage().input_tokens,
        result.usage().output_tokens,
    )
    return result.output
