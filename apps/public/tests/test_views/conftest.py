"""
Pytest fixtures for view testing.

These fixtures provide common test utilities for view testing:
- request_factory: A Django RequestFactory instance
- authenticated_request: A request with an authenticated user
- htmx_request: A request with HTMX headers
- messages_request: A request with message support
"""

import os

import django
import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
django.setup()

# Use the built-in User model for tests to avoid dependency issues
User = get_user_model()


@pytest.fixture
def request_factory():
    """Return a RequestFactory instance."""
    return RequestFactory()


@pytest.fixture
def user():
    """Return a test user."""
    user = User(
        username="testviewuser",
        email="testviewuser@example.com",
    )
    user.is_authenticated = True
    return user


@pytest.fixture
def authenticated_request(request_factory, user):
    """Return a GET request with an authenticated user."""
    request = request_factory.get("/")
    request.user = user

    # Add session
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()

    return request


@pytest.fixture
def htmx_request(authenticated_request):
    """Return a request with HTMX headers."""
    authenticated_request.htmx = True
    authenticated_request.META["HTTP_HX_REQUEST"] = "true"
    return authenticated_request


@pytest.fixture
def messages_request(authenticated_request):
    """Return a request with message support."""
    setattr(authenticated_request, "_messages", FallbackStorage(authenticated_request))
    return authenticated_request


@pytest.fixture
def create_post_request(request_factory, user):
    """Return a factory function to create POST requests with data."""

    def _create_post_request(data, url="/"):
        request = request_factory.post(url, data)
        request.user = user

        # Add session
        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

        # Add messages
        setattr(request, "_messages", FallbackStorage(request))

        return request

    return _create_post_request
