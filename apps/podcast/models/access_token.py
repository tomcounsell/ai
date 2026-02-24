import secrets

from django.db import models

from apps.common.behaviors import Timestampable


class PodcastAccessToken(Timestampable):
    """Per-user access token for restricted podcast feeds."""

    podcast = models.ForeignKey(
        "podcast.Podcast",
        on_delete=models.CASCADE,
        related_name="access_tokens",
    )
    label = models.CharField(
        max_length=200,
        help_text="Who/what this token is for (e.g. 'Tom iPhone', 'Client A')",
    )
    token = models.CharField(max_length=64, unique=True, db_index=True)
    is_active = models.BooleanField(default=True)
    last_accessed_at = models.DateTimeField(null=True, blank=True)
    access_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.podcast} \u2014 {self.label}"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def record_access(self):
        """Increment access count and update timestamp. Use F() to avoid race conditions."""
        from django.db.models import F
        from django.utils import timezone

        PodcastAccessToken.objects.filter(pk=self.pk).update(
            access_count=F("access_count") + 1,
            last_accessed_at=timezone.now(),
        )
