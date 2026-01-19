# Task Analyzer SOP

**Version**: 1.0.0
**Last Updated**: 2026-01-19
**Owner**: Valor System
**Status**: Active

## Overview

Analyze incoming user requests to determine required capabilities, identify the domain, and assess task complexity. This SOP is the first step in the routing pipeline and determines how subsequent systems handle the request.

## Prerequisites

- User request must be received
- Conversation context must be available
- MCP Library must be accessible

## Parameters

### Required
- **user_query** (string): The raw user request text
  - Example: `"Fix the authentication bug in the login system"`
  - Min length: 1 character

### Optional
- **conversation_context** (list): Recent conversation history
  - Default: `[]`
  - Max items: 10 most recent messages
- **user_preferences** (dict): User-specific settings
  - Default: `{}`

## Steps

### 1. Parse User Intent
**Purpose**: Extract the core action the user wants to perform

**Actions**:
- MUST identify the primary verb (fix, create, search, explain, etc.)
- MUST extract the target object (code, document, issue, etc.)
- SHOULD identify any constraints or preferences mentioned
- MAY note the urgency level if expressed

**Validation**:
- Intent must have at least one verb and one object
- If ambiguous, flag for clarification

**Error Handling**:
- If no clear intent detected, ask user for clarification
- If multiple conflicting intents, prioritize based on conversation context

### 2. Extract Required Capabilities
**Purpose**: Determine which system capabilities are needed

**Actions**:
- MUST map intent to capability categories:
  - Code operations: `code`, `debugging`, `testing`
  - Documentation: `knowledge`, `documentation`, `search`
  - Project management: `issues`, `projects`, `tracking`
  - Infrastructure: `deployment`, `monitoring`, `logs`
  - Communication: `messaging`, `notifications`
- SHOULD identify secondary capabilities needed
- MAY suggest additional helpful capabilities

**Validation**:
- At least one capability must be identified
- Capabilities must exist in MCP Library

**Error Handling**:
- Unknown capability maps to `general` category
- Log unmapped intents for future improvement

### 3. Identify Domain
**Purpose**: Categorize the request into a domain area

**Actions**:
- MUST classify into one primary domain:
  - `development`: Code, bugs, features, testing
  - `productivity`: Tasks, documentation, planning
  - `business`: Payments, customers, analytics
  - `infrastructure`: Deployment, monitoring, scaling
  - `research`: Search, analysis, exploration
- SHOULD consider conversation history for context
- MAY identify secondary domains

**Validation**:
- Domain must be a valid category
- Check if domain matches user's typical patterns

**Error Handling**:
- Default to `development` for code-related queries
- Default to `research` for questions

### 4. Assess Complexity
**Purpose**: Determine the expected effort and approach

**Actions**:
- MUST assign complexity level:
  - `simple`: Single action, immediate response (<30s)
  - `medium`: Multiple steps, tool usage (30s-2m)
  - `complex`: Multi-phase work, planning required (>2m)
- SHOULD estimate number of tool calls needed
- MAY flag tasks requiring human oversight

**Validation**:
- Complexity must align with detected capabilities
- Cross-check against similar historical tasks

**Error Handling**:
- Default to `medium` if uncertain
- Escalate to user if very complex

### 5. Determine Interaction Mode
**Purpose**: Choose how to handle the request

**Actions**:
- MUST select interaction mode:
  - `interactive`: Requires user input during execution
  - `autonomous`: Can complete without user interaction
  - `batch`: Multiple independent subtasks
- SHOULD set response style (detailed, concise, technical)
- MAY configure output format

**Validation**:
- Mode must be appropriate for complexity level
- Check user preferences for overrides

**Error Handling**:
- Default to `interactive` for complex or ambiguous tasks
- Use `autonomous` only for well-defined tasks

## Success Criteria

- Intent clearly identified with confidence >0.8
- At least one capability mapped
- Domain assigned
- Complexity level set
- Interaction mode determined
- Output is a structured TaskAnalysis object

## Error Recovery

- **Ambiguous Intent**: Return top 2-3 interpretations, ask user to clarify
- **No Capability Match**: Flag as unsupported, suggest alternatives
- **Context Missing**: Proceed with available info, note limitations
- **Timeout**: Return partial analysis with confidence scores

## Examples

### Example 1: Bug Fix Request
```
Input:
  user_query: "Fix the authentication bug in the login system"
  conversation_context: []

Expected Output:
  intent:
    verb: "fix"
    object: "authentication bug"
    target: "login system"
  capabilities: ["code", "debugging", "testing"]
  domain: "development"
  complexity: "medium"
  interaction_mode: "autonomous"
  confidence: 0.92
```

### Example 2: Research Question
```
Input:
  user_query: "What are the best practices for Python async programming?"

Expected Output:
  intent:
    verb: "explain"
    object: "best practices"
    target: "Python async programming"
  capabilities: ["search", "knowledge"]
  domain: "research"
  complexity: "simple"
  interaction_mode: "interactive"
  confidence: 0.95
```

### Example 3: Complex Feature
```
Input:
  user_query: "Implement user notifications with email and push support"

Expected Output:
  intent:
    verb: "implement"
    object: "user notifications"
    features: ["email", "push"]
  capabilities: ["code", "testing", "documentation"]
  domain: "development"
  complexity: "complex"
  interaction_mode: "interactive"
  confidence: 0.88
```

## Related SOPs

- [MCP Library Selector](mcp-library-selector.sop.md)
- [Coding Agent Router](coding-agent-router.sop.md)

## Version History

- v1.0.0 (2026-01-19): Initial version
