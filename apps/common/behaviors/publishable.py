from django.db import models
from django.utils import timezone


class Publishable(models.Model):
    """
    A behavior mixin that adds publishing workflow functionality to a model.

    This mixin provides fields and methods to handle the publishing lifecycle of content,
    including publishing, unpublishing, and tracking edit history. It's useful for models
    like blog posts, articles, or any content that needs publication control.

    Attributes:
        published_at (DateTimeField, optional): Timestamp when the content was published.
            Null if the content has never been published.

        edited_at (DateTimeField, optional): Timestamp when the content was last edited.
            Null if the content has never been edited after publication.

        unpublished_at (DateTimeField, optional): Timestamp when the content was unpublished.
            Null if the content has never been unpublished or is currently published.

    Properties:
        is_published (bool): Returns True if the content is currently published,
            False otherwise. Can be set to True to publish the content or False to unpublish it.

    Methods:
        publish(): Marks the content as published by setting the published_at timestamp.
        unpublish(): Marks the content as unpublished by setting the unpublished_at timestamp.

    Example:
        ```python
        class Article(Publishable, models.Model):
            title = models.CharField(max_length=200)
            content = models.TextField()

            # Can now use:
            # article.publish()
            # article.unpublish()
            # article.is_published (getter/setter)
        ```

    Note:
        This model is abstract and should be used as a mixin in other models.
        The publishing status is determined by comparing the published_at and
        unpublished_at timestamps, with the most recent action taking precedence.
    """

    published_at = models.DateTimeField(null=True, blank=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    unpublished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def is_published(self) -> bool:
        """
        Check if the content is currently published.

        The content is considered published if it has a published_at timestamp
        in the past and either has no unpublished_at timestamp or the unpublished_at
        timestamp is older than the published_at timestamp.

        Returns:
            bool: True if the content is currently published, False otherwise.
        """
        now = timezone.now()
        # If unpublished_at is more recent than published_at, the item is unpublished
        if self.unpublished_at and (
            not self.published_at or self.unpublished_at > self.published_at
        ):
            return False
        # Item is published if it has a published_at date in the past
        elif self.published_at and self.published_at < now:
            return True
        else:
            return False

    @is_published.setter
    def is_published(self, value: bool):
        """
        Set the publication status of the content.

        Args:
            value (bool): True to publish the content, False to unpublish it.
        """
        if value and not self.is_published:
            self.unpublished_at = None
            self.published_at = timezone.now()
        elif not value and self.is_published:
            self.unpublished_at = timezone.now()

    def publish(self):
        """
        Publish the content by setting the published_at timestamp to now.
        """
        self.is_published = True

    def unpublish(self):
        """
        Unpublish the content by setting the unpublished_at timestamp to now.
        """
        self.is_published = False

    @property
    def publication_status(self) -> str:
        """
        Get a human-readable status of the content's publication state.

        Returns:
            str: One of "Published", "Unpublished", or "Draft"
        """
        if self.is_published:
            return "Published"
        elif self.published_at:
            return "Unpublished"
        else:
            return "Draft"
