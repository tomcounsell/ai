---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-15
tracking: https://github.com/tomcounsell/ai/issues/2091
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-15T00:00:00Z
---

# Fix SDLC Router Happy-Path Merge Termination (#2091)

## Problem

`test_1036_replay_terminates` (Scenario 4, "happy-path termination") in
`tests/unit/test_sdlc_router_oscillation.py` fails at line 464:

```python
happy = {"ISSUE":"completed","PLAN":"completed","CRITIQUE":"completed",
         "BUILD":"completed","TEST":"completed","REVIEW":"completed","DOCS":"completed"}
r4 = decide_next_dispatch(happy, {"pr_number": 1039})
assert r4.skill == SKILL_DO_MERGE   # gets "/do-pr-review"
```

The issue was filed as a suspected router regression. Investigation shows the
**router is correct**; the **test fixture is stale**.

## Root Cause

Scenario 4 seeds all stages `completed` and a PR open, but supplies **no
recorded REVIEW verdict** (`meta = {"pr_number": 1039}` only). Walking
`decide_next_dispatch`:

- No guard trips (G3 only redirects when `last`/`proposed` is a plan-stage
  skill; G6 needs `pr_merge_state == "CLEAN"`).
- Row 8e (`_rule_review_completed_no_verdict`, added by #2062 WS3b) matches:
  `REVIEW == completed` with **no recorded verdict** is treated as an unearned
  marker and re-dispatched to `/do-pr-review`.
- Row 10 (`_rule_ready_to_merge`) would not fire either — since #2062 WS3a it
  requires a recorded `APPROVED` verdict, which the fixture omits.

Under the #2062 **WS3c invariant** a `REVIEW == completed` marker is unwritable
without a readable verdict, so an all-completed state with no verdict is an
*impossible* production state (or a crash), and the router deliberately routes
it to re-review. This exact behavior is pinned by
`tests/unit/test_sdlc_router.py::TestRow10VerdictGate::test_review_completed_no_verdict_routes_to_review`
(the #1897 replay) and `TestRow8eNoVerdictRecovery::test_dispatches_pr_review_row_8e`.

The Scenario 4 fixture predates #2062 and omits the verdict. Notably, the
**12-turn replay in the same test** already passes `latest_review_verdict:
"APPROVED"` on its final `happy` turn and reaches `/do-merge` correctly — only
the standalone Scenario 4 assertion forgot the verdict.

## Solution

Update the stale Scenario 4 fixture to supply the recorded `APPROVED` verdict —
matching the real happy-path terminal state, the #2062 WS3c invariant, and the
replay's own final turn. With the verdict present, Row 8e steps aside and Row 10
routes to `/do-merge`. Strengthen the assertion to pin `row_id == "10"` so the
happy-path terminal is anchored to the merge row, not merely the merge skill.

The router source (`agent/sdlc_router.py`) is **not** changed: any router
short-circuit that merged a no-verdict all-completed state would regress the
shipped #2062 behavior (`test_review_completed_no_verdict_routes_to_review`,
`test_rule_ready_to_merge_false_without_verdict`).

## Failure Path Test Strategy

The corrected fixture IS the failure-path assertion: it drives the happy-path
terminal state (all stages completed + PR open + recorded APPROVED verdict) and
pins the terminal dispatch to `/do-merge` via Row 10. The negative side is
already covered by the shipped #2062 tests, which pin the no-verdict variant to
`/do-pr-review` — those must continue to pass unchanged, proving the fix does
not weaken the verdict gate.

## Test Impact
- [ ] `tests/unit/test_sdlc_router_oscillation.py::test_1036_replay_terminates` — UPDATE: add `latest_review_verdict: "APPROVED"` to the Scenario 4 meta and assert `r4.row_id == "10"`, aligning the standalone assertion with the replay's final turn and the #2062 WS3c invariant.
- [ ] No other test changes: the #2062 row-8e/row-10 tests in `tests/unit/test_sdlc_router.py` must pass **unchanged**, confirming the no-verdict re-review gate is preserved.

## Rabbit Holes

- Do NOT add a router terminal short-circuit for no-verdict all-completed
  states — it reintroduces the #1897 misroute (#2062 WS3a/b/c).
- Do NOT relax Row 8e or the Row 10 verdict gate.
- Do NOT touch head_sha staleness (WS3d) logic — out of scope.
- The no-verdict `REVIEW == completed` re-review behavior (Row 8e) is
  deliberate #2062 behavior and is loop-bound by G4 (`guard_g4_oscillation`
  escalates to `Blocked` after `MAX_SAME_STAGE_DISPATCHES`). Whether the WS3c
  stage-marker write-refusal is fully enforced at every write-site is a
  separate #2062 concern, out of scope for this fixture correction.

## No-Gos (Out of Scope)

- No changes to `agent/sdlc_router.py` dispatch logic or guards.
- No changes to `agent/pipeline_graph.py`.
- No changes to the stage-marker write path (WS3c invariant enforcement).

## Update System

No update-system changes required — this is a test-only fixture correction with
no new dependencies, config, or migration steps.

## Agent Integration

No agent integration required — this change touches only a unit-test fixture. No
new CLI entry point, no bridge import, no runtime surface.

## Documentation
- [ ] Update the `test_1036_replay_terminates` docstring in `tests/unit/test_sdlc_router_oscillation.py` to record that the happy-path terminal fixture must carry a recorded APPROVED verdict, per the #2062 WS3c invariant (`REVIEW == completed` is unwritable without a readable verdict), so this stale-fixture failure class does not recur.
- [ ] Add a one-line note to `docs/sdlc/do-test.md` cross-referencing the #2062 verdict-gate invariant, so future router-test authors seed the recorded verdict when asserting `/do-merge` termination.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Repro test passes | `pytest tests/unit/test_sdlc_router_oscillation.py::test_1036_replay_terminates -n0` | PASS, `r4.skill == /do-merge`, `r4.row_id == 10` |
| No #2062 regression | `pytest tests/unit/test_sdlc_router.py -k "Row10VerdictGate or Row8eNoVerdictRecovery" -n0` | all PASS unchanged |
| Full oscillation file green | `pytest tests/unit/test_sdlc_router_oscillation.py -n0` | all PASS |
| Format clean | `python -m ruff format tests/unit/test_sdlc_router_oscillation.py` | no reformatting needed |

## Success Criteria

- `test_1036_replay_terminates` passes: all-completed + PR-open + APPROVED
  verdict → `/do-merge` (Row 10).
- No other `test_sdlc_router_oscillation.py` scenario regresses.
- The #2062 row-8e/row-10 gate tests pass unchanged.
- The happy-path termination is pinned by an assertion on both `skill` and
  `row_id`.
