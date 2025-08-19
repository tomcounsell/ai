"""
Tests for navigation functionality.

These tests verify that:
- The active_navigation context processor correctly identifies the active section
- The navbar template correctly applies active state styles
- Navigation works without JavaScript
"""

from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from apps.public.context_processors import active_navigation


class NavigationContextProcessorTestCase(TestCase):
    """Test the active_navigation context processor."""

    def setUp(self):
        """Set up test data and request factory."""
        self.factory = RequestFactory()

    def test_home_page_active(self):
        """Test that the home page is correctly identified as active."""
        request = self.factory.get("/")
        context = active_navigation(request)
        self.assertEqual(context["active_section"], "home")

    def test_todos_page_active(self):
        """Test that the todos page is correctly identified as active."""
        request = self.factory.get("/todos/")
        context = active_navigation(request)
        self.assertEqual(context["active_section"], "todos")

        # Test with a detail page
        request = self.factory.get("/todos/123/")
        context = active_navigation(request)
        self.assertEqual(context["active_section"], "todos")

    def test_teams_page_active(self):
        """Test that the teams page is correctly identified as active."""
        request = self.factory.get("/team/")
        context = active_navigation(request)
        self.assertEqual(context["active_section"], "teams")

        # Test with a detail page
        request = self.factory.get("/team/123/")
        context = active_navigation(request)
        self.assertEqual(context["active_section"], "teams")

    def test_account_page_active(self):
        """Test that the account page is correctly identified as active."""
        request = self.factory.get("/account/")
        context = active_navigation(request)
        self.assertEqual(context["active_section"], "account")

        # Test with settings page
        request = self.factory.get("/account/settings/")
        context = active_navigation(request)
        self.assertEqual(context["active_section"], "account")

    def test_admin_page_active(self):
        """Test that the admin page is correctly identified as active."""
        request = self.factory.get("/admin/")
        context = active_navigation(request)
        self.assertEqual(context["active_section"], "admin")

    def test_other_page_no_active(self):
        """Test that other pages don't have an active section."""
        request = self.factory.get("/some-other-page/")
        context = active_navigation(request)
        self.assertIsNone(context["active_section"])


class NavigationRenderingTestCase(TestCase):
    """Test the rendering of navigation with active state."""

    def setUp(self):
        """Set up test data."""
        # We'll test the navigation without requiring login
        self.client = Client()

    def test_home_page_active_state(self):
        """Test that the home page has the active state in navigation."""
        response = self.client.get(reverse("public:home"))
        html = response.content.decode("utf-8")

        # Check that home link has active class
        self.assertIn("border-slate-500", html)
        self.assertIn("Home", html)
