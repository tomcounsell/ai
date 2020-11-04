from django.db import models

from apps.common.behaviors import Timestampable


class SMS(Timestampable, models.Model):
    to_number = models.CharField(max_length=15)
    from_number = models.CharField(max_length=15, null=True, blank=True)
    body = models.TextField(default="")

    # UPDATE HISTORY
    sent_at = models.DateTimeField(null=True)
    read_at = models.DateTimeField(null=True)
