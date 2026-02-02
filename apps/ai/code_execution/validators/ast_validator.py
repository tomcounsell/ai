"""
Abstract Syntax Tree (AST) validation for security analysis.

This validator analyzes the structure of Python code to detect potentially
dangerous patterns before execution. It examines the parsed AST to identify:
    - Forbidden imports
    - Dangerous function calls (eval, exec, compile)
    - File operations
    - Subprocess execution
    - Network operations
    - Excessive complexity

Key Concept:
    AST analysis examines code STRUCTURE, not runtime behavior. It can detect
    what the code ATTEMPTS to do, but cannot determine if it would succeed.

Limitations:
    - Cannot detect obfuscated malicious code
    - Cannot analyze dynamically constructed strings
    - Can have false positives (legitimate code flagged as dangerous)
    - Should be combined with other security layers

References:
    - Python AST module: https://docs.python.org/3/library/ast.html
    - Bandit security linter: https://github.com/PyCQA/bandit
"""

import ast
from dataclasses import dataclass
from typing import List, Optional, Set

from ..exceptions import ValidationError


@dataclass
class ASTViolation:
    """
    Represents a security violation found in code's AST.

    Attributes:
        violation_type: Category of violation (import, function_call, etc.)
        message: Human-readable description
        line_number: Line where violation occurs
        col_offset: Column offset of violation
        severity: high, medium, or low
        node_type: AST node class name (Import, Call, etc.)
    """

    violation_type: str
    message: str
    line_number: int
    col_offset: int = 0
    severity: str = "high"
    node_type: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "violation_type": self.violation_type,
            "message": self.message,
            "line_number": self.line_number,
            "col_offset": self.col_offset,
            "severity": self.severity,
            "node_type": self.node_type,
        }


class ASTValidator:
    """
    Validates code by analyzing its Abstract Syntax Tree.

    This validator walks through the parsed AST looking for dangerous patterns.
    It's more sophisticated than simple string matching because it understands
    Python's structure.

    Example Detections:
        - Direct imports: import os
        - From imports: from subprocess import run
        - Function calls: eval("malicious")
        - Attribute access: __import__('os')

    Usage:
        >>> validator = ASTValidator()
        >>> violations = validator.validate("import os")
        >>> for v in violations:
        ...     print(f"{v.severity}: {v.message} on line {v.line_number}")

    Configuration:
        Customize forbidden imports and functions by passing sets to __init__:
        >>> validator = ASTValidator(
        ...     forbidden_imports={'os', 'sys'},
        ...     forbidden_functions={'eval', 'exec'}
        ... )
    """

    # Default sets of forbidden patterns
    DEFAULT_FORBIDDEN_IMPORTS = {
        # System access
        "os",
        "sys",
        "pathlib",
        "subprocess",
        "shutil",
        "glob",
        # Network
        "socket",
        "urllib",
        "requests",
        "http",
        "httpx",
        "ftplib",
        "smtplib",
        # Database
        "sqlite3",
        "dbm",
        "shelve",
        # Serialization
        "pickle",
        "marshal",
        "dill",
        # Concurrency
        "threading",
        "multiprocessing",
        "asyncio",
        # Dynamic code
        "importlib",
        "imp",
        "runpy",
        # File I/O
        "io",
        "tempfile",
        # Other
        "ctypes",
        "gc",
        "inspect",
        "code",
    }

    DEFAULT_FORBIDDEN_FUNCTIONS = {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "getattr",  # Can be used for obfuscation
        "setattr",  # Can modify object attributes
        "delattr",  # Can delete object attributes
    }

    def __init__(
        self,
        forbidden_imports: Optional[Set[str]] = None,
        forbidden_functions: Optional[Set[str]] = None,
        max_operations: int = 10000,
        allow_getattr: bool = True,  # Less strict by default
        allow_setattr: bool = False,
    ):
        """
        Initialize AST validator with configuration.

        Args:
            forbidden_imports: Set of module names that cannot be imported
            forbidden_functions: Set of function names that cannot be called
            max_operations: Maximum number of AST nodes allowed
            allow_getattr: Whether to allow getattr() (useful for data access)
            allow_setattr: Whether to allow setattr() (rarely needed)
        """
        self.forbidden_imports = (
            forbidden_imports or self.DEFAULT_FORBIDDEN_IMPORTS.copy()
        )
        self.forbidden_functions = (
            forbidden_functions or self.DEFAULT_FORBIDDEN_FUNCTIONS.copy()
        )
        self.max_operations = max_operations

        # Adjust forbidden functions based on flags
        if allow_getattr and "getattr" in self.forbidden_functions:
            self.forbidden_functions.remove("getattr")
        if allow_setattr and "setattr" in self.forbidden_functions:
            self.forbidden_functions.remove("setattr")

    def validate(self, code: str) -> List[ASTViolation]:
        """
        Analyze code's AST for security violations.

        Args:
            code: Python code to analyze

        Returns:
            List of ASTViolation objects (empty if code is safe)

        Example:
            >>> validator = ASTValidator()
            >>> violations = validator.validate("import os; os.system('ls')")
            >>> assert len(violations) > 0  # Should detect os import
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            # Syntax errors handled by SyntaxValidator
            # Don't duplicate error reporting
            return []

        violations: List[ASTViolation] = []

        # Check complexity first
        node_count = sum(1 for _ in ast.walk(tree))
        if node_count > self.max_operations:
            violations.append(
                ASTViolation(
                    violation_type="complexity",
                    message=f"Code too complex: {node_count} operations (max: {self.max_operations})",
                    line_number=1,
                    severity="medium",
                )
            )

        # Walk the AST looking for violations
        for node in ast.walk(tree):
            # Check imports
            if isinstance(node, ast.Import):
                violations.extend(self._check_import(node))
            elif isinstance(node, ast.ImportFrom):
                violations.extend(self._check_import_from(node))

            # Check function calls
            elif isinstance(node, ast.Call):
                violations.extend(self._check_call(node))

            # Check attribute access (for __import__, etc.)
            elif isinstance(node, ast.Attribute):
                violations.extend(self._check_attribute(node))

        return violations

    def validate_or_raise(self, code: str) -> None:
        """
        Validate code and raise ValidationError if violations found.

        Args:
            code: Python code to validate

        Raises:
            ValidationError: If any security violations are detected

        Example:
            >>> validator = ASTValidator()
            >>> validator.validate_or_raise("print('safe')")  # OK
            >>> validator.validate_or_raise("import os")  # Raises
        """
        violations = self.validate(code)
        if violations:
            # Group violations by severity
            high = [v for v in violations if v.severity == "high"]
            medium = [v for v in violations if v.severity == "medium"]
            low = [v for v in violations if v.severity == "low"]

            # Build error message
            messages = []
            if high:
                messages.append(
                    f"{len(high)} high-severity violations: {high[0].message}"
                )
            if medium:
                messages.append(f"{len(medium)} medium-severity violations")
            if low:
                messages.append(f"{len(low)} low-severity violations")

            raise ValidationError(
                "Security violations detected: " + "; ".join(messages),
                details={
                    "violations": [v.to_dict() for v in violations],
                    "high_severity_count": len(high),
                    "medium_severity_count": len(medium),
                    "low_severity_count": len(low),
                },
            )

    def _check_import(self, node: ast.Import) -> List[ASTViolation]:
        """Check import statements for forbidden modules."""
        violations = []
        for alias in node.names:
            module_name = alias.name.split(".")[0]  # Get root module
            if module_name in self.forbidden_imports:
                violations.append(
                    ASTViolation(
                        violation_type="forbidden_import",
                        message=f"Import of '{alias.name}' is not allowed",
                        line_number=node.lineno,
                        col_offset=node.col_offset,
                        severity="high",
                        node_type="Import",
                    )
                )
        return violations

    def _check_import_from(self, node: ast.ImportFrom) -> List[ASTViolation]:
        """Check from...import statements for forbidden modules."""
        violations = []
        if node.module:
            module_name = node.module.split(".")[0]
            if module_name in self.forbidden_imports:
                violations.append(
                    ASTViolation(
                        violation_type="forbidden_import",
                        message=f"Import from '{node.module}' is not allowed",
                        line_number=node.lineno,
                        col_offset=node.col_offset,
                        severity="high",
                        node_type="ImportFrom",
                    )
                )
        return violations

    def _check_call(self, node: ast.Call) -> List[ASTViolation]:
        """Check function calls for forbidden functions."""
        violations = []

        # Get function name
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name and func_name in self.forbidden_functions:
            violations.append(
                ASTViolation(
                    violation_type="forbidden_function",
                    message=f"Call to '{func_name}()' is not allowed",
                    line_number=node.lineno,
                    col_offset=node.col_offset,
                    severity="high",
                    node_type="Call",
                )
            )

        return violations

    def _check_attribute(self, node: ast.Attribute) -> List[ASTViolation]:
        """Check attribute access for dangerous patterns."""
        violations = []

        # Check for dunder attributes often used in escapes
        dangerous_attrs = {
            "__import__",
            "__builtins__",
            "__globals__",
            "__code__",
            "__subclasses__",
        }

        if node.attr in dangerous_attrs:
            violations.append(
                ASTViolation(
                    violation_type="dangerous_attribute",
                    message=f"Access to '{node.attr}' is not allowed",
                    line_number=node.lineno,
                    col_offset=node.col_offset,
                    severity="high",
                    node_type="Attribute",
                )
            )

        return violations
