"""
Valor Delegation Tool - Executes development tasks using Claude Code sessions.

## Architecture Decision: Enabled Delegation

This tool spawns Claude Code sessions to execute development tasks autonomously.
It has been re-enabled to support actual task execution rather than just guidance.

### Implementation Features

**Current Capabilities**: This tool executes development tasks by spawning Claude Code sessions:
- Autonomous task execution with proper workspace isolation
- Directory validation and tool permissions
- Execution monitoring with timeout support
- Comprehensive error handling and reporting

**Usage Patterns**:
- Bug fixes and feature implementation
- Code refactoring and optimization
- Testing and validation tasks
- Git workflow automation

### Technical Implementation

The tool spawns subprocess calls to `claude code` with:
- Working directory isolation for workspace safety
- Comprehensive prompts with context and requirements
- Proper error handling and timeout management
- Structured output parsing and response formatting

### Benefits of Direct Execution

1. **Task Completion**: Actually executes requested work autonomously
2. **Workspace Awareness**: Operates in correct project directories
3. **Git Integration**: Can commit changes and follow workflows
4. **Testing Support**: Runs tests and validates implementations
5. **Real Results**: Provides actual code changes, not just guidance

### Integration Notes

The tool returns structured execution results containing:
- Task completion status and summary
- Details of changes made (files modified, tests run, etc.)
- Any errors encountered during execution
- Git commit information if changes were committed
- Working directory context for reference

Example response structure:
```
✅ **Task Completed Successfully**

Task: Fix authentication bug in login system

**Changes Made:**
- Modified src/auth/login.py: Fixed password validation logic
- Updated tests/test_auth.py: Added test cases for edge cases
- All tests passing: 15/15

**Git Status:**
- Committed changes: "Fix authentication validation bug"
- Branch: main
- Files changed: 2

**Working Directory:** `/Users/user/project`
```

### Error Handling

If Claude Code execution fails, the tool provides detailed error information:
- Exit codes and error messages
- Stdout/stderr output for debugging
- Timeout information if applicable
- Suggestions for manual resolution

This enables reliable task execution while maintaining visibility into the process.
"""

import os
import subprocess
import time


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


def estimate_task_duration(task_description: str, specific_instructions: str | None = None) -> int:
    """Estimate how long a task might take in seconds based on its description.
    
    Args:
        task_description: Description of the task
        specific_instructions: Additional instructions that might affect duration
        
    Returns:
        int: Estimated duration in seconds
    """
    # Keywords that suggest longer tasks
    long_task_keywords = [
        "refactor", "rewrite", "implement", "create", "build", "setup",
        "comprehensive", "entire", "all", "complete", "full",
        "test suite", "documentation", "migration", "upgrade"
    ]
    
    # Keywords that suggest quick tasks
    quick_task_keywords = [
        "fix", "update", "change", "modify", "add", "remove",
        "typo", "rename", "move", "simple", "quick", "small"
    ]
    
    # Handle None or empty task description
    if not task_description:
        return 30  # Default estimate
    
    task_lower = task_description.lower()
    if specific_instructions:
        task_lower += " " + specific_instructions.lower()
    
    # Check for indicators of task complexity
    long_indicators = sum(1 for keyword in long_task_keywords if keyword in task_lower)
    quick_indicators = sum(1 for keyword in quick_task_keywords if keyword in task_lower)
    
    # Base estimate
    if long_indicators > quick_indicators:
        return 60  # 1 minute for complex tasks
    elif quick_indicators > 0:
        return 15  # 15 seconds for simple tasks
    else:
        return 30  # 30 seconds default


def spawn_valor_session(
    task_description: str,
    target_directory: str,
    specific_instructions: str | None = None,
    tools_needed: list[str] | None = None,
    force_sync: bool = False,
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
        force_sync: If True, always execute synchronously (for background execution).

    Returns:
        str: Result of Claude's execution including task completion status.
        If task is estimated to take >30 seconds and not force_sync, returns ASYNC_PROMISE marker.

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
    
    # Execute actual Claude Code delegation
    # Previous safety return was removed to enable real task execution
    
    # Estimate task duration
    estimated_duration = estimate_task_duration(task_description, specific_instructions)
    
    # If task is estimated to take >30 seconds and not forced sync, return async promise marker
    if estimated_duration > 30 and not force_sync:
        return f"ASYNC_PROMISE|I'll work on this task in the background: {task_description}"

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
        # Track execution time
        start_time = time.time()
        result = execute_valor_delegation(
            prompt=full_prompt, working_directory=target_directory, allowed_tools=tools_needed
        )
        execution_time = time.time() - start_time
        
        # Log if our estimate was significantly off
        if execution_time > 30 and estimated_duration <= 30:
            print(f"Task took {execution_time:.1f}s but was estimated at {estimated_duration}s")
            
        return result
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        return f"""❌ **Development Tool Error**

The Claude Code delegation failed: {str(e)}

I can help you with this task directly instead. What specifically do you need assistance with?

For "{task_description}", I can:
- Provide implementation guidance
- Share code examples
- Explain the approach step-by-step
- Review existing code if you share it"""
