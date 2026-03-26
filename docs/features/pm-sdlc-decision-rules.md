# PM SDLC Decision Rules

## Overview

The ChatSession PM persona orchestrates SDLC work by spawning DevSessions for each pipeline stage. DevSessions emit structured `<!-- OUTCOME -->` JSON blocks that encode the stage result. The PM decision rules tell the PM how to interpret these outcomes and route to the next stage automatically.

## Problem Solved

Without explicit decision rules, the PM had no instructions for:
- Parsing OUTCOME blocks from DevSession output
- Mapping status values (success/partial/fail) to next actions
- Deciding when to auto-merge vs. escalate to human
- Handling tech debt and nit findings (patch vs. skip)

This led to the PM either asking permission for obvious merges or silently skipping review findings.

## Decision Table

| OUTCOME Status | PM Action |
|----------------|-----------|
| `success` | Proceed to next stage. If TEST + REVIEW + DOCS all passed clean, auto-merge without asking. |
| `partial` | Dispatch PATCH for tech_debt/nits, then re-REVIEW. |
| `fail` | Dispatch PATCH for blockers, then re-REVIEW. |
| No OUTCOME block | PM uses judgment from the output text. |

## Key Principles

1. **Findings are never silently ignored.** They are either fixed or annotated with inline code comments explaining why they were left as-is.
2. **Auto-merge on clean gates.** When all pipeline stages pass with zero findings, the PM merges without asking human permission.
3. **Escalate only when genuinely blocked.** The PM only asks the human when a decision requires business judgment or the pipeline is stuck.

## Annotate Rather Than Skip

When a review finding is genuinely not worth fixing (e.g., a style nit in older code, a suggestion that contradicts the plan), the do-patch builder adds an inline code comment instead of silently skipping:

```python
# NOTE: [finding summary] -- left as-is because [rationale]
```

This creates a paper trail so the next reviewer does not re-flag the same issue. The finding is "addressed" (annotated), not "skipped."

## Implementation

- **PM dispatch rules**: `agent/sdk_client.py` -- appended to the PM dispatch instructions block (lines 1583-1596)
- **Annotate pattern**: `.claude/skills/do-patch/SKILL.md` -- added after the builder agent prompt template

## Related

- [Chat/Dev Session Architecture](chat-dev-session-architecture.md) -- how ChatSession and DevSession interact
- Issue [#544](https://github.com/tomcounsell/ai/issues/544) -- tracking issue
