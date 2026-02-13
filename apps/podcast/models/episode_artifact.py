from django.db import models

from apps.common.behaviors import Timestampable

from .episode import Episode


class EpisodeArtifact(Timestampable):
    """Generic document store for research, plans, prompts, and logs tied to an episode.

    Title conventions use relative file paths to organize artifacts by type:
        - "research/p2-perplexity.md" for research documents
        - "research/p3-gemini.md" for research from other sources
        - "plans/content-plan.md" for episode planning documents
        - "prompts/notebooklm-focus.md" for generation prompts
        - "logs/generation.md" for process logs

    The metadata JSONField is a catch-all for structured data such as
    chapter timestamps, keyword lists, quality scores, or tool parameters.
    """

    episode = models.ForeignKey(
        Episode, on_delete=models.CASCADE, related_name="artifacts"
    )
    title = models.CharField(max_length=200)
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["title"]
        unique_together = [("episode", "title")]

    def __str__(self) -> str:
        return f"{self.episode} / {self.title}"
