"""
Test the account settings form layout and styling.
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

User = get_user_model()


class AccountSettingsFormTestCase(TestCase):
    """Test case for the account settings form."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="password123",
            first_name="Test",
            last_name="User",
        )
        self.client = Client()
        self.client.login(username="testuser", password="password123")
        self.url = reverse("account_settings")

    def test_account_settings_form_layout(self):
        """Test that the account settings form has the correct layout."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        # Check that first name field exists
        self.assertContains(response, 'name="first_name"')
        self.assertContains(response, 'id="id_first_name"')

        # Check that last name field exists
        self.assertContains(response, 'name="last_name"')
        self.assertContains(response, 'id="id_last_name"')

        # Check responsive grid layout for inline fields
        self.assertContains(response, "sm:grid-cols-6")
        self.assertContains(response, "sm:col-span-3")

        # Check for appropriate styling classes
        self.assertContains(response, "w-full")
        self.assertContains(response, "rounded-xs")

    def test_form_submission(self):
        """Test that the account settings form can be submitted successfully."""
        data = {
            "first_name": "Updated",
            "last_name": "Name",
            "email": "updated@example.com",
            "update_info": True,  # Simulating the button being clicked
        }
        response = self.client.post(self.url, data, follow=True)
        self.assertEqual(response.status_code, 200)

        # Refresh user from database
        self.user.refresh_from_db()

        # Check that user details were updated
        self.assertEqual(self.user.first_name, "Updated")
        self.assertEqual(self.user.last_name, "Name")
        self.assertEqual(self.user.email, "updated@example.com")
