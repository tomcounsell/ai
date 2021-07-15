from rest_framework import serializers
from apps.common.utilities.processing.serializers import WritableSerializerMethodField
from apps.user.models import User


class UserSerializer(serializers.HyperlinkedModelSerializer):

    username = serializers.CharField(required=False, allow_null=False, allow_blank=False, min_length=3, max_length=150)
    token = serializers.ReadOnlyField(source='jwt_token')
    is_agreed_to_terms = WritableSerializerMethodField(deserializer_field=serializers.BooleanField(), required=False)

    class Meta:
        model = User

        fields = ('id', 'token', 'is_agreed_to_terms',
                  'username', 'email', 'first_name',
                  'created_at', 'modified_at')

        read_only_fields = ('id', 'token',
                            'created_at', 'modified_at')

    def get_is_agreed_to_terms(self, obj):
        return obj.is_agreed_to_terms

    def set_is_agreed_to_terms(self, value):
        if self.instance:
            self.instance.is_agreed_to_terms = value
