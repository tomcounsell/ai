---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-18
tracking: https://github.com/tomcounsell/ai/issues/3411
last_comment_id:
---

# Lane branch identity: one recorded branch, read by the guard, the cleanup, and the checkpoint

## Problem

_placeholder_

## Freshness Check

_placeholder_

## Prior Art

_placeholder_

## Research

_placeholder_

## Spike Results

_placeholder_

## Data Flow

_placeholder_

## Why Previous Fixes Failed

_placeholder_

## Architectural Impact

_placeholder_

## Appetite

_placeholder_

## Prerequisites

_placeholder_

## Solution

_placeholder_

## Failure Path Test Strategy

_placeholder_

## Test Impact

- [ ] `tests/e2e/test_context_propagation.py:152` — UPDATE: asserts `derived_branch_name == "session/my-cool-feature"` when a slug is set; the accessor's precedence is being inverted.
- [ ] `tests/unit/test_session_branch_guard.py` — UPDATE: the #887 main-checkout cases must stay RED-on-removal under the new resolver.
- [ ] `tests/unit/test_safe_delete_branch.py` — UPDATE: add the checked-out-in-a-worktree refusal case.

## Rabbit Holes

_placeholder_

## Risks

_placeholder_

## Race Conditions

_placeholder_

## No-Gos (Out of Scope)

_placeholder_

## Update System

_placeholder_

## Agent Integration

_placeholder_

## Documentation

- [ ] Create `docs/features/lane-branch-identity.md` naming the single source of truth for a lane's branch and the three consumers that read it.
- [ ] Add the entry to the `docs/features/README.md` index table.
- [ ] Update `docs/features/sdlc-lane-identity.md` with a cross-link (slug identity vs. branch identity are now explicitly distinct).

## Success Criteria

_placeholder_

## Team Orchestration

_placeholder_

## Step by Step Tasks

_placeholder_

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lint clean | `python -m ruff check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

_placeholder_
