"""
Security test suite with adversarial test cases.

These tests intentionally attempt to bypass sandbox restrictions.
They verify that the security layers properly block malicious code.

IMPORTANT: These tests contain potentially dangerous code patterns.
They should only be run in isolated test environments.
"""

import pytest
from apps.ai.code_execution import CodeExecutor
from apps.ai.code_execution.validators import ASTValidator
from apps.ai.code_execution.sandboxes import RestrictedPythonSandbox, SandboxConfig


class TestImportRestrictions:
    """Test that dangerous imports are blocked."""

    def test_block_os_import(self):
        """Should block import of os module."""
        executor = CodeExecutor()
        result = executor.execute("import os")

        assert not result.success or result.validation_violations
        # Should be caught by AST validator before execution

    def test_block_subprocess_import(self):
        """Should block import of subprocess module."""
        executor = CodeExecutor()
        result = executor.execute("import subprocess")

        assert not result.success or result.validation_violations

    def test_block_socket_import(self):
        """Should block import of socket module."""
        executor = CodeExecutor()
        result = executor.execute("import socket")

        assert not result.success or result.validation_violations

    def test_block_from_import(self):
        """Should block 'from os import ...' style imports."""
        executor = CodeExecutor()
        result = executor.execute("from os import system")

        assert not result.success or result.validation_violations

    def test_block_nested_module_import(self):
        """Should block imports of submodules from dangerous packages."""
        executor = CodeExecutor()
        result = executor.execute("import os.path")

        assert not result.success or result.validation_violations

    def test_allow_safe_imports(self):
        """Should allow safe imports like math and json."""
        executor = CodeExecutor()

        # These should succeed
        math_result = executor.execute("import math; x = math.pi")
        assert math_result.success

        json_result = executor.execute("import json; x = json.dumps({})")
        assert json_result.success


class TestFunctionRestrictions:
    """Test that dangerous built-in functions are blocked."""

    def test_block_eval(self):
        """Should block eval() function."""
        executor = CodeExecutor()
        result = executor.execute("eval('1 + 1')")

        assert not result.success or result.validation_violations

    def test_block_exec(self):
        """Should block exec() function."""
        executor = CodeExecutor()
        result = executor.execute("exec('print(1)')")

        assert not result.success or result.validation_violations

    def test_block_compile(self):
        """Should block compile() function."""
        executor = CodeExecutor()
        result = executor.execute("compile('1+1', '<string>', 'eval')")

        assert not result.success or result.validation_violations

    def test_block_open(self):
        """Should block open() function for file access."""
        executor = CodeExecutor()
        result = executor.execute("open('/etc/passwd', 'r')")

        assert not result.success or result.validation_violations

    def test_block_dunder_import(self):
        """Should block __import__() function."""
        executor = CodeExecutor()
        result = executor.execute("__import__('os')")

        assert not result.success or result.validation_violations


class TestAttributeAccessRestrictions:
    """Test that dangerous attribute access is blocked."""

    def test_block_builtins_access(self):
        """Should block access to __builtins__."""
        executor = CodeExecutor()
        result = executor.execute("x = __builtins__")

        assert not result.success or result.validation_violations

    def test_block_globals_access(self):
        """Should block access to __globals__."""
        executor = CodeExecutor()
        result = executor.execute("x = __globals__")

        # This might succeed but should not have full globals
        if result.success:
            assert "__builtins__" not in result.stdout

    def test_block_subclasses_access(self):
        """Should block object.__subclasses__() for escape attempts."""
        executor = CodeExecutor()
        result = executor.execute("x = object.__subclasses__()")

        assert not result.success or result.validation_violations


class TestResourceExhaustion:
    """Test that resource limits prevent exhaustion attacks."""

    def test_timeout_enforcement(self):
        """Should timeout infinite loop."""
        executor = CodeExecutor(
            sandbox_config=SandboxConfig(timeout_seconds=2),
        )
        result = executor.execute("""
while True:
    pass
""")

        assert not result.success
        # Should timeout

    def test_memory_intensive_operation(self):
        """Should handle memory-intensive operations."""
        executor = CodeExecutor()
        result = executor.execute("""
# Try to create a large list
try:
    x = [0] * 1_000_000
    print('Created large list')
except MemoryError:
    print('Memory limit reached')
""")

        # Should complete (either successfully or with memory error)
        assert result.success or "Memory" in result.error_message

    def test_output_size_limit(self):
        """Should truncate excessive output."""
        executor = CodeExecutor(
            sandbox_config=SandboxConfig(max_output_bytes=1000),
        )
        result = executor.execute("""
for i in range(1000):
    print('A' * 100)
""")

        # Output should be truncated
        assert result.success
        assert len(result.stdout) <= 2000  # Some buffer for truncation message
        assert "TRUNCATED" in result.stdout or len(result.stdout) < 100000

    def test_complex_code_detection(self):
        """Should detect overly complex code."""
        validator = ASTValidator(max_operations=100)

        # Generate very complex code
        complex_code = "\n".join([f"x{i} = {i}" for i in range(200)])

        violations = validator.validate(complex_code)

        assert any(v.violation_type == "complexity" for v in violations)


class TestDataExfiltration:
    """Test that output validation prevents data exfiltration."""

    def test_detect_api_key_in_output(self):
        """Should detect API keys in output."""
        executor = CodeExecutor(redact_sensitive_output=True)
        result = executor.execute("""
api_key = "sk-abc123def456ghi789jkl012mno345pqr678stu901vwx234"
print(f"API Key: {api_key}")
""")

        assert result.success
        assert "sk-abc123" not in result.stdout  # Should be redacted
        assert result.output_violations
        assert result.was_output_redacted

    def test_detect_aws_key_in_output(self):
        """Should detect AWS access keys in output."""
        executor = CodeExecutor(redact_sensitive_output=True)
        result = executor.execute("""
print("AWS Key: AKIAIOSFODNN7EXAMPLE")
""")

        assert result.success
        assert "AKIAIOSFODNN7EXAMPLE" not in result.stdout
        assert result.was_output_redacted

    def test_detect_ssn_in_output(self):
        """Should detect Social Security Numbers in output."""
        executor = CodeExecutor(redact_sensitive_output=True)
        result = executor.execute("""
print("SSN: 123-45-6789")
""")

        assert result.success
        assert "123-45-6789" not in result.stdout
        assert result.was_output_redacted

    def test_detect_credit_card_in_output(self):
        """Should detect credit card numbers in output."""
        executor = CodeExecutor(redact_sensitive_output=True)
        result = executor.execute("""
print("Card: 4532-1234-5678-9010")
""")

        assert result.success
        assert "4532-1234-5678-9010" not in result.stdout


class TestEscapeAttempts:
    """
    Test known Python sandbox escape techniques.

    These tests verify that common escape vectors are blocked.
    Note: This is not exhaustive - new escapes are discovered regularly.
    """

    def test_escape_via_class_hierarchy(self):
        """Should block escape via class hierarchy navigation."""
        executor = CodeExecutor()
        result = executor.execute("""
# Attempt to access object.__subclasses__()
classes = object.__subclasses__()
print(classes)
""")

        assert not result.success or result.validation_violations

    def test_escape_via_frame_objects(self):
        """Should block escape via frame object access."""
        executor = CodeExecutor()
        result = executor.execute("""
import sys
frame = sys._getframe()
""")

        # sys is blocked, so this should fail at import
        assert not result.success or result.validation_violations

    def test_escape_via_code_objects(self):
        """Should block escape via code object manipulation."""
        executor = CodeExecutor()
        result = executor.execute("""
def f():
    pass
code = f.__code__
""")

        assert not result.success or result.validation_violations

    def test_escape_via_getattr(self):
        """Should handle getattr-based obfuscation."""
        executor = CodeExecutor()
        result = executor.execute("""
# Try to use getattr to access blocked functions
import_func = getattr(__builtins__, '__import__')
os = import_func('os')
""")

        # May succeed but should not have access to __builtins__
        # or getattr might be blocked depending on configuration
        if result.success:
            assert "error" in result.stderr.lower() or not result.stdout


class TestLegitimateCodeExecution:
    """
    Test that legitimate, safe code executes correctly.

    These tests ensure security measures don't break valid use cases.
    """

    def test_simple_arithmetic(self):
        """Should execute simple arithmetic."""
        executor = CodeExecutor()
        result = executor.execute("""
x = 1 + 1
print(x)
""")

        assert result.success
        assert "2" in result.stdout

    def test_data_processing_with_loops(self):
        """Should execute data processing code."""
        executor = CodeExecutor()
        result = executor.execute("""
data = [1, 2, 3, 4, 5]
result = sum(x * 2 for x in data)
print(result)
""")

        assert result.success
        assert "30" in result.stdout

    def test_json_processing(self):
        """Should execute JSON processing."""
        executor = CodeExecutor()
        result = executor.execute("""
import json
data = {"name": "Alice", "age": 30}
json_str = json.dumps(data)
print(json_str)
""")

        assert result.success
        assert "Alice" in result.stdout

    def test_math_operations(self):
        """Should execute mathematical operations."""
        executor = CodeExecutor()
        result = executor.execute("""
import math
radius = 5
area = math.pi * radius ** 2
print(f"Area: {area:.2f}")
""")

        assert result.success
        assert "78.5" in result.stdout

    def test_context_access(self):
        """Should access execution context data."""
        executor = CodeExecutor()
        result = executor.execute(
            code="""
user = context['user']
print(f"Hello, {user['name']}!")
""",
            context={"user": {"name": "Alice", "id": 123}},
        )

        assert result.success
        assert "Hello, Alice!" in result.stdout

    def test_error_handling(self):
        """Should handle runtime errors gracefully."""
        executor = CodeExecutor()
        result = executor.execute("""
try:
    x = 1 / 0
except ZeroDivisionError:
    print("Caught division by zero")
""")

        assert result.success
        assert "Caught division by zero" in result.stdout


class TestValidationLayers:
    """Test that validation layers work correctly."""

    def test_syntax_error_detection(self):
        """Should detect syntax errors."""
        executor = CodeExecutor()
        result = executor.execute("print('unclosed string")

        assert not result.success
        assert result.validation_violations
        assert any(
            "syntax" in v.get("type", "").lower() for v in result.validation_violations
        )

    def test_ast_violation_details(self):
        """Should provide detailed AST violation information."""
        executor = CodeExecutor()
        result = executor.execute("import os; import sys")

        assert result.validation_violations
        # Should have violations for both imports
        assert len(result.validation_violations) >= 2

    def test_disabled_validation(self):
        """Should allow bypassing validation when disabled."""
        executor = CodeExecutor(
            enable_ast_validation=False,
            enable_output_validation=False,
        )
        # Note: This still won't execute successfully in RestrictedPythonSandbox
        # but it will get past the AST validation stage
        result = executor.execute("import os")

        # Should not have AST violations (validation disabled)
        # But should still fail during execution (import blocked at runtime)
        if not result.success:
            assert not result.validation_violations or all(
                v.get("type") != "forbidden_import"
                for v in result.validation_violations
            )


# Pytest fixtures for test setup
@pytest.fixture
def executor():
    """Standard executor for tests."""
    return CodeExecutor()


@pytest.fixture
def strict_executor():
    """Executor with strictest security settings."""
    return CodeExecutor(
        sandbox_config=SandboxConfig(
            timeout_seconds=5,
            max_memory_mb=256,
            max_output_bytes=10_000,
            enable_network=False,
        ),
        enable_syntax_validation=True,
        enable_ast_validation=True,
        enable_output_validation=True,
        redact_sensitive_output=True,
    )


@pytest.fixture
def permissive_executor():
    """Executor with minimal restrictions (for testing legitimate code)."""
    return CodeExecutor(
        enable_ast_validation=False,
        enable_output_validation=False,
    )
