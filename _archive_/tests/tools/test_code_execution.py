"""Tests for the code execution tool."""

from tools.code_execution import (
    execute_bash,
    execute_code,
    execute_javascript,
    execute_python,
)


class TestCodeExecutionBasic:
    """Basic code execution tests."""

    def test_simple_python_print(self):
        """Test simple Python print statement."""
        result = execute_code("print('hello world')")
        assert "error" not in result
        assert result["stdout"].strip() == "hello world"
        assert result["exit_code"] == 0

    def test_python_arithmetic(self):
        """Test Python arithmetic operations."""
        result = execute_code("print(2 + 3 * 4)")
        assert "error" not in result
        assert result["stdout"].strip() == "14"

    def test_python_with_function(self):
        """Test Python code with function definition."""
        code = """
def add(a, b):
    return a + b
print(add(10, 20))
"""
        result = execute_code(code)
        assert "error" not in result
        assert result["stdout"].strip() == "30"

    def test_python_list_comprehension(self):
        """Test Python list comprehension."""
        result = execute_code("print([x**2 for x in range(5)])")
        assert "error" not in result
        assert "[0, 1, 4, 9, 16]" in result["stdout"]


class TestCodeExecutionConvenience:
    """Test convenience functions."""

    def test_execute_python_function(self):
        """Test execute_python convenience function."""
        result = execute_python("print('test')")
        assert "error" not in result
        assert result["stdout"].strip() == "test"

    def test_execute_bash_function(self):
        """Test execute_bash convenience function."""
        result = execute_bash("echo 'hello bash'")
        assert "error" not in result
        assert "hello bash" in result["stdout"]

    def test_execute_javascript_function(self):
        """Test execute_javascript convenience function."""
        result = execute_javascript("console.log('hello js')")
        # May fail if node not installed
        if "error" not in result or "not installed" not in result.get("error", ""):
            assert result["stdout"].strip() == "hello js"


class TestCodeExecutionErrors:
    """Test error handling in code execution."""

    def test_syntax_error(self):
        """Test handling of syntax errors."""
        result = execute_code("print('missing paren'")
        assert result["exit_code"] != 0
        assert "SyntaxError" in result["stderr"]

    def test_runtime_error(self):
        """Test handling of runtime errors."""
        result = execute_code("x = 1 / 0")
        assert result["exit_code"] != 0
        assert "ZeroDivisionError" in result["stderr"]

    def test_name_error(self):
        """Test handling of undefined variable."""
        result = execute_code("print(undefined_variable)")
        assert result["exit_code"] != 0
        assert "NameError" in result["stderr"]

    def test_empty_code(self):
        """Test empty code returns error."""
        result = execute_code("")
        assert "error" in result
        assert "empty" in result["error"].lower()

    def test_whitespace_only_code(self):
        """Test whitespace-only code returns error."""
        result = execute_code("   \n\t  ")
        assert "error" in result


class TestCodeExecutionTimeout:
    """Test timeout handling."""

    def test_timeout_on_long_running(self):
        """Test that long-running code times out."""
        code = """
import time
time.sleep(10)
print('done')
"""
        result = execute_code(code, timeout_seconds=1)
        assert "error" in result
        assert "timed out" in result["error"].lower()

    def test_custom_timeout_success(self):
        """Test code completes within custom timeout."""
        code = """
import time
time.sleep(0.5)
print('completed')
"""
        result = execute_code(code, timeout_seconds=5)
        assert "error" not in result
        assert "completed" in result["stdout"]


class TestCodeExecutionValidation:
    """Test parameter validation."""

    def test_timeout_clamped_min(self):
        """Test timeout is clamped to minimum."""
        result = execute_code("print('test')", timeout_seconds=-10)
        assert "error" not in result  # Should succeed with clamped value

    def test_timeout_clamped_max(self):
        """Test timeout is clamped to maximum."""
        result = execute_code("print('test')", timeout_seconds=1000)
        assert "error" not in result

    def test_memory_limit_validation(self):
        """Test memory limit parameter is accepted."""
        result = execute_code("print('test')", memory_limit_mb=256)
        assert "error" not in result

    def test_unsupported_language(self):
        """Test unsupported language returns error."""
        result = execute_code("print('test')", language="cobol")
        assert "error" in result
        assert "Unsupported" in result["error"]


class TestCodeExecutionInput:
    """Test stdin input handling."""

    def test_python_with_input(self):
        """Test Python code with stdin input."""
        code = """
data = input()
print(f"Received: {data}")
"""
        result = execute_code(code, input_data="hello input")
        assert "error" not in result
        assert "Received: hello input" in result["stdout"]


class TestCodeExecutionBash:
    """Test Bash code execution."""

    def test_bash_echo(self):
        """Test bash echo command."""
        result = execute_code("echo 'bash test'", language="bash")
        assert "error" not in result
        assert "bash test" in result["stdout"]

    def test_bash_variables(self):
        """Test bash with variables."""
        code = """
NAME="World"
echo "Hello, $NAME!"
"""
        result = execute_code(code, language="bash")
        assert "error" not in result
        assert "Hello, World!" in result["stdout"]

    def test_bash_arithmetic(self):
        """Test bash arithmetic."""
        result = execute_code("echo $((5 + 3))", language="bash")
        assert "error" not in result
        assert "8" in result["stdout"]


class TestCodeExecutionMetadata:
    """Test execution metadata."""

    def test_execution_time_returned(self):
        """Test that execution time is returned."""
        result = execute_code("print('test')")
        assert "execution_time_ms" in result
        assert result["execution_time_ms"] >= 0

    def test_language_returned(self):
        """Test that language is returned."""
        result = execute_code("print('test')")
        assert result["language"] == "python"
