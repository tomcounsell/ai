from django.db import models

from apps.common.behaviors import Timestampable


class Podcast(Timestampable):
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    description = models.TextField()
    author_name = models.CharField(max_length=200)
    author_email = models.EmailField()
    cover_image_url = models.URLField(blank=True)
    language = models.CharField(max_length=10, default="en")
    is_public = models.BooleanField(default=False)
    categories = models.JSONField(default=list, blank=True)
    website_url = models.URLField(blank=True)

    class Meta:
        ordering = ["title"]
        indexes = [
            models.Index(fields=["slug"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.pk:
            # Prevent changing is_public after creation.
            # Switching visibility would leave audio files in the wrong bucket
            # and break existing feed URLs.
            try:
                existing = Podcast.objects.only("is_public").get(pk=self.pk)
                if existing.is_public != self.is_public:
                    raise ValueError(
                        "Podcast visibility (is_public) cannot be changed "
                        "after creation. Create a new podcast instead."
                    )
            except Podcast.DoesNotExist:
                pass
        super().save(*args, **kwargs)
