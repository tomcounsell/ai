from django.db import models


class Annotatable(models.Model):
    notes = models.ManyToManyField('common.Note')

    @property
    def has_notes(self):
        return True if self.notes.count() else False

    class Meta:
        abstract = True
