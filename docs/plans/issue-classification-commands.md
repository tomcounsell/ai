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

All classifications use the same base plan template. Classification adds one small targeted section:

| Classification | Added Section |
|---------------|---------------|
| bug | Reproduction steps |
| feature | User story |
| chore | Rollback plan |

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

### 2. Plan Template (`.claude/commands/plan.md`)

Single base template with optional type-specific sections injected based on classification.

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
  plan.md                 # MODIFY: Add classification + type-specific hints

bridge/
  telegram_bridge.py      # MODIFY: Add classification on intake
```

## Plan Template

One base template for all work types. Classification adds minimal targeted sections only where it matters.

### Base Template (all types use this)

```markdown
# {title}

## Classification
Type: {bug|feature|chore}
Locked: {yes/no}
Issue: {github_url}

## Problem
{what needs to change and why}

## Solution
{proposed approach}

## Implementation
{steps to complete the work}

## Verification
{how to confirm it's done correctly}
```

### Type-Specific Additions

Small sections added based on classification:

| Type | Additional Section | Why |
|------|-------------------|-----|
| bug | **Reproduction**: steps to trigger the issue | Need to verify fix works |
| feature | **User Story**: As a... I want... So that... | Keeps focus on user value |
| chore | **Rollback**: how to revert if needed | Maintenance can break things |

These additions are optional guidance, not rigid requirements. A bug that's obvious doesn't need reproduction steps. A tiny feature doesn't need a formal user story.

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

## Success Criteria

- [ ] Incoming Telegram messages are auto-classified
- [ ] Classification informs plan template selection
- [ ] PM can see classification label on GitHub/Notion
- [ ] Classification locks on plan approval
- [ ] Reclassification during drafting works smoothly
