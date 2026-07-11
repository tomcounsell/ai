---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-11
tracking: https://github.com/tomcounsell/ai/issues/2007
last_comment_id: null
revision_applied: true
---

# Teammate cold-start session never finalizes to a terminal status

## Problem

A `teammate`-type `AgentSession` (`tg_psyoptimal_-1002600253717_2745`,
`agent_session_id=0f6f86aa6aea4dfeba82bb877f5b9987`) got stuck indefinitely in
`status="running"` with no live process behind it, *after* it had actually
finished its work (delivered its reply, cleaned up its git branch). It had to be
killed manually. Two distinct defects, discovered together during one incident
on 2026-07-10 (machine "Valor the Captain"), share a single root cause:
**duplicate `AgentSession` records for one `session_id` in divergent statuses,
mishandled at two sites.**

### Defect A — pickup loop spins silently forever on a duplicate-record status conflict

Two records existed for the same `session_id`: one `pending`, one `failed`. For
~5 minutes (07:18:53–07:23:23 UTC) every worker tick tried to transition the
`pending` record to `running`, re-read the on-disk record, found `failed`, and
raised `StatusConflictError`. The pop loop catches it, logs a WARNING, releases
the slot, and `continue`s — with no attempt counter, no dedup, no escalation.
The `pending` index entry never clears (the transition keeps failing CAS), so
the loop re-pops the same session every tick forever. It only stopped when a
human ran `agent_session_scheduler cleanup --age 30`.

### Defect B — a completed cold-start run never calls `finalize_session()`

Once the duplicate was cleared, the `pending` record won the pop and transitioned
`pending→running`. Its persisted resume scalars failed `_resume_invalid_reason()`
(`missing runner_cwd`), so it correctly took the cold-start-with-prime fallback
(`agent/session_runner/runner.py` — an intentional recovery tier, **not** the
bug). It ran, delivered a reply, ran branch cleanup ("Auto-marked session done"),
and wrote a `complete` snapshot log. But **no `finalize_session()` call and no
`LIFECYCLE ... transition=running→completed` line ever appeared.** `status`
stayed `running` forever, `claude_pid` was `None`, `last_heartbeat_at` went stale.

**Current behavior:**
- Duplicate divergent-status records for one `session_id` cause an unbounded,
  silent retry loop in the pickup path (Defect A).
- A real completion path (teammate, cold-start fallback, headless runner) finishes
  real work but leaves a phantom `running` record (Defect B).

**Desired outcome:**
- Every code path that finishes executing a session reaches a terminal status via
  `finalize_session()` before the worker moves on, with no silent gaps.
- Duplicate records for one `session_id` in divergent statuses get reconciled
  automatically, and repeated `StatusConflictError`s on the same `session_id`
  escalate loudly within a bounded number of attempts instead of spinning silently.

## Freshness Check

**Baseline commit:** `3859f490` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-10T08:44:09Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `bridge/session_transcript.py:296` — `complete_transcript` uses the blind
  `list(AgentSession.query.filter(session_id=session_id))[0]` pattern (NOT cited
  in the issue by line, but the confirmed Defect B site) — **still holds**.
- `agent/session_executor.py:2150-2247` — completion/finalization block; the
  `complete_transcript` call at line 2162 and the exception-only fallback finalize
  at 2188 — **still holds** (post-incident commit `2f324bff` refactored this block
  but preserved the shape; the fallback still fires *only* when `complete_transcript`
  raises, not when it silently no-ops).
- `agent/session_executor.py:2387-2430` — "Auto-marked session done" branch-cleanup
  block that runs *after* finalization — **still holds** (line drift from the
  issue's prose; block is at 2387 now).
- `agent/agent_session_queue.py:1533-1550` — pop-loop `StatusConflictError` handler,
  logs WARNING + `continue`, no counter/escalation — **still holds**.
- `agent/agent_session_queue.py:303-331` — `enqueue_agent_session` `_mark_superseded`
  targets only `completed` duplicates and even that branch is a documented no-op (the
  in-source #730 comment at lines 319-322 records the `reject_from_terminal` override was
  removed, so `completed→superseded` is guard-rejected). Net: it reconciles nothing, so
  every divergent record (including the stale `failed`) survives — **still holds** (this
  is Defect A's duplicate-creation mechanism).
- `agent/session_runner/runner.py` cold-start-with-prime fallback — **still holds**
  (intentional recovery tier; issue's line range `392-422` drifted under commit
  `ffed9ba0` which touched this file, but the fallback logic is unchanged).
- `models/session_lifecycle.py:112` `get_authoritative_session` — the running-preferring
  tie-break helper "created to replace the blind `list(...)[0]` pattern used in 15+
  call sites" — **still holds**; `complete_transcript` is an un-migrated call site.

**Cited sibling issues/PRs re-checked:**
- #875 — CLOSED (CAS status authority). Root-cause fix for the earlier race family;
  Defect A shows the *pickup loop's reaction* to a CAS conflict is still a gap.
- #1208 / #1210 — CLOSED / MERGED ("kill is terminal"). The terminal-state guard in
  `finalize_session` is directly relevant: it is what silently swallows a finalize
  aimed at the wrong (terminal) duplicate record.
- #1979 — **now CLOSED** (issue described it as open) and its fix **PR #2006 MERGED**
  2026-07-10T07:41:13Z (issue described it as unmerged). #2006 touches
  `session_health.py`'s Delivery-guard recovery logic, not the pickup-loop or
  cold-start-completion paths this issue is about — confirmed it would not have
  prevented either defect here. No premise change.
- #1721 — CLOSED (Granite resume). Different subsystem; no impact.

**Commits on main since issue was filed (touching referenced files):**
- `1b1d1778` (SDLC issue-keyed stage ledger) — touched `models/session_lifecycle.py`
  (+78), `models/agent_session.py`. Additive stage-ledger work; did not change the
  finalize/CAS path shape. Irrelevant to both defects.
- `2f324bff` (SDLC substrate: run_id ownership) — touched `session_executor.py` (+77)
  and `session_lifecycle.py` (+243) heavily. Refactored the finalization block but
  preserved the `complete_transcript`→`finalize_session` shape and the exception-only
  fallback. Does NOT fix either defect. Line references drifted; corrected above.
- `ffed9ba0` (Resilience hygiene sweep) — touched `agent_session_queue.py`,
  `session_executor.py`, `session_runner/runner.py`. Cosmetic/logging changes in the
  pop path; the `StatusConflictError` handler still lacks a counter/escalation.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/agent-session-outcome-verification.md` (status: Planning, tracking #1267,
  Large) — verifies agent *self-attestation of outcomes* (hallucinated PR URLs) against
  reality. Tangential: it touches session-completion classification, not terminal-status
  finalization. **Not a blocker**; coordinate only if it starts editing
  `session_executor.py`'s completion block or `complete_transcript`.

**Notes:** The incident's raw worker logs have rotated away (`worker.log.1` is from
2026-07-07, before the 2026-07-10 incident), so the exact log-level trigger for
Defect B's silent finalize-miss cannot be replayed from logs. The fix is designed to
be robust to the two remaining candidate triggers (see Spike Results) — a guaranteed
post-completion finalize closes the gap regardless of which one fired.

## Prior Art

- **#875** (CLOSED): Promoted `session_lifecycle.py` to the CAS status authority.
  Working as designed here — Defect A is the *pickup loop's* silent reaction to a
  legitimate CAS conflict, not a CAS bug.
- **#1208 / #1210** (CLOSED / MERGED): "Kill is terminal" — `finalize_session`
  raises `StatusConflictError` (or idempotency-skips) rather than re-classifying a
  terminal session. This guard is exactly what silently absorbs a finalize aimed at
  the wrong (terminal) duplicate in Defect B.
- **#730** (CLOSED): Removed the `reject_from_terminal=False` override from
  `_mark_superseded` — terminal sessions must never be re-activated/re-transitioned (the
  in-source comment at agent_session_queue.py:319-322 records this). It directly constrains
  Defect A's reconciliation: we may **not** re-add that override; we delete instead.
- **#783** (CLOSED): "AgentSession status index corruption: ghost running sessions
  from lazy-load and delete-and-recreate bugs" — the closest prior art. Same symptom
  class (ghost `running` records) from divergent duplicate records. Read its
  resolution before building; the reconciliation approach here should not regress it.
- **#1979 / PR #2006** (CLOSED / MERGED): The *opposite* failure mode — a resumed
  session force-finalized to `completed` too early off a stale delivery timestamp.
  Fixed in `session_health.py`; disjoint from this issue's paths.
- **#1721** (CLOSED): Granite resume-state loss — different (PTY) subsystem.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Didn't Cover This |
|-----------|-------------|--------------------------|
| PR for #875 | Made `finalize_session` the CAS authority; conflicts raise `StatusConflictError` | Correct by design — but nothing decides *what to do* when the pickup loop hits that conflict repeatedly. The loop just retries. |
| PR for #1208/#1210 | `finalize_session` refuses terminal→different-terminal | Protects integrity, but silently swallows a finalize aimed at the wrong duplicate — leaving the *real* running record un-finalized (Defect B). |
| PR for #730 | Removed the `reject_from_terminal=False` override in `_mark_superseded` so terminal records are never re-activated | Correct — but it left `_mark_superseded` a no-op (`completed→superseded` is now guard-rejected), so no divergent duplicate is ever reconciled. The gap is a *reconciliation* mechanism that respects #730, not a re-added override. |
| `get_authoritative_session` (#783-era) | Running-preferring tie-break to replace blind `[0]` at "15+ call sites" | `complete_transcript` was never migrated — it still uses blind `sessions[0]`, so the terminal-transition entry point can target the wrong record. |

**Root cause pattern:** Duplicate divergent-status records for one `session_id`
are tolerated (created by `enqueue_agent_session`, whose `_mark_superseded`
reconciles *nothing* — its only branch targets `completed` duplicates, and
`completed→superseded` is itself rejected by the terminal guard that #730
deliberately locked down, so every divergent duplicate survives) and then
mishandled by two consumers that assume a single record: the pop loop (spins) and
`complete_transcript` (finalizes the wrong one or none). Every prior fix hardened
the *primitive* (`finalize_session` CAS) without hardening the *consumers* that
must react to divergent records.

## Spike Results

Investigation was a synchronous code-read trace (Phase 1.5, method: code-read),
performed inline during planning — no incident logs were available to replay.

### spike-1: Where does the cold-start completion path exit without `finalize_session()`?
- **Assumption**: "The teammate/cold-start completion path exits without routing to `finalize_session()`."
- **Method**: code-read (`session_executor.py` 1905-2440, `session_transcript.py` 252-334, `session_lifecycle.py` 112-450)
- **Finding**: The path DOES route to a finalizer — `session_executor.py:2162` calls
  `complete_transcript(session_id, status)`, which at `session_transcript.py:315-317`
  calls `finalize_session`. The gap is upstream selection: `complete_transcript:296`
  uses blind `list(...)[0]` instead of `get_authoritative_session`. With divergent
  duplicates present, `sessions[0]` can be a terminal (e.g. `failed`) record →
  `finalize_session` hits the reject-from-terminal guard or idempotency skip → the
  actual `running` record is never finalized. The exception-only fallback at
  `session_executor.py:2188` does NOT fire because `complete_transcript` returned
  normally (it swallowed the `StatusConflictError` at its own line 318). **Two
  candidate triggers remain, both closed by the same fix:** (a) `sessions[0]` was a
  terminal duplicate → guard-swallow; (b) the filter returned only a stale record /
  the running record's index entry was inconsistent → silent no-op.
- **Confidence**: high (on the fix shape); medium (on which of the two triggers fired in the incident)
- **Impact on plan**: The **load-bearing** fix is a guaranteed, unconditional
  post-completion finalize in the executor that re-reads the authoritative session
  and finalizes it if still `running` — this alone closes the phantom-running bug,
  robust to both triggers, regardless of which record `complete_transcript` picked.
  Migrating `complete_transcript`'s selection to `get_authoritative_session` is a
  separate, smaller correctness improvement: its only *independent* effect (once the
  executor guard exists) is that the SESSION_END summary and the pre-finalize
  `s.summary` write land on the authoritative (`running`) record instead of a blind
  `[0]` duplicate. It is not what closes the phantom-running bug.

### spike-2: Is this specific to teammate / cold-start, or general?
- **Assumption**: "Only teammate cold-start sessions hit this."
- **Method**: code-read
- **Finding**: General. `complete_transcript` and the completion block are session-type
  agnostic; `eng` sessions on the same runner use the identical path. The teammate
  cold-start case only made a duplicate record *more likely* (the self-draft/delivery-
  validator retry that produced this session's `message_text`). Any session_type with a
  divergent duplicate is exposed.
- **Confidence**: high
- **Impact on plan**: Fix is session-type agnostic. Regression test asserts the
  invariant for a teammate session (matching the incident) but the fix covers all types.

### spike-3: Where does the duplicate divergent record get created (Defect A)?
- **Assumption**: "A retry/requeue path creates a second record without reconciling the first."
- **Method**: code-read (`agent_session_queue.py:303-354`)
- **Finding**: `enqueue_agent_session` → `_mark_superseded` (line 306) targets only
  **`completed`** duplicates — and even that branch is a **documented no-op**: the
  in-source #730 comment (lines 319-322) records that the `reject_from_terminal`
  override was deliberately removed, so `completed→superseded` is rejected by the
  terminal guard and the completed record is left intact. Net effect today:
  `_mark_superseded` supersedes *nothing*. Every prior record for the `session_id`
  survives — including the stale `failed`/`killed`/`abandoned` record — producing the
  `(failed, pending)` divergent pair. This matches the incident exactly.
- **Confidence**: high
- **Impact on plan**: Reconcile stale **terminal** duplicates by **removing** them via
  the ORM `instance.delete()` (child-reference guarded) before `async_create`ing the
  new `pending` record. Do **not** re-introduce a `reject_from_terminal=False`
  supersede override — #730 forbids re-activating/transitioning terminal records, and
  the supersede transition is guard-rejected anyway. Never touch a `running`/`pending`
  record. See the resolved reconciliation decision below (delete, not supersede).

## Data Flow

1. **Entry point**: A message re-enqueues a session (self-draft/delivery-validator
   retry) → `enqueue_agent_session` (`agent_session_queue.py:1292`).
2. **Record creation**: `_mark_superseded` is a no-op (its `completed→superseded`
   transition is guard-rejected per #730; it never targeted `failed` at all) →
   `async_create` new `pending` record. Every prior record survives, including the
   stale `failed` → divergent pair.
3. **Pickup**: worker loop `_pop_agent_session` reads `pending`, `transition_status(→running)`
   CAS-re-reads, finds `failed` on disk (tie-break/index ambiguity) → `StatusConflictError`.
4. **Defect A**: pop-loop handler (`agent_session_queue.py:1534`) logs WARNING, releases
   slot, `continue`s — forever. The fix bounds this: a loop-local per-`session_id`
   counter escalates to a single ERROR at threshold N and then strikes the stuck record's
   pending-index entry (terminal action) so it stops being re-popped; an `escalated` set
   ensures the ERROR fires once, not every tick.
5. **After human dedup**: `pending` wins the pop, `pending→running` logs, session runs the
   cold-start-with-prime path and completes real work.
6. **Finalization**: `session_executor.py:2162` → `complete_transcript` → blind `sessions[0]`
   selection → `finalize_session` on the wrong/terminal record → guard-swallow or no-op.
7. **Defect B / Output**: the real `running` record is never finalized → phantom `running`
   until manual kill.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none to public signatures. An unconditional post-completion
  finalize guard is added in `session_executor.py` (the load-bearing fix).
  `complete_transcript` internal record selection changes from `list(...)[0]` to
  `get_authoritative_session`. `_mark_superseded` (a current no-op) is replaced by a
  child-guarded delete of stale terminal duplicates. A per-session_id conflict counter
  and an `escalated` set are added to the worker-loop local state.
- **Coupling**: decreases — `complete_transcript` stops re-implementing record selection
  and reuses the canonical `get_authoritative_session`.
- **Data ownership**: unchanged; `finalize_session` remains the sole terminal-status authority.
- **Reversibility**: high — all three changes are localized and independently revertible.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 remaining (reconciliation approach, escalation surface, and PR-split are
  all resolved in-plan; see Resolved Decisions)
- Review rounds: 1

The two defects share a root cause but have distinct blast radii (queue-loop
robustness vs. lifecycle-finalization correctness). Kept in one plan as two clearly
separated tracks; the shared reconciliation logic argues against splitting.

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are internal
to the worker/queue/lifecycle code and testable against the local Redis test DB.

## Solution

### Key Elements

- **Guaranteed terminal finalize on the completion exit (Defect B)**: after the runner
  completes, the executor must guarantee the *authoritative* session reaches a terminal
  status before moving on — not rely on `complete_transcript` having selected the right
  record.
- **Correct record selection in `complete_transcript` (Defect B)**: replace the blind
  `sessions[0]` with `get_authoritative_session`, which prefers the `running` record.
- **Duplicate reconciliation at enqueue (Defect A)**: the current `_mark_superseded` is a
  no-op — `transition_status(completed→superseded)` is rejected by the terminal-status
  guard (#730 removed the `reject_from_terminal=False` override, and re-adding it would
  re-introduce the #730 terminal-re-activation bug). Replace it with a reconciler that
  **deletes** stale terminal duplicates via ORM `instance.delete()`, child-guarded (skip
  the delete if `get_child_sessions()` is non-empty), before creating the new `pending`
  record, so a divergent `(terminal, pending)` pair is never born. Never touch
  `running`/`pending` records; never use raw Redis.
- **Bounded escalation in the pop loop (Defect A)**: count consecutive
  `StatusConflictError`s per `session_id`; at threshold N=3, if not already escalated, log
  ERROR **once**, record the `session_id` in an `escalated` set, and strike the stuck
  `pending` record from the pending index via a terminal ORM transition
  (`transition_status(→cancelled)` / the `cancel_pending_session` path) so the loop stops
  re-popping it. Reset the counter and the `escalated` entry on any successful pop. No
  health/reflection signal and no in-loop reconciler (keeps the pop loop's blast radius
  minimal).

### Flow

Runner completes → executor re-reads authoritative session → if still `running`,
`finalize_session(...)` (belt-and-suspenders) → terminal status logged → worker moves on.

Re-enqueue → child-guarded delete of stale terminal duplicates → single `pending`
record → clean pickup.

Pop hits repeated conflict → counter increments → at threshold N=3: one ERROR log,
`session_id` added to `escalated`, stuck `pending` record struck from the index via a
terminal transition → loop no longer spins silently.

### Technical Approach

- **`bridge/session_transcript.py:296`**: `s = get_authoritative_session(session_id)`
  (import from `models.session_lifecycle`); keep the `waiting_for_children` and
  terminal-vs-non-terminal branches. Handle `None` (no record) with a WARNING.
- **`agent/session_executor.py` (~2150-2247)**: after the existing
  `complete_transcript` call (and outside its `try/except`, on the non-deferred exit),
  add an unconditional guard: re-read `get_authoritative_session(session_id)`; if it is
  not `None` and `status == "running"`, call `finalize_session(_auth, _runner_final_status(...))`,
  catching `StatusConflictError` as success. This subsumes the current exception-only
  fallback at 2188 (which only fires when `complete_transcript` *raises*). Keep the
  `defer_reaction` guard so the nudge path is untouched.
- **`agent/agent_session_queue.py:306` `_mark_superseded` (currently a no-op)**: replace
  the rejected `transition_status(completed→superseded)` with a reconciler that iterates
  ALL terminal duplicates (`failed`/`killed`/`abandoned`/`completed`/`cancelled`) for the
  `session_id` and **deletes** each via ORM `instance.delete()`, guarded by
  `get_child_sessions()` (skip the delete when children exist so a parent-of-children is
  never orphaned). Do NOT use `reject_from_terminal=False` (#730 — that override was
  removed on purpose; re-adding it re-opens terminal re-activation). Keep the surrounding
  `try/except` WARNING so a delete failure never blocks the new record's creation. Never
  touch `running`/`pending`; ORM-only, never raw Redis.
- **`agent/agent_session_queue.py:1534` pop-loop handler**: maintain a `dict[str, int]` of
  consecutive conflict counts AND an `escalated: set[str]`, both local to
  `_run_worker_loop` (reset the counter and drop the `escalated` entry on any successful
  pop of that `session_id`). At threshold N=3, if the `session_id` is not already in
  `escalated`: log ERROR once, add it to `escalated`, and strike the stuck `pending`
  record from the pending index via a terminal ORM transition
  (`transition_status(→cancelled)` / the `cancel_pending_session` path). No
  health/reflection signal and no in-loop reconciler — the ERROR log plus the index-strike
  make the wedge operator-visible and self-clearing within a bounded number of ticks.
- Reference `docs/infra/` scan: no `docs/infra/` entries constrain this work.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `complete_transcript`'s `except StatusConflictError` (session_transcript.py:318) —
  after migration, add a test asserting it logs at INFO and the authoritative running
  record is still finalized elsewhere (the executor guard), not left running.
- [ ] The new executor completion-guard's `except StatusConflictError` — test asserts it
  is treated as success (another actor already finalized) and never leaves `running`.
- [ ] The reconciler's `except Exception` (agent_session_queue.py:330) — test asserts a
  reconciliation (delete) failure logs a WARNING and does not block the new record's creation.
- [ ] Child-guarded delete skip — test asserts a terminal duplicate that HAS child sessions
  is NOT deleted (no orphaned parent link) and the new `pending` record is still created.

### Empty/Invalid Input Handling
- [ ] `get_authoritative_session` returning `None` in `complete_transcript` — test the
  no-record path logs a WARNING and does not raise.
- [ ] Pop-loop conflict counter with an unknown/empty `session_id` — assert no KeyError.

### Error State Rendering
- [ ] Defect A escalation must be operator-visible AND bounded: test that at the conflict
  threshold an ERROR-level line is emitted exactly once (not per-tick), the session_id lands
  in the `escalated` set, and the stuck `pending` record is struck from the pending index
  (subsequent ticks no longer re-pop it and emit no further ERROR).

## Test Impact

- [ ] `tests/unit/test_session_transcript.py` (if present) — UPDATE: `complete_transcript`
  now selects via `get_authoritative_session`; update any test that asserted blind
  `[0]` selection or single-record behavior.
- [ ] `tests/unit/test_agent_session_queue.py` (if present) — UPDATE: the reconciler now
  **deletes** stale terminal duplicates (child-guarded) instead of superseding `completed`
  ones; replace any supersede-only-completed assertion (which tested a no-op) with a
  delete-and-child-guard assertion. Add pop-loop counter/`escalated`/strike-index coverage.
- [ ] `tests/unit/test_session_lifecycle.py` (if present) — no change expected;
  `finalize_session`/`get_authoritative_session` behavior is unchanged. Verify no test
  asserted `complete_transcript` leaves a running record un-finalized.
- [ ] New: `tests/unit/test_teammate_cold_start_finalize.py` — REPLACE/CREATE: regression
  for Defect B (running record reaches terminal) and Defect A (divergent pair reconciled;
  pop loop escalates within N).

The exact filenames are verified in build (Test Impact assumes the conventional
`tests/unit/test_<module>.py` layout); a builder confirms via `ls tests/` before editing.

## Rabbit Holes

- **Do NOT rewrite `finalize_session` / the CAS design.** #875 settled that; the primitive
  is correct. The bug is in the *consumers*.
- **Do NOT migrate all "15+ blind `[0]` call sites" in this plan.** Only
  `complete_transcript` is load-bearing for this incident. A fleet-wide migration is a
  separate chore (see No-Gos).
- **Do NOT try to eliminate duplicate records entirely at the Popoto layer.** Reconcile
  them at the known creation site; a global uniqueness constraint is a large, risky refactor.
- **Do NOT couple Defect A's escalation to the Delivery guard (#2006 / `session_health.py`).**
  Different path; keep the escalation in the queue loop.

## Risks

### Risk 1: Reconciling terminal duplicates deletes a record another actor needs
**Impact:** Losing audit history, orphaning child sessions, or racing a concurrent finalize.
**Mitigation:** Only reconcile records whose status is **terminal** (never `running`/
`pending`) — a terminal duplicate of a `session_id` that already has a live successor has
low residual audit value. Guard the delete with a child-reference check
(`get_child_sessions()`); skip the delete when children exist so no parent link is orphaned.
Reconcile via ORM `instance.delete()` — **not** a `reject_from_terminal=False` supersede
override, which #730 deliberately removed to keep terminal records from being re-activated.
All mutations go through the ORM/lifecycle API (never raw Redis). CAS in `finalize_session`
still guards concurrent transitions, and any residual divergent pair is caught by the
bounded pop-loop escalation.

### Risk 1b: Escalation re-fires ERROR every tick if the terminal action no-ops
**Impact:** Log flooding — if the threshold is reached but the strike-the-index action
fails to remove the entry (e.g. a transient ORM error), ERROR would fire on every
subsequent tick forever (the failure mode the critique flagged).
**Mitigation:** A loop-local `escalated: set[str]` gates the ERROR + strike action to fire
exactly once per session_id. The strike transitions the stuck `pending` record to a terminal
status, removing it from the pending index so it is no longer re-popped — the escalation is
self-terminating, not a per-tick log. The `escalated` entry is pruned on any successful pop.

### Risk 2: The executor completion-guard double-finalizes (races `complete_transcript`)
**Impact:** A redundant terminal write or a spurious conflict log.
**Mitigation:** The guard re-reads the authoritative session and only acts if it is still
`running`; `finalize_session`'s idempotency early-return and CAS make a redundant call a
no-op, and `StatusConflictError` is caught as success.

### Risk 3: The pop-loop conflict counter grows unbounded / mis-keys
**Impact:** Memory growth or a stuck counter across worker lifetime.
**Mitigation:** Counter is a loop-local dict, reset on any successful pop of that
session_id and pruned when reconciliation resolves the conflict; keyed strictly by
`session_id`.

## Race Conditions

### Race 1: Concurrent finalize between `complete_transcript` and the executor guard
**Location:** `agent/session_executor.py` completion block (~2150-2247) and
`bridge/session_transcript.py:315-317`
**Trigger:** `complete_transcript` finalizes the authoritative record, then the new guard
re-reads and also attempts to finalize.
**Data prerequisite:** The authoritative session's `status` must be re-read fresh inside the guard.
**State prerequisite:** `finalize_session` idempotency (already-terminal → early return) and CAS hold.
**Mitigation:** Guard acts only if re-read status is still `running`; `StatusConflictError`
and idempotency make the second call a safe no-op.

### Race 2: Two workers reconcile the same divergent pair at enqueue
**Location:** `agent/agent_session_queue.py:306` `_mark_superseded` (renamed reconciler)
**Trigger:** Two re-enqueues for the same `session_id` interleave.
**Data prerequisite:** Each reconciler re-reads the current record set before deleting.
**State prerequisite:** Single-writer ownership per `session_id` at the queue level.
**Mitigation:** The reconciler re-reads the terminal duplicates before deleting; a delete of
an already-deleted record is wrapped so a losing writer's `instance.delete()` is a caught,
non-fatal no-op. The whole reconcile is inside the existing `try/except` that logs a WARNING
and never blocks the new record's creation.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #783] Fleet-wide migration of the remaining blind
  `list(AgentSession.query.filter(session_id=...))[0]` call sites (health_check.py,
  sdk_client.py, hooks, etc.) to `get_authoritative_session`. Only `complete_transcript`
  is fixed here; the broader migration is tracked under the ghost-running-sessions family
  (#783 is the closest existing tracker — a fresh issue will be filed if #783 is deemed
  too stale to reopen).
- Nothing else deferred — every relevant item (both defects, reconciliation, escalation,
  regression tests, docs) is in scope for this plan.

## Update System

No update system changes required — this feature is purely internal to the
worker/queue/lifecycle code. No new dependencies, no config files, no Popoto schema
changes (the fix reuses existing fields and the existing reflection surface), so no
migration in `scripts/update/migrations.py` is needed.

## Agent Integration

No agent integration required — this is a worker/bridge-internal correctness fix. No new
CLI entry point, no MCP surface, no `.mcp.json` change. The existing operator surfaces
(`python -m tools.valor_session status/inspect`, the dashboard) already expose session
status and will reflect the fix (sessions reaching terminal status; no phantom `running`
records). Integration coverage is via the regression tests, not a new agent capability.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-lifecycle.md` — document this failure mode (divergent
  duplicate records → silent finalize-miss and pop-loop spin) and its fix (guaranteed
  completion-exit finalize as the load-bearing fix, `get_authoritative_session` in
  `complete_transcript`, enqueue-time child-guarded delete of stale terminal duplicates,
  and bounded self-terminating pop-loop escalation).
- [ ] Verify the `docs/features/README.md` index entry for session-lifecycle is current
  (add a note if the failure-mode section is newly named).

### Inline Documentation
- [ ] Comment the new executor completion-guard explaining why it is unconditional (subsumes
  the exception-only fallback) and why it is scoped to the non-deferred exit.
- [ ] Update the reconciler's docstring/comment to state it deletes stale terminal
  duplicates (child-guarded) and to cite #730 (why it deletes rather than re-activates).

## Success Criteria

- [ ] A teammate session that completes via the cold-start-with-prime fallback reaches a
  terminal `AgentSession.status` (via the unconditional executor completion guard calling
  `finalize_session()`) before the worker moves on — verified by a `LIFECYCLE` transition
  log line and `status` reading `completed`/`failed` (not `running`) after the run. This
  holds even when `complete_transcript` selected a different (terminal) record, proving the
  executor guard — not the `complete_transcript` migration — is the phantom-running fix.
- [ ] A regression test reproduces Defect B: a teammate session with invalid/missing resume
  scalars (and/or a divergent duplicate present) runs to completion and is asserted to reach
  a terminal status, not stay `running`.
- [ ] Two `AgentSession` records sharing one `session_id` in divergent statuses no longer
  cause the pop loop to retry silently forever — the stale terminal duplicate is deleted at
  enqueue (child-guarded), AND any residual divergent pair escalates to a single ERROR
  within N attempts and is struck from the pending index (self-terminating, not per-tick).
- [ ] `docs/features/session-lifecycle.md` documents this failure mode and its fix.
- [ ] `complete_transcript` no longer uses blind `sessions[0]` (grep confirms
  `get_authoritative_session` reference).
- [ ] No `reject_from_terminal=False` override in the queue reconciler (grep confirms it is
  absent from `agent/agent_session_queue.py`) — the #730 constraint is honored.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (lifecycle-finalize)**
  - Name: finalize-builder
  - Role: Defect B — `complete_transcript` selection fix + executor completion-guard
  - Agent Type: builder
  - Domain: async/concurrency, Redis/Popoto
  - Resume: true

- **Builder (queue-reconcile)**
  - Name: queue-builder
  - Role: Defect A — enqueue reconciliation + bounded pop-loop escalation
  - Agent Type: builder
  - Domain: async/concurrency, Redis/Popoto
  - Resume: true

- **Test engineer (regression)**
  - Name: regression-tester
  - Role: Defect A + B regression tests (divergent-pair fixtures, terminal-status assertions)
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: lifecycle-validator
  - Role: Verify both defects fixed, no ghost `running`, success criteria met
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: lifecycle-doc
  - Role: Update `docs/features/session-lifecycle.md` + index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Fix Defect B — correct selection + guaranteed finalize
- **Task ID**: build-finalize
- **Depends On**: none
- **Validates**: tests/unit/test_session_transcript.py, tests/unit/test_teammate_cold_start_finalize.py (create)
- **Informed By**: spike-1 (blind `[0]` selection + exception-only fallback), spike-2 (session-type agnostic)
- **Assigned To**: finalize-builder
- **Agent Type**: builder
- **Parallel**: true
- Migrate `bridge/session_transcript.py:296` to `get_authoritative_session(session_id)`; handle `None` with a WARNING.
- Add an unconditional completion-exit guard in `agent/session_executor.py` (non-deferred exit): re-read authoritative session; if still `running`, `finalize_session(...)`, catching `StatusConflictError` as success. Subsume the exception-only fallback at ~2188.
- Add inline comments explaining scope (non-deferred exit) and why unconditional.

### 2. Fix Defect A — enqueue reconciliation + bounded pop-loop escalation
- **Task ID**: build-queue
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_queue.py, tests/unit/test_teammate_cold_start_finalize.py (create)
- **Informed By**: spike-3 (`_mark_superseded` is a no-op; #730 forbids terminal re-activation)
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace the no-op `_mark_superseded` (agent_session_queue.py:306) with a reconciler that **deletes** stale terminal duplicates via ORM `instance.delete()`, guarded by `get_child_sessions()` (skip delete if children exist), before `async_create`; never touch `running`/`pending`; ORM-only mutations. Do NOT use `reject_from_terminal=False` (#730). Keep the surrounding `try/except` WARNING so a delete failure never blocks record creation.
- Add a loop-local per-`session_id` consecutive-conflict counter AND an `escalated: set[str]` in `_run_worker_loop`'s `StatusConflictError` handler (agent_session_queue.py:1534); at threshold N=3, if not already escalated: log ERROR once, add to `escalated`, and strike the stuck `pending` record from the pending index via a terminal ORM transition (e.g. `transition_status(→cancelled)` / the `cancel_pending_session` path). Reset counter and `escalated` entry on any successful pop. No health/reflection signal, no in-loop reconciler.

### 3. Regression tests (Defect A + B)
- **Task ID**: build-tests
- **Depends On**: build-finalize, build-queue
- **Assigned To**: regression-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Defect B: teammate session, invalid resume scalars and/or a `(running, failed)` divergent pair → run completion path → assert authoritative record reaches terminal (a `LIFECYCLE running→terminal` line; `status != running`).
- Defect A: `(pending, failed)` divergent pair → assert enqueue reconciliation **deletes** the stale terminal record (and the child-guarded skip path leaves a parent-of-children intact) AND, for a residual pair, the pop loop escalates to a single ERROR within N conflicts, records the session_id in `escalated`, and strikes it from the pending index instead of spinning.
- Cover the failure-path cases from Failure Path Test Strategy.

### 4. Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: lifecycle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm all success criteria; confirm no ghost `running` record survives a completed run; report pass/fail.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-finalize, build-queue
- **Assigned To**: lifecycle-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-lifecycle.md` with the failure mode and fix; verify the README index entry.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass (narrow-scope) | `pytest tests/unit/test_teammate_cold_start_finalize.py tests/unit/test_session_transcript.py tests/unit/test_agent_session_queue.py -q` | exit code 0 |
| Format clean | `python -m ruff format --check agent/ bridge/ models/` | exit code 0 |
| complete_transcript no longer blind-[0] | `grep -n "get_authoritative_session" bridge/session_transcript.py` | output contains get_authoritative_session |
| No blind [0] in complete_transcript body | `sed -n '252,334p' bridge/session_transcript.py \| grep -c "query.filter(session_id=session_id))\[0\]"` | match count == 0 |
| Reconcile deletes terminal dups | `grep -n "get_child_sessions\|\.delete()" agent/agent_session_queue.py` | reconciler calls child-guard + delete |
| No re-introduced #730 override in queue | `grep -n "reject_from_terminal=False" agent/agent_session_queue.py` | no match (empty output) |
| Pop-loop escalation bounded | `grep -n "escalated" agent/agent_session_queue.py` | output contains escalated |
| Docs updated | `grep -ci "cold-start\|divergent\|phantom running\|finalize gap" docs/features/session-lifecycle.md` | output > 0 |

## Critique Results

| Severity | Finding | Addressed By | Implementation Note |
|----------|---------|--------------|---------------------|
| BLOCKER | Defect A reconciliation re-introduced the #730 `reject_from_terminal=False` override, re-activating terminal records | Solution → Duplicate reconciliation; Technical Approach `_mark_superseded`; Risk 1; Resolved Decision 1 | Reconcile via child-guarded ORM `instance.delete()`; override dropped and explicitly forbidden with a #730 citation; Open Question 2 resolved in-plan (delete, not supersede). |
| CONCERN | Wrong baseline: `_mark_superseded` "only supersedes completed" — but `completed→superseded` is guard-rejected, so it supersedes nothing (no-op) | Root cause pattern; spike-3; Data Flow step 2; Freshness Check bullet | Corrected everywhere to state the current body is a documented no-op that reconciles nothing; every divergent record survives. |
| CONCERN | `complete_transcript` migration not load-bearing; the unconditional executor guard alone closes the bug; migration only affects summary placement | Solution Key Elements; spike-1 impact; Technical Approach; Success Criteria | Executor guard reframed as the load-bearing fix; migration reframed as a summary-placement correctness follow-on. |
| CONCERN | Pop-loop escalation unbounded on the reconcile-failure branch (ERROR every tick forever) | Solution → Bounded escalation; Technical Approach pop-loop; Data Flow step 4; Risk 4 (new); Failure Path Test Strategy | Added a loop-local `escalated` set + a terminal strike-the-pending-index action so ERROR fires once and the loop stops re-popping the stuck record. |
| CONCERN | Defect A over-built (four remediations) | Solution → Bounded escalation; Technical Approach; Step 2 task | Downscoped to loop-local counter + single ERROR at threshold + strike-index terminal action, plus the enqueue-time delete. Dropped the health/reflection signal and the in-loop one-shot reconciler. |

---

## Resolved Decisions (from critique revision)

1. **Reconcile: delete, not supersede** (was Open Question 2; critique BLOCKER). Stale
   terminal duplicates are reconciled by ORM `instance.delete()` guarded by a
   child-reference check (`get_child_sessions()`), **not** by
   `transition_status(→superseded, reject_from_terminal=False)`. #730 intentionally removed
   that override so terminal records are never re-activated, and `→superseded` from a
   terminal state is guard-rejected regardless — reusing it would re-introduce the exact
   behavior #730 forbids. The `instance.delete()` of a stale terminal duplicate (a record
   whose `session_id` already has a live successor) has low residual audit value; the
   child-guard prevents orphaning any child sessions.
2. **Escalation surface: ERROR + strike-index, bounded** (was Open Question 1; critique
   concern — downscoped). At threshold N=3 consecutive conflicts, the pop loop logs a
   single ERROR (gated by a loop-local `escalated` set) and strikes the stuck record from
   the pending index. No dashboard health signal, no `agent-session-cleanup` reflection
   flag, no in-loop reconciler — the enqueue-time delete is the real remediation and this
   is the bounded, self-terminating safety net.
3. **Ship in one PR** (was Open Question 3; PM decision). Both defects ship in a single PR
   on the session branch — they share the divergent-duplicate-record root cause, and
   splitting would fragment a coherent fix.
