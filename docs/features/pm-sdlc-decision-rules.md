# PM SDLC Decision Rules

## Overview

The PM session PM persona orchestrates SDLC work by spawning Dev sessions for each pipeline stage. Dev sessions emit structured `<!-- OUTCOME -->` JSON blocks that encode the stage result. The PM decision rules tell the PM how to interpret these outcomes and route to the next stage automatically.

## Problem Solved

Without explicit decision rules, the PM had no instructions for:
- Parsing OUTCOME blocks from Dev session output
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

## Model Selection per Stage

PM picks the model for each Dev session dispatch via the Agent tool's `model` parameter. The full mapping lives in [pipeline-graph.md](pipeline-graph.md) under "Per-Stage Model Selection." The short rule:

- **Opus stages**: PLAN, CRITIQUE, REVIEW — hard reasoning or adversarial independence
- **Sonnet stages**: ISSUE, BUILD, TEST, PATCH, DOCS — plan execution or tool-heavy work
- **PM's own model**: opus (PM is the orchestrator; it reasons about stage outcomes and routes)

When omitted, the `dev-session` agent definition's `model=None` inherits from the parent session (PM, which is Opus). PM **must** explicitly name the model on every dispatch to avoid silently inheriting Opus for Sonnet stages.

## Hard PATCH: Resume vs Fresh

Most PATCH work is a targeted fix against a written checklist of findings — a fresh Sonnet session reads the review comments or test failures and applies the fix cleanly. PM dispatches this as a normal `/do-patch` stage with `model=sonnet`.

Some PATCH work needs the original builder's context: accumulated reasoning, edge cases considered-and-dismissed, implementation rationale that lives only in the builder's transcript. For these, PM resumes the BUILD session instead of dispatching fresh. See [pm-dev-session-architecture.md](pm-dev-session-architecture.md) under "Dev Session Resume" for the mechanism.

### Resume difficulty signals

PM uses these signals to decide "resume" vs "fresh":

| Signal | Weight toward resume |
|--------|---------------------|
| Prior PATCH on the same blocker already failed | Strong |
| Multiple blockers span interacting code areas | Strong |
| Failure involves race conditions, state, or architecture | Strong |
| Review finding explicitly references implementation rationale | Moderate |
| Single isolated blocker with a clear reproduction | Weak (prefer fresh) |
| All findings are style nits or doc fixes | None (always fresh) |

Fresh is the default. PM escalates to resume only when the signal is clearly present — resume is more expensive (serializes the BUILD session back through the worker queue) and has a smaller blast radius if something goes wrong with a fresh session.

### Mid-build course correction (not PATCH)

For course corrections during a *running* BUILD — before the session has completed and before any test failures or review findings exist — PM does not dispatch PATCH at all. PM pushes an Opus-reasoned steering message into the BUILD's Redis queue via `scripts/steer_child.py`. The BUILD session executes the correction inline without returning to PM. This is faster and cheaper than a resume-patch cycle for corrections caught early.

The decision is:

- **BUILD running, going off-plan** → steer (sync, inline)
- **BUILD completed, tests failed or review blocked, fix is isolated** → fresh PATCH (Sonnet)
- **BUILD completed, tests failed or review blocked, fix needs builder context** → resume BUILD (Sonnet, original transcript)

## Key Principles

1. **Findings are never silently ignored.** They are either fixed or annotated with inline code comments explaining why they were left as-is.
2. **Auto-merge on clean gates.** When all pipeline stages pass with zero findings, the PM merges without asking human permission.
3. **Escalate only when genuinely blocked.** The PM only asks the human when a decision requires business judgment or the pipeline is stuck.
4. **Pick the model explicitly.** Every Dev session dispatch names its model. Never inherit silently.
5. **Fresh by default, resume when the signal is clear.** Hard PATCH escalation is not the default; it is reserved for the cases where the builder's accumulated context is the valuable thing.

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

- [Chat/Dev Session Architecture](pm-dev-session-architecture.md) -- how PM session and Dev session interact
- Issue [#544](https://github.com/tomcounsell/ai/issues/544) -- tracking issue
