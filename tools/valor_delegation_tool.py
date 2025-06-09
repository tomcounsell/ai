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
    print(f"🔧 execute_valor_delegation called with:")
    print(f"   - prompt length: {len(prompt)} chars")
    print(f"   - working_directory: {working_directory}")
    print(f"   - timeout: {timeout}")
    
    # Default allowed tools for coding tasks
    if allowed_tools is None:
        allowed_tools = ["Edit", "Write", "Read", "Bash", "Glob", "Grep", "LS", "MultiEdit", "Task"]

    # Validate working directory
    if working_directory:
        if not os.path.exists(working_directory):
            raise FileNotFoundError(f"Working directory does not exist: {working_directory}")
        if not os.path.isdir(working_directory):
            raise NotADirectoryError(f"Path is not a directory: {working_directory}")

    # Build Claude command - prefer non-shell execution for reliability
    # Use --print flag for non-interactive execution
    
    # Save current directory and change if needed
    original_dir = os.getcwd()
    if working_directory and working_directory != ".":
        try:
            os.chdir(working_directory)
            print(f"📂 Changed to directory: {working_directory}")
        except Exception as e:
            print(f"❌ Failed to change directory: {e}")
            return f"Failed to change to directory {working_directory}: {str(e)}"
    
    # Always use list form for better reliability
    command = ["claude", "--print", prompt]
    shell = False
    print(f"🏃 Running Claude Code with command list")

    try:
        # Execute Claude Code
        print(f"🚀 Executing subprocess.run with timeout={timeout}...")
        process = subprocess.run(
            command, 
            check=True, 
            capture_output=True, 
            text=True, 
            timeout=timeout,
            shell=shell
        )

        print(f"✅ subprocess completed successfully")
        print(f"   stdout length: {len(process.stdout)} chars")
        
        # Restore original directory
        if working_directory and working_directory != ".":
            os.chdir(original_dir)
            print(f"📂 Restored directory to: {original_dir}")
            
        return process.stdout

    except subprocess.TimeoutExpired:
        print(f"⏱️  Timeout after {timeout} seconds")
        print(f"   Command: {command if isinstance(command, str) else ' '.join(command)}")
        
        # Restore original directory
        if working_directory and working_directory != ".":
            os.chdir(original_dir)
            print(f"📂 Restored directory to: {original_dir}")
        
        # Return a timeout message instead of raising
        return f"""⏱️ **Execution Timed Out**

The Claude Code session timed out after {timeout} seconds. This might happen if:
- The task requires user permissions
- The prompt is too complex
- Claude Code is waiting for input

For the task you requested, I can help you directly instead."""
    except subprocess.CalledProcessError as e:
        # Restore original directory
        if working_directory and working_directory != ".":
            os.chdir(original_dir)
            print(f"📂 Restored directory to: {original_dir}")
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
    
    print(f"🎯 spawn_valor_session called with:")
    print(f"   - task_description: {task_description}")
    print(f"   - target_directory: {target_directory}")
    print(f"   - force_sync: {force_sync}")
    
    # Estimate task duration
    estimated_duration = estimate_task_duration(task_description, specific_instructions)
    print(f"⏱️  Estimated task duration: {estimated_duration} seconds")
    
    # If task is estimated to take >30 seconds and not forced sync, return async promise marker
    if estimated_duration > 30 and not force_sync:
        print(f"🔄 Returning ASYNC_PROMISE marker (duration > 30s and not force_sync)")
        return f"ASYNC_PROMISE|I'll work on this task in the background: {task_description}"

    # Build enhanced prompt with screenshot support and server management
    prompt_parts = [
        f"Please help me with this task: {task_description}",
        "",
        "SERVER MANAGEMENT INSTRUCTIONS:",
        "If your changes require a server restart (e.g., modifying main.py, handlers, core files):",
        "- Use a single command: `scripts/stop.sh && scripts/start.sh`",
        "- This ensures proper shutdown and restart in one operation",
        "- Wait for startup completion before finishing the task", 
        "- The server has shutdown protection, so this combined approach avoids conflicts",
        "",
    ]

    # Add screenshot-specific instructions if this is a screenshot task
    if "screenshot" in task_description.lower() or "playwright" in task_description.lower() or (specific_instructions and "screenshot" in specific_instructions.lower()):
        prompt_parts.extend([
            "SCREENSHOT CAPTURE INSTRUCTIONS:",
            "- If you create Playwright tests that capture screenshots:",
            "- Save screenshots to ./tmp/ai_screenshots/{task_id}_{timestamp}.png",
            "- Create the tmp/ai_screenshots directory if it doesn't exist",
            f"- Use task ID: {os.environ.get('NOTION_TASK_ID', 'manual_test')}",
            "- Output 'SCREENSHOT_CAPTURED:{full_path}' when screenshot is saved",
            "- Use full page screenshots with { fullPage: true } option",
            "",
        ])

    if specific_instructions:
        prompt_parts.append(f"Additional instructions: {specific_instructions}")
        prompt_parts.append("")

    # Keep the prompt simple and direct
    prompt_parts.append("Please complete this task and provide a summary of what you did.")

    full_prompt = "\n".join(prompt_parts)
    print(f"📝 Built full prompt ({len(full_prompt)} chars)")

    try:
        # Track execution time
        start_time = time.time()
        print(f"🏃 Calling execute_valor_delegation...")
        # Set a reasonable timeout based on our estimate, with a minimum of 60 seconds
        timeout = max(60, estimated_duration * 2)  # Double the estimate for safety
        print(f"⏱️  Setting timeout to {timeout} seconds")
        
        result = execute_valor_delegation(
            prompt=full_prompt, 
            working_directory=target_directory, 
            allowed_tools=tools_needed,
            timeout=timeout
        )
        execution_time = time.time() - start_time
        
        print(f"✅ Delegation completed in {execution_time:.1f}s")
        
        # Log if our estimate was significantly off
        if execution_time > 30 and estimated_duration <= 30:
            print(f"⚠️  Task took {execution_time:.1f}s but was estimated at {estimated_duration}s")
        
        # Check for screenshot capture markers in the output
        if "SCREENSHOT_CAPTURED:" in result:
            print(f"📸 Screenshot capture detected in Claude Code output")
            lines = result.split('\n')
            screenshot_paths = []
            for line in lines:
                if line.strip().startswith("SCREENSHOT_CAPTURED:"):
                    screenshot_path = line.split(":", 1)[1].strip()
                    screenshot_paths.append(screenshot_path)
                    print(f"📸 Screenshot captured: {screenshot_path}")
            
            if screenshot_paths:
                # Add visual indicator that screenshots are ready for retrieval
                result += f"\n\n📸 **Screenshot(s) Captured:** {len(screenshot_paths)} screenshot(s) ready for retrieval"
                
        return result
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        print(f"❌ Delegation failed with error: {type(e).__name__}: {str(e)}")
        return f"""❌ **Development Tool Error**

The Claude Code delegation failed: {str(e)}

I can help you with this task directly instead. What specifically do you need assistance with?

For "{task_description}", I can:
- Provide implementation guidance
- Share code examples
- Explain the approach step-by-step
- Review existing code if you share it"""
    except Exception as e:
        print(f"❌ Unexpected error in spawn_valor_session: {type(e).__name__}: {str(e)}")
        import traceback
        print(f"Traceback:\n{traceback.format_exc()}")
        return f"""❌ **Unexpected Error**

An unexpected error occurred: {str(e)}

I'll help you with this task directly instead."""
