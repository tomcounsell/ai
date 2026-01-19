"""
Integration tests for code-execution tool.

Run with: pytest tools/code-execution/tests/ -v
"""

import pytest

from tools.code_execution import (
    execute_code,
    execute_python,
    execute_javascript,
    execute_bash,
)


class TestCodeExecutionInstallation:
    """Verify tool is properly configured."""

    def test_import(self):
        """Tool can be imported."""
        from tools.code_execution import execute_code
        assert callable(execute_code)


class TestCodeExecutionValidation:
    """Test input validation."""

    def test_empty_code(self):
        """Empty code returns error."""
        result = execute_code("")
        assert "error" in result

    def test_whitespace_code(self):
        """Whitespace-only code returns error."""
        result = execute_code("   ")
        assert "error" in result

    def test_invalid_language(self):
        """Invalid language returns error."""
        result = execute_code("print('hello')", language="cobol")
        assert "error" in result


class TestPythonExecution:
    """Test Python code execution."""

    def test_simple_print(self):
        """Simple print statement works."""
        result = execute_code("print('hello world')")

        assert "error" not in result, f"Execution failed: {result.get('error')}"
        assert result["exit_code"] == 0
        assert "hello world" in result["stdout"]

    def test_with_input(self):
        """Code with input works."""
        result = execute_code(
            "name = input(); print(f'Hello, {name}!')",
            input_data="Claude"
        )

        assert "error" not in result, f"Execution failed: {result.get('error')}"
        assert result["exit_code"] == 0
        assert "Hello, Claude!" in result["stdout"]

    def test_math_calculation(self):
        """Math calculations work."""
        result = execute_code("print(2 + 2)")

        assert "error" not in result
        assert result["exit_code"] == 0
        assert "4" in result["stdout"]

    def test_import_stdlib(self):
        """Standard library imports work."""
        result = execute_code("import json; print(json.dumps({'a': 1}))")

        assert "error" not in result
        assert result["exit_code"] == 0
        assert '"a": 1' in result["stdout"]

    def test_syntax_error(self):
        """Syntax errors are captured."""
        result = execute_code("print('unclosed")

        assert result["exit_code"] != 0
        assert result["stderr"]

    def test_runtime_error(self):
        """Runtime errors are captured."""
        result = execute_code("raise ValueError('test error')")

        assert result["exit_code"] != 0
        assert "ValueError" in result["stderr"]

    def test_timeout(self):
        """Long-running code times out."""
        result = execute_code(
            "import time; time.sleep(10)",
            timeout_seconds=1
        )

        assert "error" in result
        assert "timeout" in result["error"].lower()

    def test_execution_time_tracked(self):
        """Execution time is tracked."""
        result = execute_code("print('fast')")

        assert "error" not in result
        assert "execution_time_ms" in result
        assert result["execution_time_ms"] >= 0


class TestBashExecution:
    """Test Bash code execution."""

    def test_simple_echo(self):
        """Simple echo works."""
        result = execute_code("echo 'hello bash'", language="bash")

        assert "error" not in result, f"Execution failed: {result.get('error')}"
        assert result["exit_code"] == 0
        assert "hello bash" in result["stdout"]

    def test_command_sequence(self):
        """Multiple commands work."""
        result = execute_code("echo 'line1'; echo 'line2'", language="bash")

        assert "error" not in result
        assert "line1" in result["stdout"]
        assert "line2" in result["stdout"]


class TestJavaScriptExecution:
    """Test JavaScript code execution."""

    def test_simple_log(self):
        """Simple console.log works."""
        result = execute_code("console.log('hello js')", language="javascript")

        # Skip if Node.js not installed
        if "error" in result and "not installed" in result.get("error", ""):
            pytest.skip("Node.js not installed")

        assert "error" not in result, f"Execution failed: {result.get('error')}"
        assert result["exit_code"] == 0
        assert "hello js" in result["stdout"]


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_execute_python(self):
        """execute_python works."""
        result = execute_python("print('test')")
        assert "error" not in result
        assert "test" in result["stdout"]

    def test_execute_bash(self):
        """execute_bash works."""
        result = execute_bash("echo 'test'")
        assert "error" not in result
        assert "test" in result["stdout"]
