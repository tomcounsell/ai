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
`pending` record to `running`, re-read on disk (where divergent-duplicate index
ambiguity resolved to the `failed` record), found `failed`, and raised
`StatusConflictError`. The pop loop catches it, logs a WARNING, releases the slot,
and `continue`s — with no attempt counter, no dedup, no escalation. The `pending`
index entry never clears (the transition keeps failing CAS against the terminal
duplicate the index keeps surfacing), so the loop re-pops the same session every
tick forever. It only stopped when a human ran
`agent_session_scheduler cleanup --age 30`.

The stale **terminal duplicate** is the ambiguity cause; the `pending` record is
the legitimate not-yet-run work (a queued teammate session carrying an undelivered
reply). Escalation therefore removes the *terminal duplicate* (child-guarded delete)
so the `pending` record can pop cleanly — cancelling the `pending` record is a
last resort only for the residual case where the terminal duplicate cannot be
deleted (it has child sessions, so the child-guard skips it).

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

### spike-4: Is `session_id` reachable at the pop-loop `StatusConflictError` catch site?
- **Assumption**: "The pop-loop catch site has no `session_id` in scope, so a per-session_id
  counter is unimplementable without re-plumbing." (round-2 critique CONCERN 1)
- **Method**: code-read (`models/session_lifecycle.py:32-61, 356-368`, `agent/agent_session_queue.py:1534`)
- **Finding**: `StatusConflictError` **already** carries a `session_id` attribute set in its
  `__init__` (session_lifecycle.py:52), and the pop-path raise site — the terminal-state guard
  in `finalize_session`/`transition_status` at session_lifecycle.py:360-361 — populates it
  (`session_id=getattr(session, "session_id", "?") or "?"`). The catch at
  agent_session_queue.py:1534 already binds the exception as `e`, so `e.session_id` is in scope
  today. The critique's concern is real (the `session` local is never assigned in the pop's
  except path) but the remedy is already present on the exception object — no raise-site change
  needed; the counter simply keys off `e.session_id`.
- **Confidence**: high
- **Impact on plan**: Key the loop-local conflict counter and `escalated` set off `e.session_id`.
  No new exception attribute and no raise-site edits are required. A test asserts `e.session_id`
  is populated (non-`"?"`) on a pop-path conflict so the counter never silently mis-keys.

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
   counter (keyed off the `StatusConflictError.session_id` attribute the exception already
   carries — see spike-4) escalates in two stages. At primary threshold N=3 it deletes the
   stale **terminal duplicate** (child-guarded, the same reconciler as enqueue) so the
   `pending` record pops cleanly on the next tick; this delete runs idempotently every tick
   past threshold (not gated), while a loop-local `escalated` set gates ONLY the single
   ERROR log. At a higher last-resort threshold N=6 (reached only when the terminal
   duplicate could not be deleted — it has children — so the conflict persists), it cancels
   the `pending` record via a terminal ORM transition and writes a `cancel_reason` so the
   loop stops even in that residual case.
5. **After human dedup**: `pending` wins the pop, `pending→running` logs, session runs the
   cold-start-with-prime path and completes real work.
6. **Finalization**: `session_executor.py:2162` → `complete_transcript` → blind `sessions[0]`
   selection → `finalize_session` on the wrong/terminal record → guard-swallow or no-op.
7. **Defect B / Output**: the real `running` record is never finalized → phantom `running`
   until manual kill.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none to public signatures. An unconditional post-completion
  finalize guard is added in `session_executor.py` (the load-bearing fix) **after the whole
  `if agent_session: / else:` block**, so every completion exit (including the
  `agent_session is None` branch) is covered. `complete_transcript` internal record selection
  changes from `list(...)[0]` to `get_authoritative_session`. `_mark_superseded` (a current
  no-op) is replaced by a child-guarded delete of stale terminal duplicates, extracted into a
  reusable reconciler helper reused by the pop-loop escalation. A per-`session_id` conflict
  counter and an `escalated` set (both keyed off `StatusConflictError.session_id`) are added
  to the worker-loop local state. `StatusConflictError` gains no new attribute — it already
  carries `session_id`.
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
  record. The guard runs **after the entire `if agent_session: / else:` completion block**
  (not inside the `if agent_session:` branch), so the `agent_session is None` exit is
  covered too — every non-deferred completion exit re-reads and finalizes.
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
  `StatusConflictError`s per `session_id` (keyed off `e.session_id`, which the exception
  already carries — spike-4). The escalation attacks the *ambiguity cause*, not the
  work-bearing record, in two stages:
  - **Primary (threshold N=3): delete the stale terminal duplicate.** Run the same
    child-guarded reconciler used at enqueue (delete every terminal duplicate for
    `e.session_id` that has no child sessions) so the `pending` record pops cleanly next
    tick. This delete runs **idempotently every tick** past threshold — it is NOT gated by
    `escalated`, so a transient delete failure retries instead of wedging (a re-run is a
    no-op once the duplicate is gone). Only the single ERROR log is gated by the loop-local
    `escalated` set (fires once, not per-tick).
  - **Last resort (threshold N=6): cancel the `pending` record.** Reached only in the
    residual case where the terminal duplicate could not be deleted (it has children, so the
    child-guard skips it) and the conflict therefore persists. Strike the stuck `pending`
    record via a terminal ORM transition (`transition_status(→cancelled)`) and write
    `set_cancel_reason(e.session_id, "conflict_escalation")` (agent/cancel_reason.py) so the
    cancellation is operator-visible. This is the only path that touches the `pending`
    record, and only after the primary remediation provably could not clear the conflict.
  - Reset the counter and drop the `escalated` entry on any successful pop of that
    `session_id`. No health/reflection signal (keeps the pop loop's blast radius minimal).

### Flow

Runner completes → executor runs the whole `if agent_session / else` completion block →
**then, after that block**, re-reads the authoritative session → if still `running`,
`finalize_session(...)` (belt-and-suspenders, covers both branches) → terminal status
logged → worker moves on.

Re-enqueue → child-guarded delete of stale terminal duplicates → single `pending`
record → clean pickup.

Pop hits repeated conflict → counter increments (keyed off `e.session_id`) → at N=3:
one ERROR log (gated by `escalated`) + idempotent child-guarded delete of the stale
terminal duplicate (runs every tick, ungated) → the `pending` record pops cleanly →
counter resets. Residual case only (terminal duplicate has children, delete skipped): at
N=6 the `pending` record is cancelled via a terminal transition with a `cancel_reason` →
loop no longer spins silently.

### Technical Approach

- **`bridge/session_transcript.py:296`**: `s = get_authoritative_session(session_id)`
  (import from `models.session_lifecycle`); keep the `waiting_for_children` and
  terminal-vs-non-terminal branches. Handle `None` (no record) with a WARNING.
- **`agent/session_executor.py` (~2150-2247)**: add the unconditional guard **after the
  entire `if agent_session: / else:` completion block closes** (not nested inside the
  `if agent_session:` branch), gated only by `not chat_state.defer_reaction`. This is the
  round-2 CONCERN 3 fix: the current defensive fallback lives inside the `if agent_session:`
  branch (lines 2180-2212), so the `else:` exit (agent_session lookup returned `None`, lines
  2213-2247) has no re-read+finalize backstop — a `complete_transcript` that silently no-ops
  there leaves `running`. Placed after the block, the guard covers both exits: re-read
  `get_authoritative_session(session.session_id)`; if it is not `None` and `status == "running"`,
  call `finalize_session(_auth, _runner_final_status(task.error, agent_session))`, catching
  `StatusConflictError` as success. This subsumes the current exception-only fallback at
  ~2188 (which only fires when `complete_transcript` *raises*, and only in the `if` branch).
  Keep the `defer_reaction` guard so the nudge path is untouched.
- **`agent/agent_session_queue.py:306` `_mark_superseded` (currently a no-op)**: replace
  the rejected `transition_status(completed→superseded)` with a reconciler that iterates
  ALL terminal duplicates (`failed`/`killed`/`abandoned`/`completed`/`cancelled`) for the
  `session_id` and **deletes** each via ORM `instance.delete()`, guarded by
  `get_child_sessions()` (skip the delete when children exist so a parent-of-children is
  never orphaned). Extract this reconciler into a small module-level helper
  (e.g. `_delete_stale_terminal_duplicates(session_id) -> int`, returning the delete count)
  so the pop-loop escalation can reuse the exact same child-guarded logic. Do NOT use
  `reject_from_terminal=False` (#730 — that override was removed on purpose; re-adding it
  re-opens terminal re-activation). Keep the surrounding `try/except` WARNING so a delete
  failure never blocks the new record's creation. Never touch `running`/`pending`; ORM-only,
  never raw Redis.
- **`agent/agent_session_queue.py:1534` pop-loop handler**: maintain a `dict[str, int]` of
  consecutive conflict counts AND an `escalated: set[str]`, both local to `_worker_loop`,
  **keyed off `e.session_id`** (`StatusConflictError` already carries `session_id` —
  spike-4 / round-2 CONCERN 1; no raise-site change is needed). Reset the counter and drop
  the `escalated` entry on any successful pop of that `session_id`. The escalation is
  two-staged and gates ONLY the log (round-2 CONCERN 2):
  - **At primary threshold N=3**: call `_delete_stale_terminal_duplicates(e.session_id)`
    **every tick, unconditionally** (idempotent — a re-run after the duplicate is gone is a
    no-op; a transient failure retries next tick instead of wedging). Separately, if
    `e.session_id` is not already in `escalated`, log ERROR **once** and add it to
    `escalated`. The `escalated` set gates the log alone, never the delete — this is the
    round-2 CONCERN 2 fix (a transient strike failure must not permanently silence the
    remediation). Deleting the terminal duplicate resolves the index ambiguity so the
    `pending` record pops cleanly and the counter resets — the work-bearing record is
    preserved (round-2 BLOCKER fix).
  - **At last-resort threshold N=6** (residual case: the terminal duplicate has children, so
    the child-guard skipped it and the conflict persists): strike the stuck `pending` record
    via a terminal ORM transition (`transition_status(→cancelled)`) and write
    `set_cancel_reason(e.session_id, "conflict_escalation")` (agent/cancel_reason.py) for
    operator visibility (round-2 NIT 1). This is the only branch that touches the `pending`
    record, and only after the primary delete provably could not clear the conflict. Log this
    last-resort cancel at ERROR once as well.
  - No health/reflection signal and no in-loop reconciler beyond the shared child-guarded
    delete — the ERROR logs plus the two-stage remediation make the wedge operator-visible
    and self-clearing within a bounded number of ticks while preserving legitimate queued work.
- Reference `docs/infra/` scan: no `docs/infra/` entries constrain this work.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `complete_transcript`'s `except StatusConflictError` (session_transcript.py:318) —
  after migration, add a test asserting it logs at INFO and the authoritative running
  record is still finalized elsewhere (the executor guard), not left running.
- [ ] The new executor completion-guard's `except StatusConflictError` — test asserts it
  is treated as success (another actor already finalized) and never leaves `running`.
- [ ] The executor completion-guard covers the `agent_session is None` exit — test asserts a
  completion where the `status="running"` lookup returns `None` (the `else` branch) still
  finalizes the authoritative record via the post-block guard, not just the `if agent_session:`
  path (round-2 CONCERN 3).
- [ ] The reconciler's `except Exception` (agent_session_queue.py:330) — test asserts a
  reconciliation (delete) failure logs a WARNING and does not block the new record's creation.
- [ ] Child-guarded delete skip — test asserts a terminal duplicate that HAS child sessions
  is NOT deleted (no orphaned parent link) and the new `pending` record is still created.

### Empty/Invalid Input Handling
- [ ] `get_authoritative_session` returning `None` in `complete_transcript` — test the
  no-record path logs a WARNING and does not raise.
- [ ] Pop-loop conflict counter with an unknown/empty `session_id` — assert no KeyError.
- [ ] `StatusConflictError.session_id` is populated on a pop-path conflict — test asserts the
  exception raised by the pop's `transition_status(→running)` carries a real `session_id`
  (not `"?"`) so the counter keys correctly (round-2 CONCERN 1 / spike-4).

### Error State Rendering
- [ ] Defect A escalation must preserve the work-bearing record AND be bounded (round-2 BLOCKER):
  test that at N=3 the stale **terminal duplicate** is deleted (child-guarded) and the
  `pending` record then pops cleanly — the `pending` record is NOT cancelled and its
  undelivered reply survives.
- [ ] Escalation ERROR is emitted exactly once, not per-tick — test that the ERROR line fires
  once (gated by `escalated`) while the child-guarded delete runs every tick past threshold
  (ungated), so a first-attempt transient delete failure still retries and clears on a later
  tick (round-2 CONCERN 2).
- [ ] Residual-case last resort — test that when the terminal duplicate HAS children (delete
  child-guard-skipped) the conflict persists, and at N=6 the `pending` record is cancelled via
  a terminal transition with `cancel_reason == "conflict_escalation"` set (round-2 NIT 1), so
  the loop stops spinning even in the residual case.

## Test Impact

- [ ] `tests/unit/test_session_transcript.py` (if present) — UPDATE: `complete_transcript`
  now selects via `get_authoritative_session`; update any test that asserted blind
  `[0]` selection or single-record behavior.
- [ ] `tests/unit/test_agent_session_queue.py` (if present) — UPDATE: the reconciler now
  **deletes** stale terminal duplicates (child-guarded) instead of superseding `completed`
  ones; replace any supersede-only-completed assertion (which tested a no-op) with a
  delete-and-child-guard assertion. Add pop-loop counter/`escalated`/delete-terminal-duplicate
  coverage plus the residual-case last-resort `pending` cancel with `cancel_reason`.
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

### Risk 1b: Escalation re-fires ERROR every tick, or a transient delete failure wedges silently
**Impact:** Log flooding (ERROR per tick), or — the round-2 CONCERN 2 failure mode — a
first-attempt delete failure permanently silences the remediation if the gate covers the
action as well as the log.
**Mitigation:** The loop-local `escalated: set[str]` gates **only** the ERROR log (fires once
per session_id). The child-guarded delete of the stale terminal duplicate runs **every tick,
ungated and idempotent** past threshold — a transient ORM failure simply retries on the next
tick, and a re-run after the duplicate is gone is a no-op. Deleting the ambiguity cause lets
the `pending` record pop cleanly and the counter/`escalated` entry reset on that successful
pop — the escalation is self-terminating without touching the work-bearing record.

### Risk 1c: Escalation discards a legitimate queued session (round-2 BLOCKER)
**Impact:** Cancelling the `pending` record at escalation would silently discard a queued
teammate session and its undelivered reply, while leaving the stale terminal duplicate (the
real ambiguity cause) alive to re-conflict — net-new data loss versus today's WARNING+continue.
**Mitigation:** Primary escalation (N=3) deletes the **terminal duplicate**, never the
`pending` record, so the queued work runs. The `pending` record is cancelled only as a bounded
last resort (N=6) in the residual case where the terminal duplicate has child sessions and thus
cannot be deleted — and even then the cancellation is recorded via `cancel_reason` for operator
visibility. In that residual case the `pending` record genuinely cannot transition (a
terminal-parent-of-children permanently shadows it in the index), so bounded cancellation is the
only way to stop the infinite spin.

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
  the exception-only fallback), why it sits **after the whole `if agent_session: / else:`
  block** (so both exits are covered — round-2 CONCERN 3), and why it is gated only by
  `not defer_reaction`.
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
  enqueue (child-guarded), AND any residual divergent pair escalates: at N=3 the **terminal
  duplicate is deleted** (not the `pending` record) so the queued work pops cleanly with its
  reply intact, with a single (not per-tick) ERROR; only in the residual children-present
  case does N=6 cancel the `pending` record with `cancel_reason == "conflict_escalation"`.
  The work-bearing `pending` record is never discarded except as that bounded last resort.
- [ ] `docs/features/session-lifecycle.md` documents this failure mode and its fix.
- [ ] `complete_transcript` no longer uses blind `sessions[0]` (grep confirms
  `get_authoritative_session` reference). **Note (round-2 NIT 2):** this criterion is a
  *summary-placement correctness follow-on*, not the phantom-running fix — the load-bearing
  fix is the executor completion guard above. The migration only ensures the SESSION_END
  summary and pre-finalize `s.summary` write land on the authoritative (`running`) record;
  it is verified here but is explicitly non-load-bearing for the incident.
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
- Add an unconditional completion-exit guard in `agent/session_executor.py` **after the entire `if agent_session: / else:` block closes** (not inside the `if agent_session:` branch), gated only by `not chat_state.defer_reaction`, so both exits — including the `agent_session is None` `else` branch — are covered (round-2 CONCERN 3): re-read authoritative session; if still `running`, `finalize_session(...)`, catching `StatusConflictError` as success. Subsume the exception-only fallback at ~2188.
- Add inline comments explaining scope (non-deferred exit), why it sits after the whole if/else, and why unconditional.

### 2. Fix Defect A — enqueue reconciliation + bounded pop-loop escalation
- **Task ID**: build-queue
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_queue.py, tests/unit/test_teammate_cold_start_finalize.py (create)
- **Informed By**: spike-3 (`_mark_superseded` is a no-op; #730 forbids terminal re-activation)
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace the no-op `_mark_superseded` (agent_session_queue.py:306) with a reconciler that **deletes** stale terminal duplicates via ORM `instance.delete()`, guarded by `get_child_sessions()` (skip delete if children exist), before `async_create`; never touch `running`/`pending`; ORM-only mutations. Do NOT use `reject_from_terminal=False` (#730). Keep the surrounding `try/except` WARNING so a delete failure never blocks record creation. Extract the child-guarded delete into a reusable module-level helper (`_delete_stale_terminal_duplicates(session_id) -> int`) so the pop-loop escalation reuses the identical logic.
- Add a loop-local per-`session_id` consecutive-conflict counter AND an `escalated: set[str]` in `_worker_loop`'s `StatusConflictError` handler (agent_session_queue.py:1534), **keyed off `e.session_id`** (the exception already carries `session_id` — spike-4 / round-2 CONCERN 1; no raise-site change). Two-stage escalation: **(primary, N=3)** call `_delete_stale_terminal_duplicates(e.session_id)` **every tick, ungated/idempotent** (deletes the ambiguity-causing terminal duplicate so the `pending` record pops cleanly — round-2 BLOCKER), and separately log ERROR **once** gated by `escalated` (round-2 CONCERN 2 — gate covers ONLY the log, never the delete). **(last resort, N=6, residual only)** when the terminal duplicate has children and was child-guard-skipped so the conflict persists, cancel the `pending` record via `transition_status(→cancelled)` and set `set_cancel_reason(e.session_id, "conflict_escalation")` (round-2 NIT 1); log this ERROR once too. Reset counter and `escalated` entry on any successful pop. No health/reflection signal, no in-loop reconciler beyond the shared child-guarded delete.

### 3. Regression tests (Defect A + B)
- **Task ID**: build-tests
- **Depends On**: build-finalize, build-queue
- **Assigned To**: regression-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Defect B: teammate session, invalid resume scalars and/or a `(running, failed)` divergent pair → run completion path → assert authoritative record reaches terminal (a `LIFECYCLE running→terminal` line; `status != running`).
- Defect A: `(pending, failed)` divergent pair → assert enqueue reconciliation **deletes** the stale terminal record (and the child-guarded skip path leaves a parent-of-children intact). For a residual pair reaching the pop loop: assert (a) the escalation deletes the **terminal duplicate** at N=3 and the `pending` record then pops cleanly with its reply intact — the `pending` record is NOT cancelled (round-2 BLOCKER); (b) the ERROR fires exactly once while the delete runs every tick (round-2 CONCERN 2); (c) `StatusConflictError.session_id` is populated so the counter keys correctly (round-2 CONCERN 1); (d) in the children-present residual case, N=6 cancels the `pending` record with `cancel_reason == "conflict_escalation"` (round-2 NIT 1) instead of spinning.
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
| Escalation keys off exception session_id | `grep -n "e.session_id\|exc.session_id" agent/agent_session_queue.py` | pop-loop handler references the exception's session_id |
| Escalation cancels pending only as last resort with reason | `grep -n "set_cancel_reason\|conflict_escalation" agent/agent_session_queue.py` | output contains conflict_escalation |
| Shared child-guarded delete helper | `grep -n "_delete_stale_terminal_duplicates" agent/agent_session_queue.py` | helper referenced by both enqueue and pop-loop |
| Executor guard after the whole if/else | `grep -n "get_authoritative_session" agent/session_executor.py` | guard present after the completion block (not only the exception fallback) |
| Docs updated | `grep -ci "cold-start\|divergent\|phantom running\|finalize gap" docs/features/session-lifecycle.md` | output > 0 |

## Critique Results

| Severity | Finding | Addressed By | Implementation Note |
|----------|---------|--------------|---------------------|
| BLOCKER | Defect A reconciliation re-introduced the #730 `reject_from_terminal=False` override, re-activating terminal records | Solution → Duplicate reconciliation; Technical Approach `_mark_superseded`; Risk 1; Resolved Decision 1 | Reconcile via child-guarded ORM `instance.delete()`; override dropped and explicitly forbidden with a #730 citation; Open Question 2 resolved in-plan (delete, not supersede). |
| CONCERN | Wrong baseline: `_mark_superseded` "only supersedes completed" — but `completed→superseded` is guard-rejected, so it supersedes nothing (no-op) | Root cause pattern; spike-3; Data Flow step 2; Freshness Check bullet | Corrected everywhere to state the current body is a documented no-op that reconciles nothing; every divergent record survives. |
| CONCERN | `complete_transcript` migration not load-bearing; the unconditional executor guard alone closes the bug; migration only affects summary placement | Solution Key Elements; spike-1 impact; Technical Approach; Success Criteria | Executor guard reframed as the load-bearing fix; migration reframed as a summary-placement correctness follow-on. |
| CONCERN | Pop-loop escalation unbounded on the reconcile-failure branch (ERROR every tick forever) | Solution → Bounded escalation; Technical Approach pop-loop; Data Flow step 4; Risk 4 (new); Failure Path Test Strategy | Added a loop-local `escalated` set + a terminal strike-the-pending-index action so ERROR fires once and the loop stops re-popping the stuck record. |
| CONCERN | Defect A over-built (four remediations) | Solution → Bounded escalation; Technical Approach; Step 2 task | Downscoped to loop-local counter + single ERROR at threshold + strike-index terminal action, plus the enqueue-time delete. Dropped the health/reflection signal and the in-loop one-shot reconciler. |

### Round 2 (second NEEDS REVISION — Defect A escalation design)

| Severity | Finding | Addressed By | Implementation Note |
|----------|---------|--------------|---------------------|
| BLOCKER | Pop-loop escalation cancelled the work-bearing `pending` record (discarding a queued session + undelivered reply) while the stale terminal duplicate — the actual ambiguity cause — survived to re-conflict | Defect A description; Solution → Bounded escalation; Technical Approach pop-loop; Flow; Data Flow step 4; Risk 1c (new); Resolved Decision 2; Step 2/3 tasks; Failure Path Test Strategy | Primary escalation (N=3) now child-guarded-**deletes the terminal duplicate** (via the shared `_delete_stale_terminal_duplicates` helper) so `pending` pops cleanly; the `pending` record is cancelled only as a bounded last resort (N=6) in the residual children-present case. |
| CONCERN 1 | `session_id` not in scope at the `StatusConflictError` catch (the `session` local is never assigned), so a per-session_id counter looked unimplementable | spike-4 (new); Architectural Impact; Solution → Bounded escalation; Technical Approach pop-loop; Failure Path Test Strategy | `StatusConflictError` **already** carries a `session_id` attribute populated at the pop-path raise site (session_lifecycle.py:361); the counter keys off `e.session_id`. No raise-site change needed; a test asserts it is populated. |
| CONCERN 2 | The `escalated` gate covered both the ERROR log AND the strike, so a transient strike failure re-wedges silently forever | Solution → Bounded escalation; Technical Approach pop-loop; Risk 1b (rewritten); Data Flow step 4; Failure Path Test Strategy | `escalated` now gates ONLY the ERROR log; the child-guarded delete runs every tick, ungated and idempotent, so a transient failure retries. |
| CONCERN 3 | The executor completion-guard was scoped to the `if agent_session:` branch, leaving the `else` (agent_session is None) exit uncovered | Solution Key Elements; Technical Approach executor; Architectural Impact; Step 1 task; Success Criteria; Failure Path Test Strategy | Guard moved **after the whole `if/else` block**, gated only by `not defer_reaction`, so every completion exit re-reads and finalizes. |
| NIT 1 | No operator surface (`cancel_reason`) for escalation-cancelled sessions | Solution → Bounded escalation; Technical Approach pop-loop; Risk 1c; Step 2 task; Success Criteria; Verification | The last-resort `pending` cancel writes `set_cancel_reason(e.session_id, "conflict_escalation")` (agent/cancel_reason.py). |
| NIT 2 | The `complete_transcript` migration is non-load-bearing yet listed as a success criterion | Success Criteria (annotated); spike-1 impact | The relevant success criterion is explicitly annotated as a correctness follow-on, not the phantom-running fix (which is the executor guard). |

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
2. **Escalation attacks the ambiguity cause, not the work-bearing record** (was Open
   Question 1; refined across two critique rounds). At primary threshold N=3 the pop loop
   child-guarded-**deletes the stale terminal duplicate** (via the shared
   `_delete_stale_terminal_duplicates` helper, run every tick and idempotent) so the
   `pending` record pops cleanly and its undelivered reply survives — the round-2 BLOCKER
   fix. A loop-local `escalated` set gates ONLY the single ERROR log (round-2 CONCERN 2), the
   counter keys off `e.session_id` which the exception already carries (round-2 CONCERN 1).
   The `pending` record is cancelled only as a bounded last resort at N=6, and only in the
   residual case where the terminal duplicate has child sessions (child-guard skips the
   delete, so the conflict genuinely persists); that cancel writes
   `set_cancel_reason(e.session_id, "conflict_escalation")` for operator visibility (round-2
   NIT 1). No dashboard health signal, no `agent-session-cleanup` reflection flag, no in-loop
   reconciler beyond the shared delete — the enqueue-time delete is the primary remediation
   and this is the bounded, self-terminating safety net that preserves legitimate queued work.
3. **Ship in one PR** (was Open Question 3; PM decision). Both defects ship in a single PR
   on the session branch — they share the divergent-duplicate-record root cause, and
   splitting would fragment a coherent fix.
