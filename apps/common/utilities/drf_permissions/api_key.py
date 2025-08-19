from rest_framework.permissions import BasePermission
from rest_framework_api_key.permissions import HasAPIKey

from apps.common.models import TeamAPIKey, UserAPIKey


class HasUserAPIKey(HasAPIKey):
    """
    Permission class that checks if the request has a valid user API key.

    If the API key is valid, it attaches the associated user to the request,
    allowing views to identify the user making the request.
    """

    model = UserAPIKey

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False

        # Get the API key from the request
        key = self.get_key(request)
        if not key:
            return False

        # Check if the API key exists and get the associated user
        try:
            api_key = self.model.objects.get_from_key(key)
            if not api_key:
                return False

            # Add the user to the request object
            request.user = api_key.user
            request.api_key = api_key
            return True
        except Exception:
            return False


class HasTeamAPIKey(HasAPIKey):
    """
    Permission class that checks if the request has a valid team API key.

    If the API key is valid, it attaches the associated team to the request,
    allowing views to identify the team on behalf of which the request is made.
    """

    model = TeamAPIKey

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False

        # Get the API key from the request
        key = self.get_key(request)
        if not key:
            return False

        # Check if the API key exists and get the associated team
        try:
            api_key = self.model.objects.get_from_key(key)
            if not api_key:
                return False

            # Add the team to the request object
            request.team = api_key.team
            request.api_key = api_key
            return True
        except Exception:
            return False


class HasAnyAPIKey(BasePermission):
    """
    Permission class that checks if the request has any valid API key.

    Attempts to authenticate with both UserAPIKey and TeamAPIKey.
    This is useful for endpoints that should be accessible with any type of API key.
    """

    def has_permission(self, request, view):
        has_user_api_key = HasUserAPIKey().has_permission(request, view)
        if has_user_api_key:
            return True

        return HasTeamAPIKey().has_permission(request, view)
