"""
Tests for error handling and logging utilities.
"""

from django.test import RequestFactory, TestCase

from apps.common.models import User
from apps.common.utilities.logger import (
    AppError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionError,
    ValidationError,
    handle_view_exception,
    log_error,
)


class AppErrorTestCase(TestCase):
    """Tests for the application's custom exception classes."""

    def test_app_error_base_class(self):
        """Test the base AppError class."""
        error = AppError(message="Test error", code="test_error", status_code=400)
        self.assertEqual(error.message, "Test error")
        self.assertEqual(error.code, "test_error")
        self.assertEqual(error.status_code, 400)
        self.assertEqual(error.details, {})

    def test_validation_error(self):
        """Test the ValidationError class."""
        field_errors = {"name": ["This field is required"]}
        error = ValidationError(
            message="Invalid data", code="invalid_data", field_errors=field_errors
        )
        self.assertEqual(error.message, "Invalid data")
        self.assertEqual(error.code, "invalid_data")
        self.assertEqual(error.status_code, 400)
        self.assertEqual(error.details["field_errors"], field_errors)

    def test_authentication_error(self):
        """Test the AuthenticationError class."""
        error = AuthenticationError(message="Login failed")
        self.assertEqual(error.message, "Login failed")
        self.assertEqual(error.code, "authentication_error")
        self.assertEqual(error.status_code, 401)

    def test_permission_error(self):
        """Test the PermissionError class."""
        error = PermissionError()
        self.assertEqual(error.message, "Permission denied")
        self.assertEqual(error.code, "permission_error")
        self.assertEqual(error.status_code, 403)

    def test_not_found_error(self):
        """Test the NotFoundError class."""
        error = NotFoundError(message="User not found")
        self.assertEqual(error.message, "User not found")
        self.assertEqual(error.code, "not_found")
        self.assertEqual(error.status_code, 404)

    def test_conflict_error(self):
        """Test the ConflictError class."""
        error = ConflictError(message="Email already in use")
        self.assertEqual(error.message, "Email already in use")
        self.assertEqual(error.code, "conflict")
        self.assertEqual(error.status_code, 409)


class ErrorHandlingTestCase(TestCase):
    """Tests for error handling utilities."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username="testuser", email="user@example.com", password="password"
        )

    def test_log_error(self):
        """Test error logging functionality."""
        request = self.factory.get("/")
        request.user = self.user

        # This just tests that the function runs without error
        # Actual logging output would need to be captured for more detailed testing
        log_error(ValueError("Test error"), request=request, include_traceback=True)

    def test_handle_view_exception_api(self):
        """Test error handling for API requests."""
        request = self.factory.get("/api/users/")
        request.user = self.user
        request.META["HTTP_ACCEPT"] = "application/json"

        # Mock messages framework - not needed for API
        setattr(
            request, "_messages", type("", (), {"add": lambda *args, **kwargs: None})()
        )

        response = handle_view_exception(
            NotFoundError(message="User not found"), request
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Content-Type"], "application/json")

        # Parse JSON response
        import json

        data = json.loads(response.content.decode("utf-8"))

        self.assertEqual(data["error"], "User not found")
        self.assertEqual(data["code"], "not_found")
        self.assertEqual(data["status_code"], 404)
