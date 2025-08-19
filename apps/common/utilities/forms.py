"""
Form utilities and validation helpers.

This module provides utilities for form validation, error handling, and form enhancement.
"""

from typing import Any, Dict, List, Optional, Tuple, Type

from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError
from django.forms import ModelForm
from django.http import HttpRequest

from apps.common.utilities.logger import ValidationError, log_error


class FormValidationMixin:
    """Mixin providing enhanced validation and error handling for Django forms.

    This mixin adds:
    1. Standardized field validation
    2. Consistent error formatting
    3. Request context for validation
    4. Field requirement enforcement
    """

    def __init__(self, *args, **kwargs):
        """Initialize with optional request context."""
        # Extract request if provided, but don't pass it to parent class
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)

    def clean(self):
        """Enhanced clean method with additional validation."""
        cleaned_data = super().clean()

        try:
            # Call custom validation methods if they exist
            if hasattr(self, "validate_form"):
                self.validate_form(cleaned_data)

            # Validate required fields (can be overridden in subclasses)
            if hasattr(self, "required_fields"):
                self._validate_required_fields(cleaned_data)

            return cleaned_data
        except DjangoValidationError as e:
            # Log the validation error
            log_error(e, self.request, level="info")

            # Re-raise as Django's ValidationError for form handling
            raise

    def _validate_required_fields(self, cleaned_data: dict[str, Any]) -> None:
        """Validate that all required fields have values."""
        if not hasattr(self, "required_fields"):
            return

        for field_name in self.required_fields:
            if field_name not in cleaned_data or not cleaned_data[field_name]:
                self.add_error(
                    field_name, f"{field_name.replace('_', ' ').title()} is required"
                )

    def add_form_error(self, message: str) -> None:
        """Add a non-field error with standardized formatting."""
        self.add_error(None, message)

    def get_error_dict(self) -> dict[str, list[str]]:
        """Return a dictionary of field errors in a standardized format."""
        if not hasattr(self, "errors"):
            return {}

        error_dict = {}

        # Process field errors
        for field_name, error_list in self.errors.items():
            if field_name != "__all__":  # Skip non-field errors
                error_dict[field_name] = [str(error) for error in error_list]

        # Process non-field errors
        if "__all__" in self.errors:
            error_dict["non_field_errors"] = [
                str(error) for error in self.errors["__all__"]
            ]

        return error_dict

    def raise_validation_error(self, message: str, code: str = "invalid_form") -> None:
        """Raise a consistent validation error for API handling."""
        raise ValidationError(
            message=message, code=code, field_errors=self.get_error_dict()
        )


class BaseModelForm(FormValidationMixin, ModelForm):
    """Base model form with enhanced validation and error handling.

    This form provides:
    1. Request context for model operations
    2. Standardized validation
    3. Consistent error handling
    4. Clean save/update operations
    """

    def save(self, commit: bool = True) -> Any:
        """Enhanced save method with pre/post processing hooks."""
        instance = self.instance
        is_create = instance.pk is None

        # Pre-save hook for custom logic
        if hasattr(self, "pre_save"):
            instance = self.pre_save(instance, is_create)

        # Call parent save method
        instance = super().save(commit=commit)

        # Post-save hook for custom logic
        if commit and hasattr(self, "post_save"):
            instance = self.post_save(instance, is_create)

        return instance


def clean_form_data(data: dict[str, Any]) -> dict[str, Any]:
    """
    Clean form data for consistent processing.

    This function:
    1. Converts empty strings to None
    2. Strips whitespace from string values
    3. Normalizes boolean values

    Args:
        data: The form data to clean

    Returns:
        Cleaned form data
    """
    cleaned = {}

    for key, value in data.items():
        # Handle empty strings
        if value == "":
            cleaned[key] = None
        # Handle string values
        elif isinstance(value, str):
            cleaned[key] = value.strip()
        # Handle boolean values
        elif isinstance(value, bool):
            cleaned[key] = value
        elif value in ("true", "false", "1", "0", "True", "False"):
            if value in ("true", "1", "True"):
                cleaned[key] = True
            elif value in ("false", "0", "False"):
                cleaned[key] = False
        # Pass through other values unchanged
        else:
            cleaned[key] = value

    return cleaned


def validate_form_data(
    form_class: type[forms.Form],
    data: dict[str, Any],
    request: HttpRequest | None = None,
    instance: Any = None,
) -> tuple[Any, dict[str, Any]]:
    """
    Validate form data using a Django form.

    This function:
    1. Creates a form instance with the provided data
    2. Validates the form
    3. Returns the cleaned data or raises a ValidationError

    Args:
        form_class: The form class to use for validation
        data: The data to validate
        request: Optional request object for context
        instance: Optional instance for model forms

    Returns:
        Tuple containing (instance, cleaned_data)

    Raises:
        ValidationError: If the form validation fails
    """
    # Prepare form kwargs
    form_kwargs = {"data": data, "request": request}

    # Add instance for model forms if provided
    if instance is not None:
        form_kwargs["instance"] = instance

    # Create and validate form
    form = form_class(**form_kwargs)

    if form.is_valid():
        # For model forms, get the instance
        if isinstance(form, ModelForm):
            instance = form.save()
            return instance, form.cleaned_data
        else:
            return None, form.cleaned_data
    else:
        # Convert form errors to a standardized format
        if hasattr(form, "get_error_dict"):
            field_errors = form.get_error_dict()
        else:
            field_errors = {
                field: [str(err) for err in errors]
                for field, errors in form.errors.items()
            }

        # Determine error message
        if form.errors.get("__all__"):
            message = form.errors["__all__"][0]
        else:
            message = "Form validation failed"

        # Raise validation error
        raise ValidationError(message=message, field_errors=field_errors)
