# Screenshot Handoff Solution - Implementation Plan

## Overview

This plan documents the architecture for enabling screenshot sharing between target workspace Playwright tests and the Claude Code-based AI agent system for automated bug report workflows.

## Problem Statement

**Workflow Requirements:**
1. AI agent receives bug report from Notion (e.g., "login screen is broken")
2. Agent spawns Claude Code session in target workspace to leverage existing/new Playwright tests
3. Claude Code executes test that captures screenshot of login page
4. Screenshot must be handed back to main AI agent for Telegram upload with analysis
5. Agent updates Notion task with evidence and completion status

**Core Challenge:**
- Target workspace Playwright tests run in isolated directories under Claude Code
- Claude Code sessions are ephemeral subprocess executions
- Main AI system needs to retrieve screenshots from these ephemeral sessions
- Must respect strict workspace security boundaries
- Need secure, reliable file handoff mechanism between Claude Code processes and main agent

## Current System Analysis

### ✅ Existing Capabilities
- **Claude Code Integration**: MCP tools spawn Claude Code sessions in isolated workspaces
- **Workspace Security**: Strict isolation with directory access controls via `workspace_validator.py`
- **Telegram Integration**: `TELEGRAM_IMAGE_GENERATED|` marker triggers image upload pipeline
- **Image Processing**: GPT-4o analysis, compression, metadata handling via `image_analysis_tool.py`
- **MCP Architecture**: Development tools in `mcp_servers/development_tools.py`
- **Value Delegation**: `valor_delegation_tool.py` handles Claude Code spawning

### ❌ Missing Capabilities
- Cross-process file handoff from Claude Code sessions to main agent
- Screenshot retrieval MCP tool
- Playwright test coordination with screenshot handoff
- Integration with existing `TELEGRAM_IMAGE_GENERATED|` pipeline

## Solution Architecture

### Core Strategy: Workspace-Safe File Handoff

**Key Insight:** Use workspace-relative temporary directories that both Claude Code and main agent can access within workspace boundaries

```
Claude Code Session            AI Agent (MCP)
┌─────────────────┐           ┌──────────────────┐
│ Playwright Test │           │ Screenshot Tool  │
│                 │           │                  │
│ page.screenshot │──────────▶│ retrieve_shot()  │
│ (workspace/tmp) │   File    │ analyze_image()  │
│                 │  Handoff  │ TELEGRAM_IMAGE_* │
└─────────────────┘           └──────────────────┘
```

## Implementation Plan

### Phase 1: Workspace-Safe Screenshot Directory

#### 1.1 Update Workspace Security Model
**File:** `/utilities/workspace_validator.py`

**Modifications Needed:**
- Add exception for `{workspace}/tmp/ai_screenshots/` access within workspace boundaries
- Maintain existing workspace isolation for cross-workspace access
- Enable screenshot subdirectory access within each workspace

```python
def validate_directory_access(self, chat_id: str, file_path: str) -> None:
    """Validate directory access with workspace screenshot sharing."""
    import os
    
    # Get the workspace this chat is mapped to
    workspace_name = self.get_workspace_for_chat(chat_id)
    if not workspace_name:
        raise WorkspaceAccessError(f"Chat {chat_id} is not mapped to any workspace")
    
    workspace = self.workspaces[workspace_name]
    normalized_path = os.path.abspath(file_path)
    
    # Allow workspace/tmp/ai_screenshots/ access within workspace
    for allowed_dir in workspace.allowed_directories:
        workspace_screenshots_dir = os.path.join(allowed_dir, "tmp", "ai_screenshots")
        if normalized_path.startswith(os.path.abspath(workspace_screenshots_dir)):
            return  # Access granted for workspace screenshots
    
    # Existing workspace validation logic continues...
    # ... rest of current implementation
```

### Phase 2: Screenshot File Conventions

#### 2.1 Naming Convention
**Format:** `{workspace_dir}/tmp/ai_screenshots/{task_id}_{timestamp}.png`

**Examples:**
- `/Users/valorengels/src/deckfusion/tmp/ai_screenshots/login_bug_1748855141.png`
- `/Users/valorengels/src/psyoptimal/tmp/ai_screenshots/nav_issue_1748855200.png`

#### 2.2 Claude Code Playwright Integration
**Claude Code session pattern (executed in target workspace):**

```javascript
// Claude Code generates this in target workspace Playwright test
import { test, expect } from '@playwright/test';
import path from 'path';
import fs from 'fs';

test('capture bug evidence screenshot', async ({ page }) => {
    // Ensure screenshot directory exists
    const screenshotDir = path.join(process.cwd(), 'tmp', 'ai_screenshots');
    fs.mkdirSync(screenshotDir, { recursive: true });
    
    // Generate unique filename
    const taskId = process.env.NOTION_TASK_ID || 'manual_test';
    const timestamp = Date.now();
    const screenshotPath = path.join(screenshotDir, `${taskId}_${timestamp}.png`);
    
    // Navigate to page and capture screenshot
    await page.goto('https://localhost:3000/login');
    await page.screenshot({
        path: screenshotPath,
        fullPage: true
    });
    
    console.log(`Screenshot saved: ${screenshotPath}`);
    
    // Output standardized result for main agent to parse
    console.log(`SCREENSHOT_CAPTURED:${screenshotPath}`);
});
```

### Phase 3: AI Agent Integration

#### 3.1 New MCP Tool - Screenshot Retrieval
**File:** `/mcp_servers/development_tools.py`

```python
@mcp.tool()
def retrieve_workspace_screenshot(
    task_id: str,
    chat_id: str = "",
    max_age_minutes: int = 10
) -> str:
    """
    Retrieve and analyze screenshot from current workspace after Claude Code execution.

    This tool searches for screenshots captured by Claude Code sessions in the current
    workspace's tmp/ai_screenshots/ directory and returns them using the TELEGRAM_IMAGE_GENERATED
    marker for automatic Telegram upload.

    Args:
        task_id: Task identifier for screenshot matching
        chat_id: Telegram chat ID for workspace detection and upload
        max_age_minutes: Maximum age of screenshot to accept (default: 10)

    Returns:
        TELEGRAM_IMAGE_GENERATED marker with screenshot path for automatic upload
    """
    import os
    import glob
    import time
    from pathlib import Path
    
    try:
        # Get workspace working directory for this chat
        if chat_id:
            from utilities.workspace_validator import get_workspace_validator
            validator = get_workspace_validator()
            workspace_name = validator.get_workspace_for_chat(chat_id)
            if workspace_name:
                working_dir = validator.get_allowed_directories(chat_id)[0]
            else:
                working_dir = os.getcwd()
        else:
            working_dir = os.getcwd()

        # Look for screenshots in workspace tmp directory
        screenshot_dir = os.path.join(working_dir, "tmp", "ai_screenshots")
        
        if not os.path.exists(screenshot_dir):
            return f"📸 No screenshot directory found in {working_dir}/tmp/ai_screenshots"

        # Find matching screenshot files
        pattern = os.path.join(screenshot_dir, f"{task_id}_*.png")
        matching_files = glob.glob(pattern)

        if not matching_files:
            return f"📸 No screenshots found for task {task_id} in {screenshot_dir}"

        # Get most recent file within age limit
        cutoff_time = time.time() - (max_age_minutes * 60)
        recent_files = [
            f for f in matching_files
            if os.path.getmtime(f) > cutoff_time
        ]

        if not recent_files:
            return f"📸 No recent screenshots found for task {task_id} (last {max_age_minutes} minutes)"

        # Use most recent file
        screenshot_path = max(recent_files, key=os.path.getmtime)
        
        # Validate file access through workspace validator
        if chat_id:
            access_error = validate_directory_access(chat_id, screenshot_path)
            if access_error:
                return access_error

        # Analyze screenshot using existing image analysis tool
        from tools.image_analysis_tool import analyze_image
        analysis = analyze_image(
            screenshot_path, 
            question="What does this screenshot show? Focus on any UI issues, errors, or relevant details.",
            context=f"This is a screenshot captured for task: {task_id}"
        )
        
        # Return using TELEGRAM_IMAGE_GENERATED marker for automatic upload
        caption = f"📸 **Screenshot Evidence - Task {task_id}**\n\n{analysis}"
        
        # Clean up file after successful processing
        try:
            os.remove(screenshot_path)
        except Exception:
            pass  # Don't fail if cleanup fails
            
        return f"TELEGRAM_IMAGE_GENERATED|{screenshot_path}|{caption}"

    except Exception as e:
        return f"📸 Screenshot retrieval error: {str(e)}"
```

#### 3.2 Integration with Existing Pipeline
**Leverages existing architecture:**
- `/tools/image_analysis_tool.py` - GPT-4o vision analysis of screenshots
- `TELEGRAM_IMAGE_GENERATED|` marker - Automatic Telegram upload via existing pipeline
- `/agents/valor/agent.py` - Recognizes marker and handles upload
- `/utilities/workspace_validator.py` - Security validation for file access

### Phase 4: Enhanced Valor Delegation

#### 4.1 Updated Delegation Tool
**File:** `/tools/valor_delegation_tool.py`

**Enhancement needed:** Parse Claude Code output to detect screenshot markers

```python
def spawn_valor_session(
    task_description: str,
    target_directory: str,
    specific_instructions: str | None = None,
    tools_needed: list[str] | None = None,
    force_sync: bool = False,
) -> str:
    """Enhanced to handle screenshot capture workflows."""
    
    # Build enhanced prompt for screenshot tasks
    if "screenshot" in task_description.lower() or "playwright" in task_description.lower():
        prompt_parts = [
            f"Please help me with this task: {task_description}",
            "",
            "IMPORTANT: If you create Playwright tests that capture screenshots:",
            "1. Save screenshots to ./tmp/ai_screenshots/{task_id}_{timestamp}.png",
            "2. Output 'SCREENSHOT_CAPTURED:{path}' when done",
            "3. Use process.env.NOTION_TASK_ID or generate a unique task ID",
        ]
        
        if specific_instructions:
            prompt_parts.append(f"\nAdditional instructions: {specific_instructions}")
    else:
        # Standard prompt building
        prompt_parts = [f"Please help me with this task: {task_description}"]
    
    # Execute Claude Code delegation
    result = execute_valor_delegation(...)
    
    # Parse output for screenshot markers
    if "SCREENSHOT_CAPTURED:" in result:
        lines = result.split('\n')
        for line in lines:
            if line.startswith("SCREENSHOT_CAPTURED:"):
                screenshot_path = line.split(":", 1)[1].strip()
                # Extract task ID from path for later retrieval
                # Return indication that screenshot is available
                return f"{result}\n\n📸 Screenshot captured and ready for retrieval"
    
    return result
```

#### 4.2 End-to-End Bug Report Workflow
**File:** `/mcp_servers/development_tools.py`

```python
@mcp.tool()
def execute_bug_report_with_screenshot(
    task_description: str,
    notion_task_id: str,
    chat_id: str = ""
) -> str:
    """
    Execute complete bug report workflow with automated screenshot evidence.

    This tool orchestrates:
    1. Claude Code session to create/run Playwright test
    2. Screenshot capture during test execution
    3. Screenshot retrieval and analysis
    4. Automatic Telegram upload with AI analysis

    Args:
        task_description: Description of the bug or issue to investigate
        notion_task_id: Notion task ID for tracking and file naming
        chat_id: Telegram chat ID for workspace detection and upload

    Returns:
        TELEGRAM_IMAGE_GENERATED marker with screenshot and analysis, or error message
    """
    import os
    from tools.valor_delegation_tool import spawn_valor_session
    
    try:
        # Get workspace directory for this chat
        if chat_id:
            from utilities.workspace_validator import get_workspace_validator
            validator = get_workspace_validator()
            workspace_name = validator.get_workspace_for_chat(chat_id)
            if workspace_name:
                target_directory = validator.get_allowed_directories(chat_id)[0]
            else:
                return "❌ Unable to determine workspace for this chat"
        else:
            target_directory = os.getcwd()

        # Set environment variable for Claude Code session
        os.environ['NOTION_TASK_ID'] = notion_task_id

        # Execute Claude Code session with screenshot instructions
        enhanced_instructions = f"""
        Create and run a Playwright test to investigate: {task_description}
        
        Requirements:
        1. Navigate to the relevant page/component
        2. Capture a full-page screenshot showing the issue
        3. Save screenshot to ./tmp/ai_screenshots/{notion_task_id}_{{timestamp}}.png
        4. Output the exact text: SCREENSHOT_CAPTURED:{{path}}
        
        The screenshot will be automatically retrieved and uploaded to Telegram with AI analysis.
        """

        delegation_result = spawn_valor_session(
            task_description=f"Create Playwright test with screenshot for: {task_description}",
            target_directory=target_directory,
            specific_instructions=enhanced_instructions,
            force_sync=True  # Wait for completion
        )

        # Check if screenshot was captured
        if "SCREENSHOT_CAPTURED:" not in delegation_result:
            return f"⚠️ Task completed but no screenshot captured:\n\n{delegation_result}"

        # Retrieve and process screenshot
        screenshot_result = retrieve_workspace_screenshot(
            task_id=notion_task_id,
            chat_id=chat_id,
            max_age_minutes=5
        )

        if screenshot_result.startswith("TELEGRAM_IMAGE_GENERATED|"):
            return screenshot_result  # Success - will trigger automatic upload
        else:
            return f"📋 **Task Completed**\n\n{delegation_result}\n\n⚠️ Screenshot issue: {screenshot_result}"

    except Exception as e:
        return f"❌ Bug report workflow error: {str(e)}"
```

### Phase 5: Testing and Validation

#### 5.1 Unit Tests
**File:** `/tests/test_screenshot_handoff.py`

```python
def test_workspace_screenshot_directory_access():
    """Test that workspace/tmp/ai_screenshots/ is accessible within workspace boundaries."""

def test_screenshot_retrieval_mcp_tool():
    """Test MCP tool can find and process screenshots with TELEGRAM_IMAGE_GENERATED marker."""

def test_bug_report_workflow_orchestration():
    """Test end-to-end bug report workflow with Claude Code delegation."""

def test_screenshot_cleanup_automation():
    """Test automatic file cleanup after processing."""

def test_workspace_security_boundaries():
    """Verify that screenshot access respects workspace isolation."""
```

#### 5.2 Integration Testing
- Test Claude Code delegation with screenshot instructions
- Validate `TELEGRAM_IMAGE_GENERATED|` marker processing
- Confirm automatic Telegram upload and AI analysis
- Verify workspace security boundaries remain intact
- Test screenshot cleanup and file lifecycle management

## Security Considerations

### Maintained Security Boundaries
1. **Workspace Isolation**: Screenshots only accessible within assigned workspace boundaries
2. **No Cross-Workspace Access**: Each workspace's tmp directory is isolated
3. **File Type Validation**: Only image files in screenshot directories
4. **Time-based Cleanup**: Screenshots auto-deleted after processing
5. **Access Logging**: All file access validated through workspace validator

### Risk Mitigation
- **Workspace Validation**: All screenshot access goes through existing workspace validator
- **File Size Limits**: Prevent disk abuse with size restrictions
- **Rate Limiting**: Claude Code session timeouts prevent abuse
- **Path Validation**: Strict validation of screenshot file paths within workspace
- **Automatic Cleanup**: Remove files immediately after processing

## Benefits and Trade-offs

### ✅ Benefits
- **Zero Security Compromise**: Uses existing workspace isolation model
- **Leverages Existing Infrastructure**: Integrates with Claude Code, MCP tools, and Telegram pipeline
- **AI-Powered Analysis**: GPT-4o vision analysis of all screenshots
- **Seamless Integration**: Uses established `TELEGRAM_IMAGE_GENERATED|` pattern
- **Workspace-Aware**: Respects existing chat-to-workspace mappings

### ⚠️ Trade-offs
- **Claude Code Dependency**: Requires Claude Code for test execution
- **Timing Coordination**: Brief coordination between delegation and retrieval
- **Temporary Storage**: Uses workspace tmp directories (cleaned automatically)

## Implementation Timeline

1. **Week 1**: Update workspace validator for tmp/ai_screenshots access
2. **Week 2**: Implement screenshot retrieval MCP tool with TELEGRAM_IMAGE_GENERATED integration
3. **Week 3**: Enhance valor delegation tool with screenshot detection
4. **Week 4**: Create end-to-end bug report workflow tool and comprehensive testing

## Success Criteria

1. **Functional**: Screenshots successfully captured by Claude Code and retrieved by main agent
2. **Secure**: No compromise of existing workspace isolation - enhanced validation
3. **Reliable**: 95%+ success rate for screenshot handoff with automatic retry
4. **Integrated**: Seamless integration with existing MCP tools and Telegram upload pipeline
5. **AI-Enhanced**: Automatic GPT-4o analysis of all screenshots before upload

## Modern Architecture Benefits

This updated solution leverages the current unified conversational development environment:

- **Claude Code Integration**: Natural workflow where Claude Code creates tests and captures screenshots
- **MCP Tool Architecture**: Clean separation between tools and implementation
- **Workspace Security**: Enhanced security model with workspace-relative paths
- **Telegram Integration**: Reuses proven `TELEGRAM_IMAGE_GENERATED|` pipeline
- **AI Analysis**: Every screenshot gets intelligent analysis before sharing

The solution maintains all existing security guarantees while providing a robust, AI-enhanced screenshot sharing capability for automated bug reporting workflows.
