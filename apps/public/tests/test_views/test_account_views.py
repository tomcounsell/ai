"""
Tests for account-related views in the public app.

These tests verify that the account-related views:
- Render the correct templates
- Handle form submissions appropriately
- Enforce proper authentication
- Manage sessions correctly
"""

import uuid

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.common.tests.factories import UserFactory

User = get_user_model()


class AccountViewsTestCase(TestCase):
    """Tests for account-related views."""

    def setUp(self):
        """Set up test data and utilities."""
        self.factory = RequestFactory()
        self.username = f"testuser_{uuid.uuid4().hex[:8]}"
        self.password = "testpassword123"
        self.email = f"{self.username}@example.com"
        self.user = UserFactory.create(
            username=self.username, email=self.email, password=self.password
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

    def test_login_view_get(self):
        """Test that login view renders correctly."""
        url = reverse("login")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/login.html")

    def test_login_view_post_valid(self):
        """Test successful login."""
        url = reverse("login")
        response = self.client.post(
            url, {"username": self.username, "password": self.password}
        )

        # Should redirect after successful login
        self.assertEqual(response.status_code, 302)

        # Check user is authenticated
        user = response.wsgi_request.user
        self.assertTrue(user.is_authenticated)

    def test_login_view_post_invalid(self):
        """Test login with invalid credentials."""
        url = reverse("login")
        response = self.client.post(
            url, {"username": self.username, "password": "wrongpassword"}
        )

        # Should stay on the same page with form errors
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/login.html")
        self.assertIn("form", response.context)
        self.assertTrue(response.context["form"].errors)

    def test_settings_view_unauthenticated(self):
        """Test that settings view requires login."""
        url = reverse("public:account-settings")
        response = self.client.get(url)

        # Should redirect to login page
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_settings_view_authenticated(self):
        """Test that authenticated users can access settings."""
        self.client.login(username=self.username, password=self.password)
        url = reverse("public:account-settings")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/settings.html")
        self.assertIn("user_form", response.context)
        self.assertIn("password_form", response.context)

    def test_settings_view_update_user(self):
        """Test updating user settings."""
        self.client.login(username=self.username, password=self.password)
        url = reverse("public:account-settings")

        response = self.client.post(
            url,
            {
                "first_name": "Updated",
                "last_name": "User",
                "email": "testuser@example.com",  # Keep the same email
            },
        )

        self.assertEqual(response.status_code, 200)

        # Refresh user from database
        self.user.refresh_from_db()

        # Check fields were updated
        self.assertEqual(self.user.first_name, "Updated")
        self.assertEqual(self.user.last_name, "User")

    def test_settings_view_password_change(self):
        """Test changing password."""
        self.client.login(username=self.username, password=self.password)
        url = reverse("public:account-settings")

        # This would need the actual PasswordChangeForm fields,
        # which requires the old password and new password twice
        response = self.client.post(
            url,
            {
                "old_password": "testpassword123",
                "new_password1": "newpassword456",
                "new_password2": "newpassword456",
            },
        )

        self.assertEqual(response.status_code, 200)

        # Verify password was changed by logging in with new password
        self.client.logout()
        login_success = self.client.login(
            username=self.username, password="newpassword456"
        )
        self.assertTrue(login_success)

    def test_home_view_unauthenticated(self):
        """Test that home view requires login."""
        url = reverse("public:home")
        response = self.client.get(url)

        # Should redirect to login page
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_home_view_authenticated(self):
        """Test that authenticated users can access home."""
        self.client.login(username=self.username, password=self.password)
        url = reverse("public:home")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/home.html")

    def test_logout_functionality(self):
        """Test that users can properly logout."""
        # First login
        self.client.login(username=self.username, password=self.password)

        # Verify login status
        home_url = reverse("public:home")
        response = self.client.get(home_url)
        self.assertEqual(response.status_code, 200)

        # Then logout - use the client directly to log out
        self.client.logout()

        # Verify user is now logged out
        response = self.client.get(home_url)
        self.assertEqual(response.status_code, 302)  # Should redirect to login page
        self.assertIn("login", response.url)
