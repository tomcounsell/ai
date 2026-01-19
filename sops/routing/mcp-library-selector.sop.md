# MCP Library Selector SOP

**Version**: 1.0.0
**Last Updated**: 2026-01-19
**Owner**: Valor System
**Status**: Active

## Overview

Select the optimal set of MCP servers to load based on task requirements and authentication status. This minimizes context pollution while ensuring all needed capabilities are available.

## Prerequisites

- Task analysis must be complete (from Task Analyzer SOP)
- MCP Library catalog must be loaded
- Environment variables accessible for auth checking

## Parameters

### Required
- **required_capabilities** (list[str]): Capabilities needed for the task
  - Example: `["code", "issues", "deployment"]`
  - Min items: 1

### Optional
- **auth_status_check** (bool): Whether to verify authentication
  - Default: `true`
- **min_servers** (int): Minimum servers to load
  - Default: `1`
- **max_servers** (int): Maximum servers to load
  - Default: `5`
- **prefer_ready** (bool): Prefer authenticated servers
  - Default: `true`

## Steps

### 1. Query MCP Library
**Purpose**: Find servers matching required capabilities

**Actions**:
- MUST load MCP library catalog
- MUST query for servers matching each capability
- SHOULD build capability-to-server mapping
- MAY cache results for session duration

**Validation**:
- Library loaded successfully
- At least one server matches requested capabilities

**Error Handling**:
- If library fails to load, use hardcoded fallback list
- If no matches found, return empty with warning

### 2. Filter by Authentication Status
**Purpose**: Only include servers ready for use

**Actions**:
- MUST check auth_status for each candidate server
- MUST verify environment variables are set for token-based auth
- SHOULD prioritize servers with `auth_status: ready`
- MAY include `needs_setup` servers with warning

**Validation**:
- Each selected server has valid auth status
- Required env vars are present and non-empty

**Error Handling**:
- Skip servers with invalid auth
- Collect auth setup instructions for skipped servers

### 3. Apply Greedy Set Cover
**Purpose**: Select minimal set covering all capabilities

**Actions**:
- MUST use greedy algorithm to minimize server count
- SHOULD prefer servers covering more capabilities
- SHOULD prefer authenticated servers over unauthenticated
- MAY break ties by server priority/reliability

**Validation**:
- All requested capabilities are covered
- Server count is within min/max bounds

**Error Handling**:
- If coverage impossible, return partial with uncovered list
- Suggest alternatives for uncovered capabilities

### 4. Generate Auth Alerts
**Purpose**: Notify user of auth requirements

**Actions**:
- MUST identify servers needing auth setup
- SHOULD format setup instructions clearly
- MAY offer to skip servers if alternatives exist
- MAY suggest auth priority order

**Validation**:
- All alerts have actionable instructions
- Critical servers are highlighted

**Error Handling**:
- If all critical servers need auth, block and alert
- For optional servers, continue with warning

### 5. Configure Session
**Purpose**: Prepare server configuration for session

**Actions**:
- MUST create server load configuration
- SHOULD set appropriate timeouts per server
- MAY configure rate limiting
- MAY set up fallback servers

**Validation**:
- Configuration is valid JSON/dict
- All servers in config are accessible

**Error Handling**:
- Invalid config falls back to default
- Log configuration for debugging

## Success Criteria

- All required capabilities covered by selected servers
- All selected servers have valid authentication
- Server count is minimized
- User notified of any auth requirements
- Session configuration ready for use

## Error Recovery

- **No Matching Servers**: Suggest capability alternatives or ask user to rephrase
- **Auth Failed**: Provide step-by-step setup guide, offer to continue without
- **Partial Coverage**: List uncovered capabilities, ask if acceptable
- **Too Many Servers**: Prioritize by task domain, ask user to confirm

## Examples

### Example 1: Development Task
```
Input:
  required_capabilities: ["code", "issues", "testing"]
  auth_status_check: true

Expected Output:
  selected_servers:
    - mcp_id: "github"
      capabilities_matched: ["code", "issues"]
      auth_status: "ready"
    - mcp_id: "code_execution"
      capabilities_matched: ["testing"]
      auth_status: "ready"
  coverage: 100%
  auth_alerts: []
```

### Example 2: Mixed Auth Status
```
Input:
  required_capabilities: ["code", "issues", "deployment"]
  auth_status_check: true

Expected Output:
  selected_servers:
    - mcp_id: "github"
      auth_status: "ready"
    - mcp_id: "render"
      auth_status: "needs_setup"
  coverage: 100%
  auth_alerts:
    - server: "render"
      message: "Render requires authentication"
      instructions: "1. Go to Render Account Settings..."
      critical: true
```

### Example 3: Partial Coverage
```
Input:
  required_capabilities: ["code", "quantum_computing"]

Expected Output:
  selected_servers:
    - mcp_id: "github"
      capabilities_matched: ["code"]
  coverage: 50%
  uncovered: ["quantum_computing"]
  suggestion: "No MCP server supports quantum_computing. Consider using web search for research."
```

## Related SOPs

- [Task Analyzer](task-analyzer.sop.md)
- [Coding Agent Router](coding-agent-router.sop.md)

## Version History

- v1.0.0 (2026-01-19): Initial version
