from django.db import models

from apps.common.behaviors import Timestampable

from .episode import Episode


class EpisodeArtifact(Timestampable):
    """Document store for any file or resource produced during episode creation.

    Artifacts are intentionally free-form.  The title is a human-readable name,
    the description explains what the artifact contains, and workflow_context
    records which workflow step(s) created or consume the artifact using each
    step's full name (e.g. "Research Gathering", "Cross-Validation → Episode
    Planning") — never phase numbers, since numbering shifts as the workflow
    evolves.

    Both text content and external URLs are supported so the same model works
    for markdown research docs, binary audio served from S3, PDFs, and anything
    else the workflow produces.

    The metadata JSONField is a catch-all for structured data such as
    chapter timestamps, keyword lists, quality scores, or tool parameters.
    """

    episode = models.ForeignKey(
        Episode, on_delete=models.CASCADE, related_name="artifacts"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    workflow_context = models.CharField(
        max_length=200,
        blank=True,
        help_text="Workflow step(s) that produced and/or consume this artifact, by name (not number).",
    )
    content = models.TextField(blank=True)
    url = models.URLField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["title"]
        unique_together = [("episode", "title")]

    def __str__(self) -> str:
        return f"{self.episode} / {self.title}"
