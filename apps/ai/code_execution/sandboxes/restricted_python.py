"""
Restricted Python Sandbox (PROOF OF CONCEPT ONLY)

⚠️  CRITICAL SECURITY WARNING  ⚠️

This sandbox implementation uses PURE PYTHON mechanisms and is NOT SECURE
for production use. It is provided as:
    - A proof of concept for the architecture
    - A development/testing tool
    - An educational reference
    - A fallback for non-critical use cases

SECURITY LIMITATIONS:
    1. Python introspection allows numerous escape techniques
    2. Can be bypassed by determined attackers
    3. Not suitable for executing untrusted code in production
    4. Should NEVER be used with adversarial input

FOR PRODUCTION:
    Use OS-level isolation: E2B, gVisor, Firecracker, or similar.
    See docs/SAFE_LLM_CODE_EXECUTION_ARCHITECTURE.md for details.

What This Implementation Demonstrates:
    - Import restriction pattern
    - Namespace control
    - Resource limiting
    - Output capture
    - Timeout enforcement
    - Structured error handling

These patterns are still valuable as ADDITIONAL layers of defense even
when using OS-level isolation.
"""

import signal
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from typing import Any, Callable

from ..exceptions import (
    ResourceLimitError,
    SecurityViolationError,
)
from ..exceptions import TimeoutError as ExecutionTimeoutError
from .base import BaseSandbox, SandboxConfig, SandboxResult


class RestrictedPythonSandbox(BaseSandbox):
    """
    Pure Python sandbox using restricted builtins and import controls.

    SECURITY LEVEL: Low (Development/Testing Only)

    This implementation demonstrates the core sandboxing patterns but
    should NOT be used for production deployments. It provides:
        - Custom import function blocking dangerous modules
        - Restricted __builtins__ dictionary
        - Pre-imported safe libraries
        - Timeout enforcement (Unix only)
        - Output capture

    Known Escape Vectors (Incomplete List):
        - Access to object.__subclasses__() to find unrestricted classes
        - Use of __import__ alternatives via object introspection
        - Accessing frame objects to escape namespace restrictions
        - Using type() and class hierarchies to access builtins
        - Many others documented in security research

    Appropriate Use Cases:
        ✓ Local development
        ✓ Testing the executor architecture
        ✓ Running code from trusted users only
        ✓ As an ADDITIONAL layer with OS-level isolation

    Inappropriate Use Cases:
        ✗ Production with untrusted code
        ✗ User-facing applications
        ✗ Adversarial scenarios
        ✗ Anything involving sensitive data
    """

    # Modules that pose direct security risks
    BLOCKED_MODULES = {
        # System access
        "os",
        "sys",
        "pathlib",
        "subprocess",
        "shutil",
        "glob",
        # Network access
        "socket",
        "urllib",
        "requests",
        "http",
        "ftplib",
        "smtplib",
        "ssl",
        "httpx",
        # Database access
        "sqlite3",
        "dbm",
        "shelve",
        # Serialization (code execution risk)
        "pickle",
        "marshal",
        # Concurrency
        "threading",
        "multiprocessing",
        "asyncio",
        "concurrent",
        # Dynamic code execution
        "importlib",
        "imp",
        "runpy",
        # File I/O
        "io",
        "tempfile",
        # Other dangerous modules
        "ctypes",
        "gc",
        "inspect",
        "code",
        "codeop",
    }

    def __init__(self):
        """Initialize the restricted Python sandbox."""
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        self._alarm_set = False

    def execute(self, code: str, config: SandboxConfig) -> SandboxResult:
        """
        Execute code in a restricted Python environment.

        Flow:
            1. Validate code syntax
            2. Create restricted global namespace
            3. Set up timeout (Unix only)
            4. Capture stdout/stderr
            5. Execute with exec()
            6. Return results

        Args:
            code: Python code to execute
            config: Sandbox configuration

        Returns:
            SandboxResult with execution outcome
        """
        self._validate_code(code)

        start_time = time.time()
        stdout_capture = StringIO()
        stderr_capture = StringIO()

        try:
            # Create restricted execution environment
            restricted_globals = self._create_restricted_globals(config)

            # Set timeout (Unix systems only)
            self._set_timeout(config.timeout_seconds)

            # Redirect output
            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                # Execute the code
                exec(code, restricted_globals)

            execution_time = time.time() - start_time

            return SandboxResult(
                success=True,
                stdout=self._truncate_output(
                    stdout_capture.getvalue(), config.max_output_bytes
                ),
                stderr=self._truncate_output(
                    stderr_capture.getvalue(), config.max_output_bytes
                ),
                execution_time_seconds=execution_time,
                metadata={
                    "sandbox_type": "RestrictedPython",
                    "security_level": "low",
                    "production_ready": False,
                },
            )

        except ExecutionTimeoutError as e:
            return self._create_error_result(e, time.time() - start_time)

        except ImportError as e:
            # Import was blocked
            error = SecurityViolationError(
                f"Import blocked: {str(e)}",
                violation_type="blocked_import",
                attempted_operation=str(e),
            )
            return self._create_error_result(error, time.time() - start_time)

        except Exception as e:
            return self._create_error_result(e, time.time() - start_time)

        finally:
            self._cancel_timeout()
            # Ensure output streams are restored
            sys.stdout = self._original_stdout
            sys.stderr = self._original_stderr

    def _create_restricted_globals(self, config: SandboxConfig) -> dict[str, Any]:
        """
        Create a restricted global namespace for code execution.

        This namespace includes:
            - Safe built-in functions only
            - Pre-imported safe libraries
            - Custom import function that blocks dangerous modules
            - Execution context data from config

        Security Note:
            While this restricts direct access to dangerous functions,
            it does NOT prevent all escape vectors. Determined attackers
            can still access unrestricted functionality through Python's
            introspection capabilities.

        Args:
            config: Sandbox configuration with allowed imports

        Returns:
            Dictionary to use as globals dict in exec()
        """
        # Safe built-in functions
        safe_builtins = {
            # Type constructors
            "int": int,
            "float": float,
            "str": str,
            "bool": bool,
            "list": list,
            "dict": dict,
            "tuple": tuple,
            "set": set,
            "frozenset": frozenset,
            "bytes": bytes,
            "bytearray": bytearray,
            # Safe operations
            "len": len,
            "range": range,
            "enumerate": enumerate,
            "zip": zip,
            "map": map,
            "filter": filter,
            "min": min,
            "max": max,
            "sum": sum,
            "abs": abs,
            "round": round,
            "pow": pow,
            "divmod": divmod,
            "sorted": sorted,
            "reversed": reversed,
            "any": any,
            "all": all,
            "chr": chr,
            "ord": ord,
            "hex": hex,
            "oct": oct,
            "bin": bin,
            # Essential for error handling
            "Exception": Exception,
            "ValueError": ValueError,
            "TypeError": TypeError,
            "KeyError": KeyError,
            "IndexError": IndexError,
            "AttributeError": AttributeError,
            "RuntimeError": RuntimeError,
            "StopIteration": StopIteration,
            # Safe utilities
            "print": print,
            "isinstance": isinstance,
            "issubclass": issubclass,
            "hasattr": hasattr,
            "getattr": getattr,
            # Custom restricted import
            "__import__": self._create_restricted_import(config.allowed_imports),
            # Explicitly exclude dangerous builtins
            # (not including them is the default, but being explicit for documentation)
            # "open": NOT included
            # "compile": NOT included
            # "eval": NOT included
            # "exec": NOT included
            # "__builtins__": NOT included (using this dict instead)
        }

        # Start with safe builtins
        restricted_globals = {"__builtins__": safe_builtins}

        # Add pre-imported safe modules (only if in allowed list)
        # This is more efficient than importing in the code
        if "math" in config.allowed_imports:
            import math

            restricted_globals["math"] = math

        if "json" in config.allowed_imports:
            import json

            restricted_globals["json"] = json

        if "re" in config.allowed_imports:
            import re

            restricted_globals["re"] = re

        if "datetime" in config.allowed_imports:
            import datetime

            restricted_globals["datetime"] = datetime

        if "collections" in config.allowed_imports:
            import collections

            restricted_globals["collections"] = collections

        # Add execution context
        restricted_globals["context"] = config.execution_context

        return restricted_globals

    def _create_restricted_import(self, allowed_imports: tuple[str, ...]) -> Callable:
        """
        Create a custom __import__ function that blocks dangerous modules.

        This function replaces Python's built-in __import__ to prevent
        importing modules that could be used for malicious purposes.

        Security Note:
            This is just ONE layer of defense. Attackers can bypass this by:
            - Accessing __import__ through object introspection
            - Using importlib via introspection
            - Finding already-imported modules in sys.modules
            - Many other techniques

        Args:
            allowed_imports: Tuple of module names that are permitted

        Returns:
            Custom import function to use as __import__
        """

        def restricted_import(name, *args, **kwargs):
            # Check if module is explicitly blocked
            if name in self.BLOCKED_MODULES:
                raise ImportError(
                    f"Import of '{name}' is not allowed for security reasons"
                )

            # Check if module is in allowed list
            if name not in allowed_imports:
                raise ImportError(
                    f"Import of '{name}' is not in the allowed modules list"
                )

            # Import is allowed
            return __import__(name, *args, **kwargs)

        return restricted_import

    def _set_timeout(self, timeout_seconds: float) -> None:
        """
        Set execution timeout using SIGALRM (Unix only).

        On Unix systems, this uses signal.alarm() to interrupt execution
        after the specified timeout. On Windows, this is a no-op.

        Security Note:
            - Pure Python code in tight loops may not respond to signals
            - C extensions block signal delivery
            - This is not a guaranteed hard limit

        Args:
            timeout_seconds: Maximum execution time
        """
        if not hasattr(signal, "SIGALRM"):
            # Windows or other non-Unix system
            # Timeout enforcement not available
            return

        def timeout_handler(signum, frame):
            raise ExecutionTimeoutError(
                "Code execution exceeded time limit", timeout_seconds=timeout_seconds
            )

        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(int(timeout_seconds))
        self._alarm_set = True

    def _cancel_timeout(self) -> None:
        """Cancel the timeout alarm if it was set."""
        if self._alarm_set and hasattr(signal, "SIGALRM"):
            signal.alarm(0)
            self._alarm_set = False

    def _truncate_output(self, output: str, max_bytes: int) -> str:
        """
        Truncate output to prevent resource exhaustion.

        Args:
            output: Output string to truncate
            max_bytes: Maximum size in bytes

        Returns:
            Truncated string with warning if truncated
        """
        if len(output.encode("utf-8")) > max_bytes:
            # Calculate how many characters fit in max_bytes
            truncated = output.encode("utf-8")[:max_bytes].decode(
                "utf-8", errors="ignore"
            )
            return f"{truncated}\n\n[OUTPUT TRUNCATED - exceeded {max_bytes} bytes]"
        return output

    def cleanup(self) -> None:
        """Clean up any remaining resources."""
        self._cancel_timeout()
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr
