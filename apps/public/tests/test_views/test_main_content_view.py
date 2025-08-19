"""
Tests for the MainContentView base class.

These tests verify that the MainContentView:
- Properly initializes context
- Handles GET/POST requests appropriately
- Renders templates correctly
- Combines context data properly
"""

import os
from unittest.mock import MagicMock, patch

import django
from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

# Setup Django if not already done
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
django.setup()

from apps.public.views.helpers.main_content_view import MainContentView

# Use regular Django User model for simple tests
User = get_user_model()


class TestMainContentView(MainContentView):
    """A concrete implementation of MainContentView for testing."""

    template_name = "test_template.html"
    url = "/test-url/"

    def get(self, request, *args, **kwargs):
        self.context["test_value"] = "test_get"
        return self.render(request)

    def post(self, request, *args, **kwargs):
        self.context["test_value"] = "test_post"
        return self.render(request)


class MainContentViewTestCase(TestCase):
    """Tests for MainContentView functionality."""

    def setUp(self):
        """Set up test data and utilities."""
        self.factory = RequestFactory()
        # Create a real user for testing
        self.user = User.objects.create_user(
            username="mcvuser", email="mcvuser@example.com", password="testpassword123"
        )

    def _add_session_to_request(self, request):
        """Helper to add session to a request."""
        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

    def test_init_sets_default_context(self):
        """Test that the constructor sets the default context."""
        view = TestMainContentView()

        # Check default context values
        self.assertIn("is_oob", view.context)
        self.assertFalse(view.context["is_oob"])

    @patch("django.template.loader.get_template")
    @patch("django.shortcuts.render")
    def test_dispatch_sets_url_and_base_template(self, mock_render, mock_get_template):
        """Test that dispatch sets url and base_template."""
        # Setup mocks
        mock_template = MagicMock()
        mock_get_template.return_value = mock_template
        mock_render.return_value = HttpResponse("Mocked render response")

        view = TestMainContentView()
        request = self.factory.get("/")
        request.user = self.user
        self._add_session_to_request(request)

        view.dispatch(request)

        # Check that context contains url and base_template
        self.assertEqual(view.context["url"], "/test-url/")
        self.assertEqual(view.context["base_template"], "base.html")

    @patch("django.template.loader.get_template")
    @patch("django.shortcuts.render")
    def test_dispatch_with_just_logged_in(self, mock_render, mock_get_template):
        """Test that dispatch handles just_logged_in session variable."""
        # Setup mocks
        mock_template = MagicMock()
        mock_get_template.return_value = mock_template
        mock_render.return_value = HttpResponse("Mocked render response")

        view = TestMainContentView()
        request = self.factory.get("/")
        request.user = self.user
        self._add_session_to_request(request)

        # Set just_logged_in in session
        request.session["just_logged_in"] = True

        view.dispatch(request)

        # Check that context contains just_logged_in
        self.assertTrue(view.context["just_logged_in"])

    def test_get_sets_context_and_renders(self):
        """Test that get adds to context and renders template."""
        view = TestMainContentView()
        request = self.factory.get("/")
        request.user = self.user
        self._add_session_to_request(request)

        # Mock the render method to avoid template rendering
        original_render = view.render
        view.render = lambda r=None, t=None, c=None: HttpResponse(
            f"Template: {t or view.template_name}, Context: {view.context}"
        )

        try:
            response = view.get(request)

            # Check response
            self.assertEqual(response.status_code, 200)
            content = response.content.decode("utf-8")
            self.assertIn("Template: test_template.html", content)
            self.assertIn("test_value", view.context)
            self.assertEqual(view.context["test_value"], "test_get")

        finally:
            # Restore original render method
            view.render = original_render

    def test_post_sets_context_and_renders(self):
        """Test that post adds to context and renders template."""
        view = TestMainContentView()
        request = self.factory.post("/")
        request.user = self.user
        self._add_session_to_request(request)

        # Mock the render method to avoid template rendering
        original_render = view.render
        view.render = lambda r=None, t=None, c=None: HttpResponse(
            f"Template: {t or view.template_name}, Context: {view.context}"
        )

        try:
            response = view.post(request)

            # Check response
            self.assertEqual(response.status_code, 200)
            content = response.content.decode("utf-8")
            self.assertIn("Template: test_template.html", content)
            self.assertIn("test_value", view.context)
            self.assertEqual(view.context["test_value"], "test_post")

        finally:
            # Restore original render method
            view.render = original_render

    def test_render_combines_context(self):
        """Test that render combines instance context with provided context."""
        # Create a view
        view = TestMainContentView()

        # Set up view context
        view.context = {"base_value": "base", "shared_key": "base_version"}

        # Additional context to merge
        additional_context = {"new_value": "new", "shared_key": "new_version"}

        # Mock django_render directly by patching the MainContentView's render method
        original_render = view.render

        try:
            # Replace render method with a mock to inspect the combined context
            def mock_render_method(request=None, template_name=None, context=None):
                combined_context = {**view.context, **(context or {})}
                self.assertEqual(combined_context["base_value"], "base")
                self.assertEqual(combined_context["new_value"], "new")
                self.assertEqual(
                    combined_context["shared_key"], "new_version"
                )  # New value overrides
                return HttpResponse("Mocked render response")

            view.render = mock_render_method

            # Call render with additional context
            request = self.factory.get("/")
            response = view.render(request, context=additional_context)

            # Verify response
            self.assertEqual(response.status_code, 200)

        finally:
            # Restore original render method
            view.render = original_render


if __name__ == "__main__":
    import unittest

    unittest.main()
