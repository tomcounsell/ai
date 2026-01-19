# Coding Agent Router SOP

**Version**: 1.0.0
**Last Updated**: 2026-01-19
**Owner**: Valor System
**Status**: Active

## Overview

Route coding tasks to the optimal execution agent based on task characteristics. Choose between Claude Code (interactive, MCP-enabled) and Gemini CLI (autonomous, cost-optimized) based on task requirements.

## Prerequisites

- Task analysis complete with complexity assessment
- MCP server selection complete
- Agent availability verified

## Parameters

### Required
- **task_type** (enum): Type of coding task
  - Values: `interactive`, `autonomous`, `batch`
  - Example: `autonomous`

### Optional
- **file_count** (int): Number of files involved
  - Default: `1`
- **requires_mcp** (bool): Whether MCP tools are needed
  - Default: `false`
- **complexity** (enum): Task complexity level
  - Values: `simple`, `medium`, `complex`
  - Default: `medium`
- **cost_sensitive** (bool): Optimize for cost
  - Default: `false`
- **time_sensitive** (bool): Optimize for speed
  - Default: `false`

## Steps

### 1. Analyze Task Characteristics
**Purpose**: Understand task requirements for routing

**Actions**:
- MUST identify if task is interactive or autonomous
- MUST determine MCP tool requirements
- SHOULD estimate file scope (single, few, many)
- MAY check historical patterns for similar tasks

**Validation**:
- Task type clearly identified
- MCP requirement determined

**Error Handling**:
- Default to `interactive` if ambiguous
- Default `requires_mcp` to `true` for safety

### 2. Check MCP Tool Requirements
**Purpose**: Determine if Claude Code's MCP integration is needed

**Actions**:
- MUST check if task requires external APIs (GitHub, Sentry, etc.)
- MUST verify if MCP servers are available
- SHOULD prefer MCP for complex integrations
- MAY use direct API calls for simple operations

**Validation**:
- Required MCP servers are loaded
- Auth status is valid for required servers

**Error Handling**:
- If MCP unavailable, suggest alternatives
- Fall back to CLI tools when possible

### 3. Assess Complexity and Scope
**Purpose**: Match agent capabilities to task demands

**Actions**:
- MUST evaluate code complexity:
  - `simple`: Single file, clear change
  - `medium`: Few files, some reasoning
  - `complex`: Many files, architectural decisions
- SHOULD consider user interaction needs
- MAY factor in debugging likelihood

**Validation**:
- Complexity aligns with task description
- Scope is realistic for chosen agent

**Error Handling**:
- Upgrade complexity if uncertain
- Split complex tasks into phases

### 4. Apply Routing Rules
**Purpose**: Select the optimal agent

**Actions**:
- MUST apply routing decision tree:
  ```
  IF requires_mcp OR complexity == "complex":
    SELECT claude_code
  ELIF task_type == "autonomous" AND complexity == "simple":
    SELECT gemini_cli
  ELIF cost_sensitive AND NOT time_sensitive:
    SELECT gemini_cli
  ELSE:
    SELECT claude_code
  ```
- SHOULD log routing decision for analytics
- MAY allow user override

**Validation**:
- Selected agent is available
- Selection matches task requirements

**Error Handling**:
- If primary agent unavailable, use backup
- Alert user if forced to suboptimal choice

### 5. Configure Execution Context
**Purpose**: Prepare agent for task execution

**Actions**:
- MUST set agent context (working directory, files)
- MUST configure available tools
- SHOULD set resource limits
- MAY inject task-specific prompts

**Validation**:
- Context is complete and valid
- Tools are accessible

**Error Handling**:
- Missing context: request from user
- Tool errors: log and continue if non-critical

## Success Criteria

- Agent selected based on clear criteria
- Task requirements fully covered by agent capabilities
- Execution context properly configured
- Routing decision logged for analysis
- User informed of agent selection (if relevant)

## Error Recovery

- **Both Agents Unavailable**: Queue task, notify user, provide manual instructions
- **MCP Required but Unavailable**: Suggest auth setup, offer degraded mode
- **Complexity Mismatch**: Offer to split task or adjust approach
- **Timeout**: Fall back to simpler agent, reduce scope

## Examples

### Example 1: Simple Autonomous Task
```
Input:
  task_type: "autonomous"
  requires_mcp: false
  complexity: "simple"
  cost_sensitive: true

Routing Decision:
  agent: "gemini_cli"
  reason: "Simple autonomous task without MCP needs, cost-optimized"
```

### Example 2: MCP-Required Task
```
Input:
  task_type: "interactive"
  requires_mcp: true
  complexity: "medium"
  cost_sensitive: false

Routing Decision:
  agent: "claude_code"
  reason: "MCP tools required for GitHub/Sentry integration"
```

### Example 3: Complex Development
```
Input:
  task_type: "interactive"
  requires_mcp: false
  complexity: "complex"
  file_count: 15

Routing Decision:
  agent: "claude_code"
  reason: "Complex multi-file task requires advanced reasoning"
```

### Example 4: Batch Operations
```
Input:
  task_type: "batch"
  requires_mcp: false
  complexity: "simple"
  file_count: 50

Routing Decision:
  agent: "gemini_cli"
  reason: "Batch of simple operations, cost-optimized"
  configuration:
    parallel: true
    max_concurrent: 5
```

## Agent Comparison

| Criteria | Claude Code | Gemini CLI |
|----------|-------------|------------|
| MCP Support | Full | Limited |
| Interactive Mode | Excellent | Basic |
| Cost | Higher | Lower |
| Complex Reasoning | Excellent | Good |
| Batch Processing | Good | Excellent |
| Autonomy | Medium | High |

## Related SOPs

- [Task Analyzer](task-analyzer.sop.md)
- [MCP Library Selector](mcp-library-selector.sop.md)

## Version History

- v1.0.0 (2026-01-19): Initial version
