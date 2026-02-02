"""
Code validation components for pre-execution security checks.

This package provides tools for analyzing code BEFORE execution to detect
security issues, syntax errors, and dangerous patterns.

Validators work together as multiple layers of defense:
    1. SyntaxValidator - Basic Python syntax checking
    2. ASTValidator - Abstract Syntax Tree analysis for forbidden patterns
    3. ComplexityValidator - Limit code complexity to prevent resource exhaustion
    4. OutputValidator - Post-execution output sanitization

Philosophy:
    Validation is about REDUCING risk, not eliminating it. No static analysis
    can detect all malicious code. These validators should be used IN ADDITION
    to OS-level sandboxing, not as a replacement.
"""

from .ast_validator import ASTValidator, ASTViolation
from .output_validator import OutputValidator
from .syntax_validator import SyntaxValidator

__all__ = [
    "SyntaxValidator",
    "ASTValidator",
    "ASTViolation",
    "OutputValidator",
]
