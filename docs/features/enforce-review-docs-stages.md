# Mandatory REVIEW and DOCS Stage Enforcement

Ensures that every SDLC session completing BUILD must also complete REVIEW and DOCS before the Observer delivers output to Telegram.

## Problem

The pipeline graph defined correct edges (TEST -> REVIEW -> DOCS -> MERGE), but the Observer had multiple paths that allowed REVIEW and DOCS to be bypassed — an audit found fewer than 10% of merged PRs reached these stages.

## Solution

### Hard Delivery Gates

> **Note**: The Observer Agent (`bridge/observer.py`) was removed as part of the ChatSession/DevSession architecture redesign. Mandatory gate enforcement is now handled by ChatSession orchestration and the nudge loop in `agent/agent_session_queue.py`. The gate check functions in `agent/goal_gates.py` remain the source of truth for deterministic stage validation.

### State Machine Stage Transitions (`bridge/pipeline_state.py`)

Stage completion is now managed by the `PipelineStateMachine`. Stages can only complete via explicit `complete_stage()` calls at session completion time — no transcript parsing or pattern matching. This eliminates false completions entirely.

### `has_remaining_stages()` (`bridge/pipeline_state.py`)

The `PipelineStateMachine.has_remaining_stages()` method walks the pipeline graph from the current stage forward, checking if any reachable stage is not yet completed.

### Plan Status Update in `/do-docs`

The `/do-docs` skill now updates the plan document's `status:` frontmatter to "Complete" after documentation is created/updated.

## Related

- [Pipeline Graph](pipeline-graph.md) — defines the stage transition edges
- [Goal Gates](goal-gates.md) — deterministic gate check functions
- [Chat Dev Session Architecture](chat-dev-session-architecture.md) — ChatSession/DevSession routing model that replaced the Observer

## Tracking

- Issue: [#418](https://github.com/tomcounsell/ai/issues/418)
- PR: [#421](https://github.com/tomcounsell/ai/pull/421)
