# Pipeline Graph

Canonical directed graph defining SDLC pipeline stage transitions with cycle support.

## Overview

The pipeline graph (`bridge/pipeline_graph.py`) is the single source of truth for how the SDLC pipeline routes between stages. It replaces three previously duplicated and inconsistent pipeline definitions with one canonical graph that all routing code derives from.

## Problem Solved

Before this feature, pipeline routing was defined in three places that disagreed:

| Location | Definition |
|----------|-----------|
| SDLC `SKILL.md` dispatch table | 9 stages including PATCH |
| Observer `_STAGE_TO_SKILL` | 6 stages without PATCH |
| `stage_detector.py` `STAGE_ORDER` | 6 stages without PATCH |

The Observer's `_next_sdlc_skill()` walked stages linearly and could not model cycles (TEST fail -> PATCH -> TEST). Cycles happened via ad-hoc LLM decisions rather than being encoded in the routing logic.

## Architecture

The graph is a simple Python dict mapping `(stage, outcome)` tuples to next stages:

```python
PIPELINE_EDGES: dict[tuple[str, str], str] = {
    ("ISSUE", "success"): "PLAN",
    ("PLAN", "success"): "BUILD",
    ("BUILD", "success"): "TEST",
    ("TEST", "success"): "REVIEW",
    ("TEST", "fail"): "PATCH",
    ("REVIEW", "success"): "DOCS",
    ("REVIEW", "fail"): "PATCH",
    ("PATCH", "success"): "TEST",
    ("PATCH", "fail"): "TEST",
    ("DOCS", "success"): "MERGE",
}
```

### Happy Path

```
ISSUE -> PLAN -> BUILD -> TEST -> REVIEW -> DOCS -> MERGE
```

### Failure Cycles

```
TEST(fail) -> PATCH -> TEST        (test failure fix loop)
REVIEW(fail) -> PATCH -> TEST -> REVIEW  (review feedback loop)
```

### Max Cycle Limit

A `MAX_PATCH_CYCLES` counter (default: 3) prevents infinite PATCH -> TEST loops. When the limit is reached, `get_next_stage()` returns `None`, escalating to human review.

## Key Exports

| Export | Type | Description |
|--------|------|-------------|
| `PIPELINE_EDGES` | `dict[tuple[str, str], str]` | Canonical graph edges |
| `STAGE_TO_SKILL` | `dict[str, str]` | Stage to `/do-*` command mapping |
| `DISPLAY_STAGES` | `list[str]` | PM-facing linear stage list (excludes PATCH) |
| `get_next_stage()` | function | Graph traversal with cycle counter |
| `MAX_PATCH_CYCLES` | `int` | Max cycles before escalation (default 3) |

## Design Decisions

- **PATCH is routing-only**: It does not appear in `DISPLAY_STAGES` or progress templates. It is a routing concept for the Observer, not a stage the PM sees.
- **No external dependencies**: Pure Python data structures, no state machine library needed.
- **Fallback behavior**: Unknown outcomes fall back to the "success" transition. Unknown stages return `None`.
- **MERGE is terminal**: `get_next_stage("DOCS", "success")` returns `None` because MERGE has no corresponding skill -- it requires human decision.

## Integration

The Observer (`bridge/observer.py`) imports `STAGE_TO_SKILL` and `get_next_stage` from the graph module. The `_next_sdlc_skill()` function uses graph-based routing: it finds the last completed/failed stage, calls `get_next_stage()` with the appropriate outcome and `cycle_count`, and returns the next stage and skill command. The `cycle_count` is derived from the session's history entries tagged with `stage: "PATCH"`.

The coach (`bridge/coach.py`) imports `DISPLAY_STAGES` and `STAGE_TO_SKILL` from the graph module instead of maintaining its own hardcoded copies.

Individual `/do-*` skills no longer contain pipeline navigation language. They report their results; the Observer/graph determines what happens next.

## Files

| File | Role |
|------|------|
| `bridge/pipeline_graph.py` | Canonical graph definition |
| `bridge/observer.py` | Uses `get_next_stage()` for graph-based routing with cycle counting |
| `bridge/coach.py` | Imports `DISPLAY_STAGES` and `STAGE_TO_SKILL` (no local copies) |
| `bridge/stage_detector.py` | `STAGE_ORDER` unchanged (display only) |
| `tests/unit/test_pipeline_graph.py` | 27 tests covering all routing scenarios |
