"""Tools for the PydanticAI agent."""

import ast
import io
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Dict

from pydantic_ai import RunContext, Tool


async def run_python_code(code: str) -> dict[str, Any]:
    """
    Execute Python code and return the results.

    This tool allows the agent to run Python code dynamically,
    which is useful for calculations, data processing, demonstrations,
    and answering questions that benefit from code execution.

    Args:
        code: Python code to execute

    Returns:
        Dictionary containing:
        - success: Whether the code executed successfully
        - output: The stdout output from the code
        - error: Any error messages if execution failed
        - result: The final expression result if applicable
    """
    # Create string buffers to capture output
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    # Store original stdout/stderr
    old_stdout = sys.stdout
    old_stderr = sys.stderr

    result = None
    error = None

    try:
        # Parse the code to separate statements and expressions
        tree = ast.parse(code)

        # Separate the last node if it's an expression
        last_node = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last_node = tree.body.pop()

        # Execute all statements
        if tree.body:
            exec_code = compile(tree, "<string>", "exec")
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                exec(exec_code, {"__name__": "__main__"})

        # Evaluate the last expression if it exists
        if last_node:
            eval_code = compile(ast.Expression(last_node.value), "<string>", "eval")
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                result = eval(eval_code, {"__name__": "__main__"})

    except SyntaxError as e:
        error = f"Syntax Error: {str(e)}"
        stderr_buffer.write(f"Line {e.lineno}: {e.msg}\n")

    except Exception as e:
        error = f"{type(e).__name__}: {str(e)}"
        stderr_buffer.write(traceback.format_exc())

    finally:
        # Restore original stdout/stderr
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    # Get the captured output
    stdout_output = stdout_buffer.getvalue()
    stderr_output = stderr_buffer.getvalue()

    # Build response
    response = {
        "success": error is None,
        "output": stdout_output,
    }

    if error:
        response["error"] = error
        if stderr_output:
            response["stderr"] = stderr_output

    if result is not None:
        try:
            # Try to convert result to string for display
            response["result"] = str(result)
        except:
            response["result"] = repr(result)

    return response


# Create the Python execution tool
python_tool = Tool(
    run_python_code,
    name="run_python",
    description=(
        "Execute Python code and return the results. "
        "Use this for calculations, data processing, demonstrations, "
        "or any task that benefits from code execution."
    ),
)


async def search_web(query: str, num_results: int = 5) -> dict[str, Any]:
    """
    Search the web for information.

    This is a placeholder for a web search tool.
    In production, this would integrate with a search API.

    Args:
        query: The search query
        num_results: Number of results to return

    Returns:
        Search results
    """
    return {
        "query": query,
        "results": [
            {
                "title": f"Result for: {query}",
                "snippet": "This is a placeholder result. Integrate with a real search API for actual results.",
                "url": "https://example.com",
            }
        ],
        "note": "This is a placeholder. Integrate with a real search API for actual web search.",
    }


# Create the web search tool
search_tool = Tool(
    search_web,
    name="search_web",
    description="Search the web for current information and facts.",
)
