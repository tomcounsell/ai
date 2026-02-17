"""Stage 3: Opus synthesizer that merges subagent findings into a comprehensive report."""

import logging
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent

logger = logging.getLogger(__name__)


# --- Output schema ---


class DeepResearchReport(BaseModel):
    content: str  # full research report (markdown, 3000-6000 words)
    sources_cited: list[str]  # all URLs from all subagents
    key_findings: list[str]  # top-level findings
    confidence_assessment: str  # overall research quality
    gaps_remaining: list[str]  # what wasn't covered


# --- Agent ---

_PROMPT_FILE = Path(__file__).parent / "prompts" / "synthesizer.md"
_SYSTEM_PROMPT = _PROMPT_FILE.read_text()

synthesizer_agent = Agent(
    "anthropic:claude-opus-4-6",
    output_type=DeepResearchReport,
    system_prompt=_SYSTEM_PROMPT,
    defer_model_check=True,
)


# --- Public interface ---


def synthesize_findings(
    plan_summary: str,
    findings_text: str,
) -> DeepResearchReport:
    """Synthesize all subagent findings into a comprehensive research report.

    Args:
        plan_summary: Summary of the original research plan and synthesis guidance.
        findings_text: Formatted text containing all subagent findings.

    Returns:
        DeepResearchReport with comprehensive content, sources, and assessment.
    """
    prompt = (
        f"Research Plan Summary:\n{plan_summary}\n\n"
        f"Subagent Findings:\n{findings_text}"
    )
    result = synthesizer_agent.run_sync(prompt)
    logger.info(
        "synthesize_findings: model=%s input_tokens=%d output_tokens=%d",
        synthesizer_agent.model,
        result.usage().input_tokens,
        result.usage().output_tokens,
    )
    return result.output
