"""
Tests for the User model and related functionality.
"""

import uuid
from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from ..test_behaviors import TimestampableTest

User = get_user_model()


class UserModelTestCase(TimestampableTest, TestCase):
    """Test cases for the User model."""

    model = User

    def setUp(self):
        """Set up test data."""
        # Generate a unique username to avoid UniqueViolation errors
        unique_username = f"testuser_{uuid.uuid4().hex[:8]}"
        self.user = User.objects.create_user(
            username=unique_username,
            email=f"{unique_username}@example.com",
            password="password123",
            first_name="Test",
            last_name="User",
            phone_number="1234567890",
        )

    def test_user_creation(self):
        """Test that a user can be created with valid data."""
        self.assertTrue(self.user.username.startswith("testuser_"))
        self.assertTrue(self.user.email.endswith("@example.com"))
        self.assertEqual(self.user.first_name, "Test")
        self.assertEqual(self.user.last_name, "User")
        self.assertEqual(self.user.phone_number, "1234567890")
        self.assertFalse(self.user.is_email_verified)
        self.assertFalse(self.user.is_beta_tester)
        self.assertIsNone(self.user.agreed_to_terms_at)

    def test_serialized_property(self):
        """Test the serialized property returns expected data."""
        serialized = self.user.serialized

        self.assertEqual(serialized["username"], self.user.username)
        self.assertEqual(serialized["email"], self.user.email)
        self.assertEqual(serialized["first_name"], "Test")
        self.assertEqual(serialized["last_name"], "User")
        self.assertEqual(serialized["is_staff"], False)
        self.assertEqual(serialized["is_active"], True)

    def test_four_digit_login_code_for_example_email(self):
        """Test that example.com emails return a fixed login code."""
        # Generate a unique username to avoid UniqueViolation errors
        unique_username = f"example_{uuid.uuid4().hex[:8]}"
        example_user = User.objects.create_user(
            username=unique_username,
            email=f"{unique_username}@example.com",
            password="password123",
        )
        self.assertEqual(example_user.four_digit_login_code, "1234")

    def test_four_digit_login_code_for_normal_email(self):
        """Test that non-example.com emails generate a unique login code based on user data."""
        # The login code is generated from user ID, email and last login
        # Let's ensure it's a 4-digit code
        self.user.email = (
            f"{self.user.username}@gmail.com"  # Ensure it's not example.com
        )
        self.user.save()

        login_code = self.user.four_digit_login_code
        self.assertEqual(len(login_code), 4)
        self.assertTrue(login_code.isdigit())

    def test_is_agreed_to_terms_property(self):
        """Test the is_agreed_to_terms property."""
        # Initially should be False
        self.assertFalse(self.user.is_agreed_to_terms)

        # Set a date in the past (before 2019-11-01)
        old_date = timezone.make_aware(datetime(2019, 1, 1))
        self.user.agreed_to_terms_at = old_date
        self.assertFalse(self.user.is_agreed_to_terms)

        # Set a date after 2019-11-01
        new_date = timezone.make_aware(datetime(2020, 1, 1))
        self.user.agreed_to_terms_at = new_date
        self.assertTrue(self.user.is_agreed_to_terms)

    def test_is_agreed_to_terms_setter(self):
        """Test the is_agreed_to_terms setter."""
        # Initially None
        self.assertIsNone(self.user.agreed_to_terms_at)

        # Set to True
        self.user.is_agreed_to_terms = True
        self.assertIsNotNone(self.user.agreed_to_terms_at)
        self.assertTrue(self.user.is_agreed_to_terms)

        # Set to False
        self.user.is_agreed_to_terms = False
        self.assertIsNone(self.user.agreed_to_terms_at)
        self.assertFalse(self.user.is_agreed_to_terms)

    def test_str_method_with_first_name(self):
        """Test string representation with first name."""
        self.assertEqual(str(self.user), "Test User")

    def test_str_method_with_only_first_name(self):
        """Test string representation with only first name."""
        self.user.last_name = ""
        self.user.save()
        self.assertEqual(str(self.user), "Test")

    def test_str_method_with_username(self):
        """Test string representation with username when no first name."""
        self.user.first_name = ""
        self.user.save()
        self.assertEqual(str(self.user), self.user.username)

    def test_str_method_with_verified_email(self):
        """Test string representation with verified email."""
        # Create a new user with email-like username
        email_username = f"email_user_{uuid.uuid4().hex[:8]}@example.com"
        email_user = User.objects.create_user(
            username=email_username,
            email=email_username,
            password="password123",
            is_email_verified=True,
        )

        # User with verified email should show username part
        self.assertEqual(str(email_user), email_username.split("@")[0])

    def test_str_method_with_unverified_email(self):
        """Test string representation with unverified email."""
        # Create a new user with email-like username
        email_username = f"email_user2_{uuid.uuid4().hex[:8]}@example.com"
        email_user = User.objects.create_user(
            username=email_username,
            email=email_username,
            password="password123",
            is_email_verified=False,
        )

        # User with unverified email should show full email with note
        self.assertEqual(str(email_user), f"{email_username} (unverified)")

    def test_str_method_all_options_fallthrough(self):
        """Test string representation when all preferences are empty."""
        # User with empty preferences but with email
        email = "fallback@example.com"

        # Need to create it normally to avoid database constraints
        unique_username = f"fallback_{uuid.uuid4().hex[:8]}"
        user = User.objects.create_user(
            username=unique_username,
            email=email,
            password="password123",
        )

        # Now update all the fields to test the fallback
        user.first_name = ""
        user.last_name = ""
        # Make username match email pattern to trigger email fallback
        user.username = email
        user.is_email_verified = False
        user.save()

        # With unverified email, it should show the email with note
        self.assertEqual(str(user), f"{email} (unverified)")

    def test_str_method_exception_handling(self):
        """Test string representation error handling."""
        # Create a real user
        unique_username = f"error_{uuid.uuid4().hex[:8]}"
        user = User.objects.create_user(
            username=unique_username,
            email=f"{unique_username}@example.com",
            password="password123",
            id=99999,  # Specific ID for testing
        )

        # Use a context manager with patch to simulate an exception during __str__ method
        from unittest import mock

        with mock.patch.object(
            User, "first_name", new_callable=mock.PropertyMock
        ) as mock_first_name:
            # Set up the mock to raise an exception when accessed
            mock_first_name.side_effect = Exception("Test exception")

            # The fallback should use the user ID
            self.assertEqual(str(user), f"User {user.id}")

    def test_initials_property_with_first_and_last_name(self):
        """Test the initials property with first and last name."""
        self.assertEqual(self.user.initials, "TU")

    def test_initials_property_with_only_first_name(self):
        """Test the initials property with only first name."""
        self.user.last_name = ""
        self.user.save()
        self.assertEqual(self.user.initials, "T")

    def test_initials_property_with_unverified_email(self):
        """Test the initials property with unverified email."""
        # Create a new user with email-like username
        email_username = f"email_user3_{uuid.uuid4().hex[:8]}@example.com"
        email_user = User.objects.create_user(
            username=email_username,
            email=email_username,
            password="password123",
            is_email_verified=False,
        )

        # Initials should extract from the part before the parentheses
        self.assertEqual(email_user.initials, "E")  # First letter of the email

    def test_initials_empty_fallback(self):
        """Test the initials property with empty string representation."""
        from unittest import mock

        with mock.patch("apps.common.models.user.User.__str__", return_value=""):
            self.assertEqual(self.user.initials, "U")  # Default fallback
