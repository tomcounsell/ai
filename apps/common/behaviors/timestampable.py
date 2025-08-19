from django.db import models


class Timestampable(models.Model):
    """
    created_at: datetime
    modified_at: datetime

    An abstract mixin for models that need to track the creation and modification timestamps.

    This mixin automatically adds two fields to the model that keep track of when an object
    was created and last modified. It can be used in various models that require these features.

    Attributes:
        created_at (datetime): A datetime field that stores the timestamp of when the object was created.
            It is automatically set to the current time when the object is first created and is not
            subsequently modified.

        modified_at (datetime): A datetime field that stores the timestamp of when the object was last
            modified. It is automatically updated to the current time whenever the object is saved.

    Note:
        This model is abstract and should be used as a mixin in other models.
        The `auto_now_add` and `auto_now` options are used to automatically manage the timestamps.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
