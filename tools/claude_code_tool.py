"""
Claude Code Tool - Allows spawning new Claude Code sessions with specific prompts and directories.

This tool enables Valor to perform software engineering tasks.
Detailed prompts must be provided for autonomous execution.
Example tasks include:
- running tests
- commiting and pushing changes to GitHub
- making changes to any file
- exploring the repository
- creating plans for future development
- executing complex multi-step dev tasks that require reasoning and planning

Tips & Recommendations:

Memory Management:
- Use /memory command to quickly edit CLAUDE.md files
- Use # shortcut to add quick notes during sessions
- Review your memory files periodically - they can get stale

TDD Workflow Optimization:
- Keep plan documents focused and specific
- Update the "Current Work Status" section in CLAUDE.md as you switch between projects
- Consider adding common test patterns to project-specific CLAUDE.md files

Session Continuity:
- Use claude -c to resume recent work
- Always check TodoRead when continuing implementation work
- Update plan documents if you discover new requirements mid-implementation

Project Organization:
- Create /docs/plan/ directories in projects that will use this TDD process
- Consider adding test coverage requirements to project-specific CLAUDE.md files
- Keep successful patterns documented for reuse
"""

import os
import subprocess


def execute_claude_code(
    prompt: str,
    working_directory: str | None = None,
    allowed_tools: list[str] | None = None,
    timeout: int | None = None,
) -> str:
    """Execute a Claude Code session with a specific prompt and context.

    This function spawns a new Claude Code session with the provided prompt
    and configuration. It handles directory validation, tool permissions,
    and execution monitoring.

    Args:
        prompt: Detailed instructions for Claude to execute.
        working_directory: Directory to run Claude in (defaults to current).
        allowed_tools: List of tools Claude can use (defaults to common tools).
        timeout: Maximum execution time in seconds (None for no timeout).

    Returns:
        str: Claude's output from the execution.

    Raises:
        subprocess.CalledProcessError: If Claude execution fails.
        FileNotFoundError: If working directory doesn't exist.
        NotADirectoryError: If working_directory path is not a directory.
        subprocess.TimeoutExpired: If execution exceeds timeout.

    Example:
        >>> result = execute_claude_code(
        ...     "Create a simple Python script",
        ...     "/tmp",
        ...     ["Write", "Edit", "Read"]
        ... )
        >>> "script" in result.lower()
        True
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
    """Spawn a new Claude Code session for a specific development task.

    This is a higher-level wrapper that formats prompts appropriately
    for common development workflows. It creates comprehensive prompts
    with task descriptions, requirements, and best practices.

    Args:
        task_description: High-level description of what needs to be done.
        target_directory: Directory where the work should be performed.
        specific_instructions: Additional detailed instructions.
        tools_needed: Specific tools Claude should have access to.

    Returns:
        str: Result of Claude's execution including task completion status.

    Example:
        >>> result = spawn_claude_session(
        ...     "Create a FastAPI application",
        ...     "/home/user/projects",
        ...     "Include user authentication"
        ... )
        >>> "FastAPI" in result
        True

    Note:
        This function automatically includes common development requirements
        like following existing patterns, testing, and git workflows.
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
