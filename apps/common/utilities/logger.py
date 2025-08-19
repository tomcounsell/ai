"""
Centralized logging and error handling utilities.

This module provides standardized logging and error handling functions for the entire project.
It ensures consistent error reporting, formatting, and handling across all applications.
"""

import logging
import traceback
from collections.abc import Callable
from functools import wraps
from typing import Any, Dict, List, Optional, Type, TypeVar

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response

# Standard logger for the application
logger = logging.getLogger(__name__)

# Type variables for better type hinting
T = TypeVar("T")
R = TypeVar("R")


class AppError(Exception):
    """Base exception class for application-specific errors.

    Attributes:
        message (str): Human-readable error message
        code (str): Machine-readable error code
        status_code (int): HTTP status code to return
        details (Dict): Additional error details
    """

    def __init__(
        self,
        message: str = "An unexpected error occurred",
        code: str = "error_unknown",
        status_code: int = 500,
        details: dict[str, Any] | None = None,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


class ValidationError(AppError):
    """Exception raised for data validation errors."""

    def __init__(
        self,
        message: str = "Invalid data provided",
        code: str = "validation_error",
        field_errors: dict[str, list[str]] | None = None,
        details: dict[str, Any] | None = None,
    ):
        status_code = 400
        details = details or {}
        if field_errors:
            details["field_errors"] = field_errors
        super().__init__(
            message=message, code=code, status_code=status_code, details=details
        )


class AuthenticationError(AppError):
    """Exception raised for authentication errors."""

    def __init__(
        self,
        message: str = "Authentication failed",
        code: str = "authentication_error",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message=message, code=code, status_code=401, details=details)


class PermissionError(AppError):
    """Exception raised for permission/authorization errors."""

    def __init__(
        self,
        message: str = "Permission denied",
        code: str = "permission_error",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message=message, code=code, status_code=403, details=details)


class NotFoundError(AppError):
    """Exception raised when a requested resource is not found."""

    def __init__(
        self,
        message: str = "Resource not found",
        code: str = "not_found",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message=message, code=code, status_code=404, details=details)


class ConflictError(AppError):
    """Exception raised for resource conflicts (like duplicate entries)."""

    def __init__(
        self,
        message: str = "Resource conflict",
        code: str = "conflict",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message=message, code=code, status_code=409, details=details)


def log_error(
    exc: Exception,
    request: HttpRequest | None = None,
    level: int = logging.ERROR,
    include_traceback: bool = True,
) -> None:
    """Log an exception with consistent formatting and contextual information.

    Args:
        exc: The exception to log
        request: Optional HTTP request that caused the exception
        level: Logging level to use
        include_traceback: Whether to include the traceback in the log
    """
    # Build error message with context
    error_msg = f"Error: {exc.__class__.__name__}: {str(exc)}"

    # Add request information if available
    if request:
        user_info = (
            f"User: {request.user.username}"
            if request.user.is_authenticated
            else "User: Anonymous"
        )
        path_info = f"Path: {request.path}"
        method_info = f"Method: {request.method}"
        error_msg = f"{error_msg}\n{user_info}\n{path_info}\n{method_info}"

    # Add traceback for non-production environments or critical errors
    if include_traceback and (not settings.PRODUCTION or level >= logging.ERROR):
        tb = traceback.format_exc()
        error_msg = f"{error_msg}\nTraceback:\n{tb}"

    # Log the error
    logger.log(level, error_msg)


def handle_view_exception(
    exc: Exception, request: HttpRequest, template_name: str = "error.html"
) -> HttpResponse:
    """Handle exceptions in standard Django views with consistent error responses.

    This function:
    1. Logs the exception with appropriate context
    2. Maps standard exceptions to appropriate HTTP responses
    3. Returns a user-friendly error page or JSON response

    Args:
        exc: The exception to handle
        request: The current request
        template_name: Template to render for HTML responses

    Returns:
        Appropriate HttpResponse with error details
    """
    # Transform common Django exceptions to our application exceptions
    if isinstance(exc, Http404):
        exc = NotFoundError(message="The requested resource was not found")
    elif isinstance(exc, PermissionDenied):
        exc = PermissionError()

    # Get status code and message
    if isinstance(exc, AppError):
        status_code = exc.status_code
        message = exc.message
    else:
        # Generic server error for unexpected exceptions
        status_code = 500
        message = "An unexpected error occurred"
        # In production, don't expose internal error details
        if not settings.DEBUG and not settings.TESTING:
            message = (
                "The server encountered an error and could not complete your request"
            )

    # Log the error appropriately
    log_error(exc, request)

    # Add error message to Django messages framework for HTML responses
    if "text/html" in request.headers.get("accept", ""):
        try:
            message_level = messages.ERROR
            messages.add_message(request, message_level, message)
        except Exception:
            # Ignore message framework errors in tests
            pass

    # Determine the response format based on request
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    accepts_json = "application/json" in request.headers.get("accept", "")
    is_htmx = getattr(request, "htmx", False)

    # For API and AJAX requests, return JSON response
    if is_ajax or accepts_json or request.path.startswith("/api/"):
        error_data = {
            "error": message,
            "status_code": status_code,
        }

        # Include additional details for our custom exceptions
        if isinstance(exc, AppError):
            error_data["code"] = exc.code
            error_data.update(exc.details)

        return JsonResponse(error_data, status=status_code)

    # For HTMX requests, render an error partial
    elif is_htmx:
        htmx_error_template = "components/common/error_message.html"
        context = {
            "error_message": message,
            "error_code": getattr(exc, "code", "server_error"),
            "status_code": status_code,
            "is_htmx": True,
        }
        return render(request, htmx_error_template, context, status=status_code)

    # For regular requests, render a full error page
    else:
        context = {
            "error_message": message,
            "error_code": getattr(exc, "code", "server_error"),
            "status_code": status_code,
        }
        return render(request, template_name, context, status=status_code)


def api_exception_handler(exc: Exception, context: dict) -> Response:
    """DRF exception handler that provides consistent API error responses.

    Args:
        exc: The exception that occurred
        context: The DRF context for the exception

    Returns:
        Response: DRF Response object with error details
    """
    # Map Django and DRF exceptions to our application exceptions
    if isinstance(exc, Http404):
        exc = NotFoundError(message="The requested resource was not found")
    elif isinstance(exc, PermissionDenied):
        exc = PermissionError()

    # Extract request from context
    request = context.get("request")

    # Log the error
    log_error(exc, request)

    # Prepare the error response
    if isinstance(exc, AppError):
        data = {
            "error": exc.message,
            "code": exc.code,
            "status_code": exc.status_code,
        }

        # Include field errors and other details
        if exc.details:
            data.update(exc.details)

        return Response(data, status=exc.status_code)

    # Handle DRF's ValidationError separately
    elif isinstance(exc, APIException):
        data = {
            "error": str(exc.detail) if hasattr(exc, "detail") else str(exc),
            "code": exc.get_codes() if hasattr(exc, "get_codes") else "api_error",
            "status_code": exc.status_code,
        }
        return Response(data, status=exc.status_code)

    # Fallback for unexpected errors
    else:
        # Hide actual error details in production
        if settings.PRODUCTION:
            message = (
                "The server encountered an error and could not complete your request"
            )
        else:
            message = str(exc)

        data = {
            "error": message,
            "code": "server_error",
            "status_code": 500,
        }
        return Response(data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def error_decorator(view_func: Callable[..., T]) -> Callable[..., T]:
    """Decorator to handle exceptions in view functions.

    This decorator:
    1. Catches all exceptions in the view
    2. Logs them appropriately
    3. Returns a standardized error response

    Usage:
        @error_decorator
        def my_view(request):
            # View code that might raise exceptions
            return render(request, 'template.html')

    Args:
        view_func: The view function to decorate

    Returns:
        Wrapped view function with error handling
    """

    @wraps(view_func)
    def wrapped_view(request, *args, **kwargs):
        try:
            return view_func(request, *args, **kwargs)
        except Exception as exc:
            return handle_view_exception(exc, request)

    return wrapped_view


class ErrorHandlingMixin:
    """Mixin for handling errors in class-based views.

    This mixin provides error handling for Django class-based views by
    overriding the dispatch method to catch and handle exceptions.

    Usage:
        class MyView(ErrorHandlingMixin, View):
            def get(self, request):
                # View code that might raise exceptions
                return render(request, 'template.html')
    """

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except Exception as exc:
            return handle_view_exception(exc, request)


def raises_app_error(
    exc_class: type[AppError] = AppError,
    message: str = None,
    code: str = None,
    status_code: int = None,
    details: dict = None,
) -> Callable[[Callable[..., R]], Callable[..., R]]:
    """Decorator to transform exceptions into standardized application errors.

    This decorator:
    1. Catches specified exceptions in a function
    2. Transforms them into AppError exceptions with consistent formatting

    Usage:
        @raises_app_error(exc_class=ValidationError, message="Invalid user data")
        def validate_user(user_data):
            # Function that might raise exceptions
            if not user_data.get('email'):
                raise ValueError("Email is required")

    Args:
        exc_class: The AppError subclass to raise
        message: Custom error message
        code: Custom error code
        status_code: Custom HTTP status code
        details: Additional error details

    Returns:
        Decorated function that transforms exceptions
    """

    def decorator(func):
        @wraps(func)
        def wrapped_func(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except AppError:
                # Pass through our custom exceptions unchanged
                raise
            except Exception as exc:
                # Determine error parameters
                error_msg = message if message is not None else str(exc)
                error_code = code
                error_status = status_code
                error_details = details

                # Create and raise the appropriate application error
                raise exc_class(
                    message=error_msg,
                    code=error_code,
                    status_code=error_status,
                    details=error_details,
                ) from exc

        return wrapped_func

    return decorator
