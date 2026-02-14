"""Workflow state management service for podcast episode production.

Provides functions to query, advance, pause, resume, and fail the
12-step EpisodeWorkflow pipeline.  All functions are synchronous and
operate on the EpisodeWorkflow model via ``episode_id``.
"""

from __future__ import annotations

from django.utils import timezone

from apps.podcast.models import Episode, EpisodeArtifact, EpisodeWorkflow

WORKFLOW_STEPS = [
    "Setup",
    "Perplexity Research",
    "Question Discovery",
    "Targeted Research",
    "Cross-Validation",
    "Master Briefing",
    "Synthesis",
    "Episode Planning",
    "Audio Generation",
    "Audio Processing",
    "Publishing Assets",
    "Publish",
]

# Minimum word count to consider an artifact "substantial"
_MIN_ARTIFACT_WORDS = 200


def _now_iso() -> str:
    """Return the current time as an ISO-8601 string."""
    return timezone.now().isoformat()


def _make_history_entry(
    step: str,
    status: str = "started",
    error: str | None = None,
) -> dict:
    """Build a history dict for a workflow step transition."""
    return {
        "step": step,
        "status": status,
        "started_at": _now_iso(),
        "completed_at": None,
        "error": error,
    }


def _next_step(current: str) -> str | None:
    """Return the step after *current*, or ``None`` if at the end."""
    try:
        idx = WORKFLOW_STEPS.index(current)
    except ValueError:
        return None
    if idx + 1 < len(WORKFLOW_STEPS):
        return WORKFLOW_STEPS[idx + 1]
    return None


def _completed_steps(history: list[dict]) -> list[str]:
    """Extract the names of all steps whose history entry is completed."""
    return [
        entry["step"]
        for entry in history
        if entry.get("status") == "completed" and entry.get("completed_at")
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_status(episode_id: int) -> dict:
    """Return current workflow state for an episode.

    Returns a dict with keys: ``current_step``, ``status``, ``blocked_on``,
    ``completed_steps``, ``next_step``, ``history``.

    If no ``EpisodeWorkflow`` exists for the episode, returns a stub dict
    with ``status='not_started'``.
    """
    try:
        wf = EpisodeWorkflow.objects.get(episode_id=episode_id)
    except EpisodeWorkflow.DoesNotExist:
        return {
            "current_step": None,
            "status": "not_started",
            "blocked_on": "",
            "completed_steps": [],
            "next_step": WORKFLOW_STEPS[0],
            "history": [],
        }

    completed = _completed_steps(wf.history)
    return {
        "current_step": wf.current_step,
        "status": wf.status,
        "blocked_on": wf.blocked_on,
        "completed_steps": completed,
        "next_step": _next_step(wf.current_step),
        "history": wf.history,
    }


def advance_step(episode_id: int, completed_step: str) -> EpisodeWorkflow:
    """Mark *completed_step* as done and move the workflow to the next step.

    Updates the current history entry for *completed_step* with a
    ``completed_at`` timestamp and ``status='completed'``.  If a next step
    exists, sets ``current_step`` to it and appends a fresh history entry.
    If *completed_step* is the final step, the workflow status becomes
    ``'complete'``.

    Raises ``EpisodeWorkflow.DoesNotExist`` if no workflow exists.
    Raises ``ValueError`` if already at the last step after completion.
    """
    wf = EpisodeWorkflow.objects.get(episode_id=episode_id)

    # Mark the completed step in history
    now = _now_iso()
    for entry in reversed(wf.history):
        if entry["step"] == completed_step:
            entry["completed_at"] = now
            entry["status"] = "completed"
            break

    next_s = _next_step(completed_step)
    if next_s is None:
        # Final step completed
        wf.current_step = completed_step
        wf.status = "complete"
        wf.blocked_on = ""
    else:
        wf.current_step = next_s
        wf.status = "running"
        wf.blocked_on = ""
        wf.history.append(_make_history_entry(next_s))

    wf.save(update_fields=["current_step", "status", "blocked_on", "history"])
    return wf


def pause_for_human(episode_id: int, reason: str) -> EpisodeWorkflow:
    """Pause the workflow, waiting for human input.

    Sets ``status='paused_for_human'`` and records *reason* in
    ``blocked_on``.  Also updates the latest history entry for the current
    step to reflect the paused state.
    """
    wf = EpisodeWorkflow.objects.get(episode_id=episode_id)

    wf.status = "paused_for_human"
    wf.blocked_on = reason

    # Update the current step's history entry
    for entry in reversed(wf.history):
        if entry["step"] == wf.current_step:
            entry["status"] = "paused_for_human"
            break

    wf.save(update_fields=["status", "blocked_on", "history"])
    return wf


def resume_workflow(episode_id: int) -> EpisodeWorkflow:
    """Resume a previously paused workflow.

    Clears the ``blocked_on`` field and sets ``status='running'``.
    Also restores the current step's history entry status to ``'started'``.
    """
    wf = EpisodeWorkflow.objects.get(episode_id=episode_id)

    wf.status = "running"
    wf.blocked_on = ""

    # Restore the current step's history entry
    for entry in reversed(wf.history):
        if entry["step"] == wf.current_step:
            entry["status"] = "started"
            break

    wf.save(update_fields=["status", "blocked_on", "history"])
    return wf


def check_quality_gate(episode_id: int, gate_name: str) -> dict:
    """Check whether a quality gate passes for the given episode.

    Supported gates:

    * ``"wave_1"`` -- after Master Briefing, before Synthesis.  Checks that
      a ``p3-briefing`` artifact exists and contains substantial content
      (>= 200 words).
    * ``"wave_2"`` -- after Episode Planning, before Audio Generation.
      Checks that a ``content_plan`` or ``content-plan`` artifact exists.

    Returns ``{"passed": bool, "details": str}``.
    """
    episode = Episode.objects.get(pk=episode_id)
    artifact_titles = list(
        EpisodeArtifact.objects.filter(episode=episode).values_list("title", flat=True)
    )

    if gate_name == "wave_1":
        # Check for p3-briefing artifact with substantial content
        briefing_artifact = None
        for title in artifact_titles:
            if "p3-briefing" in title.lower():
                briefing_artifact = EpisodeArtifact.objects.get(
                    episode=episode, title=title
                )
                break

        if briefing_artifact is None:
            return {
                "passed": False,
                "details": "No p3-briefing artifact found.",
            }

        word_count = (
            len(briefing_artifact.content.split()) if briefing_artifact.content else 0
        )
        if word_count < _MIN_ARTIFACT_WORDS:
            return {
                "passed": False,
                "details": (
                    f"p3-briefing artifact has only {word_count} words "
                    f"(minimum {_MIN_ARTIFACT_WORDS} required)."
                ),
            }

        return {
            "passed": True,
            "details": f"p3-briefing artifact found with {word_count} words.",
        }

    elif gate_name == "wave_2":
        # Check for content_plan or content-plan artifact
        has_plan = any(
            "content_plan" in t.lower() or "content-plan" in t.lower()
            for t in artifact_titles
        )
        if not has_plan:
            return {
                "passed": False,
                "details": "No content_plan or content-plan artifact found.",
            }
        return {
            "passed": True,
            "details": "Content plan artifact found.",
        }

    else:
        return {
            "passed": False,
            "details": f"Unknown quality gate: {gate_name}",
        }


def fail_step(episode_id: int, step: str, error: str) -> EpisodeWorkflow:
    """Mark the workflow as failed at *step* with an error message.

    Sets ``status='failed'`` and records the error in the history entry for
    the given step.
    """
    wf = EpisodeWorkflow.objects.get(episode_id=episode_id)

    wf.status = "failed"

    # Update the matching history entry
    for entry in reversed(wf.history):
        if entry["step"] == step:
            entry["status"] = "failed"
            entry["error"] = error
            break

    wf.save(update_fields=["status", "history"])
    return wf
