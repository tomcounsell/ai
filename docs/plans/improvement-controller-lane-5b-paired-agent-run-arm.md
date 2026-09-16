---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-09-16
tracking: https://github.com/tomcounsell/ai/issues/3311
last_comment_id: 5685151250
---

# Improvement controller lane 5b: paired agent-run arm and the deferred evaluations

## Problem

TODO

## Freshness Check

TODO

## Prior Art

TODO

## Research

TODO

## Data Flow

TODO

## Architectural Impact

TODO

## Appetite

TODO

## Prerequisites

TODO

## Solution

TODO

### Key Elements

TODO

### Flow

TODO

### Technical Approach

TODO

## Failure Path Test Strategy

TODO

### Exception Handling Coverage

TODO

### Empty/Invalid Input Handling

TODO

### Error State Rendering

TODO

## Test Impact

No existing tests affected — this lane adds a new arm job mode alongside the
existing retrieve mode and extends the runner by additive branches only. Lane 4's
parity, blinding, corruption, and verdict-disjointness tests must keep passing
unchanged; they are the regression net, not modified files. (Full section below.)

## Rabbit Holes

TODO

## Risks

TODO

## Race Conditions

TODO

## No-Gos (Out of Scope)

TODO

## Update System

TODO

## Agent Integration

TODO

## Documentation

- [ ] Create `docs/features/agent-run-arm.md` describing the agent_run arm mode, the frozen task-set contract, and how to read its report
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

TODO

## Team Orchestration

TODO

## Step by Step Tasks

TODO

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lane 4 arena tests still pass | `scripts/pytest-clean.sh tests/unit/test_improvement_eval_arena.py -q` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

TODO
