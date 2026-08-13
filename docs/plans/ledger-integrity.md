---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2730
---

# ledger integrity: a G4 counter that can see alternation, and an inter-run liveness gate on dispatch

Two issues, one lane, one PR. Both are failures of the same organ — the issue-keyed `PipelineLedger`'s record of who ran what — and both surface as a guard that looks armed and is not.

- **#2730** — G4 scores an alternating oscillation at 1 no matter how long it runs, because `compute_same_stage_count` breaks its walk on the first differing skill.
- **#2731** — `dispatch record` authorizes on lease *ownership* and never consults lease *liveness*, so it cannot refuse a dispatch whose foreign holder is still alive.

**This is round 2.** Round 1 proposed three workstreams; two were withdrawn or re-scoped after a critique and three spikes. Read Round 1 Disposition before assuming any part of this is new.

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

### 1. #2730 — let the walk cross skill boundaries (`agent/sdlc_router.py`)

Delete the skill-identity break at `:1874`. The snapshot-equality test at `:1879` remains the sole streak terminator and continues to do all the "progress or spinning?" work.

Why that is the whole fix:
- An A→B→A→B loop over an unchanged snapshot now counts every entry and reaches `MAX_SAME_STAGE_DISPATCHES`.
- A productive alternation still scores 1 per snapshot, because the snapshot advances — the spike-1 history stays at rest and does **not** escalate.
- Same-skill loops (rows 2c/8c/8d/8e/8f) count exactly as today; the removed test was redundant for them, since identical snapshots on consecutive same-skill entries already implied it.
- **The returned `skill` is unchanged.** It is `history[-1]["skill"]`, read before the loop (`:1863`), so `last_dispatched_skill` — which drives G1 (`:336`), G3 (`:403`), G7 (`:685`), row 8b (`:1284`) and row 8d (`:1380`) — is bit-identical. This is what keeps the change from rewriting routing, and it is a blocking test obligation, not an assumption.

`guard_g4_oscillation`'s message (`:460`) reads "{skill} dispatched {count} times", wrong wording once a streak can span skills. Reword for a repeating cycle **without** adding a `_meta` key — the guard already receives `stage_states` and can name the streak's skills locally.

Then correct `_rule_patch_applied_after_review` (`:1275`) and audit the remaining claims (`grep -in "bound by G4"`, eleven hits) so each names a bound that can fire.

### 2. #2731 — consult lease liveness before appending (`tools/sdlc_dispatch.py`)

In `_cli_record`, before the append, peek the issue lease. If held by a **different** `run_id`, decide on liveness:

| lease state | decision |
|---|---|
| no holder, or holder is us | append (unchanged) |
| foreign holder, provably live | **refuse** — named reason, non-zero exit, holder identified |
| foreign holder, provably dead (`orphaned_lock`) | append — the #2629 recovery case |
| foreign holder, liveness indeterminate | **refuse** — unknown never authorizes entry |

The indeterminate branch is deliberate and consistent: `_lock_owner_is_live` already fails *toward* live, so an unverifiable holder reads as live and refusing is the matching outcome.

**Constraints:** no new fields on the dispatch record; `build_stage_snapshot` untouched; lease helpers reached through the module object, never a `from`-import (#2469/#2637).

Note this largely *hardens* what `resolve_ledger_lease` already does — a foreign live holder mostly fails ownership today. The new value is the explicit, named, tested refusal plus correct handling of the dead-holder and indeterminate cases.

## Risks

### Risk 1: The counter change makes G4 fire on productive work
**Impact:** the worst outcome available here — every healthy pipeline escalates to a human every few turns.
**Mitigation:** the snapshot-equality conjunct is retained *unchanged* and is the only streak terminator. The spike-1 history (`706fc4da0`, four productive rounds) is a required non-escalation fixture. Demonstrated-red in both directions: a stuck alternating loop must escalate **and** the productive one must stay at rest.

### Risk 2: `last_dispatched_skill` shifts and rewrites routing
**Impact:** G1/G3/G7/row-8b/row-8d change behavior silently.
**Mitigation:** the returned skill is read before the loop and untouched. Pinned by a test asserting the second return value equals `history[-1]["skill"]` across every fixture, including alternating ones.

### Risk 3: The #2731 refusal blocks legitimate recovery
**Impact:** a crashed run's issue becomes undispatchable — worse than the collision it prevents.
**Mitigation:** the dead-holder branch is exactly #2629's recovery case and is a required test. The lease TTL remains the backstop, unchanged.

### Risk 4: The intra-run residual is mistaken for coverage
**Impact:** someone reads "dispatch has a liveness gate" and assumes two BUILD agents cannot collide. They can.
**Mitigation:** an acceptance criterion — the residual and its #2026 root cause are documented in the shipped feature docs, not only here.

### Risk 5: Lane collision on `tools/sdlc_stage_query.py`
**Mitigation:** no edit required there by construction (the G4 change sits behind an unchanged signature). Pinned by a Verification row.

## Race Conditions

### Race 1: The lease is taken between the liveness peek and the append
**Location:** `tools/sdlc_dispatch._cli_record`
**Mitigation:** unchanged and already correct — `revalidate_ledger_lease` runs immediately before the write. The new peek is an *additional* refusal, never a replacement, and must be ordered so a foreign takeover between peek and write still fails the revalidate.

### Race 2: The foreign holder dies between the peek and the refusal
**Impact:** a legitimate recovery is refused once.
**Mitigation:** accepted and bounded — the refusal is non-destructive and the next attempt sees a dead holder. The message says so, so the operator retries rather than escalates.

## No-Gos (Out of Scope)

- **[FENCE-BLOCKED] #2730's record-completeness half.** spike-2 established the fix must live in `PipelineStateMachine.start_stage()` (`agent/pipeline_state.py:870`) — its only two production callers are `tools/sdlc_stage_marker.py:576` and `agent/hooks/pre_tool_use.py:334`, and the hook path bypasses `write_marker` entirely, so a `write_marker`-only fix is a no-op on the bridge. Outside this lane's fence. **#2730 is only partially closed by this PR and the PR body must say so.**
- **[SEPARATE-SLUG #2026]** The run model that makes intra-run forks indistinguishable.
- **[SEPARATE-SLUG #2658]** The general fireable-gate audit.
- **[SEPARATE-SLUG #2491]** The three stage↔skill maps (`pipeline_graph.STAGE_TO_SKILL`, `sdlc_stage_marker._SKIP_STAGE_SKILL`, `pre_tool_use._SKILL_TO_STAGE`) found by spike-2.
- **[SEPARATE-SLUG #2675]** Why #2711's and #2629's dispatch histories emptied.
- A general guard rework, a new `_meta` key, or any widening of `build_stage_snapshot`.

## Test Impact

- [ ] `tests/unit/test_sdlc_router_oscillation.py` — ADD: an alternating loop over an unchanged snapshot escalates; the spike-1 productive history does **not**; the returned skill is `history[-1]["skill"]` in both. KEEP every #1641/#1668 same-skill case green.
- [ ] `tests/unit/test_sdlc_dispatch.py` + `tests/integration/test_sdlc_dispatch.py` — ADD the four-row liveness table. The integration file pins `same_stage_dispatch_count` at `:207`/`:295`; verify both.
- [ ] `tests/unit/test_sdlc_stage_query.py:601` — CHECK: asserts on the count.
- [ ] `tests/unit/test_sdlc_lease_helper_binding.py` — CHECK: no new `from`-import of lease helpers.
- [ ] A test pinning `build_stage_snapshot`'s exact key set.

## Update System

No update-system changes. Both edits are internal to `agent/sdlc_router.py` and `tools/sdlc_dispatch.py` — no new dependency, config file, Popoto model, or migration. The one skill-body edit (the loser-side stand-down protocol) lands in an existing `.claude/skills-global/` file already registered for hardlink sync by `scripts/update/hardlinks.py`, so it propagates on each machine's next `/update` with no new wiring.

Per the repo's post-merge convention, run `/update` after this PR merges so running services pick up the new router and `sdlc-tool` behavior — the G4 counter change affects live dispatch decisions, so a stale worker would keep scoring alternation at 1.

## Agent Integration

No new entry points. `sdlc-tool dispatch` and the router are both existing surfaces; this changes a counting rule and adds a refusal on one of them. Three integration points to verify rather than add:

- [ ] `sdlc-tool dispatch record` exits non-zero with the named reason on a live foreign holder, and the reason reaches stderr where the `/do-sdlc` supervisor and the `/sdlc` router both already read refusal reasons.
- [ ] The refusal reason is distinguishable from the existing `ISSUE_LOCKED` / `LEASE_ABSENT` / `TARGET_REPO_MISSING` set, so a supervisor can tell "someone is live in this stage" from "you do not own this issue".
- [ ] A G4 escalation triggered by an alternating streak renders a `Blocked` a supervisor can act on — the reason names the cycle rather than a single skill.

## Documentation

- [ ] `docs/features/sdlc-router-oscillation-guard.md` — what the counter now counts; the corrected per-row bound claims.
- [ ] `docs/features/sdlc-issue-ownership-lock.md` — the liveness gate **and the intra-run residual with its #2026 pointer** (Risk 4).
- [ ] `docs/tools-reference.md` — the new refusal reason.
- [ ] Inline: `compute_same_stage_count` (why the walk crosses skills; the snapshot conjunct is the sole terminator), `guard_g4_oscillation`'s message, `:1275`, and the other "bound by G4" claims.
- [ ] The loser-side stand-down protocol in the skill body that owns it.

## Success Criteria

- [ ] An alternating loop over an unchanged snapshot escalates via G4 (#2730).
- [ ] The spike-1 productive history does **not** escalate (Risk 1).
- [ ] Every #1641/#1668 same-skill case passes unchanged.
- [ ] `compute_same_stage_count`'s returned skill is unchanged across all fixtures (Risk 2).
- [ ] Every "bound by G4" docstring names a bound that can fire.
- [ ] A dispatch against a live foreign lease holder is refused with a named reason (#2731).
- [ ] A dispatch against a dead foreign holder succeeds (#2629 recovery).
- [ ] Indeterminate liveness refuses.
- [ ] The dispatch record gains no fields; `build_stage_snapshot` is unchanged.
- [ ] `tools/sdlc_stage_query.py` is not modified.
- [ ] The intra-run residual is documented in shipped docs (Risk 4).
- [ ] The PR body states that #2730's record-completeness half is deferred, and why.

## Step by Step Tasks

### 1. Let the G4 walk cross skill boundaries
- **Task ID**: build-g4-crossing
- **Depends On**: none
- **Parallel**: true
- Remove the skill break at `:1874`; retain the snapshot conjunct untouched.
- Reword `guard_g4_oscillation`'s message for a multi-skill streak, no new `_meta` key.
- Correct `:1275`; audit all eleven `grep -in "bound by G4"` hits.
- Tests per Test Impact, including both demonstrated-red directions and the returned-skill pin.

### 2. Consult lease liveness in `dispatch record`
- **Task ID**: build-liveness-gate
- **Depends On**: none
- **Parallel**: true
- Implement the four-row table; order the peek so `revalidate_ledger_lease` still closes the TOCTOU (Race 1).
- Lease helpers via the module object only (#2469/#2637).
- Named reason, non-zero exit, holder identified, retry guidance for Race 2.

### 3. Documentation
- **Task ID**: document-lane
- **Depends On**: build-g4-crossing, build-liveness-gate
- **Parallel**: false

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: document-lane
- **Parallel**: false
- Run the Verification table; confirm every Success Criterion; confirm the PR body carries the deferral note.

## Verification

| Check | Command | Expected |
|---|---|---|
| Router/oscillation tests | `scripts/pytest-clean.sh tests/unit/test_sdlc_router.py tests/unit/test_sdlc_router_oscillation.py tests/unit/test_sdlc_router_decision.py -q` | exit 0 |
| Dispatch tests | `scripts/pytest-clean.sh tests/unit/test_sdlc_dispatch.py tests/integration/test_sdlc_dispatch.py -q` | exit 0 |
| Stage-query + lease binding | `scripts/pytest-clean.sh tests/unit/test_sdlc_stage_query.py tests/unit/test_sdlc_lease_helper_binding.py -q` | exit 0 |
| Lint / format | `python -m ruff check . && python -m ruff format --check .` | exit 0 |
| Off-limits file untouched | `git diff --name-only main...HEAD -- tools/sdlc_stage_query.py` | empty |
| Snapshot not widened | `scripts/pytest-clean.sh -k build_stage_snapshot -q` | exit 0 |
| Every G4 claim reviewed | `grep -in "bound by G4" agent/sdlc_router.py` | eleven hits, each reviewed in the PR body |

## Open Questions

None. Both round-1 questions were resolved by ruling (#2731 scope; #2730 counter in scope); the record-completeness fence question is recorded as a No-Go rather than left open.

## Critique Results

### Round 1 — NEEDS REVISION

War room 2026-08-13, FULL depth. **4 blockers**, all re-verified against source by the author with a shell. Full detail lives in the round-1 history; the surviving dispositions are tabulated in "Round 1 Disposition" above. Summary of the four:

1. **The #2730(b) evidence did not show the defect it claimed.** The backward walk returns `1`; the reported `0` came from D5 self-clearing. Adjacent plan/critique pairs share a snapshot while each cycle differs — the state advanced every round, so G4 correctly declined. *Disposition: the defect is real but the evidence was wrong; the fix is re-shaped to preserve the productive case (Risk 1).*
2. **The dedup identity `(run_id, stage, occupancy)` was unimplementable** — `run_id` is shared by fork inheritance, `occupancy` does not exist, and the marker path writes via `PipelineStateMachine._save()` rather than `update_stage_states`, so the stated atomicity mitigation was impossible. *Disposition: workstream deferred (fence-blocked).*
3. **`_sdlc_dispatches` has at least seven consumers, not the two named** — `last_dispatched_skill` drives G1/G3/G7/row-8b/row-8d; `_latest_dispatch_at` drives rows 8 and 2b. *Disposition: now Risk 2, with a dedicated pin; the chosen fix leaves the returned skill bit-identical.*
4. **The #2731 Option B recommendation was blind to its target** — `CLAUDE_PID` names the top-level process even from a subagent, and `fence_is_live` returns `False` for unknown, which would have *admitted* the second agent. *Disposition: owner ruled lease-read-only; residual documented.*

Also fixed from the concern list: eleven "bound by G4" claims (case-insensitive), not six; `tests/integration/test_sdlc_dispatch.py` added to Test Impact; Risk 6's speculative arithmetic dropped with the deferred workstream; the stub tasks given content; Q1 closed by ruling.
