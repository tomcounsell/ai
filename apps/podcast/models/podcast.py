from django.conf import settings
from django.db import models

from apps.common.behaviors import Publishable, Timestampable


class Podcast(Timestampable, Publishable):
    class Privacy(models.TextChoices):
        PUBLIC = "public", "Public"
        UNLISTED = "unlisted", "Unlisted"
        RESTRICTED = "restricted", "Restricted"

    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    description = models.TextField()
    author_name = models.CharField(max_length=200)
    author_email = models.EmailField()
    cover_image_url = models.URLField(blank=True)
    language = models.CharField(max_length=10, default="en")
    privacy = models.CharField(
        max_length=20,
        choices=Privacy.choices,
        default=Privacy.UNLISTED,
    )
    categories = models.JSONField(default=list, blank=True)
    website_url = models.URLField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="podcasts",
    )
    spotify_url = models.URLField(blank=True)
    apple_podcasts_url = models.URLField(blank=True)

    class Meta:
        ordering = ["title"]
        indexes = [
            models.Index(fields=["slug"]),
        ]

    def __str__(self):
        return self.title

    @property
    def is_public(self) -> bool:
        """Backward-compat property: True if privacy is PUBLIC."""
        return self.privacy == self.Privacy.PUBLIC

    @property
    def is_unlisted(self) -> bool:
        return self.privacy == self.Privacy.UNLISTED

    @property
    def is_restricted(self) -> bool:
        return self.privacy == self.Privacy.RESTRICTED

    @property
    def uses_private_bucket(self) -> bool:
        """Only restricted podcasts use the private Supabase bucket."""
        return self.is_restricted

    def save(self, *args, **kwargs):
        if self.pk:
            # Prevent changing privacy after creation.
            # Switching visibility would leave audio files in the wrong bucket
            # and break existing feed URLs.
            try:
                existing = Podcast.objects.only("privacy").get(pk=self.pk)
                if existing.privacy != self.privacy:
                    raise ValueError(
                        "Podcast privacy cannot be changed "
                        "after creation. Create a new podcast instead."
                    )
            except Podcast.DoesNotExist:
                pass
        super().save(*args, **kwargs)
