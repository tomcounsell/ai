# Error Investigation SOP

**Version**: 1.0.0
**Last Updated**: 2026-01-20
**Owner**: Valor AI System
**Status**: Active

## Overview

This SOP defines the standard procedure for investigating errors reported in Sentry. It covers issue analysis, root cause identification, and fix recommendations.

## Prerequisites

- SENTRY_API_KEY environment variable configured
- SENTRY_ORG_SLUG environment variable configured
- Access to relevant Sentry project

## Parameters

### Required
- **issue_id** (string): Sentry issue identifier
  - Format: Numeric or alphanumeric ID
  - Description: The issue to investigate

### Optional
- **severity** (string): Minimum severity to investigate
  - Values: `fatal` | `error` | `warning` | `info`
  - Default: `error`

- **time_range** (string): Time window for event analysis
  - Format: Duration string (e.g., `24h`, `7d`)
  - Default: `24h`

- **include_events** (boolean): Include detailed event data
  - Default: `true`

- **max_events** (integer): Maximum events to analyze
  - Default: `10`
  - Max: `100`

## Steps

### 1. Fetch Issue Details

**Purpose**: Retrieve comprehensive issue information from Sentry.

**Actions**:
- MUST fetch issue metadata (title, culprit, first/last seen)
- MUST retrieve issue status and assignment
- SHOULD fetch issue tags and frequency data
- MAY fetch related issues if available

**API Call**:
```
GET /api/0/issues/{issue_id}/
```

**Validation**:
- Issue exists and is accessible
- Response contains required fields

**Error Handling**:
- If issue not found: Return error with issue ID
- If unauthorized: Check API key permissions

### 2. Analyze Stack Trace

**Purpose**: Examine the error's stack trace to identify the failure point.

**Actions**:
- MUST extract exception type and message
- MUST identify the failing function and line
- SHOULD trace through the call stack
- SHOULD identify affected code files
- MAY correlate with recent code changes

**Analysis Points**:
- Exception type (e.g., TypeError, ValueError, NullPointerException)
- Error message content
- File and line number of failure
- Call stack depth and path
- Local variables at failure point (if available)

**Validation**:
- Stack trace is parseable
- Source code locations are identifiable

### 3. Gather Context

**Purpose**: Collect additional context to understand the error conditions.

**Actions**:
- MUST review request/response data (if applicable)
- MUST check user and environment tags
- SHOULD review breadcrumbs leading to error
- SHOULD check related events for patterns
- MAY review system metrics at time of error

**Context Points**:
- User information (if available)
- Browser/device information
- Request URL and method
- Request parameters (sanitized)
- Environment (production, staging, etc.)

### 4. Identify Root Cause

**Purpose**: Determine the underlying cause of the error.

**Actions**:
- MUST analyze error pattern across multiple events
- MUST consider timing and frequency patterns
- SHOULD check for recent deployments or changes
- SHOULD identify common factors across occurrences
- MAY consult historical data for similar issues

**Root Cause Categories**:
- Code bug (logic error, null reference, type mismatch)
- Data issue (invalid input, missing data, corruption)
- Infrastructure (timeout, resource exhaustion, network)
- Third-party (external service failure, API change)
- Configuration (missing config, wrong environment)

**Validation**:
- Root cause hypothesis is testable
- Evidence supports the conclusion

### 5. Generate Recommendations

**Purpose**: Provide actionable fix recommendations.

**Actions**:
- MUST suggest specific code changes if applicable
- MUST estimate severity and impact
- SHOULD provide testing recommendations
- SHOULD suggest preventive measures
- MAY recommend monitoring additions

**Recommendation Format**:
```
1. Immediate Fix: [specific code change or action]
2. Root Cause Address: [underlying issue resolution]
3. Prevention: [how to prevent recurrence]
4. Testing: [how to verify the fix]
```

## Success Criteria

- Root cause identified with supporting evidence
- Clear, actionable recommendations provided
- Severity and impact assessed
- Related issues identified (if any)

## Error Recovery

| Error Type | Recovery Procedure |
|------------|-------------------|
| Issue not found | Verify issue ID, check project access |
| Rate limited | Wait 60 seconds, retry with backoff |
| Incomplete data | Proceed with available data, note limitations |
| Parse error | Log raw data, attempt alternative parsing |

## Output Format

```json
{
  "issue_id": "string",
  "title": "string",
  "severity": "fatal|error|warning|info",
  "first_seen": "ISO timestamp",
  "last_seen": "ISO timestamp",
  "event_count": "number",
  "affected_users": "number",
  "root_cause": {
    "category": "string",
    "description": "string",
    "evidence": ["string"],
    "confidence": "high|medium|low"
  },
  "stack_trace_summary": {
    "exception_type": "string",
    "message": "string",
    "file": "string",
    "line": "number",
    "function": "string"
  },
  "recommendations": [
    {
      "type": "immediate|preventive|monitoring",
      "action": "string",
      "priority": "high|medium|low"
    }
  ],
  "related_issues": ["issue_ids"]
}
```

## Examples

### Example 1: NullPointerException Investigation

```
Input:
  issue_id: 12345
  time_range: 24h
  include_events: true

Expected Output:
  issue_id: 12345
  title: "NullPointerException in UserService.getProfile"
  severity: error
  event_count: 47
  root_cause:
    category: code_bug
    description: "User profile accessed before null check"
    evidence:
      - "All events have user_id parameter"
      - "Profile is null for deleted users"
    confidence: high
  recommendations:
    - type: immediate
      action: "Add null check before accessing profile"
      priority: high
    - type: preventive
      action: "Add validation in UserService constructor"
      priority: medium
```

## Related SOPs

- [Performance Analysis](performance-analysis.sop.md)
- [Alert Triage](alert-triage.sop.md)

## Version History

- v1.0.0 (2026-01-20): Initial version
