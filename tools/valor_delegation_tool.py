"""
Valor Delegation Tool - Provides development guidance instead of spawning Claude Code sessions.

## Architecture Decision: Guidance Over Delegation

This tool was redesigned in commit 05c9323 to prevent agent hanging issues caused by recursive 
Claude Code spawning. Instead of executing tasks directly, it now provides comprehensive 
development guidance.

### Historical Context: The Hanging Issue

**Problem**: The original implementation attempted to spawn subprocess Claude Code sessions 
from within a PydanticAI agent tool. This caused:
- Agent hanging during tool selection 
- Infinite recursion when Claude Code spawned more Claude Code sessions
- Process deadlocks and unresponsive agent behavior
- Poor user experience with delayed/missing responses

**Root Cause**: PydanticAI agents running subprocess commands that spawn the same tool 
created circular dependencies and resource contention.

**Solution**: Replace subprocess delegation with intelligent guidance responses that:
- Provide step-by-step implementation approaches
- Share relevant code examples and patterns
- Offer testing strategies and best practices
- Include architecture and integration advice

### Current Implementation

The tool now returns structured guidance responses containing:
- Task-specific implementation approaches
- Code structure and architecture advice
- Testing and validation strategies  
- Integration patterns and best practices
- Working directory context for user reference

This approach provides equivalent value to users while eliminating:
- Hanging and deadlock issues
- Recursive spawning problems
- Process management complexity
- Security concerns with subprocess execution

### Benefits of Guidance Approach

1. **Reliability**: No hanging, deadlocks, or subprocess issues
2. **Performance**: Instant responses without subprocess overhead
3. **Security**: No arbitrary command execution or process spawning
4. **Flexibility**: Guidance adapts to user context and preferences
5. **Maintainability**: Simpler architecture without process management

### Integration Notes

Tools or systems expecting delegation results should handle the new guidance format:
- Response starts with "💡 **Development Guidance Available**"
- Contains structured sections for implementation approaches
- Includes working directory context
- Provides actionable next steps and questions

Example response structure:
```
💡 **Development Guidance Available**

For the task: **[task_description]**

**Implementation Approach:**
- Step-by-step guidance...

**Specific Help I Can Provide:**
- Architecture advice...

**Working Directory:** `/path/to/project`

What specific aspect would you like me to help you with first?
```

### Migration Notes for Developers

If you need actual code execution (the original delegation behavior):
1. Use Claude Code directly from command line instead of through agent tools
2. Consider MCP tools for specific development tasks
3. Use the guidance provided to implement solutions manually
4. For complex automation, consider CI/CD pipelines instead of agent delegation

This architectural change prioritizes system reliability and user experience over 
convenience features that caused critical stability issues.
"""

import os
import subprocess


def execute_valor_delegation(
    prompt: str,
    working_directory: str | None = None,
    allowed_tools: list[str] | None = None,
    timeout: int | None = None,
) -> str:
    """Execute a delegated Claude Code session with a specific prompt and context.

    This function spawns a new Claude Code session with the provided prompt
    and configuration. It handles directory validation, tool permissions,
    and execution monitoring. Claude Code is executed in the specified working
    directory to maintain workspace isolation.

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

    # Build Claude command - execute in working directory using cd
    if working_directory:
        # Use shell command to change directory and run Claude Code
        command = f'cd "{working_directory}" && claude code "{prompt}"'
        shell = True
    else:
        # Run in current directory
        command = ["claude", "code", prompt]
        shell = False

    try:
        # Execute Claude Code
        process = subprocess.run(
            command, 
            check=True, 
            capture_output=True, 
            text=True, 
            timeout=timeout,
            shell=shell
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


def spawn_valor_session(
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
    
    # IMPORTANT: Prevent recursive Claude Code sessions that cause hanging
    # For safety, always avoid spawning Claude Code when running as an agent tool
    # This prevents infinite recursion and hanging issues
    return f"""💡 **Development Guidance Available**

For the task: **{task_description}**

I can help you with this directly instead of delegating to another session:

**Implementation Approach:**
- I can provide step-by-step guidance for this task
- Share relevant code examples and patterns
- Review your implementation if you share code
- Suggest testing strategies and best practices

**Specific Help I Can Provide:**
- Code structure and architecture advice
- Implementation details and examples
- Testing and validation approaches
- Integration patterns and best practices

**Working Directory:** `{target_directory}`
{f"**Additional Requirements:** {specific_instructions}" if specific_instructions else ""}

What specific aspect would you like me to help you with first? I can provide detailed technical guidance to accomplish this task."""

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

    try:
        return execute_valor_delegation(
            prompt=full_prompt, working_directory=target_directory, allowed_tools=tools_needed
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        return f"""❌ **Development Tool Error**

The Claude Code delegation failed: {str(e)}

I can help you with this task directly instead. What specifically do you need assistance with?

For "{task_description}", I can:
- Provide implementation guidance
- Share code examples
- Explain the approach step-by-step
- Review existing code if you share it"""
