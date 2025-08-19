"""Simplified tools for the PydanticAI agent."""

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

from pydantic_ai import RunContext


async def run_python(ctx: RunContext[Any], code: str) -> str:
    """
    Execute Python code and return the output.

    Args:
        ctx: The run context
        code: Python code to execute

    Returns:
        The output from executing the code
    """
    # Create string buffers to capture output
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    # Store original stdout/stderr
    old_stdout = sys.stdout
    old_stderr = sys.stderr

    result_str = ""

    try:
        # Redirect output
        sys.stdout = stdout_buffer
        sys.stderr = stderr_buffer

        # Create a namespace for execution
        namespace = {}

        # Execute the code
        exec(code, namespace)

        # Get the output
        stdout_output = stdout_buffer.getvalue()
        stderr_output = stderr_buffer.getvalue()

        # Build result string
        if stdout_output:
            result_str = stdout_output
        if stderr_output:
            if result_str:
                result_str += "\n"
            result_str += f"Errors:\n{stderr_output}"

        if not result_str:
            result_str = "Code executed successfully with no output."

    except Exception as e:
        result_str = f"Error executing code: {type(e).__name__}: {str(e)}"

    finally:
        # Restore original stdout/stderr
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return result_str
