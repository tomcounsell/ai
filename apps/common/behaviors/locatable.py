from django.db import models
from timezone_field import TimeZoneField


class Locatable(models.Model):

    address = models.ForeignKey('common.Address', null=True, blank=True, on_delete=models.SET_NULL)
    timezone = TimeZoneField()

    longitude = models.FloatField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)

    class Meta:
        abstract = True
