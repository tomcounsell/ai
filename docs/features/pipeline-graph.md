# Pipeline Graph

Canonical directed graph defining SDLC pipeline stage transitions with cycle support.

## Overview

The pipeline graph (`agent/pipeline_graph.py`) is the single source of truth for how the SDLC pipeline routes between stages. It replaces three previously duplicated and inconsistent pipeline definitions with one canonical graph that all routing code derives from.

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

Stage tracking is handled by `PipelineStateMachine` in `agent/pipeline_state.py`, which uses the pipeline graph for transitions. The state machine's `has_remaining_stages()` method uses `get_next_stage()` to check if a non-terminal next stage exists, correctly handling cycles and the PATCH routing-only stage.

## Runtime Wiring (PR #601)

As of PR #601, the pipeline graph is fully wired into the runtime execution path. Previously, `classify_outcome()`, `fail_stage()`, and the graph-based routing were well-tested but never called in production.

### Outcome Classification

When a Dev session completes, the worker's `_handle_dev_session_completion()` calls `classify_outcome(stage, None, result)` on the PipelineStateMachine. This uses a three-tier approach:
1. **Tier 0**: Parse `<!-- OUTCOME {...} -->` contracts from output (structured status from skills)
2. **Tier 1**: SDK stop_reason — anything other than "end_turn" is a process failure
3. **Tier 2**: Deterministic tail patterns scoped by stage (fallback when no OUTCOME contract)

Tier 0 enables the `("REVIEW", "partial")` edge: when `/do-pr-review` finds tech debt or nits, it emits `status: "partial"`, routing to PATCH instead of DOCS.

The result routes to `complete_stage()` (success/ambiguous) or `fail_stage()` (fail/partial), which triggers the appropriate graph edge.

### Stage States Initialization

SDLC sessions have `stage_states` initialized eagerly at session creation (ISSUE=ready, all others=pending).

### Dashboard Stage Routing

The dashboard (`ui/data/sdlc.py`) routes stage reads through `PipelineStateMachine(session).get_display_progress()` (PR #747, issue #735). This ensures the dashboard is a direct consumer of the canonical `DISPLAY_STAGES` stored-state path — the same path used by the merge gate. `get_display_progress()` takes no arguments and returns stored state only. Artifact inference was removed in PR #733 (issue #729).

## Integration

PM session orchestration uses the graph for pipeline progression. Individual `/do-*` skills report their results; the PM session determines what happens next.

## Per-Stage Model Selection

The PM session chooses which Claude model runs each stage at dispatch time, via the `model` parameter on `Agent(subagent_type="dev-session", ...)`. The Agent tool supports `sonnet`, `opus`, and `haiku` as per-call overrides; `agent_definitions.py` sets `model=None` on `dev-session` so inheritance is the fallback when PM omits the override.

Stages fall into two tiers based on the cognitive load of the work:

| Stage | Model | Rationale |
|-------|-------|-----------|
| ISSUE | sonnet | Template-driven issue drafting; isolated from downstream work |
| PLAN | opus | Hardest reasoning in the pipeline; downstream stages depend on plan quality |
| CRITIQUE | opus | Adversarial war-room critics; finding subtle flaws is where Opus earns its keep |
| BUILD | sonnet | Translation of a detailed plan into code; PATCH is the escape hatch. Mid-build course correction comes from PM steering (see below), not a model swap |
| TEST | sonnet | Tool-heavy verification. Independence from BUILD is the anti-cheat property; Opus is overkill |
| PATCH (easy) | sonnet | Targeted fix against a written checklist of failures or review findings |
| PATCH (hard) | sonnet, resumed from BUILD | PM resumes the original BUILD session's transcript to leverage accumulated context — see "Dev Session Resume" in [pm-dev-session-architecture.md](pm-dev-session-architecture.md) |
| REVIEW | opus | Independent adversarial read of the diff; same justification as CRITIQUE |
| DOCS | sonnet | Content transformation against code + plan; tool-heavy search and update |
| MERGE | — | Human decision |

### Rationale for the tier split

The three Opus stages (PLAN, CRITIQUE, REVIEW) are the "hard thinking moments where quality of output gates everything downstream." Every other stage either executes a written plan or performs tool-driven verification, where Sonnet's capability ceiling is sufficient.

As the Claude Code harness improves at BUILD execution and models get smarter, expect plans to become progressively more high-level, leaving more room for mid-build course correction via PM steering (PM is Opus by default). The model tier split assumes this trend and keeps BUILD on Sonnet even for non-trivial work.

### Mid-build course correction (no model swap)

When PM observes a running BUILD going off-plan, the correction path is **steering**, not a model change. PM writes an Opus-reasoned steering message and pushes it to the BUILD's Redis queue via `scripts/steer_child.py`. The BUILD session stays on Sonnet but executes Opus-authored instructions. This avoids the complexity of mid-transcript model swaps and keeps the builder's context intact.

### Hard PATCH via resume

For PATCH failures that need deep context, PM resumes the original BUILD session's Claude Code transcript rather than starting a fresh PATCH session. The builder already has every file, plan assumption, and implementation decision in its transcript — a resumed session reads that directly instead of re-deriving from the diff + plan artifacts. See "Dev Session Resume" in [pm-dev-session-architecture.md](pm-dev-session-architecture.md) for the mechanism.

## Files

| File | Role |
|------|------|
| `agent/pipeline_graph.py` | Canonical graph definition (moved from `bridge/` in Phase 3; `bridge/pipeline_graph.py` is now a shim) |
| `agent/agent_session_queue.py` | `_handle_dev_session_completion()` calls `classify_outcome()` and routes to `complete_stage()`/`fail_stage()`; initializes `stage_states` for SDLC sessions |
| `agent/pipeline_state.py` | `PipelineStateMachine` -- stage tracking, outcome classification, and transitions using the graph (moved from `bridge/` in Phase 3; `bridge/pipeline_state.py` is now a shim) |
| `tests/unit/test_pipeline_graph.py` | 27 tests covering all routing scenarios |
