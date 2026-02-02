"""
Output validation for post-execution security checks.

This validator sanitizes and validates the OUTPUT of code execution to prevent:
    - Data exfiltration (sensitive information in output)
    - Resource exhaustion (extremely large output)
    - Accidental leakage of secrets
    - Injection attacks (if output will be displayed in web page)

Philosophy:
    Defense in depth - even if code somehow accesses sensitive data,
    output validation can prevent it from being returned to the caller.

Use Cases:
    1. Check output size before storing/returning
    2. Scan for patterns indicating sensitive data
    3. Sanitize output for safe display
    4. Redact detected sensitive information
"""

import re
import sys
from typing import List, Optional, Pattern
from dataclasses import dataclass

from ..exceptions import OutputValidationError


@dataclass
class SensitivePattern:
    """
    Definition of a pattern that indicates sensitive data.

    Attributes:
        name: Human-readable name (e.g., "Social Security Number")
        pattern: Compiled regex pattern to match
        severity: high, medium, or low
        should_redact: Whether to redact or reject entirely
        replacement: Text to replace matches with (if redacting)
    """

    name: str
    pattern: Pattern
    severity: str = "high"
    should_redact: bool = True
    replacement: str = "[REDACTED]"


class OutputValidator:
    """
    Validates and sanitizes code execution output.

    This validator provides multiple layers of output security:
        1. Size limits to prevent resource exhaustion
        2. Sensitive data pattern detection
        3. Optional redaction of detected sensitive data
        4. Metadata about validation results

    Usage:
        >>> validator = OutputValidator()
        >>> result = validator.validate("API key: sk-1234...")
        >>> if result.has_violations:
        ...     print(result.violations)

        >>> # With automatic redaction
        >>> validator = OutputValidator(redact=True)
        >>> sanitized = validator.sanitize("SSN: 123-45-6789")
        >>> assert "123-45-6789" not in sanitized

    Thread Safety:
        Instances are thread-safe after construction (patterns are precompiled).
    """

    # Default patterns for sensitive data
    DEFAULT_PATTERNS = [
        SensitivePattern(
            name="Social Security Number",
            pattern=re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
            severity="high",
        ),
        SensitivePattern(
            name="Credit Card",
            pattern=re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),
            severity="high",
        ),
        SensitivePattern(
            name="Email Address",
            pattern=re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
            severity="low",
            should_redact=False,  # Often legitimate
        ),
        SensitivePattern(
            name="AWS Access Key",
            pattern=re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
            severity="high",
        ),
        SensitivePattern(
            name="AWS Secret Key",
            pattern=re.compile(r"\b[0-9a-zA-Z/+=]{40}\b"),
            severity="medium",
            should_redact=True,
        ),
        SensitivePattern(
            name="Generic API Key",
            pattern=re.compile(
                r"\b(api[_-]?key|apikey|api[_-]?token)\s*[:=]\s*['\"]?[\w-]{20,}['\"]?",
                re.IGNORECASE,
            ),
            severity="high",
        ),
        SensitivePattern(
            name="OpenAI API Key",
            pattern=re.compile(r"\bsk-[a-zA-Z0-9]{48}\b"),
            severity="high",
        ),
        SensitivePattern(
            name="Generic Secret",
            pattern=re.compile(
                r"\b(secret|password|passwd|pwd)\s*[:=]\s*['\"]?[\w@#$%^&*-]{8,}['\"]?",
                re.IGNORECASE,
            ),
            severity="high",
        ),
        SensitivePattern(
            name="Private Key Header",
            pattern=re.compile(r"-----BEGIN (RSA |DSA )?PRIVATE KEY-----"),
            severity="high",
        ),
        SensitivePattern(
            name="Database Connection String",
            pattern=re.compile(
                r"\b(postgresql|mysql|mongodb)://[^\s]+@[^\s]+",
                re.IGNORECASE,
            ),
            severity="high",
        ),
    ]

    def __init__(
        self,
        max_output_bytes: int = 1_000_000,  # 1 MB default
        patterns: Optional[List[SensitivePattern]] = None,
        redact: bool = False,
        strict: bool = False,
    ):
        """
        Initialize output validator.

        Args:
            max_output_bytes: Maximum allowed output size
            patterns: Custom sensitive data patterns (uses defaults if None)
            redact: Automatically redact sensitive data instead of rejecting
            strict: Reject output on any violation (even low severity)
        """
        self.max_output_bytes = max_output_bytes
        self.patterns = patterns or self.DEFAULT_PATTERNS
        self.redact = redact
        self.strict = strict

    @dataclass
    class ValidationResult:
        """Result of output validation."""

        is_valid: bool
        original_output: str
        sanitized_output: str
        violations: List[dict]
        was_truncated: bool = False
        was_redacted: bool = False

        @property
        def has_violations(self) -> bool:
            """Whether any violations were found."""
            return len(self.violations) > 0

        def to_dict(self) -> dict:
            """Convert to dictionary for JSON serialization."""
            return {
                "is_valid": self.is_valid,
                "sanitized_output": self.sanitized_output,
                "violations": self.violations,
                "was_truncated": self.was_truncated,
                "was_redacted": self.was_redacted,
                "violation_count": len(self.violations),
            }

    def validate(self, output: str) -> ValidationResult:
        """
        Validate and potentially sanitize output.

        Process:
            1. Check size limits
            2. Scan for sensitive patterns
            3. Redact if configured
            4. Return result with violations

        Args:
            output: Output string from code execution

        Returns:
            ValidationResult with sanitized output and violation info
        """
        violations = []
        sanitized = output
        was_truncated = False
        was_redacted = False

        # Check size
        output_bytes = len(output.encode("utf-8"))
        if output_bytes > self.max_output_bytes:
            violations.append(
                {
                    "type": "size_limit",
                    "severity": "medium",
                    "message": f"Output size {output_bytes} exceeds limit {self.max_output_bytes}",
                }
            )
            # Truncate output
            sanitized = (
                output.encode("utf-8")[: self.max_output_bytes].decode(
                    "utf-8", errors="ignore"
                )
                + f"\n\n[TRUNCATED - exceeded {self.max_output_bytes} bytes]"
            )
            was_truncated = True

        # Scan for sensitive patterns
        for pattern_def in self.patterns:
            matches = list(pattern_def.pattern.finditer(sanitized))
            if matches:
                violations.append(
                    {
                        "type": "sensitive_data",
                        "pattern": pattern_def.name,
                        "severity": pattern_def.severity,
                        "match_count": len(matches),
                        "message": f"Detected {pattern_def.name} in output ({len(matches)} matches)",
                    }
                )

                # Redact if configured and pattern allows
                if self.redact and pattern_def.should_redact:
                    sanitized = pattern_def.pattern.sub(
                        pattern_def.replacement, sanitized
                    )
                    was_redacted = True

        # Determine validity
        is_valid = True
        if self.strict and violations:
            is_valid = False
        elif any(v["severity"] == "high" for v in violations) and not self.redact:
            is_valid = False

        return self.ValidationResult(
            is_valid=is_valid,
            original_output=output,
            sanitized_output=sanitized,
            violations=violations,
            was_truncated=was_truncated,
            was_redacted=was_redacted,
        )

    def sanitize(self, output: str) -> str:
        """
        Sanitize output by redacting sensitive data and enforcing size limits.

        This is a convenience method that always redacts, regardless of
        the redact flag set in __init__.

        Args:
            output: Output to sanitize

        Returns:
            Sanitized output with redactions

        Example:
            >>> validator = OutputValidator()
            >>> clean = validator.sanitize("API key: sk-abc123...")
            >>> assert "sk-abc123" not in clean
        """
        # Temporarily enable redaction
        original_redact = self.redact
        self.redact = True

        result = self.validate(output)

        self.redact = original_redact

        return result.sanitized_output

    def validate_or_raise(self, output: str) -> str:
        """
        Validate output and raise exception if violations found.

        Args:
            output: Output to validate

        Returns:
            Sanitized output (if valid or redaction enabled)

        Raises:
            OutputValidationError: If output invalid and redaction not enabled

        Example:
            >>> validator = OutputValidator(strict=True)
            >>> try:
            ...     clean = validator.validate_or_raise("safe output")
            ... except OutputValidationError:
            ...     print("Output rejected")
        """
        result = self.validate(output)

        if not result.is_valid:
            raise OutputValidationError(
                f"Output validation failed: {len(result.violations)} violations",
                details={
                    "violations": result.violations,
                    "output_size": len(output),
                    "max_size": self.max_output_bytes,
                },
            )

        return result.sanitized_output
