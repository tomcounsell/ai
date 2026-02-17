"""
Syntax validation for Python code.

This validator performs basic syntax checking to ensure code is valid Python
before attempting execution. This is the first line of defense.
"""

import ast
from dataclasses import dataclass

from ..exceptions import ValidationError


@dataclass
class SyntaxValidationResult:
    """Result of syntax validation."""

    is_valid: bool
    error_message: str | None = None
    line_number: int | None = None
    offset: int | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "is_valid": self.is_valid,
            "error_message": self.error_message,
            "line_number": self.line_number,
            "offset": self.offset,
        }


class SyntaxValidator:
    """
    Validates Python code syntax using the ast module.

    This validator uses Python's built-in AST parser to check syntax
    without executing the code. It provides detailed error messages
    including line numbers and offsets.

    Usage:
        >>> validator = SyntaxValidator()
        >>> result = validator.validate("print('hello')")
        >>> assert result.is_valid

        >>> result = validator.validate("print('unclosed")
        >>> assert not result.is_valid
        >>> print(result.error_message)  # Shows syntax error details

    Thread Safety:
        This validator is stateless and thread-safe. Multiple threads
        can use the same instance concurrently.
    """

    def validate(self, code: str) -> SyntaxValidationResult:
        """
        Validate Python code syntax.

        Args:
            code: Python code string to validate

        Returns:
            SyntaxValidationResult with validation outcome

        Example:
            >>> validator = SyntaxValidator()
            >>> result = validator.validate("x = 1 + 1")
            >>> if not result.is_valid:
            ...     print(f"Syntax error on line {result.line_number}")
        """
        if not isinstance(code, str):
            return SyntaxValidationResult(
                is_valid=False, error_message="Code must be a string"
            )

        if not code.strip():
            return SyntaxValidationResult(
                is_valid=False, error_message="Code cannot be empty"
            )

        try:
            # Parse the code into an AST
            # This validates syntax without executing
            ast.parse(code)
            return SyntaxValidationResult(is_valid=True)

        except SyntaxError as e:
            return SyntaxValidationResult(
                is_valid=False,
                error_message=str(e),
                line_number=e.lineno,
                offset=e.offset,
            )

        except Exception as e:
            # Unexpected error during parsing
            return SyntaxValidationResult(
                is_valid=False, error_message=f"Unexpected error: {str(e)}"
            )

    def validate_or_raise(self, code: str) -> None:
        """
        Validate code syntax and raise ValidationError if invalid.

        This is a convenience method for use in code that wants to
        handle validation errors via exceptions rather than result objects.

        Args:
            code: Python code to validate

        Raises:
            ValidationError: If code has syntax errors

        Example:
            >>> validator = SyntaxValidator()
            >>> try:
            ...     validator.validate_or_raise("print('hello')")
            ...     print("Code is valid")
            ... except ValidationError as e:
            ...     print(f"Validation failed: {e}")
        """
        result = self.validate(code)
        if not result.is_valid:
            details = {}
            if result.line_number:
                details["line_number"] = result.line_number
            if result.offset:
                details["offset"] = result.offset

            raise ValidationError(
                f"Syntax error: {result.error_message}", details=details
            )
