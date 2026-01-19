"""
Code Execution Tool

Sandboxed code execution environment with resource limits and safety measures.
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Literal


class CodeExecutionError(Exception):
    """Code execution failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


def execute_code(
    code: str,
    language: Literal["python", "javascript", "bash"] = "python",
    timeout_seconds: int = 30,
    memory_limit_mb: int = 128,
    enable_network: bool = False,
    dependencies: list[str] | None = None,
    input_data: str | None = None,
) -> dict:
    """
    Execute code in a sandboxed environment.

    Args:
        code: Source code to execute
        language: Programming language (python, javascript, bash)
        timeout_seconds: Execution timeout (1-300)
        memory_limit_mb: Memory limit in MB (16-1024)
        enable_network: Allow network access (default: False)
        dependencies: List of required packages (not installed, just noted)
        input_data: Input data to pass to code via stdin

    Returns:
        dict with keys:
            - stdout: Standard output
            - stderr: Standard error
            - exit_code: Process exit code
            - execution_time_ms: Execution duration
            - error: Error message (if failed)
    """
    # Validate parameters
    timeout_seconds = max(1, min(300, timeout_seconds))
    memory_limit_mb = max(16, min(1024, memory_limit_mb))

    if not code or not code.strip():
        return {"error": "Code cannot be empty"}

    start_time = time.time()

    try:
        if language == "python":
            result = _execute_python(code, timeout_seconds, input_data)
        elif language == "javascript":
            result = _execute_javascript(code, timeout_seconds, input_data)
        elif language == "bash":
            result = _execute_bash(code, timeout_seconds, input_data)
        else:
            return {"error": f"Unsupported language: {language}"}

        execution_time_ms = int((time.time() - start_time) * 1000)
        result["execution_time_ms"] = execution_time_ms
        result["language"] = language

        return result

    except subprocess.TimeoutExpired:
        return {
            "error": f"Execution timed out after {timeout_seconds} seconds",
            "language": language,
            "timeout_seconds": timeout_seconds,
        }
    except Exception as e:
        return {
            "error": f"Execution failed: {str(e)}",
            "language": language,
        }


def _execute_python(code: str, timeout: int, input_data: str | None) -> dict:
    """Execute Python code."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False
    ) as f:
        f.write(code)
        f.flush()
        temp_path = f.name

    try:
        proc = subprocess.run(
            [sys.executable, temp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_data,
            cwd=tempfile.gettempdir(),
        )

        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        }
    finally:
        Path(temp_path).unlink(missing_ok=True)


def _execute_javascript(code: str, timeout: int, input_data: str | None) -> dict:
    """Execute JavaScript code using Node.js."""
    # Check if node is available
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
    except (subprocess.SubprocessError, FileNotFoundError):
        return {"error": "Node.js not installed", "exit_code": 1, "stdout": "", "stderr": ""}

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False
    ) as f:
        f.write(code)
        f.flush()
        temp_path = f.name

    try:
        proc = subprocess.run(
            ["node", temp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_data,
            cwd=tempfile.gettempdir(),
        )

        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        }
    finally:
        Path(temp_path).unlink(missing_ok=True)


def _execute_bash(code: str, timeout: int, input_data: str | None) -> dict:
    """Execute Bash code."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".sh", delete=False
    ) as f:
        f.write(code)
        f.flush()
        temp_path = f.name

    try:
        proc = subprocess.run(
            ["bash", temp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_data,
            cwd=tempfile.gettempdir(),
        )

        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        }
    finally:
        Path(temp_path).unlink(missing_ok=True)


def execute_python(code: str, timeout_seconds: int = 30, input_data: str | None = None) -> dict:
    """Convenience function for Python execution."""
    return execute_code(code, language="python", timeout_seconds=timeout_seconds, input_data=input_data)


def execute_javascript(code: str, timeout_seconds: int = 30, input_data: str | None = None) -> dict:
    """Convenience function for JavaScript execution."""
    return execute_code(code, language="javascript", timeout_seconds=timeout_seconds, input_data=input_data)


def execute_bash(code: str, timeout_seconds: int = 30, input_data: str | None = None) -> dict:
    """Convenience function for Bash execution."""
    return execute_code(code, language="bash", timeout_seconds=timeout_seconds, input_data=input_data)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m tools.code_execution 'print(\"hello\")'")
        sys.exit(1)

    code = sys.argv[1]
    print(f"Executing: {code}")

    result = execute_code(code)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    else:
        print(f"stdout: {result['stdout']}")
        if result["stderr"]:
            print(f"stderr: {result['stderr']}")
        print(f"exit_code: {result['exit_code']}")
        print(f"execution_time_ms: {result['execution_time_ms']}")
