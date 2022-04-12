import uuid
from django.db import models
from apps.common.behaviors.timestampable import Timestampable


class Address(Timestampable, models.Model):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    line_1 = models.CharField(max_length=100, null=True, blank=True)
    line_2 = models.CharField(max_length=100, null=True, blank=True)
    line_3 = models.CharField(max_length=100, null=True, blank=True)
    city = models.CharField(max_length=35, null=True, blank=True)
    region = models.CharField(max_length=35, null=True, blank=True)
    postal_code = models.CharField(max_length=10, null=True, blank=True)
    country = models.ForeignKey('common.Country', related_name='addresses',
                                null=True, on_delete=models.SET_NULL)

    # MODEL PROPERTIES
    @property
    def inline_string(self):
        string = "%s " % self.line_1
        string += "%s" % self.city or ""
        string += ", %s " % self.region or ""
        return string

    @property
    def google_map_url(self):
        return "http://maps.google.com/?q=%s" % self.inline_string.replace(
            " ", "%20")

    # MODEL FUNCTIONS
    def __str__(self):
        return str(self.inline_string)

    class Meta:
        verbose_name_plural = 'addresses'
