from django.core.exceptions import ValidationError
from django.db import models


# https://djangosnippets.org/snippets/1741/
# http://stackoverflow.com/questions/28529179/django-creating-a-custom-model-field
class MoneyField(models.IntegerField):
    description = "A field to save a currency as cents(int) "
    description += "in db, but act like a float"

    def get_db_prep_value(self, value, *args, **kwargs):
        if value is None:
            return None
        return int(round(value * 100))

    def to_python(self, value):
        if value is None or isinstance(value, float):
            return value
        try:
            return float(value) / 100
        except (TypeError, ValueError):
            msg = "This value must be an integer or a "
            msg += "string represents an integer."
            raise ValidationError(
                msg
            )

    def from_db_value(self, value, expression, connection, context):
        return self.to_python(value)

    def formfield(self, **kwargs):
        from django.forms import FloatField
        defaults = {'form_class': FloatField}
        defaults.update(kwargs)
        return super(MoneyField, self).formfield(**defaults)
