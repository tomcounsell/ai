"""Canonical pipeline graph for SDLC stage routing.

This module defines the single source of truth for pipeline stage transitions.
All routing code (SDLC skill, stage detector display) derives from
this graph. The graph supports cycles for test-failure and review-feedback loops.

Graph structure:
- Nodes are pipeline stages (ISSUE, PLAN, CRITIQUE, BUILD, TEST, PATCH, REVIEW, DOCS, MERGE)
- Edges are (stage, outcome) -> next_stage transitions
- PATCH is a routing-only stage -- it does NOT appear in display/progress templates
- The happy path is: ISSUE -> PLAN -> CRITIQUE -> BUILD -> TEST -> REVIEW -> DOCS -> MERGE
- Cycles: TEST(fail) -> PATCH -> TEST, REVIEW(fail) -> PATCH -> TEST -> REVIEW
- Cycles: CRITIQUE(fail) -> PLAN -> CRITIQUE (max MAX_CRITIQUE_CYCLES)

Usage:
    from bridge.pipeline_graph import get_next_stage, STAGE_TO_SKILL, DISPLAY_STAGES

    next_info = get_next_stage("TEST", "fail")
    # Returns ("PATCH", "/do-patch")

    next_info = get_next_stage("TEST", "success")
    # Returns ("REVIEW", "/do-pr-review")
"""

import logging

logger = logging.getLogger(__name__)

# Maximum number of PATCH -> TEST cycles before escalating to human.
# Prevents infinite loops when fixes don't converge.
MAX_PATCH_CYCLES = 3

# Maximum number of CRITIQUE -> PLAN cycles before escalating to human.
# Prevents infinite plan revision loops when critique findings don't converge.
MAX_CRITIQUE_CYCLES = 2

# Canonical directed graph: (current_stage, outcome) -> next_stage
# "success" = stage completed successfully, move forward
# "fail" = stage failed, enter patch cycle
PIPELINE_EDGES: dict[tuple[str, str], str] = {
    # Happy path (outcome = "success")
    ("ISSUE", "success"): "PLAN",
    ("PLAN", "success"): "CRITIQUE",
    ("CRITIQUE", "success"): "BUILD",
    ("BUILD", "success"): "TEST",
    ("TEST", "success"): "REVIEW",
    ("REVIEW", "success"): "DOCS",
    ("DOCS", "success"): "MERGE",
    # Critique failure routes back to PLAN for revision
    ("CRITIQUE", "fail"): "PLAN",
    # Partial: review approved but has tech_debt/nits that need patching
    ("REVIEW", "partial"): "PATCH",
    # Failure cycles (outcome = "fail")
    ("TEST", "fail"): "PATCH",
    ("REVIEW", "fail"): "PATCH",
    # PATCH always routes back to TEST (re-verify the fix)
    ("PATCH", "success"): "TEST",
    ("PATCH", "fail"): "TEST",
}

# Maps stages to their corresponding /do-* skill commands
STAGE_TO_SKILL: dict[str, str] = {
    "ISSUE": "/do-issue",
    "PLAN": "/do-plan",
    "CRITIQUE": "/do-plan-critique",
    "BUILD": "/do-build",
    "TEST": "/do-test",
    "PATCH": "/do-patch",
    "REVIEW": "/do-pr-review",
    "DOCS": "/do-docs",
    "MERGE": "/do-merge",
}

# Display-only linear stage list for progress templates and PM-facing messages.
# PATCH is intentionally excluded -- it's a routing concept, not a display stage.
# Used by PipelineStateMachine.get_display_progress() and bridge/coach.py.
DISPLAY_STAGES: list[str] = [
    "ISSUE",
    "PLAN",
    "CRITIQUE",
    "BUILD",
    "TEST",
    "REVIEW",
    "DOCS",
    "MERGE",
]


def get_next_stage(
    current_stage: str | None,
    outcome: str | None = "success",
    cycle_count: int = 0,
    critique_cycle_count: int = 0,
) -> tuple[str, str] | None:
    """Determine the next pipeline stage based on current stage and outcome.

    Uses the canonical PIPELINE_EDGES graph to resolve transitions. Supports
    cycles (TEST -> PATCH -> TEST, CRITIQUE -> PLAN -> CRITIQUE) with
    max-cycle counters to prevent infinite loops.

    Args:
        current_stage: The stage that just completed (e.g., "TEST", "BUILD").
            If None, returns the first stage (ISSUE).
        outcome: The result of the current stage. Defaults to "success".
            Common values: "success", "fail", "partial", "blocked".
            Unknown outcomes default to "success" behavior.
        cycle_count: Number of PATCH -> TEST cycles already completed in this
            session. When this reaches MAX_PATCH_CYCLES, returns None to
            escalate to human review.
        critique_cycle_count: Number of CRITIQUE -> PLAN -> CRITIQUE cycles
            already completed. When this reaches MAX_CRITIQUE_CYCLES, returns
            None to escalate to human review.

    Returns:
        Tuple of (stage_name, skill_command) for the next stage, or None if:
        - All stages are complete (MERGE is terminal)
        - Max cycle count reached (escalate to human)
        - Unknown current_stage with no matching edge
    """
    if current_stage is None:
        return ("ISSUE", STAGE_TO_SKILL["ISSUE"])

    if outcome is None:
        outcome = "success"

    # Check cycle limit for CRITIQUE stages (CRITIQUE -> PLAN -> CRITIQUE loop)
    if (
        current_stage == "CRITIQUE"
        and outcome == "fail"
        and critique_cycle_count >= MAX_CRITIQUE_CYCLES
    ):
        logger.warning(
            f"Max critique cycle limit reached ({critique_cycle_count}/{MAX_CRITIQUE_CYCLES}). "
            f"Escalating to human review."
        )
        return None

    # Check cycle limit for PATCH stages
    if current_stage == "PATCH" and cycle_count >= MAX_PATCH_CYCLES:
        logger.warning(
            f"Max patch cycle limit reached ({cycle_count}/{MAX_PATCH_CYCLES}). "
            f"Escalating to human review."
        )
        return None

    # Look up the edge in the graph
    next_stage = PIPELINE_EDGES.get((current_stage, outcome))

    # If no edge for this outcome, fall back to "success" outcome
    if next_stage is None and outcome != "success":
        next_stage = PIPELINE_EDGES.get((current_stage, "success"))

    if next_stage is None:
        logger.info(
            f"No transition found for ({current_stage}, {outcome}). "
            f"Pipeline may be complete or stage is unknown."
        )
        return None

    skill = STAGE_TO_SKILL.get(next_stage)
    if skill is None:
        logger.warning(f"No skill mapped for stage {next_stage}")
        return None

    return (next_stage, skill)
