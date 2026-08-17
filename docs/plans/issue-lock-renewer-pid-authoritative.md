---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-17
tracking: https://github.com/tomcounsell/ai/issues/2648
last_comment_id: 5311761287
---

# Re-stamp the renewer's pid on same-owner lease renewal so pid liveness becomes authoritative

## Problem

`models/session_lifecycle.py::_lock_owner_is_live` opens with an unconditional
short-circuit:

```python
if _lock_renewal_is_fresh(payload) is True:
    return True          # every pid check below is skipped
```

A process that acquires the SDLC issue lease, writes **one** renewal stamp, and
then dies reads as a LIVE owner for up to
`ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS` (1200 s at the default TTL). The pid
check that would immediately have returned `False` is never invoked. Every rival
— including the dead run's own parent supervisor — is told `orphaned_lock:
false`, which is a *positive assertion of life* and the one refusal a
correctly-behaving supervisor never overrides. The failure is silent and reads
as healthy contention; it costs a supervision cycle plus operator forensics to
distinguish a ghost from a real rival.

This is not "TTL alone, not liveness". `_lock_owner_is_live` *does* consult
liveness. The defect is that one liveness signal short-circuits past the other.

**Why the obvious fix is wrong.** Simply deleting the short-circuit reverts
#2620: the payload's `pid` belongs to the ephemeral `sdlc-tool session-ensure`
CLI, dead within seconds, so pid inference alone reads EVERY locally-minted
lease as orphaned. It also re-opens #2703 defect 1, because
`tools/sdlc_session_ensure.py::_iter_orphan_sessions` now gates `--kill-orphans`
on this same predicate. **The payload must gain a checkable durable pid before
the predicate can be tightened at all.**

**Scope.** Only the surviving half of #2648. Everything else on the original
Next Steps list is mooted by #2784 (heartbeat now releases on confirmed
supervisor death) and #2703 (the reaper's new dependence on this predicate). See
**No-Gos**.

## Freshness Check

Baseline commit: `335fde5b3` (`main`, 2026-08-17). Issue filed 2026-08-07.
Disposition: **Minor drift** — the mechanism is intact and the corrected
file:line references are recorded below; the *remedy* was re-scoped by the
accepted premise-check comment, which is honored throughout this plan.

### file:line references re-verified

| Cited in issue / comments | Current location at `335fde5b3` | Status |
|---|---|---|
| short-circuit `:1017-1023` | `models/session_lifecycle.py:1033` | drifted, claim holds |
| `_lock_renewal_is_fresh` `:949-979` | `models/session_lifecycle.py:962` | drifted, claim holds |
| freshness constant `:859-861` | `models/session_lifecycle.py:859` | unchanged |
| `_lock_owner_is_live` | `models/session_lifecycle.py:995` | present |
| renewal branch `:1277-1296` | `_healed_renewal_payload` at `:1084`; renew branches at `:1296` (renew_only CAS) and `:1378` (default) | drifted, claim holds |
| heartbeat renew `sdlc_lease_heartbeat.py:141` | `tools/sdlc_lease_heartbeat.py:429` | drifted, claim holds |
| worker renew `session_executor.py:246` | `agent/session_executor.py:246` | unchanged |
| reaper predicate | `tools/sdlc_session_ensure.py:1059` (`_iter_orphan_sessions`) | present |

### Defect reproduced against current main

Constructed payloads, no Redis, spying on
`agent.session_health._psutil_process_for_pid` (dead pid on every row):

| `renewed_at` age | `is_live` | pid check reached |
|---|---|---|
| 5 s | `True` | no |
| 1199 s | `True` | no |
| 1201 s | `False` | yes |
| absent | `False` | yes |

The bug is still present and still constructible. Constants confirmed:
`ISSUE_LOCK_TTL_SECONDS = 1800`, `ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS = 1200`.

### Cited siblings re-checked

- **#2620** CLOSED — introduced the short-circuit; its documented rationale is
  the constraint this plan must satisfy, not overturn.
- **#2703** CLOSED — added the reaper's dependence on this exact predicate. The
  central tension; resolved in **Technical Approach**.
- **#2784** MERGED (`ac96dc210`) — heartbeat watches its supervisor and
  *releases* on confirmed death. Narrows but does not close this issue: the
  releaser is the heartbeat, so a dead heartbeat has no releaser.
- **#2714** CLOSED, **#2659/#2667** MERGED (`acc350c73`) — adjacent lease work,
  no conflict.

### Commits touching the relevant files since the issue was filed

`ac96dc210` (#2784), `acc350c73` (#2667), `ec585ceb9` (#2803), `e50eba258`
(#2792), `971ff1caf` (#2747), `ac190fb26`, `0f070970b`, `baea50bf8`. Reviewed:
only #2784 and #2667 touch lease liveness, and both are accounted for above.

### Active-plan overlap

`ls -lt docs/plans/` shows no active plan touching `models/session_lifecycle.py`
lease liveness. No overlap.

## Prior Art

- **#1954 / PR #1956** — introduced the issue lock (duplicate-PR root cause
  #1915). Established that a false-dead verdict is the expensive failure.
- **#2305 defect 1 / PR #2415** — retired the "any non-terminal AgentSession
  carrying this run_id" liveness mirage and made the lock payload
  (`pid` + `machine_id` + `create_time`) the only trusted evidence.
- **#2537** — same-machine detection re-keyed from the rename-mutable
  `hostname` to the stable `machine_id`.
- **#2620** — made renewal freshness the short-circuit, precisely because the
  stamped pid is the dead `session-ensure` CLI.
- **#2703** — made `--kill-orphans` consume the same predicate.
- **#2714 / #2784** — supervisor-anchored heartbeat lifetime plus active
  release on confirmed supervisor death.

## Why Previous Fixes Failed

Each prior fix was correct for its own failure direction and none is being
reverted; what accumulated is a predicate pulled in opposite directions by
consumers that arrived at different times.

- **#2305 (pid becomes authoritative)** was applied at the right layer but
  against a payload whose pid was never durable. It made the predicate
  *structurally* right and *empirically* useless for locally-minted leases.
- **#2620 (freshness short-circuits)** correctly diagnosed that symptom, but
  fixed it by making a stamp *conclusive* when the evidence only supports
  *corroborating*. A stamp outlives its writer; that is the whole defect here.
  #2620 addressed the symptom (pid always reads dead) rather than the cause
  (the durable renewer's identity never reaches the payload).
- **#2703** then built a second consumer on the weakened predicate, so the
  naive tightening now has a blast radius #2620 never had.

The pattern: three fixes to a liveness predicate, none of which fixed *what the
payload records*. This plan fixes the recorded evidence first and tightens the
predicate only where that evidence exists.

## Research

Purely internal. No external libraries, APIs, or ecosystem patterns are
involved: the change touches one repo-local predicate, its two repo-local
durable renewers, and `psutil` calls that already exist in-tree via
`agent.session_health._psutil_process_for_pid`. No relevant external findings —
proceeding with codebase context.

## Spike Results

### spike-1: Does the defect still reproduce at `335fde5b3`?

- **Assumption**: "A dead-pid payload with a fresh `renewed_at` still reads live
  and never reaches the pid check."
- **Method**: prototype (in-process, no Redis)
- **Result**: **Confirmed.** Table in *Freshness Check* above. `is_live=True`
  with `pid_check_reached=False` at 5 s and 1199 s; `is_live=False` with
  `pid_check_reached=True` at 1201 s and with no stamp.
- **Confidence**: high
- **Impact if false**: plan would be moot; it is not.

### spike-2: Does the composed design preserve #2703's reaper exemption?

- **Assumption**: "Stamping a renewer identity ONLY from durable renewers, and
  making freshness corroborating ONLY when that identity is present, keeps a
  live-but-quiet local run exempt from `--kill-orphans`."
- **Method**: prototype — reimplemented the proposed predicate standalone and
  enumerated every payload shape that reaches either consumer.
- **Result**: **Confirmed for these eight rows, but the row set was
  incomplete** — every row here presupposes the renewer died *because* the run
  died or is restarting. The missing shape (a renewer that exited on its own
  deadline while the run lives) is measured in **spike-5**, which is the
  authority where the two overlap. With `ISSUE_LOCK_RENEWER_GRACE_SECONDS = 180`:

  | # | Payload shape | Proposed verdict | Why it must be that |
  |---|---|---|---|
  | A | fresh stamp, renewer pid ALIVE | live | #2703: live-but-quiet local run stays exempt |
  | B | fresh stamp (210 s), renewer pid DEAD | **dead** | the #2648 incident — now caught in 180 s, not 1200 s |
  | C | fresh stamp (60 s), renewer pid DEAD | live | inside grace; a restarting renewer is not a dead run |
  | D | fresh stamp, NO renewer identity | live | unchanged #2620 behavior for legacy/CLI-only payloads |
  | E | stale stamp, dead pid | dead | unchanged |
  | F | no `renewed_at` (pre-#2620), dead pid | dead | unchanged |
  | G | renewer pid dead 30 s (worker restart) | live | inside grace |
  | H | cross-machine, renewer pid unresolvable | live | unchanged fail-toward-live |

  At `GRACE = 300` row B regressed to `live` (the observed incident was
  inspected at 210 s). 180 s is the largest value that still catches the
  recorded incident; see **Technical Approach** for the sizing argument.
- **Confidence**: high
- **Impact if false**: would force splitting the predicate per consumer, which
  this plan rejects (see **No-Gos**).

### spike-3: Does a running heartbeat stamp its identity early enough to matter?

- **Assumption**: "A durable renewer stamps within seconds of the mint, not one
  renewal interval (600 s) later."
- **Method**: code-read of `tools/sdlc_lease_heartbeat.py`.
- **Result**: **Confirmed.** The loop seeds `since_renew = interval` with the
  comment "so tick one always renews", so the detached heartbeat's *first*
  action is a renewal. The worker path ticks every 60 s. The window in which a
  supervised lease carries no renewer identity is therefore seconds, not
  minutes.
- **Confidence**: high
- **Impact if false**: the fix would be inert for the first 600 s of every local
  run. It is not.

### spike-4: Which callers renew, and which of them are ephemeral?

- **Assumption**: "Exactly two renewers are durable; every other caller of the
  renewal path is a short-lived CLI that would re-poison the field."
- **Method**: code-read (`grep` over every `touch_issue_lock(` call site).
- **Result**: **Confirmed.** Durable: `tools/sdlc_lease_heartbeat.py:429`
  (detached, `start_new_session=True`), `agent/session_executor.py:246` (worker
  tier-1 60 s tick). Ephemeral: `tools/_sdlc_utils.py:609`,
  `tools/_sdlc_utils.py:834`, `tools/sdlc_stage_marker.py:884`,
  `tools/sdlc_next_skill.py:638`, `tools/sdlc_session_ensure.py:539` (the mint).
  Read-only peeks (`tools/merge_predicate.py:656`,
  `tools/sdlc_review_finalize.py:351`) never write.
- **Confidence**: high
- **Impact if false**: an opt-in flag would be the wrong shape.

### spike-5: What happens when the renewer exits at its own deadline while the run is still alive? (round-2 BLOCKER)

- **Assumption under test**: spike-2's claim that "there is no payload shape
  reachable by a live-but-quiet local run that the tightened branch turns dead."
- **Method**: prototype, **measured not predicted** — imported the real
  `_lock_renewal_is_fresh` / `_payload_from_same_machine` /
  `_lock_owner_is_live` from `models/session_lifecycle.py` at `a3e560f38`,
  reimplemented the proposed tightened branch beside them, and drove the
  reaper's payload-present path
  (`tools/sdlc_session_ensure.py::_iter_orphan_sessions`) verbatim.
  `machine_id` resolved to `541DBF1F-…`, `GRACE=180`, `FRESH=1200`.
- **Setup grounding** (verified at `a3e560f38`, not assumed):
  `tools/sdlc_lease_heartbeat.py:351-365` has TWO deadline exits that
  deliberately do NOT release — `EXIT_UNSUPERVISED_MAX_LIFETIME` (90 min) whose
  own comment reads *"Deliberately NO release — failing to resolve a supervisor
  is not positive proof the run is dead"*, and `EXIT_MAX_LIFETIME` (4 h). Only
  `EXIT_SUPERVISOR_DEAD` releases. And `tools/sdlc_session_ensure.py:1128` is
  `if not owner_live: yield s` with **no idle-time conjunct** on the
  payload-present path (the `_last_activity_at` fallback is reached only when
  the payload is `None`), so a dead verdict reaps immediately.

**Measured results:**

| # | Row | today | proposed (naive) | reaper |
|---|---|---|---|---|
| I | renewer exited at deadline, run **ALIVE**, quiet 200 s | live | **dead** | **REAP** |
| I' | same, quiet 800 s (mid 6–25 min stage) | live | **dead** | **REAP** |
| I'' | same, quiet 60 s (an ephemeral CLI renewal just landed) | live | live | keep |
| Ia | **shape (a)**: deadline exit clears `renewer_*`, run ALIVE, quiet 800 s | live | **live** | keep |
| Ia' | shape (a), run then dies, quiet 800 s | live | live | keep |
| Ib | **shape (a) residual**: heartbeat *crashed* (no clear), run ALIVE, quiet 800 s | live | **dead** | **REAP** |
| II | run DEAD, heartbeat dead, quiet 200 s (the #2648 incident) | live | **dead** | REAP ✅ |
| III | run ALIVE, heartbeat ALIVE, quiet 800 s (#2703 exemption) | live | live | keep |
| IV | no renewer identity, fresh (#2620 fallback) | live | live | keep |
| V | cross-machine dead renewer | live | live | keep |

- **Result**: **The blocker is real and reproduced.** Rows I/I' show the naive
  tightening converting a live run from "loses at most one TTL window" into
  **reaped within 180 s, mid-stage** — strictly worse than the bug being fixed,
  and via `_lock_says_live` it is the #1915 unattended-rival shape. Row Ia shows
  **shape (a) closes it**: with `renewer_*` cleared at the non-releasing exits,
  the payload falls back to the untouched #2620 short-circuit and the verdict is
  byte-identical to today's. Row Ib is shape (a)'s honest residual (sized in
  **Technical Approach**). Row II confirms the tightening still fires on the
  case it was written for, so the fix is not neutered.
- **Shape (b) measured separately** (idle-time conjunct at the reaper's
  payload-present path), and it **loses**:

  | Consumer | Protected by (b)? | Measured |
  |---|---|---|
  | `_iter_orphan_sessions` reaper | yes | reap suppressed until idle ≥ `ORPHAN_AGE_SECONDS` (600 s) |
  | peek path (`orphaned_lock`) | **no** | predicate still returns `False` |
  | `reflections/utilities.py::_lock_says_live` | **no** | returns `False` → gate 3 admits `create_session` |

  (b) therefore leaves the *most expensive* consumer — the autonomous
  reflection lane — fully exposed, and it costs something real: its clock is
  `_last_activity_at` → `updated_at`, which that helper's own docstring
  (`tools/sdlc_session_ensure.py:1005-1019`) says is "refreshed by probes and
  sibling `session-ensure` renewals … exactly the mirage that let a hollow
  tracking session read as live forever." Adding it as a **conjunct** on the
  payload-present path re-imports that mirage as a veto: a genuinely dead
  anchor whose `updated_at` keeps getting probe-refreshed would never reap,
  which re-opens #2703 defect 1 from the opposite side. #2305 removed
  `updated_at` from the payload-present path deliberately; putting it back is a
  revert, not a guard.
- **Decision: shape (a), and NOT (b) or (c).** (a) removes the one *systematic*
  false-dead generator; (b) is both insufficient (two of three consumers
  unprotected) and a regression of a decision two prior issues made on purpose.
- **Confidence**: high (measured against real helpers, not a paraphrase).
- **Impact if false**: if (a) could not be implemented as a compare-and-set
  write, the fallback would be an honest scope question, not a neutered branch.
  It can — the CAS path already exists at `models/session_lifecycle.py:1300`.

## Data Flow

```
sdlc-tool session-ensure  (ephemeral CLI, dies in seconds)
      |
      | touch_issue_lock(...)  -> SET NX  session:issuelock:{N}
      |    payload: {run_id, session_id, pid=<CLI pid, dead in seconds>,
      |              machine_id, hostname, create_time, renewed_at, target_repo}
      v
  _maybe_launch_lease_heartbeat()  -- detached, start_new_session=True
      |                               (skipped under VALOR_WORKER_MODE / pytest)
      v
  tools/sdlc_lease_heartbeat.py            agent/session_executor.py
    tick 1 immediately, then every            _tick_issue_lock_renewal
    TTL//3 (600s):                            every 60s (worker path):
      peek -> owner check                       touch_issue_lock(..., default branch)
      touch_issue_lock(renew_only=True)              |
           |                                         |
           +--------------> _healed_renewal_payload  <-+
                                  |
                     re-stamps renewed_at ONLY today.
                     >>> NEW: also stamps renewer_pid /
                         renewer_create_time when the caller
                         module is on the durable allowlist. <<<
                                  |
      heartbeat deadline exits (:359 EXIT_UNSUPERVISED_MAX_LIFETIME,
      :365 EXIT_MAX_LIFETIME -- deliberately no release)
           |
           +---> >>> NEW: one final CAS write that DROPS the
                     renewer_* group, so a run whose renewer
                     retired on its own clock falls back to the
                     untouched #2620 short-circuit. <<<
                                  |
                                  v
                       session:issuelock:{N}  (payload)
                                  |
        +-------------------------+-------------------------+
        v                         v                         v
 touch_issue_lock(peek=True)  _iter_orphan_sessions()  reflections/utilities.py
  -> _lock_owner_is_live         -> _lock_owner_is_live    ::_lock_says_live  (:277-302)
  -> IssueLockResult                live => EXEMPT from     -> bool | None
     .orphaned_lock                 --kill-orphans          -> sdlc_upvote_lanes.py:529,573
  -> ISSUE_LOCKED JSON to           dead => session         -> sdlc_progress.py:571,942,981
     /sdlc, /do-sdlc, the           reaped (NO idle
     router, merge_predicate        gate on this path)
```

**Three consumers, not two.** All three read the *same* predicate over the
*same* payload; that is the coupling this plan must not break. Their failure
costs are not equal, and they are listed here worst-first:

| Consumer | What a FALSE-DEAD verdict does | Cost |
|---|---|---|
| `reflections/utilities.py::_lock_says_live` → `sdlc_upvote_lanes.py:529` (gate 3) / `:573` (Race-1 re-read), `sdlc_progress.py:571`, `:942`, `:981` | `False` is the ONLY verdict that lets the reflection proceed to `create_session` (`True` and `None` both `continue`) | **Worst.** Starts an AUTONOMOUS rival SDLC lane on a live run — the #1915 duplicate-PR shape, unattended, with no operator in the loop |
| `_iter_orphan_sessions` → `--kill-orphans` | reaps the `sdlc-local-{N}` anchor immediately (no idle gate on the payload-present path) | Kills a live pipeline anchor mid-stage |
| `touch_issue_lock(peek=True)` → `orphaned_lock` | reports a live owner as a ghost | A rival may steal the lease; it *reports* before it acts, so an operator can intervene |

Containment that genuinely exists at the reflection layer, and its limit:
`sdlc_upvote_lanes` gate 2 (`_ledger_has_recorded_stage`) runs BEFORE gate 3
and excludes every candidate whose `PipelineLedger.stage_states_json` is
non-empty — so a live run that has recorded any stage never reaches the lock
read at all. The exposed window there is the pre-first-stage-marker gap, in
which spike-3 shows the heartbeat is freshly spawned and its pid resolves
alive. `sdlc_progress.py:942` is the wider exposure: its `create` rung fires
for an issue that already has a PR and a pushed branch, which a live mid-flight
lane matches, and there the lock read is the binding gate. Risk 1 argues the
tightened branch against that site specifically.

## Architectural Impact

- **No schema change.** `session:issuelock:{N}` is a plain non-Popoto Redis
  string key holding JSON. New keys are additive; absent keys keep today's
  behavior exactly. No migration, no `scripts/update/migrations.py` entry.
- **No new module, no new process.** All edits land in five existing source
  files (`models/session_lifecycle.py`, `tools/sdlc_lease_heartbeat.py`,
  `agent/session_executor.py`, plus two test modules).
- **One predicate stays one predicate.** `_lock_owner_is_live` remains the
  single authoritative liveness signal shared by **all three** consumers — the
  peek path, the orphan reaper, and `reflections/utilities.py::_lock_says_live`
  (`:277-302`), which explicitly delegates classification to it "so this module
  never forks the liveness rule." This plan deliberately does not fork it per
  consumer, and it does not add a per-consumer softening gate either (see
  spike-5's rejection of shape (b)).
- **Trust model shift, stated plainly.** Today the payload records the identity
  of a process that is guaranteed dead. After this change it also records the
  identity of the process that is guaranteed *alive as long as the run is* —
  which is what makes tightening safe at all.

## Appetite

**Small.** Four files, one new constant, one new payload-writing branch, one new
predicate branch, and a test class. The reasoning (the #2620/#2703 tension) is
the expensive part and is done in this document. No new services, no schema, no
migration.

## Prerequisites

None. Every file exists on `main` at `335fde5b3`.

## Solution

### Key Elements

1. **`_healed_renewal_payload` gains an opt-in `stamp_renewer_identity` flag.**
   When set, the renewal payload additionally carries **exactly two** new keys:
   `renewer_pid` and `renewer_create_time` — the identity of the process
   performing *this* renewal. **There is no `renewer_machine_id`** (BLOCKER 2,
   argued below).
2. **`touch_issue_lock` gains a keyword-only `stamp_renewer_identity: bool =
   False`,** plumbed into both renewal branches (`renew_only` CAS at `:1296` and
   the default same-owner branch at `:1378`).
3. **Exactly two callers pass it:** `tools/sdlc_lease_heartbeat.py:429` and
   `agent/session_executor.py:246`. The restriction is enforced **structurally
   at runtime** by a caller-module allowlist inside the renewal path itself, not
   only by the grep anti-criterion (see *Why the four-file grep is not
   sufficient*).
4. **`touch_issue_lock` also gains a keyword-only `drop_renewer_identity: bool
   = False`,** which performs a same-owner CAS renewal writing a payload with
   the `renewer_*` group **removed**. The heartbeat calls it at both of its
   non-releasing deadline exits (spike-5 shape (a)).
5. **`_lock_owner_is_live` makes freshness *corroborating rather than
   conclusive*, but only when a durable renewer identity is present, complete,
   and the payload is same-machine per the existing
   `_payload_from_same_machine`.** Otherwise the existing short-circuit is
   byte-for-byte unchanged.
6. **New named constant `ISSUE_LOCK_RENEWER_GRACE_SECONDS`** (default 180,
   env-overridable, marked provisional) bounds how long a dead renewer pid is
   tolerated while `renewed_at` is still fresh.

### Flow

`_lock_owner_is_live(payload)` after the change:

```
payload absent/not-a-dict                        -> True    (unchanged)
renewal fresh?
  |- True and renewer_pid truthy
  |          and renewer_create_time is not None
  |          and _payload_from_same_machine(payload):
  |      [own try/except Exception -> True around all of this]
  |      renewer pid alive and create_time matches within 1e-3 -> True
  |      renewer pid dead/recycled:
  |            renewed_at age <= GRACE            -> True
  |            renewed_at age  > GRACE            -> False   <-- the fix
  |- True and no/partial renewer identity          -> True    (unchanged #2620)
  |- False or None                                 -> fall through to the
                                                      existing pid checks
                                                      (unchanged)
```

**The grace clock's subject, stated so a builder cannot read it the other way.**
The clock is the **age of `renewed_at`**, NOT the duration for which the renewer
pid has been dead. The literal condition is

```python
time.time() - float(payload["renewed_at"]) > ISSUE_LOCK_RENEWER_GRACE_SECONDS
```

reusing the same field `_lock_renewal_is_fresh` (`:962-980`) already parsed.
Because this branch is only reached when `_lock_renewal_is_fresh(payload) is
True`, `renewed_at` is guaranteed present and float-parseable — no second
`try/except` for the parse, and do not re-derive it through a helper that can
return `None`. The alternative reading (duration of renewer death) would need
state the payload does not carry and would change every spike row.

Two consequences worth stating outright, because they cut in opposite
directions and both are real:

- `renewed_at` is re-stamped by **every** same-owner renewal, including all five
  ephemeral CLI renewers. So any `sdlc-tool` write resets the grace clock — the
  tightened branch only ever fires during a stretch of ≥ `GRACE` seconds with
  *zero* lock writes (spike-5 row I'').
- That same property means the tightening cannot fire on a chatty run at all.
  Its discriminating power lives exactly in the quiet stretches, which is where
  the #2648 incident was observed.

Everything below the fall-through — the missing-pid guard, the cross-machine
fail-toward-live, the missing-`create_time` guard, the psutil
pid + `create_time` comparison, the blanket `except -> True` — is untouched.

### Technical Approach

**Why the two halves compose (this is the argument, not an assumption).**

The coordinator's framing is *"freshness alone must not short-circuit past
liveness when the pid evidence is checkable."* The load-bearing clause is **when
the pid evidence is checkable**. Today it never is, on a locally-minted lease:
the payload's `pid` belongs to the `session-ensure` CLI, and #2620 exists
precisely because gating on it read every local lease as orphaned. So the two
recommendations in the issue's comments are not alternatives — they are ordered
dependencies. Stamping the renewer's identity is what *makes* pid evidence
checkable; only then does "freshness must not short-circuit past checkable pid
evidence" have anything to act on.

They compose safely because the tightened branch is **gated on the presence of
the very evidence the stamping produces.** Concretely:

- **#2703 defect 1 is preserved.** The reaper's exposed case is an
  `sdlc-local-{N}` anchor with `last_heartbeat_at = None`, for which renewal
  freshness is the only life signal. That anchor is kept alive by the detached
  heartbeat, which after this change stamps its own live pid on tick one
  (spike-3). So the payload carries `renewer_pid`, that pid resolves ALIVE, and
  the predicate returns live — the session stays exempt (spike-2 row A,
  spike-5 row III). If for any reason no durable renewer ever writes, the
  payload carries no `renewer_pid` and the untouched short-circuit applies
  (row D / row IV).
- **Legacy and cross-machine payloads are byte-identically unchanged.** No
  `renewer_pid` → short-circuit as today. Cross-machine → the existing
  fail-toward-live. All 14 existing tests carry no `renewer_pid` and stay green
  as written; this is a property to *verify*, not to hope for (see **Test
  Impact**).

**The hole in that argument, and how it is closed (round-2 BLOCKER).**

The first draft of this plan claimed *"there is no payload shape reachable by a
live-but-quiet local run that the tightened branch turns dead."* **That claim
was false**, and the counterexample is not the absent-renewer case argued
above — it is a **present-but-dead renewer on a still-live run**:

`tools/sdlc_lease_heartbeat.py` has two exits that deliberately do NOT release
the lease. `EXIT_UNSUPERVISED_MAX_LIFETIME` (`:360`, 90 min) carries the inline
rationale *"Deliberately NO release — failing to resolve a supervisor is not
positive proof the run is dead, so the lease's own 1800 s TTL is the correct
disposition. A live-but-unresolvable run therefore loses at most one TTL
window."* `EXIT_MAX_LIFETIME` (`:365`, 4 h) does the same. Only
`EXIT_SUPERVISOR_DEAD` releases, and only on *confirmed* supervisor death
(#2784). So a long-running lane reaches a state where the run is alive, the
heartbeat has retired on its own clock, and the payload still names the dead
heartbeat as its durable renewer.

Under the naive tightening, spike-5 measured what happens next: at 180 s of
quiet the predicate reads DEAD, and because
`tools/sdlc_session_ensure.py:1128` is a bare `if not owner_live: yield s`
with **no idle-time conjunct on the payload-present path**, `--kill-orphans`
reaps the live `sdlc-local-{N}` anchor immediately — well inside a 6–25 min
stage. Via `_lock_says_live` the same verdict can start an autonomous rival
lane. **Today's disposition for this exact state is "lose at most one TTL
window"; the naive tightening turns it into death-in-180 s.** That is strictly
worse than the bug being fixed, and it re-opens #2703 defect 1 *through* the
composition rather than preserving it.

**The fix, chosen by measurement (spike-5), is shape (a):** the heartbeat
performs one final compare-and-set write at both non-releasing exits that
**drops the `renewer_*` group**. The payload then carries no durable renewer
identity, the tightened branch is not entered at all, and the untouched #2620
short-circuit applies — spike-5 row Ia, verdict byte-identical to today's. This
is well-targeted precisely because it is the *same* judgement the exit comments
already make in prose: *failing to resolve a supervisor is not proof of death*,
so the payload should stop asserting an identity whose death would be read as
proof. The clear must be CAS (reuse `_RENEW_IF_VALUE_MATCHES_LUA` at
`models/session_lifecycle.py:1300`) so a successor that already took the lease
is untouched, and best-effort/exception-swallowed like the release at `:377-390`
— a failure to clear must never block the exit.

**Shape (a)'s residual, sized honestly rather than waved at.** Clearing on a
*deliberate* exit does nothing for a heartbeat that dies without running its
exit path — SIGKILL, OOM-kill, an unhandled exception outside the tick's
handler, power loss. Spike-5 row Ib measures that state as still reading dead.
The size of the residual is the size of the conjunction it requires:

1. The heartbeat dies **without** executing either non-releasing return and
   without the supervisor-death release; **and**
2. that death **spares the run**; **and**
3. the run then goes ≥ `GRACE` (180 s) with zero `sdlc-tool` lock writes.

Condition (2) is the narrow one, and it is narrow in the direction that helps.
The heartbeat is spawned `start_new_session=True`, so it *survives* a kill of
the supervisor's process tree — the deaths that take the heartbeat out are
overwhelmingly machine-scoped (reboot, sleep, power loss, an operator's kill
sweep), and every one of those kills the run too, which makes the DEAD verdict
**correct** and is precisely the #2648 incident. The residual is the strict
subset where something kills the detached heartbeat *alone*: an OOM-kill of a
small sleeping loop, or an operator killing one pid by hand. In worker mode the
residual does not exist at all — `_maybe_launch_lease_heartbeat` is skipped
under `VALOR_WORKER_MODE`, the durable renewer is the worker itself, and if the
worker dies the run (its subprocess) dies with it.

So the systematic false-dead generator (a deadline exit, which happens on
*every* lane that outlives 90 min) is removed; what remains is an
anomaly-conditioned one. Its backstops are unchanged: the lease's 1800 s TTL,
#2784's release where the heartbeat is alive to perform it, and the fact that
any single `sdlc-tool` write resets the grace clock. This is a **documented,
accepted residual**, recorded as Risk 5 rather than closed.

**Why shape (b) was rejected, and why (c) is not "free insurance".**
Adding an idle-time conjunct at the reaper's payload-present path was the other
candidate. Spike-5 measured its coverage: it protects the reaper and leaves
*both* the peek path and `_lock_says_live` returning `False`, so the worst
consumer — the autonomous reflection lane — stays fully exposed. And it is not
cost-free: its clock is `_last_activity_at` → `updated_at`, which #2305 removed
from the payload-present path on purpose because probes and sibling
`session-ensure` renewals refresh it (the "liveness mirage"). Re-adding it as a
conjunct means a genuinely dead anchor with a probe-refreshed `updated_at`
never reaps, re-opening #2703 defect 1 from the other side. Taking (c) = (a)+(b)
would buy the reaper a partial guard against row Ib while paying that revert in
full and still leaving the reflection gate exposed. (a) alone is the decision.

**Why the four-file grep is necessary but NOT sufficient — the restriction is
made structural.** The anti-criterion greps four ephemeral-renewer files, but
renewal-adjacent call sites already exist outside that set:
`tools/sdlc_session_release.py:92`, `tools/merge_predicate.py:656`, and
`agent/supervised_run.py:256`. A future caller can therefore reach the renewal
path without touching any watched file, and the grep would stay green while the
field is re-poisoned by an ephemeral process (Risk 2). The plan therefore adds
a runtime allowlist **inside the renewal path itself**: a module-level
`_DURABLE_RENEWER_MODULES = {"tools.sdlc_lease_heartbeat",
"agent.session_executor"}` checked against the calling module's `__name__`. A
disallowed caller passing the flag gets the stamp **silently dropped** plus one
`logger.warning` — never an exception, because nothing in this path may be
allowed to break lock acquisition. The grep row stays as the cheap
build-time signal; the allowlist is the one that holds at runtime, and it is
directly testable (call from a disallowed module, assert no `renewer_*` keys).

**Why `renewer_machine_id` is dropped (round-2 BLOCKER).**
The first draft stamped a third key, `renewer_machine_id`, and gated the
tightened branch on it. It is both redundant and unsafe:

- **Redundant.** `_healed_renewal_payload`'s own docstring
  (`models/session_lifecycle.py:1112-1118`) records that renewal requires a
  `run_id` match and that run_ids are minted on exactly one machine; both
  renewal branches enforce that match (`:1289` CAS, `:1369` default). The
  renewer is therefore *always* on the owner's machine, and the payload already
  carries the owner's `machine_id`. A second machine field adds no information.
- **Unsafe.** `_local_machine_id()` returns `""`, not `None`, when unresolvable
  (`:924-936`). The "write all three or none" rule only covered a `None`
  create_time, so on a host that cannot identify itself the group would be
  written with an empty id and a naive equality gate would read `"" == ""` as
  *same machine* — a false-dead on exactly the indeterminate evidence
  `_payload_from_same_machine` is careful to reject (`:957` guards with
  `if payload_machine_id and local_machine_id:` before comparing, and falls
  back to hostname otherwise).

The tightened branch therefore gates on the existing
`_payload_from_same_machine(payload)`, which already has both the truthiness
guard and the legacy hostname fallback. The write rule collapses to **"write
both or neither"**: stamp only when `_current_process_create_time()` is not
`None`; a pid without a create_time is no identity, mirroring
`_resolved_supervisor`'s rule.

**Why a grace window, and why 180 s.**

The tightened branch introduces one genuinely new false-dead risk that neither
prior issue faced: a payload whose recorded renewer has been *replaced* rather
than lost. The concrete case is a worker restart
(`./scripts/valor-service.sh restart`) — the old worker pid is dead, the new
worker will re-stamp on its next 60 s tick, and in between the payload names a
dead renewer for a perfectly live run. Without a grace window a rival peeking in
that gap reads `orphaned_lock: true` against a running pipeline, which is the
#1915 duplicate-PR shape #2620 was fixing.

`ISSUE_LOCK_RENEWER_GRACE_SECONDS` bounds that gap. Sizing:

- **Lower bound:** must exceed a worker restart plus one renewal tick. The
  worker ticks every 60 s, so three ticks (180 s) is a comfortable floor.
- **Upper bound:** must be *below* the age at which the recorded incident was
  inspected (210 s), or the fix does not catch the case it was written for.
  Spike-2 confirms 300 s regresses row B; 180 s does not.
- **The cadence objection, narrowed to what actually holds.** It is tempting to
  argue the grace must exceed the heartbeat's `TTL//3` (600 s) renewal cadence.
  That is wrong *while the renewer pid resolves alive* — a heartbeat sleeping
  between ticks is a live process, and the branch returns `True` without ever
  consulting the clock. Cadence would only matter if the renewer's pid changed
  every tick, which it does not. But the narrower true statement matters: **once
  the renewer pid resolves dead, `renewed_at` recency is the binding signal**,
  and `renewed_at` is refreshed by every renewer including the ephemeral ones.
  The earlier blanket phrasing ("the check is on the pid, not on tick recency")
  overstated this and is corrected here.

**The grace window is TTL-INDEPENDENT BY DESIGN.** Unlike
`ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS` (defined as `2 * (TTL // 3)`, so it
tracks the TTL automatically), `ISSUE_LOCK_RENEWER_GRACE_SECONDS` is a flat
literal that does **not** derive from `ISSUE_LOCK_TTL_SECONDS`. That is
deliberate: the quantity it bounds is *how long a dead renewer pid is tolerated*
— sized against the worker's fixed 60 s restart tick and a one-off incident
observation, neither of which moves when the TTL moves. Deriving it from the
TTL would couple it to a number that has nothing to do with process restart
time. The cost of that independence is stated plainly in the next paragraph.

**Two honest caveats on the number itself.**

- **Its ceiling rests on n = 1.** The 210 s upper bound is a *single* incident
  observation — the age at which the #2648 lease happened to be inspected. It
  is not a distribution, not a p99, and not evidence that a dead renewer is
  typically noticed at that age. It is the only datum available, and 180 s is
  the largest round value below it. Treat the constant as provisional in the
  strong sense: the first additional incident observation should re-derive it,
  and it is env-overridable specifically so a machine can move it without a
  code change.
- **At a lowered TTL the tightening buys almost nothing.** Because the grace is
  TTL-independent while the freshness window is not, lowering
  `ISSUE_LOCK_TTL_SECONDS` to 300 s puts freshness at `2 * (300 // 3)` = 200 s,
  against a 180 s grace — the tightened branch would then have a 20 s window in
  which it can fire before freshness lapses and the untouched pid path takes
  over anyway. The fix is effectively inert at that TTL. This is acceptable
  because it is *harmless* (a narrower window fails toward today's behavior,
  never toward a new false-dead) and because the default TTL is 1800 s and the
  catalog records no live machine running a lowered value. An operator lowering
  the TTL that far should lower the grace proportionally; the entry added to
  `docs/features/config-timeout-catalog.md` says so explicitly.

Net effect at the default TTL: the blind window for a dead durable renewer
collapses from 1200 s to ≤180 s of quiet, and to ~0 s of *additional* exposure
beyond what the grace deliberately buys.

**Constant placement — deviation from the `TimeoutSettings` default, argued.**
`docs/features/config-timeout-catalog.md`'s promote-vs-name-locally criterion
would nominally promote a session-lifecycle TTL to `settings.timeouts.*`. This
one is deliberately **named locally**, immediately beside
`ISSUE_LOCK_TTL_SECONDS` (`:847`) and `ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS`
(`:859`), following the same shape: module-level
`int(os.environ.get("ISSUE_LOCK_RENEWER_GRACE_SECONDS", "180"))` with a GRAIN OF
SALT comment marking it provisional and tunable. Rationale: the three constants
are one semantic family read in one predicate, and promoting a lone new member
while its two siblings stay module-local would *create* the split, undiscoverable
knob the catalog exists to eliminate. The requirement the directive actually
enforces — named, env-overridable, provisional-marked — is satisfied. Promoting
all three as a paired field set is a separate, larger change and is a **No-Go**
here.

**Where the stamp is NOT written, and why that is acceptable.** The worker's
call at `agent/session_executor.py:246` uses the mint-capable default branch; on
a lapsed key it does `SET NX` and the fresh payload's own `pid` is already the
worker's durable pid, but no `renewer_*` keys. That payload keeps today's
behavior for at most one 60 s tick, after which the renewal branch stamps. This
is a documented, bounded residual, not a gap to paper over on the mint path
(which would duplicate the identity in two places).

**Residual this fix does NOT close.** If `_maybe_launch_lease_heartbeat` fails
to spawn entirely (best-effort, swallowed), a local run's lease is only ever
touched by ephemeral CLI renewers, never gains a `renewer_pid`, and keeps the
full 1200 s blind window. Backstops: the lease TTL (1800 s) and #2784's release
where a heartbeat does exist. Making heartbeat-expected a payload assertion is a
**Rabbit Hole**, explicitly out of scope.

## Failure Path Test Strategy

### Exception Handling Coverage

- `psutil` raising inside the renewer-pid check must be caught by the existing
  blanket handler and read as LIVE (never crash the predicate, never hard-kill
  on unverifiable evidence). Covered by a test that makes
  `_psutil_process_for_pid` raise.
- `_current_process_create_time()` returning `None` inside a durable renewal
  must produce a **partial** identity that the predicate treats as *no* identity
  (fail-toward-live), mirroring `_resolved_supervisor`'s "a pid without a
  create_time is no identity" rule.

### Empty/Invalid Input Handling

- `renewer_pid` present but `renewer_create_time` absent → partial identity →
  short-circuit as today.
- `renewer_pid` non-numeric / zero / negative → treated as absent.
- `renewer_machine_id` naming a different machine → cross-machine →
  fail-toward-live.
- `renewed_at` unparseable → existing `None` path, pid fall-through unchanged.

### Error State Rendering

No user-facing rendering changes. `orphaned_lock` keeps its exact meaning and
JSON shape (`ISSUE_LOCKED` payload in `sdlc-tool next-skill` / `session-ensure`);
only the *accuracy* of the flag improves. No new CLI output, no new log format
beyond one debug line naming the dead renewer pid.

## Test Impact

- [ ] `tests/unit/test_session_lifecycle.py::test_peek_dead_pid_with_fresh_renewal_is_not_orphaned` (`:1219`) — KEEP UNCHANGED: its payload carries no `renewer_pid`, so it must still assert `orphaned_lock is False`. This is the #2620 regression guard and the primary proof the tightening is gated.
- [ ] `tests/unit/test_session_lifecycle.py::test_peek_dead_pid_with_stale_renewal_is_orphaned` (`:1249`) — KEEP UNCHANGED.
- [ ] `tests/unit/test_session_lifecycle.py::test_malformed_renewed_at_falls_back_to_pid_inference` (`:1279`) — KEEP UNCHANGED.
- [ ] `tests/unit/test_session_lifecycle.py::test_same_owner_renewal_restamps_renewed_at` (`:1310`) — UPDATE: add an assertion that a renewal WITHOUT `stamp_renewer_identity` writes no `renewer_*` keys. The existing "identity fields survive" assertions at `:1331-1333` stay.
- [ ] `tests/unit/test_session_lifecycle.py::test_renewal_preserves_pid_and_hostname_from_original_payload` (`:1456`) — KEEP UNCHANGED.
- [ ] `tests/unit/test_session_lifecycle.py` `TestLockOwnerIsLive` rows at `:576`, `:586`, `:598`, `:610`, `:629`, `:650` — KEEP UNCHANGED: no `renewer_pid` in any payload, so all must stay green byte-for-byte. Running them unmodified IS the backward-compatibility proof.
- [ ] `tests/unit/test_sdlc_session_ensure.py` (orphan-reaper tests, if present for `_iter_orphan_sessions`) — VERIFY UNCHANGED: locate and run; a live-owner exemption test must not change behavior.

No xfail markers (decorator or runtime `pytest.xfail()`) exist in `tests/`
relating to the issue lock, so there are none to convert.

## Rabbit Holes

- **Recording "a heartbeat was expected" on the payload** so the *absence* of a
  `renewer_pid` becomes evidence of death. This would close the last residual
  but requires reasoning about a best-effort spawn's success, and mis-sizing it
  turns every spawn hiccup into a false-dead. Out of scope.
- **Respawning a dead heartbeat.** Tempting once its death is detectable, but it
  is a lifecycle change to `/do-sdlc`, not a liveness-predicate fix.
- **Splitting the predicate per consumer** (a strict version for the peek, a
  lenient one for the reaper). Two predicates over one payload is exactly the
  drift that produced this issue. Rejected in **No-Gos**.
- **Re-tuning `ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS` itself.** With a durable
  renewer identity present the constant stops being the binding signal; shrinking
  it would change behavior for every legacy payload too. Leave it.
- **Migrating the three `ISSUE_LOCK_*` constants into `TimeoutSettings`.**
  Separate change, separate justification per field.

## Risks

### Risk 1: The tightened branch turns a live run dead (false-dead)

A false-dead verdict is the expensive direction: it lets a rival treat a live
owner as a ghost, which is the #1915 duplicate-PR shape, and it makes an
`sdlc-local-{N}` anchor reapable mid-pipeline (#2703 defect 1).

**Mitigation.** Three independent gates, each verified by spike-2: the branch
fires only when a *complete* renewer identity is present (partial → today's
behavior); only when same-machine (cross-machine → fail-toward-live); and only
after `ISSUE_LOCK_RENEWER_GRACE_SECONDS` of dead-renewer evidence. Plus the
existing blanket `except -> True`. The demonstrated-red test suite asserts all
three gates in the *live* direction, not only the dead one.

### Risk 2: An ephemeral CLI renewer acquires the stamp later and re-poisons the field

If a future edit adds `stamp_renewer_identity=True` to one of the five
short-lived CLI renewers, the payload would carry a pid that is dead within
seconds and the predicate would report every lease orphaned after the grace —
strictly worse than today.

**Mitigation.** An anti-criterion row in **Verification** mechanically asserts
the flag appears at exactly the two durable call sites, plus its definition and
plumbing, and nowhere else. Demonstrated red against a deliberately-violating
edit before it is trusted.

### Risk 3: The grace window is mis-sized for a machine with a slower renewer

A machine that has raised `ISSUE_LOCK_TTL_SECONDS` (and therefore the heartbeat's
`TTL//3` cadence) does not need a larger grace — the check is on pid liveness,
not tick recency (argued above) — but a machine with an unusually slow worker
restart could exceed 180 s.

**Mitigation.** The constant is env-overridable
(`ISSUE_LOCK_RENEWER_GRACE_SECONDS`) and explicitly marked provisional/tunable
at its definition, per the repo's magic-number convention. The failure mode of
an under-sized grace is a transient false-dead *peek*, which reports rather than
steals.

### Risk 4: Redis payload growth / forward compatibility

Three new keys on a JSON string value. Negligible size; and an older process
reading a newer payload simply ignores unknown keys (the predicate reads by
`.get`, the renewal branch spreads `{**payload}`), so a mixed-version machine is
safe in both directions.

## Race Conditions

### Race 1: Renewer dies between the stamp and the peek

Inherent and intended — this is the defect being fixed. The grace window is the
explicit bound on how long that state is tolerated. No locking needed: the
payload write is already a compare-and-set (`_RENEW_IF_VALUE_MATCHES_LUA`, #2714
L0) on the `renew_only` path and a same-run_id-verified read-compare-write within
one function invocation on the default path.

### Race 2: Two durable renewers stamping the same lease

Possible in principle if a worker-driven session also had a detached heartbeat.
It cannot happen: `_maybe_launch_lease_heartbeat` is skipped under
`VALOR_WORKER_MODE`. Even if it did, both writes are same-owner idempotent
extends and either identity is a genuinely live durable renewer, so the
predicate's verdict is correct under both.

### Race 3: Worker restart between stamp and peek

Covered by the grace window (spike-2 row G). The new worker re-stamps within one
60 s tick.

### Race 4: pid recycling of a dead renewer

The OS reassigns the dead renewer's pid to an unrelated process, which would read
alive. Closed by carrying `renewer_create_time` and applying the same `1e-3`
create_time comparison the existing pid path uses.

## No-Gos (Out of Scope)

- **Do NOT make freshness corroborating unconditionally.** Only when a complete,
  same-machine durable renewer identity is present. Unconditional tightening
  re-opens #2703 defect 1 and reverts #2620.
- **Do NOT remove or weaken the `fresh -> live` short-circuit for payloads
  without a renewer identity.** That path must stay byte-for-byte as it is.
- **Do NOT pass `stamp_renewer_identity=True` from any short-lived CLI
  renewer** (`tools/_sdlc_utils.py`, `tools/sdlc_stage_marker.py`,
  `tools/sdlc_next_skill.py`, `tools/sdlc_session_ensure.py`).
- **Do NOT fork `_lock_owner_is_live` into per-consumer variants.**
- **Do NOT touch the `/do-sdlc` fork-acquisition surface (#2026/#2076),
  `release_issue_lock` reachability, or the heartbeat's supervisor-death
  release (#2784).** All mooted by the accepted re-scope.
- **Do NOT change `ISSUE_LOCK_TTL_SECONDS` or
  `ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS`.**
- **Do NOT add a Popoto migration.** The lock is a plain string key.
- **Do NOT delete or rewrite the existing `TestLockOwnerIsLive` /
  `test_peek_dead_pid_with_fresh_renewal_is_not_orphaned` tests to accommodate
  the change.** They are the backward-compatibility proof; if they need editing,
  the design is wrong.

## Update System

No update-system changes required. This is an in-process predicate and payload
change with no new dependency, config file, entry point, or service. Deployment
is the normal `/update` git pull plus `./scripts/valor-service.sh restart` (the
worker imports `session_executor`), which the standard post-merge `/update` step
already performs. Live lease payloads self-heal on their next durable renewal —
no backfill, and a payload that never gains the field simply keeps today's
behavior.

## Agent Integration

No agent integration required. This is internal to `models/session_lifecycle.py`
and its two durable renewers; the agent reaches it only through the existing
`sdlc-tool` entry points (`session-ensure`, `next-skill`, `stage-marker`), whose
CLI surface, flags, and JSON shapes are unchanged. `orphaned_lock` keeps its
meaning and position in the `ISSUE_LOCKED` payload — only its accuracy improves.
No new `pyproject.toml [project.scripts]` entry, no bridge import.

## Documentation

- [ ] Update `docs/features/sdlc-issue-ownership-lock.md`: add `renewer_pid` /
      `renewer_create_time` / `renewer_machine_id` to the payload example
      (around `:79`) and its field description (`:82`); rewrite the "Renewal
      freshness short-circuit" bullet (`:215`) and the "Renewal freshness, not
      pid liveness" section (`:221-249`) to describe freshness as *corroborating
      when a durable renewer identity is present, conclusive otherwise*;
      document `ISSUE_LOCK_RENEWER_GRACE_SECONDS` beside the existing constants
      paragraph (`:120`).
- [ ] Update `docs/features/sdlc-issue-ownership-lock.md`'s `--kill-orphans`
      section (`:268-279`) to state explicitly that the reaper's exemption is
      preserved because the tightened branch is gated on evidence only a live
      durable renewer produces.
- [ ] Update `.claude/skills/sdlc/SKILL.md` (the ISSUE_LOCKED paragraph around
      `:202`) so the `orphaned_lock` contract matches: a dead durable renewer is
      now reported orphaned within the grace window rather than after the full
      freshness window.
- [ ] Update `tools/sdlc_lease_heartbeat.py`'s module docstring (`:33-42`),
      which currently asserts the payload pid "is dead before this detached
      heartbeat's first tick" — still true of `pid`, now false of
      `renewer_pid`, and the distinction is the whole point.
- [ ] Inline: docstrings on `_healed_renewal_payload`, `_lock_owner_is_live`,
      and the new constant must carry the #2648 rationale and the GRAIN OF SALT
      note, matching the existing house style in that module.

## Success Criteria

1. **Red half (a).** A payload with a fresh `renewed_at`, a `renewer_pid` that
   psutil cannot find, and a `renewed_at` older than the grace window reads
   `_lock_owner_is_live is False` — with a test that is **red on `main`** and
   green after the fix, both runs captured in the PR body.
2. The same payload with the renewer pid ALIVE reads `True`.
3. The same payload inside the grace window reads `True`.
4. **Green-both half (b).** A payload with no durable renewer identity reads
   exactly as it does today: fresh-in-window still reads LIVE. This case must be
   run and shown **GREEN before the fix AND green after it** — a single
   post-fix green does not discharge it, because the risk being ruled out is
   precisely that the tightening ate the #2703/#2620 fallback. At suite level
   the same obligation is: all 14 pre-existing lock-liveness tests green,
   unmodified, on both sides of the change. Criteria 1 and 4 are one pair of
   evidence, not two independent checks; a build that produces only one of them
   has not demonstrated red.
5. `tools/sdlc_lease_heartbeat.py` and `agent/session_executor.py` are the ONLY
   call sites passing `stamp_renewer_identity=True` (anti-criterion).
6. `ISSUE_LOCK_RENEWER_GRACE_SECONDS` is a named, env-overridable, GRAIN OF
   SALT-annotated module constant beside its two siblings.
7. `--kill-orphans` still exempts a live-but-quiet `sdlc-local-{N}` anchor whose
   heartbeat is alive (explicit test at the `_iter_orphan_sessions` layer, not
   only at the predicate layer).
8. Docs listed above updated; `ruff check` and `ruff format --check` clean.

## Team Orchestration

Single builder. The change is four files and one test module; splitting it would
cost more in handoff than it saves. `/do-plan-critique` runs the war room before
build, as the disposition comment on #2648 explicitly requested ("it wants a plan
and a critique round, not a same-day PR").

### Team Members

| Role | Agent Type | Responsibility |
|------|-----------|----------------|
| Builder | `builder` | All five task steps, sequentially |
| Critic | `plan-reviewer` (via `/do-plan-critique`) | Pre-build war room on this document |

## Step by Step Tasks

### 1. Capture the demonstrated red baseline

**PATCH-LOCATION TRAP — read before writing a single test.**
`_lock_owner_is_live` does NOT import the pid helper at module scope. It does a
**lazy, function-body import**: `from agent.session_health import
_psutil_process_for_pid`, executed on every call. Consequences a builder will
hit immediately:

- The only patch target that works is
  `patch("agent.session_health._psutil_process_for_pid", ...)`.
- `patch("models.session_lifecycle._psutil_process_for_pid", ...)` raises
  `AttributeError` — the name does not exist in that module's namespace. It is
  not a silently-ineffective patch; the test errors out.
- **The #2648 issue body's forensics predate the move and will mislead anyone
  who copies them verbatim.** Do not lift patch targets from the issue.
- The same applies to the sibling helpers the predicate reaches lazily; confirm
  the import site by reading the function body rather than assuming module
  scope. Every pre-existing test in `tests/unit/test_session_lifecycle.py`'s
  `TestLockOwnerIsLive` already uses the `agent.session_health` target — copy
  the patch stanza from there, not from the issue.

Now the work:

- Add `tests/unit/test_session_lifecycle.py::TestRenewerIdentityLiveness` with,
  at minimum: dead-renewer-past-grace (must be RED on `main`),
  live-renewer (green both), dead-renewer-inside-grace (green both),
  no-renewer-identity (green both, the #2620 guard),
  partial-identity (a renewer pid recorded without its create_time),
  cross-machine renewer, recycled renewer pid (create_time mismatch), and
  psutil-raises.
- **The demonstrated-red evidence is a PAIR, and both halves are required.**
  Neither one alone proves the change is correct:
  - **(a) The tightening works.** A stamp-then-die payload that DOES carry a
    durable renewer identity, past the grace window, must read DEAD. This half
    is **RED on `main`** and green after the fix. Capture the failure output
    verbatim.
  - **(b) The #2703 protection survives.** The fallback payload — fresh
    `renewed_at`, NO durable renewer identity — must still read LIVE inside the
    freshness window. This half must be shown **GREEN both before AND after**
    the fix, run twice and both runs pasted into the PR body. A half-(b) that
    is only run after the fix proves nothing: the whole risk is that the
    tightening quietly ate the fallback, and only a before/after pair rules
    that out. Success Criterion 4's "unmodified pre-existing tests stay green"
    is the same obligation stated at suite level; this is the explicit
    per-case form of it.
- Run the new class against `main` BEFORE touching source. The (a) case MUST
  fail; if every case passes on `main`, the test does not construct the
  stamp-then-die state and must be rewritten.
- Do not use `pytest` bare — `scripts/pytest-clean.sh` only.

### 2. Add the constant and the payload stamping

- Define `ISSUE_LOCK_RENEWER_GRACE_SECONDS` in `models/session_lifecycle.py`
  immediately after `ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS` (`:859`), as
  `int(os.environ.get("ISSUE_LOCK_RENEWER_GRACE_SECONDS", "180"))` with a GRAIN
  OF SALT comment recording the 180 s sizing argument (three worker ticks; below
  the 210 s incident-observation age).
- Extend `_healed_renewal_payload(payload, target_repo, *, stamp_renewer_identity=False)`
  to write `renewer_pid` (`os.getpid()`), `renewer_create_time`
  (`_current_process_create_time()`), and `renewer_machine_id`
  (`_local_machine_id()`) when the flag is set. Write all three or none: a
  `None` create_time must leave the whole group unwritten (partial identity is
  no identity).
- Add keyword-only `stamp_renewer_identity: bool = False` to `touch_issue_lock`
  and plumb it to BOTH renewal branches (`:1296` renew_only CAS, `:1378`
  default).

### 3. Tighten `_lock_owner_is_live`

- Replace the bare short-circuit at `:1033` with the branch specified in
  **Solution → Flow**. Keep every line below the fall-through untouched.
- Reuse `_payload_from_same_machine`-equivalent logic against
  `renewer_machine_id`, and the existing `1e-3` create_time tolerance.
- Wrap the renewer psutil call so any exception reads LIVE, matching the
  existing handler's posture; log at debug naming the dead renewer pid.
- Run the new test class: the previously-red case must now be green.

### 4. Wire the two durable renewers

- `tools/sdlc_lease_heartbeat.py:429` — add `stamp_renewer_identity=True` to the
  `renew_only=True` extend.
- `agent/session_executor.py:246` — add `stamp_renewer_identity=True`.
- Confirm by grep that no other call site carries the flag (Verification row 5).
- **Do not name the flag, even in a comment, inside the four ephemeral-renewer
  files** (`tools/_sdlc_utils.py`, `tools/sdlc_stage_marker.py`,
  `tools/sdlc_next_skill.py`, `tools/sdlc_session_ensure.py`). The
  anti-criterion row greps exactly those four files for the token, so a
  well-meant "deliberately does not opt in here" comment tallies as a
  violation and turns a correct build red. Paraphrase instead — e.g. "renews
  without recording a durable renewer identity (see #2648)" — and put the
  rationale in the `touch_issue_lock` docstring, which the row does not grep.

### 5. Prove the reaper is unharmed

- Add a test at the `tools/sdlc_session_ensure.py::_iter_orphan_sessions` layer:
  an `sdlc-local-{N}` row with `last_heartbeat_at = None` whose lock payload
  carries a fresh stamp and a LIVE `renewer_pid` must NOT be yielded; the same
  row with a dead `renewer_pid` past the grace MUST be yielded.
- Run the full pre-existing lock-liveness set unmodified
  (`scripts/pytest-clean.sh tests/unit/test_session_lifecycle.py -q`) and
  confirm all green.

### 6. Documentation

- Execute every checkbox in **## Documentation**.

### 7. Final Validation

- `scripts/pytest-clean.sh tests/unit/test_session_lifecycle.py tests/unit/test_sdlc_session_ensure.py -q`
- `python -m ruff check .` and `python -m ruff format --check .`
- Run every row in **## Verification**.
- Paste the full demonstrated-red PAIR into the PR body: half (a) red on `main`
  then green after, and half (b) green on `main` AND green after. Four runs,
  four pasted outputs. A PR carrying only the (a) red/green pair is incomplete.

## Verification

Verification commands enumerate roots explicitly (`models/ tools/ agent/
tests/ docs/ .claude/`) rather than anchoring on `^./` — `ugrep` on this machine
prints no `./` prefix and such an anchor silently matches nothing.

**Anti-criterion scoping — the row must never count its own prose.** The
"no ephemeral CLI renewer stamps" row greps an explicit four-file list, not a
recursive root. That is deliberate and must stay that way:

- This plan document, the PR description, and the test module all legitimately
  name the flag; none of them is in the row's file list, so none is tallied.
  Do NOT widen the row to `grep -r ... tools/` — that would sweep the
  `touch_issue_lock` docstring, the heartbeat's own opt-in, and every future
  comment, and the row would report a violation against a correct build.
- Inside the four listed files the token must not appear at all, in code OR in
  a comment. Prose there must paraphrase (see Task 4). This is the standing
  trap where a comment quoting the very string a gate forbids fails the gate.
- The two "opts in" rows above are positive checks over the two durable
  renewers only, so they cannot collide with the anti-criterion's file set.

| Check | Command | Expected |
|-------|---------|----------|
| Lock-liveness tests pass | `scripts/pytest-clean.sh tests/unit/test_session_lifecycle.py -q` | exit code 0 |
| Orphan-reaper tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_session_ensure.py -q` | exit code 0 |
| Lint clean | `python -m ruff check models/ tools/ agent/ tests/` | exit code 0 |
| Format clean | `python -m ruff format --check models/ tools/ agent/ tests/` | exit code 0 |
| Grace constant is named and env-overridable | `grep -c 'ISSUE_LOCK_RENEWER_GRACE_SECONDS = int(os.environ.get("ISSUE_LOCK_RENEWER_GRACE_SECONDS"' models/session_lifecycle.py` | output contains 1 |
| Grace constant marked provisional | `grep -c 'GRAIN OF SALT' models/session_lifecycle.py` | output > 1 |
| Renewer identity reaches the payload | `grep -c 'renewer_pid' models/session_lifecycle.py` | output > 1 |
| Both durable renewers opt in | `grep -rl 'stamp_renewer_identity=True' tools/ agent/ \| sort` | output contains agent/session_executor.py |
| Heartbeat opts in | `grep -c 'stamp_renewer_identity=True' tools/sdlc_lease_heartbeat.py` | output contains 1 |
| **Anti-criterion:** no ephemeral CLI renewer stamps | `grep -c 'stamp_renewer_identity' tools/_sdlc_utils.py tools/sdlc_stage_marker.py tools/sdlc_next_skill.py tools/sdlc_session_ensure.py` | match count == 0 |
| **Anti-criterion:** the #2620 short-circuit still exists for identity-less payloads | `grep -c 'test_peek_dead_pid_with_fresh_renewal_is_not_orphaned' tests/unit/test_session_lifecycle.py` | output contains 1 |
| Demonstrated-red test exists | `grep -c 'class TestRenewerIdentityLiveness' tests/unit/test_session_lifecycle.py` | output contains 1 |
| Feature doc updated | `grep -c 'renewer_pid' docs/features/sdlc-issue-ownership-lock.md` | output > 1 |
| SDLC skill contract updated | `grep -c 'ISSUE_LOCK_RENEWER_GRACE_SECONDS' .claude/skills/sdlc/SKILL.md` | output contains 1 |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_session_lifecycle.py` | exit code 1 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness; History & Consistency | Blast radius is enumerated as two consumers but there are three. Data Flow and Architectural Impact assert `_lock_owner_is_live` is 'shared by the peek path and the orphan reaper'; `reflections/utilities.py::_lock_says_live` (:277-302) is a third wrapper, read by `reflections/sdlc_upvote_lanes.py:529,573` (gate 3) and `reflections/sdlc_progress.py:571,942,981`. At sdlc_upvote_lanes.py:530 only a `False` verdict lets the reflection proceed to create_session, so a false-dead starts an autonomous rival SDLC lane on a live run (the #1915 shape, unattended). Risk 1, Success Criteria and Test Impact never mention this layer. **Suggestion:** Correct Data Flow + Architectural Impact to list all three consumers; extend the Risk 1 per-consumer argument to the reflection gate; add a Success Criterion and a test at the `_lock_says_live` layer mirroring SC7. | pending | `_lock_says_live` returns `bool \| None`; callers fail closed on `None` but ACT on `False`. Every new early-return in the tightened branch must be `True` (fail-toward-live); only the full conjunction (renewer identity complete AND same-machine AND pid dead/recycled AND renewed_at age > GRACE) may return `False`. Test: patch `reflections.utilities._get_redis` to return a dead-renewer-past-grace payload and assert `_lock_says_live(N) is False`; live-renewer and inside-grace payloads must return `True`. |
| BLOCKER | Risk & Robustness; Scope & Value | `renewer_machine_id` is both redundant and unsafe. `_healed_renewal_payload`'s own docstring (models/session_lifecycle.py:1112-1118) states renewal requires a run_id match and run_ids are minted on exactly one machine, so the renewer is always on the owner's machine; both renewal branches enforce that match (:1289 CAS, :1369 default). Meanwhile `_local_machine_id()` returns `""` (not None) when unresolvable (:924-936), and the plan's 'write all three or none' rule only covers a None create_time -- so a naive equality gate makes `"" == ""` read as same-machine on a host that cannot identify itself, a false-dead on exactly the indeterminate evidence `_payload_from_same_machine` is careful to reject. Two critics flagged the same component, so this is elevated. **Suggestion:** Drop `renewer_machine_id`; stamp only `renewer_pid` + `renewer_create_time` and gate the tightened branch on the existing `_payload_from_same_machine(payload)`. If the key is kept, treat a falsy machine id on either side as incomplete identity and do not write the group at all. | pending | Gate becomes `if renewer_pid and renewer_create_time is not None and _payload_from_same_machine(payload):`. That helper (:939-959) already falls back to `payload['hostname'] == socket.gethostname()`, which the renewer group has no equivalent for. If kept, mirror the truthiness guard at :957 (`if payload_machine_id and local_machine_id:`), never a bare `==`, and gate the write as `if stamp_renewer_identity and (ct := _current_process_create_time()) is not None and (mid := _local_machine_id()):`. |
| BLOCKER | Risk & Robustness (round 2) | The composition claim has a counterexample: a live run whose heartbeat exited WITHOUT releasing. The plan asserts 'There is no payload shape reachable by a live-but-quiet local run that the tightened branch turns dead', but tools/sdlc_lease_heartbeat.py has two deadline exits that deliberately do not release -- `EXIT_UNSUPERVISED_MAX_LIFETIME` at :351-365 (UNSUPERVISED_MAX_LIFETIME_SECONDS, 90 min, :156; its comment reads 'Deliberately NO release -- failing to resolve a supervisor is not positive proof the run is dead') and `EXIT_MAX_LIFETIME` at :365 (MAX_LIFETIME_SECONDS, 4 h, :146). Only EXIT_SUPERVISOR_DEAD (:372-397) releases, and only on confirmed supervisor death (#2784). So: run alive, heartbeat ran (payload therefore DOES carry renewer_pid), heartbeat exits at its bound, supervisor blocked inside a claude -p stage issuing zero sdlc-tool writes (docs/features/config-timeout-catalog.md:85-87) -- renewed_at ages with a dead renewer_pid, complete identity, same machine, and at 180 s the tightened branch returns False. tools/sdlc_session_ensure.py:1128 is `if not owner_live: yield s` with NO idle-time gate when a payload is present, so --kill-orphans reaps the live sdlc-local-{N} anchor immediately, well inside a 6-25 min stage (today it reads live for the full 1200 s window). #2703 defect 1 is re-opened by the composition, not preserved by it. Spike-2's eight rows contain no 'renewer exited normally, run still alive' entry -- rows B/C/G all presuppose the renewer died because the run died or is restarting. **Suggestion:** Add the missing spike-2 row and close it: either treat a heartbeat's non-releasing deadline exit as a payload event (clear the renewer_* group before exiting at :359 and :365, so the payload falls back to the untouched #2620 short-circuit), or gate the tightened branch so it cannot fire against a session the reaper would reap without an idle-time check. Re-run the Risk 1 argument against this shape. | pending | Clearing the group on exit is the cheaper fix and needs no predicate change: at both non-releasing returns, perform one final `touch_issue_lock(issue_number, run_id, renew_only=True)`-shaped write whose payload omits renewer_pid / renewer_create_time / renewer_machine_id. It must be compare-and-set (reuse the _RENEW_IF_VALUE_MATCHES_LUA path at models/session_lifecycle.py:1300) so a successor that already took the lease is untouched, and it must be best-effort/exception-swallowed like the release at :377-390 -- a failure to clear must never block the exit. |
| CONCERN | Risk & Robustness | The grace clock's subject is misstated. Technical Approach argues 'the check is on the renewer's pid, not on tick recency', but Solution -> Flow makes tick recency the second half of the dead branch (`renewed_at age > GRACE -> False`). The clock runs on `renewed_at` age -- re-stamped unconditionally by every same-owner renewal including all five ephemeral CLI renewers -- not on how long the renewer pid has been dead. A builder could reasonably implement the other reading, which needs state the payload does not carry and would change every spike-2 row. **Suggestion:** State the clock explicitly in Solution -> Flow ('age of `renewed_at`, not duration of renewer death') and narrow the cadence argument to what holds: cadence is irrelevant while the renewer pid resolves alive; once it resolves dead, `renewed_at` recency is the binding signal. | pending | The condition is `time.time() - float(payload['renewed_at']) > ISSUE_LOCK_RENEWER_GRACE_SECONDS`, reusing the parse `_lock_renewal_is_fresh` (:962-980) already did. Because the branch is only reached when `_lock_renewal_is_fresh(payload) is True`, `renewed_at` is guaranteed present and float-parseable -- no second try/except needed, and do not re-derive it from a helper that can return None. |
| CONCERN | History & Consistency | Failure Path Test Strategy contradicts Task 3 on exception handling. The former says a psutil raise in the renewer-pid check 'must be caught by the existing blanket handler'; Task 3 says to wrap the new call so any exception reads LIVE. Task 3 is correct: the existing handler is inside the `try:` at models/session_lifecycle.py:1046-1063, BELOW the short-circuit at :1033 where the new branch goes. A builder following the Failure Path wording adds no handler, and the exception escapes `_lock_owner_is_live`. The reaper catches it (tools/sdlc_session_ensure.py:1120-1127) but the peek path at :1249 does not, turning it into an `acquired=False` fail-closed on a lease the caller owns. **Suggestion:** Correct the Failure Path Test Strategy bullet to require the new branch's OWN `try/except Exception -> True` (matching the posture at :1055-1063, not reusing that handler); keep Task 3's wording as the authority. | pending | Structure the branch as `try: proc = _psutil_process_for_pid(renewer_pid) ... except Exception: return True` before any `return False` is reachable. Do NOT move the new branch inside the `try:` at :1046 -- that block is entered only after the pid / machine_id / create_time guards at :1035-1045, which a renewer-only payload need not satisfy. |
| CONCERN | History & Consistency (round 2) | Task 1's PATCH-LOCATION TRAP is correct today but the plan does not protect it against its own Task 3. The trap tells the builder that `patch("agent.session_health._psutil_process_for_pid")` is the only working target because _lock_owner_is_live does a lazy function-body import at models/session_lifecycle.py:1046. The new branch goes in at :1033, ABOVE that import, so it needs its own access to the helper. If the builder satisfies it by hoisting the import to module scope in models/session_lifecycle.py, the module-level name comes into existence and the plan's own patch guidance silently stops holding for the new TestRenewerIdentityLiveness class. **Suggestion:** Task 3 must explicitly require the new branch to use its own function-body import of _psutil_process_for_pid, matching the existing lazy import, and must forbid hoisting it to module scope. | pending | Write it as `from agent.session_health import _psutil_process_for_pid` INSIDE the new branch's try block, exactly mirroring models/session_lifecycle.py:1046. Do not add a module-scope import in models/session_lifecycle.py: `patch("models.session_lifecycle._psutil_process_for_pid")` currently raises AttributeError and every pre-existing test (tests/unit/test_session_lifecycle.py:564-567) relies on the agent.session_health target being the only one. |
| NIT | Scope & Value | Three statements about the reaper-layer test disagree: Task 5 mandates ADDING two tests at `_iter_orphan_sessions`; Test Impact lists tests/unit/test_sdlc_session_ensure.py only as 'VERIFY UNCHANGED ... if present'; and Verification has no row asserting the new reaper test, unlike SC1/5/6/8. The 'if present' hedge is stale -- `TestKillOrphans` is at tests/unit/test_sdlc_session_ensure.py:1212 and `_iter_orphan_sessions` at tools/sdlc_session_ensure.py:1059. **Suggestion:** Change the Test Impact row to ADD (plus verify the existing TestKillOrphans rows at :1212 unchanged) and add a Verification grep row for the new reaper test's name so SC7 is mechanically checkable. | pending | n/a (NIT) |
| NIT | Scope & Value | Two Verification rows are ineffective. `grep -c 'GRAIN OF SALT' models/session_lifecycle.py \| output > 1` already passes on main (3 occurrences today), so it cannot detect a new constant shipped without its annotation. Separately the row greps .claude/skills/sdlc/SKILL.md for `ISSUE_LOCK_RENEWER_GRACE_SECONDS`, but the matching Documentation checkbox only asks for the orphaned_lock contract wording and never says to name the constant. **Suggestion:** Anchor the provisional row to the new constant (assert the count rises to 4, or grep the constant name inside its GRAIN OF SALT block) and add 'name ISSUE_LOCK_RENEWER_GRACE_SECONDS' to the SKILL.md documentation checkbox. | pending | n/a (NIT) |
| NIT | History & Consistency | The constant-placement argument aims to avoid a 'split, undiscoverable knob', but docs/features/config-timeout-catalog.md:82-92 already catalogs ISSUE_LOCK_TTL_SECONDS with the same 'not TIMEOUTS__*' caveat, and the Documentation section does not add the new sibling there -- producing the discoverability gap the argument claims to prevent. **Suggestion:** Add a Documentation checkbox for docs/features/config-timeout-catalog.md covering ISSUE_LOCK_RENEWER_GRACE_SECONDS (and opportunistically the currently-uncatalogued ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS), with a matching Verification grep row. | pending | n/a (NIT) |

---

## Open Questions

Both questions carry a decided default in the plan body, so neither blocks
build; they are raised for the critique war room to adjudicate rather than
left unresolved.

1. **Grace window default.** 180 s is derived from two constraints (three worker
   ticks below; the 210 s incident observation above). It is the largest value
   that still catches the recorded incident. Is a tighter default (e.g. 120 s)
   preferred, accepting a narrower tolerance for a slow worker restart?
2. **The last residual.** A local run whose heartbeat never spawns keeps the full
   1200 s blind window, bounded by the 1800 s TTL. Closing it needs a
   "heartbeat expected" payload assertion, which is a different design question
   (how to treat a best-effort spawn's success as evidence) and is listed as a
   rabbit hole here. Confirm the TTL backstop is an acceptable disposition for
   that path.
