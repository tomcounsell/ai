# Mandatory REVIEW and DOCS Stage Enforcement

Ensures that every SDLC job completing BUILD must also complete REVIEW and DOCS before the Observer delivers output to Telegram.

## Problem

The pipeline graph defined correct edges (TEST -> REVIEW -> DOCS -> MERGE), but the Observer had multiple paths that allowed REVIEW and DOCS to be bypassed — an audit found fewer than 10% of merged PRs reached these stages.

## Solution

### Hard Delivery Gates in Observer (`bridge/observer.py`)

The `_check_mandatory_gates()` method runs REVIEW and DOCS goal gate checks before any deliver-to-Telegram decision. Gate results are cached per `Observer.run()` invocation to avoid redundant API calls.

Three enforcement points:

1. **Typed outcome success path** (line ~580): When a typed outcome reports success and `has_remaining_stages()` returns False, gates are checked before delivering.
2. **Deterministic SDLC guard bypass** (line ~722): When the guard is bypassed (e.g., `needs_human=True`), gates are checked before falling through to the LLM Observer.
3. **LLM Observer deliver decision** (line ~836): After the LLM decides to deliver, gates override to steer if unsatisfied.

### Cycle Safety

If the same gate-forced steering happens 3+ times (tracked via `_gate_steering_count` in session history), the Observer delivers with a warning instead of looping indefinitely.

### State Machine Stage Transitions (`bridge/pipeline_state.py`)

Stage completion is now managed by the `PipelineStateMachine`. Stages can only complete via explicit `complete_stage()` calls at job completion time — no transcript parsing or pattern matching. This eliminates false completions entirely.

### `has_remaining_stages()` (`bridge/pipeline_state.py`)

The `PipelineStateMachine.has_remaining_stages()` method walks the pipeline graph from the current stage forward, checking if any reachable stage is not yet completed.

### Plan Status Update in `/do-docs`

The `/do-docs` skill now updates the plan document's `status:` frontmatter to "Complete" after documentation is created/updated.

## Related

- [Pipeline Graph](pipeline-graph.md) — defines the stage transition edges
- [Observer Agent](observer-agent.md) — routing decisions and deterministic guards
- [Goal Gates](goal-gates.md) — deterministic gate check functions
- [Typed Skill Outcomes](typed-skill-outcomes.md) — how skills report completion

## Tracking

- Issue: [#418](https://github.com/tomcounsell/ai/issues/418)
- PR: [#421](https://github.com/tomcounsell/ai/pull/421)
