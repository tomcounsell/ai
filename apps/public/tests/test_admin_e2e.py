"""
End-to-end tests for Django admin site login and logout.

This module provides browser-based testing for essential admin
functionality using the Django test client. The async Playwright
browser tests have been removed; use the browser_test_runner for
Playwright-based admin tests instead.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

User = get_user_model()


class AdminSiteTestCase(TestCase):
    """Tests for Django admin site login/logout using the Django test client."""

    def setUp(self):
        """Set up test data with a superuser."""
        # Create a unique admin user for each test
        self.username = f"admin_{uuid.uuid4().hex[:8]}"
        self.password = "securepass123"
        self.admin = User.objects.create_superuser(
            username=self.username,
            email=f"{self.username}@example.com",
            password=self.password,
        )
        self.client = Client()

    def test_admin_login(self):
        """Test that an admin user can log in to the admin site."""
        # Get the admin login URL
        login_url = reverse("admin:login")

        # Try accessing a protected admin page - should redirect to login
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)

        # Login with correct credentials
        response = self.client.post(
            login_url,
            {"username": self.username, "password": self.password, "next": "/admin/"},
            follow=True,
        )

        # Check we're logged in and can access the admin
        self.assertEqual(response.status_code, 200)

        # Check for common admin elements - this project uses Django Unfold
        self.assertContains(response, "Database")  # Admin section title
        self.assertContains(response, "Users")  # Users model in admin

    def test_admin_logout(self):
        """Test that an admin user can log out from the admin site."""
        # First log in
        self.client.login(username=self.username, password=self.password)

        # Check we can access the admin
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)

        # Now log out - Django uses POST for logout
        logout_url = reverse("admin:logout")
        response = self.client.post(logout_url, follow=True)

        # Check we're logged out successfully
        self.assertEqual(response.status_code, 200)

        # Try accessing admin again - should fail or redirect
        response = self.client.get(reverse("admin:index"))
        self.assertNotEqual(response.status_code, 200)
