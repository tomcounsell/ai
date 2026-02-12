from django.db import models

from apps.common.behaviors import Expirable, Publishable, Timestampable

from .podcast import Podcast


class Episode(Timestampable, Publishable, Expirable):
    podcast = models.ForeignKey(
        Podcast, on_delete=models.CASCADE, related_name="episodes"
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField()
    episode_number = models.PositiveIntegerField()
    description = models.TextField(blank=True)
    show_notes = models.TextField(blank=True)

    # Audio
    audio_url = models.URLField()
    audio_duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    audio_file_size_bytes = models.BigIntegerField(null=True, blank=True)

    # Media
    cover_image_url = models.URLField(blank=True)
    is_explicit = models.BooleanField(default=False)

    # Podcasting 2.0
    transcript = models.TextField(blank=True)
    chapters = models.TextField(blank=True)

    # Content
    companion_resources = models.JSONField(default=dict, blank=True)
    report_text = models.TextField(blank=True)
    sources_text = models.TextField(blank=True)

    class Meta:
        ordering = ["episode_number"]
        indexes = [
            models.Index(fields=["slug"]),
        ]
        unique_together = [
            ("podcast", "episode_number"),
            ("podcast", "slug"),
        ]

    def __str__(self):
        return f"{self.episode_number}. {self.title}"

    @property
    def effective_cover_image_url(self):
        return self.cover_image_url or self.podcast.cover_image_url
