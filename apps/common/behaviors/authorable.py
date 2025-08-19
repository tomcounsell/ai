from django.db import models

from settings import AUTH_USER_MODEL


class Authorable(models.Model):
    """
    author: User
    is_author_anonymous: bool
    authored_at: datetime

    A mixin for use with models that have an associated author, represented by a user.

    This mixin encapsulates common attributes related to authorship and can be used
    in various models that require an author attribute.

    Attributes:
        author (ForeignKey): A foreign key to the user model representing the author of the content.
            The related name is dynamically generated based on the class name.

        is_author_anonymous (bool): A boolean flag indicating whether the author's identity should
            be kept anonymous. Defaults to False.

        authored_at (datetime): A timestamp representing when the content was authored.
            Automatically set when the object is created.

    Properties:
        author_display_name (str): A property that returns the display name of the author.
            If `is_author_anonymous` is True, it returns "Anonymous"; otherwise, it returns the string representation of the author.

    Note:
        This model is abstract and should be used as a mixin in other models.

    """

    author = models.ForeignKey(
        AUTH_USER_MODEL,
        related_name="%(class)ss",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    is_author_anonymous = models.BooleanField(default=False)
    authored_at = models.DateTimeField(auto_now_add=True)

    @property
    def author_display_name(self):
        if self.is_author_anonymous:
            return "Anonymous"
        else:
            return str(self.author)

    class Meta:
        abstract = True
