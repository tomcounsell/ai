from django.conf import settings
from django.db import models
from rest_framework_api_key.models import AbstractAPIKey

from apps.common.behaviors import Timestampable


class UserAPIKey(AbstractAPIKey, Timestampable):
    """
    API key associated with a specific user.

    Allows a user to generate and manage API keys for authenticated
    API access without using their username/password credentials.

    Attributes:
        user (ForeignKey): The user this API key belongs to
        name (str): A descriptive name for the API key (e.g., "Mobile App")
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    name = models.CharField(max_length=50)

    class Meta(AbstractAPIKey.Meta):
        verbose_name = "User API Key"
        verbose_name_plural = "User API Keys"


class TeamAPIKey(AbstractAPIKey, Timestampable):
    """
    API key associated with a specific team.

    Allows team members to create shared API keys for integrations
    and services that need to access team resources.

    Attributes:
        team (ForeignKey): The team this API key belongs to
        name (str): A descriptive name for the API key
    """

    team = models.ForeignKey(
        "common.Team",
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    name = models.CharField(max_length=50)

    class Meta(AbstractAPIKey.Meta):
        verbose_name = "Team API Key"
        verbose_name_plural = "Team API Keys"
