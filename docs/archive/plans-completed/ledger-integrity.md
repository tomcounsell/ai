---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2730
---

# ledger integrity: a dispatch record no stage can bypass

The issue-keyed `PipelineLedger`'s record of who ran what is written by the router alone, while stage markers are written by the stage itself. Any stage reached without passing through the router therefore leaves a marker and no ledger entry — issue #2711's ledger shows six completed stages against zero dispatch records. G4 counts those records, so a guard that looks armed has nothing to count.

**What ships here:** the record is made where every actor funnels through, so no path can bypass it (#2730's record-completeness half), plus the correction of one docstring that claimed a loop bound which cannot fire.

**What does not:** the G4 counter's blindness to alternating loops (split to #2801, with measurements showing the obvious fix does not fire on the loops it names) and #2731's dispatch liveness gate (closed as re-scoped — the lease cannot see the collision it was filed about). Both are explained in Round 3 Disposition.

**This is round 3.** Round 1 proposed three workstreams; two were withdrawn or re-scoped after a critique and three spikes. Round 2 was escalated to the lead rather than revised again, because the lane as fenced could not close either issue. The lead's ruling inverted the round-2 shape. **Read Round 3 Disposition first — it is what shipped; the round-1 and round-2 sections below are the reasoning that led there, not the current scope.**

## Round 3 Disposition (lead ruling — operative)

Round 2 ended NEEDS REVISION with the lane deadlocked: WS1 (the G4 counter change) only fires on frozen alternation, which is the territory of the record-completeness half the plan had deferred as fence-blocked, and WS2 (the #2731 liveness gate) was dead code wherever it could be placed. The lead resolved it by moving the fence rather than the scope:

| Item | Round-2 position | Round-3 ruling |
|---|---|---|
| #2730 record-completeness | DEFERRED — fence-blocked on `agent/pipeline_state.py` | **IN SCOPE, SHIPPED.** Fence extended to `agent/pipeline_state.py`. This is now the lane's headline change. |
| #2730(b) G4 counter change | IN SCOPE | **SPLIT OUT to #2801.** Measured: removing the skill break makes a frozen alternation escalate but leaves a real 8↔8b loop at 2, because `_patch_cycle_count` moves each cycle. Shipping it would claim a fix that does not fire on the named loops. #2801 carries both halves and the measurements. |
| The eleven "bound by G4" claims | IN SCOPE | **IN SCOPE, SHIPPED (row 8b only).** Audited all eleven; ten name same-skill loops G4 genuinely counts. Only `_rule_patch_applied_after_review` (row 8b) claimed a bound for an alternating loop, in two places. Both corrected to state the loop is unbounded and point at #2801. |
| #2731 liveness gate | IN SCOPE, NARROWED | **NOT BUILT — issue closed as re-scoped.** Under the lease-read-only ruling the inter-run gate is a no-op (`resolve_ledger_lease` already refuses every foreign holder), and the intra-run collision it was filed about cannot be gated by the lease at all. Analysis is on the issue. |

### What shipped for record-completeness

An **upsert slot**, not a dedup predicate. The router records an unconfirmed slot (`confirmed: false`) before invoking; the stage entry upgrades it in place. Where no slot exists, the stage was reached without the router and a confirmed record is appended. Two writers observing one dispatch would otherwise double every router-driven entry and halve G4's effective threshold, and a dedup predicate cannot distinguish "the router already recorded this" from a genuine second entry.

The record is applied inside `PipelineStateMachine._save()` **after** the preserved-metadata merge. `_save()` re-reads `_sdlc_dispatches` from the backing store and merges that copy over anything in memory, so a record written before the merge is silently discarded. `_activate_stage` is the single point every `start_stage` branch and every actor funnels through — including the PreToolUse hook, which never touches `write_marker` and is the dominant marker writer on the bridge. That is why a `write_marker`-only fix would have been a no-op exactly where the divergence was observed.

Only the **newest** record can be a given stage entry's slot. Scanning further back would let a stale slot — a dispatch recorded but never started — absorb an unrelated later entry, dropping that entry's record and leaving `last_dispatched_skill` naming the wrong skill, which is the defect this feature exists to fix. Bypass-path records inherit `pr_number` from the newest record carrying one, because `pr_number` is inside the snapshot G4 compares and a `None` beside a router record's real value would break the streak at every router/bypass boundary.

`stage` and `confirmed` ride on the record and never enter `stage_snapshot`. A per-dispatch-varying field there would stop every snapshot comparison matching and silently disable oscillation detection outright.

`last_dispatched_skill` **does** change, and that is the fix: after a `/do-build` → `/do-patch` chain it now reads `/do-patch` rather than `/do-build`. Rows 8b and 8d gate on exactly this value, so making it true is the point.

## Round 1 Disposition

Round 1 was **NEEDS REVISION** (4 blockers, all confirmed by the author with a shell). What survived, and what was then ruled:

| Round-1 item | Outcome |
|---|---|
| #2730 record-completeness (markers imply a dispatch entry) | **DEFERRED — fence-blocked.** spike-2 proved the fix must live in `PipelineStateMachine.start_stage()` (`agent/pipeline_state.py`), because the PreToolUse hook writes markers *without* going through `write_marker` and is the dominant writer on the bridge. That file is outside this lane's fence and the extension was not granted. See No-Gos. |
| #2730(b) counter defect | **IN SCOPE — lead ruled.** Round 1 withdrew it after spike-1 showed the motivating history was productive work. Ruled back in: #2730's headline claim is "defangs G4", and shipping while G4 scores alternation at ≤1 fails the issue's purpose. Re-shaped below so it cannot re-introduce the false positive spike-1 warned about. |
| #2731 gate | **IN SCOPE, NARROWED — owner ruled.** Issue-lease read only. No claim key, no record fields. Covers inter-run collisions; the intra-run shape is an explicit documented residual. #2731's body and ACs were rewritten to match (comment `5280704079`). |

## Problem

### #2730 — the counter cannot see the shape it is asked to detect

`compute_same_stage_count` (`agent/sdlc_router.py:1870-1881`) walks `_sdlc_dispatches` backward and counts entries sharing **both** the last entry's skill **and** its `stage_snapshot`. The skill test breaks the walk:

```python
for entry in reversed(history):
    ...
    if entry.get("skill") != skill:
        break                      # <- any alternation stops the walk here
```

An A→B→A→B loop therefore scores at most 1, however long it runs and however stuck it is. `MAX_SAME_STAGE_DISPATCHES = 3` is unreachable for every alternating loop — and the alternating loops are the ones that matter: rows 8↔8b (`/do-patch` ↔ `/do-pr-review`) and rows 2b↔3 (`/do-plan-critique` ↔ `/do-plan`).

`_rule_patch_applied_after_review`'s docstring (`:1275`) states "Loop-bound by G4 (`same_stage_dispatch_count`)" for exactly such a loop. That bound does not exist. PR #2790 extended that same docstring; this plan owns the correction.

**What is NOT the defect — and why the fix is shaped as it is (spike-1).** The `706fc4da0` history (four `/do-plan`↔`/do-plan-critique` rounds) reports `same_stage_dispatch_count: 0`, but that zero comes from the D5 self-clearing branch (`:1888-1894`), not the skill break, and those four rounds were **productive work**: each *cycle* carries a different recorded snapshot because the plan genuinely changed each round. G4 correctly declined to escalate. Any fix that makes that history escalate is a false positive that would wedge every healthy pipeline on this machine. The snapshot-equality conjunct is what separates spinning from progress, and it is retained untouched.

### #2731 — the gate authorizes on ownership, never on liveness

`_cli_record` (`tools/sdlc_dispatch.py`) resolves and re-validates the lease, then appends. Both checks ask *"do I own this?"*. Neither asks *"is somebody else still alive in it?"* A foreign live holder and a foreign dead holder are treated identically — and since #2714 anchored the lease heartbeat to its supervisor's lifetime, the lease can now answer that question honestly.

**Known residual, deliberate and documented.** This gate covers **inter-run** collisions only. Two agents inside one supervised run share `run_id` (forks inherit it — `agent/supervised_run.py`, #2026 WS1), lease ownership compares `run_id` alone, lease liveness is renewal recency tracking the *run* not the process, and under `/do-sdlc` both forks are subagents of one `claude` process sharing `CLAUDE_PID`, pid, create_time, worktree and `session_id`. Nothing the lease can see distinguishes them. **The #2629 collision that prompted the issue is not fixed here.** Its root cause is the run model — one `run_id` per pipeline, shared by every fork — which is #2026 territory and would need a self-minted claim token with its own TTL and release protocol. Shipping without saying so would leave the next reader believing the stage is guarded.

## Freshness Check

**Baseline:** `main` at `ad2ad58f8`. **Disposition:** Current.

Verified at baseline:
- `agent/sdlc_router.py:1856-1896` (`compute_same_stage_count`), `:1874` (the skill break), `:1883-1894` (D5), `:86` (`MAX_SAME_STAGE_DISPATCHES = 3`), `:98` (`MAX_DISPATCH_HISTORY = 10`), `:439-471` (`guard_g4_oscillation`), `:204-229` (`build_stage_snapshot`), `:1275` (the false G4 claim).
- `tools/sdlc_dispatch.py` — `_cli_record`'s resolve + revalidate, and the module-object lease access guarded by `tests/unit/test_sdlc_lease_helper_binding.py` (#2469/#2637).
- `models/session_lifecycle.py:886` (`IssueLockResult`, incl. `orphaned_lock`), `:962` (`_lock_renewal_is_fresh`), `:995` (`_lock_owner_is_live`), `:1122` (`touch_issue_lock`, `peek=True`).
- `tools/sdlc_stage_query.py:528-541` — calls `build_stage_snapshot` + `compute_same_stage_count`, passing `raw_states`. **Both callees are in-fence; this call site needs no edit,** which is what keeps the off-limits file untouched.

**Merged work this sits on:** PR #2784 (#2714, supervisor-anchored lease heartbeat — what makes the lease trustworthy for run liveness) and PR #2790 (#2740/#2767/#2769, from this agent — touched row 8b's docstring, corrected here).

## Prior Art

- **#1641 / #1668** — the two oscillation classes G4 was built for. Both are *same-skill* repeats, which is why the consecutive-run counter was adequate then and why the alternation gap went unnoticed.
- **#2026 WS1** — structural fork `run_id` inheritance. Working as designed, and the direct cause of #2731's intra-run residual.
- **#2620 / #2714** — moved lease liveness from pid inference to renewal recency, then anchored it to the supervisor. Together they are why a liveness read is trustworthy now and was not before.
- **#2012 task 2** — made the lease the sole authorization for a dispatch record. This plan adds the liveness question that authorization never asked.
- **#2658** — "gates that cannot fire." #2730 is a member; this closes one instance and does not open the general audit.
- **#2305 / `agent/pid_fence.py`** — *unknown never authorizes more force*, the rule the indeterminate branch follows.

## Why Previous Fixes Failed

| Prior fix | What it did | Why this stayed open |
|---|---|---|
| #1641/#1668 → G4 | Counter over consecutive same-skill dispatches | Closed the two same-skill loops it was shown. Later rows then *cited* G4 as their bound without checking it could count their shape. |
| #2012 task 2 | Lease-authorized the dispatch record | Settled *who may write*. Never asked whether someone else is *currently writing*. |
| #2714 | Anchored the lease heartbeat to the supervisor | Made lease liveness trustworthy — but nothing on the dispatch path reads it. |

**Pattern:** each fix made one signal authoritative and left its consumers assuming a stronger guarantee than it provides. Both defects are a claimed bound never checked against the shape it claims to bound.

## Technical Approach

### 1. #2730 — record the dispatch where every actor funnels through (`agent/pipeline_state.py`)

`PipelineStateMachine._activate_stage` is the single point every `start_stage` branch reaches, from `tools/sdlc_stage_marker.py` and from `agent/hooks/pre_tool_use.py` alike. The stage entry applies an upsert against `_sdlc_dispatches` there: upgrade the router's unconfirmed slot if the newest record is one for this stage, otherwise append a confirmed record.

The write lands inside `_save()` **after** the preserved-metadata merge, because `_save()` re-reads `_sdlc_dispatches` from the backing store and merges it over anything in memory. It rides the same single write, so there is no second read-modify-write to race against.

`stage_for_skill` inverts the canonical `STAGE_TO_SKILL` rather than adding a fourth hand-maintained stage↔skill map (the three that already exist are catalogued on #2491).

### 2. The false loop-bound claim (`agent/sdlc_router.py`)

Audit all eleven `grep -in "bound by G4"` hits. Ten name same-skill re-dispatch loops (rows 2c/8c/8d/8e/8f) where consecutive entries share a skill and, while the stage stalls, a snapshot — G4 counts those. `_rule_patch_applied_after_review` (row 8b) is the one exception and claims the bound twice; correct both to state the loop is unbounded and point at #2801. Phrase the correction without reusing the claim string, so a future repo-wide grep for it does not hit the note recording its removal.

## Risks

### Risk 1: Double-counting halves G4's threshold
**Impact:** two writers observing one dispatch double every router-driven entry, so `MAX_SAME_STAGE_DISPATCHES` is reached in half the turns and healthy pipelines escalate.
**Mitigation:** the upsert slot, not a dedup predicate. Pinned by tests asserting exact record **counts** per scenario, which a naive unconditional append fails.

### Risk 2: `last_dispatched_skill` shifts and rewrites routing
**Impact:** G1/G3/G7/row-8b/row-8d change behavior.
**Mitigation:** the shift is intended — `history[-1]["skill"]` now names the skill that actually ran. All five consumers plus the two `_latest_dispatch_at` rows were enumerated and their suites verified unchanged. Rows 8b and 8d gate on exactly this value, so making it true is the point.

### Risk 3: A stale slot absorbs an unrelated entry
**Impact:** a dispatch recorded but never started leaves an unconfirmed slot forever; letting a later entry upgrade it drops that entry's record and leaves `last_dispatched_skill` naming the wrong skill — the exact defect being fixed.
**Mitigation:** only the newest record is eligible. The router records immediately before invoking, so nothing can land in between. Pinned by a test.

### Risk 4: A per-dispatch-varying field reaches `stage_snapshot`
**Impact:** G4 compares snapshots; a field that varies per dispatch stops every comparison matching and silently disables oscillation detection outright — strictly worse than the missing records.
**Mitigation:** `stage` and `confirmed` ride on the record, never the snapshot. Pinned by a test on the snapshot's exact key set.

### Risk 5: Lane collision on `tools/sdlc_stage_query.py`
**Mitigation:** no edit required there by construction. Pinned by a Verification row.

## Race Conditions

### Race 1: `_save()`'s merge discards the record
**Location:** `PipelineStateMachine._save`
**Mitigation:** the record is applied after the preserved-metadata merge, so it sees the freshest history and rides the same write. Pinned by a test that the record survives the merge.

### Race 2: The dispatch record fails while the marker succeeds
**Impact:** the divergence this lane exists to close, reopened in the other direction.
**Mitigation:** a failing dispatch record never blocks the marker write, and is a required test. The marker remains the stronger signal; a missing record degrades G4's count rather than losing stage state.

## No-Gos (Out of Scope)

- **[SPLIT OUT — #2801] #2730's G4 counter half**, and the reconciliation of the remaining "bound by G4" claims with whatever #2801 concludes. **#2730 is only partially closed by this PR and the PR body says so.**
- **[SEPARATE-SLUG #2026]** The run model that makes intra-run forks indistinguishable.
- **[SEPARATE-SLUG #2658]** The general fireable-gate audit.
- **[SEPARATE-SLUG #2491]** The three stage↔skill maps (`pipeline_graph.STAGE_TO_SKILL`, `sdlc_stage_marker._SKIP_STAGE_SKILL`, `pre_tool_use._SKILL_TO_STAGE`) found by spike-2.
- **[SEPARATE-SLUG #2675]** Why #2711's and #2629's dispatch histories emptied.
- A general guard rework, a new `_meta` key, or any widening of `build_stage_snapshot`.

## Test Impact

- [x] `tests/unit/test_sdlc_router_oscillation.py` — ADD: exact record counts per scenario (router slot upgraded, bypass appended, re-entry preserved); the stale-slot case; the `pr_number` inheritance case; the `at` re-stamp.
- [x] `tests/unit/test_pipeline_state_machine.py` — ADD: the record survives `_save()`'s merge; a failing record does not block the marker write; `start_stage` on an already-`in_progress` stage records nothing.
- [x] `tests/unit/test_pre_tool_use_start_stage.py` — CHECK: the hook path now records.
- [x] `tests/unit/test_sdlc_stage_query.py` — CHECK: asserts on the count.
- [x] A test pinning `build_stage_snapshot`'s exact key set.

## Update System

No update-system changes — no new dependency, config file, Popoto model, or migration.

Per the repo's post-merge convention, run `/update` after this PR merges so running services pick up the new `PipelineStateMachine` behavior. The bridge's PreToolUse hook is the dominant marker writer, so a stale worker would keep writing markers without records — the exact divergence this closes.

## Agent Integration

No new entry points. The change is internal to an existing write path: every actor that already called `start_stage` now also records a dispatch. Integration points verified rather than added:

- [x] The PreToolUse hook path records without any skill-body change.
- [x] `last_dispatched_skill` reports the skill that actually ran, which rows 8b and 8d gate on.
- [x] A failed record surfaces without blocking the marker write, so no caller gains a new failure mode.

## Documentation

- [x] `docs/features/sdlc-router-oscillation-guard.md` — the upsert slot and what `last_dispatched_skill` now means.
- [x] Inline: `_activate_stage` / `_save` (why the record lands after the merge), the upsert-slot rationale, and row 8b's corrected loop-bound note.

## Success Criteria

- [x] A stage reached without the router records a dispatch entry (#2730 record-completeness).
- [x] A router-driven dispatch records exactly **one** entry, not two — the slot is upgraded in place.
- [x] A genuine re-entry into a stage still records a second entry, so the G4 signal survives.
- [x] `start_stage` on an already-`in_progress` stage records nothing; an unknown stage records nothing and does not raise.
- [x] Only the newest record can be a stage entry's slot — a stale slot cannot absorb a later entry.
- [x] Bypass-path records inherit `pr_number`, so a router/bypass boundary does not break a G4 streak.
- [x] The record survives `_save()`'s preserved-metadata merge.
- [x] A failing dispatch record never blocks the marker write.
- [x] `stage` and `confirmed` never enter `stage_snapshot`; the snapshot's key set is pinned by a test.
- [x] `stage_for_skill` inverts the canonical `STAGE_TO_SKILL` rather than adding a fourth hand-maintained map (#2491).
- [x] Row 8b's docstring no longer claims a loop bound that cannot fire; it names the unbounded loop and points at #2801.
- [x] The other ten "bound by G4" claims were each audited and name same-skill loops G4 genuinely counts.
- [x] `tools/sdlc_stage_query.py` is not modified.
- [x] The PR body states that #2730 is only partially closed, and why.

## Step by Step Tasks

### 1. Record the dispatch at the stage entry
- **Task ID**: build-record-completeness
- **Depends On**: none
- **Parallel**: false
- Upsert against `_sdlc_dispatches` from `_activate_stage`, applied inside `_save()` after the preserved-metadata merge.
- Only the newest record is an eligible slot; bypass records inherit `pr_number`; upgrading re-stamps `at`.
- Keep `stage`/`confirmed` off `stage_snapshot`; invert `STAGE_TO_SKILL` rather than adding a map.
- Tests per Test Impact, asserting exact record counts rather than presence.

### 2. Correct the false loop-bound claim
- **Task ID**: sweep-false-claims
- **Depends On**: none
- **Parallel**: true
- Audit all eleven `bound by G4` hits; correct row 8b's two, leaving the ten sound ones alone.

### 3. Documentation
- **Task ID**: document-lane
- **Depends On**: build-record-completeness, sweep-false-claims
- **Parallel**: false

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: document-lane
- **Parallel**: false
- Run the Verification table; confirm every Success Criterion; confirm the PR body carries the partial-closure note.

## Verification

| Check | Command | Expected |
|---|---|---|
| Router/oscillation tests | `scripts/pytest-clean.sh tests/unit/test_sdlc_router.py tests/unit/test_sdlc_router_oscillation.py tests/unit/test_sdlc_router_decision.py -q` | exit 0 |
| Dispatch tests | `scripts/pytest-clean.sh tests/unit/test_sdlc_dispatch.py tests/integration/test_sdlc_dispatch.py -q` | exit 0 |
| State machine + marker + hooks | `scripts/pytest-clean.sh tests/unit/test_pipeline_state_machine.py tests/unit/test_sdlc_stage_marker.py tests/unit/test_pre_tool_use_start_stage.py tests/unit/test_post_tool_use_stage_completion.py -q` | exit 0 |
| Stage-query + lease binding | `scripts/pytest-clean.sh tests/unit/test_sdlc_stage_query.py tests/unit/test_sdlc_lease_helper_binding.py -q` | exit 0 |
| Lint / format | `python -m ruff check . && python -m ruff format --check .` | exit 0 |
| Off-limits file untouched | `git diff --name-only main...HEAD -- tools/sdlc_stage_query.py` | empty |
| Snapshot not widened | `scripts/pytest-clean.sh -k build_stage_snapshot -q` | exit 0 |
| Row 8b claim corrected | `grep -in "bound by G4" agent/sdlc_router.py` | ten hits, none in `_rule_patch_applied_after_review` |

## Open Questions

None. Round 1's questions were closed by ruling; round 2's fence/scope escalation was closed by the lead's round-3 ruling recorded above.

## Critique Results

### Round 1 — NEEDS REVISION

War room 2026-08-13, FULL depth. **4 blockers**, all re-verified against source by the author with a shell. Full detail lives in the round-1 history; the surviving dispositions are tabulated in "Round 1 Disposition" above. Summary of the four:

1. **The #2730(b) evidence did not show the defect it claimed.** The backward walk returns `1`; the reported `0` came from D5 self-clearing. Adjacent plan/critique pairs share a snapshot while each cycle differs — the state advanced every round, so G4 correctly declined. *Disposition: the defect is real but the evidence was wrong; the fix is re-shaped to preserve the productive case (Risk 1).*
2. **The dedup identity `(run_id, stage, occupancy)` was unimplementable** — `run_id` is shared by fork inheritance, `occupancy` does not exist, and the marker path writes via `PipelineStateMachine._save()` rather than `update_stage_states`, so the stated atomicity mitigation was impossible. *Disposition: workstream deferred (fence-blocked).*
3. **`_sdlc_dispatches` has at least seven consumers, not the two named** — `last_dispatched_skill` drives G1/G3/G7/row-8b/row-8d; `_latest_dispatch_at` drives rows 8 and 2b. *Disposition: now Risk 2, with a dedicated pin; the chosen fix leaves the returned skill bit-identical.*
4. **The #2731 Option B recommendation was blind to its target** — `CLAUDE_PID` names the top-level process even from a subagent, and `fence_is_live` returns `False` for unknown, which would have *admitted* the second agent. *Disposition: owner ruled lease-read-only; residual documented.*

Also fixed from the concern list: eleven "bound by G4" claims (case-insensitive), not six; `tests/integration/test_sdlc_dispatch.py` added to Test Impact; Risk 6's speculative arithmetic dropped with the deferred workstream; the stub tasks given content; Q1 closed by ruling.

### Round 2 — NEEDS REVISION

War room 2026-08-13 (round 2). **4 blockers.** The two decisive ones were
re-verified by the author by execution.

**B1 (confirmed by execution) — WS1 does not fire on the loops it names.**
`build_stage_snapshot` includes `_patch_cycle_count` and `_critique_cycle_count`
(`agent/sdlc_router.py:226-227`), and those move on exactly the transitions
inside the cited loops: `complete_stage("PATCH")` → `patch_cycle_count += 1`
(`agent/pipeline_state.py:979`), `fail_stage("CRITIQUE")` →
`critique_cycle_count += 1` (`:1020`). So consecutive dispatch snapshots in a
real 8↔8b or 2b↔3 loop differ, and the walk still breaks at the snapshot test.
Measured with the proposed one-line change applied:

| fixture | count | escalates at `MAX_SAME_STAGE_DISPATCHES = 3`? |
|---|---|---|
| frozen alternation (no snapshot field moves) | **4** | yes |
| real 8↔8b loop (`_patch_cycle_count` moves each cycle: 1,1,2,2,3,3) | **2** | **no** |

The trade-off is the plan's own core and it was never stated: *the snapshot
conjunct that protects the productive history (Risk 1) is the same thing that
defeats detection of the stuck version of that loop.* The residual set WS1
actually catches — alternation where **no** snapshot field moves — is precisely
the state that arises when markers/verdicts/counters never land, i.e. **the
record-completeness half this plan defers as fence-blocked.** WS1 is gated on
the deferred half.

**B2 (confirmed) — WS2 is dead code where the plan puts it.**
`resolve_ledger_lease` returns `ISSUE_LOCKED` for *any* foreign holder
(`tools/_sdlc_utils.py:744-749`) and `_cli_record` returns immediately
(`tools/sdlc_dispatch.py:204-210`). A gate "before the append" therefore can
never observe a foreign holder. Placing it *before* `resolve_ledger_lease` makes
it reachable but regresses #2144: `reestablish_run_id` adopts a **live**
supervisor's `run_id` (`tools/_sdlc_run_identity.py:162-164`), so "foreign +
live → refuse" converts the sanctioned self-heal into a hard refusal. The new
reason would also miss the heal tuple (`:378`) and the loud/exit tuple
(`:394-403`), so it would exit **0** despite the plan promising non-zero.

**B3 — row 3 of the liveness table is not implementable.** `orphaned_lock` means
the lock is still *held* by the foreign `run_id`, merely judged dead
(`models/session_lifecycle.py:1249`). Appending then requires passing
`revalidate_ledger_lease`, which refuses on a foreign payload — so the append is
only possible by skipping the revalidate (which Race 1 promises to keep) or by a
lease-takeover primitive that exists nowhere (`tools/sdlc_session_ensure.py`
computes `orphaned_lock` and still refuses, leaving TTL expiry as the only
recovery).

**B4 — an existing test inverts.**
`tests/unit/test_sdlc_router_oscillation.py:313-320`
(`test_compute_same_stage_count_resets_on_skill_change`) records three
dispatches on `states = {}`, so all three snapshots are byte-identical: today
`count == 1`, after the change `count == 3`. Success Criterion "every
#1641/#1668 same-skill case passes unchanged" is false as written.

**Confirmed sound by the critic:** the returned-skill invariant (Risk 2) holds by
inspection — `skill` is read at `:1863` before the loop and both return sites use
it, so `last_dispatched_skill` is bit-identical and the five routing consumers
are unaffected. `tools/sdlc_stage_query.py` genuinely needs no edit. The
intra-run residual and the fence-blocked deferral are honestly written.

**Disposition: the lane as fenced cannot meaningfully close either issue.** WS1
is a strict improvement but only for frozen alternation, which is the deferred
half's territory; WS2 is dead code, a #2144 regression, or needs an unscoped
primitive. Escalated to the lead for a fence/scope decision rather than revised
a third time.

### Round 3 — APPROVED (by lead ruling)

Round 2 was escalated rather than revised, so round 3 is a **lead ruling on the
fence and scope**, not a fresh war room. The ruling and its consequences are
tabulated in "Round 3 Disposition" at the top of this document.

The ruling resolves all four round-2 blockers by inverting what the lane builds:

- **B1** (WS1 fires only on frozen alternation, which is the deferred half's
  territory) — resolved by shipping the deferred half instead and splitting the
  counter change to #2801. B1's own logic is the argument for the split: the
  measurement stands, so shipping the counter change would claim a fix that does
  not fire on the loops it names.
- **B2 / B3** (WS2 is dead code, a #2144 regression, or needs an unscoped
  lease-takeover primitive) — resolved by not building it. #2731 is closed as
  re-scoped, with the analysis on the issue.
- **B4** (an existing oscillation test inverts under the counter change) — moot;
  the counter change is not in this PR, so the test is untouched.

The one round-2 item that survives into this PR is the false-claims sweep, and
it was narrowed by audit: ten of the eleven claims are sound, so only row 8b is
corrected.

**Fence extension granted:** `agent/pipeline_state.py`, which spike-2 identified
as the only location where the record-completeness fix is not a no-op on the
bridge.
