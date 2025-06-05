# Software Engineering Tools Documentation

This document provides comprehensive documentation for the two primary software engineering tools in the AI agent system that integrate with Claude Code for development tasks.

## Overview

The system provides two distinct approaches for delegating software engineering tasks to Claude Code:

1. **`delegate_coding_task`** - PydanticAI agent tool for direct task execution
2. **`technical_analysis`** - MCP tool for research and analysis workflows

Both tools spawn Claude Code sessions but serve different purposes and use different architectural patterns.

## Table of Contents

- [Tool Comparison Matrix](#tool-comparison-matrix)
- [delegate_coding_task](#delegate_coding_task)
- [technical_analysis](#technical_analysis)
- [Architecture Analysis](#architecture-analysis)
- [Integration Patterns](#integration-patterns)
- [Best Practices](#best-practices)
- [Improvement Recommendations](#improvement-recommendations)

## Tool Comparison Matrix

| Aspect | delegate_coding_task | technical_analysis |
|--------|---------------------|-------------------|
| **Location** | `agents/valor/agent.py` + `tools/valor_delegation_tool.py` | `mcp_servers/social_tools.py` |
| **Architecture** | PydanticAI agent tool | MCP server tool |
| **Primary Purpose** | Execute development tasks | Research and analyze without modification |
| **File Modification** | Yes - writes, edits, commits code | No - read-only analysis |
| **Context Integration** | Automatic workspace detection via chat_id | MCP context injection system |
| **Subprocess Approach** | Direct `subprocess.run()` execution | Direct `subprocess.run()` execution |
| **Working Directory** | Smart workspace resolution | Basic workspace detection |
| **Error Handling** | Comprehensive with user-friendly fallbacks | Basic subprocess error handling |
| **Timeout** | No timeout (uses default) | 2-hour timeout for research tasks |
| **Response Format** | Structured execution results | Research findings with technical depth |

## delegate_coding_task

### Location and Architecture

**Primary Implementation:**
- `agents/valor/agent.py:286-358` - PydanticAI agent tool wrapper
- `tools/valor_delegation_tool.py` - Core implementation with subprocess management

**Integration Pattern:**
```python
@valor_agent.tool
def delegate_coding_task(
    ctx: RunContext[ValorContext],
    task_description: str,
    target_directory: str = "",
    specific_instructions: str = "",
) -> str:
```

### Core Functionality

**Purpose:** Execute actual development work including writing code, running tests, making git commits, and providing real implementation results.

**Key Features:**
1. **Autonomous Task Execution** - Actually performs the requested work
2. **Workspace Awareness** - Operates in correct project directories based on chat context
3. **Git Integration** - Can commit changes and follow development workflows
4. **Testing Support** - Runs tests and validates implementations
5. **Real Results** - Provides actual code changes, not just guidance

### Implementation Details

#### Workspace Resolution Logic
```python
# Determine working directory intelligently
working_dir = target_directory

# Auto-detect from chat_id if not specified
if not working_dir and ctx.deps.chat_id:
    from integrations.notion.utils import get_workspace_working_directory, get_dm_working_directory
    
    # Try group workspace directory first
    workspace_dir = get_workspace_working_directory(ctx.deps.chat_id)
    if workspace_dir:
        working_dir = workspace_dir
    elif ctx.deps.username and not ctx.deps.is_group_chat:
        # For DMs, use user-specific working directory
        dm_dir = get_dm_working_directory(ctx.deps.username)
        working_dir = dm_dir

# Fall back to current directory
if not working_dir:
    working_dir = "."
```

#### Prompt Construction
```python
prompt_parts = [
    f"TASK: {task_description}",
    "",
    f"WORKING DIRECTORY: {target_directory}",
    "",
    "INSTRUCTIONS:",
]

if specific_instructions:
    prompt_parts.extend([specific_instructions, ""])

prompt_parts.extend([
    "REQUIREMENTS:",
    "- Follow existing code patterns and conventions",
    "- Ensure all changes are properly tested if tests exist",
    "- Use appropriate git workflow (branch, commit, etc.)",
    "- Provide clear commit messages",
    "- Handle errors gracefully",
    "",
    "Execute this task autonomously and report results.",
])
```

#### Subprocess Execution
```python
def execute_valor_delegation(
    prompt: str,
    working_directory: str | None = None,
    allowed_tools: list[str] | None = None,
    timeout: int | None = None,
) -> str:
```

**Features:**
- Directory validation before execution
- Shell command construction for working directory changes
- Comprehensive error handling with detailed output
- Proper timeout management
- Tool permission configuration

### Usage Patterns

**Bug Fixes:**
```python
delegate_coding_task(ctx, "Fix the authentication bug in login system")
# Actually fixes the bug, runs tests, commits changes
```

**Feature Implementation:**
```python
delegate_coding_task(
    ctx, 
    "Add dark mode toggle to settings",
    specific_instructions="Use CSS variables for theme switching"
)
# Implements the feature with proper styling and tests
```

**Refactoring:**
```python
delegate_coding_task(ctx, "Refactor API endpoints for better organization")
# Performs refactoring while maintaining functionality
```

### Response Format

**Successful Execution:**
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

**Error Handling:**
```
❌ **Development Tool Error**

The Claude Code delegation failed: subprocess timeout

I can help you with this task directly instead. What specifically do you need assistance with?

For "Fix authentication bug", I can:
- Provide implementation guidance
- Share code examples
- Explain the approach step-by-step
- Review existing code if you share it
```

## technical_analysis

### Location and Architecture

**Primary Implementation:**
- `mcp_servers/social_tools.py:249-368` - MCP tool implementation

**Integration Pattern:**
```python
@mcp.tool()
def technical_analysis(
    research_topic: str, 
    focus_areas: str = "", 
    chat_id: str = ""
) -> str:
```

### Core Functionality

**Purpose:** Conduct comprehensive technical research and analysis using Claude Code's read-only capabilities, focused on understanding rather than modifying codebases.

**Key Features:**
1. **Research-Focused** - Explores codebases without modification
2. **Comprehensive Analysis** - Provides detailed technical insights
3. **Industry Research** - Investigates best practices and patterns
4. **Architecture Understanding** - Explains system design and decisions
5. **Read-Only Safety** - Never modifies files, only analyzes

### Implementation Details

#### Workspace Detection
```python
# Get workspace context if available
working_dir = "."
context_info = ""

if chat_id:
    try:
        from utilities.workspace_validator import get_workspace_validator
        validator = get_workspace_validator()
        workspace_dir = validator.get_working_directory(chat_id)
        if workspace_dir:
            working_dir = workspace_dir
            workspace_name = validator.get_workspace_for_chat(chat_id)
            context_info = f"Workspace: {workspace_name}, Directory: {working_dir}"
    except Exception:
        pass  # Continue with default directory
```

#### Research Prompt Construction
```python
prompt_parts = [
    f"TECHNICAL RESEARCH TASK: {research_topic}",
    "",
    "RESEARCH OBJECTIVES:",
    "- Conduct comprehensive technical analysis and investigation",
    "- Focus on understanding, not modifying files",
    "- Provide detailed findings with code examples and explanations", 
    "- Explore relevant files, documentation, and patterns",
    "- Research best practices and architectural decisions",
    "",
]

# Include focus areas and workspace context if available
if focus_areas:
    prompt_parts.extend([f"FOCUS AREAS: {focus_areas}", ""])
    
if context_info:
    prompt_parts.extend([f"WORKSPACE CONTEXT: {context_info}", ""])

prompt_parts.extend([
    "RESEARCH GUIDELINES:",
    "- Use Read, Glob, Grep, and other analysis tools extensively",
    "- Do NOT edit, write, or modify any files",
    "- Focus on understanding and explaining what exists",
    "- Provide code examples and architectural insights",
    "- Research industry standards and best practices",
    "- Explain your findings clearly with technical depth",
    "",
    "Conduct this technical research thoroughly and provide comprehensive analysis."
])
```

#### Subprocess Execution
```python
# Execute Claude Code for research
if working_dir and working_dir != ".":
    command = f'cd "{working_dir}" && claude code "{full_prompt}"'
    shell = True
else:
    command = ["claude", "code", full_prompt]
    shell = False

process = subprocess.run(
    command,
    check=True,
    capture_output=True,
    text=True,
    timeout=7200,  # 2 hour timeout for research tasks
    shell=shell
)

return f"🔬 **Technical Research Results**\n\n{process.stdout}"
```

### Usage Patterns

**Architecture Analysis:**
```python
technical_analysis("How does the authentication system work in this codebase?")
# Analyzes auth flows, security patterns, and implementation details
```

**Technology Research:**
```python
technical_analysis(
    "Compare different image compression approaches", 
    "performance, quality"
)
# Researches compression algorithms and their trade-offs
```

**API Documentation:**
```python
technical_analysis("What are the current API endpoints and their purposes?")
# Documents existing API structure and functionality
```

### Response Format

**Research Results:**
```
🔬 **Technical Research Results**

# Authentication System Analysis

## Overview
The authentication system uses a multi-layered approach with JWT tokens and OAuth2 integration...

## Key Components
1. **Authentication Service** (`src/auth/service.py`)
   - Handles user login/logout workflows
   - Manages JWT token generation and validation
   - Integrates with OAuth2 providers

2. **Authorization Middleware** (`src/auth/middleware.py`)
   - Route-level permission checking
   - Role-based access control (RBAC)
   - API key validation for external services

## Security Analysis
- Uses secure password hashing with bcrypt
- Implements proper session management
- CSRF protection enabled for web endpoints
- Rate limiting on authentication endpoints

## Recommendations
- Consider implementing refresh token rotation
- Add multi-factor authentication support
- Enhance audit logging for security events
```

**Error Handling:**
```
🔬 **Research Timeout**: Technical analysis of 'complex system architecture' exceeded 2 hours. Try breaking down into smaller research questions.

🔬 **Research Error**: Technical analysis failed: claude command not found

🔬 **Research Error**: Permission denied accessing /protected/directory
```

## Architecture Analysis

### Similarities

Both tools share several architectural patterns:

1. **Claude Code Integration** - Both spawn `claude code` subprocesses
2. **Working Directory Management** - Both handle directory context
3. **Subprocess Execution** - Both use `subprocess.run()` with similar patterns
4. **Error Handling** - Both catch and format subprocess errors
5. **Prompt Construction** - Both build comprehensive prompts for Claude Code

### Key Differences

#### 1. Integration Architecture

**delegate_coding_task:**
- **PydanticAI Tool Pattern** - Integrated as agent tool with runtime context
- **Automatic Context** - Receives chat_id, username, and conversation history
- **Type Safety** - Full Pydantic validation and type hints
- **Agent Integration** - Part of unified conversational development environment

**technical_analysis:**
- **MCP Tool Pattern** - Standalone tool exposed through Model Context Protocol
- **Context Injection** - Relies on MCP context manager for chat data
- **Claude Code Direct** - Called directly by Claude Code through MCP
- **Stateless Design** - No conversation state, operates independently

#### 2. Purpose and Safety

**delegate_coding_task:**
- **Execution-Focused** - Designed to actually perform development work
- **File Modification** - Writes, edits, commits, and deploys code
- **Git Integration** - Creates branches, commits changes, follows workflows
- **Testing Integration** - Runs tests, validates implementations
- **Production Impact** - Can affect live codebases and systems

**technical_analysis:**
- **Research-Focused** - Designed for investigation and understanding
- **Read-Only Operation** - Explicitly prevents file modification
- **Analysis Tools** - Uses Read, Glob, Grep, LS for exploration
- **Knowledge Generation** - Produces insights, documentation, recommendations
- **Safe Operation** - Cannot break existing systems or code

#### 3. Error Handling Sophistication

**delegate_coding_task:**
```python
# Comprehensive error handling with user guidance
try:
    result = spawn_valor_session(...)
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
```

**technical_analysis:**
```python
# Basic subprocess error handling
except subprocess.TimeoutExpired:
    return f"🔬 **Research Timeout**: Technical analysis of '{research_topic}' exceeded 2 hours..."
    
except subprocess.CalledProcessError as e:
    return f"🔬 **Research Error**: Technical analysis failed: {e.stderr or 'Unknown error'}"
    
except Exception as e:
    return f"🔬 **Research Error**: {str(e)}"
```

#### 4. Workspace Resolution

**delegate_coding_task:**
```python
# Sophisticated workspace resolution
working_dir = target_directory

if not working_dir and ctx.deps.chat_id:
    # Try group workspace directory
    workspace_dir = get_workspace_working_directory(ctx.deps.chat_id)
    if workspace_dir:
        working_dir = workspace_dir
    elif ctx.deps.username and not ctx.deps.is_group_chat:
        # For DMs, use user-specific working directory
        dm_dir = get_dm_working_directory(ctx.deps.username)
        working_dir = dm_dir

# Fallback to current directory
if not working_dir:
    working_dir = "."
```

**technical_analysis:**
```python
# Basic workspace detection
working_dir = "."
context_info = ""

if chat_id:
    try:
        validator = get_workspace_validator()
        workspace_dir = validator.get_working_directory(chat_id)
        if workspace_dir:
            working_dir = workspace_dir
            workspace_name = validator.get_workspace_for_chat(chat_id)
            context_info = f"Workspace: {workspace_name}, Directory: {working_dir}"
    except Exception:
        pass  # Continue with default directory
```

## Integration Patterns

### PydanticAI Agent Integration (delegate_coding_task)

**Benefits:**
- **Automatic Context** - Receives full conversation context automatically
- **Type Safety** - Pydantic validation ensures correct parameter types
- **Agent Ecosystem** - Integrates with other agent tools seamlessly
- **Conversation Continuity** - Can reference previous messages and context
- **Error Recovery** - Can provide alternative assistance if delegation fails

**Trade-offs:**
- **Coupling** - Tightly coupled to PydanticAI agent system
- **Complexity** - More complex integration with multiple layers
- **Dependencies** - Requires full agent infrastructure to function

### MCP Tool Integration (technical_analysis)

**Benefits:**
- **Direct Access** - Called directly by Claude Code without intermediaries
- **Stateless Design** - No dependencies on conversation state
- **Modular Architecture** - Can be used independently of agent system
- **Performance** - Direct execution without agent overhead
- **Flexibility** - Can be called from any Claude Code session

**Trade-offs:**
- **Context Management** - Requires explicit context injection system
- **Limited Context** - May not have full conversation history
- **Error Handling** - Less sophisticated error recovery options
- **Integration Complexity** - Requires MCP server configuration

## Best Practices

### When to Use Each Tool

#### Use delegate_coding_task when:
- **Implementation Required** - Need to actually write, modify, or deploy code
- **Agent Context Matters** - Conversation history and chat context are important
- **Testing Required** - Need to run tests and validate implementations
- **Git Workflow** - Need to commit changes and follow development practices
- **User Guidance** - Want sophisticated error handling and alternative assistance

**Examples:**
- "Fix the authentication bug in the login system"
- "Add a dark mode toggle to the application settings"
- "Refactor the API endpoints for better performance"
- "Update dependencies and run all tests"

#### Use technical_analysis when:
- **Research Required** - Need to understand existing systems without modification
- **Analysis Focus** - Want detailed technical insights and documentation
- **Safety Critical** - Cannot risk modifying production code
- **Architecture Review** - Need to understand system design and patterns
- **Documentation** - Want to generate technical documentation

**Examples:**
- "How does the authentication system work in this codebase?"
- "Compare different image compression approaches for performance"
- "What are the current API endpoints and their purposes?"
- "Analyze the database schema and relationships"

### Development Guidelines

#### For delegate_coding_task:

1. **Always Specify Context**
   ```python
   # Good: Provide clear task description and context
   delegate_coding_task(
       ctx,
       "Fix authentication timeout bug",
       specific_instructions="Focus on session management and token refresh"
   )
   
   # Avoid: Vague or ambiguous requests
   delegate_coding_task(ctx, "Fix stuff")
   ```

2. **Handle Errors Gracefully**
   ```python
   # Tool already handles this, but be prepared for guidance responses
   result = delegate_coding_task(ctx, task_description)
   if "Development Tool Error" in result:
       # Tool failed, but provided guidance - follow up as needed
   ```

3. **Leverage Workspace Context**
   ```python
   # Tool automatically resolves workspace from chat_id
   # No need to specify target_directory unless overriding
   ```

#### For technical_analysis:

1. **Be Specific About Research Goals**
   ```python
   # Good: Clear research topic and focus areas
   technical_analysis(
       "How does user authentication work?",
       "security, performance, scalability"
   )
   
   # Avoid: Overly broad requests
   technical_analysis("Tell me about the system")
   ```

2. **Use for Read-Only Analysis**
   ```python
   # Good: Research and analysis tasks
   technical_analysis("What are the current API rate limiting strategies?")
   
   # Wrong: Implementation requests (use delegate_coding_task instead)
   technical_analysis("Implement rate limiting for the API")
   ```

3. **Leverage Extended Timeout**
   ```python
   # Tool has 2-hour timeout for complex research
   technical_analysis("Comprehensive architecture analysis of the entire system")
   ```

### Error Handling Patterns

#### delegate_coding_task Error Recovery:
```python
async def handle_coding_task(ctx, task):
    result = delegate_coding_task(ctx, task)
    
    if "Development Tool Error" in result:
        # Tool provides guidance, can follow up with more specific help
        return result + "\n\nWould you like me to break this down into smaller steps?"
    else:
        # Task completed successfully
        return result
```

#### technical_analysis Error Recovery:
```python
def handle_research_task(topic, focus_areas=""):
    result = technical_analysis(topic, focus_areas)
    
    if "Research Timeout" in result:
        # Break down into smaller research questions
        return "Let me break this into smaller research questions..."
    elif "Research Error" in result:
        # Provide alternative analysis approach
        return "I can help analyze this using alternative methods..."
    else:
        return result
```

## Implementation Analysis

### Subprocess Execution Patterns

Both tools use nearly identical subprocess execution patterns with subtle differences:

**delegate_coding_task (`tools/valor_delegation_tool.py:142-164`)**:
```python
process = subprocess.run(
    command, 
    check=True, 
    capture_output=True, 
    text=True, 
    timeout=timeout,  # Variable timeout
    shell=shell
)
# Raises exceptions for error handling
```

**technical_analysis (`mcp_servers/social_tools.py:350-368`)**:
```python
process = subprocess.run(
    command,
    check=True,
    capture_output=True,
    text=True,
    timeout=7200,  # Fixed 2-hour timeout
    shell=shell
)
# Returns formatted error strings
```

### Working Directory Resolution

**delegate_coding_task** - Sophisticated workspace detection:
```python
# agents/valor/agent.py:330-342
if not working_dir and ctx.deps.chat_id:
    workspace_dir = get_workspace_working_directory(ctx.deps.chat_id)
    if workspace_dir:
        working_dir = workspace_dir
    elif ctx.deps.username and not ctx.deps.is_group_chat:
        dm_dir = get_dm_working_directory(ctx.deps.username)
        working_dir = dm_dir
```

**technical_analysis** - Basic workspace detection:
```python
# mcp_servers/social_tools.py:291-301
if chat_id:
    validator = get_workspace_validator()
    workspace_dir = validator.get_working_directory(chat_id)
    if workspace_dir:
        working_dir = workspace_dir
```

### Error Handling Strategies

**delegate_coding_task** - Exception-based with user-friendly fallbacks:
```python
# agents/valor/agent.py:354-355
except Exception as e:
    return f"Error providing development guidance: {str(e)}"
```

**technical_analysis** - Direct error string returns:
```python
# mcp_servers/social_tools.py:361-368
except subprocess.TimeoutExpired:
    return f"🔬 **Research Timeout**: ..."
except subprocess.CalledProcessError as e:
    return f"🔬 **Research Error**: ..."
```

## Improvement Recommendations

### 1. Consolidate Subprocess Execution

**Current Issue:** Both tools implement nearly identical subprocess execution with subtle differences causing maintenance overhead.

**Recommended Solution:** Create shared utility in `utilities/claude_code_executor.py`:

```python
from typing import Optional, Union
import subprocess
import os

class ClaudeCodeExecutor:
    @staticmethod
    def execute_claude_code(
        prompt: str,
        working_directory: str = ".",
        timeout: Optional[int] = None,
        tools_allowed: Optional[list[str]] = None
    ) -> tuple[bool, str]:
        """
        Unified Claude Code execution with consistent error handling.
        
        Returns:
            tuple[bool, str]: (success, result_or_error_message)
        """
        # Build command
        if working_directory and working_directory != ".":
            command = f'cd "{working_directory}" && claude code "{prompt}"'
            shell = True
        else:
            command = ["claude", "code", prompt]
            shell = False
        
        try:
            process = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=shell
            )
            return True, process.stdout
            
        except subprocess.TimeoutExpired:
            return False, f"⏱️ Claude Code execution timed out after {timeout} seconds"
        except subprocess.CalledProcessError as e:
            error_msg = f"❌ Claude Code failed (exit {e.returncode})"
            if e.stderr:
                error_msg += f": {e.stderr}"
            return False, error_msg
        except Exception as e:
            return False, f"❌ Execution error: {str(e)}"
```

**Benefits:**
- Single source of truth for Claude Code execution
- Consistent error handling across tools
- Easier testing and maintenance
- Standardized timeout handling

### 2. Unify Workspace Resolution

**Current Issue:** Different workspace detection strategies with overlapping functionality.

**Recommended Solution:** Enhance `utilities/workspace_validator.py`:

```python
class WorkspaceResolver:
    @staticmethod
    def resolve_working_directory(
        chat_id: Optional[str] = None,
        username: Optional[str] = None,
        is_group_chat: bool = False,
        target_directory: str = ""
    ) -> tuple[str, str]:
        """
        Unified workspace resolution logic.
        
        Returns:
            tuple[str, str]: (working_directory, context_description)
        """
        if target_directory:
            return target_directory, f"Explicit directory: {target_directory}"
            
        if chat_id:
            # Try group workspace first
            workspace_dir = get_workspace_working_directory(chat_id)
            if workspace_dir:
                workspace_name = get_workspace_for_chat(chat_id)
                return workspace_dir, f"Workspace: {workspace_name}"
                
            # Try DM directory for private chats
            if username and not is_group_chat:
                dm_dir = get_dm_working_directory(username)
                return dm_dir, f"DM directory for @{username}"
        
        return ".", "Current directory (no workspace context)"
```

### 3. Standardize Response Formatting

**Current Issue:** Inconsistent response formats between tools.

**Recommended Solution:** Create response formatters in `utilities/swe_response_formatter.py`:

```python
class SWEResponseFormatter:
    @staticmethod
    def format_execution_result(
        tool_name: str,
        task_description: str,
        success: bool,
        result: str,
        working_directory: str,
        execution_time: float = None
    ) -> str:
        """Standard format for execution results."""
        
        status_emoji = "✅" if success else "❌"
        header = f"{status_emoji} **{tool_name} {'Completed' if success else 'Failed'}**"
        
        sections = [
            header,
            "",
            f"**Task:** {task_description}",
            f"**Directory:** {working_directory}",
        ]
        
        if execution_time:
            sections.append(f"**Duration:** {execution_time:.1f}s")
            
        sections.extend(["", "**Results:**", result])
        
        return "\n".join(sections)
    
    @staticmethod  
    def format_research_result(
        research_topic: str,
        findings: str,
        focus_areas: str = "",
        working_directory: str = "."
    ) -> str:
        """Standard format for research results."""
        
        sections = [
            "🔬 **Technical Research Complete**",
            "",
            f"**Research Topic:** {research_topic}",
        ]
        
        if focus_areas:
            sections.append(f"**Focus Areas:** {focus_areas}")
            
        sections.extend([
            f"**Analyzed:** {working_directory}",
            "",
            "**Findings:**",
            findings
        ])
        
        return "\n".join(sections)
```

### 4. Implement Intelligent Tool Selection

**Current Issue:** Users must manually choose between delegate_coding_task and technical_analysis.

**Recommended Solution:** Add decision logic to agent system:

```python
def suggest_swe_tool(user_request: str) -> tuple[str, str]:
    """
    Analyze user request and suggest appropriate SWE tool.
    
    Returns:
        tuple[str, str]: (recommended_tool, reasoning)
    """
    modification_keywords = [
        "fix", "implement", "add", "create", "build", "update", 
        "refactor", "delete", "modify", "change", "commit"
    ]
    
    analysis_keywords = [
        "analyze", "research", "investigate", "understand", "explain",
        "review", "study", "explore", "examine", "document"
    ]
    
    request_lower = user_request.lower()
    
    has_modification = any(keyword in request_lower for keyword in modification_keywords)
    has_analysis = any(keyword in request_lower for keyword in analysis_keywords)
    
    if has_modification and not has_analysis:
        return "delegate_coding_task", "Request involves code modification"
    elif has_analysis and not has_modification:
        return "technical_analysis", "Request is research/analysis focused"
    elif has_modification and has_analysis:
        return "delegate_coding_task", "Mixed request, defaulting to execution tool"
    else:
        return "technical_analysis", "Unclear intent, defaulting to safe analysis"
```

### 5. Add Execution Monitoring

**Current Issue:** No visibility into tool usage patterns or success rates.

**Recommended Solution:** Add monitoring to both tools:

```python
# utilities/swe_monitoring.py
from utilities.database import get_database_connection
import time
import json

class SWEMonitor:
    @staticmethod
    def log_execution(
        tool_name: str,
        task_description: str,
        working_directory: str,
        success: bool,
        execution_time: float,
        error_message: str = None,
        chat_id: str = None
    ):
        """Log SWE tool execution for monitoring and analytics."""
        
        conn = get_database_connection()
        conn.execute("""
            INSERT INTO swe_executions 
            (tool_name, task_description, working_directory, success, 
             execution_time, error_message, chat_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tool_name, task_description, working_directory, success,
            execution_time, error_message, chat_id, time.time()
        ))
        conn.commit()
    
    @staticmethod
    def get_success_rate(tool_name: str = None, days: int = 7) -> dict:
        """Get success rate statistics for SWE tools."""
        
        conn = get_database_connection()
        where_clause = "WHERE timestamp > ?" 
        params = [time.time() - (days * 24 * 3600)]
        
        if tool_name:
            where_clause += " AND tool_name = ?"
            params.append(tool_name)
            
        cursor = conn.execute(f"""
            SELECT tool_name, 
                   COUNT(*) as total,
                   SUM(CASE WHEN success THEN 1 ELSE 0 END) as successful,
                   AVG(execution_time) as avg_time
            FROM swe_executions 
            {where_clause}
            GROUP BY tool_name
        """, params)
        
        return {row[0]: {
            'total': row[1], 
            'successful': row[2], 
            'success_rate': row[2]/row[1] if row[1] > 0 else 0,
            'avg_time': row[3]
        } for row in cursor.fetchall()}
```

### 6. Enhanced Error Recovery

**Current Issue:** Limited error recovery and user guidance on failures.

**Recommended Solution:** Add intelligent error recovery:

```python
class SWEErrorRecovery:
    @staticmethod
    def suggest_recovery(
        tool_name: str, 
        error_message: str, 
        task_description: str
    ) -> str:
        """Provide intelligent recovery suggestions based on error patterns."""
        
        error_lower = error_message.lower()
        
        if "timeout" in error_lower:
            return (
                "💡 **Recovery Suggestion:** Task may be too complex for single execution. "
                "Try breaking it into smaller steps or use technical_analysis first "
                "to understand the scope."
            )
        elif "permission denied" in error_lower:
            return (
                "💡 **Recovery Suggestion:** Check file permissions and working directory. "
                "Ensure Claude Code has access to the target files."
            )
        elif "command not found" in error_lower:
            return (
                "💡 **Recovery Suggestion:** Claude Code may not be in PATH. "
                "Verify installation with `claude --version`."
            )
        elif "no such file" in error_lower:
            return (
                "💡 **Recovery Suggestion:** Working directory may be incorrect. "
                "Verify the target directory exists and contains the expected files."
            )
        else:
            return (
                f"💡 **Recovery Suggestion:** Consider using {'technical_analysis' if tool_name == 'delegate_coding_task' else 'delegate_coding_task'} "
                f"as an alternative approach for: {task_description}"
            )
```

### 7. Database Schema for SWE Operations

**Recommended Addition:** Add SWE execution tracking to database schema:

```sql
-- Add to utilities/database.py
CREATE TABLE IF NOT EXISTS swe_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT NOT NULL,
    task_description TEXT NOT NULL,
    working_directory TEXT NOT NULL,
    success BOOLEAN NOT NULL,
    execution_time REAL,
    error_message TEXT,
    chat_id TEXT,
    timestamp REAL NOT NULL,
    result_summary TEXT
);

CREATE INDEX IF NOT EXISTS idx_swe_executions_timestamp ON swe_executions(timestamp);
CREATE INDEX IF NOT EXISTS idx_swe_executions_tool ON swe_executions(tool_name);
CREATE INDEX IF NOT EXISTS idx_swe_executions_success ON swe_executions(success);
```

### 8. Integration Testing Framework

**Current Issue:** No automated testing for cross-tool workflows.

**Recommended Solution:** Create integration test suite:

```python
# tests/test_swe_integration.py
class TestSWEIntegration:
    
    def test_tool_selection_logic(self):
        """Test that tool selection recommends correct tool for various requests."""
        
        test_cases = [
            ("Fix the authentication bug", "delegate_coding_task"),
            ("How does the auth system work?", "technical_analysis"),
            ("Research best practices for API design", "technical_analysis"),
            ("Implement user registration", "delegate_coding_task"),
        ]
        
        for request, expected_tool in test_cases:
            recommended_tool, _ = suggest_swe_tool(request)
            assert recommended_tool == expected_tool
    
    def test_workspace_resolution_consistency(self):
        """Test that both tools resolve workspaces consistently."""
        
        # Test with same inputs
        chat_id = "12345"
        username = "testuser"
        
        # Both should resolve to same workspace
        delegate_dir = resolve_working_directory(chat_id, username, False)
        analysis_dir = resolve_working_directory(chat_id, username, False)
        
        assert delegate_dir == analysis_dir
    
    def test_error_handling_consistency(self):
        """Test that both tools handle similar errors consistently."""
        
        # Test timeout scenarios
        # Test permission errors  
        # Test invalid directory errors
        pass
```

## Summary

The two SWE tools provide complementary capabilities but suffer from code duplication and inconsistent patterns. The key improvements would:

1. **Consolidate common functionality** into shared utilities
2. **Standardize response formats** for consistent user experience  
3. **Unify workspace resolution** to eliminate behavioral differences
4. **Add intelligent tool selection** to guide users to the right tool
5. **Implement monitoring and analytics** for system improvement
6. **Enhance error recovery** with actionable guidance
7. **Create comprehensive testing** for integration scenarios

These improvements would create a more cohesive, maintainable, and powerful software engineering automation platform while preserving the distinct strengths of each tool.

---

*This documentation serves as the definitive guide for understanding, using, and improving the software engineering automation tools in the AI agent system.*
