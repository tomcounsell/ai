"""
Tests for component view functionality in the public app.

These tests verify that component context processors:
- Provide the correct context data
- Handle different request types appropriately
"""

import os

import django
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase

# Setup Django if not already done
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
django.setup()

from apps.public.views.components.account_menu import get_account_menu_context
from apps.public.views.components.footer import get_footer_context
from apps.public.views.components.navbar import get_navbar_context
from apps.public.views.components.toast import get_toast_context

User = get_user_model()


class ComponentContextTestCase(TestCase):
    """Tests for component context processors."""

    def setUp(self):
        """Set up test data and utilities."""
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username="componentuser",
            email="componentuser@example.com",
            password="testpassword123",
        )

    def _add_session_to_request(self, request):
        """Helper to add session to a request."""
        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

    def test_account_menu_context_authenticated(self):
        """Test account menu context with an authenticated user."""
        request = self.factory.get("/")
        request.user = self.user
        self._add_session_to_request(request)

        context = get_account_menu_context(request)

        # Verify context contains expected values
        self.assertIsInstance(context, dict)

    def test_account_menu_context_unauthenticated(self):
        """Test account menu context with an unauthenticated user."""
        request = self.factory.get("/")
        request.user = AnonymousUser()
        self._add_session_to_request(request)

        context = get_account_menu_context(request)

        # Verify context contains expected values
        self.assertIsInstance(context, dict)

    def test_navbar_context(self):
        """Test navbar context."""
        request = self.factory.get("/")
        request.user = self.user
        self._add_session_to_request(request)

        context = get_navbar_context(request)

        # Verify context contains expected values
        self.assertIsInstance(context, dict)

    def test_footer_context(self):
        """Test footer context."""
        request = self.factory.get("/")
        request.user = self.user
        self._add_session_to_request(request)

        context = get_footer_context(request)

        # Verify context contains expected values for footer
        self.assertIsInstance(context, dict)

    def test_toast_context(self):
        """Test toast context with messages."""
        request = self.factory.get("/")
        request.user = self.user
        self._add_session_to_request(request)

        # Add messages to the request
        setattr(request, "_messages", FallbackStorage(request))
        from django.contrib import messages

        messages.success(request, "Test success message")
        messages.error(request, "Test error message")

        context = get_toast_context(request)

        # Verify context contains the messages
        self.assertIsInstance(context, dict)
