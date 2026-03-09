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

logger = logging.getLogger(__name__)

# Maps skill invocations to the stage they represent starting.
# When we see "/do-build" in the transcript, it means BUILD is now in_progress.
SKILL_TO_STAGE: dict[str, str] = {
    "/do-plan": "PLAN",
    "/do-build": "BUILD",
    "/do-test": "TEST",
    "/do-pr-review": "REVIEW",
    "/do-docs": "DOCS",
}

# The ordered pipeline stages
STAGE_ORDER = ["ISSUE", "PLAN", "BUILD", "TEST", "REVIEW", "DOCS"]

# Patterns that indicate a stage has been invoked or completed
_SKILL_INVOCATION_PATTERN = re.compile(
    r"(?:^|\s)(/do-(?:plan|build|test|pr-review|docs))(?:\s|$|['\"])",
    re.MULTILINE,
)

# Patterns for explicit completion markers in transcript
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
    "REVIEW": re.compile(
        r"(?:review\s+(?:passed|approved|complete))|(?:pr-review\s+complete)",
        re.IGNORECASE,
    ),
    "DOCS": re.compile(
        r"(?:documentation?\s+(?:created|updated|complete))|"
        r"(?:docs/features/\S+\.md\s+created)",
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

    return transitions


def apply_transitions(session, transitions: list[dict[str, str]]) -> int:
    """Apply detected stage transitions to an AgentSession.

    Writes [stage] history entries to the session, which is how
    get_stage_progress() determines stage status. Only writes entries
    for stages that haven't already been recorded.

    Args:
        session: AgentSession instance (must have append_history method)
        transitions: List of transitions from detect_stages()

    Returns:
        Number of transitions actually applied (skips already-recorded ones)
    """
    if not transitions or not session:
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
            continue
        if current_status == "in_progress" and new_status == "in_progress":
            continue
        if current_status == "failed":
            continue

        # Write the stage entry
        if new_status == "completed":
            entry = f"{stage} COMPLETED"
        else:
            entry = f"{stage} IN_PROGRESS"

        try:
            session.append_history("stage", entry)
            applied += 1
            logger.info(
                f"[stage-detector] Applied {stage} -> {new_status}: {t['reason']}"
            )
        except Exception as e:
            logger.error(
                f"[stage-detector] Failed to apply {stage} -> {new_status}: {e}"
            )

    return applied
