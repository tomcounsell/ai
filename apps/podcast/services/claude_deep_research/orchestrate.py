"""Orchestrate multi-agent deep research: plan -> research -> synthesize."""

import logging

from apps.podcast.services.claude_deep_research.planner import (
    ResearchPlan,
    plan_research,
)
from apps.podcast.services.claude_deep_research.researcher import (
    SubagentFindings,
    research_subtask,
)
from apps.podcast.services.claude_deep_research.synthesizer import (
    DeepResearchReport,
    synthesize_findings,
)

logger = logging.getLogger(__name__)


def _format_findings_for_synthesis(
    plan: ResearchPlan,
    findings: list[SubagentFindings],
) -> tuple[str, str]:
    """Format plan and findings into text for the synthesizer.

    Returns:
        Tuple of (plan_summary, findings_text).
    """
    plan_summary = (
        f"Original research plan had {len(plan.subtasks)} subtasks.\n\n"
        f"Synthesis guidance: {plan.synthesis_guidance}"
    )

    sections = []
    for finding in findings:
        section = (
            f"## {finding.focus}\n\n"
            f"**Confidence:** {finding.confidence}\n\n"
            f"{finding.findings}\n\n"
            f"**Key Data Points:**\n"
            + "\n".join(f"- {dp}" for dp in finding.key_data_points)
            + "\n\n**Sources:**\n"
            + "\n".join(f"- {s}" for s in finding.sources)
        )
        if finding.gaps_identified:
            section += "\n\n**Gaps:**\n" + "\n".join(
                f"- {g}" for g in finding.gaps_identified
            )
        sections.append(section)

    findings_text = "\n\n---\n\n".join(sections)
    return plan_summary, findings_text


def deep_research(command: str) -> DeepResearchReport:
    """Replicate claude.ai deep research via multi-agent orchestration.

    Stage 1: Opus plans subtasks
    Stage 2: Sonnet subagents research sequentially
    Stage 3: Opus synthesizes into comprehensive report

    Args:
        command: Free-text research command.

    Returns:
        DeepResearchReport with comprehensive content and sources.

    Raises:
        RuntimeError: If ALL subagents fail.
    """
    logger.info("deep_research: starting planning stage")
    plan = plan_research(command)
    logger.info(
        "deep_research: plan created with %d subtasks",
        len(plan.subtasks),
    )

    findings: list[SubagentFindings] = []
    for i, subtask in enumerate(plan.subtasks, 1):
        logger.info(
            "deep_research: researching subtask %d/%d: %s",
            i,
            len(plan.subtasks),
            subtask.focus[:80],
        )
        try:
            result = research_subtask(
                focus=subtask.focus,
                search_strategy=subtask.search_strategy,
                key_questions=subtask.key_questions,
                allowed_domains=subtask.allowed_domains or None,
            )
            findings.append(result)
        except Exception as exc:
            logger.warning(
                "deep_research: subagent failed for subtask '%s': %s",
                subtask.focus[:50],
                exc,
            )

    if not findings:
        raise RuntimeError("All subagents failed — no findings to synthesize")

    logger.info(
        "deep_research: synthesizing %d findings",
        len(findings),
    )
    plan_summary, findings_text = _format_findings_for_synthesis(plan, findings)
    report = synthesize_findings(plan_summary, findings_text)

    logger.info(
        "deep_research: complete — %d sources, %d key findings",
        len(report.sources_cited),
        len(report.key_findings),
    )
    return report
