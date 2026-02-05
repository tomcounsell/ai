---
status: Complete
tracking: https://github.com/tomcounsell/ai/issues/17
type: feature
---

# Plan: Structured Issue Classification

## Overview

Hybrid classification system: dynamic LLM classification during Telegram intake, with labels applied to GitHub/Notion for PM visibility. Classification remains fluid until plan approval, then locks.

**Telegram-first execution**: Plans are executed by messaging Valor in Telegram (e.g., "handle issue #17"), not by running `/build` in Claude Code.

## Problem Statement

Currently, Valor lacks:
- Automatic classification of incoming work requests
- Structured templates tailored to work type (bug vs feature vs chore)
- PM-visible labels for tracking and filtering
- Clear point where classification becomes immutable
- **Mandatory labeling enforcement** - plans can be created without any classification

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

### Phase 4: Plan Execution via /build Skill

**The agent decides when to execute based on conversation flow.**

The `/build` skill is available to the agent. When the agent determines it's time to execute a plan (user approved, user asked to start work, context indicates readiness), it invokes the skill.

Examples of natural triggers:
- User: "looks good, let's build it" → agent invokes `/build`
- User: "handle issue #17" → agent looks up plan, invokes `/build`
- User: "approved, go ahead" → agent invokes `/build` on current plan

No special intent detection needed - the agent understands context.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Telegram Message                          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Agent (Claude SDK)                      │
│                                                              │
│  Understands context, has access to skills:                  │
│  - /make-plan → create plan + tracking issue                 │
│  - /build → execute plan (spawn agents per Team Orch)        │
└─────────────────────────────────────────────────────────────┘
                              │
          Agent decides based on conversation
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
┌──────────────────────────┐    ┌──────────────────────────┐
│      /make-plan          │    │       /build             │
│                          │    │                          │
│  → Classify (bug/feat)   │    │  → Parse plan document   │
│  → Draft plan document   │    │  → Spawn team agents     │
│  → Create tracking issue │    │  → Execute tasks         │
│  → Request approval      │    │  → Report progress       │
└──────────────────────────┘    └──────────────────────────┘
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

The bridge passes messages to the agent. The agent has skills available and decides when to use them based on conversation context. No special intent detection in the bridge.

### 4b. /build Skill

The `/build` skill (already exists at `.claude/commands/build.md`) handles plan execution:

1. Accepts plan path or issue number as argument
2. If issue number → looks up plan by `tracking:` field
3. Parses Team Orchestration and Step by Step Tasks sections
4. Spawns agents per task definitions
5. Reports progress
6. Updates plan status on completion

The agent invokes this skill when conversation indicates readiness to execute.

### 5. Mandatory Label Validation

**Labels are REQUIRED for all plans.** No plan can be finalized without a classification.

Update `.claude/skills/make-plan/SKILL.md` hooks to enforce:

```yaml
hooks:
  Stop:
    - hooks:
        # Existing validations...
        - type: command
          command: >-
            uv run $CLAUDE_PROJECT_DIR/.claude/hooks/validators/validate_file_contains.py
            --directory docs/plans
            --extension .md
            --contains 'type: bug'
            --contains 'type: feature'
            --contains 'type: chore'
            --match-any
```

Create validation script `.claude/hooks/validators/validate_plan_label.py`:

```python
"""Validate that plan has a classification label in frontmatter."""
import sys
import re
from pathlib import Path

def validate_plan_label(plan_path: str) -> bool:
    content = Path(plan_path).read_text()
    # Check frontmatter for type field
    frontmatter_match = re.search(r'^---\n(.*?)\n---', content, re.DOTALL)
    if not frontmatter_match:
        return False
    frontmatter = frontmatter_match.group(1)
    # Must have type: bug|feature|chore
    return bool(re.search(r'^type:\s*(bug|feature|chore)\s*$', frontmatter, re.MULTILINE))

if __name__ == "__main__":
    # Validate all .md files in docs/plans/
    plans_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/plans")
    for plan in plans_dir.glob("*.md"):
        if not validate_plan_label(plan):
            print(f"ERROR: {plan} missing required 'type: bug|feature|chore' in frontmatter")
            sys.exit(1)
    sys.exit(0)
```

### 6. GitHub/Notion Label Application

When creating tracking issues:

**GitHub Projects:**
```bash
gh issue create \
  --repo {org}/{repo} \
  --title "[Plan] {Feature Name}" \
  --label "plan" \
  --label "{type}"  # bug, feature, or chore - REQUIRED
  --body "..."
```

**Notion Projects:**
- Set "Type" property to the classification value
- This property must exist in the Notion database schema

## Files to Create/Modify

```
tools/
  classifier.py                              # NEW: Haiku/Ollama classifier (bug/feature/chore)

.claude/skills/make-plan/
  SKILL.md                                   # MODIFY: Add label requirement + validation hooks

.claude/commands/build.md
  SKILL.md                                   # MODIFY: Add issue number → plan lookup

.claude/hooks/validators/
  validate_plan_label.py                     # NEW: Enforce label in frontmatter
```

## Plan Template

One base template for all work types. Classification adds minimal targeted sections only where it matters.

### Base Template (all types use this)

**IMPORTANT**: The `type` field in frontmatter is MANDATORY. Plans cannot be finalized without it.

```markdown
---
status: Planning
type: {bug|feature|chore}    # REQUIRED - validation will fail without this
appetite: {Small|Medium|Large}
tracking: {github_issue_url or notion_page_url}
---

# {title}

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

- [ ] Incoming Telegram messages are auto-classified (bug/feature/chore)
- [ ] Classification informs plan template selection
- [ ] PM can see classification label on GitHub/Notion
- [ ] Classification locks on plan approval
- [ ] Reclassification during drafting works smoothly
- [ ] **Plans without labels fail validation** - `make-plan` skill blocks completion without `type:` in frontmatter
- [ ] **GitHub issues automatically get type label** - `--label "{type}"` applied on issue creation
- [ ] **Notion tasks automatically get Type property** - set during task creation
- [ ] **Agent can invoke /build** - skill available via SDK
- [ ] **/build accepts issue number** - looks up plan by `tracking:` field
- [ ] **Progress reported to Telegram** - updates sent as agents complete tasks
- [ ] **Plan status updated on completion** - status: Complete, issue closed
