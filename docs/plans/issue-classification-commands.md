---
tracking: https://github.com/tomcounsell/ai/issues/17
---

# Plan: Structured Issue Classification

## Overview

Hybrid classification system: dynamic LLM classification during Telegram intake, with labels applied to GitHub/Notion for PM visibility. Classification remains fluid until plan approval, then locks.

## Problem Statement

Currently, Valor lacks:
- Automatic classification of incoming work requests
- Structured templates tailored to work type (bug vs feature vs chore)
- PM-visible labels for tracking and filtering
- Clear point where classification becomes immutable

This leads to inconsistent planning and poor visibility for project management.

## Proposed Solution: Hybrid Classification

### Phase 1: Dynamic Classification (Telegram Intake)

When a message arrives via Telegram, use a lightweight LLM (Haiku or local Ollama) to classify:

```
bug     → Something broken that worked before
feature → New functionality or capability
chore   → Maintenance, refactoring, docs, deps
```

This classification:
- Happens instantly on message intake
- Informs which plan template to use
- Can change as conversation develops
- Costs ~$0.0001 per classification (Haiku) or free (Ollama)

### Phase 2: Template Selection

Based on classification, use the appropriate plan template:

| Classification | Template | Key Sections |
|---------------|----------|--------------|
| bug | Bug Fix | Reproduction, Root Cause, Regression Tests |
| feature | Feature | User Story, Phases, E2E Tests |
| chore | Chore | Scope, Impact, Rollback Plan |

### Phase 3: Label Lock on Approval

**Critical Rule**: Classification is fluid until plan approval, then immutable.

```
Telegram: "the login is broken"
  → Classified: bug
  → Plan drafted using bug template

During planning: "actually let's add 2FA while we're in there"
  → Reclassified: feature
  → Plan updated using feature template

PM approves plan
  → Label LOCKED as "feature"
  → GitHub issue labeled, cannot change
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Telegram Message                          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Lightweight Classifier (Haiku/Ollama)           │
│                                                              │
│  Input: message text + recent context                        │
│  Output: { type: "bug"|"feature"|"chore", confidence: 0.9 }  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Plan Template Selection                    │
│                                                              │
│  bug.md → feature.md → chore.md                              │
│  (classification can change during drafting)                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Plan Approval                           │
│                                                              │
│  PM reviews plan → Approves                                  │
│  Classification LOCKS → Label applied to GitHub/Notion       │
└─────────────────────────────────────────────────────────────┘
```

## Implementation

### 1. Classifier Module (`tools/classifier.py`)

```python
from anthropic import Anthropic

CLASSIFICATION_PROMPT = """Classify this work request into exactly one category:
- bug: Something broken that previously worked
- feature: New functionality or capability
- chore: Maintenance, refactoring, documentation, dependencies

Request: {message}

Respond with JSON: {"type": "bug"|"feature"|"chore", "confidence": 0.0-1.0, "reason": "brief explanation"}"""

def classify_request(message: str, context: str = "") -> dict:
    """Classify a work request using Haiku."""
    # Use Haiku for speed and cost
    # Falls back to local Ollama if API unavailable
    ...
```

### 2. Plan Templates (`.claude/commands/`)

Create three template files:
- `bug.md` - Bug fix template with reproduction steps, root cause analysis
- `feature.md` - Feature template with user story, phases, E2E tests
- `chore.md` - Chore template with scope, impact assessment, rollback

### 3. Label Lock Integration

In the plan approval workflow:
```python
def approve_plan(plan_path: str, classification: str):
    """Lock classification and apply label."""
    # 1. Mark plan as approved
    # 2. Apply label to GitHub issue (if exists)
    # 3. Apply property to Notion task (if exists)
    # 4. Record lock timestamp in plan metadata
```

### 4. Bridge Integration

Update `telegram_bridge.py` to:
1. Classify incoming messages
2. Pass classification to SDK agent
3. Track classification changes during conversation
4. Enforce lock on plan approval

## Files to Create/Modify

```
tools/
  classifier.py          # NEW: Haiku/Ollama classifier

.claude/commands/
  bug.md                  # NEW: Bug fix template
  feature.md              # NEW: Feature template
  chore.md                # NEW: Chore template

bridge/
  telegram_bridge.py      # MODIFY: Add classification on intake
```

## Plan Templates

### Bug Template (`bug.md`)

```markdown
# Bug Fix: {title}

## Classification
Type: bug
Locked: {yes/no}
Issue: {github_url}

## Bug Description
{what's broken}

## Expected Behavior
{what should happen}

## Reproduction Steps
1. {step}

## Root Cause Analysis
{findings after investigation}

## Proposed Fix
{solution approach}

## Files to Modify
{file list}

## Regression Tests
{tests to prevent recurrence}

## Verification
{how to confirm fix works}
```

### Feature Template (`feature.md`)

```markdown
# Feature: {title}

## Classification
Type: feature
Locked: {yes/no}
Issue: {github_url}

## User Story
As a {user type}
I want to {action}
So that {benefit}

## Problem Statement
{why this is needed}

## Solution
{proposed approach}

## Implementation Phases
### Phase 1: Foundation
### Phase 2: Core Implementation
### Phase 3: Integration

## Testing Strategy
- Unit tests: {coverage}
- E2E tests: {scenarios}
- Edge cases: {considerations}

## Acceptance Criteria
{measurable criteria for done}
```

### Chore Template (`chore.md`)

```markdown
# Chore: {title}

## Classification
Type: chore
Locked: {yes/no}
Issue: {github_url}
Category: {refactor|docs|deps|config|cleanup}

## Scope
{what's included and excluded}

## Motivation
{why this maintenance is needed}

## Impact Assessment
{what could break, dependencies affected}

## Implementation Steps
1. {step}

## Rollback Plan
{how to revert if needed}

## Verification
{how to confirm success}
```

## Benefits

- **PM Visibility**: Labels on GitHub/Notion for filtering and reporting
- **Flexibility**: Classification can evolve during planning phase
- **Consistency**: Same work type always uses same template structure
- **Immutability**: Once approved, classification is locked for audit trail
- **Low Cost**: Haiku classification is ~$0.0001/request, Ollama is free

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Misclassification by small model | Human can override during planning phase |
| PM changes mind after approval | Requires explicit unlock (with reason logged) |
| Templates too rigid | Templates are guidelines, can be adapted |

## Success Criteria

- [ ] Incoming Telegram messages are auto-classified
- [ ] Classification informs plan template selection
- [ ] PM can see classification label on GitHub/Notion
- [ ] Classification locks on plan approval
- [ ] Reclassification during drafting works smoothly
