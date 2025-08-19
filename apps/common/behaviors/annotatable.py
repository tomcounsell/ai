from django.db import models


class Annotatable(models.Model):
    """
    A behavior mixin that allows models to have associated notes.

    This mixin provides a many-to-many relationship with the Note model,
    allowing any model to have multiple notes attached to it. This is useful
    for adding comments, annotations, or any additional information to objects.

    Attributes:
        notes (ManyToManyField): A many-to-many relationship with the Note model.
            Allows multiple notes to be associated with an instance of the model.

    Properties:
        has_notes (bool): Returns True if the object has any associated notes,
            False otherwise.

    Example:
        ```python
        class BlogPost(Annotatable, models.Model):
            title = models.CharField(max_length=100)
            content = models.TextField()

            # Can now use:
            # blog_post.notes.add(note)
            # blog_post.has_notes
        ```

    Note:
        This model is abstract and should be used as a mixin in other models.
    """

    notes = models.ManyToManyField("common.Note")

    @property
    def has_notes(self) -> bool:
        """
        Check if the object has any associated notes.

        Returns:
            bool: True if the object has at least one note, False otherwise.
        """
        return self.notes.exists()

    class Meta:
        abstract = True
