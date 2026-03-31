# Pipeline Graph

Canonical directed graph defining SDLC pipeline stage transitions with cycle support.

## Overview

The pipeline graph (`bridge/pipeline_graph.py`) is the single source of truth for how the SDLC pipeline routes between stages. It replaces three previously duplicated and inconsistent pipeline definitions with one canonical graph that all routing code derives from.

## Problem Solved

Before this feature, pipeline routing was defined in three places that disagreed:

| Location | Definition |
|----------|-----------|
| SDLC `SKILL.md` dispatch table | 10 numbered rows covering all stages including PATCH |
| Observer `_STAGE_TO_SKILL` | 6 stages without PATCH |
| `build_pipeline.py` stage tracking | 6 stages without PATCH |

The Observer's `_next_sdlc_skill()` walked stages linearly and could not model cycles (TEST fail -> PATCH -> TEST). Cycles happened via ad-hoc LLM decisions rather than being encoded in the routing logic.

## Architecture

The graph is a simple Python dict mapping `(stage, outcome)` tuples to next stages:

```python
PIPELINE_EDGES: dict[tuple[str, str], str] = {
    ("ISSUE", "success"): "PLAN",
    ("PLAN", "success"): "CRITIQUE",
    ("CRITIQUE", "success"): "BUILD",
    ("CRITIQUE", "fail"): "PLAN",
    ("BUILD", "success"): "TEST",
    ("TEST", "success"): "REVIEW",
    ("TEST", "fail"): "PATCH",
    ("REVIEW", "success"): "DOCS",
    ("REVIEW", "fail"): "PATCH",
    ("REVIEW", "partial"): "PATCH",
    ("PATCH", "success"): "TEST",
    ("PATCH", "fail"): "TEST",
    ("DOCS", "success"): "MERGE",
}
```

### Happy Path

```
ISSUE -> PLAN -> CRITIQUE -> BUILD -> TEST -> REVIEW -> DOCS -> MERGE
```

### Failure Cycles

```
CRITIQUE(fail) -> PLAN -> CRITIQUE  (plan revision loop)
TEST(fail) -> PATCH -> TEST         (test failure fix loop)
REVIEW(fail) -> PATCH -> TEST -> REVIEW  (review feedback loop)
```

### Max Cycle Limits

A `MAX_PATCH_CYCLES` counter (default: 3) prevents infinite PATCH -> TEST loops. A `MAX_CRITIQUE_CYCLES` counter (default: 2) prevents infinite CRITIQUE -> PLAN loops. When either limit is reached, `get_next_stage()` returns `None`, escalating to human review.

## Key Exports

| Export | Type | Description |
|--------|------|-------------|
| `PIPELINE_EDGES` | `dict[tuple[str, str], str]` | Canonical graph edges |
| `STAGE_TO_SKILL` | `dict[str, str]` | Stage to `/do-*` command mapping |
| `DISPLAY_STAGES` | `list[str]` | PM-facing linear stage list (excludes PATCH) |
| `get_next_stage()` | function | Graph traversal with cycle counter |
| `MAX_PATCH_CYCLES` | `int` | Max PATCH cycles before escalation (default 3) |
| `MAX_CRITIQUE_CYCLES` | `int` | Max CRITIQUE cycles before escalation (default 2) |

## Design Decisions

- **PATCH is routing-only**: It does not appear in `DISPLAY_STAGES` or progress templates. It is a routing concept for the Observer, not a stage the PM sees.
- **No external dependencies**: Pure Python data structures, no state machine library needed.
- **Fallback behavior**: Unknown outcomes fall back to the "success" transition. Unknown stages return `None`.
- **MERGE is terminal**: MERGE requires human authorization via the `/do-merge` skill. There is no edge beyond MERGE.

## Mandatory Gate Enforcement

The pipeline graph is the backbone of stage routing. Mandatory gate enforcement for REVIEW and DOCS stages is handled by the goal gate functions in `agent/goal_gates.py`, which are checked before delivery decisions.

### State Machine Integration

Stage tracking is handled by `PipelineStateMachine` in `bridge/pipeline_state.py`, which uses the pipeline graph for transitions. The state machine's `has_remaining_stages()` method uses `get_next_stage()` to check if a non-terminal next stage exists, correctly handling cycles and the PATCH routing-only stage.

## Runtime Wiring (PR #601)

As of PR #601, the pipeline graph is fully wired into the runtime execution path. Previously, `classify_outcome()`, `fail_stage()`, and the graph-based routing were well-tested but never called in production.

### Outcome Classification

When a DevSession completes, the `subagent_stop_hook` calls `classify_outcome(stage, stop_reason, output_tail)` on the PipelineStateMachine. This uses a two-tier approach:
1. SDK stop_reason: anything other than "end_turn" is a process failure
2. Deterministic tail patterns scoped by stage (e.g., "changes requested" in REVIEW output -> fail)

The result routes to `complete_stage()` (success/ambiguous) or `fail_stage()` (fail/partial), which triggers the appropriate graph edge.

### Coach Graph-Based Routing

The coach (`bridge/coach.py`) uses `PipelineStateMachine.next_stage(outcome)` to determine the next stage from the graph, replacing the previous linear scan of `DISPLAY_STAGES`. It infers the outcome from stage statuses written by the subagent_stop hook.

### Stage States Initialization

SDLC sessions have `stage_states` initialized eagerly at session creation (ISSUE=ready, all others=pending), eliminating the need for the dashboard inference fallback (`_infer_stages_from_history()`), which is now deprecated.

## Integration

The coach (`bridge/coach.py`) imports `DISPLAY_STAGES` and `STAGE_TO_SKILL` from the graph module instead of maintaining its own hardcoded copies.

ChatSession orchestration uses the graph for pipeline progression. Individual `/do-*` skills report their results; the ChatSession determines what happens next.

## Files

| File | Role |
|------|------|
| `bridge/pipeline_graph.py` | Canonical graph definition |
| `agent/hooks/subagent_stop.py` | Calls `classify_outcome()` and routes to `complete_stage()`/`fail_stage()` |
| `agent/job_queue.py` | Nudge loop uses graph for routing decisions; initializes `stage_states` for SDLC sessions |
| `bridge/coach.py` | Uses `PipelineStateMachine.next_stage(outcome)` for graph-based routing |
| `bridge/pipeline_state.py` | `PipelineStateMachine` -- stage tracking, outcome classification, and transitions using the graph |
| `tests/unit/test_pipeline_graph.py` | 27 tests covering all routing scenarios |
