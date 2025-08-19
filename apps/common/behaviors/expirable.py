from django.db import models


class Expirable(models.Model):
    """
    valid_at: datetime
    expired_at: datetime

    A mixin for use with models that require expiration functionality.

    This mixin provides fields and methods to handle the validity and expiration of objects.
    It can be used in various models that require these features, such as tokens, offers, or temporary access permissions.

    Attributes:
        valid_at (DateTimeField, optional): A timestamp indicating when the object becomes valid.
            Can be left blank if the object is valid immediately upon creation.

        expired_at (DateTimeField, optional): A timestamp indicating when the object expires.
            Can be left blank if the object does not expire.

    Properties:
        is_expired (bool): A property that returns True if the object has expired, False otherwise.
            Can be set to True to mark the object as expired, or False to unmark it.

    Note:
        This model is abstract and should be used as a mixin in other models.
    """

    valid_at = models.DateTimeField(null=True, blank=True)
    expired_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_expired(self) -> bool:
        from django.utils.timezone import now

        return True if self.expired_at and self.expired_at < now() else False

    @is_expired.setter
    def is_expired(self, value: bool):
        from django.utils.timezone import now

        if value is True:
            self.expired_at = now()
        elif value is False and self.is_expired:
            self.expired_at = None
        elif value is None:
            # Handle None case by removing expiration
            self.expired_at = None

    class Meta:
        abstract = True
