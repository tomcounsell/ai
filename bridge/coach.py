"""Coaching message builder for context-aware auto-continue.

Generates targeted coaching messages instead of bare "continue" when
the classifier rejects a completion or when a skill/plan is active.

Philosophy: The coach is here to help, not to be a supervisor. When
uncertain about context, degrade gracefully to plain "continue" rather
than risk misdirecting the agent. It is better to say little or nothing
than to accidentally coach in the wrong direction.

Coaching tiers (in priority order):
1. LLM coaching - classifier provided a coaching_message (preferred)
1b. Heuristic rejection coaching - static fallback when no LLM coaching available
1c. SDLC stage progress coaching - explicit next-stage instruction for SDLC pipelines
2. Skill-aware coaching - a /do-* skill is active with plan success criteria
3. Plain continue - fallback for genuine status updates

Tone: Explanatory and supportive. Tell the agent what it needs to
confirm next time it stops — don't bark commands at it.
"""

import logging
import re
from pathlib import Path

from bridge.pipeline_graph import DISPLAY_STAGES, STAGE_TO_SKILL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Skill detection heuristics
#
# Currently supports four SDLC skills. Each entry maps a detection pattern
# to metadata about the skill — what workflow phase it corresponds to and
# what kind of evidence the coach should ask for.
#
# Detection works two ways (OR logic):
#   1. Message text contains the skill's trigger pattern (e.g. "/do-build")
#   2. AgentSession.current_stage matches the skill's phase name
#
# To add a future skill:
#   1. Add an entry to SKILL_DETECTORS with its trigger and phase
#   2. Optionally add a skill-specific coaching template
#   3. The coach will automatically pick it up — no other wiring needed
# ---------------------------------------------------------------------------
SKILL_DETECTORS: dict[str, dict] = {
    "/do-plan": {
        "phase": "plan",
        "description": "Creating a structured plan document",
        "evidence_hint": "a finalized plan doc with all required sections filled in",
    },
    "/do-build": {
        "phase": "build",
        "description": "Implementing a plan with code changes",
        "evidence_hint": "passing tests, commit hashes, and a PR link",
    },
    "/do-test": {
        "phase": "test",
        "description": "Running test suites and validating quality",
        "evidence_hint": "test output with pass/fail counts and coverage numbers",
    },
    "/do-docs": {
        "phase": "document",
        "description": "Creating or updating documentation",
        "evidence_hint": "created/updated doc file paths and an index entry",
    },
}


def build_coaching_message(
    classification,
    plan_file: str | None = None,
    job_message_text: str | None = None,
    sdlc_stage_progress: dict | None = None,
) -> str:
    """Build a context-aware coaching message for auto-continue.

    The coach produces explanatory, supportive messages that tell the agent
    what it needs to confirm next time it pauses. When uncertain about
    context, it falls back to plain "continue" rather than risk giving
    wrong guidance.

    Args:
        classification: ClassificationResult from the summarizer.
        plan_file: Path to the active plan document, if any.
        job_message_text: Original message text that triggered the job.
        sdlc_stage_progress: Dict mapping stage names to statuses from
            AgentSession.get_stage_progress(). When provided with remaining
            stages, triggers Tier 1c SDLC pipeline coaching.

    Returns:
        Coaching message string to send as the continuation prompt.

    Examples:
        >>> # Rejected completion → specific coaching
        >>> build_coaching_message(rejected_classification)
        '[System Coach] Your previous output looked like a completion, but ...'

        >>> # Active plan with criteria → quote criteria
        >>> build_coaching_message(status, plan_file='docs/plans/foo.md')
        '[System Coach] You are working through a plan. ...'

        >>> # SDLC progress with remaining stages -> explicit next-step
        >>> progress = {"PLAN": "completed", "BUILD": "pending"}
        >>> build_coaching_message(status, sdlc_stage_progress=progress)
        '[System Coach] The SDLC pipeline has completed: PLAN. ...'

        >>> # No context → plain continue
        >>> build_coaching_message(status)
        'continue'
    """
    # Tier 1: LLM-generated coaching — highest priority
    coaching_msg = getattr(classification, "coaching_message", None)
    if coaching_msg and coaching_msg.strip():
        return f"[System Coach] {coaching_msg.strip()}"

    # Tier 1b: Heuristic rejection coaching — fallback when LLM coaching absent
    if getattr(classification, "was_rejected_completion", False):
        return _build_heuristic_rejection_coaching()

    # Tier 1c: SDLC stage progress coaching — explicit next-stage instruction
    if sdlc_stage_progress:
        sdlc_msg = _build_sdlc_stage_coaching(sdlc_stage_progress)
        if sdlc_msg:
            return sdlc_msg

    # Tier 2: Skill-aware coaching (plan file with extractable criteria)
    if plan_file and Path(plan_file).exists():
        criteria = _extract_success_criteria(plan_file)
        if criteria:
            return _build_skill_coaching_with_criteria(criteria)
        # Plan file exists but couldn't parse criteria cleanly —
        # point to the file rather than guessing at content
        return _build_skill_coaching_with_file_pointer(plan_file)

    # Tier 2b: Detect skill from message text (no plan file available)
    detected = _detect_active_skill(job_message_text)
    if detected:
        return _build_generic_skill_coaching(detected)

    # Tier 3: Plain continue — no context to coach on
    return "continue"


def _build_sdlc_stage_coaching(stage_progress: dict) -> str | None:
    """Build SDLC pipeline coaching with explicit next-stage instruction.

    Parses the stage progress dict to determine which stages are done and
    which stage should be invoked next. Returns None if there are no
    remaining stages (all completed or all pending with no progress),
    allowing the caller to fall through to lower-priority coaching tiers.

    Args:
        stage_progress: Dict mapping stage names (e.g. "PLAN", "BUILD")
            to statuses ("completed", "in_progress", "pending", "failed").

    Returns:
        Coaching message string with explicit next-step instruction,
        or None if no actionable SDLC coaching can be produced.
    """
    if not stage_progress:
        return None

    completed = [s for s in DISPLAY_STAGES if stage_progress.get(s) == "completed"]
    in_progress = [s for s in DISPLAY_STAGES if stage_progress.get(s) == "in_progress"]

    # If nothing is completed and nothing is in progress, no useful coaching
    if not completed and not in_progress:
        return None

    # Find the next stage to invoke: first pending stage in pipeline order
    next_stage = None
    for stage in DISPLAY_STAGES:
        if stage_progress.get(stage) == "pending":
            next_stage = stage
            break

    # If no pending stages remain, all stages are done or in progress
    if next_stage is None:
        return None

    # Build the coaching message
    skill_name = STAGE_TO_SKILL.get(next_stage)
    if not skill_name:
        # ISSUE stage has no corresponding skill — skip to the next pending
        for stage in DISPLAY_STAGES:
            if stage_progress.get(stage) == "pending" and stage in STAGE_TO_SKILL:
                next_stage = stage
                skill_name = STAGE_TO_SKILL[next_stage]
                break
        if not skill_name:
            return None

    completed_str = ", ".join(completed) if completed else "none yet"
    in_progress_str = ", ".join(in_progress) if in_progress else ""

    parts = [
        f"[System Coach] The SDLC pipeline has completed: {completed_str}.",
    ]
    if in_progress_str:
        parts.append(f" In progress: {in_progress_str}.")
    parts.append(
        f" The next stage is {next_stage}. "
        f"Return to the SDLC pipeline and invoke `{skill_name}` to continue. "
        f"Do NOT investigate logs, check system status, or start other work — "
        f"proceed directly to `{skill_name}`."
    )

    return "".join(parts)


def _build_heuristic_rejection_coaching() -> str:
    """Build static coaching for a rejected completion (heuristic fallback).

    Used when the LLM classifier did not provide a coaching_message,
    e.g. when classification fell back to heuristics. Provides generic
    but helpful guidance about what evidence to include.

    Tone: supportive and instructive. Explain what happened and what
    the agent should include next time it believes work is done.
    """
    return (
        "[System Coach] Your previous output looked like a completion, but "
        "it wasn't accepted because it lacked verification evidence. "
        "Next time you're ready to report completion, include concrete proof: "
        "test output with pass/fail counts, command exit codes, commit hashes, "
        "or file paths you've confirmed exist. "
        "Phrases like 'should work', 'probably', or 'I think' signal uncertainty — "
        "run the verification commands and share the actual output instead."
    )


def _build_skill_coaching_with_criteria(criteria: str) -> str:
    """Build coaching that quotes plan success criteria verbatim.

    Only called when we successfully extracted criteria from the plan file.
    Truncates at 500 chars to keep messages reasonable.
    """
    if len(criteria) > 500:
        criteria = criteria[:500] + "\n..."
    return (
        "[System Coach] You are working through a plan. "
        "Here are the success criteria to confirm before completing:\n"
        f"{criteria}\n\n"
        "When you're ready to wrap up, confirm which of these are done "
        "and include the evidence (test output, commits, file paths)."
    )


def _build_skill_coaching_with_file_pointer(plan_file: str) -> str:
    """Build coaching that points to the plan file without guessing content.

    Used when we know a plan file exists but couldn't cleanly parse the
    success criteria section. Better to point the agent to the file than
    to hallucinate what the criteria might be.
    """
    return (
        "[System Coach] You are working through a plan. "
        f"Check the success criteria in `{plan_file}` to confirm what's "
        "left to do before completing. Include concrete evidence for "
        "each criterion when you're ready to wrap up."
    )


def _build_generic_skill_coaching(skill_info: dict) -> str:
    """Build coaching when a skill is detected but no plan file is available.

    Uses the skill's evidence_hint to give relevant guidance without
    guessing at specific criteria.
    """
    evidence_hint = skill_info.get("evidence_hint", "concrete evidence of completion")
    description = skill_info.get("description", "a development task")
    return (
        f"[System Coach] You are {description}. "
        f"When you're ready to wrap up, confirm completion with "
        f"{evidence_hint}."
    )


def _extract_success_criteria(plan_file: str) -> str | None:
    """Extract the ## Success Criteria section from a plan document.

    Uses simple regex to find the section between the heading and the
    next ## heading (or end of file). Returns None if the file doesn't
    exist, the section is missing, or extraction fails for any reason.

    Returns the raw section content only when parsed with certainty.
    Never guesses or approximates — it's better to return None and let
    the caller fall back to a file pointer than to quote wrong content.
    """
    try:
        path = Path(plan_file)
        if not path.is_absolute():
            # Try relative to the working directory
            if not path.exists():
                logger.debug(f"Plan file not found: {plan_file}")
                return None

        if not path.exists():
            logger.debug(f"Plan file not found: {plan_file}")
            return None

        content = path.read_text()
        match = re.search(
            r"^## Success Criteria\s*\n(.*?)(?=^## |\Z)",
            content,
            re.DOTALL | re.MULTILINE,
        )
        if match:
            criteria = match.group(1).strip()
            return criteria if criteria else None
        return None
    except Exception as e:
        logger.debug(f"Failed to read plan success criteria: {e}")
        return None


def _detect_active_skill(message_text: str | None) -> dict | None:
    """Detect if an SDLC skill was invoked in the message text.

    Checks message text against SKILL_DETECTORS patterns. Returns the
    skill's metadata dict if found, None otherwise.

    Only matches the four SDLC skills (/do-plan, /do-build, /do-test,
    /do-docs). Non-SDLC messages (general chat, Q&A, exploration)
    return None and the coach falls back to plain "continue".
    """
    if not message_text:
        return None

    for trigger, info in SKILL_DETECTORS.items():
        if trigger in message_text:
            return info

    return None


def detect_skill_from_phase(phase: str | None) -> dict | None:
    """Detect active skill from workflow phase name.

    Called when session phase is available. Maps phase names back to
    skill metadata for coaching purposes.

    Args:
        phase: Workflow phase string (e.g. "plan", "build", "test", "document")

    Returns:
        Skill metadata dict if phase matches a known skill, None otherwise.
    """
    if not phase:
        return None

    for _trigger, info in SKILL_DETECTORS.items():
        if info["phase"] == phase:
            return info

    return None
