"""TaskTypeProfile model — TRM (Task-Relevant Maturity) registry.

Aggregates per-task-type performance metrics across completed dev sessions
for each project. Powers the PM's delegation style decision: whether to give
a dev session detailed step-by-step SDLC instructions ("structured") or a
lean objective-only handoff ("autonomous").

Based on Andy Grove's Task-Relevant Maturity principle from *High Output
Management*: the right supervision style depends on demonstrated familiarity
with the specific task type, not global skill level.

Key model: TaskTypeProfile
    Keyed by project_key + task_type. Accumulates session_count, avg_turns,
    and rework_rate at each session completion. delegation_recommendation is
    re-derived on every update.

Public API:
    update_task_type_profile(session_id)  — call after finalize_session
    get_delegation_recommendation(project_key, task_type) -> "structured" | "autonomous"

Race condition note:
    Profile updates are eventually-consistent. Two concurrent completions of
    the same task_type may produce a session_count off by 1. Acceptable —
    profiles are advisory metrics, not authoritative records.
"""

import logging
import time

from popoto import (
    AutoKeyField,
    Field,
    FloatField,
    IndexedField,
    IntField,
    KeyField,
    Model,
    SortedField,
)

logger = logging.getLogger(__name__)

# Thresholds for delegation_recommendation derivation.
# "structured" if rework_rate > threshold OR session_count < minimum.
# "autonomous" otherwise.
REWORK_RATE_THRESHOLD = 0.3
SESSION_COUNT_MINIMUM = 5


class TaskTypeProfile(Model):
    """Per-project, per-task-type performance registry.

    Keyed by project_key + task_type. Updated at each session completion via
    update_task_type_profile(). delegation_recommendation is re-derived on
    every update — it is a computed value, not set directly.

    Fields:
        project_key: The project this profile belongs to.
        task_type: Task category string from TASK_TYPE_VOCABULARY.
        session_count: Total completed sessions of this task type.
        avg_turns: Rolling average turn count across completed sessions.
        rework_rate: Fraction of completed sessions with rework_triggered=True.
        failure_stage_distribution: JSON dict counting how often each task_type
            was the last before a failed session (advisory only).
        delegation_recommendation: "structured" or "autonomous" — derived from
            rework_rate and session_count on each update.
        last_updated: Unix timestamp of most recent update.
    """

    id = AutoKeyField()
    project_key = KeyField()
    task_type = KeyField()  # composite key: project_key + task_type
    session_count = IntField(default=0)
    avg_turns = FloatField(default=0.0)
    rework_rate = FloatField(default=0.0)
    failure_stage_distribution = Field(null=True)  # JSON: {"sdlc-build": 2, "sdlc-test": 1}
    delegation_recommendation = IndexedField(default="structured")  # "structured" | "autonomous"
    last_updated = SortedField(type=float, partition_by="project_key")

    class Meta:
        ttl = 7776000  # 90 days — same as AgentSession


def _derive_recommendation(rework_rate: float, session_count: int) -> str:
    """Compute delegation_recommendation from current metrics.

    Returns "structured" if the task type is new or error-prone:
    - session_count < SESSION_COUNT_MINIMUM (not enough data)
    - rework_rate > REWORK_RATE_THRESHOLD (frequently needs rework)

    Returns "autonomous" when the task type is well-proven.

    Args:
        rework_rate: Fraction of sessions with rework_triggered=True (0.0–1.0).
        session_count: Total completed sessions for this task type.

    Returns:
        "structured" or "autonomous"
    """
    if session_count < SESSION_COUNT_MINIMUM:
        return "structured"
    if rework_rate > REWORK_RATE_THRESHOLD:
        return "structured"
    return "autonomous"


def _get_or_create_profile(project_key: str, task_type: str) -> "TaskTypeProfile":
    """Fetch or create a TaskTypeProfile for the given project_key + task_type.

    Args:
        project_key: Project identifier.
        task_type: Task category string.

    Returns:
        Existing or newly-created TaskTypeProfile (not yet saved — caller saves).
    """
    existing = list(TaskTypeProfile.query.filter(project_key=project_key, task_type=task_type))
    if existing:
        return existing[0]

    profile = TaskTypeProfile(
        project_key=project_key,
        task_type=task_type,
        session_count=0,
        avg_turns=0.0,
        rework_rate=0.0,
        delegation_recommendation="structured",
        last_updated=time.time(),
    )
    return profile


def update_task_type_profile(session_id: str) -> None:
    """Update the TaskTypeProfile for the task_type of the given session.

    Reads the session, fetches (or creates) the profile for
    project_key + task_type, re-aggregates metrics incrementally, and saves.

    Only updates profiles for completed sessions. Skips sessions where:
    - session not found
    - task_type is None or empty
    - session status is not "completed"

    This function is called from finalize_session() wrapped in try/except —
    it must never raise. All exceptions are caught and logged at DEBUG level.

    Args:
        session_id: The session_id of the completing session.
    """
    if not session_id:
        logger.debug("[trm] update_task_type_profile: empty session_id, skipping")
        return

    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            logger.debug(f"[trm] update_task_type_profile: session {session_id} not found")
            return
        session = sessions[0]
    except Exception as e:
        logger.debug(f"[trm] update_task_type_profile: session lookup failed: {e}")
        return

    # Only update for completed sessions
    if getattr(session, "status", None) != "completed":
        logger.debug(
            f"[trm] update_task_type_profile: session {session_id} "
            f"status={getattr(session, 'status', None)} — skipping (not completed)"
        )
        return

    task_type = getattr(session, "task_type", None)
    if not task_type:
        logger.debug(
            f"[trm] update_task_type_profile: session {session_id} has no task_type, skipping"
        )
        return

    project_key = getattr(session, "project_key", None) or "unknown"

    try:
        profile = _get_or_create_profile(project_key, task_type)

        # Incremental update: re-compute rolling average and rework_rate
        old_count = profile.session_count or 0
        new_count = old_count + 1

        # Rolling avg turns: (old_avg * old_count + new_turns) / new_count
        turn_count = getattr(session, "turn_count", None) or 0
        old_avg = profile.avg_turns or 0.0
        new_avg = (old_avg * old_count + turn_count) / new_count

        # Rolling rework_rate: (old_rate * old_count + rework_flag) / new_count
        rework_str = getattr(session, "rework_triggered", None)
        rework_flag = 1 if str(rework_str).lower() == "true" else 0
        old_rate = profile.rework_rate or 0.0
        new_rate = (old_rate * old_count + rework_flag) / new_count

        profile.session_count = new_count
        profile.avg_turns = new_avg
        profile.rework_rate = new_rate
        profile.delegation_recommendation = _derive_recommendation(new_rate, new_count)
        profile.last_updated = time.time()
        profile.save()

        logger.debug(
            f"[trm] TaskTypeProfile updated: project={project_key} task_type={task_type} "
            f"count={new_count} avg_turns={new_avg:.1f} rework_rate={new_rate:.2f} "
            f"recommendation={profile.delegation_recommendation}"
        )
    except Exception as e:
        logger.debug(f"[trm] TaskTypeProfile update failed (non-fatal): {e}")


def get_delegation_recommendation(project_key: str | None, task_type: str | None) -> str:
    """Look up the delegation recommendation for a project + task type.

    Returns "structured" (safe default) if:
    - project_key or task_type is None/empty
    - no profile exists for this combination
    - any lookup error occurs

    Returns "autonomous" only when an established profile with low rework_rate
    and sufficient session_count exists.

    Args:
        project_key: Project identifier.
        task_type: Task category string.

    Returns:
        "structured" or "autonomous" — never raises.
    """
    if not project_key or not task_type:
        logger.debug(
            "[trm] get_delegation_recommendation: missing project_key or task_type → structured"
        )
        return "structured"

    try:
        existing = list(TaskTypeProfile.query.filter(project_key=project_key, task_type=task_type))
        if not existing:
            logger.debug(
                f"[trm] No profile for {project_key}/{task_type} → structured (new task type)"
            )
            return "structured"

        profile = existing[0]
        recommendation = getattr(profile, "delegation_recommendation", "structured") or "structured"
        logger.debug(
            f"[trm] Delegation recommendation for {project_key}/{task_type}: {recommendation}"
        )
        return recommendation
    except Exception as e:
        logger.debug(f"[trm] get_delegation_recommendation failed (safe default): {e}")
        return "structured"
