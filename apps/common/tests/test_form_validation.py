"""
Tests for form validation utilities.
"""

from django.test import RequestFactory, TestCase

from apps.common.forms.blog_post import BlogPostForm
from apps.common.models import BlogPost, User
from apps.common.utilities.forms import BaseModelForm, clean_form_data


# Test form for validation utilities
class TestUserForm(BaseModelForm):
    """Test form for validating FormValidationMixin."""

    required_fields = ["username", "email"]

    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name"]

    def validate_form(self, cleaned_data):
        # Custom validation logic
        if cleaned_data.get("username") == "admin":
            self.add_error("username", "This username is reserved")

        # Email must contain @ character
        email = cleaned_data.get("email", "")
        if email and "@" not in email:
            self.add_error("email", "Enter a valid email address.")


class FormValidationTestCase(TestCase):
    """Tests for form validation utilities."""

    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.user = User.objects.create_user(
            username="testuser", email="user@example.com", password="password"
        )

    def test_form_validation_mixin(self):
        """Test the FormValidationMixin for required fields."""
        # Test with invalid data (missing required fields)
        form = TestUserForm(data={}, request=self.request)
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)
        self.assertIn("email", form.errors)

        # Test with valid data
        form = TestUserForm(
            data={"username": "newuser", "email": "new@example.com"},
            request=self.request,
        )
        self.assertTrue(form.is_valid())

    def test_custom_validation(self):
        """Test custom validation logic in forms."""
        # Test reserved username
        form = TestUserForm(data={"username": "admin", "email": "admin@example.com"})
        self.assertFalse(form.is_valid())
        self.assertIn("This username is reserved", form.errors["username"])

        # Test invalid email format
        form = TestUserForm(data={"username": "user2", "email": "invalid-email"})
        self.assertFalse(form.is_valid())
        self.assertIn("Enter a valid email address.", form.errors["email"])

    def test_get_error_dict(self):
        """Test getting a standardized error dictionary."""
        form = TestUserForm(data={"username": "admin"})
        form.is_valid()  # Triggers validation

        error_dict = form.get_error_dict()
        self.assertIn("username", error_dict)
        self.assertIn("email", error_dict)
        self.assertIn("This username is reserved", error_dict["username"])

    def test_clean_form_data(self):
        """Test form data cleaning utility."""
        data = {
            "name": " Test User ",
            "email": "",
            "active": "true",
            "inactive": False,  # Use Python boolean instead of string
            "count": 5,
        }

        cleaned = clean_form_data(data)

        self.assertEqual(cleaned["name"], "Test User")  # Whitespace stripped
        self.assertIsNone(cleaned["email"])  # Empty string converted to None
        self.assertTrue(cleaned["active"])  # String 'true' converted to boolean
        self.assertFalse(cleaned["inactive"])  # String 'false' converted to boolean
        self.assertEqual(cleaned["count"], 5)  # Number unchanged


class BlogPostFormTestCase(TestCase):
    """Tests for the BlogPostForm implementation."""

    def test_required_fields(self):
        """Test required fields validation."""
        form = BlogPostForm(data={})
        self.assertFalse(form.is_valid())
        self.assertIn("title", form.errors)
        self.assertIn("content", form.errors)

    def test_title_length_validation(self):
        """Test title length validation."""
        # Title too short
        form = BlogPostForm(data={"title": "Test", "content": "x" * 100})
        self.assertFalse(form.is_valid())
        self.assertIn("Title must be at least 5 characters long", form.errors["title"])

    def test_content_length_validation(self):
        """Test content length validation."""
        # Content too short
        form = BlogPostForm(data={"title": "Test Title", "content": "Too short"})
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Content must be at least 100 characters long", form.errors["content"]
        )

    def test_subtitle_validation(self):
        """Test subtitle relationship to title validation."""
        # Subtitle longer than title
        form = BlogPostForm(
            data={
                "title": "Short Title",
                "subtitle": "This subtitle is longer than the title and should fail validation",
                "content": "x" * 100,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Subtitle should be shorter than the title", form.errors["subtitle"]
        )

    def test_reading_time_calculation(self):
        """Test automatic reading time calculation in pre_save."""
        # Create form that meets minimum requirements
        form = BlogPostForm(
            data={
                "title": "Test Title That Is Long Enough",
                "content": "x" * 2500,  # Should result in ~2-3 minutes reading time
            }
        )

        # Force form is_valid() to run, but we don't care about the result
        # since we're just testing pre_save
        form.is_valid()

        # Test pre_save functionality
        instance = BlogPost()
        form.instance = instance
        updated_instance = form.pre_save(instance, is_create=True)

        # Should set reading time based on content length
        self.assertIsNotNone(updated_instance.reading_time_minutes)
        self.assertEqual(
            updated_instance.reading_time_minutes, 3
        )  # 2500 chars / 1000 = 2.5 → 3 (rounded up)
