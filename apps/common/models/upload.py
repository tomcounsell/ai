from django.contrib.postgres.fields import JSONField
from django.db import models

from ..behaviors import Timestampable


class Upload(Timestampable, models.Model):
    original = models.URLField(default="")
    name = models.CharField(max_length=50, blank=True, null=True)
    thumbnail = models.URLField(default="", blank=True, null=True)
    meta_data = JSONField(blank=True, null=True)

    @property
    def file_type(self):
        return self.meta_data.get('type', "") if self.meta_data else ""

    @property
    def is_image(self):
        return True if 'image' in self.file_type else False

    @property
    def is_pdf(self):
        return True if 'pdf' in self.file_type else False

    @property
    def width(self):
        if self.is_image:
            return self.meta_data['meta'].get(
                'width') if self.meta_data.get('meta') else None

    @property
    def height(self):
        if self.is_image:
            return self.meta_data['meta'].get(
                'height') if self.meta_data.get('meta') else None

    @property
    def file_extension(self):
        return self.meta_data.get('ext', "")

    @property
    def link_title(self):
        if self.name:
            title = self.name
        elif 'etc' in self.meta_data:
            title = (self.meta_data['etc'] or "").upper()
        else:
            title = (self.meta_data['type'] or "").upper(
                ) if 'type' in self.meta_data else ""
        if 'ext' in self.meta_data:
            title = title + " .%s" % (self.meta_data['ext'] or "").upper()
        return title
