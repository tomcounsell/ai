---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-09
tracking: https://github.com/tomcounsell/ai/issues/1916
last_comment_id:
revision_applied: true
revision_round: 2
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
- PM check-ins: 0 (root cause, fix shape, and scope are all settled; the
  completed-path consistency fix is confirmed in scope — see Scope Decision below)
- Review rounds: 1

### Scope Decision: completed-path backfill is IN scope

The bug statement (Problem section) names two symptoms of one defect: (a) the
`in_progress` write is rejected, and (b) the pipeline's `ISSUE` stage "is left
stuck at `ready` forever" behind a later `completed` stage. Symptom (b) is
produced by the `completed`-path force-set (`sdlc_stage_marker.py:200-202`), which
completes PLAN without ever completing ISSUE. Fixing only the `in_progress` path
would ship half the described defect: the very next run that reaches PLAN via a
`completed` write (e.g. the `--status completed` marker at end of Phase 4) would
still strand ISSUE at `ready`.

**Alternative rejected — loud-rejection-only.** We considered making the
`completed` path *reject* (exit 1) when predecessors are incomplete instead of
backfilling. Rejected because: (1) the marker is documented as a best-effort
"record of where the pipeline reached", so a completion write legitimately implies
prior stages were reached — rejecting it would turn a routine end-of-stage marker
into a hard failure on every fresh pipeline; and (2) it would leave the pipeline
*more* inconsistent (PLAN completed, ISSUE ready, and now a loud error too),
solving nothing the operator can act on. Backfilling with the never-over-`failed`
guard leaves the pipeline internally consistent while still surfacing genuine
failures loudly. Both paths therefore use the same `_backfill_predecessors`
mechanism.

## Prerequisites

No prerequisites — this work has no external dependencies. (Redis must be
reachable to run the state-machine unit/integration tests, which is already true
for the standard test environment.)

## Solution

### Key Elements

- **New standalone helper `PipelineStateMachine._backfill_predecessors(stage)`**:
  the single source of truth for synthetic predecessor promotion, reachable
  independently of `start_stage`'s early no-op. Walks the success-edge
  predecessor chain back toward ISSUE using **scan-then-mutate**: first scan the
  whole transitive chain and, if *any* member is `failed`, raise `ValueError`
  **before mutating anything** (a failed predecessor is a genuine inconsistency
  worth surfacing loudly, and no partial state is persisted). Only after a clean
  scan does it promote every chain member in `{pending, ready, in_progress}` to
  `completed` in one pass, persist with a **single `_save()`**, and emit a
  distinct `sdlc.stage_backfilled` analytics metric per synthetic promotion so
  the backfills are observable (they no longer masquerade as ordinary
  `sdlc.stage_started` events).
- **`PipelineStateMachine.start_stage(stage, backfill_predecessors=False)`**:
  opt-in mode. When `True` and the linear predecessor check at the tail of
  `start_stage` would fail, call `_backfill_predecessors(stage)` then
  `_activate_stage(stage)`. Default stays strict, so the router (`sdlc_router`)
  and bridge hook (`pre_tool_use`) keep today's ordering enforcement unchanged.
- **`tools/sdlc_stage_marker.py::write_marker`**: the best-effort marker opts in
  (`backfill_predecessors=True`) on the `in_progress` path. On the `completed`
  path it calls `_backfill_predecessors(stage)` **directly, before** touching the
  target stage — it must NOT pre-set `states[stage] = "in_progress"` first,
  because routing the completed-path backfill through `start_stage` would hit the
  `if current == "in_progress": return` early no-op (`pipeline_state.py:453-455`)
  and skip backfill entirely, leaving ISSUE stuck at `ready` — reproducing the
  very bug this plan fixes. Calling the standalone helper sidesteps that no-op.
- **Documentation**: record that the marker records "reality" (reaching a stage
  implies prior stages were reached) while the router enforces ordering — the two
  callers deliberately differ.

### Flow

Fresh session at PLAN → `stage-marker --status in_progress` → backfill ISSUE to
`completed` → PLAN set to `in_progress` and persisted → dashboards/stall
detection observe the transition.

### Technical Approach

**1. New `_backfill_predecessors(stage)` helper on `PipelineStateMachine`
(spine-restricted walk, scan-then-mutate, single save, distinct metric).**

The walk MUST be restricted to the ISSUE-rooted main-line spine. This is the
round-2 BLOCKER fix. `_get_predecessors` derives from `PIPELINE_EDGES`, and TEST
has **two** success in-edges — `("BUILD","success"): "TEST"` **and**
`("PATCH","success"): "TEST"` (`pipeline_graph.py:45,57`). So
`_get_predecessors("TEST") == [BUILD, PATCH]`. A naive transitive walk that
reached TEST (any `in_progress`/`completed` marker for TEST, REVIEW, DOCS, or
MERGE) would pull the off-happy-path **PATCH** into `to_promote` and force-set
`states["PATCH"] = "completed"` even though no patch ran — corrupting later
genuine TEST→PATCH re-entry (backfill mutates `self.states` directly, so
`patch_cycle_count` is never incremented). The fix: only walk predecessors that
sit on the ISSUE spine.

A stage is **on the ISSUE spine** iff its transitive *success*-predecessor set
contains ISSUE (or it IS ISSUE). PATCH has **no** success in-edge
(`_get_predecessors("PATCH") == []`, because the only edges into PATCH are
`("TEST","fail")`, `("REVIEW","fail")`, `("REVIEW","partial")` — none are
`"success"`), so PATCH is off-spine and is skipped. The spine is exactly the
linear happy path ISSUE→PLAN→CRITIQUE→BUILD→TEST→REVIEW→DOCS→MERGE.

```python
def _reaches_issue(self, stage: str) -> bool:
    """True iff `stage` sits on the ISSUE-rooted success spine — its transitive
    success-predecessor set contains ISSUE (or it IS ISSUE). PATCH is off-spine
    (`_get_predecessors("PATCH") == []`), so this returns False for it — that is
    what stops a backfill reaching TEST (predecessors [BUILD, PATCH]) from
    pulling the off-happy-path PATCH into the promotion set.
    """
    if stage == "ISSUE":
        return True
    seen: set[str] = set()
    frontier = list(self._get_predecessors(stage))
    while frontier:
        p = frontier.pop()
        if p in seen:
            continue
        seen.add(p)
        if p == "ISSUE":
            return True
        frontier.extend(self._get_predecessors(p))
    return False

def _backfill_predecessors(self, stage: str) -> list[str]:
    """Promote the ISSUE-rooted success spine behind `stage` to completed.

    Scan-then-mutate: collect every transitive ON-SPINE predecessor currently in
    {pending, ready, in_progress}; if ANY collected member is `failed`, raise
    ValueError BEFORE mutating (a failed predecessor is a real inconsistency,
    never silently erased, and no partial state is persisted). Then promote all
    collected members in one pass, persist with a single _save(), and emit
    sdlc.stage_backfilled per synthetic promotion. Off-spine predecessors (PATCH,
    reached via TEST's second success in-edge) are never walked or promoted.
    Returns the promoted stages.
    """
    to_promote: list[str] = []
    seen: set[str] = set()
    # Seed and extend the frontier with ON-SPINE predecessors only — PATCH,
    # being off-spine, is excluded here and never force-completed.
    frontier = [p for p in self._get_predecessors(stage) if self._reaches_issue(p)]
    while frontier:                       # SCAN — no mutation in this loop
        pred = frontier.pop()
        if pred in seen:
            continue
        seen.add(pred)
        st = self.states.get(pred, "pending")
        if st == "failed":
            raise ValueError(
                f"Cannot backfill predecessors of {stage}: {pred} is failed"
            )
        if st != "completed":
            to_promote.append(pred)
        frontier.extend(
            p for p in self._get_predecessors(pred) if self._reaches_issue(p)
        )
    for pred in to_promote:               # MUTATE — only after a clean scan
        self.states[pred] = "completed"
    if to_promote:
        self._save()                      # single persist for the whole chain
        for pred in to_promote:
            _record_stage_metric("sdlc.stage_backfilled", pred)
    return to_promote
```

The walk terminates at ISSUE (`_get_predecessors("ISSUE")` is empty) and never
descends into PATCH (the `_reaches_issue` filter drops it at both the seed and
the extend step). The helper never touches `failed` — the `raise` happens in the
scan phase before any `states[...]` assignment, so a deep `failed` member leaves
the machine untouched (fixes CONCERN 1: no partial persisted state). The
`sdlc.stage_backfilled` metric (fixes CONCERN 2) makes synthetic promotions
observable and distinct from real `sdlc.stage_started` transitions.

**2. `start_stage(stage, backfill_predecessors=False)`.** Add the keyword-only
default-`False` parameter. On the predecessor loop's failure branch (currently
`pipeline_state.py:499-502`): if `backfill_predecessors`, call
`self._backfill_predecessors(stage)` (which raises on a `failed` chain member,
preserving the loud path) then `self._activate_stage(stage)`; otherwise raise as
today. The strict default is unchanged for the router and `pre_tool_use` hook.

**3. `write_marker` completed-path fix (fixes the BLOCKER).** The current code
force-sets `states[stage] = "in_progress"` (`sdlc_stage_marker.py:200-202`) before
completing. Routing backfill through `start_stage` here is defeated by
`start_stage`'s `if current == "in_progress": return` no-op
(`pipeline_state.py:453-455`) — the target is already `in_progress`, so backfill
is skipped and ISSUE stays `ready`. Fix: call the standalone helper directly,
**before** setting the target in_progress:

```python
elif status == "completed":
    current = sm.states.get(stage, "pending")
    if current == "completed":
        return {"stage": stage, "status": status}, 0   # idempotent no-op
    if current not in ("in_progress", "ready"):
        sm._backfill_predecessors(stage)   # standalone; raises → outer except → exit 1
        sm.states[stage] = "in_progress"
    sm.complete_stage(stage)
```

Because `_backfill_predecessors` inspects only *predecessors* of `stage`, it is
independent of the target's own state — the `start_stage` no-op can never gate it.

**4. `write_marker` in_progress path.** Pass `backfill_predecessors=True` to
`start_stage` on the `in_progress` branch (fresh session: PLAN is `pending`, so
the early no-op does not fire; the predecessor check fails on `ISSUE=ready`, and
the new branch backfills ISSUE then activates PLAN).

**5. Loud-failure (D7) contract preserved.** A `failed` predecessor raises
`ValueError`: on the in_progress path it is caught at `sdlc_stage_marker.py:189`
→ exit 1; on the completed path it propagates to the outer `except Exception`
(line 207) → exit 1. Any other write exception still yields exit 1. The change
only converts the *first-write-at-a-forward-stage* case from a false failure into
a persisted transition.

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

### Backfill Helper Coverage (`_backfill_predecessors`)
- [ ] **Partial-state guard (CONCERN 1)**: with a chain where a *deeper*
  predecessor is `failed` and a nearer one is `pending`, assert `ValueError` is
  raised AND that no `states[...]` value changed (the nearer predecessor is still
  `pending`, `_save()` was not called). This proves the scan-then-mutate ordering.
- [ ] **Single save**: patch `_save` and assert it is invoked at most once per
  `_backfill_predecessors` call, regardless of chain length.
- [ ] **Distinct metric (CONCERN 2)**: assert `_record_stage_metric` is called
  with `"sdlc.stage_backfilled"` (not `"sdlc.stage_started"`) once per promoted
  predecessor; assert no metric is emitted when the chain is already completed.
- [ ] **Off-spine PATCH exclusion (round-2 BLOCKER)**: `_backfill_predecessors("TEST")`
  (and `("REVIEW")`, `("MERGE")`) on a fresh session promotes ISSUE/PLAN/CRITIQUE/BUILD
  to `completed` and leaves `PATCH` unchanged (`pending`); assert PATCH is NOT in the
  returned promoted list and `states["PATCH"] == "pending"`. Also unit-test the
  `_reaches_issue` predicate directly: `True` for ISSUE/PLAN/CRITIQUE/BUILD/TEST/
  REVIEW/DOCS/MERGE, `False` for PATCH.

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
- [ ] New test (completed-path backfill, BLOCKER regression) — ADD to
  `tests/unit/test_sdlc_stage_marker.py`: on a fresh session (`ISSUE=ready`,
  `PLAN=pending`), a `--status completed` write for PLAN must persist
  `ISSUE → completed` AND `PLAN → completed` (asserting the standalone helper is
  reached and the `start_stage` no-op does not gate it). A companion case where a
  predecessor is `failed` must exit 1 and leave state unmutated.
- [ ] New test (`_backfill_predecessors` unit) — ADD to
  `tests/unit/test_pipeline_state.py` / `test_pipeline_state_machine.py`: the
  scan-then-mutate, single-save, and `sdlc.stage_backfilled` metric assertions
  listed under Failure Path Test Strategy.
- [ ] New test (off-spine PATCH exclusion, round-2 BLOCKER regression) — ADD to
  `tests/unit/test_pipeline_state.py` / `test_pipeline_state_machine.py`:
  `_backfill_predecessors("TEST")` / `("REVIEW")` / `("MERGE")` on a fresh session
  promotes only the ISSUE-spine stages and leaves `PATCH == "pending"` with
  `patch_cycle_count == 0`; plus a direct `_reaches_issue` truth-table test
  (`True` for all spine stages, `False` for PATCH).

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
**Mitigation:** The marker records "we reached this stage on the happy path",
which implies the prior **spine** stages ran; this matches the tool's documented
best-effort semantics. Backfill is restricted to the ISSUE-rooted success spine
(ISSUE→PLAN→CRITIQUE→BUILD→TEST→REVIEW→DOCS→MERGE): reaching REVIEW implies
BUILD/TEST ran, but does **not** imply the off-spine PATCH ran, so PATCH is never
force-completed (see Technical Approach step 1 — `_reaches_issue`). This is the
key correction over the naive "all predecessors" walk: PATCH is an error-recovery
detour, not a happy-path prerequisite, so its absence is normal and must not be
synthesized. The guard against masking *failures* (never backfill over `failed`)
preserves the only signal that would indicate genuine trouble. Router/hook callers
keep strict default, so real ordering enforcement is untouched.

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
- [x] Update `docs/features/pipeline-state-machine.md` to document the
  `backfill_predecessors` parameter on `start_stage` and the marker-vs-router
  semantics distinction (marker records reality; router enforces ordering).
- [x] Update `docs/features/sdlc-stage-tracking.md` to note that a fresh pipeline
  entering at PLAN backfills ISSUE to completed on the first `in_progress` write.

### Inline Documentation
- [x] Docstring on `start_stage` explaining `backfill_predecessors` and the
  never-backfill-over-`failed` guard.
- [x] Update the D7 degradation-contract docstring in `sdlc_stage_marker.py` to
  reflect that first-write-at-a-forward-stage now persists rather than failing.

## Success Criteria

- [ ] `sdlc-tool stage-marker --stage PLAN --status in_progress --issue-number N`
  on a fresh issue exits 0 and persists `ISSUE → completed`, `PLAN → in_progress`
  (verified by re-running the reproduction from the Freshness Check).
- [ ] `sdlc-tool stage-marker --stage PLAN --status completed --issue-number N` on
  a fresh issue (`ISSUE=ready`) exits 0 and persists `ISSUE → completed`,
  `PLAN → completed` — no stage left stuck at `ready` behind a completed stage
  (this is the "ISSUE stuck at ready" half of the defect, now in scope).
- [ ] `start_stage(stage, backfill_predecessors=True)` and
  `_backfill_predecessors(stage)` with a `failed` predecessor still raise
  `ValueError` (loud path preserved) and mutate no state.
- [ ] Strict-default `start_stage` behavior is unchanged for router/hook callers
  (existing tests pass).
- [ ] A marker write at a **multi-predecessor stage** (TEST — predecessors
  `[BUILD, PATCH]`) or any stage downstream of it (REVIEW/DOCS/MERGE) that
  triggers backfill promotes the spine (ISSUE/PLAN/CRITIQUE/BUILD) to `completed`
  but leaves **PATCH un-promoted** (still `pending`), and `patch_cycle_count`
  stays `0` (round-2 BLOCKER regression guard).
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
| Standalone helper exists | `grep -c "_backfill_predecessors" agent/pipeline_state.py` | output > 0 |
| Backfill metric emitted | `grep -c "sdlc.stage_backfilled" agent/pipeline_state.py` | output > 0 |
| Completed path calls helper directly | `grep -c "_backfill_predecessors" tools/sdlc_stage_marker.py` | output > 0 |
| Spine-restriction predicate exists | `grep -c "_reaches_issue" agent/pipeline_state.py` | output > 0 |
| Backfill at TEST leaves PATCH pending | `pytest tests/unit/test_pipeline_state_machine.py -k patch_not_backfilled -q` | exit code 0 |

## Critique Results

**Round 1 verdict: NEEDS REVISION** (2026-07-09). Revision applied — all findings
addressed:

- **BLOCKER (completed-path backfill defeated by `start_stage` early no-op).**
  Fixed. Extracted a standalone `_backfill_predecessors(stage)` helper; the
  completed path calls it **directly** and no longer routes through `start_stage`
  (whose `if current == "in_progress": return` no-op skipped backfill) and no
  longer pre-sets `states[stage] = "in_progress"` before backfill. See Technical
  Approach step 3.
- **CONCERN 1 (partial state on a `failed` deep predecessor).** Fixed. The helper
  is scan-then-mutate: it validates the whole chain and raises before any
  mutation, then promotes all members with a single `_save()`. Test added.
- **CONCERN 2 (backfill bypassed metrics).** Fixed. The helper emits a distinct
  `sdlc.stage_backfilled` metric per synthetic promotion. Test added.
- **CONCERN 3 (open scope question).** Resolved in favor of inclusion. See
  "Scope Decision" under Appetite, which documents why the loud-rejection-only
  alternative was rejected. Open Questions section removed.
- **Two informational nits.** Accepted; no separate action taken beyond the above
  structural changes, which subsume them.

**Round 2 verdict: NEEDS REVISION** (2026-07-09). Revision applied — the new
BLOCKER is addressed:

- **BLOCKER (backfill walk promotes off-spine PATCH via TEST's second
  success-predecessor edge).** Fixed. `_get_predecessors("TEST")` returns
  `[BUILD, PATCH]` because `("PATCH","success"): "TEST"` exists alongside
  `("BUILD","success"): "TEST"` (`pipeline_graph.py:45,57`). The prior naive
  transitive walk would pull PATCH into `to_promote` for any backfill reaching
  TEST/REVIEW/DOCS/MERGE, force-completing PATCH without incrementing
  `patch_cycle_count` and corrupting later genuine TEST→PATCH re-entry. Fix:
  restrict the walk to the ISSUE-rooted success spine via a new `_reaches_issue`
  predicate (a stage is on-spine iff its transitive success-predecessor set
  contains ISSUE). PATCH has no success in-edge (`_get_predecessors("PATCH") ==
  []`), so it is off-spine and is skipped at both the seed and extend steps of
  the frontier. See Technical Approach step 1. A success criterion, Failure-Path
  test, and Test Impact case were added covering a backfill at a multi-predecessor
  stage (TEST) that leaves PATCH `pending` and `patch_cycle_count == 0`. Risk 1's
  mitigation was corrected to state that reaching a stage implies prior *spine*
  stages ran (not PATCH).
- **Two EXCLUDED findings** (per the round-2 critic) are intentionally NOT acted
  on: (1) "completed-path backfill is an uncaught crash" is factually wrong — the
  outer `try/except` at `tools/sdlc_stage_marker.py:207` already catches it and
  exits 1; (2) the concurrent-failure race is a pre-existing `_save()` property,
  out of scope for this bugfix.
