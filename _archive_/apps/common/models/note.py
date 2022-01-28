import uuid
from django.db import models
from apps.common.behaviors import Timestampable, Authorable


class Note(Timestampable, Authorable, models.Model):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    text = models.TextField(default="", blank=True)

    # MODEL PROPERTIES

    # MODEL FUNCTIONS
