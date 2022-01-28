from django.db import models


class Expirable(models.Model):

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

    class Meta:
        abstract = True
