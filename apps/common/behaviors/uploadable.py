import json
import uuid

from django.contrib.postgres.fields import JSONField
from django.db import models


class Uploadable(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    url = models.URLField(default="")
    meta_data = JSONField(blank=True, null=True)

    class Meta:
        abstract = True

    # MODEL PROPERTIES
    @property
    def file_type(self):
        if self.meta_data and isinstance(self.meta_data, str):
            self.meta_data = json.loads(self.meta_data)
        try:
            return self.meta_data.get('type', "") if self.meta_data else ""
        except:
            return ""

    @property
    def name(self):
        if self.meta_data and isinstance(self.meta_data, str):
            self.meta_data = json.loads(self.meta_data)
        return self.meta_data.get('name', "") if self.meta_data else ""

    @property
    def file_extension(self):
        if self.meta_data and isinstance(self.meta_data, str):
            self.meta_data = json.loads(self.meta_data)
        return self.meta_data.get('ext', "") if self.meta_data else ""

    @property
    def link_title(self):
        if self.name:
            title = self.name
        elif 'etc' in self.meta_data:
            title = (self.meta_data['etc'] or "").upper()
        else:
            title = (self.meta_data['type'] or
                     "").upper() if 'type' in self.meta_data else ""
        if 'ext' in self.meta_data:
            title = title + " .%s" % (self.meta_data['ext'] or "").upper()
        return title
