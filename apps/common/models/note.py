import uuid

from django.db import models

from apps.common.behaviors import Authorable, Timestampable


class Note(Timestampable, Authorable, models.Model):
    """
    A model representing a text note or comment.

    This model provides a versatile way to add notes to various entities through
    the Annotatable behavior. It includes author tracking via the Authorable behavior
    and timestamps via the Timestampable behavior.

    Attributes:
        id (UUID): Unique identifier for the note
        text (str): The content of the note
        author (ForeignKey): The user who created the note (from Authorable)
        is_author_anonymous (bool): Whether the author should remain anonymous (from Authorable)
        authored_at (datetime): When the note was created (from Authorable)
        created_at (datetime): When this note record was created (from Timestampable)
        modified_at (datetime): When this note record was last modified (from Timestampable)

    Example:
        ```python
        # Adding a note to an annotatable object
        note = Note.objects.create(
            text="This is an important comment",
            author=request.user
        )
        blog_post.notes.add(note)
        ```
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    text = models.TextField(default="", blank=True)

    # MODEL PROPERTIES
    @property
    def summary(self) -> str:
        """
        Get a short summary of the note text.

        Returns:
            str: First 50 characters of the note with ellipsis if truncated
        """
        if len(self.text) <= 50:
            return self.text
        return f"{self.text[:47]}..."

    # MODEL FUNCTIONS
    def __str__(self) -> str:
        """
        Get a string representation of the note.

        Returns:
            str: The note summary
        """
        return self.summary
