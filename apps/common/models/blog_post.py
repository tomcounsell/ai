import uuid

from django.db import models

from apps.common.behaviors import (
    Annotatable,
    Authorable,
    Expirable,
    Locatable,
    Permalinkable,
    Publishable,
    Timestampable,
)


class BlogPost(
    Timestampable,
    Authorable,
    Publishable,
    Expirable,
    Locatable,
    Permalinkable,
    Annotatable,
    models.Model,
):
    """
    A comprehensive blog post model that demonstrates the use of all behavior mixins.

    This model serves as a practical example showing how behavior mixins can be combined
    to create a feature-rich entity with minimal code duplication.

    Attributes:
        id (UUID): Unique identifier for the blog post
        title (str): The title of the blog post
        subtitle (str): Optional subtitle or description
        content (str): The main content of the blog post
        featured_image (ForeignKey): An optional featured image for the blog post
        reading_time_minutes (int): Estimated reading time in minutes
        tags (str): Comma-separated tags for the blog post

    Behaviors:
        Timestampable: Tracks creation and modification dates
        Authorable: Associates the post with an author and tracks authorship details
        Publishable: Manages publishing state and related timestamps
        Expirable: Allows posts to expire after a certain date
        Locatable: Associates the post with a geographical location
        Permalinkable: Provides a slug field for SEO-friendly URLs
        Annotatable: Allows notes to be attached to the blog post

    Properties:
        summary (str): Returns a shortened version of the content for previews
        is_featured (bool): Indicates if this is a featured post based on tag
        reading_time_display (str): Human-readable representation of reading time
        slug_source (str): Source field for generating the permalink slug
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    subtitle = models.CharField(max_length=255, blank=True, default="")
    content = models.TextField()
    featured_image = models.ForeignKey(
        "common.Image",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="featured_blog_posts",
    )
    reading_time_minutes = models.PositiveIntegerField(default=3)
    tags = models.CharField(max_length=255, blank=True, default="")

    # MODEL PROPERTIES
    @property
    def summary(self):
        """Returns a shortened version of the content for previews."""
        if len(self.content) <= 200:
            return self.content
        return f"{self.content[:197]}..."

    @property
    def is_featured(self):
        """Determines if this is a featured post based on tags."""
        return "featured" in self.tags.lower()

    @property
    def reading_time_display(self):
        """Returns a human-readable representation of reading time."""
        if self.reading_time_minutes == 1:
            return "1 minute"
        return f"{self.reading_time_minutes} minutes"

    @property
    def slug_source(self):
        """Source for automatically generating the permalink slug."""
        return self.title

    # MODEL FUNCTIONS
    def __str__(self):
        return self.title

    def get_absolute_url(self):
        """Returns the URL for this blog post."""
        return f"/blog/{self.slug}/"

    def get_meta_description(self):
        """Returns meta description for SEO purposes."""
        return self.subtitle or self.summary

    def add_tag(self, tag):
        """Adds a tag to the blog post."""
        tag = tag.strip().lower()
        tags = self.tags.split(",") if self.tags else []
        if tag not in tags:
            tags.append(tag)
            self.tags = ",".join(filter(None, tags))

    def remove_tag(self, tag):
        """Removes a tag from the blog post."""
        tag = tag.strip().lower()
        tags = self.tags.split(",") if self.tags else []
        if tag in tags:
            tags.remove(tag)
            self.tags = ",".join(filter(None, tags))

    class Meta:
        verbose_name = "Blog Post"
        verbose_name_plural = "Blog Posts"
        ordering = ["-published_at", "-created_at"]
