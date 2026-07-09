---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-09
tracking: https://github.com/tomcounsell/ai/issues/1916
last_comment_id:
---

# stage-marker in_progress rejected at PLAN on a fresh pipeline

## Problem

On a skills-only machine processing a batch of pre-filed issues, the SDLC
pipeline begins directly at the PLAN stage. The first marker write of that
pipeline is `sdlc-tool stage-marker --stage PLAN --status in_progress`. In two
independent runs (#1902 and #1905) that write was rejected:

> `sdlc_stage_marker: FAILED to write PLAN=in_progress (substrate present, session resolved, but the state-machine write was rejected or raised). State NOT persisted.`

The subsequent `--status completed` write for the same stage succeeded. The net
effect: PLAN jumps `pending → completed` with no `in_progress` transition ever
recorded, and the pipeline's `ISSUE` stage is left stuck at `ready` forever.

**Current behavior:**
- `start_stage("PLAN")` enforces that its predecessor `ISSUE` is `completed`. A
  freshly auto-ensured session has `ISSUE = ready` (never `completed`), so the
  predecessor check raises `ValueError` and the marker tool exits 1.
- The `--status completed` path force-sets the target stage to `in_progress`
  (bypassing the predecessor check entirely) then completes it — an asymmetry
  that lets `completed` succeed where `in_progress` fails, and leaves `ISSUE`
  un-completed.
- The skill invocations wrap the call in `2>/dev/null || true` (see
  `docs/sdlc/do-plan.md:12`), so the D7 "loud failure" stderr diagnostic is
  swallowed and the failure looks silent (`{}` on stdout — matching the
  "returned `{}` silently" note from the #1898 run).

**Desired outcome:**
- `stage-marker --status in_progress` at PLAN on a fresh issue persists:
  `ISSUE → completed`, `PLAN → in_progress`.
- The pipeline is left internally consistent (no stage stuck at `ready` behind a
  later completed stage).
- Consumers that key off the `in_progress` state (dashboards, stall detection,
  dispatch-history analysis) see the transition.

## Freshness Check

**Baseline commit:** `0029d6cb` (`git rev-parse HEAD` at plan time, branch `main`)
**Issue filed at:** 2026-07-06T04:15:25Z
**Disposition:** Unchanged — bug reproduced live against current `main`.

**Reproduction (live, this plan session):**
```
$ sdlc-tool stage-marker --stage PLAN --status in_progress --issue-number 1916
sdlc_stage_marker: FAILED to write PLAN=in_progress (substrate present, session
resolved, but the state-machine write was rejected or raised). State NOT persisted.
{}
EXIT: 1
```
Direct state-machine probe on the auto-ensured session `sdlc-local-1916`:
```
states: {'ISSUE': 'ready', 'PLAN': 'pending', ...}
ValueError: Cannot start PLAN: no predecessor completed. Predecessors: {'ISSUE': 'ready'}
```

**File:line references re-verified:**
- `tools/sdlc_stage_marker.py:186-193` — the `in_progress` branch calls
  `sm.start_stage(stage)` and returns `{}, 1` on `ValueError`. Still holds.
- `tools/sdlc_stage_marker.py:194-203` — the `completed` branch force-sets
  `states[stage] = "in_progress"` when current not in `("in_progress","ready")`,
  bypassing predecessors. Still holds (this is the asymmetry).
- `agent/pipeline_state.py:436-502` — `start_stage()` predecessor enforcement;
  `PLAN` has no cycle early-return that applies on a fresh session, so it falls
  to the linear predecessor check at 488-502 and raises. Still holds.
- `agent/pipeline_graph.py:42` — `("ISSUE", "success"): "PLAN"` confirms ISSUE
  is PLAN's sole success-predecessor. Still holds.

**Cited sibling issues/PRs re-checked:**
- #1902, #1905, #1898 — referenced only as reproduction contexts (batch runs).
  No code dependency.

**Commits on main since issue was filed (touching referenced files):**
- `0f33567e` "SDLC issue ownership lock (#1954)" — added
  `renew_issue_lock_for_session()` to `sdlc_stage_marker.write_marker` (runs
  before the state-machine write). Irrelevant to the root cause; does not change
  the predecessor rejection.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Line numbers are current as of `0029d6cb`.

## Prior Art

- **#1954 / PR #1956**: SDLC issue-ownership lock. Touched the same marker
  function but for a different concern (lock renewal). No overlap with the
  predecessor-ordering defect.
- No closed issue or merged PR found addressing `start_stage` predecessor
  rejection at the first stage of a pipeline. This is the first fix for this
  defect.

## Research

No relevant external findings — this is a purely internal state-machine bug with
no external library or API surface. Proceeding with codebase context.

## Data Flow

1. **Entry point**: A skill (e.g. do-plan) shells out `sdlc-tool stage-marker
   --stage PLAN --status in_progress --issue-number N` at stage start.
2. **`tools/sdlc_stage_marker.py::write_marker`**: probes substrate (PRESENT),
   resolves/auto-ensures the PM session via `find_session(..., ensure=True)`.
   On a skills-only batch machine this creates a *fresh* session
   (`sdlc-local-N`) with no prior stage history.
3. **`PipelineStateMachine.__init__`**: loads empty `stage_states`, initializes
   all stages to `pending`, then sets `ISSUE = ready` (the "nothing started"
   default). Critically, `ISSUE` is never set to `completed`.
4. **`start_stage("PLAN")`**: reaches the linear predecessor check, finds
   `ISSUE = ready` (not `completed`), and raises `ValueError`.
5. **Output (broken)**: marker returns `{}, 1`; skill's `2>/dev/null` swallows
   the stderr diagnostic. No `in_progress` transition persisted.

## Why Previous Fixes Failed

No prior fix attempted this defect. Root-cause note: the marker tool is
documented as a "best-effort, belt-and-suspenders record of where the pipeline
is", but it delegates to `start_stage()`, whose predecessor enforcement is
designed for the *router's* forward-transition decisions, not for recording an
already-reached stage. The two callers have different needs; the marker inherited
strictness it should not have.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0-1 (root cause and fix shape are settled; the one open question
  is scope of the `completed`-path consistency fix)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. (Redis must be
reachable to run the state-machine unit/integration tests, which is already true
for the standard test environment.)

## Solution

### Key Elements

- **`PipelineStateMachine.start_stage(stage, backfill_predecessors=False)`**:
  opt-in mode. When `True` and the linear predecessor check would fail, walk the
  success-edge predecessor chain back toward ISSUE and promote any predecessor in
  `pending`/`ready`/`in_progress` to `completed`, then activate the target. If any
  predecessor is `failed`, do NOT backfill — raise as before (a failed
  predecessor is a genuine inconsistency worth surfacing loudly). Default stays
  strict, so the router (`sdlc_router`) and bridge hook (`pre_tool_use`) keep
  today's ordering enforcement unchanged.
- **`tools/sdlc_stage_marker.py::write_marker`**: the best-effort marker opts in
  (`backfill_predecessors=True`) on the `in_progress` path, and applies the same
  predecessor backfill on the `completed` path so a forced completion no longer
  leaves ISSUE stuck at `ready`.
- **Documentation**: record that the marker records "reality" (reaching a stage
  implies prior stages were reached) while the router enforces ordering — the two
  callers deliberately differ.

### Flow

Fresh session at PLAN → `stage-marker --status in_progress` → backfill ISSUE to
`completed` → PLAN set to `in_progress` and persisted → dashboards/stall
detection observe the transition.

### Technical Approach

- Add `backfill_predecessors: bool = False` to `start_stage`. On the predecessor
  loop's failure branch (currently `pipeline_state.py:499-502`), if
  `backfill_predecessors` and no predecessor is `failed`: recursively backfill
  the success-edge predecessor chain to `completed` (reuse `_get_predecessors`),
  then `_activate_stage(stage)`. Keep the raise when a predecessor is `failed`.
- The backfill only promotes states in `{pending, ready, in_progress}` →
  `completed`; it never touches `failed`. This keeps a real failure signal intact.
- In `write_marker`, pass `backfill_predecessors=True` to `start_stage` on the
  `in_progress` branch. On the `completed` branch, before `complete_stage`,
  backfill predecessors the same way (via `start_stage(stage,
  backfill_predecessors=True)` used to reach `in_progress`, then complete) so the
  pipeline is left consistent. Preserve the existing idempotent-already-completed
  no-op (exit 0).
- The D7 loud-failure contract is preserved for genuine failures: a `failed`
  predecessor, or any exception, still yields exit 1. The change only converts
  the *first-write-at-a-forward-stage* case from a false failure into a
  persisted transition.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/sdlc_stage_marker.py:207` (`except Exception`) — add/keep a test
  asserting exit 1 on a genuine write failure (e.g. `session.save()` raising),
  verifying the loud path still fires when backfill is not the issue.
- [ ] `agent/hooks/pre_tool_use.py:329` (`except Exception`) swallows
  `start_stage` errors — out of scope to change; note that its default
  (strict, no backfill) is intentionally unchanged.

### Empty/Invalid Input Handling
- [ ] Invalid stage / status already return `{}, 0` early (`sdlc_stage_marker.py:143-149`)
  — add/confirm a test that an unknown stage is unaffected by backfill.
- [ ] `start_stage` with a `failed` predecessor + `backfill_predecessors=True`
  must still raise `ValueError` (not silently backfill over a failure).

### Error State Rendering
- [ ] Confirm the marker CLI still prints the loud stderr diagnostic and exits 1
  on a genuine `PRESENT_WRITE_FAILED` (failed predecessor), and prints the clean
  `{"stage":"PLAN","status":"in_progress"}` + exit 0 on the fixed path.

## Test Impact

- [ ] `tests/unit/test_sdlc_stage_marker.py` — UPDATE: any test asserting that
  `in_progress` at PLAN on a fresh/no-ISSUE session returns exit 1 must be
  updated to assert exit 0 with `ISSUE → completed, PLAN → in_progress`
  persisted. (Audit this file for such an assertion before editing.)
- [ ] `tests/unit/test_pipeline_state.py` / `tests/unit/test_pipeline_state_machine.py`
  — UPDATE: add cases for `start_stage(stage, backfill_predecessors=True)`; verify
  existing strict-default `start_stage` tests still pass unchanged (default
  behavior must not shift).
- [ ] New test (first-write-at-PLAN acceptance) — ADD to
  `tests/unit/test_sdlc_stage_marker.py`: fresh session → `in_progress` PLAN
  persists; and a `failed`-predecessor case that still exits 1.

## Rabbit Holes

- **Rewriting the router's ordering enforcement.** The router's strict
  `start_stage` default is correct — do not weaken it. Only the marker opts in.
- **Auto-completing ISSUE inside `__init__`.** Tempting, but changing the "fresh
  session sets ISSUE=ready" default would ripple through every consumer of
  `stage_states`. Keep the change at the write boundary (opt-in backfill).
- **Fixing the `2>/dev/null` swallowing in skill context files.** Once the write
  persists, the loud path no longer fires for this case, so the swallowing is no
  longer masking a real failure here. Changing every skill's redirect is a
  separate, broader concern — leave it out of this fix.
- **Blanket backfill across all stages including over `failed`.** Backfilling
  over a `failed` predecessor would erase a real failure signal — explicitly
  excluded.

## Risks

### Risk 1: Backfill masks a genuinely-skipped stage
**Impact:** A marker write for a late stage (e.g. REVIEW) could backfill BUILD/TEST
to completed even if they were truly skipped.
**Mitigation:** The marker records "we reached this stage", which implies prior
stages ran; this matches the tool's documented best-effort semantics. The guard
against masking *failures* (never backfill over `failed`) preserves the only
signal that would indicate genuine trouble. Router/hook callers keep strict
default, so real ordering enforcement is untouched.

### Risk 2: Behavior change leaks to strict callers
**Impact:** If the default flipped, the router could silently advance over
incomplete predecessors.
**Mitigation:** `backfill_predecessors` defaults to `False`; a test asserts the
strict default is unchanged for existing `start_stage` callers.

## Race Conditions

No new race conditions identified. `write_marker` already runs a load → mutate →
`_save()` sequence on the resolved session; `_save()` re-reads and merges
underscore-metadata to avoid clobbering concurrent verdict/dispatch writes
(`pipeline_state.py:_save`). Backfilling predecessors adds more stage keys to the
same single save, within the same code path — no new cross-process interleaving.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG] Nothing filed separately.
- Nothing deferred — every relevant item is in scope for this plan. The skill
  `2>/dev/null` redirect audit is called out as a rabbit hole, not a deferred
  work item: this fix removes the masked failure for the documented case, and a
  fleet-wide redirect change is a distinct concern with no acceptance dependency
  here.

## Update System

No update system changes required — the fix is purely internal to
`agent/pipeline_state.py` and `tools/sdlc_stage_marker.py`. No new dependencies,
config files, or migration steps. `stage_states` is an existing Popoto field on
`AgentSession`; no schema change, so no `scripts/update/migrations.py` entry is
needed.

## Agent Integration

No agent integration required — `sdlc-tool stage-marker` is already wired as a
CLI the agent invokes via Bash (dispatched through `~/.local/bin/sdlc-tool` →
`python -m tools.sdlc_stage_marker`). This fix changes the tool's internal
behavior only; no new MCP surface, `.mcp.json`, or bridge import is involved.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/pipeline-state-machine.md` to document the
  `backfill_predecessors` parameter on `start_stage` and the marker-vs-router
  semantics distinction (marker records reality; router enforces ordering).
- [ ] Update `docs/features/sdlc-stage-tracking.md` to note that a fresh pipeline
  entering at PLAN backfills ISSUE to completed on the first `in_progress` write.

### Inline Documentation
- [ ] Docstring on `start_stage` explaining `backfill_predecessors` and the
  never-backfill-over-`failed` guard.
- [ ] Update the D7 degradation-contract docstring in `sdlc_stage_marker.py` to
  reflect that first-write-at-a-forward-stage now persists rather than failing.

## Success Criteria

- [ ] `sdlc-tool stage-marker --stage PLAN --status in_progress --issue-number N`
  on a fresh issue exits 0 and persists `ISSUE → completed`, `PLAN → in_progress`
  (verified by re-running the reproduction from the Freshness Check).
- [ ] `start_stage(stage, backfill_predecessors=True)` with a `failed`
  predecessor still raises `ValueError` (loud path preserved).
- [ ] Strict-default `start_stage` behavior is unchanged for router/hook callers
  (existing tests pass).
- [ ] A test covers the first-write-at-PLAN path (acceptance criterion from #1916).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sdlc_stage_marker.py tests/unit/test_pipeline_state.py tests/unit/test_pipeline_state_machine.py -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/sdlc_stage_marker.py agent/pipeline_state.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/sdlc_stage_marker.py agent/pipeline_state.py` | exit code 0 |
| Backfill param exists | `grep -c "backfill_predecessors" agent/pipeline_state.py` | output > 0 |
| Marker opts in | `grep -c "backfill_predecessors=True" tools/sdlc_stage_marker.py` | output > 0 |
| Fresh PLAN in_progress persists | `sdlc-tool stage-marker --stage PLAN --status in_progress --issue-number 1916; echo $?` | output contains in_progress |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. Scope of the `completed`-path consistency fix: the acceptance criterion is
   satisfied by the `in_progress` fix alone. Should this plan also backfill
   predecessors on the `completed` path (so a forced PLAN completion no longer
   leaves ISSUE at `ready`), or keep the fix minimal to `in_progress` and file
   the `completed`-path inconsistency separately? Current plan includes it as a
   small, same-mechanism change.
