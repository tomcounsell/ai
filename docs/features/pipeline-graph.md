# Pipeline Graph

Canonical directed graph defining SDLC pipeline stage transitions with cycle support.

## Overview

The pipeline graph (`agent/pipeline_graph.py`) is the canonical source for **state-machine bookkeeping** — it defines which stage becomes `ready` when another stage completes. Dispatch decisions (which `/do-*` skill to invoke) are made by `agent/sdlc_router.py` via the `sdlc-tool next-skill` CLI.

## Problem Solved (Historical)

Before PR #601, pipeline routing was defined in three places that disagreed. After PR #601, the two surfaces were: (1)  for state-machine bookkeeping and (2) the SKILL.md Step 4 hand-edited dispatch table as the runtime routing surface.

As of issue #1216, the SKILL.md hand-edited dispatch table is eliminated. The canonical dispatch surface is now  ( + guards G1–G6), accessed at runtime via . The graph remains for state-machine bookkeeping only.

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

Stage tracking is handled by `PipelineStateMachine` in `agent/pipeline_state.py`, which uses the pipeline graph for transitions. The state machine's `has_remaining_stages()` method uses `get_next_stage()` to check if a non-terminal next stage exists, correctly handling cycles and the PATCH routing-only stage.

## Runtime Wiring

Stage transitions are written **in-session** by the Claude Code Skill hooks as the eng session dispatches each `/do-*` stage skill:
- `agent/hooks/pre_tool_use.py::_start_pipeline_stage` calls `start_stage(stage)` on skill invoke (stage resolved via the `_SKILL_TO_STAGE` mapping).
- `agent/hooks/post_tool_use.py::_complete_pipeline_stage` calls `complete_stage(stage)` on skill return (stage read via `current_stage()`).

`complete_stage()` consults the graph to mark the next stage `ready`. The graph's `get_next_stage()` is the canonical traversal used by `PipelineStateMachine.has_remaining_stages()` and by the SDLC router to choose the next skill.

### Outcome Classification (retained, not wired)

`PipelineStateMachine.classify_outcome(stage, stop_reason, output_tail)` still implements a three-tier classifier:
1. **Tier 0**: Parse `<!-- OUTCOME {...} -->` contracts from output (structured status from skills)
2. **Tier 1**: SDK stop_reason — anything other than "end_turn" is a process failure
3. **Tier 2**: Deterministic tail patterns scoped by stage (fallback when no OUTCOME contract)

Tier 0 supports the `("REVIEW", "partial")` edge: when `/do-pr-review` finds tech debt or nits, it can emit `status: "partial"`, which the graph routes to PATCH instead of DOCS.

However, `classify_outcome()` is **no longer wired into a completion handler**. The PM/Dev merge (PR #1691) removed `_handle_dev_session_completion()`, which previously called `classify_outcome()` and routed to `complete_stage()`/`fail_stage()`. The in-session hooks now call `complete_stage()` directly on skill return, so the method is orphaned in production and `fail_stage()` is not invoked post-turn. The classifier remains available on the state machine for callers that need to interpret a stage result.

### Stage States Initialization

SDLC sessions have `stage_states` initialized eagerly at session creation (ISSUE=ready, all others=pending).

### Dashboard Stage Routing

The dashboard (`ui/data/sdlc.py`) routes stage reads through `PipelineStateMachine(session).get_display_progress()`. This ensures the dashboard is a direct consumer of the canonical `DISPLAY_STAGES` stored-state path — the same path used by the merge gate. `get_display_progress()` takes no arguments and returns stored state only.

## Integration

The eng session uses the graph for pipeline progression: it both orchestrates and executes the pipeline, dispatching one `/do-*` stage skill at a time. The in-session hooks record each stage transition, and the eng session reads the resulting `stage_states` to determine what happens next.

## Per-Stage Model Selection

The stage→model mapping lives in the engineer persona's Stage→Model Dispatch Table (`config/personas/engineer.md`). Model selection is applied at dispatch time via the `--model` flag on `valor-session create` when the eng session fans out child eng sessions (one per issue), and is mirrored by `/do-sdlc` local supervision, which spawns each stage subagent with the matching `model:` (see [SDLC Local Supervision](sdlc-local-supervision.md)). A single eng session that runs the pipeline in-process executes every stage on its own session model.

Stages fall into two tiers based on the cognitive load of the work:

| Stage | Model | Rationale |
|-------|-------|-----------|
| ISSUE | sonnet | Template-driven issue drafting; isolated from downstream work |
| PLAN | opus | Hardest reasoning in the pipeline; downstream stages depend on plan quality |
| CRITIQUE | opus | Adversarial war-room critics; finding subtle flaws is where Opus earns its keep |
| BUILD | sonnet | Translation of a detailed plan into code; PATCH is the escape hatch. Mid-build course correction comes from parent-session steering (see below), not a model swap |
| TEST | sonnet | Tool-heavy verification. Independence from BUILD is the anti-cheat property; Opus is overkill |
| PATCH (easy) | sonnet | Targeted fix against a written checklist of failures or review findings |
| PATCH (hard) | sonnet, resumed from BUILD | Eng session resumes the original BUILD transcript to leverage accumulated context — see [Eng Session Architecture](eng-session-architecture.md) |
| REVIEW | opus | Independent adversarial read of the diff; same justification as CRITIQUE |
| DOCS | sonnet | Content transformation against code + plan; tool-heavy search and update |
| MERGE | — | Human decision |

### Rationale for the tier split

The three Opus stages (PLAN, CRITIQUE, REVIEW) are the "hard thinking moments where quality of output gates everything downstream." Every other stage either executes a written plan or performs tool-driven verification, where Sonnet's capability ceiling is sufficient.

As the Claude Code harness improves at BUILD execution and models get smarter, expect plans to become progressively more high-level, leaving more room for mid-build course correction via parent-session steering. The model tier split assumes this trend and keeps BUILD on Sonnet even for non-trivial work.

### Mid-build course correction (no model swap)

When a parent eng session observes a running child BUILD session going off-plan, the correction path is **steering**, not a model change. The parent writes a steering message and delivers it to the BUILD's turn-boundary inbox via `scripts/steer_child.py`. The BUILD session stays on its model but executes the steered instructions. This avoids the complexity of mid-transcript model swaps and keeps the builder's context intact.

### Hard PATCH via resume

For PATCH failures that need deep context, the Eng session resumes the original BUILD session's Claude Code transcript rather than starting a fresh PATCH session. The builder already has every file, plan assumption, and implementation decision in its transcript — a resumed session reads that directly instead of re-deriving from the diff + plan artifacts. See [Eng Session Architecture](eng-session-architecture.md) for the mechanism.

## Files

| File | Role |
|------|------|
| `agent/pipeline_graph.py` | Canonical graph definition. State-machine bookkeeping only — not consulted for dispatch decisions. |
| `agent/hooks/pre_tool_use.py` | `_start_pipeline_stage` calls `start_stage()` on stage-skill invoke (`_SKILL_TO_STAGE` mapping) |
| `agent/hooks/post_tool_use.py` | `_complete_pipeline_stage` calls `complete_stage()` on stage-skill return (`current_stage()`) |
| `agent/pipeline_state.py` | `PipelineStateMachine` -- stage tracking, transitions using the graph, and the retained-but-unwired `classify_outcome()`. Canonical location. |
| `tests/unit/test_pipeline_graph.py` | 27 tests covering all routing scenarios |
