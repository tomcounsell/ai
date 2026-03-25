from django.db import models
from django.utils import timezone


class Announcement(models.Model):
    """A public announcement about the Blended Workforce book."""

    title: models.CharField = models.CharField(max_length=255)
    body: models.TextField = models.TextField()
    published_at: models.DateTimeField = models.DateTimeField(
        null=True, blank=True, help_text="Leave blank to keep as draft."
    )
    created_at: models.DateTimeField = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-published_at"]

    def __str__(self) -> str:
        return self.title

    @property
    def is_published(self) -> bool:
        return self.published_at is not None and self.published_at <= timezone.now()
