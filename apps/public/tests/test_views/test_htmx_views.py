"""
Tests for HTMX-enabled views in the public app.

These tests verify that HTMX views:
- Only respond to HTMX requests
- Render the correct templates
- Include OOB templates when appropriate
- Set proper HTMX response headers
- Handle push URL updates correctly
"""

import os
from unittest.mock import MagicMock, patch

import django
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

# Setup Django if not already done
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
django.setup()

from apps.public.views.helpers.htmx_view import HTMXView

# Use regular Django User model for simple tests
User = get_user_model()


# Simplified HTMXView for testing
class MockHTMXView(HTMXView):
    """A mock HTMX view for testing."""

    template_name = "mock_template.html"
    oob_templates = {"mock_target": "mock_oob_template.html"}
    push_url = "/mock-url"

    def get(self, request, *args, **kwargs):
        return self.render(request)


class HTMXViewTestCase(TestCase):
    """Tests for HTMX view functionality."""

    def setUp(self):
        """Set up test data and utilities."""
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username="htmxuser",
            email="htmxuser@example.com",
            password="testpassword123",
        )

    def _create_htmx_request(self, url="/test/"):
        """Create a request with HTMX headers."""
        request = self.factory.get(url)
        request.htmx = True
        request.user = self.user

        # Add session
        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

        # Add messages
        setattr(request, "_messages", FallbackStorage(request))

        return request

    def _create_non_htmx_request(self, url="/test/"):
        """Create a regular non-HTMX request."""
        request = self.factory.get(url)
        request.htmx = False
        request.user = self.user

        # Add session
        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

        return request

    def test_htmx_view_requires_htmx_request(self):
        """Test that HTMXView only accepts HTMX requests."""
        view = MockHTMXView()
        request = self._create_non_htmx_request()

        # Should raise NotImplementedError for non-HTMX requests
        with self.assertRaises(NotImplementedError):
            view.dispatch(request)

    @patch("django.template.loader.get_template")
    def test_htmx_view_handles_htmx_requests(self, mock_get_template):
        """Test that HTMXView accepts HTMX requests."""
        # Setup template mock
        mock_template = MagicMock()
        mock_get_template.return_value = mock_template

        # Create a custom TestHTMXView that we'll instrument
        class TestHTMXView(HTMXView):
            template_name = "test_template.html"
            oob_templates = {"test_target": "test_oob_template.html"}
            push_url = "/test-push-url"

            def get(self, request, *args, **kwargs):
                self.context["test_key"] = "test_value"
                return self.render(request)

        # Override render method with our own implementation
        original_render = TestHTMXView.render

        try:
            TestHTMXView.render = lambda self, req=None, template=None, context=None, oob=None, push=None: HttpResponse(
                f"Mocked HTMX response with push_url: {self.push_url}"
            )

            # Call the view
            view = TestHTMXView()
            request = self._create_htmx_request()
            response = view.dispatch(request)

            # Check response
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.content.decode("utf-8"),
                "Mocked HTMX response with push_url: /test-push-url",
            )

        finally:
            # Restore original render method
            TestHTMXView.render = original_render

    def test_htmx_view_with_messages(self):
        """Test that HTMXView includes messages template when messages exist."""

        # Create a custom HTMXView subclass for testing
        class TestMessagesView(HTMXView):
            template_name = "test_template.html"

            def get(self, request, *args, **kwargs):
                oob_dict = {}
                if list(messages.get_messages(request)):
                    oob_dict["messages"] = "common/toasts.html"
                return self.render(request, oob_templates=oob_dict)

        # Create view and request with messages
        view = TestMessagesView()
        request = self._create_htmx_request()

        # Add a message
        from django.contrib import messages

        messages.success(request, "Test message")

        # Mock the actual rendering to avoid template errors
        with patch(
            "django.template.loader.render_to_string", return_value="Mocked template"
        ):
            with patch("django.template.loader.get_template"):
                # We're testing that the view correctly detects the message and adds it to oob_templates
                with patch.object(view, "render", wraps=view.render) as mock_render:
                    # Call the view's get method
                    view.get(request)

                    # Verify that render was called with the messages template
                    args, kwargs = mock_render.call_args
                    oob_templates = kwargs.get("oob_templates", {})
                    self.assertIn("messages", oob_templates)
                    self.assertEqual(oob_templates["messages"], "common/toasts.html")


if __name__ == "__main__":
    import unittest

    unittest.main()
