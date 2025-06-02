# Screenshot Handoff Solution - Implementation Plan

## Overview

This plan documents the architecture for enabling screenshot sharing between target workspace Playwright tests and the AI agent system for automated bug report workflows.

## Problem Statement

**Workflow Requirements:**
1. AI agent receives bug report from Notion (e.g., "login screen is broken")
2. Agent navigates to target workspace and leverages existing/new Playwright tests
3. Test captures screenshot of login page
4. AI agent retrieves screenshot and uploads to Telegram with analysis
5. Agent updates Notion task with evidence and completion status

**Core Challenge:**
- Target workspace Playwright tests run in isolated directories
- AI system has workspace security restrictions preventing cross-workspace file access
- Need secure, reliable file handoff mechanism between processes

## Current System Analysis

### ✅ Existing Capabilities
- **Notion Integration**: Task monitoring, AI analysis, workspace mapping
- **Workspace Security**: Strict isolation with directory access controls
- **Telegram Integration**: Image upload/download, chat history management
- **Image Processing**: Compression, analysis, metadata handling
- **Server Management**: Unified startup, health validation, process management

### ❌ Missing Capabilities
- Cross-workspace file sharing mechanism
- Playwright test coordination tools
- Screenshot retrieval and handoff automation

## Solution Architecture

### Core Strategy: User Temporary Directory

**Key Insight:** Use `~/tmp/ai_screenshots/` as user-owned neutral territory outside workspace restrictions

```
Target Workspace               AI Agent System
┌─────────────────┐           ┌──────────────────┐
│ Playwright Test │           │ Screenshot Tool  │
│                 │           │                  │
│ page.screenshot │──────────▶│ retrieve_shot()  │
│ (~/tmp/ai_...)  │   File    │ upload_telegram()│
│                 │  Handoff  │ update_notion()  │
└─────────────────┘           └──────────────────┘
```

## Implementation Plan

### Phase 1: Shared Directory Infrastructure

#### 1.1 Create User Screenshot Directory
```bash
# System setup (one-time)
mkdir -p ~/tmp/ai_screenshots
chmod 755 ~/tmp/ai_screenshots
```

#### 1.2 Update Workspace Security
**File:** `/utilities/workspace_validator.py`

**Modifications Needed:**
- Add exception for `~/tmp/ai_screenshots/` access across all workspaces
- Maintain existing workspace isolation for all other directories
- Add validation for screenshot file extensions (`.png`, `.jpg`, `.jpeg`)

```python
def validate_directory_access(chat_id: int, file_path: str) -> bool:
    """Validate directory access with screenshot sharing exception."""
    import os
    screenshot_dir = os.path.expanduser('~/tmp/ai_screenshots/')

    # Allow ~/tmp/ai_screenshots/ access for all chats
    if file_path.startswith(screenshot_dir):
        return True

    # Existing workspace validation logic
    # ... rest of current implementation
```

### Phase 2: Screenshot File Conventions

#### 2.1 Naming Convention
**Format:** `~/tmp/ai_screenshots/{workspace}_{task_id}_{timestamp}.png`

**Examples:**
- `~/tmp/ai_screenshots/deckfusion_login_bug_1748855141.png`
- `~/tmp/ai_screenshots/psyoptimal_nav_issue_1748855200.png`

#### 2.2 Playwright Test Integration
**Target workspace test pattern:**

```javascript
// In any workspace Playwright test
const os = require('os');
const path = require('path');

const taskId = process.env.NOTION_TASK_ID || 'manual_test';
const workspace = 'deckfusion'; // or extracted from cwd
const timestamp = Date.now();
const screenshotPath = path.join(
    os.homedir(),
    'tmp',
    'ai_screenshots',
    `${workspace}_${taskId}_${timestamp}.png`
);

await page.screenshot({
    path: screenshotPath,
    fullPage: true
});

console.log(`Screenshot saved: ${screenshotPath}`);
```

### Phase 3: AI Agent Integration

#### 3.1 New MCP Tool - Screenshot Retrieval
**File:** `/mcp_servers/development_tools.py`

```python
@mcp.tool()
def retrieve_workspace_screenshot(
    workspace: str,
    task_id: str,
    chat_id: int,
    max_age_minutes: int = 10
) -> str:
    """
    Retrieve and upload screenshot from target workspace test.

    Args:
        workspace: Target workspace name (deckfusion, psyoptimal, etc.)
        task_id: Notion task identifier for screenshot matching
        chat_id: Telegram chat ID for upload destination
        max_age_minutes: Maximum age of screenshot to accept

    Returns:
        Success message with upload confirmation
    """
    import os
    import glob
    import time
    from datetime import datetime, timedelta

    # Directory validation (should pass with new security rules)
    screenshot_dir = os.path.expanduser("~/tmp/ai_screenshots")
    if not validate_directory_access(chat_id, screenshot_dir):
        return "Error: Access denied to screenshot directory"

    # Find matching screenshot files
    pattern = f"{screenshot_dir}/{workspace}_{task_id}_*.png"
    matching_files = glob.glob(pattern)

    if not matching_files:
        return f"No screenshots found for {workspace} task {task_id}"

    # Get most recent file within age limit
    cutoff_time = time.time() - (max_age_minutes * 60)
    recent_files = [
        f for f in matching_files
        if os.path.getmtime(f) > cutoff_time
    ]

    if not recent_files:
        return f"No recent screenshots found (last {max_age_minutes} minutes)"

    # Use most recent file
    screenshot_path = max(recent_files, key=os.path.getmtime)

    # Upload to Telegram using existing image upload pipeline
    try:
        # Leverage existing image analysis and upload tools
        upload_result = upload_screenshot_to_telegram(screenshot_path, chat_id)

        # Clean up temporary file
        os.remove(screenshot_path)

        return f"Screenshot uploaded successfully: {upload_result}"

    except Exception as e:
        return f"Upload failed: {str(e)}"

def upload_screenshot_to_telegram(screenshot_path: str, chat_id: int) -> str:
    """Upload screenshot using existing Telegram integration."""
    # Implementation leverages existing image upload pipeline
    # from /tools/image_generation_tool.py patterns
    pass
```

#### 3.2 Integration with Existing Image Pipeline
**Leverage existing tools:**
- `/tools/image_analysis_tool.py` - For screenshot analysis if needed
- `/mcp_servers/social_tools.py` - For Telegram upload mechanism
- `/integrations/telegram/handlers.py` - For message formatting

### Phase 4: Workflow Orchestration

#### 4.1 End-to-End Workflow Tool
**File:** `/mcp_servers/development_tools.py`

```python
@mcp.tool()
def execute_bug_report_workflow(
    notion_task_id: str,
    workspace: str,
    test_command: str,
    chat_id: int
) -> str:
    """
    Execute complete bug report workflow with screenshot evidence.

    Args:
        notion_task_id: Notion task ID for tracking
        workspace: Target workspace (deckfusion, psyoptimal, etc.)
        test_command: Playwright test command to execute
        chat_id: Telegram chat for progress updates

    Returns:
        Workflow completion status with evidence links
    """
    # 1. Validate workspace access
    workspace_path = get_workspace_path(workspace)
    if not validate_directory_access(chat_id, workspace_path):
        return f"Access denied to {workspace} workspace"

    # 2. Set environment for test execution
    os.environ['NOTION_TASK_ID'] = notion_task_id

    # 3. Execute Playwright test in target workspace
    import subprocess
    try:
        result = subprocess.run(
            test_command,
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        if result.returncode != 0:
            return f"Test execution failed: {result.stderr}"

    except subprocess.TimeoutExpired:
        return "Test execution timed out (5 minutes)"

    # 4. Retrieve and upload screenshot
    screenshot_result = retrieve_workspace_screenshot(
        workspace=workspace,
        task_id=notion_task_id,
        chat_id=chat_id,
        max_age_minutes=5
    )

    # 5. Update Notion task with evidence
    notion_update = update_notion_task_with_evidence(
        task_id=notion_task_id,
        screenshot_evidence=screenshot_result,
        test_output=result.stdout
    )

    return f"Workflow completed: {screenshot_result} | Notion: {notion_update}"
```

### Phase 5: Testing and Validation

#### 5.1 Unit Tests
**File:** `/tests/test_screenshot_handoff.py`

```python
def test_screenshot_sharing_permissions():
    """Test that /tmp/ai_screenshots/ is accessible across workspaces."""

def test_screenshot_retrieval_tool():
    """Test MCP tool can find and process screenshots."""

def test_workflow_orchestration():
    """Test end-to-end bug report workflow."""

def test_cleanup_automation():
    """Test temporary file cleanup."""
```

#### 5.2 Integration Testing
- Test with real workspace Playwright setup
- Validate Telegram upload integration
- Confirm Notion task update workflow
- Verify security boundaries remain intact

## Security Considerations

### Maintained Security Boundaries
1. **Workspace Isolation**: Only `~/tmp/ai_screenshots/` is shared, all other directories remain isolated
2. **File Type Validation**: Only image files allowed in shared directory
3. **Time-based Cleanup**: Screenshots auto-deleted after upload
4. **Access Logging**: All cross-workspace access logged for audit

### Risk Mitigation
- **File Size Limits**: Prevent disk abuse with size restrictions
- **Rate Limiting**: Prevent screenshot spam
- **Path Validation**: Strict validation of screenshot file paths
- **Automatic Cleanup**: Remove files after processing to prevent accumulation

## Benefits and Trade-offs

### ✅ Benefits
- **Minimal Security Impact**: Only creates specific exception for temporary files
- **Leverages Existing Infrastructure**: Uses current image upload and Telegram integration
- **Clean Separation**: Target workspaces don't need AI system dependencies
- **Flexible**: Works with any workspace that can save to `/tmp/ai_screenshots/`

### ⚠️ Trade-offs
- **Temporary Storage Dependency**: Relies on shared filesystem
- **Timing Coordination**: Requires coordination between test execution and screenshot retrieval
- **Additional Cleanup Logic**: Need to manage temporary file lifecycle

## Implementation Timeline

1. **Week 1**: Update workspace validator and create shared directory infrastructure
2. **Week 2**: Implement screenshot retrieval MCP tool and test with manual files
3. **Week 3**: Create workflow orchestration tool and integrate with existing systems
4. **Week 4**: End-to-end testing and documentation

## Success Criteria

1. **Functional**: Screenshot successfully transferred from any workspace to AI agent
2. **Secure**: No compromise of existing workspace isolation
3. **Reliable**: 95%+ success rate for screenshot handoff
4. **Clean**: Automatic cleanup prevents disk accumulation
5. **Integrated**: Seamless integration with existing Notion and Telegram workflows

This solution provides a robust, secure mechanism for screenshot sharing while maintaining the security and modularity of the existing AI agent system.
