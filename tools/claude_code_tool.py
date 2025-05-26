"""
Claude Code Tool - Allows spawning new Claude Code sessions with specific prompts and directories.

This tool enables Valor to delegate complex coding tasks to new Claude sessions,
providing directory context and detailed prompts for autonomous execution.
"""

import os
import subprocess


def execute_claude_code(
    prompt: str,
    working_directory: str | None = None,
    allowed_tools: list[str] | None = None,
    timeout: int = 300,
) -> str:
    """
    Execute a Claude Code session with a specific prompt and context.

    Args:
        prompt: Detailed instructions for Claude to execute
        working_directory: Directory to run Claude in (defaults to current)
        allowed_tools: List of tools Claude can use (defaults to common tools)
        timeout: Maximum execution time in seconds

    Returns:
        Claude's output from the execution

    Raises:
        subprocess.CalledProcessError: If Claude execution fails
        FileNotFoundError: If working directory doesn't exist
    """
    # Default allowed tools for coding tasks
    if allowed_tools is None:
        allowed_tools = ["Edit", "Write", "Read", "Bash", "Glob", "Grep", "LS", "MultiEdit", "Task"]

    # Validate working directory
    if working_directory:
        if not os.path.exists(working_directory):
            raise FileNotFoundError(f"Working directory does not exist: {working_directory}")
        if not os.path.isdir(working_directory):
            raise NotADirectoryError(f"Path is not a directory: {working_directory}")

    # Build Claude command
    command = ["claude", "-p", prompt]

    # Add allowed tools
    if allowed_tools:
        command.extend(["--allowedTools"] + allowed_tools)

    # Change to working directory if specified
    original_cwd = os.getcwd()
    try:
        if working_directory:
            os.chdir(working_directory)

        # Execute Claude Code
        process = subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=timeout
        )

        return process.stdout

    except subprocess.TimeoutExpired:
        raise subprocess.TimeoutExpired(
            command, timeout, f"Claude Code execution timed out after {timeout} seconds"
        )
    except subprocess.CalledProcessError as e:
        # Include both stdout and stderr in error for debugging
        error_msg = f"Claude Code failed with exit code {e.returncode}\n"
        if e.stdout:
            error_msg += f"STDOUT: {e.stdout}\n"
        if e.stderr:
            error_msg += f"STDERR: {e.stderr}"
        raise subprocess.CalledProcessError(e.returncode, command, error_msg)
    finally:
        # Always restore original directory
        os.chdir(original_cwd)


def spawn_claude_session(
    task_description: str,
    target_directory: str,
    specific_instructions: str | None = None,
    tools_needed: list[str] | None = None,
) -> str:
    """
    Spawn a new Claude Code session for a specific development task.

    This is a higher-level wrapper that formats prompts appropriately
    for common development workflows.

    Args:
        task_description: High-level description of what needs to be done
        target_directory: Directory where the work should be performed
        specific_instructions: Additional detailed instructions
        tools_needed: Specific tools Claude should have access to

    Returns:
        Result of Claude's execution
    """

    # Build comprehensive prompt
    prompt_parts = [
        f"TASK: {task_description}",
        "",
        f"WORKING DIRECTORY: {target_directory}",
        "",
        "INSTRUCTIONS:",
    ]

    if specific_instructions:
        prompt_parts.extend([specific_instructions, ""])

    prompt_parts.extend(
        [
            "REQUIREMENTS:",
            "- Follow existing code patterns and conventions",
            "- Ensure all changes are properly tested if tests exist",
            "- Use appropriate git workflow (branch, commit, etc.)",
            "- Provide clear commit messages",
            "- Handle errors gracefully",
            "",
            "Execute this task autonomously and report results.",
        ]
    )

    full_prompt = "\n".join(prompt_parts)

    return execute_claude_code(
        prompt=full_prompt, working_directory=target_directory, allowed_tools=tools_needed
    )
