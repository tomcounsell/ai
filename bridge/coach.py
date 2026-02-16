"""Coaching message builder for context-aware auto-continue.

Generates targeted coaching messages instead of bare "continue" when
the classifier rejects a completion or when a skill/plan is active.

Coaching tiers (in priority order):
1. Rejection coaching - agent's completion was rejected (hedging/no evidence)
2. Skill-aware coaching - a /do-* skill is active with plan success criteria
3. Plain continue - fallback for genuine status updates
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def build_coaching_message(
    classification,
    plan_file: str | None = None,
    job_message_text: str | None = None,
) -> str:
    """Build a context-aware coaching message for auto-continue.

    Args:
        classification: ClassificationResult from the summarizer.
        plan_file: Path to the active plan document, if any.
        job_message_text: Original message text that triggered the job.

    Returns:
        Coaching message string to send as the continuation prompt.
    """
    # Tier 1: Rejection coaching
    if getattr(classification, "was_rejected_completion", False):
        return _build_rejection_coaching()

    # Tier 2: Skill-aware coaching (if a plan is active)
    if plan_file:
        criteria = _extract_success_criteria(plan_file)
        if criteria:
            return _build_skill_coaching(criteria)

    # Tier 2b: Detect skill from message text
    if job_message_text and _detect_skill(job_message_text):
        return _build_generic_skill_coaching()

    # Tier 3: Plain continue
    return "continue"


def _build_rejection_coaching() -> str:
    """Build coaching for a rejected completion."""
    return (
        "[System Coach] Your previous output was classified as a status update, "
        "not a completion. To complete successfully, include concrete evidence: "
        "test output with pass/fail counts, command exit codes, commit hashes, "
        "or specific file paths confirmed to exist. "
        "Avoid hedging language like 'should work', 'probably', or 'I think'."
    )


def _build_skill_coaching(criteria: str) -> str:
    """Build coaching that references plan success criteria."""
    if len(criteria) > 500:
        criteria = criteria[:500] + "..."
    return (
        f"[System Coach] You are working on a plan with these success criteria:\n"
        f"{criteria}\n"
        f"Review these criteria and provide evidence of completion for each item. "
        f"Include test output, commit hashes, or other concrete proof."
    )


def _build_generic_skill_coaching() -> str:
    """Build coaching when a skill is detected but no plan file is available."""
    return (
        "[System Coach] You are executing a skill. Continue working toward "
        "completion. When done, provide concrete evidence: test results, "
        "commit hashes, file paths, or command output."
    )


def _extract_success_criteria(plan_file: str) -> str | None:
    """Extract the ## Success Criteria section from a plan document.

    Uses simple regex to find the section. Returns None if the file
    doesn't exist or the section is missing.
    """
    try:
        path = Path(plan_file)
        if not path.exists():
            logger.debug(f"Plan file not found: {plan_file}")
            return None

        content = path.read_text()
        match = re.search(
            r"## Success Criteria\s*\n(.*?)(?=\n## |\Z)",
            content,
            re.DOTALL,
        )
        if match:
            criteria = match.group(1).strip()
            return criteria if criteria else None
        return None
    except Exception as e:
        logger.debug(f"Failed to read plan success criteria: {e}")
        return None


def _detect_skill(message_text: str) -> bool:
    """Detect if a /do-* skill was invoked in the message text."""
    skill_patterns = ["/do-plan", "/do-build", "/do-test", "/do-docs"]
    return any(p in message_text for p in skill_patterns)
