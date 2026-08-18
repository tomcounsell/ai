---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-17
tracking: https://github.com/tomcounsell/ai/issues/2648
last_comment_id: 5311761287
revision_applied: true
revision_applied_at: 2026-08-18T03:58:32Z
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
  tier-1 60 s tick). Ephemeral **writers — four call sites across three
  files**: `tools/_sdlc_utils.py:609`, `tools/_sdlc_utils.py:834`,
  `tools/sdlc_stage_marker.py:884`, `tools/sdlc_session_ensure.py:539` (the
  mint). Read-only peeks never write and cannot re-poison the field:
  `tools/sdlc_next_skill.py:638` (`peek=True`, corrected at round 5 — an
  earlier draft miscounted it as a fifth renewer), `tools/merge_predicate.py:656`,
  `tools/sdlc_review_finalize.py:351`. `tools/sdlc_next_skill.py` stays in the
  anti-criterion file list as a deliberate defensive inclusion, not as evidence
  of a renewer.
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
- **No new module, no new process.** Three existing source files change
  (`models/session_lifecycle.py`, `tools/sdlc_lease_heartbeat.py`,
  `agent/session_executor.py`), plus three existing test modules and one new
  one (`tests/unit/reflections/test_utilities_lock_says_live.py`).
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

**Medium** — relabelled at round 3, honestly rather than defensively.

The original idea was one mechanism: stamp the durable renewer's pid and gate on
it. Two critique rounds each added a second: round 2 added the CAS
`drop_renewer_identity` clear at both heartbeat deadline exits (Task 5, forced
by the spike-5 composition hole), and round 3 added the stamp/strip invariant
plus an explicit-token allowlist. Three mechanisms across three source files,
with tests in four modules, is not Small, and the earlier defence — "the
reasoning is the expensive part" — stopped being true once the reasoning started
producing mechanisms.

It is still bounded: no new services, no schema, no migration, no new module,
and every mechanism is forced by a measured failure rather than anticipated. But
a builder should size this as Medium and a reviewer should expect three
interacting parts, not one.

## Prerequisites

None. Every file exists on `main` at `335fde5b3`.

## Solution

### Key Elements

0. **The invariant everything else serves (round-3).** *The `renewer_*` group is
   present on the payload if and only if the MOST RECENT renewal was performed
   by a durable renewer.* Every element below exists to keep that biconditional
   true; if a change breaks it, the change is wrong. Stating it as an invariant
   rather than as a sequence of writes is what closes the round-3 re-poisoning
   concern, which arose precisely because the first design treated the clear as
   a one-shot event instead of a maintained property.
1. **`_healed_renewal_payload` gains an opt-in `stamp_renewer_identity` flag.**
   When set, the renewal payload additionally carries **exactly two** new keys:
   `renewer_pid` and `renewer_create_time` — the identity of the process
   performing *this* renewal. **There is no `renewer_machine_id`** (BLOCKER 2,
   argued below).
2. **`_healed_renewal_payload` STRIPS the `renewer_*` group whenever
   `stamp_renewer_identity` is not set for that call** — the other half of the
   invariant, and a deliberate exception to that helper's "spread the EXISTING
   payload, never reconstruct a subset" rule (`:1084-1096`). Without it the
   group is sticky in the wrong direction: the default renewal branch at
   `:1378` is a bare non-CAS `_R.set` of a spread payload, so any of the four
   ephemeral CLI renewers would re-carry a stale `renewer_pid` it read before a
   clear, and silently undo it (round-3 CONCERN). The exception is narrow and
   must be commented as such at the spread site, naming this invariant.
3. **`touch_issue_lock` gains a keyword-only `stamp_renewer_identity: bool =
   False`,** plumbed into both renewal branches (`renew_only` CAS at `:1296` and
   the default same-owner branch at `:1378`).
4. **Exactly two callers pass it,** each also passing an explicit
   `renewer_module` token checked against `_DURABLE_RENEWER_MODULES`:
   `tools/sdlc_lease_heartbeat.py:429` and `agent/session_executor.py:246`. The
   token is passed explicitly and **never derived by introspection** — see the
   round-3 BLOCKER below, where `__name__` is shown to be `"__main__"` for the
   detached heartbeat.
5. **`touch_issue_lock` also gains a keyword-only `drop_renewer_identity: bool
   = False`,** which performs a same-owner CAS renewal writing a payload with
   the `renewer_*` group **removed**. The heartbeat calls it at both of its
   non-releasing deadline exits (spike-5 shape (a)). Element 2 keeps the clear
   durable; this element is what performs it when the heartbeat is the last
   writer, which is exactly the deadline-exit case.
6. **`_lock_owner_is_live` makes freshness *corroborating rather than
   conclusive*, but only when a durable renewer identity is present, complete,
   and the payload is same-machine per the existing
   `_payload_from_same_machine`.** Otherwise the existing short-circuit is
   byte-for-byte unchanged.
7. **New named constant `ISSUE_LOCK_RENEWER_GRACE_SECONDS`** (default 180,
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

- `renewed_at` is re-stamped by **every** same-owner renewal, including all four
  ephemeral CLI renewal call sites. So any `sdlc-tool` write resets the grace clock — the
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
small sleeping loop, or an operator killing one pid by hand. **Worker mode has
the same residual, in symmetric form** — `_maybe_launch_lease_heartbeat` is
skipped under `VALOR_WORKER_MODE` and the durable renewer is the worker itself,
but the worker's `claude -p` subprocesses are spawned `start_new_session=True`
(`agent/session_runner/role_driver.py:462`) and can outlive a worker crash, so a
SIGKILLed worker leaves a dead `renewer_pid` behind a possibly-live run with no
clear mechanism. Risk 5 records it; a crash cannot run cleanup code, which is
row Ib's own reasoning.

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

**The runtime allowlist: what it is, and the introspection trap that would have
made it inert (round-3 BLOCKER).**

The renewal path carries a module-level
`_DURABLE_RENEWER_MODULES = {"tools.sdlc_lease_heartbeat",
"agent.session_executor"}`. A caller passing `stamp_renewer_identity=True` must
also pass a `renewer_module` token; if the token is not in the set, the stamp is
**silently dropped** plus one `logger.warning` — never an exception, because
nothing on this path may be allowed to break lock acquisition.

**The token is passed explicitly by the caller. It must NEVER be derived by
introspection.** The first draft specified "checked against the calling module's
`__name__`", and that design is not merely fragile — it fails closed on the
exact path the whole fix exists for. `tools/sdlc_session_ensure.py:235-238`
launches the heartbeat as
`[sys.executable, "-m", "tools.sdlc_lease_heartbeat", ...]`. Under `-m` the
module executes as `__main__`, so every frame defined in that file resolves
`f_globals["__name__"]` to `"__main__"`, never the dotted path — the file's own
`if __name__ == "__main__":` guard at `tools/sdlc_lease_heartbeat.py:531` is
proof it runs under that name. The detached heartbeat would therefore fail the
allowlist on **every** call and never stamp anything in production, while
`agent/session_executor.py` (imported normally by the worker) would be
unaffected. The failure is silent by construction, and — the part that makes it
genuinely dangerous — **the mandated test would still pass**, because unit tests
reach the heartbeat via a plain `import tools.sdlc_lease_heartbeat`, where
`__name__` resolves correctly. A green suite over an inert fix on every real
deployment. Do not use `sys._getframe`, `inspect.stack()[n].frame.f_globals`, or
`inspect.getmodule(frame).__name__`; all three return `"__main__"` here.

**What the allowlist does and does not buy, stated plainly.** With an explicit
token it is a *declaration* checked against a list, not a structural barrier: a
caller that can pass `stamp_renewer_identity=True` can also pass a token that is
on the list. Its honest value is that opting in is two-part and self-naming, so
the opt-in cannot be added absent-mindedly and both halves are greppable. It is
kept for that reason and because a mis-stamp is the expensive direction (Risk 2),
not because it is unforgeable. The four-file grep anti-criterion remains the
cheap build-time signal, and the mandatory REVIEW stage is the backstop that
actually catches a new renewal-writing caller. Risk 6 states the residual
without overclaiming.

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

**Scope of that claim, stated precisely (round-4 CONCERN).** It holds while a
durable renewer identity is on the payload. It does **not** hold after a
heartbeat's non-releasing deadline exit: shape (a) clears the group there by
design, so such a run reverts to the untouched #2620 behavior and its blind
window is the full 1200 s freshness window again. That is the deliberate price
of not reaping live runs (Risk 5), not an oversight — but the collapse is a
property of stamped leases, not of every lease. Because Task 5's clear preserves
`renewed_at` and the remaining TTL, the reverted window is the *same* one `main`
would have had, not a refreshed one.

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

- **The new branch needs its OWN `try/except Exception -> True`. It does NOT
  reuse the existing blanket handler.** That handler lives inside the `try:` at
  `models/session_lifecycle.py:1046-1063`, which is *below* the short-circuit at
  `:1033` where the new branch goes, and is entered only after the pid /
  machine_id / create_time guards at `:1035-1045` that a renewer-only payload
  need not satisfy. A builder who assumes the existing handler covers the new
  code adds none, and a `psutil` raise then escapes `_lock_owner_is_live`
  entirely: the reaper catches it (`tools/sdlc_session_ensure.py:1120-1127`) but
  the peek path at `:1249` does not, turning it into an `acquired=False`
  fail-closed on a lease the caller owns. Structure the branch so
  `except Exception: return True` is reachable before any `return False` is.
  Covered by a test that makes `_psutil_process_for_pid` raise.
- `_current_process_create_time()` returning `None` inside a durable renewal
  must produce a **partial** identity that the predicate treats as *no* identity
  (fail-toward-live), mirroring `_resolved_supervisor`'s "a pid without a
  create_time is no identity" rule.
- The heartbeat's deadline-exit clear (shape (a)) is best-effort: any exception
  from the CAS write is swallowed and the exit proceeds, matching the release at
  `tools/sdlc_lease_heartbeat.py:377-390`. A failure to clear must never block
  the exit; the lease TTL remains the backstop.
- The durable-renewer module allowlist never raises. A disallowed caller passing
  the flag gets the stamp silently dropped plus one `logger.warning` — an
  exception here would break lock acquisition itself.

### Empty/Invalid Input Handling

- `renewer_pid` present but `renewer_create_time` absent → partial identity →
  short-circuit as today.
- `renewer_pid` non-numeric / zero / negative → treated as absent.
- Payload not same-machine per `_payload_from_same_machine` → fail-toward-live.
  (There is no `renewer_machine_id` key; see **Technical Approach**.)
- `renewed_at` unparseable → existing `None` path, pid fall-through unchanged.

### Reflection-Layer Coverage (BLOCKER 1)

`reflections/utilities.py::_lock_says_live` (`:277-307`) returns `bool | None`
and its callers `continue` on `True` and on `None` but **act** on `False`. Every
new early-return in the tightened branch must therefore be `True`; only the full
conjunction (complete renewer identity AND same-machine AND renewer pid
dead-or-recycled AND `renewed_at` age > grace) may return `False`. This is a
property of the predicate, tested at the reflection layer as well as the
predicate layer — see **Test Impact**.

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
- [ ] `tests/unit/test_sdlc_session_ensure.py::TestKillOrphans` (`:1211`) — VERIFY UNCHANGED: the existing rows must stay green byte-for-byte. (The "if present" hedge in the first draft was stale; the class exists and `_iter_orphan_sessions` is at `tools/sdlc_session_ensure.py:1059`.)
- [ ] `tests/unit/test_sdlc_session_ensure.py` — ADD two rows to `TestKillOrphans`: an `sdlc-local-{N}` row with `last_heartbeat_at = None` whose payload carries a fresh stamp and a LIVE `renewer_pid` must NOT be yielded; the same row with a dead `renewer_pid` past the grace MUST be yielded. This is SC7 at the consumer layer, not the predicate layer.
- [ ] `tests/unit/reflections/test_utilities_lock_says_live.py` — NEW module (BLOCKER 1). Patch `reflections.utilities._get_redis` to return each payload shape and assert `_lock_says_live(N)`: dead-renewer-past-grace → `False`; live-renewer → `True`; inside-grace → `True`; no-renewer-identity → `True`; cross-machine → `True`. The existing `tests/unit/reflections/test_sdlc_upvote_lanes.py` stubs `_lock_says_live` wholesale, so it exercises the gate but never the payload→verdict path; this module covers the half that matters here.
- [ ] `tests/unit/reflections/test_sdlc_upvote_lanes.py` (`:697`, `:704`) — KEEP UNCHANGED: the "imported not forked" guards must stay green, since this plan's whole posture is one predicate for all three consumers.
- [ ] `tests/unit/test_sdlc_lease_heartbeat.py` — ADD: the two non-releasing deadline exits perform the `renewer_*` clear (shape (a)); a clear that raises does not block the exit; and a clear that loses its CAS is retried once (Race 6 mode 1). Locate the existing exit-path tests (around `:185-226`) and extend them rather than adding a parallel class.
- [ ] `tests/unit/test_sdlc_lease_heartbeat.py` — ADD the `-m` invocation regression test (SC5b): drive the heartbeat via `runpy.run_module("tools.sdlc_lease_heartbeat", run_name="__main__")` or a subprocess and assert the renewal still stamps `renewer_pid`. A plain `import` cannot detect the round-3 BLOCKER.
- [ ] `tests/unit/test_session_lifecycle.py` — ADD `test_ephemeral_renewal_strips_renewer_identity` (SC5c): stamp via a durable renewal, then renew WITHOUT the flag, and assert the payload carries no `renewer_*` keys.
- [ ] `tests/unit/test_session_lifecycle.py` — ADD `test_disallowed_module_stamp_is_dropped` (SC5, Risk 6): call the renewal path with `stamp_renewer_identity=True` and a `renewer_module` token absent from `_DURABLE_RENEWER_MODULES`; assert no `renewer_*` keys are written and no exception is raised. The Verification row greps this exact name.

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
branch's own `try/except Exception -> True`. The demonstrated-red test suite
asserts all three gates in the *live* direction, not only the dead one.

**Per-consumer, worst-first — the reflection gate argued specifically
(BLOCKER 1).** The three consumers do not share a failure cost, so the
mitigation is argued against each:

- **`_lock_says_live` → `sdlc_upvote_lanes.py:529` (gate 3).** This is the worst
  case and the reason the "every early-return is `True`" rule is a hard
  constraint rather than a style preference: `False` is the *only* verdict that
  lets the reflection fall through to `create_session`, and it does so with no
  operator in the loop. Two things bound it. First, gate 2
  (`_ledger_has_recorded_stage`) runs *before* the lock read and excludes every
  candidate whose `PipelineLedger.stage_states_json` is non-empty, so a live run
  that has recorded any stage never reaches the predicate at all; the exposed
  window is the pre-first-stage-marker gap, in which spike-3 shows the heartbeat
  is freshly spawned and its pid resolves ALIVE (spike-5 row III). Second, shape
  (a) removes the systematic false-dead generator that would otherwise reach
  this gate on every lane outliving 90 minutes (spike-5 rows I/Ia).
- **`sdlc_progress.py:942`** is the wider exposure, because its `create` rung
  fires for an issue that already has a PR and a pushed branch — a shape a live
  mid-flight lane matches, and where the lock read is the binding gate rather
  than a second opinion. It is covered by the same two bounds, but without gate
  2's help, which is why the reflection-layer test is mandatory (SC9) rather
  than implied by the predicate-layer tests.
- **`_iter_orphan_sessions`** reaps immediately on a dead verdict (no idle gate
  on the payload-present path, `tools/sdlc_session_ensure.py:1128`), covered by
  SC7's consumer-layer test.
- **The peek path** reports before it acts, so an operator can intervene; it is
  the cheapest of the three and needs no additional guard.

### Risk 2: An ephemeral CLI renewer acquires the stamp later and re-poisons the field

If a future edit adds `stamp_renewer_identity=True` to one of the four
short-lived CLI renewers, the payload would carry a pid that is dead within
seconds and the predicate would report every lease orphaned after the grace —
strictly worse than today.

**Mitigation.** An anti-criterion row in **Verification** mechanically asserts
the flag appears at exactly the two durable call sites, plus its definition and
plumbing, and nowhere else. Demonstrated red against a deliberately-violating
edit before it is trusted.

### Risk 3: The grace window is mis-sized for a machine with a slower renewer

A machine that has raised `ISSUE_LOCK_TTL_SECONDS` (and therefore the
heartbeat's `TTL//3` cadence) does not need a larger grace: **while the renewer
pid resolves alive**, the branch returns `True` without ever consulting the
clock, so cadence is irrelevant. The narrower true statement is the one that
binds — **once the renewer pid resolves dead, `renewed_at` recency is the
signal**, and `renewed_at` is refreshed by every renewer including the four
ephemeral ones. What could still exceed 180 s is an unusually slow worker
restart.

**Mitigation.** The constant is env-overridable
(`ISSUE_LOCK_RENEWER_GRACE_SECONDS`) and explicitly marked provisional/tunable
at its definition, per the repo's magic-number convention. The failure mode of
an under-sized grace is a transient false-dead *peek*, which reports rather than
steals.

### Risk 4: Redis payload growth / forward compatibility

Two new keys on a JSON string value (`renewer_pid`, `renewer_create_time`).
Negligible size; and an older process reading a newer payload simply ignores
unknown keys (the predicate reads by `.get`, the renewal branch spreads
`{**payload}`), so a mixed-version machine is safe in both directions. The
shape-(a) clear removes keys rather than adding them, which an older reader
also tolerates — it is the pre-#2648 payload shape exactly.

### Risk 5: Shape (a)'s residual — a heartbeat that dies without running its exit path

Clearing the `renewer_*` group at the two non-releasing deadline exits does
nothing for a heartbeat killed without executing either return: SIGKILL, OOM,
an unhandled exception outside the tick handler, power loss. Spike-5 row Ib
measures that state as still reading dead past the grace, on a run that may be
alive.

**Disposition: accepted and documented, not closed.** The size of the residual
is the size of the conjunction it needs — the heartbeat dies without either
non-releasing return and without the supervisor-death release; *and* that death
spares the run; *and* the run then goes ≥180 s with zero `sdlc-tool` lock
writes. The middle condition is the narrow one and it is narrow in the helpful
direction: the heartbeat is spawned `start_new_session=True`, so it survives a
kill of the supervisor's process tree, and the deaths that do take it out are
overwhelmingly machine-scoped (reboot, sleep, power loss, an operator kill
sweep) — every one of which kills the run too, making the DEAD verdict
**correct** and identical to the #2648 incident this plan fixes. The residual is
the strict subset where something kills the detached heartbeat *alone*.

**Worker mode has the SAME residual, not none (round-5 CONCERN).** An earlier
draft claimed it "does not exist at all" in worker mode on the grounds that the
run dies with the worker. That is false, and it was the one residual with no
covering task. `agent/session_runner/role_driver.py:462` spawns every role
turn's `claude -p` with `start_new_session=True` — its own comment says "the
worker orphan sweep reaps survivors after a crash", language that only makes
sense because survivors routinely exist. `agent/session_executor.py:246` stamps
the **worker's** pid (`os.getpid()`), not the subprocess's, and the worker's
60 s tick loop dies with the worker. So a worker SIGKILL or OOM leaves a dead
`renewer_pid` with no tick to refresh it and — unlike the heartbeat's two
non-releasing exits — **no clear mechanism at all**, because Task 5 edits only
`tools/sdlc_lease_heartbeat.py`. After the grace, the tightened branch reads
DEAD against a possibly-still-working orphaned subprocess.

This is spike-5 row Ib on the worker side: **symmetric to the heartbeat case,
not absent.** It carries the same three-part conjunction (renewer dies without a
clear; the death spares the run; then ≥ grace of quiet), the same
anomaly-conditioning, and the same backstops (1800 s TTL, and any `sdlc-tool`
write resetting the grace clock). It is recorded here rather than closed:
extending the clear to the worker would mean a crash-path write from a process
that has just been SIGKILLed, which is not implementable, and the honest
disposition is the TTL.

**Backstops are unchanged — but only because Task 5 makes the clear a pure
key-removal** (round-4 CONCERN). They are the 1800 s lease TTL, #2784's release
where the heartbeat lives to perform it, and the fact that any single
`sdlc-tool` write resets the grace clock. That first backstop is only intact
because the exit clear preserves the payload's existing `renewed_at` and the
key's *remaining* TTL. Had it been implemented as an ordinary same-owner renewal
— the obvious reading of "a CAS renewal that drops two keys" — it would have
re-stamped `renewed_at` to now and reset the lease to a full 1800 s, handing a
run that is genuinely dead at its deadline exit up to ~600 s of extra false-live
*and* up to ~600 s of extra lease life versus `main`, on the exact path where
the tightening has already been surrendered. The backstop claim is therefore
load-bearing on Task 5's implementation detail, not free.

The trade this risk records is the one the plan is making deliberately: a
*systematic* false-dead generator (a deadline exit, which fires on every lane
outliving 90 minutes) is exchanged for an *anomaly-conditioned* one.

### Risk 6: A future caller reaches the renewal path without touching a watched file

**This risk is precautionary, and the correction matters (round-3 CONCERN).**
An earlier draft cited `tools/sdlc_session_release.py:92`,
`tools/merge_predicate.py:656`, and `agent/supervised_run.py:256` as proof that
renewal-writing callers already exist outside the four watched files. That was
wrong. All three were re-read and **all three are read-only peeks**
(`touch_issue_lock(..., peek=True)`); none reaches a renewal branch, and each
would have to be rewritten from a peek into a renewal call before the stamp flag
could matter at all — a visible edit this repo's mandatory REVIEW stage catches.
The citations are removed rather than left standing as if they were near-misses.

So: **no current caller performs a renewal write outside the four watched
files.** The risk is that a *future* one does, and the grep anti-criterion is
scoped to a fixed file list that would not see it.

**Mitigation, sized to a hypothetical rather than an observed gap.** The
`renewer_module` token plus `_DURABLE_RENEWER_MODULES` (Task 2) makes opting in
a two-part, self-naming act that cannot be added absent-mindedly; it is a
declaration checked against a list, not an unforgeable barrier (argued in
**Technical Approach**). The grep row is the build-time signal and REVIEW is the
real backstop. Tested by calling the renewal path with a disallowed token and
asserting no `renewer_*` keys are written.

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

### Race 5: The deadline-exit clear lands after a successor has taken the lease

The retiring heartbeat's final `renewer_*` clear could otherwise overwrite a
payload that now belongs to a *different*, live run — stripping that run's
durable renewer identity and handing it the full 1200 s blind window.

Closed by making the clear compare-and-set: it reuses
`_RENEW_IF_VALUE_MATCHES_LUA` (`models/session_lifecycle.py:1301`), which writes
only if the stored value is byte-identical to the one read. A successor's
payload never matches, so the clear no-ops. This is the same reason #2714 made
the renew path a CAS, and the same reason the supervisor-death release at
`tools/sdlc_lease_heartbeat.py:379-381` is a compare-and-delete.

### Race 6: A same-run_id ephemeral renewal lands around the deadline-exit clear

Distinct from Race 5 and more likely: not a *successor* takeover but a benign
`sdlc-tool` CLI renewal on the **same** run_id, racing the heartbeat's exit
clear. Two failure modes, both real, both closed:

1. **The clear loses its CAS** because an ephemeral renewal landed between the
   heartbeat's read and its write. The exit clear is a single best-effort
   exception-swallowed write with no retry, so it would simply be lost — and
   unlike the tick loop, which stops on a lost CAS via `EXIT_LEASE_LOST`, there
   is nothing downstream to notice. **Closed by** a bounded 2-attempt
   re-GET/re-CAS loop checking `result.acquired`, falling through to the
   existing swallow only after the budget is exhausted. A genuine successor
   (different `run_id`) still fails both attempts, so Race 5's guarantee is
   preserved.
2. **An ephemeral renewal lands *after* a successful clear** and re-carries the
   stale `renewer_pid` it read beforehand, because the default renewal branch
   (`:1373-1380`) is a bare non-CAS `_R.set` of a payload spread from the
   existing one. This would silently undo the clear and drop the run back into
   spike-5 row I — false-dead, reapable on the next quiet stretch, with no
   backstop until the 1800 s TTL. A retry does **not** close this; the write can
   land at any later time. **Closed by Key Element 2**: a renewal that does not
   set `stamp_renewer_identity` strips the group rather than spreading it, so
   an ephemeral renewal cannot re-assert a durable identity no matter when it
   lands. This is why the invariant is stated as a maintained property rather
   than as a one-shot clear.

**The cost of Key Element 2, stated honestly.** Because an ephemeral renewal now
strips the group, a lease whose last write before death happened to be an
ephemeral CLI renewal carries no durable identity and keeps the full 1200 s
blind window. That is a *partial-coverage* loss, not a correctness loss: it
fails toward live, the safe direction, and it costs nothing in the quiet-stretch
case the tightening actually targets — any ephemeral write also resets
`renewed_at`, so the grace clock had restarted anyway and the branch could not
have fired. The #2648 incident shape (run dies, heartbeat's stamp is the last
write, then silence) is unaffected.

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

- [ ] Update `docs/features/sdlc-issue-ownership-lock.md`: add `renewer_pid` and
      `renewer_create_time` — **exactly two keys, no `renewer_machine_id`** — to
      the payload example (around `:79`) and its field description (`:82`),
      noting that the group is written only by a durable renewer and cleared
      when one retires on its own deadline; rewrite the "Renewal
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
      freshness window. **Name `ISSUE_LOCK_RENEWER_GRACE_SECONDS` explicitly** —
      the Verification row greps for it, and a checkbox that only asks for
      reworded prose would leave that row unsatisfiable (NIT 2).
- [ ] Update `docs/features/config-timeout-catalog.md` (the `ISSUE_LOCK_*`
      paragraph at `:82-92`): add `ISSUE_LOCK_RENEWER_GRACE_SECONDS` beside
      `ISSUE_LOCK_TTL_SECONDS` with the same "env var, not `TIMEOUTS__*`"
      caveat, and record that it is deliberately **TTL-independent** and should
      be lowered by hand if an operator lowers the TTL. Add the
      currently-uncatalogued `ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS` in the same
      pass, since the constant-placement argument in **Technical Approach**
      rests on these three being discoverable as one family (NIT 3).
- [ ] Update `tools/sdlc_lease_heartbeat.py`'s deadline-exit comments (`:351-365`)
      to record that the exit now clears the `renewer_*` group, and why: the
      exits' existing prose already says failing to resolve a supervisor is not
      proof of death, and the clear is that judgement applied to the payload.
- [ ] Update `tools/sdlc_lease_heartbeat.py`'s module docstring (`:33-42`),
      which currently asserts the payload pid "is dead before this detached
      heartbeat's first tick" — still true of `pid`, now false of
      `renewer_pid`, and the distinction is the whole point.
- [ ] Inline, and **scoped so it cannot fight the Verification table**
      (round-4 CONCERN): the new constant's comment carries both the #2648
      rationale and the literal `GRAIN OF SALT` marker, on a **single** comment
      line, matching the existing shape at `models/session_lifecycle.py:842` and
      `:855`. The docstrings on `_healed_renewal_payload` and
      `_lock_owner_is_live` carry the #2648 rationale **in prose, WITHOUT the
      literal marker.** `grep -c` counts matching *lines*, so the Verification
      target of exactly 4 (3 on `main` plus one new) holds only if precisely one
      new line contains the token; a builder who put the marker in all three
      places would land on 6 and turn a faithful build red against its own
      table. Do not "fix" that by relaxing the row back to `> 1` — that is the
      ineffective form round 3 replaced.

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
   call sites passing `stamp_renewer_identity=True` (anti-criterion), each with
   its explicit `renewer_module` token, **and** a disallowed token drops the
   stamp — proved by calling the renewal path with one and asserting no
   `renewer_*` keys appear (Risk 6).
5b. **The heartbeat stamps under its REAL invocation shape.** Exercised via
   `runpy.run_module(..., run_name="__main__")` or a subprocess, not a plain
   `import`. This is the round-3 BLOCKER criterion: an introspection-based
   allowlist passes every import-based test while never stamping in production,
   so a suite that only imports cannot discharge criterion 5.
5c. **The `renewer_*` group survives no ephemeral renewal.** After a durable
   stamp, a renewal without `stamp_renewer_identity` (any of the four CLI
   renewal call sites) leaves a payload carrying **no** `renewer_*` keys — Key Element 2's
   invariant, and what makes the Task 5 clear durable rather than defeasible
   (Race 6 mode 2).
6. `ISSUE_LOCK_RENEWER_GRACE_SECONDS` is a named, env-overridable, GRAIN OF
   SALT-annotated module constant beside its two siblings, and is catalogued in
   `docs/features/config-timeout-catalog.md`.
7. `--kill-orphans` still exempts a live-but-quiet `sdlc-local-{N}` anchor whose
   heartbeat is alive (explicit test at the `_iter_orphan_sessions` layer, not
   only at the predicate layer).
8. **Shape (a) holds (the BLOCKER 3 criterion).** Both non-releasing deadline
   exits in `tools/sdlc_lease_heartbeat.py` clear the `renewer_*` group via the
   CAS path, and a payload so cleared reads `_lock_owner_is_live is True` at
   800 s of quiet — byte-identical to today's verdict (spike-5 row Ia). A build
   that ships the tightening **without** this clear satisfies criteria 1-4 and
   is still wrong; this criterion is what separates them.
9. **The reflection layer is proved, not inferred (the BLOCKER 1 criterion).**
   `reflections/utilities.py::_lock_says_live` returns `False` for a
   dead-renewer-past-grace payload and `True` for live-renewer, inside-grace,
   no-identity, and cross-machine payloads. Tested against the real predicate
   with `_get_redis` patched, mirroring SC7's consumer-layer posture.
10. Docs listed above updated; `ruff check` and `ruff format --check` clean.

## Team Orchestration

Single builder. The change is four files and one test module; splitting it would
cost more in handoff than it saves. `/do-plan-critique` runs the war room before
build, as the disposition comment on #2648 explicitly requested ("it wants a plan
and a critique round, not a same-day PR").

### Team Members

| Role | Agent Type | Responsibility |
|------|-----------|----------------|
| Builder | `builder` | All eight task steps, sequentially |
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
- Add `test_drop_renewer_identity_preserves_renewed_at` (round-4 CONCERN):
  after a `drop_renewer_identity=True` write, the stored `renewed_at` must equal
  its pre-clear value **exactly**, and the key's TTL must not have been reset to
  `ISSUE_LOCK_TTL_SECONDS`. This is the case that catches the obvious-but-wrong
  implementation of Task 5 — delegating the clear to `_healed_renewal_payload`,
  whose `:1118` re-stamps `renewed_at` unconditionally.
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
  to write **exactly two** keys when the flag is set: `renewer_pid`
  (`os.getpid()`) and `renewer_create_time` (`_current_process_create_time()`).

  **Do NOT write `renewer_machine_id`.** It is redundant — renewal already
  requires a `run_id` match (`:1289` CAS, `:1369` default) and run_ids are
  minted on exactly one machine, so the renewer is always on the owner's
  machine, whose `machine_id` the payload already carries. And it is unsafe:
  `_local_machine_id()` returns `""`, not `None`, when unresolvable
  (`:924-936`), so on a host that cannot identify itself a naive equality gate
  would read `"" == ""` as *same machine* — a false-dead on exactly the
  indeterminate evidence `_payload_from_same_machine` is careful to reject.

  The write rule is therefore **both or neither**: stamp only when
  `_current_process_create_time()` is not `None`, mirroring
  `_resolved_supervisor`'s "a pid without a create_time is no identity" rule.
- **Make the strip the other half of the write rule (Key Element 2, round-3
  CONCERN).** When `stamp_renewer_identity` is NOT set, `_healed_renewal_payload`
  must **remove** `renewer_pid` / `renewer_create_time` from the payload it
  returns rather than spreading them through. This is a deliberate, narrow
  exception to that helper's documented "spread the EXISTING payload, never
  reconstruct a subset" rule (`:1084-1096`) — comment it as such at the spread
  site and name the invariant (Key Element 0). Without it the default branch at
  `:1378`, a bare non-CAS `_R.set`, lets any ephemeral CLI renewal re-carry a
  stale `renewer_pid` and silently undo a clear (Race 6).
- **Add the durable-renewer allowlist with an EXPLICIT token (Risk 6).** A
  module-level `_DURABLE_RENEWER_MODULES = {"tools.sdlc_lease_heartbeat",
  "agent.session_executor"}`, checked against a `renewer_module` argument the
  caller passes. A disallowed token gets the stamp **silently dropped plus one
  `logger.warning`** — never an exception, because nothing on this path may be
  allowed to break lock acquisition.

  **Do NOT derive the caller's identity by introspection.** `__name__`,
  `sys._getframe`, and `inspect` all resolve to `"__main__"` for the detached
  heartbeat, which `tools/sdlc_session_ensure.py:235-238` launches as
  `python -m tools.sdlc_lease_heartbeat`. An introspection-based check would
  fail on every heartbeat call in production, never stamp anything, and do it
  silently — while a unit test using a plain `import` would still pass. See the
  round-3 BLOCKER in **Technical Approach**.
- Add keyword-only `stamp_renewer_identity: bool = False` to `touch_issue_lock`
  and plumb it to BOTH renewal branches (`:1296` renew_only CAS, `:1378`
  default). Put the "why only two callers" rationale in the `touch_issue_lock`
  docstring, which the anti-criterion row does not grep.

### 3. Tighten `_lock_owner_is_live`

- Replace the bare short-circuit at `:1033` with the branch specified in
  **Solution → Flow**. Keep every line below the fall-through untouched.
- **Gate on the existing `_payload_from_same_machine(payload)`** (`:939-959`),
  not on any renewer-specific machine field. That helper already carries both
  the truthiness guard at `:957` (`if payload_machine_id and local_machine_id:`)
  and the legacy `hostname` fallback, neither of which a `renewer_machine_id`
  group would have. The gate reads:

  ```python
  if renewer_pid and renewer_create_time is not None and _payload_from_same_machine(payload):
  ```

  Use the existing `1e-3` create_time tolerance for the recycled-pid comparison.
- **The new branch does its own function-body import of the pid helper, and must
  NOT hoist it to module scope.** Write
  `from agent.session_health import _psutil_process_for_pid` *inside* the new
  branch's `try`, exactly mirroring the existing lazy import at `:1046`. The new
  branch sits at `:1033`, **above** that import, so it cannot rely on it — but
  satisfying that by adding a module-scope import in
  `models/session_lifecycle.py` would silently invalidate Task 1's
  PATCH-LOCATION TRAP: the module-level name would come into existence,
  `patch("models.session_lifecycle._psutil_process_for_pid")` would stop raising
  `AttributeError`, and every pre-existing test at
  `tests/unit/test_session_lifecycle.py:564-567` relies on
  `agent.session_health` being the only working target. Task 1 and Task 3 must
  not be allowed to undermine each other here.
- **The branch needs its OWN `try/except Exception -> True`** — it does not and
  cannot reuse the handler at `:1046-1063`, which lives below the short-circuit
  and behind guards a renewer-only payload need not satisfy. Structure it so
  `except Exception: return True` is reachable before any `return False` is, and
  do NOT move the new branch inside the `try:` at `:1046`.
- **Log the DEAD verdict at `logger.warning`, not debug** (round-4 NIT), naming
  the renewer pid, the `renewed_at` age, and the grace value. Risk 1 identifies
  this branch's false-dead as the worst failure in the plan — on the
  `_lock_says_live` → `create_session` path it starts an autonomous rival lane
  with no operator in the loop — so an operator investigating an unexplained
  rival lane or a reaped `sdlc-local-{N}` anchor must be able to tell that the
  new tightening fired without having had debug logging enabled beforehand.
  Keep the LIVE paths and the `except Exception -> True` fallback at debug;
  only the `return False` path is elevated.
- Read the grace clock as `time.time() - float(payload["renewed_at"])`. Because
  the branch is only reached when `_lock_renewal_is_fresh(payload) is True`,
  `renewed_at` is guaranteed present and float-parseable: no second `try/except`
  for the parse, and do not re-derive it through a helper that can return `None`.
- Run the new test class: the previously-red case must now be green.

### 4. Wire the two durable renewers

- `tools/sdlc_lease_heartbeat.py:429` — add
  `stamp_renewer_identity=True, renewer_module="tools.sdlc_lease_heartbeat"` to
  the `renew_only=True` extend.
- `agent/session_executor.py:246` — add
  `stamp_renewer_identity=True, renewer_module="agent.session_executor"`.
- **Regression test for the `-m` trap, in the real invocation shape.** A test
  using a plain `import tools.sdlc_lease_heartbeat` **cannot** detect the
  round-3 BLOCKER class of defect, because `__name__` resolves correctly under
  import and only diverges under `-m`. Exercise the real shape instead —
  `runpy.run_module("tools.sdlc_lease_heartbeat", run_name="__main__")` or an
  actual subprocess — and assert the stamp still lands on the payload. This test
  is the only thing standing between a green suite and a fix that is inert on
  every real deployment.
- Confirm by grep that no other call site carries the flag (Verification row 5).
- **Do not name the flag, even in a comment, inside the four ephemeral-renewer
  files** (`tools/_sdlc_utils.py`, `tools/sdlc_stage_marker.py`,
  `tools/sdlc_next_skill.py`, `tools/sdlc_session_ensure.py`). The
  anti-criterion row greps exactly those four files for the token, so a
  well-meant "deliberately does not opt in here" comment tallies as a
  violation and turns a correct build red. Paraphrase instead — e.g. "renews
  without recording a durable renewer identity (see #2648)" — and put the
  rationale in the `touch_issue_lock` docstring, which the row does not grep.

### 5. Clear the renewer identity at the heartbeat's non-releasing exits (shape (a))

**This task is the fix for BLOCKER 3 and is not optional. Without it the
tightening is strictly worse than the bug it fixes** — spike-5 rows I/I' measure
a live run being reaped within 180 s, mid-stage, where today it merely "loses at
most one TTL window".

- Add keyword-only `drop_renewer_identity: bool = False` to `touch_issue_lock`.
  When set, it performs a same-owner CAS write of a payload with the
  `renewer_pid` / `renewer_create_time` group **removed**.
- **The clear must be a pure key-removal: it must NOT refresh `renewed_at` and
  must NOT extend the TTL** (round-4 CONCERN). Do **not** delegate to
  `_healed_renewal_payload`, whose `:1118` is an unconditional
  `new_payload["renewed_at"] = time.time()`, and do not let the CAS take the
  default `ttl` (`ISSUE_LOCK_TTL_SECONDS`, 1800). Build the CAS payload inline —
  `{**payload}` minus the two `renewer_*` keys, `renewed_at` untouched — and
  pass the key's *remaining* lifetime into the eval via `_R.pttl(key) // 1000`,
  guarding the `-1` (no expiry) and `-2` (missing key) sentinels by falling
  through to the swallow rather than inventing a TTL. This is a second, narrower
  exception to the helper's "spread the EXISTING payload" rule and must be
  commented alongside Key Element 2's.

  Why it matters: today the deadline exits perform **no Redis write at all**, so
  the payload's `renewed_at` is already up to one 600 s tick stale and the TTL is
  decaying on its own clock. A clear that re-stamped both would hand a run that
  is genuinely dead at its deadline exit up to ~600 s of *extra* false-live plus
  up to ~600 s of extra lease lifetime versus `main` — widening the very window
  this issue exists to narrow, on the one path where the tightening has already
  been given up (Risk 5). Spike-5 row Ia measured the verdict at an instant and
  so could not have caught this; the divergence is in the decay clock.
- It MUST go through the compare-and-set path
  (`_RENEW_IF_VALUE_MATCHES_LUA`, `models/session_lifecycle.py:1301`) so a
  successor that already took the lease is untouched (Race 5).
- **Bound a 2-attempt re-GET/re-CAS retry around it** (Race 6, mode 1). Check
  `result.acquired`; retry once on a lost CAS, then fall through to the existing
  swallow. A same-run_id ephemeral renewal racing the clear wins the retry; a
  genuine successor with a different `run_id` fails both attempts, so Race 5's
  guarantee is preserved. Without the retry the clear is a single unverified
  write with nothing downstream to notice its loss — unlike the tick loop, which
  catches a lost CAS via `EXIT_LEASE_LOST`.
- **The retry alone does not make the clear durable.** Race 6 mode 2 (an
  ephemeral renewal landing *after* a successful clear and re-carrying the stale
  identity through the non-CAS default branch) is closed by Task 2's strip, not
  here. Both are required; neither substitutes for the other.
- Call it at BOTH non-releasing deadline exits in `tools/sdlc_lease_heartbeat.py`:
  `EXIT_UNSUPERVISED_MAX_LIFETIME` (`:360`) and `EXIT_MAX_LIFETIME` (`:365`).
  Do NOT add it to `EXIT_SUPERVISOR_DEAD` (`:392`), which already releases the
  lease outright (#2784) and has nothing to clear.
- **Factoring the shared retry-plus-swallow logic into one helper is expected
  and correct** — the two exits sit four lines apart and this file already
  factors exit logic that way (`_log_exit`, `:244`, called from five sites). The
  Verification row is `> 0`, not an exact count, precisely so a well-factored
  build is not turned red. Coverage of both exits is proved by the SC8 tests.
- It MUST be best-effort and exception-swallowed, matching the release at
  `:377-390`: a failure to clear can never block the exit. The lease TTL is the
  backstop.
- **Do not "fix" this by weakening the predicate instead.** Shape (b) — an
  idle-time conjunct at the reaper's payload-present path — was measured and
  rejected in spike-5: it protects only one of three consumers, leaves the
  autonomous reflection lane fully exposed, and re-imports the `updated_at`
  liveness mirage that #2305 removed from that path on purpose.
- Test: both exits perform the clear; a raising clear does not block the exit;
  and a cleared payload reads `_lock_owner_is_live is True` at 800 s of quiet
  (spike-5 row Ia — the verdict must be byte-identical to today's).

### 6. Prove the other two consumers are unharmed

- **Reaper (SC7).** Add a test at the
  `tools/sdlc_session_ensure.py::_iter_orphan_sessions` layer: an
  `sdlc-local-{N}` row with `last_heartbeat_at = None` whose lock payload
  carries a fresh stamp and a LIVE `renewer_pid` must NOT be yielded; the same
  row with a dead `renewer_pid` past the grace MUST be yielded. Extend
  `tests/unit/test_sdlc_session_ensure.py::TestKillOrphans` (`:1211`) rather
  than adding a parallel class, and verify its existing rows stay green.
- **Reflection layer (SC9, BLOCKER 1).** Add
  `tests/unit/reflections/test_utilities_lock_says_live.py`: patch
  `reflections.utilities._get_redis` to return each payload shape and assert
  `_lock_says_live(N)` is `False` only for dead-renewer-past-grace, and `True`
  for live-renewer, inside-grace, no-identity, and cross-machine. This exercises
  the real predicate; the existing `test_sdlc_upvote_lanes.py` stubs
  `_lock_says_live` wholesale and so proves nothing about the payload→verdict
  path.
- Run the full pre-existing lock-liveness set unmodified
  (`scripts/pytest-clean.sh tests/unit/test_session_lifecycle.py -q`) and
  confirm all green.

### 7. Documentation

- Execute every checkbox in **## Documentation**.

### 8. Final Validation

- `scripts/pytest-clean.sh tests/unit/test_session_lifecycle.py tests/unit/test_sdlc_session_ensure.py tests/unit/test_sdlc_lease_heartbeat.py tests/unit/reflections/test_utilities_lock_says_live.py tests/unit/reflections/test_sdlc_upvote_lanes.py -q`
- `python -m ruff check .` and `python -m ruff format --check .`
- Run every row in **## Verification**.
- Paste the full demonstrated-red PAIR into the PR body: half (a) red on `main`
  then green after, and half (b) green on `main` AND green after. Four runs,
  four pasted outputs. A PR carrying only the (a) red/green pair is incomplete.

## Verification

Verification commands enumerate roots explicitly (`models/ tools/ agent/
tests/ docs/ .claude/`) rather than anchoring on `^./` — `ugrep` on this machine
prints no `./` prefix and such an anchor silently matches nothing.

**Every expect-no-output row exits 1, and that is the PASS.** `grep` returns 1
when it matches nothing, so the anti-criterion rows and the xfail row all exit
non-zero on a correct build. Do not run them under `set -e`, and do not
"fix" a passing row by inverting it. All four expect-no-output forms were run
against `main` while writing this plan and behave as documented.

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

**The same trap applies to the `renewer_machine_id` row, and it is easier to
fall into.** That row greps source and docs roots recursively for a token this
plan spends two paragraphs arguing *against*. A builder who records the decision
as a code comment — `# deliberately no renewer_machine_id, see #2648` — turns a
correct build red. Paraphrase in code and docs (e.g. "the renewer is always on
the owner's machine, so no separate machine field is recorded"); the reasoning
belongs in this plan and the PR body, neither of which the row greps.
`docs/plans/` is deliberately absent from the row's root list for that reason.

| Check | Command | Expected |
|-------|---------|----------|
| Lock-liveness tests pass | `scripts/pytest-clean.sh tests/unit/test_session_lifecycle.py -q` | exit code 0 |
| Orphan-reaper tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_session_ensure.py -q` | exit code 0 |
| Heartbeat tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_lease_heartbeat.py -q` | exit code 0 |
| Reflection-layer tests pass | `scripts/pytest-clean.sh tests/unit/reflections/test_utilities_lock_says_live.py tests/unit/reflections/test_sdlc_upvote_lanes.py -q` | exit code 0 |
| Lint clean | `python -m ruff check models/ tools/ agent/ tests/ reflections/` | exit code 0 |
| Format clean | `python -m ruff format --check models/ tools/ agent/ tests/ reflections/` | exit code 0 |
| Grace constant is named and env-overridable | `grep -c 'ISSUE_LOCK_RENEWER_GRACE_SECONDS = int(os.environ.get("ISSUE_LOCK_RENEWER_GRACE_SECONDS"' models/session_lifecycle.py` | output contains 1 |
| Grace constant marked provisional | `grep -c 'GRAIN OF SALT' models/session_lifecycle.py` | output contains 4 (exactly one more than the 3 on `main`; a bare `> 1` already passes today and cannot detect a constant shipped without its annotation) |
| Renewer identity reaches the payload | `grep -c 'renewer_pid' models/session_lifecycle.py` | output > 1 |
| **No `renewer_machine_id` anywhere** (BLOCKER 2) | `grep -rl 'renewer_machine_id' models/ tools/ agent/ tests/ reflections/ docs/features/` | no output |
| Both durable renewers opt in | `grep -rl 'stamp_renewer_identity=True' tools/ agent/ \| sort` | output contains agent/session_executor.py |
| Heartbeat opts in | `grep -c 'stamp_renewer_identity=True' tools/sdlc_lease_heartbeat.py` | output contains 1 |
| Runtime allowlist exists (Risk 6) | `grep -c '_DURABLE_RENEWER_MODULES' models/session_lifecycle.py` | output > 1 |
| Allowlist is enforced, not just declared | `grep -c 'def test_disallowed_module_stamp_is_dropped' tests/unit/test_session_lifecycle.py` | output contains 1 |
| **No `__name__` introspection in the renewal path** (round-3 BLOCKER) | `grep -n '_getframe\|inspect.stack\|inspect.getmodule' models/session_lifecycle.py` | **no output** |
| Both durable renewers pass an explicit token | `grep -c 'renewer_module=' tools/sdlc_lease_heartbeat.py agent/session_executor.py` | 1 per file |
| **`-m` invocation regression test exists** | `grep -c 'run_name="__main__"' tests/unit/test_sdlc_lease_heartbeat.py` | output > 0 |
| **Ephemeral renewal strips the group** (Race 6 mode 2) | `grep -c 'def test_ephemeral_renewal_strips_renewer_identity' tests/unit/test_session_lifecycle.py` | output contains 1 |
| Exit clear retries a lost CAS (Race 6 mode 1) | `grep -c 'acquired' tools/sdlc_lease_heartbeat.py` | output > 1 |
| **Clear preserves the decay clock** (round-4 CONCERN) | `grep -c 'def test_drop_renewer_identity_preserves_renewed_at' tests/unit/test_session_lifecycle.py` | output contains 1 |
| Clear does not reset the TTL | `grep -c 'pttl' models/session_lifecycle.py` | output > 0 |
| **Shape (a):** the clear exists and is CAS | `grep -c 'drop_renewer_identity' models/session_lifecycle.py` | output > 1 |
| **Shape (a):** the heartbeat clears | `grep -c 'drop_renewer_identity=True' tools/sdlc_lease_heartbeat.py` | output > 0 — **not** an exact count of 2. Task 5 explicitly permits factoring the retry-plus-swallow logic into one helper called from both exits (the house style at `_log_exit`, `:244`, called from five sites), which yields a single literal occurrence. That both exits are covered is proved by the SC8 behavioural tests, not by a grep. |
| **Anti-criterion:** no ephemeral CLI renewer stamps | `grep -l 'stamp_renewer_identity' tools/_sdlc_utils.py tools/sdlc_stage_marker.py tools/sdlc_next_skill.py tools/sdlc_session_ensure.py` | **no output** (`-l` prints only filenames that match, so a clean build prints nothing; `-c` prints a `file:0` line per file and invites a builder to read the exit code as failure) |
| **Anti-criterion:** the #2620 short-circuit still exists for identity-less payloads | `grep -c 'test_peek_dead_pid_with_fresh_renewal_is_not_orphaned' tests/unit/test_session_lifecycle.py` | output contains 1 |
| Demonstrated-red test exists | `grep -c 'class TestRenewerIdentityLiveness' tests/unit/test_session_lifecycle.py` | output contains 1 |
| Reaper-layer test exists (SC7) | `grep -c 'renewer_pid' tests/unit/test_sdlc_session_ensure.py` | output > 1 |
| Feature doc updated | `grep -c 'renewer_pid' docs/features/sdlc-issue-ownership-lock.md` | output > 1 |
| SDLC skill contract updated | `grep -c 'ISSUE_LOCK_RENEWER_GRACE_SECONDS' .claude/skills/sdlc/SKILL.md` | output contains 1 |
| Timeout catalog updated (NIT 3) | `grep -c 'ISSUE_LOCK_RENEWER_GRACE_SECONDS' docs/features/config-timeout-catalog.md` | output contains 1 |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_session_lifecycle.py` | exit code 1 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness; History & Consistency | Blast radius is enumerated as two consumers but there are three. Data Flow and Architectural Impact assert `_lock_owner_is_live` is 'shared by the peek path and the orphan reaper'; `reflections/utilities.py::_lock_says_live` (:277-302) is a third wrapper, read by `reflections/sdlc_upvote_lanes.py:529,573` (gate 3) and `reflections/sdlc_progress.py:571,942,981`. At sdlc_upvote_lanes.py:530 only a `False` verdict lets the reflection proceed to create_session, so a false-dead starts an autonomous rival SDLC lane on a live run (the #1915 shape, unattended). Risk 1, Success Criteria and Test Impact never mention this layer. **Suggestion:** Correct Data Flow + Architectural Impact to list all three consumers; extend the Risk 1 per-consumer argument to the reflection gate; add a Success Criterion and a test at the `_lock_says_live` layer mirroring SC7. | **Data Flow** (three-consumer table, worst-first); **Risk 1** per-consumer argument; **Failure Path Test Strategy → Reflection-Layer Coverage**; **SC9**; Task 6; new `tests/unit/reflections/test_utilities_lock_says_live.py` | `_lock_says_live` returns `bool \| None`; callers fail closed on `None` but ACT on `False`. Every new early-return in the tightened branch must be `True` (fail-toward-live); only the full conjunction (renewer identity complete AND same-machine AND pid dead/recycled AND renewed_at age > GRACE) may return `False`. Test: patch `reflections.utilities._get_redis` to return a dead-renewer-past-grace payload and assert `_lock_says_live(N) is False`; live-renewer and inside-grace payloads must return `True`. |
| BLOCKER | Risk & Robustness; Scope & Value | `renewer_machine_id` is both redundant and unsafe. `_healed_renewal_payload`'s own docstring (models/session_lifecycle.py:1112-1118) states renewal requires a run_id match and run_ids are minted on exactly one machine, so the renewer is always on the owner's machine; both renewal branches enforce that match (:1289 CAS, :1369 default). Meanwhile `_local_machine_id()` returns `""` (not None) when unresolvable (:924-936), and the plan's 'write all three or none' rule only covers a None create_time -- so a naive equality gate makes `"" == ""` read as same-machine on a host that cannot identify itself, a false-dead on exactly the indeterminate evidence `_payload_from_same_machine` is careful to reject. Two critics flagged the same component, so this is elevated. **Suggestion:** Drop `renewer_machine_id`; stamp only `renewer_pid` + `renewer_create_time` and gate the tightened branch on the existing `_payload_from_same_machine(payload)`. If the key is kept, treat a falsy machine id on either side as incomplete identity and do not write the group at all. | **Technical Approach → Why `renewer_machine_id` is dropped**; Task 2 (writes exactly two keys); Task 3 (gates on `_payload_from_same_machine`); Verification row `No renewer_machine_id anywhere` | Gate becomes `if renewer_pid and renewer_create_time is not None and _payload_from_same_machine(payload):`. That helper (:939-959) already falls back to `payload['hostname'] == socket.gethostname()`, which the renewer group has no equivalent for. If kept, mirror the truthiness guard at :957 (`if payload_machine_id and local_machine_id:`), never a bare `==`, and gate the write as `if stamp_renewer_identity and (ct := _current_process_create_time()) is not None and (mid := _local_machine_id()):`. |
| BLOCKER | Risk & Robustness (round 2) | The composition claim has a counterexample: a live run whose heartbeat exited WITHOUT releasing. The plan asserts 'There is no payload shape reachable by a live-but-quiet local run that the tightened branch turns dead', but tools/sdlc_lease_heartbeat.py has two deadline exits that deliberately do not release -- `EXIT_UNSUPERVISED_MAX_LIFETIME` at :351-365 (UNSUPERVISED_MAX_LIFETIME_SECONDS, 90 min, :156; its comment reads 'Deliberately NO release -- failing to resolve a supervisor is not positive proof the run is dead') and `EXIT_MAX_LIFETIME` at :365 (MAX_LIFETIME_SECONDS, 4 h, :146). Only EXIT_SUPERVISOR_DEAD (:372-397) releases, and only on confirmed supervisor death (#2784). So: run alive, heartbeat ran (payload therefore DOES carry renewer_pid), heartbeat exits at its bound, supervisor blocked inside a claude -p stage issuing zero sdlc-tool writes (docs/features/config-timeout-catalog.md:85-87) -- renewed_at ages with a dead renewer_pid, complete identity, same machine, and at 180 s the tightened branch returns False. tools/sdlc_session_ensure.py:1128 is `if not owner_live: yield s` with NO idle-time gate when a payload is present, so --kill-orphans reaps the live sdlc-local-{N} anchor immediately, well inside a 6-25 min stage (today it reads live for the full 1200 s window). #2703 defect 1 is re-opened by the composition, not preserved by it. Spike-2's eight rows contain no 'renewer exited normally, run still alive' entry -- rows B/C/G all presuppose the renewer died because the run died or is restarting. **Suggestion:** Add the missing spike-2 row and close it: either treat a heartbeat's non-releasing deadline exit as a payload event (clear the renewer_* group before exiting at :359 and :365, so the payload falls back to the untouched #2620 short-circuit), or gate the tightened branch so it cannot fire against a session the reaper would reap without an idle-time check. Re-run the Risk 1 argument against this shape. | **spike-5** (row set + measured dispositions, shape (a) chosen over (b)/(c)); **Technical Approach → The hole in that argument**; **Key Elements 4**; **Task 5** (dedicated task); **Risk 5** (residual); **Race 5** (CAS); **SC8** | Clearing the group on exit is the cheaper fix and needs no predicate change: at both non-releasing returns, perform one final `touch_issue_lock(issue_number, run_id, renew_only=True)`-shaped write whose payload omits renewer_pid / renewer_create_time / renewer_machine_id. It must be compare-and-set (reuse the _RENEW_IF_VALUE_MATCHES_LUA path at models/session_lifecycle.py:1300) so a successor that already took the lease is untouched, and it must be best-effort/exception-swallowed like the release at :377-390 -- a failure to clear must never block the exit. |
| CONCERN | Risk & Robustness | The grace clock's subject is misstated. Technical Approach argues 'the check is on the renewer's pid, not on tick recency', but Solution -> Flow makes tick recency the second half of the dead branch (`renewed_at age > GRACE -> False`). The clock runs on `renewed_at` age -- re-stamped unconditionally by every same-owner renewal including all five ephemeral CLI renewers -- not on how long the renewer pid has been dead. A builder could reasonably implement the other reading, which needs state the payload does not carry and would change every spike-2 row. **Suggestion:** State the clock explicitly in Solution -> Flow ('age of `renewed_at`, not duration of renewer death') and narrow the cadence argument to what holds: cadence is irrelevant while the renewer pid resolves alive; once it resolves dead, `renewed_at` recency is the binding signal. | **Solution → Flow → The grace clock's subject**; **Risk 3** (cadence claim narrowed); Task 3 bullet on the grace clock | The condition is `time.time() - float(payload['renewed_at']) > ISSUE_LOCK_RENEWER_GRACE_SECONDS`, reusing the parse `_lock_renewal_is_fresh` (:962-980) already did. Because the branch is only reached when `_lock_renewal_is_fresh(payload) is True`, `renewed_at` is guaranteed present and float-parseable -- no second try/except needed, and do not re-derive it from a helper that can return None. |
| CONCERN | History & Consistency | Failure Path Test Strategy contradicts Task 3 on exception handling. The former says a psutil raise in the renewer-pid check 'must be caught by the existing blanket handler'; Task 3 says to wrap the new call so any exception reads LIVE. Task 3 is correct: the existing handler is inside the `try:` at models/session_lifecycle.py:1046-1063, BELOW the short-circuit at :1033 where the new branch goes. A builder following the Failure Path wording adds no handler, and the exception escapes `_lock_owner_is_live`. The reaper catches it (tools/sdlc_session_ensure.py:1120-1127) but the peek path at :1249 does not, turning it into an `acquired=False` fail-closed on a lease the caller owns. **Suggestion:** Correct the Failure Path Test Strategy bullet to require the new branch's OWN `try/except Exception -> True` (matching the posture at :1055-1063, not reusing that handler); keep Task 3's wording as the authority. | **Failure Path Test Strategy → Exception Handling Coverage** (rewritten: the branch needs its OWN handler); Task 3 | Structure the branch as `try: proc = _psutil_process_for_pid(renewer_pid) ... except Exception: return True` before any `return False` is reachable. Do NOT move the new branch inside the `try:` at :1046 -- that block is entered only after the pid / machine_id / create_time guards at :1035-1045, which a renewer-only payload need not satisfy. |
| CONCERN | History & Consistency (round 2) | Task 1's PATCH-LOCATION TRAP is correct today but the plan does not protect it against its own Task 3. The trap tells the builder that `patch("agent.session_health._psutil_process_for_pid")` is the only working target because _lock_owner_is_live does a lazy function-body import at models/session_lifecycle.py:1046. The new branch goes in at :1033, ABOVE that import, so it needs its own access to the helper. If the builder satisfies it by hoisting the import to module scope in models/session_lifecycle.py, the module-level name comes into existence and the plan's own patch guidance silently stops holding for the new TestRenewerIdentityLiveness class. **Suggestion:** Task 3 must explicitly require the new branch to use its own function-body import of _psutil_process_for_pid, matching the existing lazy import, and must forbid hoisting it to module scope. | **Task 3** (function-body import required, module-scope hoist forbidden, with the reason it would invalidate Task 1) | Write it as `from agent.session_health import _psutil_process_for_pid` INSIDE the new branch's try block, exactly mirroring models/session_lifecycle.py:1046. Do not add a module-scope import in models/session_lifecycle.py: `patch("models.session_lifecycle._psutil_process_for_pid")` currently raises AttributeError and every pre-existing test (tests/unit/test_session_lifecycle.py:564-567) relies on the agent.session_health target being the only one. |
| NIT | Scope & Value | Three statements about the reaper-layer test disagree: Task 5 mandates ADDING two tests at `_iter_orphan_sessions`; Test Impact lists tests/unit/test_sdlc_session_ensure.py only as 'VERIFY UNCHANGED ... if present'; and Verification has no row asserting the new reaper test, unlike SC1/5/6/8. The 'if present' hedge is stale -- `TestKillOrphans` is at tests/unit/test_sdlc_session_ensure.py:1212 and `_iter_orphan_sessions` at tools/sdlc_session_ensure.py:1059. **Suggestion:** Change the Test Impact row to ADD (plus verify the existing TestKillOrphans rows at :1212 unchanged) and add a Verification grep row for the new reaper test's name so SC7 is mechanically checkable. | **Test Impact** (ADD rows, `TestKillOrphans` at `:1211`); **Task 6**; Verification row `Reaper-layer test exists (SC7)` | n/a (NIT) |
| NIT | Scope & Value | Two Verification rows are ineffective. `grep -c 'GRAIN OF SALT' models/session_lifecycle.py \| output > 1` already passes on main (3 occurrences today), so it cannot detect a new constant shipped without its annotation. Separately the row greps .claude/skills/sdlc/SKILL.md for `ISSUE_LOCK_RENEWER_GRACE_SECONDS`, but the matching Documentation checkbox only asks for the orphaned_lock contract wording and never says to name the constant. **Suggestion:** Anchor the provisional row to the new constant (assert the count rises to 4, or grep the constant name inside its GRAIN OF SALT block) and add 'name ISSUE_LOCK_RENEWER_GRACE_SECONDS' to the SKILL.md documentation checkbox. | **Verification**: GRAIN OF SALT row anchored to exactly 4; **Documentation**: SKILL.md checkbox now names the constant | n/a (NIT) |
| NIT | History & Consistency | The constant-placement argument aims to avoid a 'split, undiscoverable knob', but docs/features/config-timeout-catalog.md:82-92 already catalogs ISSUE_LOCK_TTL_SECONDS with the same 'not TIMEOUTS__*' caveat, and the Documentation section does not add the new sibling there -- producing the discoverability gap the argument claims to prevent. **Suggestion:** Add a Documentation checkbox for docs/features/config-timeout-catalog.md covering ISSUE_LOCK_RENEWER_GRACE_SECONDS (and opportunistically the currently-uncatalogued ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS), with a matching Verification grep row. | **Documentation**: new `config-timeout-catalog.md` checkbox covering the grace constant and the uncatalogued freshness constant; matching Verification row | n/a (NIT) |
| BLOCKER | History & Consistency (round 3); component corroborated by Scope & Value | The `_DURABLE_RENEWER_MODULES` allowlist cannot match for the detached heartbeat, silently disabling the fix on exactly the path it was written for. Task 2 specifies the allowlist is "checked inside the renewal path against the calling module's `__name__`", with members `tools.sdlc_lease_heartbeat` and `agent.session_executor`. But `tools/sdlc_session_ensure.py:235-238` launches the heartbeat as `argv = [sys.executable, "-m", "tools.sdlc_lease_heartbeat", ...]`. Under `-m` execution the module runs as `__main__`, so every frame defined in that file resolves `f_globals["__name__"]` to `"__main__"`, never the dotted path — the file's own `if __name__ == "__main__":` guard at `tools/sdlc_lease_heartbeat.py:531` is proof it executes under that name. The detached heartbeat therefore fails the allowlist on every call and never stamps `renewer_pid` / `renewer_create_time` in production. Because Task 2 mandates the drop be **silent plus one `logger.warning`, never an exception**, the failure is invisible. `agent/session_executor.py` is unaffected (imported normally by the worker), so the defect is scoped precisely to the local-supervisor heartbeat that spike-3, spike-5 and the entire #2648 incident depend on. Worse, SC5's mandated test would still pass: unit tests reach the heartbeat via a plain `import tools.sdlc_lease_heartbeat`, where `__name__` resolves correctly — so the suite goes green while the fix is inert on every real deployment. **Suggestion:** Do not derive caller identity from frame/module `__name__` introspection at all. Have each of the two durable call sites pass an explicit, invocation-invariant caller token that `touch_issue_lock` checks against the allowlist. | **Technical Approach → The runtime allowlist / introspection trap**; **Key Element 4**; Task 2 (explicit token, introspection forbidden); Task 4 (`renewer_module=` at both sites + `-m` regression test); **SC5/SC5b**; Verification rows `No __name__ introspection`, `explicit token`, `-m regression test` | Replace the introspection with an explicit parameter: `touch_issue_lock(..., stamp_renewer_identity=True, renewer_module="tools.sdlc_lease_heartbeat")` at `tools/sdlc_lease_heartbeat.py:429` and `renewer_module="agent.session_executor"` at `agent/session_executor.py:246`, checked as `if renewer_module not in _DURABLE_RENEWER_MODULES: <drop + warn>`. Do NOT use `sys._getframe`, `inspect.stack()[n].frame.f_globals["__name__"]`, or `inspect.getmodule(frame).__name__` — all three return `"__main__"` under `-m`. Add a regression test that exercises the real invocation shape, e.g. `runpy.run_module("tools.sdlc_lease_heartbeat", run_name="__main__")` or an actual subprocess, and asserts the stamp still lands; a test using a plain `import` cannot detect this class of defect. Note the plan's own Verification rows (`_DURABLE_RENEWER_MODULES` count, `test_disallowed_module_stamp_is_dropped` presence) are existence greps and also cannot detect it. |
| CONCERN | Risk & Robustness (round 3) | Race 5 closes the deadline-exit clear against a *successor* takeover but not against a benign same-run_id ephemeral renewal, and the plan's own default renewal branch makes that window a re-poisoning path rather than merely a lost write. `models/session_lifecycle.py:1373-1380` is the same-owner default branch: a plain `_R.set(key, json.dumps(new_payload), ex=ttl)` with **no CAS**, whose payload comes from `_healed_renewal_payload`, which by design spreads the existing payload (`models/session_lifecycle.py:1084-1096`, "spread the EXISTING payload ... never reconstruct a subset"). So any of the five ephemeral CLI renewers landing after the heartbeat's CAS clear will re-carry the stale `renewer_pid` / `renewer_create_time` it read before the clear AND refresh `renewed_at`. Unlike the heartbeat's tick loop, which stops on a lost CAS via `EXIT_LEASE_LOST`, the Task 5 exit clear is a single best-effort exception-swallowed write with no retry and no verification, so the run silently re-enters spike-5 row I (false-dead, reaped within the grace on the next quiet stretch) with no backstop until the 1800 s TTL. This is the shape Task 5 exists to close. **Suggestion:** Give the exit clear a bounded re-GET/re-CAS retry, and separately make the drop durable against the non-CAS default branch. | **Key Element 0** (invariant) + **Key Element 2** (strip on non-stamping renewal); **Race 6** (both modes); Task 2 (strip); Task 5 (bounded 2-attempt retry); **SC5c** | Two parts, both needed. (1) At `tools/sdlc_lease_heartbeat.py:360` and `:365`, wrap the `drop_renewer_identity=True` call in a loop bounded to 2 attempts checking `result.acquired`; fall through to the existing swallow only after the budget is exhausted — a genuine successor (different `run_id`) still fails both attempts, preserving Race 5's guarantee. (2) A retry alone does NOT close the re-poisoning path, because the default branch at `models/session_lifecycle.py:1378` is an unconditional `_R.set` that can land at any later time. Either give `_healed_renewal_payload` an explicit drop of the `renewer_*` group unless `stamp_renewer_identity` is set for this call (making the clear sticky rather than a one-shot write), or add a `renewer_cleared_at` tombstone key the stamp path refuses to overwrite. Decide which in the revision; the current design leaves the clear defeasible by any `sdlc-tool` CLI write. |
| CONCERN | Scope & Value (round 3) | Risk 6's evidence for the runtime allowlist does not support it. The three "renewal-adjacent call sites [that] already exist outside that set" are cited as proof a future caller could stamp past the four-file grep, but all three are read-only peeks, verified: `tools/sdlc_session_release.py:92` is `touch_issue_lock(issue_number, None, peek=True)`, `tools/merge_predicate.py:656` is `touch_issue_lock(issue_number, run_id, peek=True)`, and `agent/supervised_run.py:256` is `touch_issue_lock(issue_number, None, peek=True)`. None reaches a renewal branch; each would have to be rewritten from a peek into a renewal call before the flag could matter, which is a visible edit this repo's mandatory REVIEW stage catches. The mitigation is therefore precautionary against a caller shape that does not exist, and it is the same mechanism the round-3 BLOCKER shows is broken as specified. **Suggestion:** Given the BLOCKER, decide the allowlist's fate deliberately rather than repairing it by reflex — either drop it and rely on the grep anti-criterion plus review, or keep it with an explicit caller token and restate Risk 6 as precautionary rather than evidence-backed. | **Risk 6 rewritten**: the three peek citations verified and removed, risk restated as precautionary; allowlist KEPT with the explicit token per the round-3 BLOCKER, with **Technical Approach** stating plainly it is a declaration checked against a list, not an unforgeable barrier | If dropping: remove `_DURABLE_RENEWER_MODULES` and its check from the renewal path, delete `test_disallowed_module_stamp_is_dropped`, and delete the two Verification rows "Runtime allowlist exists (Risk 6)" and "Allowlist is enforced, not just declared"; SC5 then reduces to the grep anti-criterion alone. If keeping: rewrite Risk 6 to say plainly that no current caller performs a renewal write outside the four watched files, so the allowlist guards a hypothetical, and implement it via the explicit `renewer_module` token from the BLOCKER row — never `__name__` introspection. Do not leave the three peek-site citations in place as if they were near-misses. |
| NIT | Scope & Value (round 3) | `appetite: Small` has not been re-examined across two revision rounds that each added a mechanism. The label is defended in the Appetite section on the grounds that "the reasoning ... is the expensive part", but round 2 added an entire second subsystem (the CAS `drop_renewer_identity` clear at two heartbeat exits, Task 5) and Task 2 carries the runtime allowlist, neither present in the original pid-stamp idea. Both prior rounds were scoped to correctness rather than proportionality, so nothing has tested whether the mechanism count now exceeds the appetite. **Suggestion:** Either relabel the appetite to Medium, or use the round-3 allowlist findings to trim scope back toward the original estimate. | **Appetite** relabelled Small -> Medium, with the mechanism count named (stamp/gate, CAS clear, invariant + allowlist) | n/a (NIT) |
| CONCERN | Risk & Robustness (round 4) | Shape (a)'s exit clear buys the retiring run a FRESH liveness window, so Risk 5's "backstops are unchanged" is inaccurate. Task 5 specifies `drop_renewer_identity` as "a same-owner CAS renewal", which routes through `_healed_renewal_payload` (`models/session_lifecycle.py:1084-1120`), whose docstring states `renewed_at` is "deliberately OVERWRITTEN on every renewal" and whose `:1118` is an unconditional `new_payload["renewed_at"] = time.time()`; and through the `renew_only` CAS at `:1296-1301`, which passes `ttl` (default `ISSUE_LOCK_TTL_SECONDS` = 1800) into `_RENEW_IF_VALUE_MATCHES_LUA`. So at `EXIT_UNSUPERVISED_MAX_LIFETIME` — the exit whose own comment says failing to resolve a supervisor "is not positive proof the run is dead" — the heartbeat's last act re-stamps `renewed_at` to now AND extends the lease a full 1800 s. Today that exit performs no Redis write at all, so the payload's `renewed_at` is already up to one 600 s tick stale and the TTL is decaying on its own clock. For a run that IS dead at its deadline exit, shape (a) therefore adds up to ~600 s of extra false-live plus up to ~600 s of extra lease lifetime versus `main`. spike-5 row Ia measured the verdict at an instant ("live", byte-identical to today) but not the decay clock, which is exactly where the two diverge. **Suggestion:** either make the drop write preserve the payload's existing `renewed_at` and remaining TTL, or accept the widening explicitly and correct Risk 5 plus the "Net effect" paragraph, which currently claims the blind window collapses to <=180 s without noting that post-deadline-exit runs revert to a *refreshed* 1200 s window. | **Task 5** (clear is a pure key-removal: inline CAS payload, `renewed_at` untouched, remaining TTL via `pttl` with sentinel guards); **Risk 5** ("backstops are unchanged" now stated as load-bearing on that detail); **Net effect** paragraph scoped to stamped leases; Task 1 case + Verification rows `Clear preserves the decay clock` / `Clear does not reset the TTL` | The unconditional re-stamp is `models/session_lifecycle.py:1118` (`new_payload["renewed_at"] = time.time()`) — verify the line yourself, an earlier draft of this note cited a wrong offset. Two viable shapes: (1) thread a `bump_renewed_at: bool = True` kwarg from `drop_renewer_identity` into `_healed_renewal_payload` and skip the re-stamp when `False`; or (2) have `touch_issue_lock`'s `drop_renewer_identity` branch build its CAS payload inline (`{**payload}` minus the two `renewer_*` keys, `renewed_at` untouched) instead of delegating to the helper — note this is a second, narrower exception to that helper's "spread the EXISTING payload" rule and must be commented alongside Key Element 2's. For the TTL, pass the key's remaining lifetime (`_R.pttl(key) // 1000`, guarding the `-1`/`-2` sentinels) into the CAS eval rather than the 1800 s default. If the TTL half is judged not worth the mechanism, say so in Risk 5 rather than leaving "backstops are unchanged" standing. Add a Task 1 case asserting that after a `drop_renewer_identity=True` write the stored `renewed_at` equals its pre-clear value. |
| CONCERN | History & Consistency (round 4) | The Documentation checkbox and the `Grace constant marked provisional` Verification row cannot both be satisfied. The last Documentation bullet requires that "docstrings on `_healed_renewal_payload`, `_lock_owner_is_live`, and the new constant must carry the #2648 rationale and the GRAIN OF SALT note" — three locations. `models/session_lifecycle.py` carries exactly 3 `GRAIN OF SALT` lines on `main` (verified by `grep -c`: lines 750, 842, 855, all on module-level constants), and neither function docstring carries the phrase today. A builder following the checkbox literally lands on 6, while the Verification row hard-codes "output contains 4 (exactly one more than the 3 on `main`)". A faithful build therefore goes red against its own Verification table — the same self-counting-prose trap the plan already documents for the two anti-criterion rows and for the `renewer_machine_id` row. **Suggestion:** narrow the Documentation bullet so only the new constant carries the literal marker, with the two function docstrings carrying the #2648 rationale in prose only. | **Documentation** inline bullet rewritten: literal `GRAIN OF SALT` marker on the new constant's single comment line ONLY; the two function docstrings carry the #2648 rationale in prose without the marker, keeping the `grep -c` target at exactly 4 | Reword the bullet to: "the new constant's comment carries both the #2648 rationale and the literal GRAIN OF SALT marker; docstrings on `_healed_renewal_payload` and `_lock_owner_is_live` carry the #2648 rationale in prose, WITHOUT the literal marker." `grep -c` counts matching LINES, not occurrences, so the target of 4 holds only if exactly ONE new line contains the token — keep the marker on a single comment line above the constant, matching the existing shape at `models/session_lifecycle.py:842` and `:855`. Do not "fix" this by relaxing the Verification row back to `> 1`; that is the ineffective form round 3 already replaced (NIT 2). |
| NIT | Risk & Robustness (round 4) | Task 3 specifies the new branch "Log at debug naming the dead renewer pid" — the same level as today's routine paths — even though Risk 1 names this branch's false-dead as the worst-case failure across all three consumers ("no operator in the loop" on the `_lock_says_live` to `create_session` path). An operator investigating an unexplained rival lane or a reaped `sdlc-local-{N}` anchor gets no elevated signal distinguishing "the new grace-tightening fired" from any other liveness path, unless debug logging happened to be enabled beforehand. **Suggestion:** emit the DEAD-verdict path only at `logger.info` or `logger.warning`, naming the renewer pid, the `renewed_at` age, and the grace value; keep the LIVE paths and the `except Exception -> True` fallback at debug. | **Task 3**: DEAD verdict logs at `logger.warning` naming the renewer pid, `renewed_at` age, and grace value; LIVE paths and the `except -> True` fallback stay at debug | n/a (NIT) |
| CONCERN | Risk & Robustness (round 5) | Risk 5's claim that the shape-(a) residual "does not exist at all" in worker mode is false, and it is the one residual with no covering task. The justification is "if the worker dies the run (its subprocess) dies with it", but `agent/session_runner/role_driver.py:462` spawns every role turn's `claude -p` with `start_new_session=True`, and its own inline comment reads "the worker orphan sweep reaps survivors after a crash" — language that only makes sense because survivors routinely exist. `agent/session_executor.py:246` stamps the *worker's* pid as `renewer_pid` (via `os.getpid()`), not the subprocess's, and the worker's 60 s tick loop dies with the worker. So a worker SIGKILL/OOM leaves a dead `renewer_pid` with no tick to refresh it and — unlike the heartbeat's two non-releasing exits, which Task 5 covers — **no clear mechanism at all**, because Task 5 only edits `tools/sdlc_lease_heartbeat.py`. After 180 s of quiet the tightened branch reads DEAD against an orphaned subprocess that may still be doing real work. This is spike-5 row Ib on the worker side, and the plan currently asserts it away rather than recording it. **Suggestion:** correct Risk 5 and the parallel sentence in Technical Approach to state the worker-mode residual is symmetric to row Ib (anomaly-conditioned, same three-part conjunction, same backstops) rather than absent. | **Risk 5** rewritten: worker-mode residual stated as symmetric to row Ib (same three-part conjunction, same backstops), not absent; parallel sentence in **Technical Approach** corrected. No code change — a crash cannot run cleanup code. | No code change is required — a crash cannot run cleanup code, which is row Ib's own reasoning. This is a prose fix in two places: Technical Approach's "In worker mode the residual does not exist at all" sentence and Risk 5's paragraph repeating it. Replace with: the worker-mode residual exists and is bounded by the same conjunction (worker dies without graceful shutdown; the orphaned `claude -p` survives and does real work; the run then goes >= GRACE with zero `sdlc-tool` lock writes), with the same backstops (1800 s TTL, any ephemeral write resetting the grace clock). If the team wants to narrow it rather than document it, the analog to Task 5 is a `drop_renewer_identity=True` call from the worker's startup orphan sweep before it reaps a surviving subprocess — but that is a scope decision, not mandated by this finding. Do NOT close it by widening `ISSUE_LOCK_RENEWER_GRACE_SECONDS`; the 210 s incident ceiling (n=1) is the binding constraint. |
| CONCERN | History & Consistency (round 5) | The Verification row `grep -c 'drop_renewer_identity=True' tools/sdlc_lease_heartbeat.py \| output contains 2` (`:1605`) fails on a faithful, well-factored build. Task 5 mandates non-trivial shared logic at each call site — a bounded 2-attempt re-GET/re-CAS retry checking `result.acquired` plus best-effort exception-swallowing — at BOTH non-releasing exits, which sit four lines apart (`tools/sdlc_lease_heartbeat.py:359` and `:365`, verified). That file already factors exactly this kind of shared exit logic into a helper: `_log_exit` is defined at `:244` and called from five exit sites. A builder who writes one `_clear_renewer_identity_at_exit()` helper containing a single `touch_issue_lock(..., drop_renewer_identity=True, ...)` call and invokes it from both exits produces ONE literal occurrence, not two, and turns a correct build red against its own table. Nothing in Task 5 forbids the factoring. **Suggestion:** either relax the row to `output > 0` (the plan's own precedent for shape-dependent counts, e.g. the `acquired` row at `:1601` uses `> 1`), or add an explicit instruction to Task 5 to inline the call at both sites. | **Verification** row relaxed to `> 0` with the reason inline; **Task 5** states that factoring the retry-plus-swallow into one helper is expected and correct (house style `_log_exit`), and that SC8's behavioural tests prove both exits are covered | Pick one and make the two locations agree. If keeping the exact count, add to Task 5: "Write the `touch_issue_lock(..., drop_renewer_identity=True)` call inline at BOTH `EXIT_UNSUPERVISED_MAX_LIFETIME` (`:359`) and `EXIT_MAX_LIFETIME` (`:365`); do not factor the retry/CAS/swallow logic into a shared helper — the Verification row's exact count of 2 depends on it." If relaxing instead, change the row at `:1605` to `output > 0` and keep the "both exits clear" obligation on SC8 and the Task 5 test ("both exits perform the clear"), which check behavior rather than token count and cannot be defeated by factoring. The second option is the more robust pairing; the first preserves the exact-count discipline rounds 3-4 established. |
| NIT | History & Consistency (round 5) | The Verification row `Allowlist is enforced, not just declared` (`:1596`) greps for a test named exactly `test_disallowed_module_stamp_is_dropped`, and the round-3 Critique Results entry names it as part of the accepted remedy, but `## Test Impact` never lists adding it — unlike every other named-test Verification row (`TestRenewerIdentityLiveness`, `test_ephemeral_renewal_strips_renewer_identity`, `test_drop_renewer_identity_preserves_renewed_at`), all of which appear in Test Impact with matching names. **Suggestion:** add the missing Test Impact bullet. | **Test Impact**: added the `test_disallowed_module_stamp_is_dropped` bullet naming the exact test the Verification row greps | n/a (NIT) |
| NIT | Scope & Value (round 5) | spike-4's ephemeral-renewer inventory over-counts. `tools/sdlc_next_skill.py:638` is the file's only `touch_issue_lock` call and it is `peek=True` (verified) — a read-only peek that never reaches a renewal branch, so it cannot "re-poison the field" (Risk 2) and needs no protection from the strip invariant. The true production renewal/mint call sites outside the two durable renewers are four across three files (`tools/_sdlc_utils.py:609`, `:834`, `tools/sdlc_stage_marker.py:884`, `tools/sdlc_session_ensure.py:539`), not "five ephemeral CLI renewers" across four files as repeated in spike-4, Key Element 2, Race 6 mode 2, Task 4 and SC5c. **Suggestion:** correct the count in every location, and either drop `tools/sdlc_next_skill.py` from the anti-criterion file list or keep it explicitly as a defensive inclusion rather than as evidence of a fifth renewer. | **spike-4** corrected: four ephemeral renewal call sites across three files; `tools/sdlc_next_skill.py:638` reclassified as a read-only peek and kept in the anti-criterion list as a defensive inclusion. Count fixed in spike-4, Key Element 2, Race 6, Risk 2, Risk 3, Solution -> Flow and SC5c | n/a (NIT) |

---

## Decided Defaults

Nothing here blocks build. Both items survived four critique rounds without
being challenged on their substance, so both are **decided**. They are recorded
because the evidence behind them is thin (see the n=1 caveat), not because they
are open — a future incident observation is the trigger to revisit them.

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
