"""Deterministic stage detector for SDLC pipeline progression.

Parses agent transcripts to detect which SDLC stages have been invoked,
without relying on the agent LLM to call session_progress.py. This is a
pure function: given transcript text, it returns a list of stage transitions.

Stage detection rules:
- Running a /do-* skill means the previous stage is transitioning
- Explicit stage markers in output are also detected
- The detector is conservative: it only marks stages it's confident about

This replaces the LLM-dependent session_progress.py CLI for stage tracking.
"""

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.skill_outcome import SkillOutcome

logger = logging.getLogger(__name__)

# Maps skill invocations to the stage they represent starting.
# When we see "/do-build" in the transcript, it means BUILD is now in_progress.
SKILL_TO_STAGE: dict[str, str] = {
    "/do-plan": "PLAN",
    "/do-build": "BUILD",
    "/do-test": "TEST",
    "/do-pr-review": "REVIEW",
    "/do-docs": "DOCS",
    "/do-merge": "MERGE",
}

# The ordered pipeline stages
STAGE_ORDER = ["ISSUE", "PLAN", "BUILD", "TEST", "REVIEW", "DOCS", "MERGE"]

# Patterns that indicate a stage has been invoked or completed
_SKILL_INVOCATION_PATTERN = re.compile(
    r"(?:^|\s)(/do-(?:plan|build|test|pr-review|docs|merge))(?:\s|$|['\"])",
    re.MULTILINE,
)

# Patterns for explicit completion markers in transcript.
# REVIEW and DOCS are intentionally excluded — these stages should ONLY be
# marked complete via typed SkillOutcome from /do-pr-review or /do-docs, or
# via skill invocation detection. This prevents false positives from incidental
# mentions like "review complete" in unrelated output.
_COMPLETION_PATTERNS: dict[str, re.Pattern] = {
    "ISSUE": re.compile(
        r"(?:issue\s+(?:created|opened|#\d+))|(?:github\.com/.+/issues/\d+)",
        re.IGNORECASE,
    ),
    "PLAN": re.compile(
        r"(?:plan\s+(?:created|written|finalized|complete))|(?:docs/plans/\S+\.md)",
        re.IGNORECASE,
    ),
    "BUILD": re.compile(
        r"(?:PR\s+(?:created|opened|#\d+))|(?:github\.com/.+/pull/\d+)|"
        r"(?:pushed\s+to\s+(?:origin|remote))",
        re.IGNORECASE,
    ),
    "TEST": re.compile(
        r"(?:\d+\s+passed.*\d+\s+failed|\d+\s+passed\b)|(?:tests?\s+pass(?:ing|ed))",
        re.IGNORECASE,
    ),
    "MERGE": re.compile(
        r"(?:merge\s+(?:complete|approved|authorized))|(?:PR\s+merged)",
        re.IGNORECASE,
    ),
}


def detect_stages(transcript: str) -> list[dict[str, str]]:
    """Parse a transcript to detect SDLC stage transitions.

    This is a pure function with no side effects. It analyzes the transcript
    text and returns a list of stage transitions that should be applied to
    the AgentSession.

    Args:
        transcript: Raw agent transcript text to analyze.

    Returns:
        List of dicts with keys:
        - stage: Stage name (e.g., "BUILD")
        - status: Either "in_progress" or "completed"
        - reason: Why this transition was detected

    Examples:
        >>> detect_stages("Running /do-build docs/plans/my-feature.md")
        [{'stage': 'BUILD', 'status': 'in_progress', 'reason': 'Skill /do-build invoked'}]

        >>> detect_stages("")
        []

        >>> detect_stages("All 42 tests passed, 0 failed")
        [{'stage': 'TEST', 'status': 'completed', 'reason': 'Test results detected'}]
    """
    if not transcript:
        return []

    transitions: list[dict[str, str]] = []
    seen_stages: set[str] = set()

    # Phase 1: Detect skill invocations (strongest signal)
    for match in _SKILL_INVOCATION_PATTERN.finditer(transcript):
        skill = match.group(1)
        stage = SKILL_TO_STAGE.get(skill)
        if stage and stage not in seen_stages:
            transitions.append(
                {
                    "stage": stage,
                    "status": "in_progress",
                    "reason": f"Skill {skill} invoked",
                }
            )
            seen_stages.add(stage)

            # If a later stage is invoked, mark earlier stages as completed
            stage_idx = STAGE_ORDER.index(stage)
            for earlier_stage in STAGE_ORDER[:stage_idx]:
                if earlier_stage not in seen_stages:
                    transitions.append(
                        {
                            "stage": earlier_stage,
                            "status": "completed",
                            "reason": f"Implicitly completed (later stage {stage} started)",
                        }
                    )
                    seen_stages.add(earlier_stage)

    # Phase 2: Detect completion markers (secondary signal)
    for stage, pattern in _COMPLETION_PATTERNS.items():
        if stage not in seen_stages and pattern.search(transcript):
            transitions.append(
                {
                    "stage": stage,
                    "status": "completed",
                    "reason": f"{stage} completion marker detected",
                }
            )
            seen_stages.add(stage)

    # Sort transitions by pipeline order, then by status (in_progress before completed)
    status_order = {"in_progress": 0, "completed": 1}
    transitions.sort(
        key=lambda t: (
            STAGE_ORDER.index(t["stage"]) if t["stage"] in STAGE_ORDER else 99,
            status_order.get(t["status"], 2),
        )
    )

    # Log detection summary
    total_patterns = len(SKILL_TO_STAGE) + len(_COMPLETION_PATTERNS)
    matched_list = [f"{t['stage']}={t['status']}" for t in transitions]
    if transitions:
        logger.info(f"[stage-detector] Checked {total_patterns} patterns, matched: {matched_list}")
    # Log implicit completions specifically
    for t in transitions:
        if "Implicitly completed" in t.get("reason", ""):
            logger.info(
                f"[stage-detector] Implicitly completing {t['stage']} (reason: {t['reason'][:120]})"
            )

    return transitions


def apply_transitions(
    session,
    transitions: list[dict[str, str]],
    outcome: "SkillOutcome | None" = None,
) -> int:
    """Apply detected stage transitions to an AgentSession.

    Writes [stage] history entries to the session, which is how
    get_stage_progress() determines stage status. Only writes entries
    for stages that haven't already been recorded.

    When a typed SkillOutcome is provided, cross-checks it against
    regex-detected transitions. If the outcome says "success" but
    regex didn't detect completion for that stage, the outcome's transition
    is merged into the transitions list. The outcome's artifacts are preferred
    over regex-extracted ones.

    Args:
        session: AgentSession instance (must have append_history method)
        transitions: List of transitions from detect_stages()
        outcome: Optional typed SkillOutcome for cross-checking

    Returns:
        Number of transitions actually applied (skips already-recorded ones)
    """
    if not transitions and not outcome:
        return 0
    if not session:
        return 0

    # Cross-check: if typed outcome exists, verify consistency with regex detections.
    # When a typed outcome reports success but regex missed it, merge the stage
    # into transitions so it gets recorded in session history. This fixes the bug
    # where completed stages render as unchecked in Telegram progress displays.
    if outcome is not None:
        regex_stages = {t["stage"] for t in transitions}
        if outcome.status == "success" and outcome.stage and outcome.stage not in regex_stages:
            logger.info(
                f"[stage-detector] Stage {outcome.stage} merged from typed outcome "
                f"(regex missed). Regex detected: {regex_stages or 'none'}"
            )
            transitions.append(
                {
                    "stage": outcome.stage,
                    "status": "completed",
                    "reason": f"Typed outcome: {outcome.stage} succeeded (regex missed)",
                }
            )
        elif outcome.status == "fail" and outcome.stage in regex_stages:
            # Outcome says fail but regex detected completion — outcome takes priority
            logger.warning(
                f"[stage-detector] Cross-check mismatch: typed outcome says "
                f"{outcome.stage} failed but regex detected it as completing. "
                f"Trusting typed outcome."
            )

    if not transitions:
        return 0

    # Get current progress to avoid duplicate writes
    current_progress = session.get_stage_progress()
    applied = 0

    for t in transitions:
        stage = t["stage"]
        new_status = t["status"]
        current_status = current_progress.get(stage, "pending")

        # Skip if already at or past the proposed status
        if current_status == "completed":
            logger.info(
                f"[stage-detector] Skipping {stage}->{new_status} (current: {current_status})"
            )
            continue
        if current_status == "in_progress" and new_status == "in_progress":
            logger.info(
                f"[stage-detector] Skipping {stage}->{new_status} (current: {current_status})"
            )
            continue
        if current_status == "failed":
            logger.info(
                f"[stage-detector] Skipping {stage}->{new_status} (current: {current_status})"
            )
            continue

        # Write the stage entry
        if new_status == "completed":
            entry = f"{stage} COMPLETED"
        else:
            entry = f"{stage} IN_PROGRESS"

        try:
            session.append_history("stage", entry)
            applied += 1
            logger.info(f"[stage-detector] Applied {stage} -> {new_status}: {t['reason']}")
            # Save checkpoint on stage completion
            if new_status == "completed":
                _save_stage_checkpoint(session, stage)
            # Record telemetry for stage transition
            try:
                from monitoring.telemetry import record_stage_transition

                sid = getattr(session, "session_id", "unknown")
                cid = getattr(session, "correlation_id", None) or "unknown"
                record_stage_transition(sid, cid, stage, current_status, new_status)
            except Exception:
                logger.debug(f"[stage-detector] Telemetry recording failed for {stage}")
        except Exception as e:
            logger.error(f"[stage-detector] Failed to apply {stage} -> {new_status}: {e}")

    return applied


def _save_stage_checkpoint(session, stage: str) -> None:
    """Save checkpoint after stage completion. Only for sessions with work_item_slug."""
    slug = getattr(session, "work_item_slug", None)
    if not slug:
        return

    try:
        from agent.checkpoint import (
            PipelineCheckpoint,
            load_checkpoint,
            record_stage_completion,
            save_checkpoint,
        )

        checkpoint = load_checkpoint(slug) or PipelineCheckpoint(
            session_id=getattr(session, "session_id", "unknown"),
            slug=slug,
        )

        # Extract artifacts from session
        artifacts = {}
        for attr, key in [
            ("issue_url", "issue_url"),
            ("pr_url", "pr_url"),
            ("plan_url", "plan_path"),
            ("branch_name", "branch"),
        ]:
            val = getattr(session, attr, None)
            if val:
                artifacts[key] = str(val)

        record_stage_completion(checkpoint, stage, artifacts=artifacts or None)
        save_checkpoint(checkpoint)
        logger.info(f"[stage-detector] Saved checkpoint for {slug} at stage {stage}")
    except Exception as e:
        logger.warning(f"[stage-detector] Failed to save checkpoint for {slug}: {e}")
