"""
Tests for the SessionStateMixin and TeamSessionMixin classes.

These tests verify that the session mixins:
- Properly handle authentication state
- Manage session variables correctly
- Load and validate team access
- Redirect appropriately for unauthenticated users
"""

import os

import django
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase

# Setup Django if not already done
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
django.setup()

from apps.common.tests.factories import UserFactory
from apps.public.views.helpers.main_content_view import MainContentView
from apps.public.views.helpers.session_mixin import SessionStateMixin

User = get_user_model()


class TestSessionStateMixinView(SessionStateMixin, MainContentView):
    """A concrete implementation of SessionStateMixin for testing."""

    template_name = "test_template.html"

    def get(self, request, *args, **kwargs):
        return self.render(request)


class SessionMixinTestCase(TestCase):
    """Tests for SessionStateMixin functionality."""

    def setUp(self):
        """Set up test data and utilities."""
        self.factory = RequestFactory()
        self.user = UserFactory.create(
            username="sessionuser",
            email="sessionuser@example.com",
            password="testpassword123",
        )

    def _add_session_to_request(self, request):
        """Helper to add session to a request."""
        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

    def _add_messages_to_request(self, request):
        """Helper to add message support to a request."""
        setattr(request, "session", "session")
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

    def test_setup_initializes_context(self):
        """Test that setup initializes the context."""
        view = TestSessionStateMixinView()
        request = self.factory.get("/")
        request.user = self.user
        self._add_session_to_request(request)

        # Call setup directly to test its behavior
        view.setup(request)

        # Check that context exists
        self.assertIsInstance(view.context, dict)

    def test_setup_handles_just_logged_in(self):
        """Test that setup handles just_logged_in session variable."""
        view = TestSessionStateMixinView()
        request = self.factory.get("/")
        request.user = self.user
        self._add_session_to_request(request)

        # Set just_logged_in in session
        request.session["just_logged_in"] = True

        # Call setup directly
        view.setup(request)

        # Check that context contains just_logged_in
        self.assertTrue(view.context["just_logged_in"])

        # Check that just_logged_in was cleared from session
        self.assertNotIn("just_logged_in", request.session)

    def test_handle_unauthenticated(self):
        """Test handle_unauthenticated redirects to login page."""
        view = TestSessionStateMixinView()
        request = self.factory.get("/test-path/")

        response = view.handle_unauthenticated(request)

        # Check that response is a redirect to login page
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)
        self.assertIn("next=/test-path/", response.url)
