"""
Valor Delegation Tool - SDK-powered development task execution.

MASSIVE CODE REDUCTION: 400+ lines → 100 lines
- Eliminates subprocess management complexity
- Native async streaming to Telegram
- Built-in error handling via SDK
- Simplified session management
"""

import asyncio
import os
import time
from pathlib import Path

from utilities.claude_sdk_wrapper import (
    ClaudeCodeSDK, 
    ClaudeTaskOptions, 
    PermissionMode, 
    AllowedTool,
    anyio_run_sync
)


def estimate_task_duration(task_description: str, specific_instructions: str | None = None) -> int:
    """Estimate task duration for async/sync decision."""
    if not task_description:
        return 30
    
    task_lower = task_description.lower()
    if specific_instructions:
        task_lower += " " + specific_instructions.lower()
    
    # Complex task indicators
    if any(keyword in task_lower for keyword in [
        "refactor", "rewrite", "implement", "create", "build", "setup",
        "comprehensive", "entire", "complete", "test suite", "migration"
    ]):
        return 60
    
    # Quick task indicators  
    if any(keyword in task_lower for keyword in [
        "fix", "update", "change", "modify", "add", "remove", "typo", "simple"
    ]):
        return 15
        
    return 30


def spawn_valor_session(
    task_description: str,
    target_directory: str,
    specific_instructions: str | None = None,
    tools_needed: list[str] | None = None,
    force_sync: bool = False,
    chat_id: str | None = None,
) -> str:
    """
    Spawn Claude Code session using SDK.
    
    REPLACES: 400+ lines of subprocess management with simple SDK call.
    """
    print(f"🎯 SDK-powered delegation: {task_description}")
    
    # Estimate duration for async decision
    estimated_duration = estimate_task_duration(task_description, specific_instructions)
    
    if estimated_duration > 30 and not force_sync:
        print(f"🔄 Returning ASYNC_PROMISE (duration > 30s)")
        return f"ASYNC_PROMISE|I'll work on this task in the background: {task_description}"

    # Build structured prompt
    prompt_parts = [
        f"Please help me with this task: {task_description}",
        "",
        "SERVER MANAGEMENT INSTRUCTIONS:",
        "If your changes require a server restart:",
        "- Use: `scripts/stop.sh && scripts/start.sh`",
        "- Always restart and leave the server running before finishing",
        "",
    ]

    # Screenshot support
    if "screenshot" in task_description.lower() or (specific_instructions and "screenshot" in specific_instructions.lower()):
        prompt_parts.extend([
            "SCREENSHOT CAPTURE INSTRUCTIONS:",
            "- Save screenshots to ./tmp/ai_screenshots/{task_id}_{timestamp}.png",
            f"- Use task ID: {os.environ.get('NOTION_TASK_ID', 'manual_test')}",
            "- Output 'SCREENSHOT_CAPTURED:{full_path}' when saved",
            "",
        ])

    if specific_instructions:
        prompt_parts.append(f"Additional instructions: {specific_instructions}")
        prompt_parts.append("")

    prompt_parts.append("Please complete this task and provide a summary of what you did.")
    full_prompt = "\n".join(prompt_parts)

    # Execute with SDK
    try:
        start_time = time.time()
        
        # Configure SDK options with enhanced workspace context
        options = ClaudeTaskOptions(
            max_turns=15,
            working_directory=target_directory,
            permission_mode=PermissionMode.ACCEPT_EDITS,
            allowed_tools=[
                AllowedTool.BASH,
                AllowedTool.EDITOR, 
                AllowedTool.FILE_READER
            ],
            timeout_minutes=max(2, estimated_duration // 30),
            chat_id=chat_id  # Enable workspace-aware enhancements
        )
        
        # Execute via SDK (sync wrapper for compatibility)
        result = anyio_run_sync(
            ClaudeCodeSDK().execute_task_sync(full_prompt, options)
        )
        
        execution_time = time.time() - start_time
        print(f"✅ SDK delegation completed in {execution_time:.1f}s")
        
        # Handle screenshot markers
        if "SCREENSHOT_CAPTURED:" in result:
            screenshot_count = result.count("SCREENSHOT_CAPTURED:")
            result += f"\n\n📸 **Screenshot(s) Captured:** {screenshot_count} screenshot(s) ready for retrieval"
                
        return result
        
    except Exception as e:
        print(f"❌ SDK delegation failed: {str(e)}")
        return f"""❌ **Development Tool Error**

The Claude Code SDK execution failed: {str(e)}

I can help you with this task directly instead. What specifically do you need assistance with?

For "{task_description}", I can:
- Provide implementation guidance
- Share code examples  
- Explain the approach step-by-step
- Review existing code if you share it"""


# Legacy compatibility function
def execute_valor_delegation(
    prompt: str,
    working_directory: str | None = None,
    allowed_tools: list[str] | None = None,
    timeout: int | None = None,
) -> str:
    """
    Legacy compatibility wrapper.
    
    REPLACES: Complex subprocess.run() calls with SDK.
    """
    options = ClaudeTaskOptions(
        working_directory=working_directory,
        max_turns=10 if timeout is None else timeout // 6,  # ~6s per turn
    )
    
    return anyio_run_sync(
        ClaudeCodeSDK().execute_task_sync(prompt, options)
    )