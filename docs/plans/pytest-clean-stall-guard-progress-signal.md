---
status: Planning
type: bug
appetite: Small
owner: Dev (lane session/sdlc-3317-stall-progress)
created: 2026-09-16
tracking: https://github.com/tomcounsell/ai/issues/3317
last_comment_id:
---

# pytest-clean stall guard progress signal

## Problem

TODO fill.

## Freshness Check

TODO fill.

## Prior Art

TODO fill.

## Research

No relevant external findings — proceeding with codebase context and training data.

## Data Flow

TODO fill.

## Appetite

TODO fill.

## Prerequisites

TODO fill.

## Solution

TODO fill.

## Failure Path Test Strategy

TODO fill.

## Test Impact

No existing tests affected — the stall watcher currently has no dedicated regression test and the zero-tests file pins the watcher off, so the change is additive pending new tests below.

## Rabbit Holes

TODO fill.

## Risks

TODO fill.

## Race Conditions

TODO fill.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

## Update System

TODO fill.

## Agent Integration

TODO fill.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/pytest-clean-stall-guard.md` describing the CPU-or-progress OR-signal, the `-v` line-count observation, and the CPU-only fallback when output is unobservable
- [ ] Add entry to `docs/features/README.md` index table for the stall-guard page

### Inline Documentation
- [ ] Comment the watcher OR-condition in `scripts/pytest-clean.sh` explaining why either CPU or outcome lines reset the stall clock
- [ ] Document `PYTEST_STALL_LIMIT_S=0` escape hatch behavior next to the new output-observation knob

## Success Criteria

TODO fill.

## Team Orchestration

TODO fill.

## Step by Step Tasks

TODO fill.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Placeholder check replaced during fill | `echo ok` | output contains ok |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

TODO fill.
