from django.db import models

from apps.common.behaviors import Timestampable

from .episode import Episode

WORKFLOW_STATUS_CHOICES = [
    ("pending", "Pending"),
    ("running", "Running"),
    ("paused_for_human", "Paused for Human"),
    ("paused_at_gate", "Paused at Gate"),
    ("failed", "Failed"),
    ("complete", "Complete"),
]


class EpisodeWorkflow(Timestampable):
    """Tracks the current state and history of an episode's production workflow.

    Each episode has at most one workflow record that records which step is
    active, whether the workflow is blocked on human input, and a full
    history of step transitions.
    """

    episode = models.OneToOneField(
        Episode, on_delete=models.CASCADE, related_name="workflow"
    )
    current_step = models.CharField(
        max_length=100,
        default="Setup",
        help_text='Current workflow step name, e.g. "Research Gathering", "Synthesis".',
    )
    status = models.CharField(
        max_length=20,
        choices=WORKFLOW_STATUS_CHOICES,
        default="pending",
    )
    blocked_on = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Description of the input or action needed to unblock the workflow.",
    )
    history = models.JSONField(
        default=list,
        blank=True,
        help_text="List of dicts: [{step, status, started_at, completed_at, error}].",
    )
    agent_session_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Anthropic agent session ID for resumable workflows.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.episode.title} - {self.current_step} ({self.status})"
