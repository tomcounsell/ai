# MCP Library & Session Management Requirements

**Status**: ✅ Core Implementation Complete (2026-01-19)
**Implementation**: `mcp/library.py`, `config/mcp_library.json`

Core features implemented:
- MCP server catalog with 10 servers configured
- Authentication status checking (ready/needs_setup/error)
- Task-based server selection with greedy set cover algorithm
- Capability-based querying and filtering

---

## Overview

Intelligent MCP (Model Context Protocol) server management to reduce Claude Code distractions and improve focus during sessions.

## Problem Statement

Currently, Claude Code can get distracted by having unnecessary MCP servers available for the task at hand. Manual LLM decision-making for skill selection is inefficient and leads to:
- Slower response times due to processing irrelevant tools
- Reduced accuracy from context pollution
- Wasted tokens on unused server descriptions

## Solution

Pre-session task analysis using Agent SDK to intelligently select only relevant MCP servers before Claude Code starts.

## Core Requirements

### 1. MCP Library Catalog

Each MCP server entry must track:

```yaml
mcp_id: string           # Unique identifier (e.g., "github")
name: string             # Display name
category: string         # Category (development/social/productivity)
capabilities: [string]   # List of capabilities (code/issues/calendar)
auth_status: enum        # ready | needs_auth | error
auth_type: enum          # none | token | oauth | session
```

### 2. Authentication States

Simple three-state system:

- **READY** ✅ - Authenticated and working, can be used immediately
- **NEEDS_AUTH** 🔐 - Requires human authentication before use
- **ERROR** ❌ - Broken or misconfigured, should be skipped

### 3. Task-Based Selection

The selection flow:

1. User provides task/request
2. Agent analyzes task to identify required capabilities
3. Query MCP library for servers with matching capabilities
4. Filter to only include servers with `auth_status: ready`
5. Configure Claude Code session with minimal MCP set
6. Alert user if critical MCPs need authentication

### 4. Example Scenarios

```yaml
# Scenario 1: Code Development
Task: "Fix the authentication bug in the login system"
Required capabilities: [code, testing, debugging]
Available MCPs:
  ✅ filesystem (ready) - selected
  ✅ pytest (ready) - selected
  🔐 github (needs_auth) - skipped, user notified
Result: Load only filesystem + pytest

# Scenario 2: Project Management
Task: "Update Linear tickets and schedule team meeting"
Required capabilities: [project_tracking, calendar]
Available MCPs:
  ✅ linear (ready) - selected
  ✅ google_calendar (ready) - selected
  ✅ github (ready) - not needed, skipped
Result: Load only linear + google_calendar
```

### 5. Implementation Architecture

```
User Request 
  → Task Analyzer (Agent SDK)
  → Capability Extraction
  → MCP Library Query
  → Auth Status Filter
  → Session Configuration
  → Claude Code (minimal MCPs)
  → Execution
```

### 6. Persistence & Learning

- Store MCP selection history per task type
- Learn common patterns (e.g., "bug fix" always needs filesystem + testing)
- Cache auth status with TTL
- Track usage statistics for optimization

### 7. User Notifications

When MCPs are unavailable due to auth:

```
⚠️ Some tools require authentication:
• GitHub: Run 'gh auth login' to authenticate
• Linear: Add LINEAR_API_KEY to your .env file

Continue with available tools? (y/n)
```

## Success Metrics

- **Reduction in Claude Code response time**: Target 30% faster
- **Improved task completion rate**: Target 95% success
- **Reduced token usage**: Target 40% fewer context tokens
- **User satisfaction**: Less manual tool selection

## Future Enhancements

- Auto-refresh for expiring tokens
- Fallback MCP suggestions when primary unavailable
- Dynamic MCP loading mid-session if needed
- MCP capability discovery through usage patterns

## Technical Notes

- MCP library stored in `config/mcp_library.json`
- Auth status checked on startup and cached for session
- Agent SDK handles pre-processing before Claude Code launch
- Supports both local and cloud-hosted MCP servers