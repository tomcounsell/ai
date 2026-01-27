# Plan: Structured Issue Classification Commands

## Overview

Add `/bug`, `/chore`, and `/feature` slash commands with structured plan templates for consistent issue handling.

## Source Inspiration

From `indydan/tac-6/.claude/commands/` - `bug.md`, `chore.md`, `feature.md`, and `classify_issue.md`.

## Problem Statement

Currently, Valor has `/sdlc` and `/prime` commands but lacks:
- Structured templates for different work types
- Consistent plan formats for bugs vs features vs chores
- Issue classification logic
- E2E test generation prompts

This leads to inconsistent planning and missed validation steps.

## Proposed Solution

Add three new slash commands with specialized templates:

### `/feature` Command

For new functionality with:
- User story format
- Phase-based implementation plan
- E2E test generation requirement
- Validation commands checklist

### `/bug` Command

For bug fixes with:
- Problem reproduction steps
- Root cause analysis section
- Regression test requirements
- Fix verification steps

### `/chore` Command

For maintenance tasks with:
- Scope definition
- Impact assessment
- Rollback plan
- Verification checklist

### `/classify` Command

Analyzes an issue/request and determines the appropriate type.

## New Files to Create

```
.claude/commands/
  feature.md       # New feature planning template
  bug.md           # Bug fix template
  chore.md         # Maintenance task template
  classify.md      # Issue classification logic
```

### Feature Template Structure

```markdown
# Feature: {name}

## Metadata
issue_number: {number}
workflow_id: {id}

## User Story
As a {user type}
I want to {action}
So that {benefit}

## Problem Statement
{problem description}

## Solution Statement
{proposed solution}

## Relevant Files
{files to modify}

## Implementation Plan
### Phase 1: Foundation
### Phase 2: Core Implementation
### Phase 3: Integration

## Step by Step Tasks
{ordered task list}

## Testing Strategy
### Unit Tests
### E2E Tests
### Edge Cases

## Validation Commands
{commands to verify}

## Acceptance Criteria
{measurable criteria}
```

### Bug Template Structure

```markdown
# Bug Fix: {title}

## Metadata
issue_number: {number}
workflow_id: {id}
severity: {critical|high|medium|low}

## Bug Description
{what's happening}

## Expected Behavior
{what should happen}

## Reproduction Steps
1. {step}
2. {step}

## Root Cause Analysis
{investigation findings}

## Proposed Fix
{solution approach}

## Files to Modify
{file list}

## Fix Implementation
{step by step}

## Regression Tests
{tests to add}

## Verification
{how to confirm fix}
```

### Chore Template Structure

```markdown
# Chore: {title}

## Metadata
issue_number: {number}
workflow_id: {id}
category: {refactor|docs|deps|config|cleanup}

## Scope
{what's included}

## Motivation
{why this is needed}

## Impact Assessment
{what could break}

## Implementation Steps
{ordered steps}

## Rollback Plan
{how to revert}

## Verification
{how to confirm success}
```

## Implementation Steps

1. Create `.claude/commands/classify.md` - classification logic
2. Create `.claude/commands/feature.md` - feature template
3. Create `.claude/commands/bug.md` - bug fix template
4. Create `.claude/commands/chore.md` - chore template
5. Update CLAUDE.md to document new commands
6. Add templates to `specs/` directory for generated plans

## Benefits

- Consistent planning across work types
- Built-in validation requirements
- E2E test prompts for UI features
- Better traceability from issue to implementation

## Estimated Effort

Low-Medium - Primarily documentation/templates

## Dependencies

None - uses existing slash command infrastructure

## Risks

- Templates might be too rigid for some tasks
- Need to keep templates updated as patterns evolve
- Users might skip classification step
