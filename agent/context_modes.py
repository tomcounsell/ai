"""Context fidelity modes for sub-agent steering.

Defines four context compression modes (full, compact, minimal, steering)
and builder functions that produce right-sized context strings for each
SDLC sub-skill. Skills declare their fidelity requirement via the
SKILL_FIDELITY registry; the dispatcher calls get_context_for_skill()
to build the appropriate context before invoking a sub-agent.

See: https://github.com/tomcounsell/ai/issues/329
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class ContextFidelity(enum.Enum):
    """Context compression level for sub-agent dispatch.

    Each mode controls how much prior session state is forwarded
    to a sub-agent when the SDLC dispatcher invokes a skill.
    """

    FULL = "full"
    COMPACT = "compact"
    MINIMAL = "minimal"
    STEERING = "steering"


@dataclass
class ContextRequest:
    """Input data for context builders.

    All fields are optional — builders gracefully degrade when fields
    are absent, producing shorter (or empty) output.

    Attributes:
        plan_path: Path to the plan document (relative to repo root).
        task_description: Specific task text for minimal mode.
        current_stage: Current SDLC stage name (e.g. "BUILD").
        completed_stages: List of previously completed stage names.
        artifacts: Key outputs from prior stages (branch, PR URL, etc.).
        recent_messages: Last N human messages for steering context.
        session_transcript: Full session transcript for full mode.
    """

    plan_path: str | None = None
    task_description: str | None = None
    current_stage: str | None = None
    completed_stages: list[str] | None = None
    artifacts: dict[str, str] | None = None
    recent_messages: list[str] | None = None
    session_transcript: str | None = None


# ---------------------------------------------------------------------------
# Builder functions
# ---------------------------------------------------------------------------


def build_full_context(request: ContextRequest) -> str:
    """Build full context — complete session transcript.

    Use case: resuming within the same skill after interruption.
    Returns the full transcript with a header, or empty string if
    no transcript is available.
    """
    if not request.session_transcript:
        return ""

    return f"## Full Session Context\n\n{request.session_transcript}"


def build_compact_context(request: ContextRequest) -> str:
    """Build compact context — structured summary for stage handoffs.

    Use case: passing context between SDLC stages (e.g. BUILD -> TEST).
    Includes plan reference, completed stages, artifacts, and current stage.
    Approximate target: ~800 tokens.
    """
    parts: list[str] = ["## Pipeline Context"]

    if request.current_stage:
        parts.append(f"\nCurrent stage: {request.current_stage}")

    if request.completed_stages:
        stages_str = ", ".join(request.completed_stages)
        parts.append(f"Completed stages: {stages_str}")

    if request.plan_path:
        parts.append(f"Plan: {request.plan_path}")

    if request.artifacts:
        parts.append("\nArtifacts:")
        for key, value in request.artifacts.items():
            parts.append(f"  - {key}: {value}")

    return "\n".join(parts)


def build_minimal_context(request: ContextRequest) -> str:
    """Build minimal context — just the task and essential references.

    Use case: individual builder sub-agents that only need their
    specific task description. Returns empty string if no task is provided.
    Approximate target: ~200 tokens.
    """
    if not request.task_description:
        return ""

    parts: list[str] = [f"## Task\n\n{request.task_description}"]

    if request.artifacts:
        parts.append("\nReferences:")
        for key, value in request.artifacts.items():
            parts.append(f"  - {key}: {value}")

    return "\n".join(parts)


def build_steering_context(request: ContextRequest) -> str:
    """Build steering context — minimal state for observer coaching.

    Use case: observer/coaching messages that need to know what stage
    the pipeline is in, what is done, and any recent human messages.
    Approximate target: ~300 tokens.
    """
    parts: list[str] = ["## Steering Context"]

    if request.current_stage:
        parts.append(f"\nActive stage: {request.current_stage}")

    if request.completed_stages:
        parts.append(f"Done: {', '.join(request.completed_stages)}")

    if request.recent_messages:
        parts.append("\nRecent human messages:")
        for msg in request.recent_messages:
            parts.append(f"  > {msg}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Skill fidelity registry
# ---------------------------------------------------------------------------

SKILL_FIDELITY: dict[str, ContextFidelity] = {
    "do-plan": ContextFidelity.COMPACT,
    "do-build": ContextFidelity.COMPACT,
    "do-test": ContextFidelity.COMPACT,
    "do-patch": ContextFidelity.COMPACT,
    "do-pr-review": ContextFidelity.COMPACT,
    "do-docs": ContextFidelity.COMPACT,
    "builder": ContextFidelity.MINIMAL,
}

# Maps fidelity enum values to their builder functions.
_FIDELITY_BUILDERS = {
    ContextFidelity.FULL: build_full_context,
    ContextFidelity.COMPACT: build_compact_context,
    ContextFidelity.MINIMAL: build_minimal_context,
    ContextFidelity.STEERING: build_steering_context,
}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def get_context_for_skill(
    skill_name: str,
    request: ContextRequest,
) -> str:
    """Build context string for a skill based on its declared fidelity.

    Looks up the skill's fidelity requirement in SKILL_FIDELITY (defaults
    to COMPACT for unknown skills) and calls the corresponding builder.

    Args:
        skill_name: Name of the skill (e.g. "do-build", "builder").
        request: ContextRequest populated with available session state.

    Returns:
        Formatted context string sized to the skill's fidelity level.
    """
    fidelity = SKILL_FIDELITY.get(skill_name, ContextFidelity.COMPACT)
    builder = _FIDELITY_BUILDERS[fidelity]
    result = builder(request)
    logger.debug(
        "Built %s context for skill %r: %d chars",
        fidelity.value,
        skill_name,
        len(result),
    )
    return result
