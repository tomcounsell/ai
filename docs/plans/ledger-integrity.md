---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2730
---

# ledger integrity: a dispatch record no path can bypass, an oscillation signal that can actually fire, and a stage-entry gate that refuses a live peer

Two issues, one lane. Both are failures of the same organ — the issue-keyed `PipelineLedger`'s record of *who ran what, when* — and both surface as a guard that looks armed and is not.

- **#2730** — stage skills write markers; only the router records dispatches. Any stage that runs without passing through the router leaves a marker and no ledger entry, so G4 has nothing to count.
- **#2731** — `record_dispatch` carries no liveness signal, so nothing at the tool layer can refuse a second agent entering a stage that already has a live one.

They ship together because #2731's gate has to read the same record #2730 makes trustworthy. Gating on a record that half the paths never write would produce a gate that silently never fires — the exact failure class this lane exists to close.

## Problem

### #2730 — the dispatch history is not a record of what happened

On issue #2711 the ledger asserts **six completed stages** and holds **zero dispatch entries**:

```
markers:    ISSUE completed, PLAN completed, CRITIQUE completed,
            BUILD completed, TEST completed, REVIEW completed, DOCS ready
dispatches: []
same_stage_dispatch_count: 0
```

(The issue reported one `/do-patch` entry when filed; the history has since emptied while every marker survived. Dispatch history is the more fragile of the two records — noted, not diagnosed, see Open Questions.)

**Mechanism.** `sdlc-tool dispatch record`'s contract is caller-side: "Call AFTER guard evaluation but BEFORE invoking the sub-skill." Only two files call it — `.claude/skills/sdlc/SKILL.md` (the router) and `.claude/skills-global/do-sdlc/SKILL.md` (the supervisor). **No stage skill records a dispatch.** Stage markers are the mirror image: eight skill and doc files write those directly.

That asymmetry is the defect. Stage completion is self-reported by the stage; dispatch is recorded only by the router. A stage that executes without the router therefore writes one and not the other. A concrete non-router path is in the repo today: `.claude/skills-global/do-build/SKILL.md:128` tells `/do-build` to route failures to `/do-patch` and re-run — a direct skill-to-skill chain that never reaches the router's record step.

### #2730(b) — G4 cannot count the oscillation shape that actually happens

**This mechanism is not in the issue and is not fixed by making the record complete.** It was found while verifying #2730 and it is the more severe half.

`compute_same_stage_count` (`agent/sdlc_router.py:1871-1880`) walks the history backward and **breaks on the first entry whose skill differs**. It only ever counts a consecutive same-skill run, so any A→B→A→B alternation scores at most 1.

Evidence from the `verdict-finalize-cluster` lane (merged as `706fc4da0`), whose dispatch history is complete and correct:

```
/do-plan → /do-plan-critique → /do-plan → /do-plan-critique →
/do-plan → /do-plan-critique → /do-plan → /do-plan-critique → /do-build
same_stage_dispatch_count: 0
```

A textbook four-round plan↔critique oscillation, perfectly recorded, counted as **zero**.

The dominant oscillation shapes in this router alternate:

| loop | rows | skills | G4 can count it? |
|---|---|---|---|
| plan ↔ critique | 2b ↔ 3 | `/do-plan-critique`, `/do-plan` | **no** — correctly bounded by G5 instead |
| review ↔ patch | 8 ↔ 8b | `/do-patch`, `/do-pr-review` | **no** — and row 8b's docstring claims G4 bounds it |
| stalled critique retry | 2c | `/do-plan-critique` ×N | yes |
| stalled/crashed review retry | 8c, 8d, 8e, 8f | `/do-pr-review` ×N | yes |

`_rule_patch_applied_after_review`'s docstring (`agent/sdlc_router.py:1275`) states "Loop-bound by G4 (`same_stage_dispatch_count`)" for a loop that provably alternates. That is a gate that cannot fire (#2658's family), and the `706fc4da0` lane extended that same claim — this plan owns the correction.

Shipping #2730 as scoped would complete the record and leave this untouched, producing a guard that *looks* fixed. That is worse than the honest breakage we have.

### #2731 — no liveness gate on stage entry

Two concurrent `/do-build` agents ran on issue #2629's BUILD stage, same slug, same worktree. Both produced byte-identical code, so the collision cost only compute; the same collision on divergent implementations corrupts the branch.

Nothing at the tool layer can refuse it. `record_dispatch` appends `{skill, at, stage_snapshot}` and nothing else — no liveness signal exists to test. `same_stage_dispatch_count` detects repetition *over time* and is blind to overlap *in time*.

A naive counter threshold is wrong: #2629's first dispatch had died mid-work and was legitimately recovered, so "refuse if count >= 1" blocks the common correct path. The refusal needs a real liveness test.

**The stated design premise does not hold, and this is the lane's one genuine open decision.** The owner's constraint is that liveness belongs to the lease, not to the dispatch record. Three facts from source say a read of the *issue* lease cannot express this gate:

1. `agent/supervised_run.py:4-8` — a stage fork **inherits the supervisor's `run_id`** rather than contesting the lock, and a bare `session-ensure` under a live signal is refused (`SUPERVISED_RUN_ACTIVE`) specifically "so no fork can mint a competing `run_id`."
2. `models/session_lifecycle.py:1140-1147` — lease ownership is decided **solely** by comparing `run_id`.
3. `_lock_renewal_is_fresh` (`:962-993`) — the lease's liveness signal is `renewed_at` recency, which its own docstring says tracks "the RUN's life rather than one ephemeral process's." The payload `pid` belongs to the ephemeral `session-ensure` CLI and is dead within seconds of a locally-minted lease.

The lease is **run-scoped by construction**. #2629's collision was two agents inside one supervised run, so both carry the same `run_id` and any lease read reports "alive, and it is you" for both. A lease read can refuse an *inter-run* collision — which `resolve_ledger_lease` already largely does — and is structurally blind to the shape #2731 was filed about.

Note also that #2731's own sketch proposes a `dispatcher: {holder_id, pid, pid_create_time}` block **on the dispatch record**, which the owner's constraint forbids. The issue and the constraint are in direct conflict, and the issue's acceptance criteria are written against the record-block design. See Open Questions Q1 — this plan does not proceed on the gate until that is settled.

## Freshness Check

**Baseline commit:** `87ecf3f36` (main)
**Issues filed at:** #2730 2026-08-13 · #2731 2026-08-13
**Disposition:** Current

**File:line references verified at baseline:**
- `tools/sdlc_dispatch.py` — `record` is the only writer; contract stated in the `record` subparser help — holds.
- `agent/sdlc_router.py:1835-1895` (`compute_same_stage_count`) — the same-skill break at `:1874` holds.
- `agent/sdlc_router.py:204-230` (`build_stage_snapshot`) — excludes `_sdlc_dispatches` and timestamps; holds.
- `agent/sdlc_router.py:439-471` (`guard_g4_oscillation`), `:86` (`MAX_SAME_STAGE_DISPATCHES = 3`) — hold.
- `agent/pipeline_graph.py:62-72` (`STAGE_TO_SKILL`) — canonical 1:1 stage→skill map, already imported by the router at `:41`.
- `tools/sdlc_stage_query.py:528-541` — calls `build_stage_snapshot` + `compute_same_stage_count` and passes `raw_states`. **Both callees are in this lane's fence; the call site needs no edit.** This is what keeps the off-limits file off-limits.
- `models/session_lifecycle.py:886-960` (`IssueLockResult`, `_lock_renewal_is_fresh`), `:995` (`_lock_owner_is_live`), `:1122` (`touch_issue_lock`) — hold, read-only for this lane.
- `agent/pid_fence.py:87-154` (`proc_create_time`, `create_times_match`, `fence_is_live`) — the existing liveness primitive.

**Merged work this plan sits on:**
- **PR #2784 (#2714)** — anchored the lease heartbeat to its supervisor's lifetime, merged 2026-08-13T12:13:49Z. This is what makes the lease a trustworthy *run*-liveness signal, and is the basis of the owner's constraint. It does not make the lease agent-scoped; see #2731 above.
- **PR #2790 (#2740/#2767/#2769)** — merged 2026-08-13T12:18:56Z from this same agent. Touched `agent/sdlc_router.py` (row 8b, `NO_RULE`) and `tools/sdlc_stage_marker.py` (four refusal messages). This lane rebases on it and corrects row 8b's G4 claim, which that PR extended.

**Active plans overlapping this area:** `docs/plans/simulated-bridge-dispatch-harness.md` mentions dispatch but targets a bridge test harness, not the ledger. No file-level collision found. `tools/sdlc_stage_query.py` is under active work by the lane-identity lane — this plan deliberately requires no edit there (see the boundary note above), which must be re-verified at BUILD start.

## Prior Art

- **#2012 task 2** — re-pointed `dispatch record` at the issue-keyed `PipelineLedger`, making the run_id-keyed lease the sole authorization. Established the writer shape this plan extends.
- **#1641 / #1668** — the two oscillation classes G4 and rows 2c/8c were built for. Both are *same-skill* repeats, which is why G4's consecutive-run counter was adequate for them and why the alternation gap went unnoticed.
- **#2026 WS1 (`agent/supervised_run.py`)** — made fork `run_id` inheritance structural. This is the direct cause of #2731's intra-run blindness: it is working as designed, and the design is what makes the lease unable to discriminate two forks.
- **#2620 / #2714** — moved lease liveness from pid inference to renewal recency, then anchored renewal to the supervisor. Together they are why the lease is trustworthy for *run* liveness and silent on *agent* liveness.
- **#2650** — the plan-doc write lease. The nearest precedent for a scoped claim key with a pid fence, and the model for Option B below. #2731 was split out of it as the piece deliberately left out.
- **#2658** — "gates that cannot fire: require demonstrated-red for verification rows, guards, and skill self-checks." #2730(b) is a member of that family, found the way that issue predicts they get found.
- **#2305 defect 1 / `agent/pid_fence.py`** — the canonical rule this plan inherits: *unknown never authorizes more force*. An unverifiable liveness answer must not license a second agent in.

## Why Previous Fixes Failed

| Prior fix | What it did | Why it left this open |
|---|---|---|
| #2012 task 2 | Made the dispatch record ledger-keyed and lease-authorized | Fixed *where* the record is written and *who* may write it. Never revisited *which actors are obliged to write it* — the caller-side contract stayed router-only. |
| #1641 / #1668 → G4 | Counter over consecutive same-skill dispatches | Closed the two same-skill loops it was shown. The alternating loops were never in evidence, so the counter's shape was never questioned; later rows then *cited* G4 as their bound without checking it could count them. |
| #2026 WS1 | Structural fork `run_id` inheritance | Solved lock contention between supervisor and forks by making them one owner. That is precisely what removes the tool layer's ability to tell two forks apart. |
| #2650 | Plan-doc write lease | Scoped to `docs/plans/*.md`. Two BUILD agents write source in a worktree and never touch it — stated in #2731 itself. |

**Root-cause pattern:** each fix made one record or one lock authoritative for the actor it had in view, and none asked *"can every actor that changes this state reach this record, and can the signal built on it actually distinguish the cases it claims to?"* That question is this plan's through-line.

## Data Flow

The write path both defects sit on:

1. `/sdlc` (router) evaluates guards, selects a target, calls `sdlc-tool dispatch record --skill S` → appends `{skill, at, stage_snapshot, run_id}` to `_sdlc_dispatches`.
   - *#2731 has no gate here.* Nothing tests whether a live agent already occupies the stage.
2. The router invokes the sub-skill.
3. The sub-skill writes its own stage marker via `sdlc-tool stage-marker` → `PipelineStateMachine` → `stage_states`.
   - *#2730 originates in the gap between 1 and 3:* a skill reached any way other than step 2 performs step 3 and never step 1.
4. `tools/sdlc_stage_query._compute_meta` reads `_sdlc_dispatches`, calls `build_stage_snapshot` + `compute_same_stage_count`, publishes `same_stage_dispatch_count` + `last_dispatched_skill` into `_meta`.
5. `guard_g4_oscillation` reads `_meta["same_stage_dispatch_count"]` and escalates at `MAX_SAME_STAGE_DISPATCHES`.
   - *#2730(b) manifests here:* the counter is structurally incapable of seeing an alternating loop, however complete the history is.

**Stated plainly:** #2730 starves the signal, #2730(b) makes the signal unable to see the common case even when well fed, and #2731 is a gate that has no signal at all.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** additive only, and deliberately confined to this lane's fence.
  - Dispatch records gain a `stage` field (audit/dedup identity). **It must not enter `build_stage_snapshot`** — G4 compares snapshots, and any per-dispatch-varying field silently kills oscillation detection. This is #2731's own warning about the `dispatcher` block, and it applies identically here.
  - `compute_same_stage_count`'s counting rule changes (Task 3). Its signature and its `_meta` key name do not, which is what keeps `tools/sdlc_stage_query.py` untouched.
- **Coupling:** decreases. Today "a stage ran" is knowable only if the router happened to be the caller. After this, the actor that changes stage state is the actor that records it.
- **Data ownership:** `_sdlc_dispatches` gains a second writer (the marker path). This is the one genuine ownership change and it is the point of the fix — see Risk 1 for the double-count hazard it creates.
- **Reversibility:** high for #2730 (additive field + a counting rule). The #2731 gate's reversibility depends on which option Q1 selects.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

Three defects across a small file set: `tools/sdlc_dispatch.py`, `tools/sdlc_stage_marker.py`, `agent/sdlc_router.py`, and tests. The weight is not volume — it is four proofs:

1. That the marker-side record cannot double-count against the router's record (Risk 1).
2. That the new counting rule fires on alternation **and** still fires on the same-skill loops rows 2c/8c/8d/8e/8f depend on, without re-opening #1641/#1668.
3. That nothing per-dispatch-varying reaches `build_stage_snapshot`.
4. That the #2731 gate refuses a live peer and admits a dead one, with *unknown* never authorizing entry.

Medium holds only if Q1 is settled before BUILD. If Q1 selects Option B (a new stage-claim key), the appetite grows to the upper edge of Medium and #2731 should be considered for its own PR.

## Prerequisites

| Requirement | Check | Purpose |
|---|---|---|
| Worktree on the pinned interpreter | `.venv/bin/python -c "import sys,pathlib; assert sys.version.split()[0].startswith(pathlib.Path('.python-version').read_text().strip())"` | `scripts/pytest-clean.sh` aborts otherwise |
| Rebased on `706fc4da0` | `git merge-base --is-ancestor 706fc4da0 HEAD` | Row 8b / marker-message work this plan corrects |
| `tools/sdlc_stage_query.py` still calls the router helpers | `grep -n "compute_same_stage_count" tools/sdlc_stage_query.py` | The lane-identity boundary; if this changed, coordinate |

## Solution

### Key Elements

- **One event, one record.** The actor that changes stage state records the dispatch. The router keeps its pre-invocation record (it is the only signal for a skill that dies before it starts); the marker path fills the gap for every other entry path, with an identity that makes the two idempotent rather than additive.
- **An oscillation signal that counts what oscillates.** G4 counts *stage re-entry without forward progress*, not *consecutive identical skill strings*, so an alternating loop is visible.
- **A stage-entry gate with a real liveness test** — pending Q1.
- **Docstring honesty.** Every row that claims a G4 bound either has one after this change, or says which guard actually bounds it.

### Technical Approach

**1. #2730 — make the record unbypassable (`tools/sdlc_stage_marker.py`, `tools/sdlc_dispatch.py`)**

`write_marker` derives the skill from `agent.pipeline_graph.STAGE_TO_SKILL[stage]` (canonical, 1:1, already imported by the router) and records a dispatch on **stage entry** — the transition of a stage *into* `in_progress`, not every marker write.

The double-count hazard is the whole design problem. The router records `/do-build` and then `/do-build` writes `BUILD in_progress`; naively that is two records for one dispatch, and G4 counts inflate toward false escalation. The dedup must key on something stable across both writes and *distinct* across genuine repeats. Candidate identity: `(run_id, stage, occupancy)` where occupancy increments only on a `not in_progress → in_progress` transition. BUILD must prove:

- A router-driven dispatch yields exactly one record.
- A bypassing chain (`/do-build` → `/do-patch`) yields exactly one record for `/do-patch`.
- A genuine repeat of the same stage yields a second record (this is the G4 signal; deduping it away is the failure mode that would silently disarm the guard).

Marker discipline is uneven — some skills write `in_progress`, several write only `completed`. A `completed` write for a stage that never recorded an entry must therefore also record one, or the stages that skip `in_progress` stay invisible. BUILD surveys which skills write which statuses and states the coverage explicitly rather than assuming.

**2. #2730(b) — an oscillation signal that can fire (`agent/sdlc_router.py`)**

Change `compute_same_stage_count` from "consecutive entries with an identical skill string" to a rule that detects a repeating **cycle** over an unchanged snapshot. The snapshot equality test is already the real "no forward progress" signal and is retained unchanged; only the skill-identity break at `:1874` is at issue.

Constraints this must satisfy, all testable:
- Rows 2c/8c/8d/8e/8f (same-skill repeats) still escalate at `MAX_SAME_STAGE_DISPATCHES` exactly as today — #1641/#1668 must not re-open.
- An A→B→A→B loop over an unchanged snapshot escalates.
- A productive alternation, where the snapshot advances each turn, never escalates. This is the false-positive risk and it is the one that would wedge every healthy pipeline; it is a blocking test obligation, not a nicety.
- `same_stage_dispatch_count` keeps its name, type, and meaning-of-magnitude so `guard_g4_oscillation` and `tools/sdlc_stage_query.py` need no change.

Then correct `_rule_patch_applied_after_review`'s docstring (`:1275`) and audit the other five "Loop-bound by G4" claims against the new rule, so each names a bound that exists.

**3. #2731 — the stage-entry liveness gate (BLOCKED on Q1)**

Two options, both honoring "no liveness fields on the dispatch record":

- **Option A — issue-lease read only.** `dispatch record` refuses when the issue lease is held by a *different, live* `run_id`. Small, uses `touch_issue_lock(peek=True)` and the existing `_lock_owner_is_live`. **Does not cover #2629's observed intra-run collision** and largely duplicates `resolve_ledger_lease`. Honest but nearly a no-op.
- **Option B — a stage-scoped claim key.** A Redis key per `(issue, stage)` holding the entering agent's `(pid, create_time)` fence, modelled on #2650's plan-doc lease and using `agent/pid_fence.py`. Refuses when the recorded fence is live and no `completed` marker for that stage postdates it; admits when provably dead (#2629's recovery case); admits when absent (pre-change records). Covers the observed collision. Costs a new key and a release path — and a claim that is never released is a wedge, so its TTL and release legs are the design risk.

Option B is the recommendation. Under either option the loser-side stand-down protocol (detect, diff against the peer's HEAD, stash anything unique, stand down **without mutating stage state**, report) is written into the skill body that owns it.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `record_dispatch_for_ledger` already swallows every exception to `False`. With the marker path now calling it, assert a failed dispatch write never fails the *marker* write — a stage must not become unrecordable because its audit entry failed. Fail-open here is correct and must be deliberate.
- [ ] `write_marker`'s `STATE_MACHINE_RAISED` catch must not be widened by the new call; assert its message is unchanged (it is the model the four refusal sites were made to follow in `706fc4da0`).
- [ ] `compute_same_stage_count` is called inside `_compute_meta`'s broad `try`. Assert the new rule never raises on a malformed history — non-dict entries, missing `skill`, missing `stage_snapshot`, `None` — because that `except` swallows into `same_stage_count = 0`, silently disarming G4.

### Empty/Invalid Input Handling
- [ ] A stage with no `STAGE_TO_SKILL` entry (an unknown stage string) must record nothing and not raise.
- [ ] Dispatch history containing pre-change records with no `stage` field must count correctly under the new rule.
- [ ] History at exactly `MAX_DISPATCH_HISTORY` (FIFO eviction boundary) — assert eviction cannot truncate a streak into invisibility, or state that it can and why that is acceptable. See Risk 6: this is arithmetic, not a corner case.

### Error State Rendering
- [ ] The #2731 refusal is a user-visible error path and is a deliverable: named reason on stderr, non-zero exit, identifying the live holder.
- [ ] A G4 escalation triggered by an *alternating* loop must name the cycle in its `Blocked.reason`, not a single skill — `guard_g4_oscillation`'s current message says "{skill} dispatched {count} times", which is wrong wording for a cycle.

## Test Impact

- [ ] `tests/unit/test_sdlc_router_oscillation.py` — UPDATE: add the alternating-loop escalation case and the productive-alternation non-escalation case; keep every existing same-skill case green (#1641/#1668 regression set).
- [ ] `tests/unit/test_sdlc_router.py`, `test_sdlc_router_decision.py` — CHECK: assertions on `same_stage_dispatch_count` and on rows 8/8b routing.
- [ ] `tests/unit/test_sdlc_stage_marker.py` — ADD: the marker path records a dispatch on stage entry; no double-count against a router record; a failed dispatch write does not fail the marker write.
- [ ] `tests/unit/test_sdlc_dispatch.py` (verify it exists; else create) — ADD: dedup identity, the `stage` field, and the #2731 gate.
- [ ] `tests/unit/test_sdlc_lease_helper_binding.py` — CHECK: it guards `sdlc_dispatch`'s module-object lease access (#2469/#2637). Any new import in that file must not snapshot lease helpers via `from`-import.
- [ ] A test asserting `build_stage_snapshot` output is unchanged by this lane — the single highest-value regression test here, since a leaked field disarms G4 silently.

No xfail markers exist for these defects.

## Rabbit Holes

- **Rewriting the dispatch table or the guard set.** #2730(b) is one counting rule. A guard redesign is a separate project.
- **Making the ledger a general event log.** Tempting once markers write dispatches. The record stays exactly as wide as G4 and the #2731 gate need.
- **Fixing #2675's ledger-anchor recreation.** It plausibly explains why #2711's history emptied. It is a different defect with its own issue.
- **Auditing every guard for fireability.** #2658 owns that. This plan corrects the G4 claims it touches and stops.
- **Making the #2731 gate cover cross-machine collisions.** `pid_fence` is local-only by construction; a foreign-machine holder fails toward "live" and the TTL is the backstop, exactly as the issue lock already does.

## Risks

### Risk 1: The marker-side record double-counts against the router's record
**Impact:** every router-driven dispatch scores twice, G4 escalates at half its intended threshold, and healthy pipelines wedge with a spurious oscillation block. This is the highest-severity outcome in the lane — it converts a dead guard into a hair-trigger one.
**Mitigation:** the dedup identity is the core of Task 2 and gets the demonstrated-red treatment: a test that fails against a naive unconditional append. Assert exact record counts, not merely "a record exists".

### Risk 2: The new counting rule fires on productive work
**Impact:** a healthy review↔patch cycle escalates to a human every few turns; every lane on the machine stalls.
**Mitigation:** the snapshot-equality conjunct is retained unchanged and is what distinguishes progress from spinning. Explicit non-escalation tests for advancing snapshots, and the #1641/#1668 same-skill set stays green.

### Risk 3: A per-dispatch-varying field reaches `build_stage_snapshot`
**Impact:** G4 silently never fires again — the failure this whole lane exists to fix, re-introduced by the fix.
**Mitigation:** the `stage` field is added to the record and explicitly not to the snapshot; a test pins the snapshot's exact key set.

### Risk 4: The #2731 claim key wedges a stage
**Impact:** a crashed agent leaves a claim nothing releases, and the stage is permanently unenterable — strictly worse than the collision it prevents.
**Mitigation:** (Option B only) the fence is a liveness test, not a lock: a dead holder's claim is ignorable by construction, never waited on. Plus a TTL. This inverts the usual lock hazard and must be tested with a stale claim from a dead pid.

### Risk 6: The history is too short to hold a countable cycle
**Impact:** G4 still cannot fire on alternation, after all this work — the streak is FIFO-evicted before it reaches the threshold.
**The arithmetic, which is tight:** `MAX_DISPATCH_HISTORY = 10` (`agent/sdlc_router.py:98`) and `MAX_SAME_STAGE_DISPATCHES = 3` (`:86`). A 2-skill cycle repeated 3 times needs 6 entries — fits. A 3-skill cycle needs 9 — barely fits. A 4-skill cycle needs 12 and **cannot ever be counted**. Task 2 makes this worse before it makes it better: the marker path adds records, so the same wall-clock history occupies more slots.
**Mitigation:** BUILD computes the worst-case cycle length the router can actually produce and either proves it fits in 10, or raises `MAX_DISPATCH_HISTORY`. Raising it is cheap (a bounded list on a ledger already holding stage snapshots) and is preferable to a threshold that silently cannot be reached. This interacts with Q4 and must be settled together with it.

### Risk 5: Lane collision on `tools/sdlc_stage_query.py`
**Impact:** conflict with the lane-identity lane.
**Mitigation:** this plan requires no edit there, by design — the G4 rule change lives entirely in `agent/sdlc_router.py` behind an unchanged signature. Re-verify at BUILD start.

## Race Conditions

### Race 1: Router record and marker record interleave
**Location:** `tools/sdlc_dispatch.record_dispatch_for_ledger` → `update_stage_states`
**Trigger:** the router's record and the skill's marker-side record land close together on the same ledger.
**Mitigation:** both go through `update_stage_states`' optimistic retry, so neither is lost. The dedup must therefore be evaluated *inside* the update function against the state being written, not read-then-write outside it — a check-then-append across two calls is exactly the interleaving that produces the double record.

### Race 2: Two agents enter a stage simultaneously (#2731's own race)
**Location:** the gate, wherever Q1 puts it
**Trigger:** two forks call `dispatch record` for the same stage within the same instant.
**Mitigation:** the claim must be acquired with an atomic primitive (`SET NX`), not read-then-write. A gate that reads, decides, then writes admits both agents in exactly the window it exists to close.

### Race 3: Stage completes between the liveness read and the append
**Location:** the gate
**Trigger:** the prior agent finishes and writes `completed` after the gate reads its claim as live.
**Mitigation:** the gate's condition is "live holder AND no `completed` marker postdating the claim"; re-read the marker immediately before refusing, so a just-finished peer does not block a legitimate next entry.

## No-Gos (Out of Scope)

- [ORDERED] Backfilling dispatch records for existing ledgers. The read paths tolerate short histories; a backfill over live ledgers is human-gated and must follow, not accompany, the writer change.
- [SEPARATE-SLUG #2675] Continuity re-ensure recreating the ledger anchor. A candidate explanation for #2711's emptied history, but a different defect.
- [SEPARATE-SLUG #2658] The general fireable-gate audit.
- [SEPARATE-SLUG #2637] The frozen lease-helper import in `sdlc_dispatch`. Named in #2730 as a *candidate* contributing cause; ruled out here — it would surface as a refused (`ok: false`) record, and would not explain markers succeeding while records vanish under the same run. Recorded so BUILD does not re-investigate.

## Update System

No update-system changes. All edits are internal to `tools/` and `agent/`, plus skill-body prose for the stand-down protocol. `.claude/skills-global/` bodies propagate via the existing `/update` hardlink; no new wiring.

## Agent Integration

No new entry points. `sdlc-tool dispatch` and `sdlc-tool stage-marker` already exist. Two integration points to verify rather than add:

- [ ] `sdlc-tool dispatch record --help` documents the refusal reason (#2731).
- [ ] The skill body that owns the loser-side stand-down states it.

## Documentation

### Feature Documentation
- [ ] `docs/features/sdlc-router-oscillation-guard.md` — the new G4 counting rule, what it can and cannot see, and the corrected per-row bound claims.
- [ ] `docs/features/sdlc-stage-tracking.md` — stage entry now records a dispatch.
- [ ] `docs/features/sdlc-pipeline.md` — check the G4 description.
- [ ] `docs/tools-reference.md` — the #2731 refusal.
- [ ] `docs/features/README.md` — index entries if any page is added.

### Inline Documentation
- [ ] `compute_same_stage_count` — the new rule and why the old one could not see alternation.
- [ ] `build_stage_snapshot` — state that per-dispatch-varying fields must never be added, and why.
- [ ] `_rule_patch_applied_after_review` (`:1275`) and the other five "Loop-bound by G4" claims.
- [ ] `guard_g4_oscillation` — the escalation message's wording for a cycle.
- [ ] `tools/sdlc_dispatch.py` module docstring — the caller contract is no longer router-only.

## Success Criteria

- [ ] A stage reached without the router records a dispatch (#2730).
- [ ] A router-driven dispatch records exactly one entry — no double count (#2730, Risk 1).
- [ ] An alternating loop over an unchanged snapshot escalates via G4 (#2730b).
- [ ] A productive alternation with an advancing snapshot never escalates (#2730b, Risk 2).
- [ ] Every same-skill escalation case from #1641/#1668 still passes unchanged.
- [ ] `build_stage_snapshot`'s key set is unchanged, pinned by a test (Risk 3).
- [ ] Every "Loop-bound by G4" docstring names a bound that can actually fire.
- [ ] `tools/sdlc_stage_query.py` is not modified.
- [ ] A dispatch whose stage has a provably live peer is refused with a named reason (#2731, pending Q1).
- [ ] A dispatch whose peer is provably dead succeeds — #2629's recovery case (#2731, pending Q1).
- [ ] The loser-side stand-down protocol is stated in the skill body that owns it (#2731).
- [ ] Tests pass; documentation updated.

## Step by Step Tasks

### 1. Survey every actor that changes stage state
- **Task ID**: audit-writers
- **Depends On**: none
- **Parallel**: true
- Enumerate every path that writes a stage marker and every path that records a dispatch, across `tools/`, `agent/`, `.claude/skills*/`, and `docs/sdlc/`.
- For each stage, record which statuses are written and by whom, so Task 2 knows which stages would stay invisible if only `in_progress` recorded.
- Confirm or refute that `/do-build`'s chain to `/do-patch` is the only in-repo non-router invocation path.

### 2. Record a dispatch on stage entry (#2730)
- **Task ID**: build-marker-records
- **Depends On**: audit-writers
- **Parallel**: false
- Derive the skill via `STAGE_TO_SKILL`; record on stage entry; add the `stage` field to the record.
- **The dedup must be evaluated inside the `update_stage_states` apply function** (Race 1), never as a read-then-append across two calls.
- Prove: one record for a router-driven dispatch, one for a bypassing chain, two for a genuine repeat.
- A failed dispatch write must never fail the marker write.
- **`stage` must not enter `build_stage_snapshot`** (Risk 3).

### 3. Make G4 count what oscillates (#2730b)
- **Task ID**: build-g4-cycle-count
- **Depends On**: none
- **Parallel**: true
- Replace the same-skill break in `compute_same_stage_count` with cycle detection over an unchanged snapshot.
- Keep the signature and the `_meta` key name so `tools/sdlc_stage_query.py` needs no edit.
- Add the alternating-escalation and productive-non-escalation cases; keep the #1641/#1668 set green.
- Correct the six "Loop-bound by G4" docstrings and `guard_g4_oscillation`'s message wording.

### 4. Stage-entry liveness gate (#2731)
- **Task ID**: build-liveness-gate
- **Depends On**: build-marker-records, **Q1 resolved**
- **Parallel**: false
- **BLOCKED until Q1 is answered.** Implement the selected option; acquire atomically (Race 2); re-read the completion marker before refusing (Race 3); unknown never authorizes entry.
- Write the loser-side stand-down protocol into the owning skill body.

### 5. Documentation
- **Task ID**: document-lane
- **Depends On**: build-marker-records, build-g4-cycle-count, build-liveness-gate
- **Parallel**: false

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-lane
- **Parallel**: false

## Verification

| Check | Command | Expected |
|---|---|---|
| Router/oscillation tests | `scripts/pytest-clean.sh tests/unit/test_sdlc_router.py tests/unit/test_sdlc_router_oscillation.py tests/unit/test_sdlc_router_decision.py -q` | exit 0 |
| Marker/dispatch tests | `scripts/pytest-clean.sh tests/unit/test_sdlc_stage_marker.py tests/unit/test_sdlc_dispatch.py -q` | exit 0 |
| Lease-helper binding intact | `scripts/pytest-clean.sh tests/unit/test_sdlc_lease_helper_binding.py -q` | exit 0 |
| Lint / format | `python -m ruff check . && python -m ruff format --check .` | exit 0 |
| Off-limits file untouched | `git diff --name-only main...HEAD -- tools/sdlc_stage_query.py` | empty |
| Snapshot not widened | `grep -n "def build_stage_snapshot" -A 30 agent/sdlc_router.py` | returns the same five keys |
| Stage→skill map is the single source | `grep -c "STAGE_TO_SKILL" tools/sdlc_stage_marker.py` | > 0 |
| No G4 claim without a bound | `grep -n "Loop-bound by G4" agent/sdlc_router.py` | every hit reviewed in the PR body |

## Open Questions

**Q1 is a genuine blocker — it is not pre-answered, and Task 4 does not start until it is settled.**

1. **#2731's gate primitive.** The owner's constraint is "liveness belongs to the lease, do not add liveness fields to the dispatch record." Source shows the *issue* lease is run-scoped and cannot see an intra-run collision, which is the shape #2629 exhibited. Option A (issue-lease read) is faithful to the literal constraint and does not fix the observed bug; Option B (a stage-scoped claim key with a pid fence, per #2650's precedent) honors "liveness lives in a lease, not in the audit record" and does fix it. **Recommendation: Option B.** Owner's call.
2. **Should #2730(b) ship in this lane or as its own issue?** It is not in #2730's text, but #2730's headline claim is that G4 is defanged, and shipping record-completeness alone yields a guard that looks armed and is not. Recommendation: keep it here.
3. **Why did #2711's dispatch history empty while its markers survived?** #2675 is the likely cause. Not investigated; listed so the reviewer can decline it explicitly.
4. **`MAX_SAME_STAGE_DISPATCHES = 3` and `MAX_DISPATCH_HISTORY = 10` under the new rule.** Should the threshold count *cycles* or *entries*? And the bound must be large enough to hold the threshold: a 4-skill cycle repeated 3 times needs 12 entries and cannot fit in 10, so the guard would be unreachable by arithmetic (Risk 6). Recommendation: count cycles, and raise `MAX_DISPATCH_HISTORY` to whatever the worst-case cycle requires with headroom.
