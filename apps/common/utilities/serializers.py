from rest_framework import serializers
from django.utils import six
from timezone_field import TimeZoneField as TimeZoneModelField


# Reference: https://github.com/mfogel/django-timezone-field/issues/29
class TimeZoneField(serializers.ChoiceField):
    def __init__(self, **kwargs):
        super().__init__(TimeZoneModelField.CHOICES + [(None, "")], **kwargs)

    def to_representation(self, value):
        return six.text_type(super().to_representation(value))


# https://github.com/encode/django-rest-framework/issues/2734#issuecomment-478077325
class WritableSerializerMethodField(serializers.SerializerMethodField):

    def __init__(self, method_name=None, **kwargs):
        self.method_name = method_name
        self.setter_method_name = kwargs.pop('setter_method_name', None)
        self.deserializer_field = kwargs.pop('deserializer_field')

        kwargs['source'] = '*'
        super(serializers.SerializerMethodField, self).__init__(**kwargs)

    def bind(self, field_name, parent):
        retval = super().bind(field_name, parent)
        if not self.setter_method_name:
            self.setter_method_name = f'set_{field_name}'

        return retval

    def to_internal_value(self, data):
        value = self.deserializer_field.to_internal_value(data)
        method = getattr(self.parent, self.setter_method_name)
        method(value)
        return {}
