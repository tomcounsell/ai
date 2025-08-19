"""
Integration tests for HTML rendering in views.

These tests verify that views:
- Render HTML correctly with templates
- Include context variables in rendered output
- Apply correct template blocks
- Properly handle form rendering
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.common.tests.factories import UserFactory

User = get_user_model()


class HTMLRenderingTestCase(TestCase):
    """Integration tests for rendered HTML from views."""

    def setUp(self):
        """Set up test data."""
        self.user = UserFactory.create(
            username="htmluser",
            email="htmluser@example.com",
            password="testpassword123",
        )

    def test_login_page_renders_correctly(self):
        """Test that the login page renders correctly."""
        url = reverse("login")
        response = self.client.get(url)

        # Check status code and template
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/login.html")

        # Check that the HTML contains expected elements
        html = response.content.decode("utf-8")
        self.assertIn("<form", html)
        self.assertIn('name="username"', html)
        self.assertIn('name="password"', html)
        self.assertIn('type="submit"', html)

    def test_settings_page_logged_in(self):
        """Test that the settings page renders correctly when logged in."""
        self.client.login(username="htmluser", password="testpassword123")
        url = reverse("settings")
        response = self.client.get(url)

        # Check status code and template
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/settings.html")

        # Check that the HTML contains expected elements
        html = response.content.decode("utf-8")
        self.assertIn('value="htmluser@example.com"', html)  # Email value
        self.assertIn('name="first_name"', html)
        self.assertIn('name="last_name"', html)
        self.assertIn('name="old_password"', html)  # Password form

    def test_home_page_logged_in(self):
        """Test that the home page renders correctly when logged in."""
        self.client.login(username="htmluser", password="testpassword123")
        url = reverse("home")
        response = self.client.get(url)

        # Check status code and template
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/home.html")

        # Check base template is extended
        self.assertTemplateUsed(response, "_base.html")

    def test_form_error_rendering(self):
        """Test that form errors are properly rendered."""
        url = reverse("login")
        response = self.client.post(
            url, {"username": "nonexistent", "password": "wrongpassword"}
        )

        # Check form errors are rendered
        html = response.content.decode("utf-8")
        self.assertIn("error", html.lower())

    def test_message_rendering(self):
        """Test that messages are properly rendered."""
        self.client.login(username="htmluser", password="testpassword123")
        url = reverse("settings")

        # Make a valid form submission that should generate success message
        response = self.client.post(
            url,
            {
                "first_name": "Updated",
                "last_name": "User",
                "email": "htmluser@example.com",
            },
            follow=True,
        )

        # Check that success message is rendered
        messages = list(response.context["messages"])
        self.assertTrue(len(messages) > 0)
        self.assertEqual(str(messages[0]), "User settings updated.")
