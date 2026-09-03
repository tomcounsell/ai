---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-09-03
tracking: https://github.com/tomcounsell/ai/issues/3065
last_comment_id: 5521519215
---

# SDLC Control Plane: Route on Read Facts, Not Asserted Facts (Residual)

## Problem

[skeleton]

## Freshness Check

[skeleton]

## Prior Art

[skeleton]

### Why Previous Fixes Failed

[skeleton]

## Research

[skeleton]

## Spike Results

[skeleton]

## Data Flow

[skeleton]

## Architectural Impact

[skeleton]

## Appetite

[skeleton]

## Prerequisites

[skeleton]

## Solution

### Key Elements

[skeleton]

### Flow

[skeleton]

### Technical Approach

[skeleton]

## Failure Path Test Strategy

[skeleton]

## Test Impact

- [ ] `tests/unit/test_verification_parser.py::test_unknown_expectation_returns_false` — REPLACE: currently asserts the silent `False` fall-through as *intended* behavior. It must assert a distinct malformed-expectation outcome instead.
- [ ] `tests/unit/test_sdlc_router.py` — UPDATE: guard and routing-table assertions for G3, G8, and row 2b change shape as those predicates gain ground-truth reads.
- [ ] `tests/unit/test_validate_build.py` — UPDATE: the `runner_agreement.md` parity fixture gains a timeout row, which the two runners currently disagree on.

## Rabbit Holes

[skeleton]

## Risks

[skeleton]

## Race Conditions

[skeleton]

## No-Gos (Out of Scope)

[skeleton]

## Update System

[skeleton]

## Agent Integration

[skeleton]

## Documentation

- [ ] Update `docs/features/sdlc-lane-identity.md` — the recorded-slug repair path changes.
- [ ] Update `docs/features/machine-readable-dod.md` — the expectation grammar and malformed-row handling change.
- [ ] Create `docs/features/sdlc-router-ground-truth-reads.md` describing the read-facts property and where each router decision now sources its facts.

## Success Criteria

[skeleton]

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Router unit tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_router.py -q -n 2` | exit code 0 |

## Step by Step Tasks

[skeleton]

## Open Questions

[skeleton]
